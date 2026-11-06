"""Use independent layout geometry as evidence without replacing recognized text."""

from pydantic import Field

from src.layout_models import LayoutPage
from src.quality.cleanup_context import ContextPage
from src.quality.models import Artifact
from src.quality.page_grouping import PagePlan, incomplete_source_clause

Box = tuple[float, float, float, float]


class StructuralRegion(Artifact):
    """A source-matched structural hypothesis, deliberately excluding OCR text."""

    id: str
    kind: str
    bbox: Box
    source_ids: list[int]


class StructuralVariant(Artifact):
    """One extractor configuration; competing variants retain their independence."""

    provider: str
    regions: list[StructuralRegion]
    assignments: dict[int, str]


class StructureEvidence(Artifact):
    """Advisory geometry plus explicitly missing, ambiguous and conflicting evidence."""

    version: int = 1
    variants: list[StructuralVariant]
    unmatched_source_ids: list[int]
    ambiguous_source_ids: list[int]
    conflicts: list[str] = Field(default_factory=list)
    continuation_source_ids: list[int] = Field(default_factory=list)


def _coverage(source: Box, region: Box) -> float:
    left = max(source[0], region[0])
    top = max(source[1], region[1])
    right = min(source[0] + source[2], region[0] + region[2])
    bottom = min(source[1] + source[3], region[1] + region[3])
    return (
        max(0, right - left) * max(0, bottom - top) / max(source[2] * source[3], 1e-12)
    )


def _relation(variant: StructuralVariant, first: int, second: int) -> str:
    if any(
        region.kind == "picture"
        and region.bbox[2] * region.bbox[3] < 0.65
        and ((first in region.source_ids) != (second in region.source_ids))
        for region in variant.regions
    ):
        return "separate"
    first_id, second_id = (
        variant.assignments.get(first),
        variant.assignments.get(second),
    )
    if first_id is None or second_id is None:
        return "unknown"
    if first_id == second_id:
        return "together"
    regions = {region.id: region for region in variant.regions}
    kinds = {regions[first_id].kind, regions[second_id].kind}
    return (
        "separate"
        if kinds.intersection(
            {"cell", "cell_text", "list_item", "section_header", "title"}
        )
        else "unknown"
    )


def build_evidence(page: ContextPage, layouts: list[LayoutPage]) -> StructureEvidence:
    """Match normalized geometry conservatively; missing detections never remove text."""
    variants = []
    matched: set[int] = set()
    ambiguous: set[int] = set()
    for layout in layouts:
        regions = [
            StructuralRegion(
                id=region.id, kind=region.kind, bbox=region.bbox, source_ids=[]
            )
            for region in layout.regions
        ]
        regions.extend(
            StructuralRegion(
                id=f"{table.id}/cell-{index}",
                kind="cell_text"
                if cell.bbox_kind == "text_extent"
                or layout.provider.startswith("google-layout")
                else "cell",
                bbox=cell.bbox,
                source_ids=[],
            )
            for table in layout.tables
            for index, cell in enumerate(table.cells)
            if cell.bbox
        )
        assignments = {}
        for identity, block in enumerate(page.blocks):
            if not block.bbox:
                continue
            for region in regions:
                if (
                    region.kind == "picture"
                    and _coverage(block.bbox, region.bbox) >= 0.8
                ):
                    region.source_ids.append(identity)
            candidates = [
                region
                for region in regions
                if region.kind not in {"picture", "table"}
                and _coverage(block.bbox, region.bbox) >= 0.8
            ]
            cells = [
                region for region in candidates if region.kind in {"cell", "cell_text"}
            ]
            candidates = cells or candidates
            for region in candidates:
                region.source_ids.append(identity)
            if candidates:
                matched.add(identity)
            if len(candidates) == 1:
                assignments[identity] = candidates[0].id
            elif len(candidates) > 1:
                ambiguous.add(identity)
        variants.append(
            StructuralVariant(
                provider=layout.provider, regions=regions, assignments=assignments
            )
        )
    evidence = StructureEvidence(
        variants=variants,
        unmatched_source_ids=sorted(set(range(len(page.blocks))) - matched),
        ambiguous_source_ids=sorted(ambiguous),
        continuation_source_ids=[
            identity
            for identity, block in enumerate(page.blocks)
            if incomplete_source_clause(block.text)
        ],
    )
    identities = sorted(
        {identity for variant in variants for identity in variant.assignments}
    )
    for offset, first in enumerate(identities):
        for second in identities[offset + 1 :]:
            relations = {_relation(variant, first, second) for variant in variants}
            if {"together", "separate"}.issubset(relations):
                evidence.conflicts.append(
                    f"Layout variants disagree on source {first}/{second}"
                )
                if len(evidence.conflicts) >= 50:
                    return evidence
    return evidence


def _separate(evidence: StructureEvidence, first: int, second: int) -> bool:
    relations = {_relation(variant, first, second) for variant in evidence.variants}
    if "separate" not in relations or "together" in relations:
        return False
    if first in evidence.continuation_source_ids:
        # Printed cells remain independent; numbered subordinate clauses may continue.
        return any(
            _relation(variant, first, second) == "separate"
            and any(
                region.kind in {"cell", "cell_text"}
                and region.id
                in {variant.assignments.get(first), variant.assignments.get(second)}
                for region in variant.regions
            )
            for variant in evidence.variants
        )
    return True


def constrain_plan(plan: PagePlan, evidence: StructureEvidence) -> PagePlan:
    """Split only supported independent units; preserve every source ID and its order."""
    result = plan.model_copy(deep=True)
    result.panels = []
    for panel in plan.panels:
        groups: list[list[int]] = [[]]
        for identity in panel.source_ids:
            if groups[-1] and any(
                _separate(evidence, earlier, identity) for earlier in groups[-1]
            ):
                groups.append([])
            groups[-1].append(identity)
        if len(groups) == 1:
            result.panels.append(panel)
            continue
        result.notes.append(
            f"{panel.id}: separated {len(groups)} independent structural units"
        )
        for index, identities in enumerate(groups):
            result.panels.append(
                panel.model_copy(
                    update={"id": f"{panel.id[:80]}-s{index}", "source_ids": identities}
                )
            )
    return result
