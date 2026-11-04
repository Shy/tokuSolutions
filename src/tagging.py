"""Shared catalog tag definitions used by generated metadata."""

# Tag definitions with colors and descriptions
TAG_DEFINITIONS: dict[str, dict[str, str]] = {
    "csm": {"color": "#FF6B6B", "label": "CSM"},
    "dx": {"color": "#4ECDC4", "label": "DX"},
    "memorial": {"color": "#95E1D3", "label": "Memorial"},
    "premium": {"color": "#F38181", "label": "Premium"},
    "kamen-rider": {"color": "#AA96DA", "label": "Kamen Rider"},
    "sentai": {"color": "#FCBAD3", "label": "Sentai"},
    "ultraman": {"color": "#A8D8EA", "label": "Ultraman"},
}


def get_tag_definitions() -> dict[str, dict[str, str]]:
    """Return the supported catalog tags, labels and colors."""
    return TAG_DEFINITIONS
