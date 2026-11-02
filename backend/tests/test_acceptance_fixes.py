"""Hồi quy cho các lỗi phát hiện ở giai đoạn nghiệm thu MVP (docs/DECISIONS.md — "Nghiệm thu MVP — sửa lỗi")."""
from __future__ import annotations

import runpy
from contextlib import contextmanager

from sqlalchemy import select, text

from app.db.models import Contractor


# ---------------------------------------------------------------- ops-F1: hook seed khi chạy `python -m app.db.seed`
def test_seed_as_main_runs_hooks(db, monkeypatch, capsys):
    import app.db.seed as seed_module

    db.execute(text("UPDATE contractors SET profile_completeness = NULL"))
    db.execute(text("DELETE FROM documents"))
    db.flush()

    @contextmanager
    def fake_session():
        yield db

    monkeypatch.setattr(seed_module, "SessionLocal", fake_session)
    runpy.run_module("app.db.seed", run_name="__main__")
    assert "Seed OK" in capsys.readouterr().out
    values = db.scalars(select(Contractor.profile_completeness)).all()
    assert values and all(v is not None for v in values)
    assert db.scalar(text("SELECT count(*) FROM documents")) > 0
