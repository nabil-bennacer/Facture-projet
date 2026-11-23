from __future__ import annotations

import os

import duckdb

from facture.warehouse.db import (
    bronze_dir, relative_to_root, set_search_path, silver_dir, warehouse_path,
)

SILVER_TABLES = ["invoices", "invoice_lines", "quarantine"]

# Couche gold : indicateurs métier calculés à la volée sur silver (vues, aucune copie)
UNPAID = "lower(strip_accents(coalesce(payment_status, ''))) <> 'payee'"
OVERDUE = (f"({UNPAID}) AND (lower(strip_accents(coalesce(payment_status, ''))) = 'en retard' "
           "OR due_date < current_date)")
GOLD_VIEWS = {
    "monthly_spend": """
        SELECT strftime(issue_date, '%Y-%m') AS month, count(*) AS invoice_count,
               sum(total_ht) AS total_ht, sum(total_tva) AS total_tva, sum(total_ttc) AS total_ttc
        FROM silver.invoices GROUP BY 1 ORDER BY 1""",
    "supplier_outstanding": f"""
        SELECT supplier_name,
               count(*) AS unpaid_count, sum(total_ttc) AS unpaid_ttc,
               count(*) FILTER (WHERE {OVERDUE}) AS overdue_count,
               coalesce(sum(total_ttc) FILTER (WHERE {OVERDUE}), 0) AS overdue_ttc,
               min(due_date) AS oldest_due_date
        FROM silver.invoices WHERE {UNPAID} GROUP BY 1 ORDER BY unpaid_ttc DESC""",
    "category_spend": """
        SELECT category, CAST(year(issue_date) AS VARCHAR) AS year, count(*) AS invoice_count,
               sum(total_ht) AS total_ht, sum(total_ttc) AS total_ttc
        FROM silver.invoices GROUP BY ALL ORDER BY year, total_ttc DESC""",
}


def build() -> None:
    target = warehouse_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.stem + ".tmp.duckdb")
    tmp.unlink(missing_ok=True)

    con = duckdb.connect(str(tmp))
    set_search_path(con)
    con.execute("CREATE SCHEMA silver")
    con.execute("CREATE SCHEMA bronze")
    con.execute("CREATE SCHEMA gold")

    for table in SILVER_TABLES:
        f = silver_dir() / f"{table}.parquet"
        if not f.exists():
            print(f"[absent] silver.{table} : {f} n'existe pas encore")
            continue
        con.execute(f"CREATE VIEW silver.{table} AS "
                    f"SELECT * FROM read_parquet('{relative_to_root(f)}')")
        n = con.execute(f"SELECT count(*) FROM silver.{table}").fetchone()[0]
        print(f"[ok] silver.{table} -> {relative_to_root(f)} ({n} lignes)")

    if any(bronze_dir().glob("*.json")):
        pattern = relative_to_root(bronze_dir()) + "/*.json"
        con.execute(f"CREATE VIEW bronze.documents AS "
                    f"SELECT * FROM read_json_auto('{pattern}', union_by_name = true)")
        n = con.execute("SELECT count(*) FROM bronze.documents").fetchone()[0]
        print(f"[ok] bronze.documents -> {pattern} ({n} documents)")
    else:
        print(f"[absent] bronze.documents : aucun JSON dans {bronze_dir()}")

    if (silver_dir() / "invoices.parquet").exists():
        for name, sql in GOLD_VIEWS.items():
            con.execute(f"CREATE VIEW gold.{name} AS {sql}")
        print(f"[ok] gold : {', '.join(GOLD_VIEWS)}")

    con.close()
    # Remplacement atomique : un lecteur ne voit jamais un entrepôt à moitié construit.
    # (Sous Windows, échoue si l'entrepôt est ouvert ailleurs, ex. DBeaver : le fermer.)
    os.replace(tmp, target)
    print(f"Entrepôt prêt : {target}")


if __name__ == "__main__":
    build()
