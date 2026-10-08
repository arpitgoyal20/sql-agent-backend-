"""A scripted chat model for graph and API tests: no network, no API key.

`script` maps a structured-output schema name (e.g. "IntentDecision") or "text" to a queue of
responses. Each response is a pydantic instance / dict / str, or a callable taking the prompt.
Text responses stream word by word, so LangGraph's "messages" stream sees real tokens.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field


class ScriptExhausted(AssertionError):
    pass


class FakeLLM(BaseChatModel):
    script: dict[str, list[Any]] = Field(default_factory=dict)
    calls: list[tuple[str, str]] = Field(default_factory=list)  # (kind, prompt)

    @property
    def _llm_type(self) -> str:
        return "fake-scripted"

    def _next(self, kind: str, prompt: str) -> Any:
        self.calls.append((kind, prompt))
        queue = self.script.get(kind)
        if not queue:
            raise ScriptExhausted(f"no scripted response left for {kind}")
        item = queue.pop(0)
        return item(prompt) if callable(item) else item

    @staticmethod
    def _prompt(messages: list[BaseMessage] | Any) -> str:
        if isinstance(messages, str):
            return messages
        if hasattr(messages, "to_messages"):
            messages = messages.to_messages()
        return "\n".join(str(m.content) for m in messages)

    # -- text ------------------------------------------------------------------------
    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        text = str(self._next("text", self._prompt(messages)))
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs) -> Iterator[ChatGenerationChunk]:
        text = str(self._next("text", self._prompt(messages)))
        for i, word in enumerate(text.split(" ")):
            yield ChatGenerationChunk(message=AIMessageChunk(content=(" " if i else "") + word))

    # -- structured --------------------------------------------------------------------
    def with_structured_output(self, schema, **kwargs):
        def respond(prompt_input):
            out = self._next(schema.__name__, self._prompt(prompt_input))
            return out if isinstance(out, schema) else schema.model_validate(out)

        return RunnableLambda(respond)

    def kinds(self) -> list[str]:
        return [k for k, _ in self.calls]
