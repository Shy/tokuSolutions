"""Official PDF quotations are fetched independently with bounded provenance."""

from pathlib import Path
from unittest.mock import patch

from src.quality.pdf_evidence import extract_pdf_evidence


def test_pdf_text_evidence_is_page_bound_and_records_content_hash(
    tmp_path: Path,
) -> None:
    """A quoted title cannot be manufactured by joining unrelated PDF pages."""
    import pymupdf

    with pymupdf.open() as document:
        document.new_page().insert_text((40, 40), "FANGMEMORY")
        document.new_page().insert_text((40, 40), "XTREMEMEMORY")
        content = document.tobytes()
    evidence = extract_pdf_evidence(content, tmp_path, tmp_path)
    assert evidence.pages[0].page == 1
    assert "FANGMEMORY" in evidence.pages[0].text
    assert evidence.sha256
    assert "XTREMEMEMORY" not in evidence.pages[0].text


def test_scanned_pdf_uses_independent_ocr_not_the_agents_quote(tmp_path: Path) -> None:
    """Scanned evidence is accepted only from an actual OCR result."""
    import pymupdf

    with pymupdf.open() as document:
        document.new_page()
        content = document.tobytes()
    with patch(
        "src.quality.pdf_evidence.ocr_pdf_page",
        return_value="ファングメモリ FANGMEMORY",
    ):
        evidence = extract_pdf_evidence(content, tmp_path, tmp_path)
    assert evidence.pages[0].method == "documentai"
    assert evidence.pages[0].text == "ファングメモリ FANGMEMORY"


def test_cached_pdf_quote_cannot_join_separate_pages() -> None:
    """A bilingual quote requires both spellings in a single real source page."""
    from src.quality.web_research import CachedPage

    cached = CachedPage(text="", pages=["ファングメモリ", "FANGMEMORY"])
    assert cached.matching_text("ファングメモリ FANGMEMORY") == ""
    assert cached.matching_text("ファングメモリ") == "ファングメモリ"


def test_official_pdf_reader_fetches_once_and_checks_quotes_per_page(
    tmp_path: Path,
) -> None:
    """Exercise the actual research boundary without trusting model-provided text."""
    import pymupdf

    from src.quality.research import WikidataLookup
    from src.quality.web_research import GlossaryResearcher

    with pymupdf.open() as document:
        document.new_page().insert_text((40, 40), "FANGMEMORY")
        document.new_page().insert_text((40, 40), "XTREMEMEMORY")
        content = document.tobytes()
    researcher = GlossaryResearcher(
        WikidataLookup(tmp_path / "lookup"), tmp_path / "web"
    )
    with patch("src.quality.web_research.requests.get") as get:
        response = get.return_value.__enter__.return_value
        response.is_redirect = False
        response.headers = {"Content-Type": "application/pdf"}
        response.iter_content.return_value = [content]
        assert (
            researcher._page_text(
                "https://toy.bandai.co.jp/manuals/fixture.pdf", "FANGMEMORY"
            )
            == "FANGMEMORY"
        )
        assert (
            researcher._page_text(
                "https://toy.bandai.co.jp/manuals/fixture.pdf",
                "FANGMEMORY XTREMEMEMORY",
            )
            == ""
        )
        assert get.call_count == 1
