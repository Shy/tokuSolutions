"""Auto-tagging system for toy manuals."""

import re
import os
from typing import List, Dict, Optional
from pydantic import BaseModel
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


# Tag definitions with colors and descriptions
TAG_DEFINITIONS: Dict[str, Dict[str, str]] = {
    "csm": {"color": "#FF6B6B", "label": "CSM"},
    "dx": {"color": "#4ECDC4", "label": "DX"},
    "memorial": {"color": "#95E1D3", "label": "Memorial"},
    "premium": {"color": "#F38181", "label": "Premium"},
    "kamen-rider": {"color": "#AA96DA", "label": "Kamen Rider"},
    "sentai": {"color": "#FCBAD3", "label": "Sentai"},
    "ultraman": {"color": "#A8D8EA", "label": "Ultraman"},
}


class TaggingResponse(BaseModel):
    """Pydantic model for Gemini tagging response."""
    tags: List[str]
    confidence: str  # "high", "medium", "low"
    reasoning: str


def _generate_tags_with_gemini(product_name: str) -> Optional[List[str]]:
    """
    Use Gemini AI to identify appropriate tags for a product.
    Falls back to None if Gemini is unavailable or fails.

    Args:
        product_name: The product's name

    Returns:
        List of tag IDs or None if Gemini fails
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            "gemini-2.5-flash",
            generation_config={"response_mime_type": "application/json"}
        )

        available_tags = list(TAG_DEFINITIONS.keys())
        tag_descriptions = {
            "csm": "Complete Selection Modification (premium collectible line)",
            "dx": "DX (Deluxe toy line, standard retail)",
            "memorial": "Memorial Edition (special commemorative releases)",
            "premium": "Premium Bandai (web-exclusive items)",
            "kamen-rider": "Kamen Rider franchise",
            "sentai": "Super Sentai franchise",
            "ultraman": "Ultraman franchise"
        }

        prompt = f"""Analyze this Japanese toy product name and identify appropriate tags.

Product name: {product_name}

Available tags:
{chr(10).join(f"- {tag}: {desc}" for tag, desc in tag_descriptions.items())}

Return a JSON object with:
- tags: array of applicable tag IDs (only use tags from the list above)
- confidence: "high", "medium", or "low"
- reasoning: brief explanation of why these tags apply

Consider:
- Product line indicators (CSM, DX, Memorial, Premium)
- Franchise identifiers (character names, series references)
- Product type (drivers, weapons, transformation items)

Kamen Rider series include: Den-O, W, OOO, Fourze, Wizard, Gaim, Drive, Ghost, Ex-Aid, Build, Zi-O, Zero-One, Saber, Revice, Geats, Faiz, Blade, Hibiki, Kabuto, Kiva, Decade
Sentai series include: Abaranger, Dekaranger, Magiranger, Boukenger, Gekiranger, Go-Onger, Shinkenger, Goseiger, Gokaiger, Go-Busters, Kyoryuger, ToQger, Ninninger

Example responses:
{{"tags": ["csm", "kamen-rider"], "confidence": "high", "reasoning": "CSM prefix indicates Complete Selection line, and Fang Memory is from Kamen Rider W"}}
{{"tags": ["csm", "kamen-rider"], "confidence": "high", "reasoning": "Den-O is a Kamen Rider series, CSM indicates premium collectible line"}}
"""

        response = model.generate_content(prompt)
        result = TaggingResponse.model_validate_json(response.text)

        # Validate all tags are in our known set
        valid_tags = [tag for tag in result.tags if tag in available_tags]

        if valid_tags and result.confidence in ["high", "medium"]:
            return valid_tags
        return None

    except Exception as e:
        # Log error and fall back to regex-based tagging
        print(f"Gemini tagging failed: {e}")
        return None


def generate_tags(product_name: str, description: str = "") -> List[str]:
    """
    Generate tags for a product based on its name and description.
    Tries Gemini AI first, falls back to regex-based tagging.

    Args:
        product_name: The product's name
        description: Optional product description

    Returns:
        List of tag IDs that apply to this product

    Examples:
        >>> generate_tags("Complete Selection Modification Fang Memory")
        ['csm', 'kamen-rider']
        >>> generate_tags("DX Ridewatch")
        ['dx', 'kamen-rider']
        >>> generate_tags("Ranger Key Memorial Edition 35 Reds Set")
        ['memorial', 'sentai']
        >>> generate_tags("Shin Ultraman Beta Capsule")
        ['ultraman']
    """
    # Try Gemini-based tagging first (if API key available)
    gemini_tags = _generate_tags_with_gemini(product_name)
    if gemini_tags:
        return sorted(gemini_tags)

    # Fall back to regex-based tagging
    tags = set()
    text = f"{product_name} {description}".lower()

    # Product grade/edition tags
    if re.search(r'\bcsm\b|complete selection modification', text):
        tags.add("csm")
    if re.search(r'\bdx\b', text):
        tags.add("dx")
    if re.search(r'\bmemorial\b', text):
        tags.add("memorial")
    if re.search(r'\bpremium\b', text):
        tags.add("premium")

    # Franchise tags
    if re.search(r'\bkamen rider\b|仮面ライダー|rider|ridewatch|driver|dengasher|faiz|零ワン|zero.*one|zein|memory|progrise', text):
        tags.add("kamen-rider")

    if re.search(r'\bsentai\b|ranger.*key|abaranger|戦隊|スーパー戦隊', text):
        tags.add("sentai")

    if re.search(r'ultraman|ウルトラマン|ultra.*replica|beta.*capsule|shin.*ultra', text):
        tags.add("ultraman")

    return sorted(list(tags))


def get_tag_definitions() -> Dict[str, Dict[str, str]]:
    """Get all tag definitions with their colors and labels."""
    return TAG_DEFINITIONS
