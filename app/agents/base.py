"""Base class for all MACRV agents."""
from __future__ import annotations
import structlog
import anthropic
from app.config import get_settings

log = structlog.get_logger(__name__)


class BaseAgent:
    def __init__(self, model: str | None = None):
        settings = get_settings()
        self.settings = settings
        self.model = model or settings.worker_model
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.log = structlog.get_logger(self.__class__.__name__)

    def _call(
        self,
        messages: list[dict],
        system: str,
        max_tokens: int = 4096,
        tools: list[dict] | None = None,
    ) -> anthropic.types.Message:
        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        return self.client.messages.create(**kwargs)
