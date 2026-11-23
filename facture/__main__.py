import sys

COMMANDS = {
    "fetch": "Télécharge les factures depuis S3",
    "bronze": "Construit la couche bronze",
    "silver": "Construit la couche silver",
    "eval": "Évalue la qualité de l'OCR",
    "warehouse": "(Re)construit l'entrepôt DuckDB (vues sur bronze/silver)",
    "mcp": "Lance le serveur MCP (stdio)",
    "agent": "Lance l'agent conversationnel",
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Usage : python -m facture <commande>\n")
        print("Commandes disponibles :")
        for cmd, desc in COMMANDS.items():
            print(f"  {cmd:10s}  {desc}")
        sys.exit(1)

    command = sys.argv[1]

    if command == "fetch":
        from facture.ingest.download import fetch
        from facture.ingest.classify import classify_all
        fetch()
        classify_all()

    elif command == "bronze":
        from facture.bronze.process import build_bronze
        build_bronze()

    elif command == "silver":
        from facture.silver.process import build_silver
        build_silver()

    elif command == "eval":
        from facture.evaluation.evaluate import run_evaluation
        run_evaluation()

    elif command == "warehouse":
        from facture.warehouse.build import build
        build()

    elif command == "mcp":
        from facture.mcp_server.server import main as run_mcp
        run_mcp()

    elif command == "agent":
        from facture.agent.cli import main as run_agent
        run_agent(sys.argv[2:])


if __name__ == "__main__":
    main()
