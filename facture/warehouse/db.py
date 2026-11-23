from __future__ import annotations

import os
from pathlib import Path

import duckdb
from dotenv import load_dotenv

# Racine du projet : tous les chemins relatifs (.env, vues) sont résolus depuis ici,
# quel que soit le dossier d'où l'on lance le programme.
ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")


def _path(var: str, default: str) -> Path:
    p = Path(os.getenv(var, default))
    return p if p.is_absolute() else ROOT / p


def warehouse_path() -> Path:
    return _path("WAREHOUSE_PATH", "data/warehouse.duckdb")


def silver_dir() -> Path:
    return _path("SILVER_DIR", "data/silver")


def bronze_dir() -> Path:
    return _path("BRONZE_DIR", "data/bronze")


def relative_to_root(p: Path) -> str:
    """Chemin écrit dans les vues : relatif à la racine si possible, pour que l'entrepôt
    reste valide sur une autre machine ou dans un conteneur."""
    try:
        return p.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def set_search_path(con: duckdb.DuckDBPyConnection) -> None:
    root = ROOT.as_posix().replace("'", "''")
    con.execute(f"SET file_search_path = '{root}'")


def connect(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """Connexion à l'entrepôt. En lecture seule par défaut : plusieurs programmes
    (MCP, agent, DBeaver) peuvent alors l'ouvrir en même temps."""
    path = warehouse_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Entrepôt introuvable ({path}). Lancez d'abord : python -m facture.warehouse.build"
        )
    con = duckdb.connect(str(path), read_only=read_only)
    set_search_path(con)
    return con
