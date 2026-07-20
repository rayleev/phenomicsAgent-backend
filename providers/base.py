from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Literal, Optional

from pydantic import BaseModel


class StreamEvent(BaseModel):
    """A single chunk from a streaming LLM response."""
    type: Literal["content", "thinking", "tool_use", "tool_result", "done"]
    delta: str = ""


class ToolUseInfo(BaseModel):
    """Information about a tool use decision from the LLM."""
    tool_name: str
    input: dict[str, Any]
    tool_use_id: Optional[str] = None


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
        """Stream a chat completion (no tools).

        Args:
            messages: List of dicts with 'role' and 'content' keys.
            thinking_enabled: Whether to request extended thinking (if supported).

        Yields:
            StreamEvent instances — type "content", "thinking", or "done".
        """
        ...
        yield  # pragma: no cover

    async def chat_stream_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        thinking_enabled: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a chat completion with tool calling support.

        Default implementation falls back to chat_stream (ignores tools).
        Subclasses should override to implement tool_use parsing.

        Args:
            messages: List of dicts with 'role' and 'content' keys.
            tools: List of tool definitions in OpenAI Function Calling format.
            thinking_enabled: Whether to request extended thinking (if supported).

        Yields:
            StreamEvent — type "content", "thinking", "tool_use", "tool_result", or "done".
        """
        async for event in self.chat_stream(messages, thinking_enabled=thinking_enabled):
            yield event
