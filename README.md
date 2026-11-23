# Facture-projet

Des factures PDF (natives ou scannées) jusqu'à un agent IA qui répond en français à des questions
sur ces factures.

```
PDF (S3) → fetch → bronze → silver → entrepôt DuckDB → serveur MCP → agent
```

## Installation

Python 3.12 requis.

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS / Linux : source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # puis remplir .env (clés S3, OCR et LLM)
```

## Lancer le pipeline

```bash
python -m facture fetch       # télécharge les factures depuis S3
python -m facture bronze      # extrait le texte (OCR pour les scans), incrémental
python -m facture silver      # tables typées, contrôles qualité, quarantaine
python -m facture eval        # précision de l'OCR (optionnel)
python -m facture warehouse   # entrepôt DuckDB : data/warehouse.duckdb
```

## Interroger les données

- **SQL** : ouvrir `data/warehouse.duckdb` en lecture seule (DBeaver, Python…). Tables :
  `silver.invoices`, `silver.invoice_lines`, `silver.quarantine`, `bronze.documents`, `gold.*`.
  Dans DBeaver, ajouter en requête d'initialisation `SET file_search_path = '<chemin du projet>';`
  (les vues utilisent des chemins relatifs à la racine du projet).
- **Serveur MCP** : `python -m facture mcp`. Pour Claude Code :
  `claude mcp add factures -- .venv\Scripts\python.exe -m facture.mcp_server`
- **Agent** : `python -m facture agent` (ou `-q "Ta question"`). Le LLM se choisit dans `.env` :

| LLM | Configuration |
|---|---|
| Claude | `LLM_PROVIDER=anthropic`, `LLM_MODEL=claude-opus-5-5`, `LLM_API_KEY=...` |
| Ollama (local) | `LLM_PROVIDER=openai`, `LLM_MODEL=qwen2.5:3b`, `LLM_BASE_URL=http://localhost:11434/v1` |

## Tests

```bash
python tests/test_mcp_server.py
python tests/test_agent.py
```

Ils n'ont besoin d'aucune clé ni donnée réelle.

## Docker

```bash
docker build -t facture .
docker run --rm --env-file .env -v "$(pwd)/data:/app/data" facture fetch   # idem pour les autres commandes
```
