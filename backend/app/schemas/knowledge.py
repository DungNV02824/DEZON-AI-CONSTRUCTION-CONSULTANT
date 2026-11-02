"""Kết quả tool `search_knowledge` (đặc tả 9.2): chunk RAG + nguồn.

RAG chỉ dùng cho MÔ TẢ (giới thiệu, phong cách, loại công trình đã làm), KHÔNG dùng làm nguồn số liệu
(đơn giá, số công trình, nhân sự, năm…) — số liệu luôn lấy qua tool SQL trả EvidenceValue.
"""
from typing import Literal

from pydantic import BaseModel

from app.evidence import SourceRef

USAGE_NOTE_VI = "Chỉ dùng cho mô tả, không dùng làm nguồn số liệu"


class KnowledgeChunk(BaseModel):
    entity_type: Literal["contractor", "project", "article"] | str
    entity_id: int
    entity_name: str | None = None
    content: str
    score: float
    url: str | None = None  # profile_url (nhà thầu) / project_url (dự án)
    source: SourceRef | None = None
    usage_note: str = USAGE_NOTE_VI
