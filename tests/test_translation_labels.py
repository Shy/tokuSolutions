"""Deterministic source-label restoration before optional AI cleanup."""

from src.quality.cleanup_context import ManualData


def test_literal_labels_are_restored_even_when_model_does_not_correct_them() -> None:
    """Digits, switch positions and circled step numbers must survive translation."""
    from src.cleanup import stage2_rule_based_cleanup

    originals = ["①", "ON", "LR44 X 3", "+", "JOKER"]
    manual: ManualData = {
        "meta": {},
        "pages": [
            {
                "blocks": [
                    {"text": text, "translation": "Wrong", "bbox": [0, 0, 0.1, 0.1]}
                    for text in originals
                ]
            }
        ],
    }
    stage2_rule_based_cleanup(manual)
    assert [block["translation"] for block in manual["pages"][0]["blocks"]] == originals
