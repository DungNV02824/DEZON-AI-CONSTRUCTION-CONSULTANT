"""Chunk + embed hồ sơ nhà thầu / dự án vào bảng `documents` (đặc tả 3.1, 4.1, bước 8).

- `Embedder`: interface `embed(texts) -> list[list[float]]`, số chiều 1536 (khớp `documents.embedding`).
- `HashEmbedder` (EMBEDDING_PROVIDER=hash, mặc định): cục bộ, xác định, không cần mạng — băm đặc trưng từ,
  cặp từ và n-gram ký tự sau khi gấp dấu tiếng Việt, có mở rộng đồng nghĩa ngành xây dựng, chuẩn hóa L2.
- `OpenAIEmbedder` (EMBEDDING_PROVIDER=openai): gọi /embeddings qua httpx (EMBEDDING_API_KEY, EMBEDDING_MODEL;
  EMBEDDING_API_BASE tùy chọn).

Nội dung document KHÔNG chứa số liệu có cấu trúc (đơn giá, số công trình, nhân sự, năm kinh nghiệm…) và
các cụm "số + đơn vị" trong mô tả tự do được che bằng "…", để RAG không trở thành nguồn số liệu
(nguyên tắc mục 0 + 2.1). Số liệu luôn lấy qua tool SQL trả EvidenceValue.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from functools import lru_cache
from typing import Protocol, runtime_checkable

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Contractor, Document, Project, ProjectContractor
from app.ingestion.parse_vi import clean_text, fold_vi
from app.schemas.common import BUILDING_TYPE_VI, WORK_SCOPE_VI

log = logging.getLogger(__name__)

EMBED_DIM = 1536
DEFAULT_EMBEDDING_API_BASE = "https://api.openai.com/v1"
ENTITY_TYPES = ("contractor", "project")


# ================================================================== embedder
@runtime_checkable
class Embedder(Protocol):
    name: str
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Mỗi văn bản -> vector `dim` chiều đã chuẩn hóa L2."""


_STOPWORDS = {
    "va", "cua", "cho", "la", "cac", "nhung", "mot", "voi", "tai", "o", "de", "duoc", "trong", "nay", "do",
    "thi", "co", "khong", "nhu", "theo", "tu", "ve", "tren", "se", "da", "dang", "ma", "nao", "gi", "the",
    "ra", "vao", "khi", "neu", "hay", "hoac", "cung", "rat", "nhieu", "it", "anh", "chi", "em", "toi", "ban",
    "minh", "a", "an", "of", "and", "or", "in", "on", "at",
}
# Mở rộng đồng nghĩa (đã gấp dấu): cụm -> cụm chuẩn được thêm vào văn bản.
_SYNONYMS: list[tuple[str, str]] = [
    (r"\bvilla\b", "biet thu"), (r"\bbiet thu\b", "villa"),
    (r"\btownhouse\b|\bnha ong\b|\bnha lien ke\b", "nha pho"),
    (r"\bapartment\b|\bpenthouse\b|\bduplex\b|\bchung cu\b|\bcondo\b", "can ho"),
    (r"\bhotel\b|\bresort\b", "khach san"), (r"\boffice\b", "van phong"),
    (r"\bindochine\b", "dong duong"), (r"\bdong duong\b", "indochine"),
    (r"\bsai gon\b|\btphcm\b|\bhcm\b|\bsg\b", "ho chi minh"), (r"\bho chi minh\b", "sai gon"),
    (r"\bhn\b", "ha noi"), (r"\bq\s?(\d{1,2})\b", r"quan \1"),
    (r"\bxay tho\b", "phan tho"), (r"\bphan tho\b", "xay tho"),
    (r"\bnoi that\b", "interior"), (r"\binterior\b", "noi that"),
]


def _features(text: str) -> dict[str, float]:
    folded = fold_vi(clean_text(text))
    extra = []
    for pat, rep in _SYNONYMS:
        for m in re.finditer(pat, folded):
            extra.append(m.expand(rep) if "\\" in rep else rep)
    folded = folded + " " + " ".join(extra)
    tokens = [t for t in re.findall(r"[a-z0-9]+", folded) if t not in _STOPWORDS]
    feats: dict[str, float] = {}

    def add(key: str, w: float) -> None:
        feats[key] = feats.get(key, 0.0) + w

    for i, tok in enumerate(tokens):
        add(f"w:{tok}", 1.0)
        if i + 1 < len(tokens):
            add(f"b:{tok}_{tokens[i + 1]}", 0.8)
        padded = f"#{tok}#"
        if len(tok) >= 3:
            grams = [padded[j:j + 3] for j in range(len(padded) - 2)]
            for g in grams:
                add(f"c:{g}", 0.3 / math.sqrt(len(grams)))
    # tf thưa: log để từ lặp lại không áp đảo
    return {k: 1.0 + math.log(v) if v > 1.0 else v for k, v in feats.items()}


class HashEmbedder:
    """Embedding băm đặc trưng (feature hashing) — xác định, cục bộ."""

    name = "hash"

    def __init__(self, dim: int = EMBED_DIM):
        self.dim = dim

    def _vector(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for feat, weight in _features(text).items():
            h = int.from_bytes(hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest(), "big")
            idx = h % self.dim
            sign = 1.0 if (h >> 63) & 1 else -1.0
            vec[idx] += sign * weight
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:
            vec[0] = 1.0  # văn bản rỗng: vector đơn vị cố định (tránh NaN với cosine)
            return vec
        return [v / norm for v in vec]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]


class OpenAIEmbedder:
    """Embedding qua API tương thích OpenAI `/embeddings` (text-embedding-3-* hỗ trợ `dimensions`)."""

    name = "openai"

    def __init__(self, api_key: str, model: str, dim: int = EMBED_DIM, base_url: str | None = None,
                 client: httpx.Client | None = None, batch_size: int = 64):
        if not api_key:
            raise ValueError("Thiếu EMBEDDING_API_KEY cho EMBEDDING_PROVIDER=openai")
        self.api_key, self.model, self.dim = api_key, model, dim
        # base_url lấy từ Settings.embedding_api_base (biến EMBEDDING_API_BASE) qua get_embedder().
        self.base_url = (base_url or DEFAULT_EMBEDDING_API_BASE).rstrip("/")
        self.client = client or httpx.Client(timeout=60)
        self.batch_size = batch_size

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch_size):
            batch = [t if t.strip() else " " for t in texts[i:i + self.batch_size]]
            resp = self.client.post(f"{self.base_url}/embeddings",
                                    headers={"Authorization": f"Bearer {self.api_key}"},
                                    json={"model": self.model, "input": batch, "dimensions": self.dim})
            resp.raise_for_status()
            data = sorted(resp.json()["data"], key=lambda d: d["index"])
            for d in data:
                vec = d["embedding"]
                if len(vec) != self.dim:
                    raise ValueError(f"Embedding {len(vec)} chiều, cần {self.dim}")
                out.append(vec)
        return out


@lru_cache
def _cached_embedder(provider: str, model: str, dim: int, key_set: bool, base_url: str = "") -> Embedder:
    settings = get_settings()
    if provider == "openai":
        return OpenAIEmbedder(settings.embedding_api_key, model, dim, base_url=base_url or None)
    if provider != "hash":
        log.warning("EMBEDDING_PROVIDER=%s không hỗ trợ — dùng HashEmbedder", provider)
    return HashEmbedder(dim)


def resolve_provider(settings=None) -> str:
    """`auto` -> openai nếu có EMBEDDING_API_KEY, ngược lại hash."""
    s = settings or get_settings()
    resolved = getattr(s, "resolved_embedding_provider", None)
    if isinstance(resolved, str):
        return resolved.lower()
    provider = (s.embedding_provider or "hash").lower()
    if provider == "auto":
        return "openai" if s.embedding_api_key else "hash"
    return provider


def get_embedder() -> Embedder:
    s = get_settings()
    return _cached_embedder(resolve_provider(s), s.embedding_model, s.embedding_dim, bool(s.embedding_api_key),
                            s.embedding_api_base)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ================================================================== chunk
def chunk_text(text: str, max_chars: int = 800, overlap: int = 150) -> list[str]:
    """Chia theo đoạn rồi theo câu, mỗi chunk <= ~max_chars, chunk sau lặp lại phần cuối chunk trước
    (khoảng `overlap` ký tự, theo ranh giới câu) để giữ ngữ cảnh."""
    text = (text or "").strip()
    if not text:
        return []
    sentences: list[str] = []
    for para in re.split(r"\n\s*\n|\n", text):
        para = clean_text(para)
        if not para:
            continue
        parts = re.split(r"(?<=[.!?…;])\s+(?=[A-ZÀ-Ỹ0-9\"“(])", para)
        for s in parts:
            s = s.strip()
            while len(s) > max_chars:  # câu quá dài: cắt theo khoảng trắng gần nhất
                cut = s.rfind(" ", 0, max_chars)
                cut = cut if cut > max_chars // 2 else max_chars
                sentences.append(s[:cut].strip())
                s = s[cut:].strip()
            if s:
                sentences.append(s)
    chunks: list[str] = []
    current: list[str] = []
    for s in sentences:
        if current and len(" ".join(current + [s])) > max_chars:
            chunks.append(" ".join(current))
            tail: list[str] = []
            for prev in reversed(current):
                if len(" ".join([prev] + tail)) > overlap:
                    break
                tail.insert(0, prev)
            current = tail + [s] if len(" ".join(tail + [s])) <= max_chars else [s]
        else:
            current.append(s)
    if current:
        chunks.append(" ".join(current))
    return chunks


# ================================================================== văn bản cho từng thực thể
_NUM = r"\d+(?:[.,]\d+)*"
_UNIT = (r"(?:tỷ|tỉ|triệu|tr|nghìn|ngàn|đồng|vnđ|vnd|đ|k|%|m²|m2|m³|m3|km|ha|năm|tháng|tuần|ngày|giờ|"
         r"nhân sự|nhân viên|người|thợ|kỹ sư|kiến trúc sư|công nhân|chuyên gia|dự án|công trình|căn|phòng|"
         r"khách hàng|đối tác|tầng|lầu|ảnh|hạng mục|sao)")
_NUMERIC_CLAIM = re.compile(
    rf"(?<![\w]){_NUM}(?:\s*[-–—~]\s*{_NUM}|\s+(?:đến|tới)\s+{_NUM})?\s*{_UNIT}"
    rf"(?:\s*/\s*(?:m²|m2|năm|tháng|công trình))?(?![\w])",
    re.IGNORECASE,
)
# Số kèm "/m²" không có đơn vị tiền ("4.300.000/m²") và số có phân cách hàng nghìn ("4.300.000").
_PER_M2_CLAIM = re.compile(rf"(?<![\w]){_NUM}\s*/\s*(?:m²|m2)(?![\w])", re.IGNORECASE)
_THOUSANDS_CLAIM = re.compile(r"(?<![\w.,])\d{1,3}(?:[.,]\d{3})+(?![\w.,])")
_YEAR_CLAIM = re.compile(r"(?<![\w])((?:từ\s+)?năm)\s+(?:19|20)\d{2}(?![\w])", re.IGNORECASE)
_BARE_YEAR = re.compile(r"(?<![\w/])(?<!\d[.,])(?:19|20)\d{2}(?![\w/]|[.,]\d)")


def strip_numeric_claims(text: str | None) -> str:
    """Che cụm số liệu trong mô tả tự do: '12 nhân sự văn phòng' -> '… văn phòng', 'năm 2018' -> 'năm …',
    'bảo hành 12 tháng' -> 'bảo hành …', '4.300.000/m²' -> '…', 'thành lập 2018' -> 'thành lập …'.
    Giữ số trong tên riêng không kèm đơn vị ('Quận 7', 'France Indochine 22')."""
    if not text:
        return ""
    t = _YEAR_CLAIM.sub(lambda m: f"{m.group(1)} …", text)
    t = _NUMERIC_CLAIM.sub("…", t)
    t = _PER_M2_CLAIM.sub("…", t)
    t = _THOUSANDS_CLAIM.sub("…", t)
    t = _BARE_YEAR.sub("…", t)
    return re.sub(r"(…\s*[,;]?\s*){2,}", "… ", t).strip()


def _vi_list(codes: list[str | None], labels: dict[str, str]) -> str:
    seen: list[str] = []
    for c in codes:
        if c and labels.get(c, c) not in seen:
            seen.append(labels.get(c, c))
    return ", ".join(seen)


def contractor_text(db: Session, c: Contractor) -> str:
    lines = [f"Nhà thầu {c.name}."]
    if c.badge:
        lines.append(f"Nhãn trên Dezon: {c.badge}.")
    if c.province:
        lines.append(f"Khu vực chính: {c.province}.")
    caps = list(c.capabilities)
    provinces = _vi_list([cap.province for cap in caps], {})
    if provinces:
        lines.append(f"Khu vực hoạt động: {provinces}.")
    types = _vi_list([cap.building_type for cap in caps], BUILDING_TYPE_VI)
    if types:
        lines.append(f"Loại công trình thực hiện: {types}.")
    scopes = _vi_list([cap.work_scope for cap in caps], WORK_SCOPE_VI)
    if scopes:
        lines.append(f"Phạm vi công việc: {scopes}.")
    if c.has_warranty_info and c.warranty_text:
        lines.append(f"Thông tin bảo hành: {strip_numeric_claims(c.warranty_text)}.")
    if c.description:
        lines.append(f"Giới thiệu: {strip_numeric_claims(c.description)}")
    projects = db.execute(
        select(Project.name, Project.building_type, Project.province)
        .join(ProjectContractor, ProjectContractor.project_id == Project.id)
        .where(ProjectContractor.contractor_id == c.id).order_by(Project.id).limit(20)
    ).all()
    if projects:
        items = []
        for name, bt, prov in projects:
            meta = ", ".join(x for x in (BUILDING_TYPE_VI.get(bt or "", ""), prov or "") if x)
            items.append(f"{name} ({meta})" if meta else name)
        lines.append("Công trình ghi nhận trên Dezon: " + "; ".join(items) + ".")
    return "\n".join(lines)


def project_text(db: Session, p: Project) -> str:
    lines = [f"Dự án {p.name}."]
    if p.building_type:
        lines.append(f"Loại công trình: {BUILDING_TYPE_VI.get(p.building_type, p.building_type)}.")
    loc = ", ".join(x for x in (p.district, p.province) if x)
    if loc:
        lines.append(f"Địa điểm: {loc}.")
    contractors = db.execute(
        select(Contractor.name, ProjectContractor.role)
        .join(ProjectContractor, ProjectContractor.contractor_id == Contractor.id)
        .where(ProjectContractor.project_id == p.id)
    ).all()
    if contractors:
        role_vi = {"thi_cong": "thi công", "thiet_ke": "thiết kế", "noi_that": "nội thất"}
        lines.append("Nhà thầu thực hiện: " + "; ".join(
            f"{name} ({role_vi.get(role, role)})" for name, role in contractors) + ".")
    if p.status == "completed":
        lines.append("Trạng thái: đã hoàn thiện.")
    elif p.status == "constructing":
        lines.append("Trạng thái: đang thi công.")
    if p.description:
        lines.append(f"Giới thiệu: {strip_numeric_claims(p.description)}")
    return "\n".join(lines)


def build_documents(db: Session, entity_type: str, entity: Contractor | Project) -> list[str]:
    """Văn bản đã chunk cho một thực thể. Chunk thứ 2 trở đi có tiền tố tên thực thể để tự đứng được."""
    if entity_type == "contractor":
        text_, prefix = contractor_text(db, entity), f"Nhà thầu {entity.name}: "
    elif entity_type == "project":
        text_, prefix = project_text(db, entity), f"Dự án {entity.name}: "
    else:
        raise ValueError(f"entity_type không hỗ trợ: {entity_type}")
    chunks = chunk_text(text_)
    return [c if i == 0 else prefix + c for i, c in enumerate(chunks)]


# ================================================================== ghi documents
def _load_entity(db: Session, entity_type: str, entity_id: int) -> Contractor | Project | None:
    if entity_type == "contractor":
        c = db.get(Contractor, entity_id)
        return c if c is not None and c.is_active else None
    if entity_type == "project":
        return db.get(Project, entity_id)
    raise ValueError(f"entity_type không hỗ trợ: {entity_type}")


def reindex_entity(db: Session, entity_type: str, entity_id: int, embedder: Embedder | None = None) -> int:
    """Upsert các chunk của một thực thể theo (entity_type, entity_id, chunk_index); xóa chunk thừa.
    Chỉ embed lại chunk có nội dung thay đổi. Thực thể không còn/không hoạt động -> xóa hết. Trả số chunk."""
    entity = _load_entity(db, entity_type, entity_id)
    if entity is None:
        db.execute(delete(Document).where(Document.entity_type == entity_type, Document.entity_id == entity_id))
        return 0
    texts = build_documents(db, entity_type, entity)
    existing = {d.chunk_index: d for d in db.scalars(
        select(Document).where(Document.entity_type == entity_type, Document.entity_id == entity_id))}
    todo = [i for i, t in enumerate(texts)
            if i not in existing or existing[i].content != t or existing[i].embedding is None]
    vectors = (embedder or get_embedder()).embed([texts[i] for i in todo]) if todo else []
    now = datetime.now(UTC)
    for i, vec in zip(todo, vectors, strict=True):
        doc = existing.get(i)
        if doc is None:
            db.add(Document(entity_type=entity_type, entity_id=entity_id, chunk_index=i, content=texts[i],
                            embedding=vec, source_id=entity.source_id, updated_at=now))
        else:
            doc.content, doc.embedding, doc.source_id, doc.updated_at = texts[i], vec, entity.source_id, now
    for i, doc in existing.items():
        if i < len(texts) and i not in todo and doc.source_id != entity.source_id:
            doc.source_id = entity.source_id
    if len(existing) > len(texts):
        db.execute(delete(Document).where(Document.entity_type == entity_type, Document.entity_id == entity_id,
                                          Document.chunk_index >= len(texts)))
    db.flush()
    return len(texts)


def reindex_all(db: Session, embedder: Embedder | None = None) -> int:
    """Re-index toàn bộ nhà thầu đang hoạt động + dự án; xóa document của thực thể đã mất. Trả tổng số chunk."""
    embedder = embedder or get_embedder()
    total = 0
    contractor_ids = list(db.scalars(select(Contractor.id).where(Contractor.is_active.is_(True))))
    project_ids = list(db.scalars(select(Project.id)))
    for cid in contractor_ids:
        total += reindex_entity(db, "contractor", cid, embedder)
    for pid in project_ids:
        total += reindex_entity(db, "project", pid, embedder)
    db.execute(delete(Document).where(Document.entity_type == "contractor",
                                      Document.entity_id.not_in(contractor_ids or [-1])))
    db.execute(delete(Document).where(Document.entity_type == "project",
                                      Document.entity_id.not_in(project_ids or [-1])))
    db.flush()
    return total


def reindex_changed(db: Session, entities: list[tuple[str, int]], embedder: Embedder | None = None) -> int:
    """Re-embed chỉ các thực thể vừa thay đổi (sau đồng bộ). Nhà thầu đổi -> các dự án của họ cũng đổi theo."""
    embedder = embedder or get_embedder()
    todo: set[tuple[str, int]] = set()
    for etype, eid in entities:
        todo.add((etype, eid))
        if etype == "project":  # tên dự án xuất hiện trong document của nhà thầu
            for cid in db.scalars(select(ProjectContractor.contractor_id).where(ProjectContractor.project_id == eid)):
                todo.add(("contractor", cid))
        elif etype == "contractor":
            for pid in db.scalars(select(ProjectContractor.project_id).where(ProjectContractor.contractor_id == eid)):
                todo.add(("project", pid))
    return sum(reindex_entity(db, etype, eid, embedder) for etype, eid in sorted(todo))


def seed_hook(db: Session) -> None:
    """Chạy sau seed (đăng ký trong app/db/seed_hooks.py): index toàn bộ dữ liệu seed."""
    n = reindex_all(db)
    log.info("RAG: đã index %s chunk", n)
