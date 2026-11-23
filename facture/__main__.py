import sys

COMMANDS = {
    "fetch": "Télécharge les factures depuis S3",
    "bronze": "Construit la couche bronze",
    "silver": "Construit la couche silver",
    "eval": "Évalue la qualité de l'OCR",
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


if __name__ == "__main__":
    main()
