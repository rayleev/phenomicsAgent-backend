from abc import ABC, abstractmethod
from typing import AsyncIterator, Literal

from pydantic import BaseModel


class StreamEvent(BaseModel):
    """A single chunk from a streaming LLM response."""
    type: Literal["content", "thinking", "done"]
    delta: str = ""


class BaseProvider(ABC):
    """Abstract base class for LLM providers."""

    def __init__(self, model: str, base_url: str, api_key: str):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    @abstractmethod
    async def chat_stream(
        self,
        messages: list[dict],
        thinking_enabled: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a chat completion.

        Args:
            messages: List of dicts with 'role' and 'content' keys.
            thinking_enabled: Whether to request extended thinking (if supported).

        Yields:
            StreamEvent instances — type "content", "thinking", or "done".
        """
        ...
        yield  # pragma: no cover
