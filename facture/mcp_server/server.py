"""Serveur MCP : expose l'entrepôt de factures (lecture seule) à un LLM.

    python -m facture.mcp_server        (transport stdio)
"""
from __future__ import annotations

import os
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from facture.mcp_server import queries as q
from facture.warehouse.db import ROOT

server = MCPServer(
    name="factures",
    instructions=(
        "Accès en lecture seule aux factures fournisseurs (couche silver). "
        "Chaque réponse contient 'sql_executed' : les requêtes réellement exécutées. "
        "N'annoncez jamais un chiffre qui ne vient pas d'un outil. "
        "Pour un total ou un comptage, préférez invoice_totals à une addition faite à la main."
    ),
)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                            open_world_hint=False)

GroupBy = Literal["none", "supplier", "customer", "month", "year", "payment_status", "category",
                  "quality_status"]


def _run(fn, *args, **kwargs) -> dict[str, Any]:
    """Exécute une requête et ajoute le SQL exécuté ; en cas d'erreur, renvoie au LLM un
    message exploitable (avec le SQL tenté) pour qu'il puisse se corriger."""
    with q.session() as s:
        try:
            result = fn(s, *args, **kwargs)
        except q.QueryError as e:
            raise ToolError(f"{e} | sql_executed: {s.executed}") from e
        return {**result, "sql_executed": s.executed}


FILTERS_DOC = (
    "Filtres optionnels, combinables : supplier / customer (recherche partielle, insensible "
    "à la casse et aux accents), payment_status (ex. 'Payée', 'En retard' ; voir "
    "describe_schema pour les valeurs existantes), category (ex. 'Conseil', 'Informatique'), "
    "quality_status ('valid' ou 'flagged'), "
    "date_from / date_to (date d'émission, AAAA-MM-JJ, bornes incluses)."
)


@server.tool(annotations=READ_ONLY, description=(
    "Liste des factures (numéro, fournisseur, client, dates, montants HT/TVA/TTC, statuts), "
    "triées par date d'émission, avec pagination (limit ≤ 200, offset). Renvoie aussi "
    "total_matching, le nombre total de factures correspondant aux filtres. " + FILTERS_DOC))
def list_invoices(supplier: str | None = None, customer: str | None = None,
                  payment_status: str | None = None, category: str | None = None,
                  quality_status: Literal["valid", "flagged"] | None = None,
                  date_from: str | None = None, date_to: str | None = None,
                  limit: int = 50, offset: int = 0) -> dict[str, Any]:
    return _run(q.list_invoices, limit=limit, offset=offset, supplier=supplier,
                customer=customer, payment_status=payment_status, category=category,
                quality_status=quality_status, date_from=date_from, date_to=date_to)


@server.tool(annotations=READ_ONLY, description=(
    "Nombre de factures et sommes des montants HT, TVA et TTC (en EUR), calculés par la base. "
    "À utiliser pour toute question de comptage ou de total. group_by permet de ventiler par "
    "fournisseur, client, mois, année, statut de paiement, catégorie ou statut de qualité. " + FILTERS_DOC))
def invoice_totals(group_by: GroupBy = "none", supplier: str | None = None,
                   customer: str | None = None, payment_status: str | None = None,
                   category: str | None = None,
                   quality_status: Literal["valid", "flagged"] | None = None,
                   date_from: str | None = None, date_to: str | None = None) -> dict[str, Any]:
    return _run(q.invoice_totals, group_by=group_by, supplier=supplier, customer=customer,
                payment_status=payment_status, category=category, quality_status=quality_status,
                date_from=date_from, date_to=date_to)


@server.tool(annotations=READ_ONLY, description=(
    "Détail complet d'une facture et de ses lignes, à partir de son numéro exact "
    "(ex. 'A-2025-0001') ou de son doc_sha256. Indique si le document a été mis en quarantaine."))
def get_invoice(invoice_number: str | None = None, doc_sha256: str | None = None) -> dict[str, Any]:
    return _run(q.get_invoice, invoice_number=invoice_number, doc_sha256=doc_sha256)


@server.tool(annotations=READ_ONLY, description=(
    "Schéma de l'entrepôt : tables (silver.invoices, silver.invoice_lines, silver.quarantine, "
    "bronze.documents, vues gold d'indicateurs), colonnes et types, valeurs possibles des statuts, plage de dates. "
    "À appeler avant d'écrire une requête avec run_sql."))
def describe_schema() -> dict[str, Any]:
    return _run(q.describe_schema)


@server.tool(annotations=READ_ONLY, description=(
    "Indicateurs métier précalculés (couche gold) : 'monthly_spend' (montants par mois), "
    "'supplier_outstanding' (encours non payé et retards par fournisseur), "
    "'category_spend' (dépenses par catégorie et par année)."))
def business_indicators(
        indicator: Literal["monthly_spend", "supplier_outstanding", "category_spend"]
) -> dict[str, Any]:
    return _run(q.business_indicator, indicator)


@server.tool(annotations=READ_ONLY, description=(
    "Exécute une requête SQL libre en LECTURE SEULE (dialecte DuckDB) : une seule instruction "
    "SELECT ou WITH ... SELECT. Toute écriture est refusée. À réserver aux questions que les "
    "autres outils ne couvrent pas ; appelez describe_schema d'abord. max_rows ≤ 500."))
def run_sql(query: str, max_rows: int = 100) -> dict[str, Any]:
    return _run(q.run_sql, query, max_rows=max_rows)


def main() -> None:
    # Les vues de l'entrepôt utilisent des chemins relatifs à la racine du projet, et le
    # verrouillage des accès fichiers les vérifie depuis le dossier courant : on s'y place,
    # quel que soit le dossier d'où le client MCP (ex. Claude Desktop) lance le serveur.
    os.chdir(ROOT)
    server.run("stdio")
