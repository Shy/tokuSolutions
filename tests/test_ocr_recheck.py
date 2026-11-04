"""Crop evidence remains advisory when independent OCR readers disagree."""

from src.quality.ocr_recheck import compare_readings


def test_ocr_consensus_preserves_polarity_conflicts() -> None:
    """Whitespace differences are harmless; a lost minus sign is not."""
    assert compare_readings("上側＋ 下側−", "上側+\n下側-") == "agreed"
    assert compare_readings("下側-", "下側+") == "conflict"
    assert compare_readings("", "下側-") == "unavailable"
