"""Fournisseur Claude, via le SDK officiel Anthropic (Messages API, outils)."""
from __future__ import annotations

from typing import Any

import anthropic

from facture.agent.providers.base import ChatProvider, Step, ToolCall, ToolOutcome, ToolSpec

# Modèles qui acceptent le repli automatique côté serveur en cas de refus
FALLBACK_MODELS = {"claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5",
                   "claude-sonnet-5-5"}


class AnthropicProvider(ChatProvider):
    name = "anthropic"

    def __init__(self, model: str, system: str, tools: list[ToolSpec],
                 api_key: str | None = None, base_url: str | None = None,
                 effort: str | None = None):
        super().__init__(model, system, tools)
        # Sans clé explicite, le SDK lit ANTHROPIC_API_KEY (ou un profil `ant auth login`)
        self.client = anthropic.Anthropic(api_key=api_key or None, base_url=base_url or None)
        self.effort = effort
        self.messages: list[dict[str, Any]] = []
        self._tools = [{"name": t.name, "description": t.description,
                        "input_schema": t.input_schema} for t in tools]

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def step(self) -> Step:
        kwargs: dict[str, Any] = dict(model=self.model, max_tokens=16000, system=self.system,
                                      tools=self._tools, messages=self.messages)
        if self.effort:
            kwargs["output_config"] = {"effort": self.effort}
        if self.model in FALLBACK_MODELS:
            response = self.client.beta.messages.create(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
        else:
            response = self.client.messages.create(**kwargs)

        # On renvoie le contenu tel quel (blocs de réflexion compris) au tour suivant
        self.messages.append({"role": "assistant", "content": response.content})

        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [ToolCall(id=b.id, name=b.name, arguments=dict(b.input or {}))
                 for b in response.content if b.type == "tool_use"]
        if response.stop_reason == "refusal":
            return Step(text=text or "(le modèle a refusé de répondre)", stop="refusal")
        if response.stop_reason == "max_tokens":
            return Step(text=text, tool_calls=[], stop="max_tokens")
        if response.stop_reason == "pause_turn":
            return self.step()
        return Step(text=text, tool_calls=calls, stop="tools" if calls else "end")

    def add_tool_results(self, outcomes: list[ToolOutcome]) -> None:
        # Tous les résultats d'un tour dans un seul message utilisateur
        self.messages.append({"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": o.call.id, "content": o.content,
             "is_error": o.is_error}
            for o in outcomes
        ]})

    def reset(self) -> None:
        self.messages = []
