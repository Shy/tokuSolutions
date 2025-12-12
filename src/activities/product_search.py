"""Product search activities for Tokullectibles."""

from temporalio import activity
from src.tokullectibles import search_tokullectibles


@activity.defn
async def search_product_url_activity(product_name: str) -> dict:
    """
    Search Tokullectibles for a product and return its URL.

    Args:
        product_name: Product name to search for (e.g., "CSM DenGasher")

    Returns:
        Dictionary with 'success', 'url', 'name', 'handle', and 'blog_links' keys.
        If not found, success=False and url is empty string.
    """
    activity.logger.info(f"Searching Tokullectibles for: {product_name}")

    try:
        result = search_tokullectibles(product_name)

        if result:
            activity.logger.info(f"Found product: {result.name} at {result.url}")
            # Convert blog_links to dict format for JSON serialization
            blog_links_data = [
                {"title": link.title, "url": link.url, "translated_url": link.translated_url}
                for link in result.blog_links
            ]
            # For backward compatibility, also include first link as blog_url
            first_blog_url = result.blog_links[0].translated_url if result.blog_links else ""
            return {
                "success": True,
                "url": result.url,
                "name": result.name,
                "handle": result.handle,
                "description": result.description or "",
                "blog_url": first_blog_url,  # Backward compat - first link
                "blog_links": blog_links_data,  # All links for future use
            }
        else:
            activity.logger.warning(f"Product not found: {product_name}")
            return {
                "success": False,
                "url": "",
                "name": product_name,
                "handle": "",
                "description": "",
                "blog_links": [],
            }

    except Exception as e:
        activity.logger.error(f"Search failed: {e}")
        return {
            "success": False,
            "url": "",
            "name": product_name,
            "handle": "",
            "description": "",
            "blog_links": [],
            "error": str(e),
        }
