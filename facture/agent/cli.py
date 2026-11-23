"""Interface en ligne de commande de l'agent.

    python -m facture agent                         # conversation (LLM défini dans .env)
    python -m facture agent -q "Question ?"         # une question puis sortie
    python -m facture agent --provider openai --model qwen3:8b --base-url http://localhost:11434/v1

Commandes pendant la conversation : /reset (oublier l'historique), /quit (quitter).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

from facture.agent.agent import Agent, system_prompt
from facture.agent.config import LLMConfig
from facture.agent.mcp_tools import McpTools
from facture.agent.providers.base import ChatProvider, ToolOutcome, ToolSpec

USE_COLOR = sys.stdout.isatty() and not os.getenv("NO_COLOR")


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else text


def make_provider(cfg: LLMConfig, tools: list[ToolSpec]) -> ChatProvider:
    if cfg.provider == "anthropic":
        from facture.agent.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(cfg.model, system_prompt(), tools, api_key=cfg.api_key,
                                 base_url=cfg.base_url, effort=cfg.effort)
    from facture.agent.providers.openai_provider import OpenAICompatibleProvider
    return OpenAICompatibleProvider(cfg.model, system_prompt(), tools, api_key=cfg.api_key,
                                    base_url=cfg.base_url, temperature=cfg.temperature)


def _short(value: object, limit: int = 400) -> str:
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return s if len(s) <= limit else s[:limit] + f"… ({len(s)} caractères)"


def show_event(kind: str, **data) -> None:
    """Rend visible ce que fait l'agent : outils appelés, SQL exécuté, résultats."""
    if kind == "thought":
        print(c(f"  · {data['text'].strip()}", "2"))
    elif kind == "tool_call":
        call = data["call"]
        args = json.dumps(call.arguments, ensure_ascii=False)
        print(c(f"  → outil {call.name}", "36") + c(f" {args}", "2"))
    elif kind == "tool_result":
        out: ToolOutcome = data["outcome"]
        if out.is_error:
            print(c(f"    ✗ erreur : {_short(out.content, 300)}", "31"))
            return
        try:
            payload = json.loads(out.content)
        except json.JSONDecodeError:
            print(c(f"    ← {_short(out.content)}", "2"))
            return
        for q in payload.pop("sql_executed", []):
            params = f"  params={q['params']}" if q.get("params") else ""
            print(c(f"    SQL: {' '.join(q['sql'].split())}{params}", "33"))
        print(c(f"    ← {_short(payload)}", "2"))


async def run(cfg: LLMConfig, questions: list[str]) -> None:
    async with McpTools() as tools:
        provider = make_provider(cfg, tools.specs)
        agent = Agent(provider, tools, cfg.max_tool_steps, on_event=show_event)
        print(c(f"Agent factures — LLM : {cfg.label()}", "1"))
        print(c(f"Outils MCP : {', '.join(sorted(tools.names))}", "2"))

        async def answer(q: str) -> None:
            t0 = time.perf_counter()
            try:
                text = await agent.ask(q)
            except Exception as e:  # clé invalide, serveur LLM injoignable...
                print(c(f"Erreur du LLM ({type(e).__name__}) : {e}", "31"))
                return
            print(f"\n{c('Agent', '1;32')} > {text.strip()}")
            print(c(f"  ({time.perf_counter() - t0:.1f} s)", "2"))

        if questions:
            for q in questions:
                print(f"\n{c('Vous', '1')} > {q}")
                await answer(q)
            return

        print(c("Posez une question sur les factures. /reset pour repartir de zéro, "
                "/quit pour quitter.", "2"))
        while True:
            try:
                q = (await asyncio.to_thread(input, f"\n{c('Vous', '1')} > ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not q:
                continue
            if q in ("/quit", "/exit", "/q"):
                break
            if q == "/reset":
                agent.reset()
                print(c("Historique effacé.", "2"))
                continue
            await answer(q)
        print(c("\nAu revoir.", "2"))


def main(argv: list[str] | None = None) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(prog="python -m facture agent",
                                description="Agent conversationnel sur les factures (via MCP).")
    p.add_argument("-q", "--question", action="append", default=[],
                   help="question à poser (répétable) ; sans -q : mode conversation")
    p.add_argument("--provider", help="remplace LLM_PROVIDER (anthropic | openai)")
    p.add_argument("--model", help="remplace LLM_MODEL")
    p.add_argument("--base-url", help="remplace LLM_BASE_URL")
    args = p.parse_args(argv)
    try:
        cfg = LLMConfig.from_env(provider=args.provider, model=args.model,
                                 base_url=args.base_url)
    except ValueError as e:
        p.error(str(e))
    try:
        asyncio.run(run(cfg, args.question))
    except KeyboardInterrupt:
        print("\nAu revoir.")


if __name__ == "__main__":
    main()
