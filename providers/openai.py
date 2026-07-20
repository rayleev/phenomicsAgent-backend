import json
from typing import AsyncIterator

from openai import AsyncOpenAI

from backend.providers.base import BaseProvider, StreamEvent


class OpenAIProvider(BaseProvider):
    """Provider for OpenAI API with tool calling support."""

    def __init__(self, model: str, base_url: str, api_key: str):
        super().__init__(model, base_url, api_key)
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def chat_stream(
        self,
        messages: list[dict],
        thinking_enabled: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a chat completion from OpenAI.

        OpenAI does not have extended thinking, so thinking_enabled is ignored.
        """
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore
            stream=True,
            timeout=30.0,
        )

        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield StreamEvent(type="content", delta=delta.content)

        yield StreamEvent(type="done", delta="")

    async def chat_stream_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        thinking_enabled: bool = False,
    ) -> AsyncIterator[StreamEvent]:
        """Stream a chat completion with tool calling support."""
        if not tools:
            async for event in self.chat_stream(messages, thinking_enabled=thinking_enabled):
                yield event
            return

        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,  # type: ignore
            tools=tools,
            tool_choice="auto",
            stream=True,
            timeout=30.0,
        )

        # Buffer for tool calls (OpenAI streams tool_calls as deltas)
        tool_call_buffers: dict[int, dict] = {}

        async for chunk in stream:
            if not chunk.choices:
                continue

            delta = chunk.choices[0].delta

            if delta and delta.content:
                yield StreamEvent(type="content", delta=delta.content)

            if delta and delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_call_buffers:
                        tool_call_buffers[idx] = {
                            "id": tc_delta.id or "",
                            "function_name": "",
                            "function_args": "",
                        }

                    buf = tool_call_buffers[idx]
                    if tc_delta.id:
                        buf["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            buf["function_name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            buf["function_args"] += tc_delta.function.arguments

        # After stream completes, emit tool_use events for collected tool calls
        if tool_call_buffers:
            for idx in sorted(tool_call_buffers.keys()):
                buf = tool_call_buffers[idx]
                try:
                    args = json.loads(buf["function_args"]) if buf["function_args"] else {}
                except json.JSONDecodeError:
                    args = {}

                yield StreamEvent(
                    type="tool_use",
                    delta=json.dumps({
                        "tool_name": buf["function_name"],
                        "input": args,
                        "tool_use_id": buf["id"],
                    }),
                )

        yield StreamEvent(type="done", delta="")
