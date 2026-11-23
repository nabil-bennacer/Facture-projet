"""Requêtes SQL du serveur MCP, indépendantes du protocole.

Sécurité (les arguments viennent d'un LLM, donc d'une source non fiable) :
- requêtes paramétrées (`?`) pour toutes les valeurs ; les noms de colonnes ne
  viennent que de listes blanches ;
- connexion DuckDB en lecture seule ;
- accès fichiers limité aux dossiers bronze/silver, configuration verrouillée ;
- SQL libre : une seule instruction, obligatoirement un SELECT.
"""
from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Iterator

import duckdb

from facture.warehouse.db import bronze_dir, connect as connect_warehouse, silver_dir


class QueryError(Exception):
    """Erreur destinée au LLM : le message doit l'aider à corriger sa demande."""


def _sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


@contextmanager
def session() -> Iterator["Session"]:
    con = connect_warehouse(read_only=True)
    try:
        dirs = ", ".join(_sql_str(d.resolve().as_posix()) for d in (silver_dir(), bronze_dir()))
        con.execute(f"SET allowed_directories = [{dirs}]")
        con.execute("SET enable_external_access = false")
        con.execute("SET lock_configuration = true")
        yield Session(con)
    finally:
        con.close()


def jsonable(v: Any) -> Any:
    """Montants en chaîne décimale exacte (pas de float), dates en ISO."""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


class Session:
    def __init__(self, con: duckdb.DuckDBPyConnection):
        self.con = con
        self.executed: list[dict[str, Any]] = []  # transparence : SQL réellement exécuté

    def query(self, sql: str, params: list[Any] | None = None, max_rows: int | None = None
              ) -> tuple[list[dict[str, Any]], bool]:
        params = params or []
        self.executed.append({"sql": sql, "params": [jsonable(p) for p in params]})
        try:
            cur = self.con.execute(sql, params)
            cols = [d[0] for d in cur.description]
            raw = cur.fetchall() if max_rows is None else cur.fetchmany(max_rows + 1)
        except duckdb.Error as e:
            raise QueryError(f"Erreur SQL : {e}") from e
        truncated = max_rows is not None and len(raw) > max_rows
        rows = [{c: jsonable(v) for c, v in zip(cols, r)} for r in raw[:max_rows]]
        return rows, truncated


# --- Filtres communs -------------------------------------------------------

def _date(name: str, value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise QueryError(f"{name} doit être une date au format AAAA-MM-JJ (reçu : {value!r})")


QUALITY_STATUSES = {"valid", "flagged"}


def where_clause(supplier: str | None = None, customer: str | None = None,
                 payment_status: str | None = None, quality_status: str | None = None,
                 category: str | None = None, date_from: str | None = None, date_to: str | None = None
                 ) -> tuple[str, list[Any]]:
    clauses, params = [], []
    # Comparaisons insensibles à la casse et aux accents ("payee" == "Payée")
    if supplier:
        clauses.append("strip_accents(supplier_name) ILIKE strip_accents(?)")
        params.append(f"%{supplier}%")
    if customer:
        clauses.append("strip_accents(customer_name) ILIKE strip_accents(?)")
        params.append(f"%{customer}%")
    if payment_status:
        clauses.append("lower(strip_accents(payment_status)) = lower(strip_accents(?))")
        params.append(payment_status)
    if category:
        clauses.append("lower(strip_accents(category)) = lower(strip_accents(?))")
        params.append(category)
    if quality_status:
        if quality_status not in QUALITY_STATUSES:
            raise QueryError(f"quality_status doit valoir 'valid' ou 'flagged' (reçu : {quality_status!r})")
        clauses.append("quality_status = ?")
        params.append(quality_status)
    d_from, d_to = _date("date_from", date_from), _date("date_to", date_to)
    if d_from:
        clauses.append("issue_date >= ?")
        params.append(d_from)
    if d_to:
        clauses.append("issue_date <= ?")
        params.append(d_to)
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


# --- Outils ------------------------------------------------------------------

LIST_COLUMNS = ("invoice_number, supplier_name, customer_name, issue_date, due_date, "
                "total_ht, total_tva, total_ttc, currency, payment_status, category, quality_status")


def list_invoices(s: Session, limit: int = 50, offset: int = 0, **filters) -> dict[str, Any]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    where, params = where_clause(**filters)
    total = s.query(f"SELECT count(*) AS n FROM silver.invoices{where}", params)[0][0]["n"]
    rows, _ = s.query(
        f"SELECT {LIST_COLUMNS} FROM silver.invoices{where} "
        f"ORDER BY issue_date, invoice_number LIMIT ? OFFSET ?",
        params + [limit, offset],
    )
    return {"total_matching": total, "returned": len(rows), "offset": offset, "invoices": rows}


GROUP_BY = {
    "none": None,
    "supplier": "supplier_name",
    "customer": "customer_name",
    "month": "strftime(issue_date, '%Y-%m')",
    "year": "CAST(year(issue_date) AS VARCHAR)",
    "payment_status": "payment_status",
    "category": "category",
    "quality_status": "quality_status",
}


def invoice_totals(s: Session, group_by: str = "none", **filters) -> dict[str, Any]:
    if group_by not in GROUP_BY:
        raise QueryError(f"group_by doit être l'une des valeurs {sorted(GROUP_BY)} (reçu : {group_by!r})")
    where, params = where_clause(**filters)
    aggs = ("count(*) AS invoice_count, coalesce(sum(total_ht), 0) AS total_ht, "
            "coalesce(sum(total_tva), 0) AS total_tva, coalesce(sum(total_ttc), 0) AS total_ttc")
    expr = GROUP_BY[group_by]
    if expr is None:
        rows, _ = s.query(f"SELECT {aggs} FROM silver.invoices{where}", params)
        return {"currency": "EUR", **rows[0]}
    rows, _ = s.query(
        f"SELECT {expr} AS group_value, {aggs} FROM silver.invoices{where} "
        f"GROUP BY 1 ORDER BY 1", params,
    )
    return {"currency": "EUR", "group_by": group_by, "groups": rows}


def get_invoice(s: Session, invoice_number: str | None = None, doc_sha256: str | None = None
                ) -> dict[str, Any]:
    if not invoice_number and not doc_sha256:
        raise QueryError("Indiquez invoice_number (ex. 'A-2025-0001') ou doc_sha256.")
    col, val = ("doc_sha256", doc_sha256) if doc_sha256 else ("invoice_number", invoice_number)
    invoices, _ = s.query(f"SELECT * FROM silver.invoices WHERE {col} = ?", [val])
    if not invoices:
        rejected, _ = s.query(
            "SELECT source_file, reason, details FROM silver.quarantine "
            "WHERE doc_sha256 = ? OR source_file ILIKE ?", [val, f"%{val}%"])
        if rejected:
            raise QueryError(f"Le document {val!r} est en quarantaine (rejeté) : {rejected}. "
                             "Il n'apparaît pas dans silver.invoices.")
        raise QueryError(f"Aucune facture avec {col} = {val!r}. "
                         "Utilisez list_invoices pour retrouver le numéro exact.")
    for inv in invoices:
        inv["lines"], _ = s.query(
            "SELECT line_no, description, quantity, unit_price, vat_rate, line_total_ht "
            "FROM silver.invoice_lines WHERE doc_sha256 = ? ORDER BY line_no",
            [inv["doc_sha256"]])
    # Un même numéro peut exister chez deux fournisseurs : on renvoie tout
    return {"matches": len(invoices), "invoices": invoices}


COLUMN_DOCS = {
    "silver.invoices": "Une ligne par facture acceptée (quality_status 'valid' ou 'flagged').",
    "silver.invoice_lines": "Lignes de facture ; jointure sur doc_sha256.",
    "silver.quarantine": "Documents rejetés (absents de silver.invoices), avec le motif.",
    "bronze.documents": "Texte brut extrait de chaque PDF (natif ou OCR) et métadonnées.",
    "gold.monthly_spend": "Indicateur : nombre et montants des factures par mois d'émission.",
    "gold.supplier_outstanding": "Indicateur : encours (factures non payées) et retards par fournisseur.",
    "gold.category_spend": "Indicateur : dépenses par catégorie et par année.",
}

INDICATORS = {"monthly_spend", "supplier_outstanding", "category_spend"}


def business_indicator(s: Session, indicator: str) -> dict[str, Any]:
    if indicator not in INDICATORS:
        raise QueryError(f"indicator doit être l'une des valeurs {sorted(INDICATORS)} (reçu : {indicator!r})")
    rows, _ = s.query(f"SELECT * FROM gold.{indicator}")
    return {"indicator": indicator, "currency": "EUR", "rows": rows}


def describe_schema(s: Session) -> dict[str, Any]:
    cols, _ = s.query(
        "SELECT schema_name, table_name, column_name, data_type FROM duckdb_columns() "
        "WHERE schema_name IN ('silver', 'bronze', 'gold') "
        "ORDER BY schema_name, table_name, column_index")
    tables: dict[str, Any] = {}
    for c in cols:
        name = f"{c['schema_name']}.{c['table_name']}"
        t = tables.setdefault(name, {"description": COLUMN_DOCS.get(name, ""), "columns": {}})
        t["columns"][c["column_name"]] = c["data_type"]
    hints: dict[str, Any] = {}
    if "silver.invoices" in tables:
        hints["payment_status_values"] = [r["payment_status"] for r in s.query(
            "SELECT DISTINCT payment_status FROM silver.invoices ORDER BY 1")[0]]
        hints["category_values"] = [r["category"] for r in s.query(
            "SELECT DISTINCT category FROM silver.invoices ORDER BY 1")[0]]
        hints["issue_date_range"] = s.query(
            "SELECT min(issue_date) AS min, max(issue_date) AS max FROM silver.invoices")[0][0]
    return {
        "tables": tables,
        "value_hints": hints,
        "notes": ["Montants en EUR, type DECIMAL : renvoyés sous forme de chaînes exactes.",
                  "La jointure factures ↔ lignes se fait sur doc_sha256."],
    }


def run_sql(s: Session, query: str, max_rows: int = 100) -> dict[str, Any]:
    max_rows = max(1, min(max_rows, 500))
    try:
        statements = s.con.extract_statements(query)
    except duckdb.Error as e:
        raise QueryError(f"Requête invalide : {e}") from e
    if len(statements) != 1:
        raise QueryError("Une seule instruction SQL est autorisée par appel.")
    if statements[0].type != duckdb.StatementType.SELECT:
        raise QueryError(f"Seules les requêtes de lecture (SELECT / WITH ... SELECT) sont "
                         f"autorisées ; instruction refusée : {statements[0].type.name}.")
    rows, truncated = s.query(query, max_rows=max_rows)
    return {"row_count": len(rows), "truncated": truncated, "rows": rows}
