"""Source-backed step presentation shared by all semantic manuals."""

import re
import unicodedata

CIRCLES = (
    "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚㉛㉜㉝㉞㉟㊱㊲㊳㊴㊵㊶㊷㊸㊹㊺㊻㊼㊽㊾㊿"
)


def format_instruction_items(source: str, items: list[str], *, bullets: bool) -> str:
    """Use circled source step numbers, adding bullets only to unnumbered items."""
    available = {
        int(unicodedata.numeric(symbol)): symbol
        for symbol in source
        if symbol in CIRCLES
    }
    result = []
    for item in items:
        lines = []
        for line in item.splitlines():
            line = re.sub(r"^\s*[●•]\s*", "", line)
            match = re.match(r"^(\d{1,2})[.)]\s+", line)
            if match and int(match.group(1)) in available:
                line = available[int(match.group(1))] + " " + line[match.end() :]
            line = re.sub("([" + CIRCLES + r"])\s*", r"\1 ", line)
            lines.append(line)
        value = "\n".join(lines).strip()
        prefix = "• " if bullets and value and value[0] not in CIRCLES else ""
        result.append(prefix + value)
    return "\n".join(result)


def normalize_step_markers(source: str, translation: str) -> str:
    """Standardize existing steps while preserving ordinary bullets and literal codes."""
    available = {
        int(unicodedata.numeric(symbol)): symbol
        for symbol in source
        if symbol in CIRCLES
    }

    def replace(match: re.Match[str]) -> str:
        number = int(match.group(1))
        return available[number] + " " if number in available else match.group(0)

    translation = re.sub(
        r"(?m)^[ \t]*(?:[●•][ \t]*)?(\d{1,2})[.)][ \t]+", replace, translation
    )
    translation = re.sub(r"(?m)^[ \t]*[●•][ \t]*(?=[" + CIRCLES + "])", "", translation)
    return re.sub("([" + CIRCLES + r"])[ \t]*(?=[A-Za-z])", r"\1 ", translation)
