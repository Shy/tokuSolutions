"""Shopify product search for Tokullectibles."""

import re
import requests
from pydantic import BaseModel
from typing import Optional


class ProductInfo(BaseModel):
    """Product information from Tokullectibles."""

    name: str
    url: str
    handle: str
    description: Optional[str] = None


def search_tokullectibles(query: str) -> Optional[ProductInfo]:
    """
    Search tokullectibles.com for a product.

    Args:
        query: Product name or partial name to search for

    Returns:
        ProductInfo if found, None otherwise

    Example:
        >>> result = search_tokullectibles("CSM DenGasher")
        >>> if result:
        ...     print(f"{result.name}: {result.url}")
    """
    # Clean query - remove common prefixes/suffixes that might interfere
    clean_query = query.strip()

    # Try exact search first via Shopify's product URL pattern
    # Shopify product URLs are typically: /products/product-handle
    # Product handles are lowercase with hyphens
    handle = _generate_handle(clean_query)

    # Try direct URL first
    product_url = f"https://tokullectibles.com/products/{handle}"
    if _check_url_exists(product_url):
        # Extract actual product name and description from page
        name = _extract_product_name(product_url) or query
        description = _extract_product_description(product_url)
        return ProductInfo(
            name=name, url=product_url, handle=handle, description=description
        )

    # If direct URL fails, try common variations
    variations = _generate_handle_variations(clean_query)
    for variant_handle in variations:
        product_url = f"https://tokullectibles.com/products/{variant_handle}"
        if _check_url_exists(product_url):
            name = _extract_product_name(product_url) or query
            description = _extract_product_description(product_url)
            return ProductInfo(
                name=name,
                url=product_url,
                handle=variant_handle,
                description=description,
            )

    return None


def _generate_handle(name: str) -> str:
    """
    Generate a Shopify product handle from a product name.
    Handles are lowercase, alphanumeric + hyphens only.

    Examples:
        "CSM DenGasher" -> "csm-dengasher"
        "Ranger Key - 35 Reds" -> "ranger-key-35-reds"
    """
    # Convert to lowercase
    handle = name.lower()

    # Replace spaces and special chars with hyphens
    handle = re.sub(
        r"[^\w\s-]", "", handle
    )  # Remove special chars except space and hyphen
    handle = re.sub(r"[-\s]+", "-", handle)  # Replace spaces/hyphens with single hyphen

    # Remove leading/trailing hyphens
    handle = handle.strip("-")

    return handle


def _generate_handle_variations(name: str) -> list[str]:
    """
    Generate common variations of product handles to try.

    Returns:
        List of handle variations to attempt
    """
    variations = []

    # Base handle
    base = _generate_handle(name)
    variations.append(base)

    # Try without edition/version numbers
    without_version = re.sub(r"-v\d+$", "", base)
    if without_version != base:
        variations.append(without_version)

    # Try with "memorial-edition" variations
    if "memorial" in base:
        # "ranger-key-memorial-edition-35-reds-set" -> "ranger-key-memorial-edition-35-reds"
        variations.append(re.sub(r"-set$", "", base))
        # Try without "set"
        without_set = base.replace("-set", "")
        if without_set not in variations:
            variations.append(without_set)

    # Try "next" vs "NEXT" handling
    if "next" in base:
        variations.append(base.replace("next", "NEXT"))
        variations.append(base.replace("-next", ""))

    return variations


def _check_url_exists(url: str) -> bool:
    """
    Check if a URL returns a successful response (not 404).

    Args:
        url: URL to check

    Returns:
        True if URL exists (status 200), False otherwise
    """
    try:
        response = requests.head(url, timeout=5, allow_redirects=True)
        return response.status_code == 200
    except requests.RequestException:
        return False


def _extract_product_name(url: str) -> Optional[str]:
    """
    Extract the actual product name from a Tokullectibles product page.

    Args:
        url: Product page URL

    Returns:
        Product name if found, None otherwise
    """
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return None

        # Look for product title in common Shopify patterns
        # Pattern 1: <h1 class="product-title">Product Name</h1>
        match = re.search(
            r'<h1[^>]*class="[^"]*product[^"]*title[^"]*"[^>]*>(.*?)</h1>',
            response.text,
            re.IGNORECASE,
        )
        if match:
            name = match.group(1).strip()
            # Clean HTML entities
            name = re.sub(r"<.*?>", "", name)  # Remove any HTML tags
            return name

        # Pattern 2: <title>Product Name | Tokullectibles</title>
        match = re.search(
            r"<title>(.*?)\s*[|–-]\s*Tokullectibles", response.text, re.IGNORECASE
        )
        if match:
            return match.group(1).strip()

        # Pattern 3: og:title meta tag
        match = re.search(
            r'<meta\s+property="og:title"\s+content="([^"]+)"',
            response.text,
            re.IGNORECASE,
        )
        if match:
            return match.group(1).strip()

        return None

    except requests.RequestException:
        return None


def _extract_product_description(url: str) -> Optional[str]:
    """
    Extract product description from a Tokullectibles product page.

    Args:
        url: Product page URL

    Returns:
        Product description if found, None otherwise
    """
    try:
        response = requests.get(url, timeout=10)
        if response.status_code != 200:
            return None

        # Pattern 1: og:description meta tag (most reliable)
        match = re.search(
            r'<meta\s+property="og:description"\s+content="([^"]+)"',
            response.text,
            re.IGNORECASE,
        )
        if match:
            desc = match.group(1).strip()
            # Clean up common HTML entities
            desc = desc.replace("&quot;", '"').replace("&amp;", "&")
            return desc if desc else None

        # Pattern 2: <meta name="description"> tag
        match = re.search(
            r'<meta\s+name="description"\s+content="([^"]+)"',
            response.text,
            re.IGNORECASE,
        )
        if match:
            desc = match.group(1).strip()
            desc = desc.replace("&quot;", '"').replace("&amp;", "&")
            return desc if desc else None

        # Pattern 3: Product description div (common Shopify pattern)
        match = re.search(
            r'<div[^>]*class="[^"]*product[^"]*description[^"]*"[^>]*>(.*?)</div>',
            response.text,
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            # Strip HTML tags and clean up
            desc = re.sub(r"<.*?>", "", match.group(1))
            desc = " ".join(desc.split())  # Normalize whitespace
            return desc[:500] if desc else None  # Limit to 500 chars

        return None

    except requests.RequestException:
        return None


def search_and_display(query: str) -> None:
    """
    Search for a product and display results (CLI helper).

    Args:
        query: Product name to search for
    """
    print(f"Searching for: {query}")
    result = search_tokullectibles(query)

    if result:
        print(f"✓ Found: {result.name}")
        print(f"  URL: {result.url}")
        print(f"  Handle: {result.handle}")
    else:
        print("✗ Product not found")
        print("\nTried these handles:")
        for handle in _generate_handle_variations(query):
            print(f"  - {handle}")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
        search_and_display(query)
    else:
        print("Usage: python shopify_search.py <product name>")
        print("\nExample:")
        print("  python shopify_search.py CSM DenGasher")
