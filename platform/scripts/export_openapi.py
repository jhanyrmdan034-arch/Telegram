#!/usr/bin/env python3
"""Export the OpenAPI document without starting a server (used by `make openapi`)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres@localhost/postgres")
os.environ.setdefault("REDIS_URL", "memory://")

from app.main import create_app  # noqa: E402

app = create_app()
document = app.openapi()

output = ROOT / "docs" / "openapi.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")

operations = sum(
    1 for path in document["paths"].values()
    for method in path
    if method in {"get", "post", "put", "patch", "delete"}
)
print(f"wrote {output} · {operations} operations · {len(document['components']['schemas'])} schemas")
