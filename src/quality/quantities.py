"""Shared conservative numeric facts for translation validation."""

import re
import unicodedata
from collections import Counter


def numeric_tokens(text: str) -> Counter[str]:
    """Normalize digit quantities, common English number words and battery-size codes."""
    normalized = unicodedata.normalize("NFKC", text).lower()
    # Japanese battery-size digits map to AA/AAA/etc., not battery quantities.
    normalized = re.sub(r"単\s*[1-5]", "", normalized)
    normalized = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", normalized)
    numbers = Counter(re.findall(r"\d+(?:\.\d+)?", normalized))
    units = [
        "zero",
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
    ]
    values = {word: value for value, word in enumerate(units)}
    values.update(
        {
            word: value
            for word, value in zip(
                [
                    "twenty",
                    "thirty",
                    "forty",
                    "fifty",
                    "sixty",
                    "seventy",
                    "eighty",
                    "ninety",
                ],
                range(20, 100, 10),
                strict=True,
            )
        }
    )
    pattern = (
        r"\b(?:(?:twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)(?:[- ]+(?:one|two|three|four|five|six|seven|eight|nine))?|"
        + "|".join(units)
        + r")\b"
    )
    for matched in re.findall(pattern, normalized):
        numbers[str(sum(values[word] for word in re.split(r"[- ]+", matched)))] += 1
    return numbers


def reconcile_polarity_symbols(source: str, reference: str) -> str:
    """Resolve circled-digit polarity OCR only with a matching independent clause."""
    if "電池" not in source:
        return source
    compact = re.sub(r"\s+", "", reference)

    def replace(match: re.Match[str]) -> str:
        prefix = match.group(1)
        for symbol in ("+", "＋", "−", "－", "⊕", "⊖"):
            if prefix + symbol + "に" in compact:
                return prefix + symbol + "に"
        return match.group(0)

    return re.sub(r"((?:上側|下側|表側|裏側)が)[①-⑳]に", replace, source)
