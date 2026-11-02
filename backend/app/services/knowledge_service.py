"""Dịch vụ tri thức cho tool `search_knowledge` (đặc tả 9.2).

Trả các chunk RAG kèm nguồn; mỗi chunk mang `usage_note` nhắc rằng chỉ dùng cho mô tả, không phải số liệu.
"""
from sqlalchemy.orm import Session

from app.rag.retrieve import RELEVANT_MIN_SCORE, search
from app.schemas.knowledge import KnowledgeChunk

ALLOWED_ENTITY_TYPES = {"contractor", "project", "article"}


def search_knowledge(db: Session, query: str, entity_type: str | None = None, limit: int = 5,
                     min_score: float = RELEVANT_MIN_SCORE) -> list[KnowledgeChunk]:
    """Tìm hồ sơ/dự án liên quan tới `query`. `entity_type` ∈ contractor|project|article|None.

    Chỉ trả chunk vượt ngưỡng liên quan `min_score` — không có gì liên quan thì trả [] (để trợ lý nói "Chưa có dữ
    liệu" thay vì đọc mô tả lạc đề)."""
    if entity_type is not None and entity_type not in ALLOWED_ENTITY_TYPES:
        entity_type = None
    return search(db, query, entity_type=entity_type, k=limit, min_score=min_score)
