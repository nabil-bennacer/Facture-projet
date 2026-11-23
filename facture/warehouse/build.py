from __future__ import annotations

import os

import duckdb

from facture.warehouse.db import (
    bronze_dir, relative_to_root, set_search_path, silver_dir, warehouse_path,
)

SILVER_TABLES = ["invoices", "invoice_lines", "quarantine"]


def build() -> None:
    target = warehouse_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.stem + ".tmp.duckdb")
    tmp.unlink(missing_ok=True)

    con = duckdb.connect(str(tmp))
    set_search_path(con)
    con.execute("CREATE SCHEMA silver")
    con.execute("CREATE SCHEMA bronze")

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

    con.close()
    # Remplacement atomique : un lecteur ne voit jamais un entrepôt à moitié construit.
    # (Sous Windows, échoue si l'entrepôt est ouvert ailleurs, ex. DBeaver : le fermer.)
    os.replace(tmp, target)
    print(f"Entrepôt prêt : {target}")


if __name__ == "__main__":
    build()
