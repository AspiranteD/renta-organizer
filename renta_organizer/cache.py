"""Simple JSON cache for API results — avoids re-calling LLM on repeated runs."""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ResultCache:
    """Key-value cache backed by a JSON file."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, dict] = self._load()
        self._dirty = False

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Cache corrupta, empezando de cero: %s", self.path)
            return {}

    def save(self) -> None:
        if not self._dirty:
            return
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self._dirty = False

    def get(self, key: str) -> dict | None:
        return self._data.get(key)

    def set(self, key: str, value: dict) -> None:
        self._data[key] = value
        self._dirty = True

    def has(self, key: str) -> bool:
        return key in self._data

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, key: str) -> bool:
        return key in self._data


def make_transaction_key(date_str: str, description: str, amount: float) -> str:
    raw = f"{date_str}|{description.strip().lower()}|{amount:.2f}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def make_pdf_key(file_path: str, file_size: int) -> str:
    raw = f"{Path(file_path).name}|{file_size}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]
