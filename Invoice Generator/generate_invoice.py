"""
Pro Forma Invoice generator - D & V Business Consulting
=========================================================

DYNAMIC VERSION
---------------
This script no longer has one fixed set of invoice values. Instead, you
build a small "client record" (a Python dict) for each invoice you want to
raise, and pass it to generate_invoice(). Everything that should change
from client to client lives in that dict:

    - invoice_no
    - date
    - client / buyer details (name, address, state, GSTIN)
    - description of service, quantity, rate
    - GST rate
    (amount, GST amount, total amount, amount-in-words and
     tax-amount-in-words are all CALCULATED automatically - you never
     type them in, so there is no chance of a typing/rounding mistake)

IGST vs CGST+SGST (inter-state vs intra-state) - HANDLED AUTOMATICALLY
-----------------------------------------------------------------------
You do NOT need a separate file/script for in-state clients. For every
client record, the script compares the buyer's "state_code" to your own
SELLER["state_code"] (24 = Gujarat):

    - Buyer's state_code == 24 (Gujarat, same as you)  -> INTRA-state
      -> invoice shows CGST + SGST, each at HALF the gst_rate you enter.
    - Buyer's state_code != 24 (any other state)        -> INTER-state
      -> invoice shows IGST at the FULL gst_rate you enter.

If you ever need to override this (e.g. billing an SEZ unit that is
physically in Gujarat but must legally be billed as inter-state/IGST),
add "tax_type": "interstate" or "tax_type": "intrastate" inside that
client's "buyer" dict and it will skip the automatic state-code check.

Everything that does NOT change per client (your own company details,
bank account, standard terms, declaration, logo) stays fixed in the
SELLER / BANK_DETAILS / TERMS_OF_DELIVERY section near the top.

HOW TO USE
----------
1. Scroll down to the "SAMPLE CLIENT RECORDS" section at the bottom.
2. Copy one of the sample dicts, fill in the new client's details.
3. Run:  python3 generate_invoice.py
4. One PDF is created per client record, named using the invoice number.

Requires: reportlab, pillow  (pip install reportlab pillow --break-system-packages)
"""

import os
import re
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, black, white

# ============================================================
# YOUR COMPANY (SELLER) DETAILS - fixed for every invoice
# ============================================================

LOGO_PATH = "logo.png"  # your company logo (set to None to draw a placeholder mark)

SELLER = {
    "name": "D & V  Business Consulting",
    "address_lines": ["626, Iconic Shyamal,", "Shyamal Cross Road,", "Ahmedabad - 380015."],
    "gstin": "24ASLPP4013H1ZV",
    "state_name": "Gujarat",
    "state_code": "24",
    "contact": "+91-9824009829",
}

TERMS_OF_DELIVERY = [
    "1) All payment to be paid via Bank transfer or cheques",
    "2) Payment refund is not permissible",
    "3) Any breach of information is subject to violation of agreement",
    "4) TDS amount to be paid regularly and submit challan to biller",
    "5) Disputes subject to Ahmedabad jurisdiction.",
]

DECLARATION_TEXT = (
    "We declare that this invoice shows the actual price of the Services "
    "described and that all particulars are true and correct."
)

BANK_DETAILS = {
    "account_holder": "D & V  Business Consulting",
    "bank_name": "ICICI Bank",
    "account_no": "034405500698",
    "branch_ifsc": "VASNA & ICIC0000344",
    "swift_code": "ICICINBBCTS",
}

SIGNATORY_FOR = "for D & V  Business Consulting"

# ============================================================
# LAYOUT CONSTANTS (no need to edit below here)
# ============================================================

PAGE_W, PAGE_H = A4
MARGIN = 24
LEFT = MARGIN
RIGHT = PAGE_W - MARGIN
CONTENT_W = RIGHT - LEFT

FONT = "Helvetica"
FONT_B = "Helvetica-Bold"
FONT_I = "Helvetica-Oblique"


# ============================================================
# HELPERS: Indian-style number formatting + amount-in-words
# (these remove the need to type amounts/words by hand)
# ============================================================

def format_indian_currency(amount):
    """372500 -> '3,72,500.00' (Indian digit grouping, always 2 decimals)."""
    amount = round(float(amount), 2)
    negative = amount < 0
    amount = abs(amount)
    rupees = int(amount)
    paise = int(round((amount - rupees) * 100))
    s = str(rupees)
    if len(s) > 3:
        last3 = s[-3:]
        rest = s[:-3]
        parts = []
        while len(rest) > 2:
            parts.insert(0, rest[-2:])
            rest = rest[:-2]
        if rest:
            parts.insert(0, rest)
        formatted = ",".join(parts) + "," + last3
    else:
        formatted = s
    result = f"{formatted}.{paise:02d}"
    return ("-" if negative else "") + result


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine",
         "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen",
         "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _two_digit_words(n):
    if n < 20:
        return _ONES[n]
    return (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")).strip()


def _three_digit_words(n):
    if n >= 100:
        rest = _two_digit_words(n % 100)
        return _ONES[n // 100] + " Hundred" + (" " + rest if rest else "")
    return _two_digit_words(n)


def number_to_indian_words(amount):
    """
    439550 -> 'Indian Rupees Four Lakh Thirty Nine Thousand Five Hundred Fifty Only'
    Uses the Indian numbering system (Lakh / Crore), same style Tally uses.
    """
    amount = round(float(amount) + 1e-6, 2)
    rupees = int(amount)
    paise = int(round((amount - rupees) * 100))

    if rupees == 0:
        rupee_words = "Zero"
    else:
        crore, rupees = divmod(rupees, 10000000)
        lakh, rupees = divmod(rupees, 100000)
        thousand, hundred = divmod(rupees, 1000)

        parts = []
        if crore:
            parts.append(_three_digit_words(crore) + " Crore")
        if lakh:
            parts.append(_three_digit_words(lakh) + " Lakh")
        if thousand:
            parts.append(_three_digit_words(thousand) + " Thousand")
        if hundred:
            parts.append(_three_digit_words(hundred))
        rupee_words = " ".join(parts)

    words = f"Indian Rupees {rupee_words}"
    if paise:
        words += f" and {_two_digit_words(paise)} Paise"
    words += " Only"
    return words


def safe_filename(text):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)


# ============================================================
# DRAWING HELPERS (layout is unchanged from the original design)
# ============================================================

def draw_logo(c, x, y, size=30):
    """Draw the company logo image, or a placeholder mark if none is set."""
    if LOGO_PATH and os.path.exists(LOGO_PATH):
        try:
            c.drawImage(LOGO_PATH, x, y - size, width=size, height=size,
                        preserveAspectRatio=True, mask='auto')
            return
        except Exception:
            pass
    c.saveState()
    c.setFillColor(HexColor("#E8720C"))
    c.circle(x + size / 2, y - size / 2, size / 2, stroke=0, fill=1)
    c.setFillColor(white)
    c.setFont(FONT_B, 9)
    c.drawCentredString(x + size / 2, y - size / 2 - 3, "D&V")
    c.restoreState()


def rect(c, x, y, w, h):
    c.rect(x, y - h, w, h, stroke=1, fill=0)


def hline(c, x1, x2, y):
    c.line(x1, y, x2, y)


def vline(c, x, y1, y2):
    c.line(x, y1, x, y2)


def label_value(c, x, y, w, label, value, label_size=6.5, value_size=8.5,
                 align="left", bold_value=True):
    """Small label on top, value below - mimics the Tally box style."""
    c.setFont(FONT, label_size)
    c.drawString(x + 3, y - 9, label)
    c.setFont(FONT_B if bold_value else FONT, value_size)
    if align == "left":
        c.drawString(x + 3, y - 21, value)
    else:
        c.drawCentredString(x + w / 2, y - 21, value)


# ============================================================
# MAIN BUILDER - pass in one client record, get one PDF out
# ============================================================

def generate_invoice(client):
    """
    Build one Pro Forma Invoice PDF from a client record.

    Required keys in `client`:
        invoice_no        e.g. "2026-27/3049"
        date               e.g. "18-May-26"
        buyer: {
            name, address_lines (list of str), state_name, state_code,
            gstin (optional)
        }
        description        e.g. "Professional Fees - Time"
        gst_rate           numeric percent, e.g. 18
        rate               numeric taxable value per unit
    Optional keys:
        sub_note, hsn_sac (default "998311"), quantity (default 1),
        quantity_unit (default "Time"), mode_of_payment (default "ADVANCE"),
        reference_no, reference_date, other_references, output_file

    All amount / GST / words-in-figures fields are calculated - not typed in.
    Returns the output file path.
    """
    buyer = client["buyer"]

    invoice_no = client["invoice_no"]
    dated = client["date"]
    mode_of_payment = client.get("mode_of_payment", "ADVANCE")
    reference_no = client.get("reference_no", "-")
    reference_date = client.get("reference_date", "-")
    other_references = client.get("other_references", "-")

    description = client["description"]
    sub_note = client.get("sub_note", "")
    hsn_sac = client.get("hsn_sac", "998311")
    gst_rate = float(client["gst_rate"])
    quantity = float(client.get("quantity", 1))
    quantity_unit = client.get("quantity_unit", "Time")
    rate = float(client["rate"])

    # ---- calculated values (no manual typing = no mistakes) ----
    taxable_value = round(quantity * rate, 2)
    tax_amount = round(taxable_value * gst_rate / 100, 2)
    total_amount = round(taxable_value + tax_amount, 2)

    rate_str = format_indian_currency(rate)
    taxable_value_str = format_indian_currency(taxable_value)
    tax_amount_str = format_indian_currency(tax_amount)
    total_amount_str = format_indian_currency(total_amount)
    amount_in_words = number_to_indian_words(total_amount)
    tax_amount_in_words = number_to_indian_words(tax_amount)

    qty_display = f"{quantity:.3f} {quantity_unit}"

    # ---- IGST  vs  CGST + SGST : decided automatically per client ----
    # Same state as seller (Gujarat, code 24)  -> intra-state -> CGST + SGST
    # Different state from seller               -> inter-state -> IGST
    # To force one or the other (e.g. an SEZ unit is always inter-state /
    # IGST even if it sits inside Gujarat), add "tax_type": "interstate"
    # or "tax_type": "intrastate" to that client's buyer dict.
    tax_type = buyer.get("tax_type")
    if not tax_type:
        same_state = str(buyer.get("state_code", "")).strip() == str(SELLER["state_code"]).strip()
        tax_type = "intrastate" if same_state else "interstate"
    is_intrastate = tax_type.lower().startswith("intra")

    if is_intrastate:
        cgst_rate = sgst_rate = gst_rate / 2
        cgst_amount = round(tax_amount / 2, 2)
        sgst_amount = round(tax_amount - cgst_amount, 2)  # keeps CGST+SGST == tax_amount exactly
        cgst_amount_str = format_indian_currency(cgst_amount)
        sgst_amount_str = format_indian_currency(sgst_amount)
        tax_break_rows = [
            (f"CGST {cgst_rate:g}%", cgst_amount_str),
            (f"SGST {sgst_rate:g}%", sgst_amount_str),
        ]
    else:
        tax_break_rows = [(f"IGST {gst_rate:g}%", tax_amount_str)]

    output_file = client.get(
        "output_file",
        f"invoice_{safe_filename(invoice_no)}.pdf"
    )

    c = canvas.Canvas(output_file, pagesize=A4)

    # ---- Row height plan ----
    title_h = 20
    header_h = 196
    items_header_h = 26
    items_row1_h = 40
    items_spacer_h = 140
    items_tax_row_h = 18                                    # height of one tax line
    items_igst_h = items_tax_row_h * len(tax_break_rows)    # 1 line (IGST) or 2 (CGST+SGST)
    items_total_h = 24
    words_row_h = 30
    hsn_header_h = 24
    hsn_data_h = 18
    hsn_total_h = 18
    taxwords_h = 20
    bank_h = 92
    sig_h = 56

    items_h = items_header_h + items_row1_h + items_spacer_h + items_igst_h + items_total_h
    hsn_h = hsn_header_h + hsn_data_h + hsn_total_h

    total_h = (title_h + header_h + items_h + words_row_h + hsn_h +
               taxwords_h + bank_h + sig_h)

    top = PAGE_H - MARGIN
    bottom = top - total_h

    # ---- Outer border ----
    c.setLineWidth(1)
    rect(c, LEFT, top, CONTENT_W, total_h)

    y = top

    # ---- Title ----
    c.setFont(FONT_B, 13)
    c.drawCentredString(PAGE_W / 2, y - 14, "Pro Forma Invoice")
    y -= title_h
    hline(c, LEFT, RIGHT, y)

    # ================= HEADER GRID =================
    header_top = y
    left_col_w = CONTENT_W * 0.55
    right_col_w = CONTENT_W - left_col_w
    mid_col_w = right_col_w / 2
    col2_x = LEFT + left_col_w
    col3_x = col2_x + mid_col_w

    hr1 = 46   # Invoice No / Dated (+ seller info)
    hr2 = 20   # Mode of payment
    hr3 = 44   # Reference / Other ref (+ buyer info)
    hr4 = header_h - hr1 - hr2 - hr3  # Terms of delivery (+ buyer info cont.)

    y1 = header_top
    y2 = y1 - hr1
    y3 = y2 - hr2
    y4 = y3 - hr3
    y5 = y4 - hr4  # == header bottom

    # vertical separators for right block
    vline(c, col2_x, y5, y1)
    vline(c, col3_x, y3, y1)  # only spans rows 1 and 3 (row2/4 are merged across)

    # horizontal separators
    hline(c, col2_x, RIGHT, y2)
    hline(c, col2_x, RIGHT, y3)
    hline(c, col2_x, RIGHT, y4)
    hline(c, LEFT, col2_x, y3)  # split between seller block and buyer block

    # -- Seller info (left, spans rows 1-2) --
    ly = y1 - 11

    # Increased logo size
    logo_size = 70

    # Draw larger logo
    draw_logo(c, LEFT + 4, y1 - 6, size=logo_size)

    # Company text starts after the larger logo
    text_x = LEFT + 4 + logo_size + 8

    c.setFont(FONT_B, 10)
    c.drawString(text_x, ly, SELLER["name"])

    ly -= 11

    c.setFont(FONT, 7)
    for line in SELLER["address_lines"]:
        c.drawString(text_x, ly, line)
        ly -= 7

    c.drawString(text_x, ly, f"GSTIN/UIN: {SELLER['gstin']}")
    ly -= 7

    c.drawString(text_x, ly, f"State Name : {SELLER['state_name']}, Code : {SELLER['state_code']}")
    ly -= 7

    c.drawString(text_x, ly, f"Contact : {SELLER['contact']}")

    # -- Invoice No. / Dated (row1, right block) --
    label_value(c, col2_x, y1, mid_col_w, "Invoice No.", invoice_no)
    label_value(c, col3_x, y1, mid_col_w, "Dated", dated)

    # -- Mode of payment (row2, merged) --
    c.setFont(FONT, 6.5)
    c.drawString(col2_x + 3, y2 - 9, "Mode/Terms of Payment")
    c.setFont(FONT_B, 8.5)
    c.drawString(col2_x + 3, y2 - 19, mode_of_payment)

    # -- Buyer info (left, spans rows 3-4) --
    by = y3 - 10
    c.setFont(FONT, 7)
    c.drawString(LEFT + 4, by, "Buyer (Bill to)")
    by -= 10
    c.setFont(FONT_B, 8.5)
    c.drawString(LEFT + 4, by, buyer["name"])
    by -= 10
    c.setFont(FONT, 7.3)
    for line in buyer["address_lines"]:
        c.drawString(LEFT + 4, by, line)
        by -= 8.5
    if buyer.get("gstin"):
        c.drawString(LEFT + 4, by, f"GSTIN/UIN : {buyer['gstin']}")
        by -= 8.5
    by -= 2
    c.drawString(LEFT + 4, by, f"State Name : {buyer['state_name']}, Code : {buyer['state_code']}")

    # -- Reference No / Other references (row3, right block) --
    label_value(c, col2_x, y3, mid_col_w, "Reference No. & Date.",
                f"{reference_no}  {reference_date}", value_size=8)
    label_value(c, col3_x, y3, mid_col_w, "Sales Person", other_references)

    # -- Terms of delivery (row4, merged) --
    c.setFont(FONT, 6.5)
    c.drawString(col2_x + 3, y4 - 9, "Terms of Delivery")
    ty = y4 - 19
    c.setFont(FONT_B, 6.8)
    for line in TERMS_OF_DELIVERY:
        c.drawString(col2_x + 3, ty, line)
        ty -= 8.6

    y = y5
    hline(c, LEFT, RIGHT, y)

    # ================= ITEMS TABLE =================
    col_w = {
        "sl": CONTENT_W * 0.04,
        "desc": CONTENT_W * 0.33,
        "hsn": CONTENT_W * 0.10,
        "gst": CONTENT_W * 0.08,
        "qty": CONTENT_W * 0.13,
        "rate": CONTENT_W * 0.13,
        "per": CONTENT_W * 0.06,
        "amt": CONTENT_W * 0.13,
    }
    xs = {}
    cx = LEFT
    for k in ["sl", "desc", "hsn", "gst", "qty", "rate", "per", "amt"]:
        xs[k] = cx
        cx += col_w[k]

    header_bottom = y - items_header_h
    for k in ["desc", "hsn", "gst", "qty", "rate", "per", "amt"]:
        vline(c, xs[k], y - items_h, y)

    c.setFont(FONT_B, 7.5)

    def col_header(key, text1, text2=None):
        cxm = xs[key] + col_w[key] / 2
        if text2:
            c.drawCentredString(cxm, y - 10, text1)
            c.drawCentredString(cxm, y - 19, text2)
        else:
            c.drawCentredString(cxm, y - 14, text1)

    col_header("sl", "Sl", "No.")
    col_header("desc", "Description of", "Services")
    col_header("hsn", "HSN/SAC")
    col_header("gst", "GST", "Rate")
    col_header("qty", "Quantity")
    col_header("rate", "Rate")
    col_header("per", "per")
    col_header("amt", "Amount")

    hline(c, LEFT, RIGHT, header_bottom)
    y = header_bottom

    # -- item row 1 --
    row_top = y
    row_bottom = y - items_row1_h
    c.setFont(FONT, 8)
    c.drawCentredString(xs["sl"] + col_w["sl"] / 2, row_top - 12, "1")
    c.setFont(FONT_B, 8)
    c.drawString(xs["desc"] + 3, row_top - 12, description)
    if sub_note:
        c.setFont(FONT_I, 7.5)
        c.drawString(xs["desc"] + 3, row_top - 22, sub_note)
    c.setFont(FONT, 8)
    c.drawCentredString(xs["hsn"] + col_w["hsn"] / 2, row_top - 12, hsn_sac)
    c.drawCentredString(xs["gst"] + col_w["gst"] / 2, row_top - 12, f"{gst_rate:g} %")
    c.drawCentredString(xs["qty"] + col_w["qty"] / 2, row_top - 12, qty_display)
    c.drawRightString(xs["rate"] + col_w["rate"] - 3, row_top - 12, rate_str)
    c.drawCentredString(xs["per"] + col_w["per"] / 2, row_top - 12, quantity_unit)
    c.drawRightString(xs["amt"] + col_w["amt"] - 3, row_top - 12, taxable_value_str)
    y = row_bottom

    # -- spacer --
    y -= items_spacer_h

    # -- Tax break-up row(s): IGST, or CGST + SGST --
    for tax_label, tax_amt_str in tax_break_rows:
        c.setFont(FONT_I, 8)
        c.drawString(xs["desc"] + 3, y - 12, tax_label)
        c.setFont(FONT, 8)
        c.drawRightString(xs["amt"] + col_w["amt"] - 3, y - 12, tax_amt_str)
        y -= items_tax_row_h
    hline(c, LEFT, RIGHT, y)

    # -- Total row --
    c.setFont(FONT_B, 8.5)
    c.drawString(xs["desc"] + 3, y - 16, "Total")
    c.drawCentredString(xs["qty"] + col_w["qty"] / 2, y - 16, qty_display)
    c.drawRightString(RIGHT - 4, y - 16, f"Rs. {total_amount_str}")
    y -= items_total_h
    hline(c, LEFT, RIGHT, y)

    # ================= AMOUNT IN WORDS =================
    c.setFont(FONT, 7.5)
    c.drawString(LEFT + 4, y - 10, "Amount Chargeable (in words)")
    c.drawRightString(RIGHT - 4, y - 10, "E. & O.E")
    c.setFont(FONT_B, 8.5)
    c.drawString(LEFT + 4, y - 22, amount_in_words)
    y -= words_row_h
    hline(c, LEFT, RIGHT, y)

    # ================= HSN/SAC SUMMARY TABLE =================
    if is_intrastate:
        hsn_col_w = {
            "hsn": CONTENT_W * 0.16,
            "taxable": CONTENT_W * 0.15,
            "cgst_rate": CONTENT_W * 0.07,
            "cgst_amt": CONTENT_W * 0.15,
            "sgst_rate": CONTENT_W * 0.07,
            "sgst_amt": CONTENT_W * 0.15,
            "total_tax": CONTENT_W * 0.25,
        }
        col_order = ["hsn", "taxable", "cgst_rate", "cgst_amt", "sgst_rate", "sgst_amt", "total_tax"]
    else:
        hsn_col_w = {
            "hsn": CONTENT_W * 0.28,
            "taxable": CONTENT_W * 0.20,
            "igst_rate": CONTENT_W * 0.12,
            "igst_amt": CONTENT_W * 0.20,
            "total_tax": CONTENT_W * 0.20,
        }
        col_order = ["hsn", "taxable", "igst_rate", "igst_amt", "total_tax"]

    hxs = {}
    cx = LEFT
    for k in col_order:
        hxs[k] = cx
        cx += hsn_col_w[k]

    for k in col_order[1:]:
        vline(c, hxs[k], y - hsn_h, y)

    c.setFont(FONT_B, 7.5)
    c.drawCentredString(hxs["hsn"] + hsn_col_w["hsn"] / 2, y - 10, "HSN/SAC")
    c.drawCentredString(hxs["total_tax"] + hsn_col_w["total_tax"] / 2, y - 10, "Total")

    if is_intrastate:
        cgst_x1, cgst_x2 = hxs["cgst_rate"], hxs["cgst_amt"] + hsn_col_w["cgst_amt"]
        sgst_x1, sgst_x2 = hxs["sgst_rate"], hxs["sgst_amt"] + hsn_col_w["sgst_amt"]
        c.drawCentredString((cgst_x1 + cgst_x2) / 2, y - 10, "CGST")
        c.drawCentredString((sgst_x1 + sgst_x2) / 2, y - 10, "SGST")
        hline(c, cgst_x1, cgst_x2, y - 12)
        hline(c, sgst_x1, sgst_x2, y - 12)
        c.setFont(FONT, 7)
        c.drawCentredString(hxs["cgst_rate"] + hsn_col_w["cgst_rate"] / 2, y - 20, "Rate")
        c.drawCentredString(hxs["cgst_amt"] + hsn_col_w["cgst_amt"] / 2, y - 20, "Amount")
        c.drawCentredString(hxs["sgst_rate"] + hsn_col_w["sgst_rate"] / 2, y - 20, "Rate")
        c.drawCentredString(hxs["sgst_amt"] + hsn_col_w["sgst_amt"] / 2, y - 20, "Amount")
    else:
        igst_x1 = hxs["igst_rate"]
        igst_x2 = hxs["igst_amt"] + hsn_col_w["igst_amt"]
        c.drawCentredString((igst_x1 + igst_x2) / 2, y - 10, "IGST")
        hline(c, igst_x1, igst_x2, y - 12)
        c.setFont(FONT, 7)
        c.drawCentredString((igst_x1 + hxs["igst_amt"]) / 2, y - 20, "Rate")
        c.drawCentredString(hxs["igst_amt"] + hsn_col_w["igst_amt"] / 2, y - 20, "Amount")

    c.setFont(FONT, 7)
    c.drawCentredString(hxs["taxable"] + hsn_col_w["taxable"] / 2, y - 20, "Taxable")
    c.drawCentredString(hxs["total_tax"] + hsn_col_w["total_tax"] / 2, y - 20, "Tax Amount")
    c.drawCentredString(hxs["taxable"] + hsn_col_w["taxable"] / 2, y - 10, "Value")
    y -= hsn_header_h
    hline(c, LEFT, RIGHT, y)

    row_h = hsn_data_h
    c.setFont(FONT, 8)
    c.drawString(hxs["hsn"] + 4, y - row_h + 6, hsn_sac)
    c.drawRightString(hxs["taxable"] + hsn_col_w["taxable"] - 4, y - row_h + 6, taxable_value_str)
    if is_intrastate:
        c.drawCentredString(hxs["cgst_rate"] + hsn_col_w["cgst_rate"] / 2, y - row_h + 6, f"{cgst_rate:g}%")
        c.drawRightString(hxs["cgst_amt"] + hsn_col_w["cgst_amt"] - 4, y - row_h + 6, cgst_amount_str)
        c.drawCentredString(hxs["sgst_rate"] + hsn_col_w["sgst_rate"] / 2, y - row_h + 6, f"{sgst_rate:g}%")
        c.drawRightString(hxs["sgst_amt"] + hsn_col_w["sgst_amt"] - 4, y - row_h + 6, sgst_amount_str)
    else:
        c.drawCentredString(hxs["igst_rate"] + hsn_col_w["igst_rate"] / 2, y - row_h + 6, f"{gst_rate:g}%")
        c.drawRightString(hxs["igst_amt"] + hsn_col_w["igst_amt"] - 4, y - row_h + 6, tax_amount_str)
    c.drawRightString(hxs["total_tax"] + hsn_col_w["total_tax"] - 4, y - row_h + 6, tax_amount_str)
    y -= row_h
    hline(c, LEFT, RIGHT, y)

    c.setFont(FONT_B, 8)
    c.drawString(hxs["hsn"] + 4, y - 13, "Total")
    c.drawRightString(hxs["taxable"] + hsn_col_w["taxable"] - 4, y - 13, taxable_value_str)
    if is_intrastate:
        c.drawRightString(hxs["cgst_amt"] + hsn_col_w["cgst_amt"] - 4, y - 13, cgst_amount_str)
        c.drawRightString(hxs["sgst_amt"] + hsn_col_w["sgst_amt"] - 4, y - 13, sgst_amount_str)
    else:
        c.drawRightString(hxs["igst_amt"] + hsn_col_w["igst_amt"] - 4, y - 13, tax_amount_str)
    c.drawRightString(hxs["total_tax"] + hsn_col_w["total_tax"] - 4, y - 13, tax_amount_str)
    y -= hsn_total_h
    hline(c, LEFT, RIGHT, y)

    # ================= TAX AMOUNT IN WORDS =================
    c.setFont(FONT, 7.5)
    c.drawString(LEFT + 4, y - 13, "Tax Amount (in words) :")
    c.setFont(FONT_B, 8.5)
    c.drawString(LEFT + 110, y - 13, tax_amount_in_words)
    y -= taxwords_h
    hline(c, LEFT, RIGHT, y)

    # ================= DECLARATION + BANK DETAILS =================
    decl_w = CONTENT_W * 0.55
    vline(c, LEFT + decl_w, y - bank_h, y)

    c.setFont(FONT, 7)
    c.drawString(LEFT + 4, y - 11, "Declaration")
    c.setFont(FONT, 7)
    dy = y - 22
    words = DECLARATION_TEXT.split()
    line = ""
    max_chars = 68
    for w in words:
        test = (line + " " + w).strip()
        if len(test) > max_chars:
            c.drawString(LEFT + 4, dy, line)
            dy -= 9
            line = w
        else:
            line = test
    if line:
        c.drawString(LEFT + 4, dy, line)

    bx = LEFT + decl_w + 6
    by = y - 11
    c.setFont(FONT, 7.5)
    c.drawString(bx, by, "Company's Bank Details")
    by -= 10
    bank_rows = [
        ("A/c Holder's Name", BANK_DETAILS["account_holder"]),
        ("Bank Name", BANK_DETAILS["bank_name"]),
        ("A/c No.", BANK_DETAILS["account_no"]),
        ("Branch & IFS Code", BANK_DETAILS["branch_ifsc"]),
        ("SWIFT Code", BANK_DETAILS["swift_code"]),
    ]
    for label, val in bank_rows:
        c.setFont(FONT, 7)
        c.drawString(bx, by, f"{label}  :")
        c.setFont(FONT_B, 7)
        c.drawString(bx + 90, by, val)
        by -= 9.5

    y -= bank_h
    hline(c, LEFT, RIGHT, y)

    # ================= SIGNATURE ROW =================
    c.setFont(FONT, 7.5)
    c.drawString(LEFT + 4, y - 13, "Customer's Seal and Signature")
    c.drawRightString(RIGHT - 4, y - 13, SIGNATORY_FOR)
    c.setFont(FONT, 7.5)
    c.drawRightString(RIGHT - 4, y - sig_h + 10, "Authorised Signatory")
    y -= sig_h

    assert abs(y - bottom) < 1.0, f"layout mismatch: {y} vs {bottom}"

    # ================= FOOTER (outside border) =================
    c.setFont(FONT, 7.5)
    c.drawCentredString(PAGE_W / 2, bottom - 14, "This is a Computer Generated Invoice")

    c.showPage()
    c.save()
    return output_file


# ============================================================
# SAMPLE CLIENT RECORDS - add / edit one block per client
# ============================================================

CLIENTS = [
    {
        "invoice_no": "2026-27/3060",
        "date": "04-July-26",
        "mode_of_payment": "ADVANCE",
        "reference_no": "PMT-01",
        "reference_date": "27-Mar-26",
        "other_references": "Dharmesh Parikh",
        "buyer": {
            "name": "TATA MOTERS LIMITED",
            "address_lines": [
                "6th Floor, Bhive Premium, 48, Church Street, Delhi,",
                "560001",
            ],
            "state_name": "Delhi",
            "state_code": "07",  # Delhi's correct GST state code (was "1" - a typo in the old sample)
            # "gstin": "07XXXXX1234X1ZX",   # add if the client is GST-registered
        },
        "description": "Professional Fees - Time",
        "sub_note": "Quarterly Payment for Consultancy Services",
        "hsn_sac": "998311",
        "gst_rate": 18,
        "quantity": 1,
        "quantity_unit": "Time",
        "rate": 200000,
    },
    # ---- Sample INTRA-STATE client (same state as you, Gujarat = 24) ----
    # Because state_code == SELLER's state_code, this one AUTOMATICALLY
    # gets CGST 9% + SGST 9% instead of IGST 18% - no other change needed.
    {
        "invoice_no": "2026-27/3059",
        "date": "06-July-26",
        "mode_of_payment": "ADVANCE",
        "reference_no": "PMT-02",
        "reference_date": "06-Jul-26",
        "other_references": "Dharmesh Parikh",
        "buyer": {
            "name": "Sample Ahmedabad Client Pvt Ltd",
            "address_lines": [
                "204, Business Hub,",
                "S G Highway, Ahmedabad - 380054.",
            ],
            "state_name": "Gujarat",
            "state_code": "24",
            "gstin": "24AAAAA0000A1Z5",
        },
        "description": "Professional Fees - Time",
        "sub_note": "Monthly Retainer for Consultancy Services",
        "hsn_sac": "998311",
        "gst_rate": 18,
        "quantity": 1,
        "quantity_unit": "Time",
        "rate": 150000,
    },
    # ---- add more clients below, one dict per invoice ----
    # {
    #     "invoice_no": "2026-27/3050",
    #     "date": "20-May-26",
    #     "mode_of_payment": "30 DAYS CREDIT",
    #     "reference_no": "PMT-02",
    #     "reference_date": "20-May-26",
    #     "other_references": "-",
    #     "buyer": {
    #         "name": "Another Client Pvt Ltd",
    #         "address_lines": ["Some Address Line 1,", "City - 000000"],
    #         "state_name": "Gujarat",
    #         "state_code": "24",
    #         "gstin": "24XXXXX1234X1ZX",
    #     },
    #     "description": "Professional Fees - Time",
    #     "sub_note": "For 5 Ventures",
    #     "hsn_sac": "998311",
    #     "gst_rate": 18,
    #     "quantity": 1,
    #     "quantity_unit": "Time",
    #     "rate": 150000,
    # },
]


if __name__ == "__main__":
    for client in CLIENTS:
        path = generate_invoice(client)
        print(f"Saved {path}")
