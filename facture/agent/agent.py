"""Boucle de l'agent : question -> LLM -> outils MCP -> LLM -> ... -> réponse.

Indépendante du fournisseur de LLM (voir providers/) et de l'affichage (callback).
"""
from __future__ import annotations

import asyncio
import datetime as dt
from typing import Callable

from facture.agent.mcp_tools import McpTools
from facture.agent.providers.base import ChatProvider, ToolCall, ToolOutcome

SYSTEM_PROMPT = """Tu es l'assistant d'analyse des factures fournisseurs de l'entreprise. \
Tu réponds toujours en français.

Règles :
- Tu n'accèdes aux données QUE par les outils fournis. Tu n'as aucune autre source.
- Chaque nombre, montant, date ou nom que tu annonces doit venir d'un résultat d'outil \
obtenu dans cette conversation. Si les outils ne permettent pas de répondre, dis-le \
clairement : n'estime jamais, n'invente jamais une valeur.
- Pour un total ou un comptage, utilise invoice_totals : la base fait le calcul. \
N'additionne pas toi-même des montants.
- Avant d'écrire une requête pour run_sql, appelle describe_schema.
- Si un outil renvoie une erreur, lis le message, corrige ton appel et réessaie.
- Les montants sont en euros : écris-les au format français (ex. 92 406,82 €) et précise \
HT, TVA ou TTC.
- Termine par une phrase courte qui rappelle les filtres utilisés (période, statut, \
fournisseur...) pour que l'utilisateur puisse vérifier.

Date du jour : {today}."""


def system_prompt() -> str:
    return SYSTEM_PROMPT.format(today=dt.date.today().isoformat())


EventHandler = Callable[..., None]


class Agent:
    def __init__(self, provider: ChatProvider, tools: McpTools, max_tool_steps: int = 10,
                 on_event: EventHandler | None = None):
        self.provider = provider
        self.tools = tools
        self.max_tool_steps = max_tool_steps
        self.on_event = on_event or (lambda *a, **k: None)

    async def _run_tool(self, call: ToolCall) -> ToolOutcome:
        self.on_event("tool_call", call=call)
        if call.parse_error:
            out = ToolOutcome(call, call.parse_error, is_error=True)
        elif call.name not in self.tools.names:
            out = ToolOutcome(call, f"Outil inconnu : {call.name}. Outils disponibles : "
                                    f"{sorted(self.tools.names)}", is_error=True)
        else:
            try:
                text, is_error = await self.tools.call(call.name, call.arguments)
                out = ToolOutcome(call, text, is_error)
            except Exception as e:  # serveur MCP injoignable, etc.
                out = ToolOutcome(call, f"Échec de l'appel MCP : {e}", is_error=True)
        self.on_event("tool_result", outcome=out)
        return out

    async def ask(self, question: str) -> str:
        self.provider.add_user(question)
        for _ in range(self.max_tool_steps):
            step = await asyncio.to_thread(self.provider.step)
            if step.stop != "tools":
                if step.stop == "max_tokens":
                    step.text += "\n(réponse tronquée : limite de longueur atteinte)"
                return step.text
            if step.text.strip():
                self.on_event("thought", text=step.text)
            outcomes = [await self._run_tool(c) for c in step.tool_calls]
            self.provider.add_tool_results(outcomes)
        return (f"Je m'arrête : plus de {self.max_tool_steps} appels d'outils pour cette "
                "question sans réponse finale. Reformulez ou précisez la question.")

    def reset(self) -> None:
        self.provider.reset()
