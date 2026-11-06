"""Provider-neutral layouts retain geometry, cell spans and original provider JSON."""

from pydantic import JsonValue

from src.layout_models import LayoutCell, LayoutPage, LayoutRegion, LayoutTable


def _object(value: JsonValue) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _array(value: JsonValue) -> list[JsonValue]:
    return value if isinstance(value, list) else []


def _number(value: JsonValue, default: float = 0) -> float:
    return float(value) if isinstance(value, (int, float)) else default


def _text(value: JsonValue) -> str:
    return value if isinstance(value, str) else ""


def _box(
    value: JsonValue, width: float, height: float
) -> tuple[float, float, float, float]:
    bounds = _object(value)
    left, right = _number(bounds.get("l")), _number(bounds.get("r"))
    top, bottom = _number(bounds.get("t")), _number(bounds.get("b"))
    if bounds.get("coord_origin") == "BOTTOMLEFT":
        top, bottom = height - top, height - bottom
    return left / width, top / height, (right - left) / width, (bottom - top) / height


def normalize_docling(raw: dict[str, JsonValue], page: int) -> LayoutPage:
    """Convert Docling's coordinate origins and table topology without flattening."""
    size = _object(_object(_object(raw.get("pages")).get("1")).get("size"))
    width, height = _number(size.get("width"), 1), _number(size.get("height"), 1)
    result = LayoutPage(page=page, provider="docling")
    for field in ("texts", "pictures", "tables"):
        for index, value in enumerate(_array(raw.get(field))):
            item = _object(value)
            provenance = _array(item.get("prov"))
            if not provenance:
                continue
            box = _box(_object(provenance[0]).get("bbox"), width, height)
            identifier = _text(item.get("self_ref")) or f"{field}-{index}"
            kind = _text(item.get("label")) or field.rstrip("s")
            result.regions.append(
                LayoutRegion(
                    id=identifier, kind=kind, text=_text(item.get("text")), bbox=box
                )
            )
            if field != "tables":
                continue
            data = _object(item.get("data"))
            cells = []
            for value in _array(data.get("table_cells")):
                cell = _object(value)
                row = int(_number(cell.get("start_row_offset_idx")))
                column = int(_number(cell.get("start_col_offset_idx")))
                cells.append(
                    LayoutCell(
                        row=row,
                        column=column,
                        row_span=max(
                            1,
                            int(_number(cell.get("end_row_offset_idx"), row + 1)) - row,
                        ),
                        column_span=max(
                            1,
                            int(_number(cell.get("end_col_offset_idx"), column + 1))
                            - column,
                        ),
                        text=_text(cell.get("text")),
                        bbox=_box(cell["bbox"], width, height)
                        if cell.get("bbox")
                        else None,
                    )
                )
            result.tables.append(
                LayoutTable(
                    id=identifier,
                    bbox=box,
                    rows=int(_number(data.get("num_rows"))),
                    columns=int(_number(data.get("num_cols"))),
                    cells=cells,
                )
            )
    nodes = {
        _text(_object(value).get("self_ref")): _object(value)
        for field in ("texts", "tables", "pictures", "groups")
        for value in _array(raw.get(field))
    }
    ranks: dict[str, int] = {}

    def visit(node: dict[str, JsonValue]) -> None:
        for value in _array(node.get("children")):
            reference = _text(_object(value).get("$ref"))
            if reference in ranks:
                continue
            ranks[reference] = len(ranks)
            if reference in nodes:
                visit(nodes[reference])

    visit(_object(raw.get("body")))
    result.regions.sort(key=lambda region: ranks.get(region.id, len(ranks)))
    return result


def normalize_pymupdf(
    raw: dict[str, JsonValue], page: int, *, ocr: bool = False
) -> LayoutPage:
    """Distinguish native extraction from OCR-assisted text/table inference."""
    width, height = _number(raw.get("width"), 1), _number(raw.get("height"), 1)
    blocks = _array(raw.get("blocks"))
    result = LayoutPage(
        page=page,
        provider="pymupdf-ocr" if ocr else "pymupdf-native",
        source_kind="ocr" if ocr else "native_text" if blocks else "image_only",
    )

    def box(value: JsonValue) -> tuple[float, float, float, float]:
        bounds = [_number(item) for item in _array(value)]
        return (
            bounds[0] / width,
            bounds[1] / height,
            (bounds[2] - bounds[0]) / width,
            (bounds[3] - bounds[1]) / height,
        )

    for index, value in enumerate(blocks):
        block = _object(value)
        result.regions.append(
            LayoutRegion(
                id=f"text-{index}",
                kind="text",
                text=_text(block.get("text")),
                bbox=box(block.get("bbox")),
            )
        )
    for index, value in enumerate(_array(raw.get("tables"))):
        table = _object(value)
        cells = [
            LayoutCell.model_validate(value) for value in _array(table.get("cells"))
        ]
        result.tables.append(
            LayoutTable(
                id=f"table-{index}",
                bbox=box(table.get("bbox")),
                rows=int(_number(table.get("rows"))),
                columns=int(_number(table.get("columns"))),
                cells=cells,
            )
        )
    return result


def _google_bounds(
    block: dict[str, JsonValue],
) -> tuple[float, float, float, float] | None:
    points = [
        _object(value)
        for value in _array(_object(block.get("boundingBox")).get("normalizedVertices"))
    ]
    if not points:
        return None
    horizontal = [_number(point.get("x")) for point in points]
    vertical = [_number(point.get("y")) for point in points]
    left, top = min(horizontal), min(vertical)
    width, height = max(horizontal) - left, max(vertical) - top
    if (
        left < 0
        or top < 0
        or max(horizontal) > 1.001
        or max(vertical) > 1.001
        or width <= 0
        or height <= 0
    ):
        raise ValueError("Invalid Google normalized layout geometry")
    return left, top, width, height


def _google_children(block: dict[str, JsonValue]) -> list[dict[str, JsonValue]]:
    text = _object(block.get("textBlock"))
    children = [_object(value) for value in _array(text.get("blocks"))]
    for entry in _array(_object(block.get("listBlock")).get("listEntries")):
        children.extend(
            _object(value) for value in _array(_object(entry).get("blocks"))
        )
    return children


def _google_text(blocks: list[dict[str, JsonValue]]) -> str:
    return "\n".join(
        text
        for block in blocks
        for text in (
            _text(_object(block.get("textBlock")).get("text")),
            _google_text(_google_children(block)),
        )
        if text
    )


def _google_boxes(
    blocks: list[dict[str, JsonValue]],
) -> list[tuple[float, float, float, float]]:
    boxes = []
    for block in blocks:
        bounds = _google_bounds(block)
        if bounds:
            boxes.append(bounds)
        boxes.extend(_google_boxes(_google_children(block)))
    return boxes


def normalize_google(raw: dict[str, JsonValue], page: int) -> LayoutPage:
    """Preserve Google's nested order/topology and distinguish absent coordinates.

    Cell rectangles are unions of the supplied child text extents, not inferred
    printed cell boundaries. Structure-only versions never receive fake boxes.
    """
    from src.layout_models import UnlocatedLayoutRegion

    if _object(raw.get("error")).get("code"):
        raise ValueError("Google returned a document processing error")
    if "documentLayout" not in raw:
        raise ValueError("Google Layout response has no documentLayout")
    result = LayoutPage(page=page, provider="google-layout")

    def visit(block: dict[str, JsonValue], fallback: str) -> None:
        identity = _text(block.get("blockId")) or fallback
        text_block = _object(block.get("textBlock"))
        kind = _text(text_block.get("type")) or "text"
        if kind.startswith("heading") or kind == "header":
            kind = "section_header"
        if kind == "paragraph":
            kind = "text"
        table_block = _object(block.get("tableBlock"))
        if "tableBlock" in block:
            kind = "table"
        elif "listBlock" in block:
            kind = "list"
        elif "imageBlock" in block:
            kind = "picture"
        bounds = _google_bounds(block)
        text = _text(text_block.get("text"))
        if bounds is not None:
            result.regions.append(
                LayoutRegion(id=identity, kind=kind, text=text, bbox=bounds)
            )
        else:
            result.unlocated_regions.append(
                UnlocatedLayoutRegion(id=identity, kind=kind, text=text)
            )
        if "tableBlock" in block:
            rows = [
                _object(value)
                for value in _array(table_block.get("headerRows"))
                + _array(table_block.get("bodyRows"))
            ]
            occupied: set[tuple[int, int]] = set()
            cells = []
            columns = 0
            for row_index, row in enumerate(rows):
                column = 0
                for value in _array(row.get("cells")):
                    cell = _object(value)
                    while (row_index, column) in occupied:
                        column += 1
                    row_span = max(1, int(_number(cell.get("rowSpan"), 1)))
                    column_span = max(1, int(_number(cell.get("colSpan"), 1)))
                    children = [_object(child) for child in _array(cell.get("blocks"))]
                    boxes = _google_boxes(children)
                    cell_box = None
                    if boxes:
                        left, top = (
                            min(box[0] for box in boxes),
                            min(box[1] for box in boxes),
                        )
                        cell_box = (
                            left,
                            top,
                            max(box[0] + box[2] for box in boxes) - left,
                            max(box[1] + box[3] for box in boxes) - top,
                        )
                    cells.append(
                        LayoutCell(
                            row=row_index,
                            column=column,
                            row_span=row_span,
                            column_span=column_span,
                            text=_google_text(children),
                            bbox=cell_box,
                            bbox_kind="text_extent",
                        )
                    )
                    for occupied_row in range(row_index, row_index + row_span):
                        for occupied_column in range(column, column + column_span):
                            occupied.add((occupied_row, occupied_column))
                    for index, child in enumerate(children):
                        visit(child, f"{identity}/r{row_index}c{column}/{index}")
                    column += column_span
                    columns = max(columns, column)
            result.tables.append(
                LayoutTable(
                    id=identity,
                    bbox=bounds,
                    rows=len(rows),
                    columns=columns,
                    cells=cells,
                )
            )
        for index, child in enumerate(_google_children(block)):
            visit(child, f"{identity}/{index}")

    for index, value in enumerate(
        _array(_object(raw.get("documentLayout")).get("blocks"))
    ):
        visit(_object(value), f"block-{index}")
    return result
