"""Normalize provider layouts without losing cells, coordinates or scan-only status."""

from pathlib import Path

import pytest
from pydantic import JsonValue

from src.quality.layout_engines import normalize_docling, normalize_pymupdf


def test_docling_preserves_cell_topology_and_converts_bottom_left_boxes() -> None:
    raw: dict[str, JsonValue] = {
        "pages": {"1": {"size": {"width": 100, "height": 200}}},
        "texts": [
            {
                "self_ref": "#/texts/0",
                "label": "section_header",
                "text": "見出し",
                "prov": [
                    {
                        "page_no": 1,
                        "bbox": {
                            "l": 10,
                            "t": 180,
                            "r": 90,
                            "b": 160,
                            "coord_origin": "BOTTOMLEFT",
                        },
                    }
                ],
            }
        ],
        "tables": [
            {
                "self_ref": "#/tables/0",
                "prov": [
                    {
                        "page_no": 1,
                        "bbox": {
                            "l": 10,
                            "t": 140,
                            "r": 90,
                            "b": 40,
                            "coord_origin": "BOTTOMLEFT",
                        },
                    }
                ],
                "data": {
                    "num_rows": 2,
                    "num_cols": 2,
                    "table_cells": [
                        {
                            "text": "A",
                            "start_row_offset_idx": 0,
                            "end_row_offset_idx": 1,
                            "start_col_offset_idx": 0,
                            "end_col_offset_idx": 2,
                        }
                    ],
                },
            }
        ],
    }
    page = normalize_docling(raw, 5)
    assert page.page == 5
    assert page.regions[0].bbox == (0.1, 0.1, 0.8, 0.1)
    assert page.tables[0].rows == 2
    assert page.tables[0].cells[0].column_span == 2


def test_pymupdf_empty_native_text_is_scan_only_not_a_successful_ocr() -> None:
    page = normalize_pymupdf(
        {
            "width": 100,
            "height": 200,
            "blocks": [],
            "tables": [],
            "image_count": 1,
            "vector_count": 0,
        },
        1,
    )
    assert page.source_kind == "image_only"
    assert page.regions == []
    assert page.tables == []


def test_docling_uses_document_tree_reading_order() -> None:
    raw: dict[str, JsonValue] = {
        "pages": {"1": {"size": {"width": 100, "height": 100}}},
        "texts": [
            {
                "self_ref": "#/texts/0",
                "text": "Second",
                "prov": [
                    {
                        "bbox": {
                            "l": 0,
                            "t": 0,
                            "r": 20,
                            "b": 10,
                            "coord_origin": "TOPLEFT",
                        }
                    }
                ],
            },
            {
                "self_ref": "#/texts/1",
                "text": "First",
                "prov": [
                    {
                        "bbox": {
                            "l": 0,
                            "t": 20,
                            "r": 20,
                            "b": 30,
                            "coord_origin": "TOPLEFT",
                        }
                    }
                ],
            },
        ],
        "body": {"children": [{"$ref": "#/texts/1"}, {"$ref": "#/texts/0"}]},
    }
    assert [region.text for region in normalize_docling(raw, 1).regions] == [
        "First",
        "Second",
    ]


def test_native_pdf_arm_preserves_actual_table_cell_boxes(tmp_path: "Path") -> None:
    """An image-only result is not caused by a broken native-table adapter."""
    import pymupdf

    from src.activities.layout_benchmark import _pymupdf_page

    path = tmp_path / "native.pdf"
    with pymupdf.open() as document:
        page = document.new_page(width=200, height=200)
        for y in [20, 70, 120]:
            page.draw_line((20, y), (180, y))
        for x in [20, 100, 180]:
            page.draw_line((x, 20), (x, 120))
        for x, y, text in [
            (30, 50, "A"),
            (110, 50, "B"),
            (30, 100, "C"),
            (110, 100, "D"),
        ]:
            page.insert_text((x, y), text)
        document.save(path)
    result = normalize_pymupdf(_pymupdf_page(path, ocr=False, tessdata=tmp_path), 1)
    assert len(result.tables) == 1
    assert result.tables[0].rows == 2 and result.tables[0].columns == 2
    assert all(cell.bbox is not None for cell in result.tables[0].cells)


def test_cached_provider_directory_rejects_changed_source_configuration(
    tmp_path: Path,
) -> None:
    """Changed page selections or OCR settings must not silently reuse old page JSON."""
    import pytest

    from src.activities.layout_benchmark import _freeze_configuration

    _freeze_configuration(tmp_path, b"first input", "/isolated/python")
    _freeze_configuration(tmp_path, b"first input", "/isolated/python")
    with pytest.raises(ValueError, match="configuration changed"):
        _freeze_configuration(tmp_path, b"different OCR setting", "/isolated/python")


def test_google_layout_recurses_and_preserves_merged_cell_geometry() -> None:
    """Google block coordinates and span topology survive normalization."""
    from src.quality.layout_engines import normalize_google

    def text(identity: str, value: str, left: float) -> dict[str, JsonValue]:
        return {
            "blockId": identity,
            "textBlock": {"text": value, "type": "paragraph"},
            "boundingBox": {
                "normalizedVertices": [
                    {"x": left, "y": 0.2},
                    {"x": left + 0.1, "y": 0.25},
                ]
            },
        }

    raw: dict[str, JsonValue] = {
        "documentLayout": {
            "blocks": [
                {
                    "blockId": "title",
                    "textBlock": {
                        "text": "Header",
                        "type": "heading-1",
                        "blocks": [
                            {
                                "blockId": "table",
                                "boundingBox": {
                                    "normalizedVertices": [
                                        {"x": 0.1, "y": 0.1},
                                        {"x": 0.9, "y": 0.5},
                                    ]
                                },
                                "tableBlock": {
                                    "headerRows": [
                                        {
                                            "cells": [
                                                {
                                                    "rowSpan": 2,
                                                    "colSpan": 1,
                                                    "blocks": [text("a", "A", 0.1)],
                                                },
                                                {
                                                    "colSpan": 2,
                                                    "blocks": [text("b", "B", 0.3)],
                                                },
                                            ]
                                        }
                                    ],
                                    "bodyRows": [
                                        {
                                            "cells": [
                                                {"blocks": [text("c", "C", 0.3)]},
                                                {"blocks": [text("d", "D", 0.6)]},
                                            ]
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                }
            ]
        }
    }
    page = normalize_google(raw, 5)
    assert page.tables[0].rows == 2
    assert page.tables[0].columns == 3
    assert [
        (cell.row, cell.column, cell.row_span, cell.column_span)
        for cell in page.tables[0].cells
    ] == [(0, 0, 2, 1), (0, 1, 1, 2), (1, 1, 1, 1), (1, 2, 1, 1)]
    assert page.tables[0].cells[0].bbox is not None
    assert page.tables[0].cells[0].text == "A"
    assert page.unlocated_regions[0].text == "Header"


def test_google_layout_without_boxes_does_not_invent_page_geometry() -> None:
    """Structure-only versions retain text and tables without fabricated rectangles."""
    from src.quality.layout_engines import normalize_google

    page = normalize_google(
        {
            "documentLayout": {
                "blocks": [
                    {
                        "blockId": "p",
                        "textBlock": {"text": "日本語", "type": "paragraph"},
                    },
                    {
                        "blockId": "t",
                        "tableBlock": {
                            "bodyRows": [
                                {
                                    "cells": [
                                        {
                                            "blocks": [
                                                {
                                                    "blockId": "c",
                                                    "textBlock": {
                                                        "text": "セル",
                                                        "type": "paragraph",
                                                    },
                                                }
                                            ]
                                        }
                                    ]
                                }
                            ]
                        },
                    },
                ]
            }
        },
        1,
    )
    assert page.regions == []
    assert [region.text for region in page.unlocated_regions] == ["日本語", "", "セル"]
    assert page.tables[0].bbox is None
    assert page.tables[0].cells[0].bbox is None
    assert page.tables[0].cells[0].text == "セル"


def test_google_version_change_cannot_reuse_another_versions_cache(
    tmp_path: Path,
) -> None:
    """Processor/version identity belongs to the frozen request configuration."""
    import pytest

    from src.activities.layout_benchmark import _freeze_configuration

    _freeze_configuration(tmp_path, b"same pages", "/python", "processor", "v1")
    with pytest.raises(ValueError, match="configuration changed"):
        _freeze_configuration(tmp_path, b"same pages", "/python", "processor", "v2")


@pytest.mark.anyio
async def test_cached_google_activity_runs_heartbeats_on_the_worker_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cached page normalization must work inside the real async Activity context."""
    import json

    from google.auth.credentials import AnonymousCredentials
    from temporalio.testing import ActivityEnvironment

    from src.activities.layout_benchmark import google_layout_activity
    from src.layout_models import LayoutBenchmarkInput
    from src.quality import providers

    anonymous = AnonymousCredentials()  # type: ignore[no-untyped-call]
    monkeypatch.setattr(providers, "google_credentials", lambda root: anonymous)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"pages": [{"page": 1, "sha256": "source"}]}))
    processor = "projects/p/locations/us/processors/one"
    raw = tmp_path / "google-layout/raw/page-0001.json"
    raw.parent.mkdir(parents=True)
    raw.write_text(
        json.dumps(
            {
                "processor": processor + "/processorVersions/v1",
                "source_sha256": "source",
                "elapsed_seconds": 1.0,
                "document": {"documentLayout": {"blocks": []}},
            }
        )
    )
    request = LayoutBenchmarkInput(
        str(manifest),
        str(tmp_path),
        "/python",
        google_processor=processor,
        google_version="v1",
    )
    import asyncio

    observed_loops: list[asyncio.AbstractEventLoop] = []
    environment = ActivityEnvironment()
    environment.on_heartbeat = lambda *details: observed_loops.append(
        asyncio.get_running_loop()
    )
    target = await environment.run(google_layout_activity, request)
    assert json.loads(Path(target).read_bytes())["status"] == "complete"
    assert observed_loops == [asyncio.get_running_loop()]


@pytest.fixture
def anyio_backend() -> str:
    """Use the actual async Worker's event loop."""
    return "asyncio"


@pytest.mark.parametrize(
    "raw",
    [{"error": {"code": 13, "message": "Failed"}}, {"text": "Ordinary OCR output"}],
)
def test_google_adapter_rejects_error_or_wrong_processor_output(
    raw: dict[str, JsonValue],
) -> None:
    """An API error or OCR-only response is not a successful layout result."""
    from src.quality.layout_engines import normalize_google

    with pytest.raises(ValueError):
        normalize_google(raw, 1)


def test_comparison_cli_does_not_report_success_when_a_provider_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Independent provider failures stay visible in the shell exit status."""
    from click.testing import CliRunner

    from src.layout_models import LayoutBenchmarkInput
    from src.quality import layout_compare

    async def failed_run(request: LayoutBenchmarkInput, address: str) -> list[str]:
        return ["FAILED google_layout_activity: unavailable"]

    monkeypatch.setattr(layout_compare, "run", failed_run)
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}")
    python = tmp_path / "python"
    python.touch()
    result = CliRunner().invoke(
        layout_compare.main,
        [
            "--manifest",
            str(manifest),
            "--output",
            str(tmp_path / "results"),
            "--docling-python",
            str(python),
            "--provider",
            "google",
        ],
    )
    assert result.exit_code != 0
    assert "FAILED google_layout_activity" in result.output
