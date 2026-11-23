"""Génère une fausse couche silver (format du contrat) pour développer l'entrepôt,
le serveur MCP et l'agent sans attendre la vraie extraction.

    python tests/fixtures/make_silver_fixture.py

Écrit tests/fixtures/silver/{invoices,invoice_lines,quarantine}.parquet.
Déterministe (graine fixe) : relancer produit exactement les mêmes fichiers.
"""
from __future__ import annotations

import hashlib
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

OUT = Path(__file__).parent / "silver"
CENT = Decimal("0.01")
MONEY = pa.decimal128(12, 2)

SUPPLIERS = {
    "A": ("Aubry", "Conseil"),
    "BS": ("Bertin Services", "Maintenance"),
    "C": ("Carpentier", "Fournitures"),
    "GPE": ("Gilbert Pereira Énergie", "Énergie"),
    "LG": ("Leroy Garnier", "Logiciels"),
    "MS": ("Morel Solutions", "Informatique"),
    "P": ("Perrin", "Transport"),
    "V": ("Vidal", "Nettoyage"),
}
CUSTOMERS = ["Lombard", "Dumas", "Grondin Pinto et Fils", "Martin SARL", "Roche & Associés"]
ITEMS = ["Prestation de conseil", "Licence annuelle", "Maintenance mensuelle", "Livraison",
         "Fournitures de bureau", "Intervention sur site", "Formation", "Abonnement"]


def money(x) -> Decimal:
    return Decimal(x).quantize(CENT)


def main() -> None:
    rng = random.Random(42)
    processed_at = datetime(2026, 11, 23, 10, 0, tzinfo=timezone.utc)
    invoices, lines, quarantine = [], [], []

    for i in range(25):
        code = rng.choice(list(SUPPLIERS))
        supplier, category = SUPPLIERS[code]
        method = "ocr" if i % 2 else "text"
        source = f"facture_{code}-2025-{i + 1:04d}_{'scan' if method == 'ocr' else 'texte'}.pdf"
        sha = hashlib.sha256(source.encode()).hexdigest()
        issue = date(2025, 1, 1) + timedelta(days=rng.randint(0, 600))
        due = issue + timedelta(days=rng.choice([30, 45, 60]))

        # 3 documents rejetés : ils n'apparaissent que dans la quarantaine
        if i in (5, 14, 21):
            reason, details = {
                5: ("missing_invoice_number", "Numéro de facture introuvable dans le texte"),
                14: ("unreadable_amounts", "Aucun montant TTC reconnu par l'OCR"),
                21: ("duplicate", "Même fournisseur et même numéro que facture_A-2025-0001"),
            }[i]
            quarantine.append(dict(doc_sha256=sha, source_file=source, reason=reason,
                                   details=details, rejected_at=processed_at))
            continue

        n_lines = rng.randint(1, 4)
        total_ht = Decimal(0)
        for n in range(1, n_lines + 1):
            qty = Decimal(rng.choice([1, 1, 2, 3, 5, 10]))
            unit = money(rng.uniform(50, 4000))
            line_ht = money(qty * unit)
            total_ht += line_ht
            lines.append(dict(doc_sha256=sha, line_no=n, description=rng.choice(ITEMS),
                              quantity=qty.quantize(Decimal("0.001")), unit_price=unit,
                              vat_rate=Decimal("20.00"), line_total_ht=line_ht))
        total_tva = money(total_ht * Decimal("0.20"))
        total_ttc = total_ht + total_tva

        quality, notes = "valid", None
        if i in (3, 17):  # factures signalées : gardées mais marquées
            total_ttc += Decimal("10.00")
            quality, notes = "flagged", "total_ttc != total_ht + total_tva (écart 10,00 €)"

        if due < date(2026, 11, 23):
            status = rng.choice(["Payée", "Payée", "En retard"])
        else:
            status = "En attente"

        invoices.append(dict(
            doc_sha256=sha, invoice_number=f"{code}-2025-{i + 1:04d}", supplier_name=supplier,
            customer_name=rng.choice(CUSTOMERS), issue_date=issue, due_date=due, currency="EUR",
            total_ht=total_ht, total_tva=total_tva, total_ttc=total_ttc, payment_status=status,
            category=category, extraction_method=method, quality_status=quality, quality_notes=notes,
            source_file=source, processed_at=processed_at,
        ))

    ts = pa.timestamp("us", tz="UTC")
    schemas = {
        "invoices": pa.schema([
            ("doc_sha256", pa.string()), ("invoice_number", pa.string()),
            ("supplier_name", pa.string()), ("customer_name", pa.string()),
            ("issue_date", pa.date32()), ("due_date", pa.date32()), ("currency", pa.string()),
            ("total_ht", MONEY), ("total_tva", MONEY), ("total_ttc", MONEY),
            ("payment_status", pa.string()), ("category", pa.string()),
            ("extraction_method", pa.string()),
            ("quality_status", pa.string()), ("quality_notes", pa.string()),
            ("source_file", pa.string()), ("processed_at", ts),
        ]),
        "invoice_lines": pa.schema([
            ("doc_sha256", pa.string()), ("line_no", pa.int32()), ("description", pa.string()),
            ("quantity", pa.decimal128(12, 3)), ("unit_price", MONEY),
            ("vat_rate", pa.decimal128(5, 2)), ("line_total_ht", MONEY),
        ]),
        "quarantine": pa.schema([
            ("doc_sha256", pa.string()), ("source_file", pa.string()), ("reason", pa.string()),
            ("details", pa.string()), ("rejected_at", ts),
        ]),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, rows in [("invoices", invoices), ("invoice_lines", lines), ("quarantine", quarantine)]:
        pq.write_table(pa.Table.from_pylist(rows, schema=schemas[name]), OUT / f"{name}.parquet")
        print(f"{name}: {len(rows)} lignes")


if __name__ == "__main__":
    main()
