"""Tiện ích test cho thẻ câu hỏi từng bước (question_step)."""
from app.ai.dialog import SKIP_VALUE

# Phần hỏi chỉ gồm 5 câu cốt lõi: không còn câu tùy chọn nào trước bản tóm tắt (giữ tên cho các test cũ).
SKIP_OPTIONAL: list[str] = []
assert SKIP_VALUE  # nút "Bỏ qua" vẫn dùng cho câu chi tiết (khi khách bấm "Cung cấp thêm thông tin")


def step(r):
    """Thẻ question_step trong kết quả lượt (TurnResult hoặc danh sách block dạng dict), None nếu không có."""
    blocks = r.ui_blocks if hasattr(r, "ui_blocks") else r
    for b in blocks:
        t = b.get("type") if isinstance(b, dict) else b.type
        if t == "question_step":
            return b
    return None


def question(r) -> str:
    """Tiêu đề câu hỏi trên thẻ question_step ("" nếu không có thẻ)."""
    s = step(r)
    if s is None:
        return ""
    return s["question"] if isinstance(s, dict) else s.question
