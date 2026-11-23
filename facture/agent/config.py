"""Configuration de l'agent : tout se règle dans .env (ou en variables d'environnement).

LLM_PROVIDER   anthropic | openai  (openai = toute API compatible OpenAI : Ollama, Mistral...)
LLM_MODEL      ex. claude-opus-5-5, qwen3:8b, mistral-large-latest
LLM_BASE_URL   ex. http://localhost:11434/v1 pour Ollama (vide = URL par défaut du fournisseur)
LLM_API_KEY    clé du fournisseur (vide pour Ollama ; pour anthropic, ANTHROPIC_API_KEY marche aussi)
LLM_EFFORT     anthropic uniquement : low | medium | high (optionnel)
LLM_TEMPERATURE  openai uniquement (optionnel)
AGENT_MAX_TOOL_STEPS  nombre maximal d'allers-retours avec les outils par question (défaut 10)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "openai": "qwen3:8b"}


@dataclass
class LLMConfig:
    provider: str
    model: str
    base_url: str | None
    api_key: str | None
    effort: str | None
    temperature: float | None
    max_tool_steps: int

    @classmethod
    def from_env(cls, **overrides: str | None) -> "LLMConfig":
        def get(name: str) -> str | None:
            v = overrides.get(name.lower().removeprefix("llm_"))
            return v if v else (os.getenv(name) or None)

        provider = (get("LLM_PROVIDER") or "anthropic").lower()
        if provider not in DEFAULT_MODELS:
            raise ValueError(f"LLM_PROVIDER doit valoir 'anthropic' ou 'openai' (reçu : {provider!r})")
        temp = get("LLM_TEMPERATURE")
        return cls(
            provider=provider,
            model=get("LLM_MODEL") or DEFAULT_MODELS[provider],
            base_url=get("LLM_BASE_URL"),
            api_key=get("LLM_API_KEY"),
            effort=get("LLM_EFFORT"),
            temperature=float(temp) if temp else None,
            max_tool_steps=int(os.getenv("AGENT_MAX_TOOL_STEPS", "10")),
        )

    def label(self) -> str:
        where = f" @ {self.base_url}" if self.base_url else ""
        return f"{self.provider} / {self.model}{where}"
