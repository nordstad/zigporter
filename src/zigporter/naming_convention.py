"""Persistent naming convention for zigporter's smart-rename workflow.

Stores the user's preferred device-naming pattern so it doesn't need to be
passed on every invocation of ``/smart-rename``.

State is stored in ``~/.config/zigporter/naming-convention.json``.
"""

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field


class NamingConvention(BaseModel):
    pattern: str  # e.g. "{area}_{type}_{desc}"
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    examples: list[str] = Field(default_factory=list)  # well-named reference devices


def load_convention(path: Path) -> NamingConvention | None:
    """Return saved convention or None if not yet configured."""
    if path.exists():
        return NamingConvention.model_validate_json(path.read_text())
    return None


def save_convention(convention: NamingConvention, path: Path) -> None:
    """Persist naming convention to disk."""
    convention.updated_at = datetime.now(tz=timezone.utc)
    path.write_text(convention.model_dump_json(indent=2))
