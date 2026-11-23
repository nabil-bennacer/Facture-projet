import csv
import json
import re
import time
from decimal import Decimal
from pathlib import Path

import pymupdf

from facture.config import PROJECT_ROOT, SILVER_DIR, BRONZE_DIR, LANDING_DIR
from facture.ocr.engine import LightOnOCR, MAX_DIMENSION
from facture.silver.process import parse_invoice

EVAL_DIR = PROJECT_ROOT / "eval"
GT_PATH = EVAL_DIR / "ground_truth.csv"

FIELDS = [
    "invoice_number", "supplier_name", "customer_name",
    "issue_date", "due_date",
    "total_ht", "total_tva", "total_ttc",
]

RESOLUTIONS = [150, 200]


def load_ground_truth() -> dict[str, dict]:
    gt = {}
    with open(GT_PATH, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            gt[row["source_file"]] = row
    return gt


def load_silver() -> dict[str, dict]:
    import pandas as pd
    df = pd.read_parquet(SILVER_DIR / "invoices.parquet")
    result = {}
    for _, row in df.iterrows():
        result[row["source_file"]] = {
            "invoice_number": row.get("invoice_number", ""),
            "supplier_name": row.get("supplier_name", ""),
            "customer_name": row.get("customer_name", ""),
            "issue_date": str(row.get("issue_date", "")),
            "due_date": str(row.get("due_date", "")),
            "total_ht": str(row.get("total_ht", "")),
            "total_tva": str(row.get("total_tva", "")),
            "total_ttc": str(row.get("total_ttc", "")),
        }
    return result


def normalize(value: str) -> str:
    v = str(value).strip().replace(" ", "").replace("\xa0", "").replace(" ", "")
    v = v.replace("**", "").replace("*", "")
    if v in ("None", "NaT", "nan", ""):
        return ""
    try:
        d = Decimal(v.replace(",", "."))
        return str(d.normalize())
    except Exception:
        pass
    m = re.match(r"(\d{2})/(\d{2})/(\d{4})", v)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return v.lower()


def compare(gt: dict, extracted: dict) -> dict:
    scores = {f: {"correct": 0, "total": 0} for f in FIELDS}

    for source_file, gt_row in gt.items():
        if source_file not in extracted:
            for f in FIELDS:
                if gt_row.get(f):
                    scores[f]["total"] += 1
            continue

        ex_row = extracted[source_file]
        for f in FIELDS:
            gt_val = normalize(gt_row.get(f, ""))
            ex_val = normalize(ex_row.get(f, ""))
            if not gt_val:
                continue
            scores[f]["total"] += 1
            if gt_val == ex_val:
                scores[f]["correct"] += 1

    return scores


def print_scores(label: str, scores: dict, count: int):
    print(f"\n{label} — {count} factures")
    print(f"{'Champ':<20} {'Correct':>8} {'Total':>8} {'Précision':>10}")
    print("-" * 50)
    total_correct, total_all = 0, 0
    for f in FIELDS:
        c = scores[f]["correct"]
        t = scores[f]["total"]
        pct = f"{c/t*100:.1f}%" if t > 0 else "N/A"
        print(f"{f:<20} {c:>8} {t:>8} {pct:>10}")
        total_correct += c
        total_all += t
    print("-" * 50)
    pct = f"{total_correct/total_all*100:.1f}%" if total_all > 0 else "N/A"
    print(f"{'GLOBAL':<20} {total_correct:>8} {total_all:>8} {pct:>10}")
    return total_correct, total_all


def pdf_page_to_png_at_dpi(pdf_path: str, page_num: int, dpi: int) -> bytes:
    doc = pymupdf.open(pdf_path)
    page = doc[page_num]
    zoom = dpi / 72
    mat = pymupdf.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)

    if max(pix.width, pix.height) > MAX_DIMENSION:
        scale = MAX_DIMENSION / max(pix.width, pix.height)
        mat = pymupdf.Matrix(zoom * scale, zoom * scale)
        pix = page.get_pixmap(matrix=mat)

    png_bytes = pix.tobytes("png")
    doc.close()
    return png_bytes


def ocr_at_dpi(pdf_path: str, dpi: int, engine: LightOnOCR) -> list[dict]:
    doc = pymupdf.open(pdf_path)
    page_count = len(doc)
    doc.close()

    results = []
    for page_num in range(page_count):
        png_bytes = pdf_page_to_png_at_dpi(pdf_path, page_num, dpi)
        start = time.time()
        text = engine.extract(png_bytes)
        duration = time.time() - start
        results.append({
            "page": page_num,
            "text": text,
            "duration_seconds": round(duration, 2),
        })
    return results


def run_resolution_comparison(gt: dict):
    engine = LightOnOCR()
    gt_files = list(gt.keys())

    all_scores = {}
    all_times = {}

    for dpi in RESOLUTIONS:
        print(f"\n{'='*50}")
        print(f"OCR à {dpi} DPI...")
        print(f"{'='*50}")

        extracted = {}
        times = []

        for source_file in gt_files:
            pdf_path = LANDING_DIR / source_file
            if not pdf_path.exists():
                continue

            pages = ocr_at_dpi(str(pdf_path), dpi, engine)
            duration = sum(p["duration_seconds"] for p in pages)
            times.append(duration)

            bronze_fake = {
                "doc_sha256": "",
                "source_file": source_file,
                "extraction_method": "image",
                "pages": pages,
            }
            parsed = parse_invoice(bronze_fake)

            extracted[source_file] = {
                "invoice_number": parsed["invoice_number"] or "",
                "supplier_name": parsed["supplier_name"] or "",
                "customer_name": parsed["customer_name"] or "",
                "issue_date": str(parsed["issue_date"] or ""),
                "due_date": str(parsed["due_date"] or ""),
                "total_ht": str(parsed["total_ht"] or ""),
                "total_tva": str(parsed["total_tva"] or ""),
                "total_ttc": str(parsed["total_ttc"] or ""),
            }
            print(f"  {source_file} ({duration:.1f}s)")

        scores = compare(gt, extracted)
        all_scores[dpi] = scores
        all_times[dpi] = times

    print(f"\n{'='*60}")
    print("COMPARAISON DES RÉSOLUTIONS")
    print(f"{'='*60}")

    for dpi in RESOLUTIONS:
        avg_time = sum(all_times[dpi]) / len(all_times[dpi]) if all_times[dpi] else 0
        print_scores(f"{dpi} DPI (moy. {avg_time:.1f}s/page)", all_scores[dpi], len(gt))

    print(f"\n{'='*60}")
    print("RÉSUMÉ")
    print(f"{'='*60}")
    print(f"{'Champ':<20}", end="")
    for dpi in RESOLUTIONS:
        print(f" {dpi} DPI".rjust(12), end="")
    print()
    print("-" * (20 + 12 * len(RESOLUTIONS)))

    for f in FIELDS:
        print(f"{f:<20}", end="")
        for dpi in RESOLUTIONS:
            c = all_scores[dpi][f]["correct"]
            t = all_scores[dpi][f]["total"]
            pct = f"{c/t*100:.0f}%" if t > 0 else "N/A"
            print(f"{pct:>12}", end="")
        print()

    print("-" * (20 + 12 * len(RESOLUTIONS)))
    print(f"{'Temps moy/page':<20}", end="")
    for dpi in RESOLUTIONS:
        avg = sum(all_times[dpi]) / len(all_times[dpi]) if all_times[dpi] else 0
        print(f"{avg:.1f}s".rjust(12), end="")
    print()


def run_evaluation():
    if not GT_PATH.exists():
        print(f"Vérité terrain introuvable : {GT_PATH}")
        return

    gt = load_ground_truth()

    silver = load_silver()
    print_scores("Résultats silver (200 DPI)", compare(gt, silver), len(gt))

    print("\n\nLancement de la comparaison multi-résolution (150 vs 200 DPI)...")
    print("Cela va re-OCRiser les 20 factures à chaque résolution.\n")
    run_resolution_comparison(gt)
