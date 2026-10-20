import hashlib
import hmac
from fastapi import HTTPException, Request, status


async def verify_github_signature(request: Request, secret: str) -> bytes:
    """Verify the X-Hub-Signature-256 header from GitHub."""
    signature_header = request.headers.get("X-Hub-Signature-256", "")
    if not signature_header:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Hub-Signature-256 header",
        )

    body = await request.body()
    expected = "sha256=" + hmac.new(
        secret.encode(), body, hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        )

    return body
