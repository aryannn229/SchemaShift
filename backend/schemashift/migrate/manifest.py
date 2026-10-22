"""The migration manifest: what ran, how far it got, what was quarantined and what was done to
recover.

It is a JSON file written atomically (temp file + rename) after every batch, so a process that is
killed
at any instruction leaves either the previous or the next consistent manifest on disk.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

State = Literal["created", "running", "interrupted", "completed", "failed", "rolled_back"]
StepState = Literal["pending", "running", "done", "skipped", "failed", "rolled_back"]


class TableProgress(BaseModel):
    table: str
    collection: str
    total_rows: int = 0  # rows in the source when the run started
    written: int = 0  # documents upserted
    quarantined: int = 0
    batches: int = 0
    cursor: list[Any] | None = None  # last primary-key value written (keyset paging)
    offset: int = 0  # tables without a primary key page by offset
    state: StepState = "pending"


class QuarantinedRecord(BaseModel):
    collection: str
    key: Any
    reasons: list[str]


class ScriptStep(BaseModel):
    id: str
    description: str
    reversibility: str
    state: StepState = "pending"
    snapshot: str | None = None  # collection holding the pre-step snapshot (irreversible steps)


class Manifest(BaseModel):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    kind: Literal["full", "incremental"] = "full"
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    state: State = "created"
    database: str = ""
    batch_size: int = 500
    tables: list[TableProgress] = []
    steps: list[ScriptStep] = []
    quarantined: list[QuarantinedRecord] = []
    quarantine_total: int = 0
    recovery_actions: list[str] = []
    checks: list[dict[str, Any]] = []
    source_fingerprint: dict[str, int] = {}
    error: str | None = None
    resumed: int = 0

    def table(self, name: str) -> TableProgress:
        return next(t for t in self.tables if t.table == name)

    def step(self, step_id: str) -> ScriptStep:
        return next(s for s in self.steps if s.id == step_id)

    def action(self, text: str) -> None:
        self.recovery_actions.append(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}")


def manifest_path(state_dir: Path) -> Path:
    return state_dir / "manifest.json"


def save(manifest: Manifest, state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    manifest.updated_at = time.time()
    data = manifest.model_dump_json(indent=2)
    fd, tmp = tempfile.mkstemp(dir=state_dir, prefix=".manifest-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, manifest_path(state_dir))
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load(state_dir: Path) -> Manifest | None:
    path = manifest_path(state_dir)
    if not path.exists():
        return None
    return Manifest.model_validate(json.loads(path.read_text(encoding="utf-8")))
