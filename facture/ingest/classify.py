import pymupdf
from facture.config import LANDING_DIR

MIN_TEXT_CHARS = 50


def classify_pdf(pdf_path: str) -> str:
    doc = pymupdf.open(pdf_path)
    for page in doc:
        text = page.get_text().strip()
        if len(text) >= MIN_TEXT_CHARS:
            doc.close()
            return "text"
    doc.close()
    return "image"


def classify_all() -> dict[str, str]:
    results = {}
    pdfs = sorted(LANDING_DIR.glob("*.pdf"))

    for pdf_path in pdfs:
        kind = classify_pdf(str(pdf_path))
        results[pdf_path.name] = kind
        print(f"  {pdf_path.name} → {kind}")

    text_count = sum(1 for v in results.values() if v == "text")
    image_count = len(results) - text_count
    print(f"\nTotal : {text_count} texte, {image_count} image, {len(results)} fichiers.")
    return results
