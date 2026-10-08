"""Advisor response cache keyed by (prompt template version, model, prompt)."""

from __future__ import annotations

import hashlib
import threading
from typing import Any, Protocol

from schemashift.ai.prompts import PROMPT_VERSION


class AdvisorCache(Protocol):
    def get(self, key: str) -> dict[str, Any] | None: ...

    def put(self, key: str, value: dict[str, Any]) -> None: ...


def cache_key(model: str, prompt: str, version: str = PROMPT_VERSION) -> str:
    return hashlib.sha256(f"{version}\x1f{model}\x1f{prompt}".encode()).hexdigest()


class InMemoryCache:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._data.get(key)

    def put(self, key: str, value: dict[str, Any]) -> None:
        with self._lock:
            self._data[key] = value

    def __len__(self) -> int:
        return len(self._data)
