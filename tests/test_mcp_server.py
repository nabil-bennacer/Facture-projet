"""Test de bout en bout du serveur MCP : lance le vrai serveur (stdio), appelle chaque
outil et compare les résultats à des requêtes DuckDB directes sur la fausse couche silver.

    python tests/test_mcp_server.py

Utilise un entrepôt temporaire construit sur tests/fixtures/silver : ne touche pas à data/.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TMP = Path(tempfile.mkdtemp(prefix="facture-mcp-test-"))
ENV = {
    "SILVER_DIR": str(ROOT / "tests" / "fixtures" / "silver"),
    "BRONZE_DIR": str(TMP / "bronze"),
    "WAREHOUSE_PATH": str(TMP / "warehouse.duckdb"),
}
os.environ.update(ENV)
sys.path.insert(0, str(ROOT))

import duckdb  # noqa: E402
from mcp import Client, StdioServerParameters  # noqa: E402

from facture.warehouse.build import build  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "silver"
# Un fichier qui existe forcément, hors des dossiers autorisés (Windows ou Linux/CI)
OUTSIDE_FILE = (ROOT / "requirements.txt").as_posix()
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  {'OK ' if ok else 'FAIL'} {name}" + (f"  ({detail})" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def direct(sql: str) -> list[tuple]:
    sql = sql.replace("INVOICES", f"'{(FIXTURE / 'invoices.parquet').as_posix()}'")
    sql = sql.replace("LINES", f"'{(FIXTURE / 'invoice_lines.parquet').as_posix()}'")
    return duckdb.sql(sql).fetchall()


async def call(client: Client, tool: str, args: dict | None = None):
    res = await client.call_tool(tool, args or {})
    text = res.content[0].text if res.content else ""
    data = res.structured_content
    if data is None and not res.is_error:
        data = json.loads(text)
    return res.is_error, data, text


async def main() -> None:
    build()
    # Lancé depuis un autre dossier que la racine, comme le ferait Claude Desktop
    params = StdioServerParameters(command=sys.executable, args=["-m", "facture.mcp_server"],
                                   env={**os.environ, **ENV, "PYTHONPATH": str(ROOT)},
                                   cwd=str(TMP))
    async with Client(params) as client:
        print("\nOutils exposés")
        tools = {t.name for t in (await client.list_tools()).tools}
        expected = {"list_invoices", "invoice_totals", "get_invoice", "describe_schema", "run_sql"}
        check("les 5 outils sont exposés", expected <= tools, str(tools))

        print("\nlist_invoices")
        err, d, _ = await call(client, "list_invoices")
        n_all = direct("SELECT count(*) FROM INVOICES")[0][0]
        check("total_matching = nombre de factures", not err and d["total_matching"] == n_all)
        check("sql_executed présent", bool(d.get("sql_executed")))
        err, d, _ = await call(client, "list_invoices", {"payment_status": "en retard"})
        n_late = direct("SELECT count(*) FROM INVOICES WHERE payment_status = 'En retard'")[0][0]
        check("filtre statut, sans casse ni accent", d["total_matching"] == n_late, f"{d['total_matching']} vs {n_late}")
        err, d, _ = await call(client, "list_invoices", {"supplier": "aubry", "date_from": "2025-06-01"})
        n = direct("SELECT count(*) FROM INVOICES WHERE supplier_name='Aubry' AND issue_date >= DATE '2025-06-01'")[0][0]
        check("filtres fournisseur + période", d["total_matching"] == n, f"{d['total_matching']} vs {n}")
        err, d, _ = await call(client, "list_invoices", {"limit": 5, "offset": 5})
        check("pagination", d["returned"] == 5 and d["offset"] == 5)
        err, _, text = await call(client, "list_invoices", {"date_from": "01/02/2025"})
        check("date invalide -> erreur explicite", err and "AAAA-MM-JJ" in text, text[:120])

        print("\ninvoice_totals")
        err, d, _ = await call(client, "invoice_totals", {"payment_status": "En retard"})
        ttc = direct("SELECT sum(total_ttc) FROM INVOICES WHERE payment_status = 'En retard'")[0][0]
        check("total TTC exact (décimal)", Decimal(d["total_ttc"]) == ttc, f"{d['total_ttc']} vs {ttc}")
        err, d, _ = await call(client, "invoice_totals", {"group_by": "supplier"})
        s = sum(Decimal(g["total_ttc"]) for g in d["groups"])
        check("ventilation par fournisseur = total", s == direct("SELECT sum(total_ttc) FROM INVOICES")[0][0])
        err, d, _ = await call(client, "invoice_totals", {"category": "energie", "group_by": "category"})
        n = direct("SELECT count(*) FROM INVOICES WHERE category = 'Énergie'")[0][0]
        check("filtre + ventilation par catégorie", n > 0 and d["groups"][0]["invoice_count"] == n, str(d.get("groups")))
        err, _, _ = await call(client, "invoice_totals", {"group_by": "supplier_name; DROP VIEW x"})
        check("group_by hors liste blanche refusé", err)

        print("\nget_invoice")
        num, sha, n_lines = direct(
            "SELECT i.invoice_number, i.doc_sha256, count(*) FROM INVOICES i "
            "JOIN LINES l USING (doc_sha256) GROUP BY ALL ORDER BY 1 LIMIT 1")[0]
        err, d, _ = await call(client, "get_invoice", {"invoice_number": num})
        check("détail + lignes", not err and len(d["invoices"][0]["lines"]) == n_lines)
        err, d, _ = await call(client, "get_invoice", {"doc_sha256": sha})
        check("recherche par doc_sha256", not err and d["invoices"][0]["invoice_number"] == num)
        err, _, text = await call(client, "get_invoice", {"invoice_number": "ZZZ-0000"})
        check("facture inconnue -> erreur qui oriente", err and "list_invoices" in text, text[:120])
        err, _, text = await call(client, "get_invoice", {"invoice_number": "scan.pdf"})
        check("document en quarantaine signalé", err and "quarantaine" in text, text[:120])

        print("\ndescribe_schema")
        err, d, _ = await call(client, "describe_schema")
        check("tables silver décrites", {"silver.invoices", "silver.invoice_lines", "silver.quarantine"} <= set(d["tables"]))
        check("montants typés DECIMAL", d["tables"]["silver.invoices"]["columns"]["total_ttc"].startswith("DECIMAL"))

        print("\nbusiness_indicators (gold)")
        err, d, _ = await call(client, "business_indicators", {"indicator": "monthly_spend"})
        s = sum(Decimal(r["total_ttc"]) for r in d["rows"])
        check("somme mensuelle = total général", not err and s == direct("SELECT sum(total_ttc) FROM INVOICES")[0][0])
        err, d, _ = await call(client, "business_indicators", {"indicator": "supplier_outstanding"})
        unpaid = direct("SELECT sum(total_ttc) FROM INVOICES WHERE payment_status <> 'Payée'")[0][0]
        check("encours fournisseurs = factures non payées",
              not err and sum(Decimal(r["unpaid_ttc"]) for r in d["rows"]) == unpaid)
        err, _, _ = await call(client, "business_indicators", {"indicator": "silver.invoices"})
        check("indicateur inconnu refusé", err)

        print("\nrun_sql (lecture)")
        err, d, _ = await call(client, "run_sql", {"query": "SELECT supplier_name, count(*) AS n FROM silver.invoices GROUP BY 1 ORDER BY 2 DESC"})
        check("SELECT autorisé", not err and d["row_count"] > 0)
        err, d, _ = await call(client, "run_sql", {"query": "WITH t AS (SELECT * FROM silver.invoices) SELECT count(*) AS n FROM t;"})
        check("WITH ... SELECT autorisé", not err and d["rows"][0]["n"] == n_all)
        err, d, _ = await call(client, "run_sql", {"query": "SELECT * FROM silver.invoice_lines", "max_rows": 3})
        check("max_rows et truncated", d["row_count"] == 3 and d["truncated"])
        err, _, text = await call(client, "run_sql", {"query": "SELECT colonne_inexistante FROM silver.invoices"})
        check("erreur SQL renvoyée au LLM avec le SQL", err and "sql_executed" in text, text[:120])

        print("\nrun_sql (écritures refusées)")
        leak = TMP / "leak.csv"
        attacks = {
            "INSERT": "INSERT INTO silver.invoices SELECT * FROM silver.invoices",
            "DELETE": "DELETE FROM silver.invoices",
            "DROP": "DROP VIEW silver.invoices",
            "CREATE": "CREATE TABLE x AS SELECT 1",
            "UPDATE": "UPDATE silver.invoices SET total_ttc = 0",
            "COPY TO": f"COPY (SELECT * FROM silver.invoices) TO '{leak.as_posix()}'",
            "ATTACH": f"ATTACH '{(TMP / 'x.db').as_posix()}'",
            "INSTALL": "INSTALL httpfs",
            "SET": "SET enable_external_access = true",
            "multi-instructions": "SELECT 1; DROP VIEW silver.invoices",
            "fichier hors entrepôt": f"SELECT * FROM read_text('{OUTSIDE_FILE}')",
        }
        for name, sql in attacks.items():
            err, _, text = await call(client, "run_sql", {"query": sql})
            check(f"{name} refusé", err, text[:120])
        check("aucun fichier écrit", not leak.exists())
        err, d, _ = await call(client, "list_invoices")
        check("données intactes après les attaques", d["total_matching"] == n_all)

    print(f"\n{'ÉCHECS : ' + ', '.join(failures) if failures else 'Tous les tests passent.'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    asyncio.run(main())
