"""Turn pasted text, a PDF, a scan/photo or a spreadsheet into invoice lines.

Every line comes back as {description, qty, uom, rate, hsn, vendor}. Vendor is
populated only when it appears on the line itself - an invoice-header supplier
is deliberately ignored, per the coding rule that vendor is part of item
identity only where the line says so.
"""
import io
import os
import re

HSN_RE = re.compile(r"\b(\d{8}|\d{6})\b")
QTY_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(NOS|PCS|PC|KG|KGS|GM|G|LTR|L|ML|MTR|M|SET|BOX|PKT|ROLL|UNITS?|EA)\b", re.I)
RATE_RE = re.compile(r"(?:RS\.?|INR|₹)\s*([\d,]+(?:\.\d{1,2})?)", re.I)
MAKER_RE = re.compile(
    r"\b(?:make|maker|brand|mfg|mfr|manufacturer|vendor)\s*(?:[:\-]\s*)?([A-Za-z][A-Za-z0-9.&\-]{1,20}"
    r"(?:\s+[A-Z][A-Za-z0-9.&\-]{1,20})?)", re.I)

NOISE_LINE = re.compile(
    r"^\s*(?:tax\s+invoice|invoice\s*(?:no|date)|gstin|pan\b|state\s+code|bill\s+to|ship\s+to|"
    r"terms|e-?way|declaration|subject\s+to|for\s+[A-Z].{0,40}$|authorised|signatory|"
    r"total|sub\s*total|grand\s*total|cgst|sgst|igst|round\s*off|amount\s+in\s+words|"
    r"bank|ifsc|a/?c\s*no|page\s+\d+|s\.?\s*no\.?$|hsn/?sac$|description$)", re.I)


def _clean(s):
    return re.sub(r"\s+", " ", (s or "")).strip(" .:-|\t")


def parse_line(raw):
    """Pull structured fields out of one invoice line."""
    txt = _clean(raw)
    if len(txt) < 3 or NOISE_LINE.match(txt):
        return None
    out = {"raw": txt, "description": txt, "qty": None, "uom": None,
           "rate": None, "hsn": None, "vendor": None}

    m = HSN_RE.search(txt)
    if m:
        out["hsn"] = m.group(1)
    m = QTY_RE.search(txt)
    if m:
        out["qty"], out["uom"] = float(m.group(1)), m.group(2).upper()
    m = RATE_RE.search(txt)
    if m:
        out["rate"] = float(m.group(1).replace(",", ""))
    m = MAKER_RE.search(txt)
    if m:
        out["vendor"] = _clean(m.group(1))

    desc = txt
    if out["hsn"]:
        desc = re.sub(r"\b(?:HSN|SAC)\b\s*/?\s*(?:code)?\s*" + out["hsn"], " ", desc, flags=re.I)
        desc = desc.replace(out["hsn"], " ")
    desc = re.sub(r"\b(?:HSN|SAC)\b\s*[:\-]?\s*", " ", desc, flags=re.I)
    desc = RATE_RE.sub(" ", desc)
    desc = re.sub(r"^\s*\d{1,3}[).\s]\s*", "", desc)          # leading serial no.
    desc = re.sub(r"\s{2,}", " ", desc).strip(" .:-|")
    if len(desc) >= 3:
        out["description"] = desc
    return out if re.search(r"[A-Za-z]{3}", out["description"]) else None


def from_text(text):
    lines = []
    for raw in (text or "").splitlines():
        p = parse_line(raw)
        if p:
            lines.append(p)
    return lines


def from_pdf(data):
    """Text-layer first; fall back to OCR per page when a page has no text."""
    try:
        import pdfplumber
    except ImportError:
        return [], "pdfplumber is not installed - cannot read PDFs"
    lines, notes = [], []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            got = False
            for table in (page.extract_tables() or []):
                for row in table:
                    cells = [_clean(c) for c in row if c]
                    if len(cells) >= 2:
                        p = parse_line(" | ".join(cells))
                        if p:
                            p["page"] = pno
                            lines.append(p)
                            got = True
            if not got:
                txt = page.extract_text() or ""
                if txt.strip():
                    for p in from_text(txt):
                        p["page"] = pno
                        lines.append(p)
                    got = True
            if not got:
                ocr, note = _ocr_image(page.to_image(resolution=220).original)
                if note:
                    notes.append(f"page {pno}: {note}")
                for p in from_text(ocr):
                    p["page"] = pno
                    p["ocr"] = True
                    lines.append(p)
    return lines, "; ".join(notes)


def _ocr_image(pil_image):
    try:
        import pytesseract
    except ImportError:
        return "", "pytesseract not installed"
    cmd = os.environ.get("TESSERACT_EXE")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    else:
        for p in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                  r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
            if os.path.exists(p):
                pytesseract.pytesseract.tesseract_cmd = p
                break
    try:
        return pytesseract.image_to_string(pil_image), ""
    except Exception as e:                                        # noqa: BLE001
        return "", f"OCR unavailable ({e.__class__.__name__}) - install Tesseract-OCR"


def from_image(data):
    try:
        from PIL import Image
    except ImportError:
        return [], "Pillow not installed"
    txt, note = _ocr_image(Image.open(io.BytesIO(data)))
    lines = from_text(txt)
    for p in lines:
        p["ocr"] = True
    return lines, note


def from_spreadsheet(data, filename):
    if filename.lower().endswith(".csv"):
        text = data.decode("utf-8", "ignore")
        rows = [r.split(",") for r in text.splitlines()]
    else:
        try:
            import openpyxl
        except ImportError:
            return [], "openpyxl not installed"
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        rows = [[("" if c is None else str(c)) for c in r]
                for r in wb.worksheets[0].iter_rows(values_only=True)]
    lines = []
    for r in rows:
        cells = [_clean(c) for c in r if _clean(c)]
        if not cells:
            continue
        p = parse_line(" | ".join(cells))
        if p:
            lines.append(p)
    return lines, ""


def ingest(data, filename):
    """Dispatch by extension. Wrapped so a single bad/corrupt file degrades
    to an empty line list with an explanation rather than a 500 that takes
    the whole upload down - the same principle the OCR cascade already
    applies per page inside from_pdf()."""
    ext = os.path.splitext(filename or "")[1].lower()
    try:
        if ext == ".pdf":
            return from_pdf(data)
        if ext in (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"):
            return from_image(data)
        if ext in (".xlsx", ".xlsm", ".csv"):
            return from_spreadsheet(data, filename)
        return from_text(data.decode("utf-8", "ignore")), ""
    except Exception as e:                                       # noqa: BLE001
        return [], f"could not read {filename or 'this file'} ({e.__class__.__name__}: {e})"
