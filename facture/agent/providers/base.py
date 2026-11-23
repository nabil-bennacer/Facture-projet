"""Interface commune des fournisseurs de LLM.

L'agent ne connaît que cette interface : changer de LLM = changer de configuration
(.env), jamais de code. Chaque fournisseur garde l'historique dans son format natif.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolSpec:
    """Un outil MCP tel qu'on le présente au LLM."""
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    parse_error: str | None = None  # arguments JSON illisibles renvoyés par le modèle


@dataclass
class ToolOutcome:
    call: ToolCall
    content: str
    is_error: bool = False


@dataclass
class Step:
    """Réponse du modèle pour un tour : du texte et/ou des appels d'outils."""
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop: str = "end"  # end | tools | refusal | max_tokens


class ChatProvider(ABC):
    name: str = "?"

    def __init__(self, model: str, system: str, tools: list[ToolSpec]):
        self.model = model
        self.system = system
        self.tools = tools

    @abstractmethod
    def add_user(self, text: str) -> None: ...

    @abstractmethod
    def step(self) -> Step:
        """Envoie l'historique au modèle et renvoie sa réponse (ajoutée à l'historique)."""

    @abstractmethod
    def add_tool_results(self, outcomes: list[ToolOutcome]) -> None: ...

    @abstractmethod
    def reset(self) -> None: ...
