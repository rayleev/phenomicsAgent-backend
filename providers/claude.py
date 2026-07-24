import json
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic
from anthropic.types import (
    RawContentBlockStartEvent,
    RawContentBlockDeltaEvent,
    ThinkingDelta,
    TextDelta,
    ThinkingBlock,
    ToolUseBlock,
)
from anthropic.lib.streaming import InputJsonEvent, ContentBlockStopEvent

from providers.base import BaseProvider, StreamEvent


class ClaudeProvider(BaseProvider):
    """Provider for Anthropic Claude API with extended thinking and tool use support."""

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
        """Stream a chat completion from Claude (no tools).

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

    async def chat_stream_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        thinking_enabled: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a chat completion with tool calling support.

        Yields StreamEvent with type:
        - "content" / "thinking": text deltas
        - "tool_use": full tool call info (delta contains JSON with tool_name + input + tool_use_id)
        - "done": stream completed
        """
        if not tools:
            async for event in self.chat_stream(messages, thinking_enabled=thinking_enabled):
                yield event
            return

        kwargs: dict = {
            "max_tokens": 32000,
            "messages": messages,  # type: ignore
            "model": self.model,
            "tools": tools,
            "tool_choice": {"type": "auto"},
        }
        if thinking_enabled:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": 16000}

        async with self.client.messages.stream(**kwargs) as stream:
            current_tool_name: str | None = None
            current_tool_input_parts: list[str] = []

            async for event in stream:
                # --- Thinking content ---
                if isinstance(event, RawContentBlockStartEvent):
                    if isinstance(event.content_block, ToolUseBlock):
                        current_tool_name = event.content_block.name
                        current_tool_input_parts = []
                    elif isinstance(event.content_block, ThinkingBlock):
                        if event.content_block.thinking:
                            yield StreamEvent(type="thinking", delta=event.content_block.thinking)

                elif isinstance(event, RawContentBlockDeltaEvent):
                    if isinstance(event.delta, ThinkingDelta):
                        yield StreamEvent(type="thinking", delta=event.delta.thinking)
                    elif isinstance(event.delta, TextDelta):
                        yield StreamEvent(type="content", delta=event.delta.text)

                elif isinstance(event, InputJsonEvent):
                    if event.partial_json:
                        current_tool_input_parts.append(event.partial_json)

                elif isinstance(event, ContentBlockStopEvent):
                    if (
                        isinstance(event.content_block, ToolUseBlock)
                        and current_tool_name
                    ):
                        full_input_str = "".join(current_tool_input_parts)
                        try:
                            tool_input = json.loads(full_input_str) if full_input_str else {}
                        except json.JSONDecodeError:
                            tool_input = {}

                        yield StreamEvent(
                            type="tool_use",
                            delta=json.dumps({
                                "tool_name": current_tool_name,
                                "input": tool_input,
                                "tool_use_id": event.content_block.id,
                            }),
                        )
                        current_tool_name = None
                        current_tool_input_parts = []

            yield StreamEvent(type="done", delta="")
