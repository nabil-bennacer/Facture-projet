"""Fournisseur générique « compatible OpenAI » (Chat Completions + tools).

Couvre avec le même code : Ollama en local (http://localhost:11434/v1), OpenAI,
Mistral, Groq, OpenRouter, un modèle servi par vLLM (ex. Hyperfluid)...
"""
from __future__ import annotations

import json
from typing import Any

from openai import OpenAI

from facture.agent.providers.base import ChatProvider, Step, ToolCall, ToolOutcome, ToolSpec


class OpenAICompatibleProvider(ChatProvider):
    name = "openai"

    def __init__(self, model: str, system: str, tools: list[ToolSpec],
                 api_key: str | None = None, base_url: str | None = None,
                 temperature: float | None = None):
        super().__init__(model, system, tools)
        # Ollama n'exige pas de clé, mais le client en veut une non vide
        self.client = OpenAI(api_key=api_key or "not-needed", base_url=base_url or None)
        self.temperature = temperature
        self._tools = [{"type": "function", "function": {
            "name": t.name, "description": t.description, "parameters": t.input_schema}}
            for t in tools]
        self.reset()

    def reset(self) -> None:
        self.messages: list[dict[str, Any]] = [{"role": "system", "content": self.system}]

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def step(self) -> Step:
        kwargs: dict[str, Any] = dict(model=self.model, messages=self.messages, tools=self._tools)
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        choice = self.client.chat.completions.create(**kwargs).choices[0]
        msg = choice.message

        calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            try:
                args = json.loads(tc.function.arguments or "{}")
                calls.append(ToolCall(id=tc.id, name=tc.function.name,
                                      arguments=args if isinstance(args, dict) else {}))
            except json.JSONDecodeError as e:
                calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments={},
                                      parse_error=f"Arguments JSON invalides : {e}"))

        entry: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            entry["tool_calls"] = [{"id": tc.id, "type": "function", "function": {
                "name": tc.function.name, "arguments": tc.function.arguments or "{}"}}
                for tc in msg.tool_calls]
        self.messages.append(entry)

        if choice.finish_reason == "length":
            return Step(text=msg.content or "", stop="max_tokens")
        return Step(text=msg.content or "", tool_calls=calls, stop="tools" if calls else "end")

    def add_tool_results(self, outcomes: list[ToolOutcome]) -> None:
        for o in outcomes:
            content = ("ERREUR : " + o.content) if o.is_error else o.content
            self.messages.append({"role": "tool", "tool_call_id": o.call.id, "content": content})
