# Facture-projet

Pipeline de traitement de factures fournisseurs : du PDF (natif ou scanné) jusqu'à un agent IA
qui répond en français à des questions sur les factures.

```
 PDF (bucket S3) ──► fetch ──► bronze ──► silver ──► entrepôt DuckDB ──► serveur MCP ──► agent IA
                    landing   texte brut  tables     vues sur Parquet     outils en      LLM au choix
                              (PDF natif  typées +   (+ indicateurs       lecture seule  (Claude, Ollama…)
                              ou OCR)     quarantaine gold)
```

| Étape | Commande | Résultat |
|---|---|---|
| Récupération | `python -m facture fetch` | PDF dans `data/landing/`, classés « texte » / « image » |
| Bronze | `python -m facture bronze` | `data/bronze/<sha256>.json` : texte brut + métadonnées (incrémental) |
| Silver | `python -m facture silver` | `data/silver/*.parquet` : factures, lignes, quarantaine |
| Évaluation OCR | `python -m facture eval` | précision champ par champ, 150 vs 200 DPI |
| Entrepôt | `python -m facture warehouse` | `data/warehouse.duckdb` : vues bronze / silver / gold |
| Serveur MCP | `python -m facture mcp` | outils d'accès aux données pour un client MCP |
| Agent | `python -m facture agent` | conversation en français sur les factures |

## 1. Installation

Prérequis : Python 3.12, et les accès fournis (bucket S3, clé d'API Hyperfluid pour l'OCR).

```bash
python -m venv .venv
# Windows :      .venv\Scripts\activate
# macOS / Linux : source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # Windows : copy .env.example .env
```

Puis remplir `.env` (jamais commité) :

| Variable | Rôle |
|---|---|
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | clé S3 en lecture sur le bucket `factures` |
| `OCR_API_URL`, `OCR_API_KEY` | endpoint *chat/completions* de LightOnOCR-3 et clé, depuis la console Hyperfluid |
| `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL` | LLM de l'agent (voir § 5) |

## 2. Construire les données

```bash
python -m facture fetch       # télécharge les nouvelles factures
python -m facture bronze      # extrait le texte ; OCR seulement pour les PDF scannés
python -m facture silver      # parse, contrôle, met en quarantaine
python -m facture warehouse   # (re)construit les vues de l'entrepôt
python -m facture eval        # optionnel : qualité de l'OCR
```

Relancer `fetch` et `bronze` ne retraite rien de ce qui est déjà ingéré (identification par sha256
du PDF). Si l'OCR est indisponible, l'erreur est tracée dans le fichier bronze et le document sera
retraité au prochain lancement.

## 3. Interroger l'entrepôt

L'entrepôt ne contient **que des vues** : les données restent dans les fichiers Parquet/JSON,
en un seul exemplaire (stockage séparé du calcul). Schémas :

- `silver.invoices`, `silver.invoice_lines`, `silver.quarantine`
- `bronze.documents` (texte brut et métadonnées de chaque PDF)
- `gold.monthly_spend`, `gold.supplier_outstanding`, `gold.category_spend` (indicateurs)

**Depuis Python**

```python
from facture.warehouse.db import connect
con = connect()   # lecture seule : plusieurs programmes peuvent l'ouvrir en même temps
con.sql("SELECT supplier_name, sum(total_ttc) FROM silver.invoices GROUP BY 1").show()
```

**Depuis DBeaver**

1. Nouvelle connexion → **DuckDB** → fichier `data/warehouse.duckdb`.
2. Cocher **Read-only connection** (sinon DBeaver verrouille le fichier pour les autres programmes).
3. *Connection settings → Initialization → Bootstrap queries* : ajouter
   `SET file_search_path = 'C:/chemin/vers/Facture-projet';` (les vues utilisent des chemins
   relatifs à la racine du projet, ce qui les rend valides sur toute machine et dans Docker).

Sous Windows, fermer DBeaver avant `python -m facture warehouse` (le fichier est remplacé).

## 4. Serveur MCP

Outils exposés, tous en lecture seule ; chaque réponse contient `sql_executed`, les requêtes
réellement exécutées :

| Outil | Rôle |
|---|---|
| `list_invoices` | liste filtrée (fournisseur, client, statut, catégorie, période) et paginée |
| `invoice_totals` | nombre et totaux HT / TVA / TTC, ventilables par fournisseur, mois, catégorie… |
| `get_invoice` | détail d'une facture et de ses lignes (signale les documents en quarantaine) |
| `describe_schema` | tables, colonnes, types, valeurs possibles des statuts |
| `business_indicators` | indicateurs gold : dépenses mensuelles, encours fournisseurs, catégories |
| `run_sql` | SQL libre, **une seule instruction SELECT** |

Sécurité : requêtes paramétrées, connexion DuckDB en lecture seule, accès fichiers limité aux
dossiers bronze/silver avec configuration verrouillée, et refus de toute instruction autre qu'un
`SELECT` (y compris `COPY … TO`, `ATTACH`, `INSTALL`, `SET`, instructions multiples).

**Brancher sur Claude Code** (depuis la racine du projet) :

```bash
# Windows
claude mcp add --scope project factures -- .venv\Scripts\python.exe -m facture.mcp_server
# macOS / Linux
claude mcp add --scope project factures -- .venv/bin/python -m facture.mcp_server
```

**Brancher sur Claude Desktop** : dans `claude_desktop_config.json` :

```json
{
  "mcpServers": {
    "factures": {
      "command": "C:/chemin/vers/Facture-projet/.venv/Scripts/python.exe",
      "args": ["-m", "facture.mcp_server"],
      "env": { "PYTHONPATH": "C:/chemin/vers/Facture-projet" }
    }
  }
}
```

## 5. Agent

```bash
python -m facture agent                                   # conversation (/reset, /quit)
python -m facture agent -q "Quel est le montant TTC des factures en retard en 2025 ?"
```

L'agent n'accède aux données **que** par les outils MCP. Il affiche chaque outil appelé, le SQL
exécuté et le résultat, garde le fil de la conversation et s'arrête proprement (`/quit`, Ctrl+C,
ou au-delà de `AGENT_MAX_TOOL_STEPS` appels d'outils pour une même question).

**Changer de LLM sans toucher au code**, dans `.env` ou en ligne de commande :

| Fournisseur | Configuration |
|---|---|
| Claude (hébergé) | `LLM_PROVIDER=anthropic`, `LLM_MODEL=claude-opus-5-5`, `LLM_API_KEY=...` |
| Ollama (local) | `LLM_PROVIDER=openai`, `LLM_MODEL=qwen3:8b`, `LLM_BASE_URL=http://localhost:11434/v1` |
| Toute API compatible OpenAI | `LLM_PROVIDER=openai`, `LLM_MODEL`, `LLM_BASE_URL`, `LLM_API_KEY` |

Pour Ollama : installer [Ollama](https://ollama.com), puis `ollama pull qwen3:8b` (un modèle qui
sait appeler des outils). Même question, deux LLM :

```bash
python -m facture agent -q "Quel fournisseur a le plus gros encours ?"
python -m facture agent --provider openai --model qwen3:8b --base-url http://localhost:11434/v1 -q "Quel fournisseur a le plus gros encours ?"
```

## 6. Docker

```bash
docker build -t facture .
docker run --rm --env-file .env -v "$(pwd)/data:/app/data" facture fetch
docker run --rm --env-file .env -v "$(pwd)/data:/app/data" facture bronze
docker run --rm --env-file .env -v "$(pwd)/data:/app/data" facture silver
docker run --rm --env-file .env -v "$(pwd)/data:/app/data" facture warehouse
docker run --rm -it --env-file .env -v "$(pwd)/data:/app/data" facture agent
```

Les secrets passent par `--env-file`, jamais dans l'image ; les données vivent dans le volume.
Pour un Ollama tournant sur la machine hôte : `LLM_BASE_URL=http://host.docker.internal:11434/v1`.

## 7. Tests

Sans clé ni donnée réelle (fausse couche silver dans `tests/fixtures/`, faux LLM local) :

```bash
python tests/test_mcp_server.py   # chaque outil, transparence SQL, écritures refusées
python tests/test_agent.py        # boucle de l'agent avec les deux types de fournisseurs
```

Ils tournent aussi à chaque push (GitHub Actions, `.github/workflows/tests.yml`).

## Structure du dépôt

```
facture/
  ingest/       téléchargement S3, détection texte / image
  ocr/          client LightOnOCR-3 (derrière une interface OcrEngine)
  bronze/       extraction brute, incrémentale, traçable
  silver/       parsing, contrôles qualité, quarantaine
  evaluation/   précision OCR vs vérité terrain (eval/ground_truth.csv)
  warehouse/    entrepôt DuckDB (vues bronze / silver / gold)
  mcp_server/   serveur MCP (outils en lecture seule)
  agent/        agent conversationnel, LLM interchangeable (providers/)
tests/          tests du serveur MCP et de l'agent, fausse couche silver
```
