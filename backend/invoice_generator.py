"""
Pro Forma Invoice generation wrapper for the web application.
Wraps Invoice Generator/generate_invoice.py for use by the backend API.
"""
import os
import sys

# Add the Invoice Generator directory to the Python path so we can import it
_INVOICE_GEN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "Invoice Generator",
)
if _INVOICE_GEN_DIR not in sys.path:
    sys.path.insert(0, _INVOICE_GEN_DIR)

from generate_invoice import generate_invoice  # noqa: E402

from backend.config import PROFORMA_DIR


def _format_date_for_pdf(date_str):
    """Convert date from YYYY-MM-DD (HTML input) to DD-MonthName-YYYY (e.g. 08-July-2026).
    If already in another format or conversion fails, return as-is."""
    if not date_str or date_str == '-':
        return date_str
    from datetime import datetime as _dt
    # Try YYYY-MM-DD first (from HTML date input)
    for fmt in ("%Y-%m-%d",):
        try:
            parsed = _dt.strptime(date_str.strip(), fmt)
            return parsed.strftime("%d-%B-%Y")  # e.g. 08-July-2026
        except ValueError:
            continue
    return date_str


def build_proforma_pdf(form_data: dict) -> str:
    """
    Build a Pro Forma Invoice PDF from form data submitted via the admin UI.

    Parameters
    ----------
    form_data : dict
        All invoice fields collected from the frontend form.

    Returns
    -------
    str
        Absolute path to the generated PDF file.
    """
    os.makedirs(PROFORMA_DIR, exist_ok=True)

    # Convert dates to DD-MonthName-YYYY format for PDF display
    form_data["date"] = _format_date_for_pdf(form_data.get("date") or "")
    form_data["reference_date"] = _format_date_for_pdf(form_data.get("reference_date") or "")

    # Build address lines from state, city, area
    address_lines = []
    city = form_data.get("city") or ""
    area = form_data.get("area") or ""
    state_name = form_data.get("state_name") or ""

    if area and city:
        address_lines.append(f"{area}, {city},")
    elif city:
        address_lines.append(f"{city},")
    if state_name:
        address_lines.append(f"{state_name}.")

    # Determine tax_type from state code
    state_code = str(form_data.get("state_code") or "24").strip()
    if state_code == "24":
        tax_type = "intrastate"
    else:
        tax_type = "interstate"

    buyer = {
        "name": form_data.get("buyer_name") or "Unknown",
        "address_lines": address_lines,
        "state_name": state_name,
        "state_code": state_code,
        "tax_type": tax_type,
    }

    # Add GSTIN only if provided
    gstin = (form_data.get("buyer_gstin") or "").strip()
    if gstin:
        buyer["gstin"] = gstin

    # Build the client dict expected by generate_invoice()
    client = {
        "invoice_no": form_data.get("invoice_no") or "",
        "date": form_data.get("date") or "",
        "mode_of_payment": form_data.get("mode_of_payment") or "ADVANCE",
        "reference_no": form_data.get("reference_no") or "-",
        "reference_date": form_data.get("reference_date") or "-",
        "other_references": form_data.get("sales_person") or "-",
        "buyer": buyer,
        "description": form_data.get("description") or "Professional Fees - Time",
        "sub_note": form_data.get("sub_note") or "",
        "hsn_sac": form_data.get("hsn_sac") or "998311",
        "gst_rate": float(form_data.get("gst_rate") or 18),
        "quantity": float(form_data.get("quantity") or 1),
        "quantity_unit": form_data.get("quantity_unit") or "Time",
        "rate": float(form_data.get("rate") or 0),
    }

    # Generate a unique filename
    agreement_id = form_data.get("agreement_id") or 0
    payment_id = form_data.get("payment_id") or 0
    import time
    timestamp = int(time.time())
    filename = f"proforma_{agreement_id}_{payment_id}_{timestamp}.pdf"
    output_path = os.path.join(PROFORMA_DIR, filename)
    client["output_file"] = output_path

    # Set the working directory to the Invoice Generator folder so logo.png resolves
    original_cwd = os.getcwd()
    try:
        os.chdir(_INVOICE_GEN_DIR)
        generate_invoice(client)
    finally:
        os.chdir(original_cwd)

    return output_path
