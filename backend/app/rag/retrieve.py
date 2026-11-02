"""Truy xuất hybrid trên `documents` (đặc tả 3.1, 9.2 `search_knowledge`).

Điểm = 0,6 × cosine (pgvector `<=>`) + 0,4 × điểm từ khóa, trong đó điểm từ khóa = max(
  `word_similarity` của pg_trgm, tỉ lệ từ khóa (đã gấp dấu) của câu hỏi xuất hiện trong chunk).
Ứng viên = hợp của top theo vector và top theo từ khóa (ILIKE / pg_trgm). Mỗi thực thể giữ chunk điểm cao nhất.
Kết quả kèm url gốc (profile_url / project_url) và SourceRef của bản ghi data_sources.
"""
from __future__ import annotations

import re

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from app.db.models import Contractor, DataSource, Project
from app.evidence import source_ref_from
from app.ingestion.parse_vi import clean_text, fold_vi
from app.rag.embed import _STOPWORDS, Embedder, get_embedder
from app.schemas.knowledge import KnowledgeChunk

VECTOR_WEIGHT = 0.6
LEXICAL_WEIGHT = 0.4
MIN_SCORE = 0.05
# Ngưỡng "liên quan" cho câu trả lời chat (search_knowledge): dưới ngưỡng coi như không tìm thấy thay vì trả đoạn
# mô tả lạc đề (đo trên dữ liệu mẫu: câu hỏi lạc đề ≤ 0,2; câu hỏi đúng thực thể/loại công trình ≥ 0,28).
RELEVANT_MIN_SCORE = 0.25


def _vec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.7g}" for v in vec) + "]"


def _terms(query: str) -> list[str]:
    raw = [t for t in re.findall(r"\w+", clean_text(query).lower()) if len(t) >= 2]
    return [t for t in raw if fold_vi(t) not in _STOPWORDS]


def _term_overlap(terms: list[str], content: str) -> float:
    if not terms:
        return 0.0
    folded = f" {' '.join(re.findall(r'[a-z0-9]+', fold_vi(content)))} "
    hits = sum(1 for t in terms if f" {fold_vi(t)} " in folded)
    return hits / len(terms)


_CANDIDATE_SQL = """
SELECT d.id, d.entity_type, d.entity_id, d.chunk_index, d.content, d.source_id,
       1 - (d.embedding <=> CAST(:qvec AS vector)) AS vec_score,
       word_similarity(:q, d.content) AS trgm_score
FROM documents d
WHERE d.embedding IS NOT NULL {type_filter} {extra}
ORDER BY {order}
LIMIT :lim
"""


def search(db: Session, query: str, entity_type: str | None = None, k: int = 5,
           embedder: Embedder | None = None, min_score: float = MIN_SCORE) -> list[KnowledgeChunk]:
    """Tìm k chunk liên quan nhất (mỗi thực thể tối đa 1 chunk), chỉ giữ chunk có điểm ≥ `min_score`."""
    query = clean_text(query)
    if not query:
        return []
    k = max(1, min(int(k), 20))
    vec = (embedder or get_embedder()).embed([query])[0]
    params: dict = {"qvec": _vec_literal(vec), "q": query, "lim": k * 6}
    type_filter = ""
    if entity_type:
        type_filter = "AND d.entity_type = :etype"
        params["etype"] = entity_type
    terms = _terms(query)
    rows: dict[int, dict] = {}
    # 1) ứng viên theo vector (dùng index HNSW)
    for r in db.execute(text(_CANDIDATE_SQL.format(type_filter=type_filter, extra="",
                                                   order="d.embedding <=> CAST(:qvec AS vector)")), params).mappings():
        rows[r["id"]] = dict(r)
    # 2) ứng viên theo từ khóa
    if terms:
        lex_params = dict(params)
        lex_params["pats"] = [f"%{t}%" for t in terms]
        stmt = text(_CANDIDATE_SQL.format(
            type_filter=type_filter, extra="AND (d.content ILIKE ANY(:pats) OR :q <% d.content)",
            order="word_similarity(:q, d.content) DESC")).bindparams(bindparam("pats"))
        for r in db.execute(stmt, lex_params).mappings():
            rows.setdefault(r["id"], dict(r))

    best: dict[tuple[str, int], tuple[float, dict]] = {}
    for r in rows.values():
        lexical = max(float(r["trgm_score"] or 0.0), _term_overlap(terms, r["content"]))
        score = VECTOR_WEIGHT * max(float(r["vec_score"] or 0.0), 0.0) + LEXICAL_WEIGHT * lexical
        key = (r["entity_type"], r["entity_id"])
        if score >= max(min_score, MIN_SCORE) and (key not in best or score > best[key][0]):
            best[key] = (score, r)
    ranked = sorted(best.values(), key=lambda x: x[0], reverse=True)

    out: list[KnowledgeChunk] = []
    for score, r in ranked:
        name, url, source = _entity_meta(db, r["entity_type"], r["entity_id"], r["source_id"])
        if name is None and url is None:
            continue  # thực thể đã bị xóa / ngừng hoạt động
        out.append(KnowledgeChunk(entity_type=r["entity_type"], entity_id=r["entity_id"], entity_name=name,
                                  content=r["content"], score=round(score, 4), url=url, source=source))
        if len(out) >= k:
            break
    return out


def _entity_meta(db: Session, entity_type: str, entity_id: int, source_id: int | None):
    name = url = None
    ds = db.get(DataSource, source_id) if source_id else None
    if entity_type == "contractor":
        c = db.get(Contractor, entity_id)
        if c is not None and c.is_active:
            name, url = c.name, c.profile_url
            ds = ds or c.source
    elif entity_type == "project":
        p = db.get(Project, entity_id)
        if p is not None:
            name, url = p.name, p.project_url
            ds = ds or p.source
    return name, url, source_ref_from(ds)
