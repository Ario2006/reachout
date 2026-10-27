from fastapi import HTTPException


def not_built(owner: str) -> HTTPException:
    """Contract stubs answer 501 until the owning task replaces them."""
    return HTTPException(501, f"Not built yet ({owner}).")
