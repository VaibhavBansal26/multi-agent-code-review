"""Tests for HMAC webhook signature validation."""
import hashlib
import hmac
import pytest
from fastapi import HTTPException
from unittest.mock import AsyncMock, MagicMock


def _make_sig(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class TestVerifyGithubSignature:
    @pytest.mark.asyncio
    async def test_valid_signature_returns_body(self):
        from app.webhook.validator import verify_github_signature

        body = b'{"action":"opened"}'
        secret = "my-secret"
        sig = _make_sig(body, secret)

        request = MagicMock()
        request.headers = {"X-Hub-Signature-256": sig}
        request.body = AsyncMock(return_value=body)

        result = await verify_github_signature(request, secret)
        assert result == body

    @pytest.mark.asyncio
    async def test_missing_header_raises_401(self):
        from app.webhook.validator import verify_github_signature

        request = MagicMock()
        request.headers = {}
        request.body = AsyncMock(return_value=b"body")

        with pytest.raises(HTTPException) as exc_info:
            await verify_github_signature(request, "secret")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_secret_raises_401(self):
        from app.webhook.validator import verify_github_signature

        body = b'{"test":true}'
        sig = _make_sig(body, "correct-secret")

        request = MagicMock()
        request.headers = {"X-Hub-Signature-256": sig}
        request.body = AsyncMock(return_value=body)

        with pytest.raises(HTTPException) as exc_info:
            await verify_github_signature(request, "wrong-secret")
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_tampered_body_raises_401(self):
        from app.webhook.validator import verify_github_signature

        original = b'{"action":"opened"}'
        tampered = b'{"action":"malicious"}'
        sig = _make_sig(original, "secret")

        request = MagicMock()
        request.headers = {"X-Hub-Signature-256": sig}
        request.body = AsyncMock(return_value=tampered)

        with pytest.raises(HTTPException) as exc_info:
            await verify_github_signature(request, "secret")
        assert exc_info.value.status_code == 401
