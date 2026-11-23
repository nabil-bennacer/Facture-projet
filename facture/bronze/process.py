import hashlib
import json
import time
from datetime import datetime, timezone

import pymupdf

from facture.config import LANDING_DIR, BRONZE_DIR
from facture.ingest.classify import classify_pdf
from facture.ocr.engine import LightOnOCR, ocr_pdf


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def extract_text_pages(pdf_path: str) -> list[dict]:
    doc = pymupdf.open(pdf_path)
    pages = []
    for i, page in enumerate(doc):
        pages.append({
            "page": i,
            "text": page.get_text(),
            "duration_seconds": 0.0,
        })
    doc.close()
    return pages


def is_already_done(sha: str) -> bool:
    bronze_file = BRONZE_DIR / f"{sha}.json"
    if not bronze_file.exists():
        return False
    with open(bronze_file, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("status") == "success"


def build_bronze():
    pdfs = sorted(LANDING_DIR.glob("*.pdf"))
    engine = LightOnOCR()

    processed, skipped, errors = 0, 0, 0

    for pdf_path in pdfs:
        sha = sha256_file(str(pdf_path))

        if is_already_done(sha):
            skipped += 1
            continue

        kind = classify_pdf(str(pdf_path))
        record = {
            "doc_sha256": sha,
            "source_file": pdf_path.name,
            "extraction_method": kind,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }

        try:
            if kind == "text":
                pages = extract_text_pages(str(pdf_path))
            else:
                pages = ocr_pdf(str(pdf_path), engine)

            record["pages"] = pages
            record["total_duration_seconds"] = round(
                sum(p["duration_seconds"] for p in pages), 2
            )
            record["status"] = "success"
            processed += 1
            print(f"  OK  {pdf_path.name} ({kind}, {record['total_duration_seconds']}s)")

        except Exception as e:
            record["pages"] = []
            record["total_duration_seconds"] = 0.0
            record["status"] = "error"
            record["error"] = str(e)
            errors += 1
            print(f"  ERR {pdf_path.name} : {e}")

        with open(BRONZE_DIR / f"{sha}.json", "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)

    print(f"\nBronze terminé : {processed} traités, {skipped} déjà faits, {errors} erreurs.")
