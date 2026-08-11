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
# Indian invoices comma-group numbers (8,000 · 1,65,847.50 in the Indian
# 2-3-3 lakh/crore style, not the Western 3-3-3 one) - \d+ alone splits
# "8,000" into "8" and a spurious "000", silently reading an 8000-unit
# quantity as 0. Accept an optional run of ",\d+" groups of any width
# instead of assuming a fixed group size.
_NUM = r"\d{1,3}(?:,\d+)*(?:\.\d+)?"
QTY_RE = re.compile(r"\b(" + _NUM + r")\s*(NOS|PCS|PC|KG|KGS|GM|G|LTR|L|ML|MTR|M|SET|BOX|PKT|ROLL|UNITS?|EA)\b", re.I)
RATE_RE = re.compile(r"(?:RS\.?|INR|₹)\s*(" + _NUM + r")", re.I)
MAKER_RE = re.compile(
    r"\b(?:make|maker|brand|mfg|mfr|manufacturer|vendor)\s*(?:[:\-]\s*)?([A-Za-z][A-Za-z0-9.&\-]{1,20}"
    r"(?:\s+[A-Z][A-Za-z0-9.&\-]{1,20})?)", re.I)

NOISE_LINE = re.compile(
    r"^\s*(?:tax\s+invoice|invoice\s*(?:no|date|id)|date:|dispatch\s+from:|gstin|pan\b|state\s+code|bill(?:ed|ing)?\s+to|ship(?:ped|ping)?\s+to|"
    r"terms|e-?way|declaration|subject\s+to|for\s+[A-Z].{0,40}$|authorised|signatory|"
    r"total|sub\s*total|grand\s*total|cgst|sgst|igst|round\s*off|amount\s+in\s+words|"
    r"bank|ifsc|a/?c\s*no|page\s+\d+|s\.?\s*no\.?$|hsn/?sac$|description[:$]|"
    r"tel:|fax:|attendee:|receiver:|billing\s+number:|email:|address:|reg\.?\s*no|reg\.?\s*date|payment\s+term|payment\s+method|beneficiary|swift\s+code|company\s+name|details\s+of\s+receiver|details\s+of\s+consignee|conference|registration\s*number|ltd\s*$|anhui|bkchcnbj|pre\s*payment|area,|global\s+decision|room\s+\d+|cocktail)", re.I)

_DOC_TYPE_NON_INVOICE = re.compile(
    r"\b(?:proforma\s+invoice|quotation|estimate|purchase\s+order)\b", re.I)

# ------------------------------------------------------- invoice item table
# A scanned/photographed invoice comes back from OCR as a flat list of text
# lines with no column or row structure at all - the buyer's address, the
# GSTIN, the column headers, and the actual item description all look
# identical to a per-line classifier. Real Indian tax invoices (this was
# tuned against real Tally-generated scans, 7 August 2026) almost always
# bracket their item table between a recognisable header row
# ("Description of Goods", "HSN/SAC", "Quantity", "Rate", "Amount" - read as
# several separate OCR lines, in no fixed order) and a footer
# ("Amount Chargeable (in words)", the declaration, "This is a Computer
# Generated Invoice"). Everything outside that bracket is header/footer
# noise regardless of what it says; everything inside it is item content,
# even when a single item's description wraps across many separate OCR
# lines - which it does, constantly.
_TABLE_HEADER_STRONG = re.compile(
    r"description\s+of\s+goods|hsn\s*/\s*sac|particulars\s+of\s+goods|item\s+description|hsn\s+code|hsn.?sac", re.I)
_TABLE_HEADER_WEAK = re.compile(
    r"\b(?:quantity|qty|rate|amount|hsn|sac|particulars|description)\b", re.I)
_TABLE_FOOTER = re.compile(
    r"amount\s+chargeable|chargeable\s*\(?\s*in\s+words|we\s+declare|declare\s+that\s+this\s+invoice|"
    r"computer\s+generated\s+invoice|authorised?\s+signatory|terms\s*&?\s*conditions|"
    r"tax'?ble|taxable\s+amt|cgst|sgst|igst|cess\s+amt|round\s*off|total\s+inv|tot\s+inv|total\s+amount|"
    r"e\s*\.?\s*&\s*o\s*\.?\s*e|continued\s+to\s+page|subject\s+to\s+.{0,20}jurisdiction", re.I)
# A new item starts at a small leading serial number - OCR renders the
# separator after it inconsistently (". ", ") ", "| ", a bare space, or
# nothing at all - "1Civil Works" and "6|Civil Works" both occur in real
# scans of the same invoice template.
_ITEM_SERIAL = re.compile(r"^\s*(\d{1,2})[).|\s]?\s*([A-Za-z].+)$")
# A quantity fragment like "1 Nos 81,333.60|Nos" matches the serial pattern
# just as well as a real "1 Civil Works" does - both are a small leading
# number followed by a word. The real tell is what that word IS: a unit of
# measure means this is a quantity, not a new item's serial number, no
# matter what follows it on the line.
_STARTS_WITH_UOM = re.compile(
    r"^(?:NOS|PCS?|KGS?|GM|G|LTR|L|ML|MTR|M|SET|BOX|PKT|ROLL|UNITS?|EA|LAYERS?|MM|CM|INCH(?:ES)?|FT|FEET|SQFT|SQM|THK|THICK|DIA|CORE|SQMM|BAGS?|DRUMS?|X)\b", re.I)
# A line with no letters at all - a bare rate, a tax percentage, a repeated
# quantity, an amount subtotal - carries a structured value (already pulled
# separately by QTY_RE/RATE_RE against the full merged blob) but no
# descriptive information, so it's dropped when rebuilding the human-
# readable description text for a merged item.
_MOSTLY_NUMERIC = re.compile(r"^[\d,.\s%|\[\]()/:\-]+$")
# Same idea, but for a bare "8,000 Nos" / "25.00Nos" style fragment, which
# _MOSTLY_NUMERIC alone doesn't catch because the unit word is letters.
_BARE_QTY_LINE = re.compile(
    r"^" + _NUM + r"\s*(?:NOS|PCS?|KGS?|GM|G|LTR|L|ML|MTR|M|SET|BOX|PKT|ROLL|UNITS?|EA)[\]\).|,\s]*$", re.I)


def _find_table_bounds(lines):
    """(start, end) indices bracketing the item table within `lines`
    (end exclusive), or None if no header row was recognisable - callers
    fall back to the plain per-line path in that case, which is the safer
    default for an invoice shaped nothing like the ones this was tuned on."""
    start = None
    for i, ln in enumerate(lines):
        if _TABLE_HEADER_STRONG.search(ln) or len(_TABLE_HEADER_WEAK.findall(ln)) >= 2:
            start = i + 1
            break
        # In OCR, column headers are often split into separate lines. Look ahead 5 lines.
        weak_count = len(_TABLE_HEADER_WEAK.findall(ln))
        if weak_count > 0:
            for j in range(1, 6):
                if i + j < len(lines):
                    weak_count += len(_TABLE_HEADER_WEAK.findall(lines[i+j]))
            if weak_count >= 3:
                start = i + 1
                break
    if start is None:
        return None
    # the header itself is usually several consecutive short column-name
    # fragments (HSN/SACQuantity / Rate / Description of Goods / per /
    # Amount / a mangled "Sl No" split across lines as "SI" ... "No") - OCR
    # does not read them in a fixed order and not all of them contain a
    # recognisable keyword ("per", "SI", "No" don't). What every one of
    # them reliably IS, though, is short and not the start of a numbered
    # item - so keep consuming on that shape instead of a keyword list, with
    # a hard cap so a genuinely short first item can't be swallowed forever.
    consumed = 0
    while (start < len(lines) and consumed < 8
           and not _ITEM_SERIAL.match(lines[start])
           and (len(lines[start]) < 15 or _TABLE_HEADER_STRONG.search(lines[start]))):
        start += 1
        consumed += 1
    end = len(lines)
    for i in range(start, len(lines)):
        if _TABLE_FOOTER.search(lines[i]):
            end = i
            break
    return start, end


def extract_invoice_items(raw_lines):
    """The item-table-aware alternative to calling parse_line() on every
    line independently. Used for anything that comes back as an
    undifferentiated block of text - OCR, and a digital PDF page with no
    text layer table PyMuPDF could detect. NOT used for text the operator
    typed or pasted directly (from_text()), where one line really is one
    item by the UI's own instruction."""
    lines = [_clean(x) for x in raw_lines if _clean(x)]
    bounds = _find_table_bounds(lines)
    if bounds is None:
        # no recognisable table header - safest fallback is the plain
        # per-line filter rather than guessing at a group boundary in
        # genuinely unstructured text.
        fallback_items = []
        for ln in lines:
            if _TABLE_FOOTER.search(ln):
                break
            if NOISE_LINE.match(ln):
                continue
            p = parse_line(ln)
            if p and (p.get("qty") or p.get("rate") or p.get("hsn")):
                fallback_items.append(p)
        return fallback_items

    start, end = bounds
    items, current = [], []

    def _flush():
        if not current:
            return
        blob = " ".join(current)
        p = parse_line(blob)
        if p:
            # parse_line's own description is the whole blob minus the bits
            # it recognised (HSN/rate/serial) - still messy here, because a
            # merged item routinely drags in bare tax-percentage and amount
            # fragments ("%6 18,000.00 9% 18,000.00") that carry no
            # descriptive information. Rebuild the description from just
            # the lines that read as prose, so the operator sees the actual
            # item text; qty/rate/hsn were already pulled from the full
            # blob above and are unaffected by this.
            prose = [ln for ln in current
                     if not (_MOSTLY_NUMERIC.match(ln) or _BARE_QTY_LINE.match(ln))]
            # No descriptive line at all - just a stray quantity/rate
            # fragment ("8 Nos" left over near a page break) - a real item
            # always has SOME text beyond a bare number+unit, so drop it
            # rather than surface a phantom item with nothing to identify it.
            if prose:
                p["description"] = _clean(" ".join(prose))
                items.append(p)
        current.clear()

    for ln in lines[start:end]:
        if NOISE_LINE.match(ln):
            continue
        m = _ITEM_SERIAL.match(ln)
        if m and not _STARTS_WITH_UOM.match(m.group(2).strip()):
            _flush()
            current.append(m.group(2).strip())
        else:
            current.append(ln)
    _flush()
    return items


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
        out["qty"], out["uom"] = float(m.group(1).replace(",", "")), m.group(2).upper()
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
    if not text:
        return []
        
    items = []
    blocks = re.split(r'\n\s*\n', text.strip())
    
    for block in blocks:
        raw_lines = block.splitlines()
        current_item_lines = []
        has_financial = False
        
        def _flush():
            if current_item_lines:
                p = parse_line(" | ".join(current_item_lines))
                if p:
                    items.append(p)
            current_item_lines.clear()
            
        for ln in raw_lines:
            ln = ln.strip()
            if not ln:
                continue
                
            line_has_financial = bool(QTY_RE.search(ln) or RATE_RE.search(ln) or HSN_RE.search(ln))
            
            if has_financial:
                _flush()
                has_financial = False
                
            current_item_lines.append(ln)
            if line_has_financial:
                has_financial = True
                
        _flush()
        
    return items


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
            if pno == 1:
                txt_for_check = page.get_text() or ""
                m = _DOC_TYPE_NON_INVOICE.search(txt_for_check[:2000])
                if m:
                    return [], f"Detected non-invoice document ({m.group(0).title()}) - please upload a Tax Invoice."
            
            got = False
            try:
                tf = page.find_tables()
                for table in (tf.tables if tf else []):
                    for row in table.extract():
                        cells = [_clean(c) for c in row if c]
                        if len(cells) >= 2:
                            line_str = " | ".join(cells)
                            if _TABLE_HEADER_STRONG.search(line_str) or len(_TABLE_HEADER_WEAK.findall(line_str)) >= 2:
                                continue
                            if _TABLE_FOOTER.search(line_str):
                                break
                            p = parse_line(line_str)
                            if p:
                                p["page"] = pno
                                lines.append(p)
                                got = True
            except Exception:                                     # noqa: BLE001
                pass  # table detection is a bonus, not required - fall through
            if not got:
                txt = page.get_text() or ""
                if txt.strip():
                    for p in extract_invoice_items(txt.splitlines()):
                        p["page"] = pno
                        lines.append(p)
                    got = True
            if not got:
                pix = page.get_pixmap(dpi=220)
                ocr, note = _ocr_array(_pixmap_to_array(pix))
                if pno == 1 and ocr.strip():
                    m = _DOC_TYPE_NON_INVOICE.search(ocr[:2000])
                    if m:
                        return [], f"Detected non-invoice document ({m.group(0).title()}) - please upload a Tax Invoice."
                if note:
                    notes.append(f"page {pno}: {note}")
                for p in extract_invoice_items(ocr.splitlines()):
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
    m = _DOC_TYPE_NON_INVOICE.search(txt[:2000])
    if m:
        return [], f"Detected non-invoice document ({m.group(0).title()}) - please upload a Tax Invoice."
    lines = extract_invoice_items(txt.splitlines())
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
