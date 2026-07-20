from typing import AsyncIterator

from anthropic import AsyncAnthropic
from anthropic.types import (
    RawContentBlockStartEvent,
    RawContentBlockDeltaEvent,
    ThinkingDelta,
    TextDelta,
    ThinkingBlock,
)

from backend.providers.base import BaseProvider, StreamEvent


class ClaudeProvider(BaseProvider):
    """Provider for Anthropic Claude API with extended thinking support."""

    def __init__(self, model: str, base_url: str, api_key: str):
        super().__init__(model, base_url, api_key)
        if self.base_url and self.base_url != "https://api.anthropic.com":
            self.client = AsyncAnthropic(api_key=api_key, base_url=base_url)
        else:
            self.client = AsyncAnthropic(api_key=api_key)

    async def chat_stream(
        self,
        messages: list[dict],
        thinking_enabled: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a chat completion from Claude.

        When thinking_enabled is True, Claude's extended thinking is used.
        """
        kwargs: dict = {
            "max_tokens": 32000,
            "messages": messages,  # type: ignore
            "model": self.model,
        }
        if thinking_enabled:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": 16000}

        async with self.client.messages.stream(**kwargs) as stream:
            async for event in stream:
                if (
                    isinstance(event, RawContentBlockStartEvent)
                    and isinstance(event.content_block, ThinkingBlock)
                ):
                    if event.content_block.thinking:
                        yield StreamEvent(type="thinking", delta=event.content_block.thinking)

                elif isinstance(event, RawContentBlockDeltaEvent):
                    if isinstance(event.delta, ThinkingDelta):
                        yield StreamEvent(type="thinking", delta=event.delta.thinking)
                    elif isinstance(event.delta, TextDelta):
                        yield StreamEvent(type="content", delta=event.delta.text)

            yield StreamEvent(type="done", delta="")
