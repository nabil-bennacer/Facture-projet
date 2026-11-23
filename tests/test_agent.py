"""Test de l'agent sans LLM réel ni clé d'API : un faux serveur local imite l'API
Anthropic et une API compatible OpenAI. On vérifie, pour les DEUX fournisseurs, que
l'agent appelle un outil MCP, renvoie le résultat au modèle et termine sa réponse,
avec le vrai serveur MCP sur la fausse couche silver.

    python tests/test_agent.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="facture-agent-test-"))
os.environ.update({
    "SILVER_DIR": str(ROOT / "tests" / "fixtures" / "silver"),
    "BRONZE_DIR": str(TMP / "bronze"),
    "WAREHOUSE_PATH": str(TMP / "warehouse.duckdb"),
})
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402

from facture.agent.agent import Agent  # noqa: E402
from facture.agent.cli import make_provider  # noqa: E402
from facture.agent.config import LLMConfig  # noqa: E402
from facture.agent.mcp_tools import McpTools  # noqa: E402
from facture.warehouse.build import build  # noqa: E402

TOOL_ARGS = {"payment_status": "En retard"}
failures: list[str] = []
requests_seen: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(name)


class FakeLLM(BaseHTTPRequestHandler):
    """1er appel : demande l'outil invoice_totals. 2e appel (résultat d'outil reçu) :
    répond en recopiant le total TTC renvoyé par l'outil."""

    def log_message(self, *a):  # silence
        pass

    def _send(self, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        requests_seen.append({"path": self.path, "body": body})
        if "chat/completions" in self.path:
            last = body["messages"][-1]
            if last["role"] == "tool":
                ttc = json.loads(last["content"])["total_ttc"]
                msg = {"role": "assistant", "content": f"Total TTC en retard : {ttc} €"}
                finish = "stop"
            else:
                msg = {"role": "assistant", "content": None, "tool_calls": [{
                    "id": "call_1", "type": "function", "function": {
                        "name": "invoice_totals", "arguments": json.dumps(TOOL_ARGS)}}]}
                finish = "tool_calls"
            self._send({"id": "x", "object": "chat.completion", "created": 0, "model": body["model"],
                        "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
        else:  # Anthropic /v1/messages
            last = body["messages"][-1]["content"]
            results = [b for b in last if isinstance(b, dict) and b.get("type") == "tool_result"] \
                if isinstance(last, list) else []
            if results:
                ttc = json.loads(results[0]["content"])["total_ttc"]
                content = [{"type": "text", "text": f"Total TTC en retard : {ttc} €"}]
                stop = "end_turn"
            else:
                content = [{"type": "tool_use", "id": "toolu_1", "name": "invoice_totals",
                            "input": TOOL_ARGS}]
                stop = "tool_use"
            self._send({"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
                        "content": content, "stop_reason": stop, "stop_sequence": None,
                        "usage": {"input_tokens": 1, "output_tokens": 1}})


async def run_one(cfg: LLMConfig, expected: Decimal) -> None:
    events: list[str] = []
    async with McpTools() as tools:
        agent = Agent(make_provider(cfg, tools.specs), tools, cfg.max_tool_steps,
                      on_event=lambda kind, **d: events.append(kind))
        answer = await agent.ask("Quel est le montant TTC des factures en retard ?")
    check("l'outil MCP a été appelé et son résultat affiché",
          events == ["tool_call", "tool_result"], str(events))
    check("réponse finale = total calculé par la base", f"{expected} €" in answer, answer)


async def main() -> None:
    build()
    expected = duckdb.sql(
        f"SELECT sum(total_ttc) FROM '{(ROOT / 'tests/fixtures/silver/invoices.parquet').as_posix()}' "
        "WHERE payment_status = 'En retard'").fetchall()[0][0]
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeLLM)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}"

    print("\nFournisseur anthropic (SDK Anthropic)")
    await run_one(LLMConfig("anthropic", "claude-opus-5-5", url, "test-key", None, None, 5), expected)
    sent = [r for r in requests_seen if "messages" in r["path"]]
    check("outils MCP transmis au modèle", {t["name"] for t in sent[0]["body"]["tools"]} >= {"invoice_totals", "run_sql"})
    check("résultat d'outil renvoyé dans un bloc tool_result", any(
        isinstance(m["content"], list) and m["content"][0].get("type") == "tool_result"
        for m in sent[-1]["body"]["messages"]))

    print("\nFournisseur openai (API compatible OpenAI, ex. Ollama)")
    requests_seen.clear()
    await run_one(LLMConfig("openai", "qwen3:8b", url + "/v1", None, None, None, 5), expected)
    sent = [r for r in requests_seen if "chat/completions" in r["path"]]
    check("prompt système en français envoyé", "français" in sent[0]["body"]["messages"][0]["content"])

    server.shutdown()
    print(f"\n{'ÉCHECS : ' + ', '.join(failures) if failures else 'Tous les tests passent.'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
