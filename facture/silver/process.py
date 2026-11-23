import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

import pyarrow as pa
import pyarrow.parquet as pq

from facture.config import BRONZE_DIR, SILVER_DIR


def parse_amount(raw: str) -> Decimal | None:
    if not raw:
        return None
    cleaned = raw.replace("€", "").replace(" ", "").replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def parse_date(raw: str):
    if not raw:
        return None
    raw = raw.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_rate(raw: str) -> Decimal | None:
    if not raw:
        return None
    cleaned = raw.replace("%", "").replace(" ", "").replace(",", ".").strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def extract_field(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self._current_row = []
        self._current_cell = ""
        self._in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag in ("td", "th"):
            self._in_cell = True
            self._current_cell = ""

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self._in_cell = False
            self._current_row.append(self._current_cell.strip())
        elif tag == "tr":
            if self._current_row:
                self.rows.append(self._current_row)
            self._current_row = []

    def handle_data(self, data):
        if self._in_cell:
            self._current_cell += data


def _parse_all_html_tables(text: str) -> list[list[list[str]]]:
    tables = []
    for m in re.finditer(r"<table.*?</table>", text, re.DOTALL):
        parser = TableParser()
        parser.feed(m.group())
        tables.append(parser.rows)
    return tables


def parse_html_table(text: str) -> list[list[str]]:
    m = re.search(r"<table.*?</table>", text, re.DOTALL)
    if not m:
        return []
    parser = TableParser()
    parser.feed(m.group())
    return parser.rows


def parse_text_table(text: str) -> list[list[str]]:
    lines = text.split("\n")
    header_idx = None
    for i, line in enumerate(lines):
        if "Désignation" in line:
            header_idx = i
            break

    if header_idx is None:
        return []

    col_names = ["Désignation", "Qté", "Prix unitaire HT", "TVA", "Total HT"]
    data_lines = []
    i = header_idx + 1

    while i < len(lines) and lines[i].strip() in col_names[1:]:
        i += 1

    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue

        if re.match(r"^Total\s+HT", line) or line.startswith("Mode de paiement") or line.startswith("IBAN"):
            break

        description = line
        vals = []
        i += 1
        while i < len(lines) and len(vals) < 4:
            val = lines[i].strip()
            if not val:
                i += 1
                continue
            vals.append(val)
            i += 1

        if len(vals) == 4:
            data_lines.append([description] + vals)

    return data_lines


def parse_invoice_lines(text: str, method: str) -> list[dict]:
    if method == "image":
        rows = parse_html_table(text)
        if rows:
            rows = rows[1:]
    else:
        rows = parse_text_table(text)

    lines = []
    for idx, row in enumerate(rows):
        if len(row) < 5:
            continue
        lines.append({
            "line_no": idx + 1,
            "description": row[0],
            "quantity": parse_amount(row[1]),
            "unit_price": parse_amount(row[2]),
            "vat_rate": parse_rate(row[3]),
            "line_total_ht": parse_amount(row[4]),
        })
    return lines


def parse_invoice(bronze: dict) -> dict:
    text = "\n".join(p["text"] for p in bronze["pages"])
    method = bronze["extraction_method"]

    supplier = None
    skip_words = {"facture", "numéro", "date", "catégorie", "statut", "facturé", "total", "mode"}
    lines_raw = text.strip().split("\n")
    for line in lines_raw:
        line_clean = line.strip().lstrip("#").strip().replace("**", "").strip()
        if not line_clean:
            continue
        if any(w in line_clean.lower() for w in skip_words):
            continue
        if line_clean.startswith("SIRET") or line_clean.startswith("TVA "):
            continue
        if re.match(r"^\d", line_clean) or line_clean.startswith("<") or line_clean == "---":
            continue
        supplier = line_clean
        break

    customer = extract_field(text, r"Client\s*:\s*(.+)")
    invoice_number = extract_field(text, r"Num[ée]ro de facture\s*\*?\*?\s*:\s*(.+)")
    if invoice_number:
        invoice_number = invoice_number.replace("*", "").strip()
    issue_date_raw = extract_field(text, r"Date d[''']?[ée]mission\s*\*?\*?\s*:\s*(.+)")
    due_date_raw = extract_field(text, r"Date d[''']?[ée]ch[ée]ance\s*\*?\*?\s*:\s*(.+)")
    payment_status = extract_field(text, r"Statut\s*\*?\*?\s*:\s*(.+)")
    category = extract_field(text, r"Cat[ée]gorie\s*\*?\*?\s*:\s*(.+)")
    if category:
        category = category.replace("*", "").strip()
    if issue_date_raw:
        issue_date_raw = issue_date_raw.replace("*", "").strip()
    if due_date_raw:
        due_date_raw = due_date_raw.replace("*", "").strip()
    if payment_status:
        payment_status = payment_status.replace("*", "").strip()

    total_ht = parse_amount(extract_field(text, r"\*?\*?Total HT\*?\*?\s*[:\s]*?([\d\s \xa0,.]+\s*€)"))
    total_tva = parse_amount(extract_field(text, r"\*?\*?Total TVA\*?\*?\s*[:\s]*?([\d\s \xa0,.]+\s*€)"))
    total_ttc = parse_amount(extract_field(text, r"\*?\*?Total TTC\*?\*?\s*[:\s]*?([\d\s \xa0,.]+\s*€)"))

    if total_ht is None:
        total_ht = parse_amount(extract_field(text, r"Total HT\s+([\d\s \xa0,.]+\s*€)"))
    if total_tva is None:
        total_tva = parse_amount(extract_field(text, r"Total TVA\s+([\d\s \xa0,.]+\s*€)"))
    if total_ttc is None:
        total_ttc = parse_amount(extract_field(text, r"Total TTC\s+([\d\s \xa0,.]+\s*€)"))

    if total_ht is None or total_tva is None or total_ttc is None:
        all_tables = _parse_all_html_tables(text)
        for table in all_tables:
            for row in table:
                if len(row) >= 2:
                    label = row[0].strip()
                    val = row[-1]
                    if "Total HT" in label and total_ht is None:
                        total_ht = parse_amount(val)
                    elif "Total TVA" in label and total_tva is None:
                        total_tva = parse_amount(val)
                    elif "Total TTC" in label and total_ttc is None:
                        total_ttc = parse_amount(val)

    invoice_lines = parse_invoice_lines(text, method)

    return {
        "invoice_number": invoice_number,
        "supplier_name": supplier,
        "customer_name": customer,
        "issue_date": parse_date(issue_date_raw),
        "due_date": parse_date(due_date_raw),
        "currency": "EUR",
        "total_ht": total_ht,
        "total_tva": total_tva,
        "total_ttc": total_ttc,
        "payment_status": payment_status,
        "category": category,
        "lines": invoice_lines,
    }


def validate_invoice(parsed: dict, sha: str, source_file: str) -> tuple[str, str | None]:
    reasons = []

    if not parsed["invoice_number"]:
        reasons.append("missing_invoice_number")
    if not parsed["supplier_name"]:
        reasons.append("missing_supplier")
    if parsed["total_ht"] is None or parsed["total_ttc"] is None:
        reasons.append("missing_amounts")

    if parsed["total_ht"] is not None and parsed["total_tva"] is not None and parsed["total_ttc"] is not None:
        expected_ttc = parsed["total_ht"] + parsed["total_tva"]
        if abs(expected_ttc - parsed["total_ttc"]) > Decimal("0.02"):
            reasons.append("totals_mismatch")

    if reasons:
        return "rejected", "; ".join(reasons)

    notes = []
    if not parsed["lines"]:
        notes.append("no_lines_parsed")
    if not parsed["due_date"]:
        notes.append("missing_due_date")

    if notes:
        return "flagged", "; ".join(notes)

    return "valid", None


def build_silver():
    bronze_files = sorted(BRONZE_DIR.glob("*.json"))

    invoices = []
    all_lines = []
    quarantine = []
    seen_invoice_numbers = set()

    for bf in bronze_files:
        with open(bf) as f:
            bronze = json.load(f)

        if bronze.get("status") != "success":
            continue

        sha = bronze["doc_sha256"]
        source_file = bronze["source_file"]

        parsed = parse_invoice(bronze)
        quality_status, quality_notes = validate_invoice(parsed, sha, source_file)

        if parsed["invoice_number"] and parsed["invoice_number"] in seen_invoice_numbers:
            quarantine.append({
                "doc_sha256": sha,
                "source_file": source_file,
                "reason": "duplicate",
                "details": f"Numéro {parsed['invoice_number']} déjà vu",
                "rejected_at": datetime.now(timezone.utc),
            })
            print(f"  DOUBLON {source_file}")
            continue

        if quality_status == "rejected":
            quarantine.append({
                "doc_sha256": sha,
                "source_file": source_file,
                "reason": quality_notes.split(";")[0].strip(),
                "details": quality_notes,
                "rejected_at": datetime.now(timezone.utc),
            })
            print(f"  REJET   {source_file} : {quality_notes}")
            continue

        if parsed["invoice_number"]:
            seen_invoice_numbers.add(parsed["invoice_number"])

        invoices.append({
            "doc_sha256": sha,
            "invoice_number": parsed["invoice_number"],
            "supplier_name": parsed["supplier_name"],
            "customer_name": parsed["customer_name"],
            "issue_date": parsed["issue_date"],
            "due_date": parsed["due_date"],
            "currency": parsed["currency"],
            "total_ht": parsed["total_ht"],
            "total_tva": parsed["total_tva"],
            "total_ttc": parsed["total_ttc"],
            "payment_status": parsed["payment_status"],
            "category": parsed["category"],
            "extraction_method": "ocr" if bronze["extraction_method"] == "image" else "text",
            "quality_status": quality_status,
            "quality_notes": quality_notes,
            "source_file": source_file,
            "processed_at": datetime.now(timezone.utc),
        })

        for line in parsed["lines"]:
            all_lines.append({
                "doc_sha256": sha,
                "line_no": line["line_no"],
                "description": line["description"],
                "quantity": line["quantity"],
                "unit_price": line["unit_price"],
                "vat_rate": line["vat_rate"],
                "line_total_ht": line["line_total_ht"],
            })

        print(f"  OK      {source_file} ({quality_status})")

    invoices_schema = pa.schema([
        ("doc_sha256", pa.string()),
        ("invoice_number", pa.string()),
        ("supplier_name", pa.string()),
        ("customer_name", pa.string()),
        ("issue_date", pa.date32()),
        ("due_date", pa.date32()),
        ("currency", pa.string()),
        ("total_ht", pa.decimal128(12, 2)),
        ("total_tva", pa.decimal128(12, 2)),
        ("total_ttc", pa.decimal128(12, 2)),
        ("payment_status", pa.string()),
        ("category", pa.string()),
        ("extraction_method", pa.string()),
        ("quality_status", pa.string()),
        ("quality_notes", pa.string()),
        ("source_file", pa.string()),
        ("processed_at", pa.timestamp("us", tz="UTC")),
    ])

    lines_schema = pa.schema([
        ("doc_sha256", pa.string()),
        ("line_no", pa.int32()),
        ("description", pa.string()),
        ("quantity", pa.decimal128(12, 3)),
        ("unit_price", pa.decimal128(12, 2)),
        ("vat_rate", pa.decimal128(5, 2)),
        ("line_total_ht", pa.decimal128(12, 2)),
    ])

    quarantine_schema = pa.schema([
        ("doc_sha256", pa.string()),
        ("source_file", pa.string()),
        ("reason", pa.string()),
        ("details", pa.string()),
        ("rejected_at", pa.timestamp("us", tz="UTC")),
    ])

    pq.write_table(pa.Table.from_pylist(invoices, schema=invoices_schema), SILVER_DIR / "invoices.parquet")
    pq.write_table(pa.Table.from_pylist(all_lines, schema=lines_schema), SILVER_DIR / "invoice_lines.parquet")
    pq.write_table(pa.Table.from_pylist(quarantine, schema=quarantine_schema), SILVER_DIR / "quarantine.parquet")

    print(f"\nSilver terminé : {len(invoices)} factures, {len(all_lines)} lignes, {len(quarantine)} en quarantaine.")
