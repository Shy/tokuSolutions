"""Small file-reference inputs for combined structural analysis and semantic layout."""

from dataclasses import dataclass

from src.layout_models import LayoutBenchmarkInput
from src.semantic_models import SemanticLayoutInput


@dataclass
class CombinedLayoutInput:
    """Independent frozen parser jobs and the staged manual they inform."""

    semantic: SemanticLayoutInput
    parsers: tuple[LayoutBenchmarkInput, ...]
    parser_queue: str = ""
