"""Automatic source-preserving cleanup Activities with atomic output writes."""

import asyncio
import json
from pathlib import Path
from typing import TypedDict, cast

from temporalio import activity

from src.cleanup import (
    stage1_ftfy_cleanup,
    stage2_rule_based_cleanup,
)
from src.quality.cleanup_context import ManualData
from src.quality.core import write_bytes


class CleanupResult(TypedDict, total=False):
    """Serializable cleanup Activity result."""

    success: bool
    fixes: int
    removals: int
    corrections: int
    corrected_product_name: str | None
    tags: list[str] | None
    error: str | None


def _read(path: Path) -> ManualData:
    return cast(ManualData, json.loads(path.read_text(encoding="utf-8")))


def _save(path: Path, data: ManualData) -> None:
    write_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())


def _normalize_file(json_path: str, stage: int) -> CleanupResult:
    path = Path(json_path)
    data = _read(path)
    if stage == 1:
        count = stage1_ftfy_cleanup(data)
        result: CleanupResult = {"success": True, "fixes": count}
    else:
        count = stage2_rule_based_cleanup(data)
        result = {"success": True, "removals": count}
    _save(path, data)
    return result


@activity.defn
async def ftfy_cleanup_activity(json_path: str) -> CleanupResult:
    """Normalize English encoding without modifying Japanese source text."""
    try:
        return await asyncio.to_thread(_normalize_file, json_path, 1)
    except Exception as error:  # noqa: BLE001 — cleanup remains an optional improvement.
        activity.logger.warning(f"Encoding cleanup failed: {error}")
        return {"success": False, "fixes": 0, "error": str(error)}


@activity.defn
async def rule_based_cleanup_activity(json_path: str) -> CleanupResult:
    """Normalize formatting while preserving source labels and potential ruby."""
    try:
        return await asyncio.to_thread(_normalize_file, json_path, 2)
    except Exception as error:  # noqa: BLE001 — cleanup remains an optional improvement.
        activity.logger.warning(f"Formatting cleanup failed: {error}")
        return {"success": False, "removals": 0, "error": str(error)}
