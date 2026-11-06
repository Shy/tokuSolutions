"""Keep source step references while presenting numbered and unnumbered lists consistently."""

from src.quality.instruction_numbering import format_instruction_items


def test_numbered_items_use_source_circles_without_double_bullets() -> None:
    """Numbering is source backed and does not alter simultaneous actions."""
    assert (
        format_instruction_items(
            "①押しながら②合わせます。③離します。",
            ["①While pressing, ②Align the joint.", "3. Release the button."],
            bullets=True,
        )
        == "① While pressing, ② Align the joint.\n③ Release the button."
    )


def test_unverified_numbers_codes_and_unordered_items_stay_distinct() -> None:
    """Do not reinterpret quantities or version numbers as diagram steps."""
    assert (
        format_instruction_items(
            "電池3個。\n●入れます。\n●閉めます。",
            ["Use 3 batteries.", "Close the cover."],
            bullets=True,
        )
        == "• Use 3 batteries.\n• Close the cover."
    )
    assert (
        format_instruction_items("ver. 2.0", ["2.0 is the version."], bullets=False)
        == "2.0 is the version."
    )


def test_polarity_ocr_does_not_turn_a_symbol_into_a_required_step_number() -> None:
    """Independent OCR must corroborate the same battery-polarity clause."""
    from src.quality.quantities import reconcile_polarity_symbols

    assert (
        reconcile_polarity_symbols(
            "ボタン電池は上側が④に、下側がに", "上側が+に、\n下側が−に"
        )
        == "ボタン電池は上側が+に、下側がに"
    )
    assert (
        reconcile_polarity_symbols("電池は4個、④押します。", "電池は4個、+押します。")
        == "電池は4個、④押します。"
    )
    assert (
        reconcile_polarity_symbols("電池は上側が④に", "④電池を入れます。")
        == "電池は上側が④に"
    )


def test_existing_overlay_numbering_preserves_unordered_bullets() -> None:
    """Normalize old accepted panels without deleting their unnumbered items."""
    from src.quality.instruction_numbering import normalize_step_markers

    assert (
        normalize_step_markers(
            "①押します。③離します。",
            "①While pressing, ②Align the joint.\n• 3. Release.\n• Do the same on the left.",
        )
        == "① While pressing, ② Align the joint.\n③ Release.\n• Do the same on the left."
    )
