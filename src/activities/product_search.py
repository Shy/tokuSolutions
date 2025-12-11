"""Product search activities for Tokullectibles."""

from temporalio import activity
from src.shopify_search import search_tokullectibles


@activity.defn
async def search_product_url_activity(product_name: str) -> dict:
    """
    Search Tokullectibles for a product and return its URL.

    Args:
        product_name: Product name to search for (e.g., "CSM DenGasher")

    Returns:
        Dictionary with 'success', 'url', 'name', and 'handle' keys.
        If not found, success=False and url is empty string.
    """
    activity.logger.info(f"Searching Tokullectibles for: {product_name}")

    try:
        result = search_tokullectibles(product_name)

        if result:
            activity.logger.info(f"Found product: {result.name} at {result.url}")
            return {
                "success": True,
                "url": result.url,
                "name": result.name,
                "handle": result.handle,
                "description": result.description or "",
                "blog_url": result.blog_url or "",
            }
        else:
            activity.logger.warning(f"Product not found: {product_name}")
            return {
                "success": False,
                "url": "",
                "name": product_name,
                "handle": "",
                "description": "",
            }

    except Exception as e:
        activity.logger.error(f"Search failed: {e}")
        return {
            "success": False,
            "url": "",
            "name": product_name,
            "handle": "",
            "description": "",
            "error": str(e),
        }
