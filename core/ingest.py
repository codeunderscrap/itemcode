"""Turn pasted text, a PDF, a scan/photo or a spreadsheet into invoice lines.

Every line comes back as {description, qty, uom, rate, hsn, vendor}. Vendor is
populated only when it appears on the line itself - an invoice-header supplier
is deliberately ignored, per the coding rule that vendor is part of item
identity only where the line says so.

PDF/OCR stack: PyMuPDF (`fitz`) reads digital PDFs - text layer and tables -
and rasterises any page that has neither. PaddleOCR reads whatever is left:
scanned PDF pages and photographed invoices. Both are real pip dependencies
(`pymupdf`, `paddlepaddle`, `paddleocr`) - the desktop install now assumes
internet + admin rights, a deliberate change from the original "no pip
install" constraint (agents/CONTRACTS.md house rule 1, revised 7 August 2026
- see that file's changelog note). PaddleOCR's models download once, on
first real OCR use, and are then cached under the user's profile - the
first scan on a fresh machine is slow; every one after that is fast. If
either library is missing (a broken install, not the expected path), each
function degrades to an empty line list with a plain-English note rather
than crashing the request - the same principle applies per-page inside
from_pdf() so one bad page never sinks the rest of the document.
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


# ------------------------------------------------------------------- OCR
# PaddleOCR is expensive to construct (it loads detection + recognition +
# angle-classification models) - built once, lazily, on first real use, and
# reused for the rest of the process. Constructing it at import time would
# slow down every server start whether or not anyone ever uploads a scan.
_OCR_ENGINE = None
_OCR_UNAVAILABLE = None  # once we know it can't load, remember why and stop retrying


def _get_ocr_engine():
    global _OCR_ENGINE, _OCR_UNAVAILABLE
    if _OCR_ENGINE is not None:
        return _OCR_ENGINE, None
    if _OCR_UNAVAILABLE is not None:
        return None, _OCR_UNAVAILABLE
    try:
        from paddleocr import PaddleOCR
        _OCR_ENGINE = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
        return _OCR_ENGINE, None
    except Exception as e:                                        # noqa: BLE001
        _OCR_UNAVAILABLE = (f"OCR unavailable ({e.__class__.__name__}) - "
                             f"pip install paddlepaddle paddleocr")
        return None, _OCR_UNAVAILABLE


def _ocr_array(img_array):
    """img_array: an HxWx3 (or HxWx4) numpy array, e.g. from a PyMuPDF
    pixmap or a decoded photo. Returns (text, note)."""
    engine, err = _get_ocr_engine()
    if engine is None:
        return "", err
    try:
        result = engine.ocr(img_array, cls=True)
    except Exception as e:                                        # noqa: BLE001
        return "", f"OCR failed ({e.__class__.__name__}: {e})"
    lines = []
    for page in (result or []):
        for det in (page or []):
            # det = [ [ [x,y]*4 ], (text, confidence) ]
            text = det[1][0]
            if text:
                lines.append(text)
    return "\n".join(lines), ""


def _pixmap_to_array(pix):
    """fitz.Pixmap -> numpy array, RGB. PaddleOCR reads numpy arrays or file
    paths directly; going through PIL isn't necessary."""
    import numpy as np
    if pix.alpha:
        pix = fitz_module().Pixmap(pix, 0)  # drop alpha channel
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 1:  # greyscale
        arr = np.repeat(arr, 3, axis=2)
    return arr


def fitz_module():
    import fitz
    return fitz


def from_pdf(data):
    """Text-layer first, then tables, then OCR per page - a page only goes
    to OCR when it has neither, so a normal digital invoice never pays the
    OCR cost at all."""
    try:
        fitz = fitz_module()
    except ImportError:
        return [], "pymupdf is not installed - cannot read PDFs (pip install pymupdf)"
    lines, notes = [], []
    try:
        doc = fitz.open(stream=data, filetype="pdf")
    except Exception as e:                                        # noqa: BLE001
        return [], f"could not open PDF ({e.__class__.__name__}: {e})"
    try:
        for pno, page in enumerate(doc, 1):
            got = False
            try:
                tf = page.find_tables()
                for table in (tf.tables if tf else []):
                    for row in table.extract():
                        cells = [_clean(c) for c in row if c]
                        if len(cells) >= 2:
                            p = parse_line(" | ".join(cells))
                            if p:
                                p["page"] = pno
                                lines.append(p)
                                got = True
            except Exception:                                     # noqa: BLE001
                pass  # table detection is a bonus, not required - fall through
            if not got:
                txt = page.get_text() or ""
                if txt.strip():
                    for p in from_text(txt):
                        p["page"] = pno
                        lines.append(p)
                    got = True
            if not got:
                pix = page.get_pixmap(dpi=220)
                ocr, note = _ocr_array(_pixmap_to_array(pix))
                if note:
                    notes.append(f"page {pno}: {note}")
                for p in from_text(ocr):
                    p["page"] = pno
                    p["ocr"] = True
                    lines.append(p)
    finally:
        doc.close()
    return lines, "; ".join(notes)


def from_image(data):
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return [], "Pillow not installed"
    img = Image.open(io.BytesIO(data)).convert("RGB")
    txt, note = _ocr_array(np.array(img))
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
