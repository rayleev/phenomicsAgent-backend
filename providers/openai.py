from typing import AsyncIterator

from openai import AsyncOpenAI

from backend.providers.base import BaseProvider, StreamEvent


class OpenAIProvider(BaseProvider):
    """Provider for OpenAI API."""

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
