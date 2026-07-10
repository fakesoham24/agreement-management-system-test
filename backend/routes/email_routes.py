"""
Email Routes — Admin endpoints for managing email credentials, templates,
company selection, manual email sending, pro forma invoice generation, and viewing logs.
"""
import json
import os
import uuid
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from typing import Optional, List
from backend.auth import require_admin, get_current_user, decode_token
from backend.database import get_db
from backend.config import PROFORMA_DIR, APP_URL
from backend.email_service import (
    encrypt_value, decrypt_value, get_access_token,
    send_email, render_template, DEFAULT_EMAIL_TEMPLATE,
    DEFAULT_CONSULTANT_EMAIL_TEMPLATE,
)

# 1x1 transparent PNG pixel (68 bytes)
_TRACKING_PIXEL = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
    b'\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
    b'\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01'
    b'\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


def _inject_tracking_pixel(body: str, tracking_id: str, is_html: bool) -> str:
    """Append a 1x1 tracking pixel <img> tag to the email body."""
    pixel_url = f"{APP_URL}/api/email/track/{tracking_id}.png"
    pixel_tag = (
        f'<img src="{pixel_url}" width="1" height="1" '
        f'style="display:block;width:1px;height:1px;opacity:0;" alt="" />'
    )
    if is_html:
        # Insert before </body> if present, otherwise append
        if '</body>' in body.lower():
            import re
            return re.sub(r'(</body>)', pixel_tag + r'\1', body, count=1, flags=re.IGNORECASE)
        return body + pixel_tag
    else:
        # Wrap plain text in minimal HTML with the pixel
        return (
            f'<html><body>'
            f'<pre style="font-family:inherit;white-space:pre-wrap;">{body}</pre>'
            f'{pixel_tag}</body></html>'
        )

router = APIRouter(prefix="/api/email", tags=["Email"])


# ==========================================
# Request Models
# ==========================================
class CredentialsUpdate(BaseModel):
    gmail_client_id: Optional[str] = None
    gmail_client_secret: Optional[str] = None  # Plain text — will be encrypted
    gmail_refresh_token: Optional[str] = None   # Plain text — will be encrypted
    sender_email: Optional[str] = None
    cc_emails: Optional[str] = None


class TemplateUpdate(BaseModel):
    email_subject: Optional[str] = None
    email_template_type: Optional[str] = "text"
    email_template: Optional[str] = None        # Text template
    email_template_html: Optional[str] = None   # HTML template (stored separately)


class TestEmailRequest(BaseModel):
    recipient_email: str


class CompanyEmail(BaseModel):
    agreement_id: int
    email: str
    payment_ids: List[int] = []


class SendEmailRequest(BaseModel):
    companies: List[CompanyEmail]


# ==========================================
# GET /api/email/settings — Get current settings (credentials masked)
# ==========================================
@router.get("/settings")
def get_email_settings(
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    cursor = db.cursor()
    settings = cursor.execute("SELECT * FROM email_settings LIMIT 1").fetchone()

    if not settings:
        return {
            "settings": {
                "gmail_client_id": "",
                "gmail_client_secret": "",
                "gmail_refresh_token": "",
                "sender_email": "",
                "cc_emails": "",
                "email_subject": "Payment Reminder — {{company_name}}",
                "email_template_type": "text",
                "email_template": DEFAULT_EMAIL_TEMPLATE,
                "email_template_html": "",
                "has_credentials": False
            }
        }

    s = dict(settings)

    # Mask sensitive fields — show only last 4 chars
    client_secret = decrypt_value(s.get("gmail_client_secret_encrypted") or "")
    refresh_token = decrypt_value(s.get("gmail_refresh_token_encrypted") or "")

    return {
        "settings": {
            "gmail_client_id": s.get("gmail_client_id") or "",
            "gmail_client_secret": ("•" * 20 + client_secret[-4:]) if len(client_secret) > 4 else "",
            "gmail_refresh_token": ("•" * 20 + refresh_token[-4:]) if len(refresh_token) > 4 else "",
            "sender_email": s.get("sender_email") or "",
            "cc_emails": s.get("cc_emails") or "",
            "email_subject": s.get("email_subject") or "Payment Reminder — {{company_name}}",
            "email_template_type": s.get("email_template_type") or "text",
            "email_template": s.get("email_template") or DEFAULT_EMAIL_TEMPLATE,
            "email_template_html": s.get("email_template_html") or "",
            "has_credentials": bool(client_secret and refresh_token)
        }
    }


# ==========================================
# PUT /api/email/credentials — Save credentials only
# ==========================================
@router.put("/credentials")
def update_credentials(
    data: CredentialsUpdate,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    cursor = db.cursor()
    existing = cursor.execute("SELECT * FROM email_settings LIMIT 1").fetchone()

    # Encrypt sensitive fields only if new values are provided (not masked)
    encrypted_secret = ""
    encrypted_token = ""

    if data.gmail_client_secret and not data.gmail_client_secret.startswith("•"):
        encrypted_secret = encrypt_value(data.gmail_client_secret)
    elif existing:
        encrypted_secret = dict(existing).get("gmail_client_secret_encrypted") or ""

    if data.gmail_refresh_token and not data.gmail_refresh_token.startswith("•"):
        encrypted_token = encrypt_value(data.gmail_refresh_token)
    elif existing:
        encrypted_token = dict(existing).get("gmail_refresh_token_encrypted") or ""

    if existing:
        cursor.execute("""
            UPDATE email_settings SET
                gmail_client_id = ?,
                gmail_client_secret_encrypted = ?,
                gmail_refresh_token_encrypted = ?,
                sender_email = ?,
                cc_emails = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            data.gmail_client_id or "",
            encrypted_secret,
            encrypted_token,
            data.sender_email or "",
            data.cc_emails or "",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            existing["id"]
        ))
    else:
        cursor.execute("""
            INSERT INTO email_settings (
                gmail_client_id, gmail_client_secret_encrypted, gmail_refresh_token_encrypted,
                sender_email, cc_emails, email_subject, email_template_type, email_template,
                email_template_html, is_enabled, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.gmail_client_id or "",
            encrypted_secret,
            encrypted_token,
            data.sender_email or "",
            data.cc_emails or "",
            "Payment Reminder — {{company_name}}",
            "text",
            DEFAULT_EMAIL_TEMPLATE,
            "",
            1,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

    db.commit()
    return {"message": "Credentials saved successfully"}


# ==========================================
# PUT /api/email/template — Save default email template only
# ==========================================
@router.put("/template")
def update_template(
    data: TemplateUpdate,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    cursor = db.cursor()
    existing = cursor.execute("SELECT * FROM email_settings LIMIT 1").fetchone()

    if existing:
        ex = dict(existing)
        cursor.execute("""
            UPDATE email_settings SET
                email_subject = ?,
                email_template_type = ?,
                email_template = ?,
                email_template_html = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            data.email_subject or "Payment Reminder — {{company_name}}",
            data.email_template_type or "text",
            data.email_template if data.email_template is not None else (ex.get("email_template") or DEFAULT_EMAIL_TEMPLATE),
            data.email_template_html if data.email_template_html is not None else (ex.get("email_template_html") or ""),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            existing["id"]
        ))
    else:
        cursor.execute("""
            INSERT INTO email_settings (
                gmail_client_id, gmail_client_secret_encrypted, gmail_refresh_token_encrypted,
                sender_email, cc_emails, email_subject, email_template_type, email_template,
                email_template_html, is_enabled, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "", "", "", "", "",
            data.email_subject or "Payment Reminder — {{company_name}}",
            data.email_template_type or "text",
            data.email_template or DEFAULT_EMAIL_TEMPLATE,
            data.email_template_html or "",
            1,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

    db.commit()
    return {"message": "Default email template saved successfully"}


# ==========================================
# GET /api/email/companies — List companies with pending payments within 60 days
# ==========================================
@router.get("/companies")
def get_companies_for_email(
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    """
    Fetch companies with pending payments due within the next 60 days.
    Only includes non-expired agreements with pending payment status.
    Companies are sorted by nearest due date first.
    A company remains in the list until its payment is marked as paid.
    """
    cursor = db.cursor()
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    sixty_days_str = (now + timedelta(days=60)).strftime("%Y-%m-%d")

    rows = cursor.execute("""
        SELECT
            p.id AS payment_id,
            p.agreement_id,
            p.due_date,
            p.amount AS payment_amount,
            p.status AS payment_status,
            aa.company_name,
            aa.email,
            aa.contact_person,
            aa.currency,
            aa.agreement_title
        FROM payments p
        JOIN agreements a ON p.agreement_id = a.id
        LEFT JOIN agreement_analysis aa ON a.id = aa.agreement_id
        WHERE p.status = 'pending'
          AND a.status != 'expired'
          AND p.due_date BETWEEN ? AND ?
        ORDER BY p.due_date ASC
    """, (today_str, sixty_days_str)).fetchall()

    # Group payments by agreement and compute totals
    companies_map = {}
    for row in rows:
        r = dict(row)
        aid = r["agreement_id"]
        amount = r["payment_amount"] or 0

        if aid not in companies_map:
            companies_map[aid] = {
                "agreement_id": aid,
                "company_name": r.get("company_name") or "Unknown",
                "email": r.get("email") or "",
                "contact_person": r.get("contact_person") or "",
                "currency": r.get("currency") or "₹",
                "agreement_title": r.get("agreement_title") or "Agreement",
                "payments": [],
                "total_amount": 0,
                "nearest_due_date": r["due_date"],
            }

        # Check if a confirmed proforma exists for this payment (ignore drafts — they are temporary)
        proforma = cursor.execute(
            "SELECT id, status FROM proforma_invoices WHERE payment_id = ? AND status = 'confirmed' ORDER BY id DESC LIMIT 1",
            (r["payment_id"],)
        ).fetchone()
        proforma_status = None
        proforma_id = None
        if proforma:
            proforma_status = dict(proforma)["status"]
            proforma_id = dict(proforma)["id"]

        companies_map[aid]["payments"].append({
            "payment_id": r["payment_id"],
            "due_date": r["due_date"],
            "amount": amount,
            "proforma_status": proforma_status,
            "proforma_id": proforma_id,
        })
        companies_map[aid]["total_amount"] += amount

        # Update nearest due date (should already be nearest due to ORDER BY, but ensure)
        if r["due_date"] < companies_map[aid]["nearest_due_date"]:
            companies_map[aid]["nearest_due_date"] = r["due_date"]

    # Convert to list and sort by nearest due date
    companies = sorted(companies_map.values(), key=lambda c: c["nearest_due_date"])

    return {"companies": companies}


# ==========================================
# POST /api/email/send — Manually send emails to selected companies
# ==========================================
@router.post("/send")
def send_emails_to_companies(
    data: SendEmailRequest,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    """
    Send payment reminder emails to selected companies.
    Uses saved credentials and default email template.
    Saves updated email addresses back to agreement_analysis.
    """
    if not data.companies:
        raise HTTPException(status_code=400, detail="No companies selected")

    cursor = db.cursor()
    settings = cursor.execute("SELECT * FROM email_settings LIMIT 1").fetchone()

    if not settings:
        raise HTTPException(status_code=400, detail="Email settings not configured. Please save credentials first.")

    s = dict(settings)

    # Decrypt credentials
    client_id = s.get("gmail_client_id") or ""
    client_secret = decrypt_value(s.get("gmail_client_secret_encrypted") or "")
    refresh_token = decrypt_value(s.get("gmail_refresh_token_encrypted") or "")
    sender_email = s.get("sender_email") or ""

    if not all([client_id, client_secret, refresh_token, sender_email]):
        raise HTTPException(status_code=400, detail="Gmail credentials are incomplete. Please fill in all credential fields.")

    # Get access token
    try:
        access_token = get_access_token(client_id, client_secret, refresh_token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Load template
    template_type = s.get("email_template_type") or "text"
    is_html = template_type == "html"

    if is_html:
        template = s.get("email_template_html") or s.get("email_template") or DEFAULT_EMAIL_TEMPLATE
    else:
        template = s.get("email_template") or DEFAULT_EMAIL_TEMPLATE
        # Auto-detect HTML content from the visual rich text editor
        if template and ('<' in template and '>' in template):
            is_html = True

    cc_emails = s.get("cc_emails") or ""
    subject_template = s.get("email_subject") or "Payment Reminder — {{company_name}}"

    stats = {"sent": 0, "failed": 0, "errors": []}

    for company in data.companies:
        recipient_email = (company.email or "").strip()
        if not recipient_email:
            stats["failed"] += 1
            stats["errors"].append(f"No email address for agreement #{company.agreement_id}")
            continue

        # Save the email back to agreement_analysis if it was added/changed by admin
        cursor.execute(
            "UPDATE agreement_analysis SET email = ? WHERE agreement_id = ?",
            (recipient_email, company.agreement_id)
        )

        # Get agreement/payment data for template variables
        agreement_data = cursor.execute("""
            SELECT aa.company_name, aa.contact_person, aa.currency, aa.agreement_title
            FROM agreement_analysis aa
            WHERE aa.agreement_id = ?
        """, (company.agreement_id,)).fetchone()

        if not agreement_data:
            stats["failed"] += 1
            stats["errors"].append(f"Agreement #{company.agreement_id} analysis not found")
            continue

        ad = dict(agreement_data)

        # Get payment details for this company
        payment_ids = company.payment_ids
        if payment_ids:
            placeholders = ",".join(["?"] * len(payment_ids))
            payments = cursor.execute(
                f"SELECT * FROM payments WHERE id IN ({placeholders}) AND status = 'pending'",
                payment_ids
            ).fetchall()
        else:
            # If no specific payment IDs, get all pending payments for this agreement within 60 days
            now = datetime.now()
            payments = cursor.execute("""
                SELECT * FROM payments
                WHERE agreement_id = ? AND status = 'pending'
                  AND due_date BETWEEN ? AND ?
                ORDER BY due_date ASC
            """, (
                company.agreement_id,
                now.strftime("%Y-%m-%d"),
                (now + timedelta(days=60)).strftime("%Y-%m-%d")
            )).fetchall()

        if not payments:
            stats["failed"] += 1
            stats["errors"].append(f"No pending payments found for {ad.get('company_name', 'Unknown')}")
            continue

        # Check for confirmed proforma invoices for ALL selected payments
        attachments = []
        proforma_paths = []
        missing_proforma = False
        for p in payments:
            p_id = dict(p)["id"]
            proforma = cursor.execute(
                "SELECT file_path, invoice_no FROM proforma_invoices WHERE payment_id = ? AND status = 'confirmed' ORDER BY id DESC LIMIT 1",
                (p_id,)
            ).fetchone()
            if not proforma:
                missing_proforma = True
                break
            pf = dict(proforma)
            if pf["file_path"] and os.path.exists(pf["file_path"]):
                invoice_no = pf.get("invoice_no") or "ProForma"
                attachments.append({
                    "filepath": pf["file_path"],
                    "filename": f"ProForma_Invoice_{invoice_no.replace('/', '_')}.pdf",
                })
                proforma_paths.append(pf["file_path"])

        if missing_proforma:
            stats["failed"] += 1
            stats["errors"].append(f"Pro Forma Invoice not confirmed for {ad.get('company_name', 'Unknown')}. Please generate and confirm the invoice first.")
            continue

        # Use the nearest pending payment for template variables
        nearest_payment = dict(payments[0])

        # Calculate total amount across all pending payments (base amount)
        total_amount = sum(dict(p)["amount"] for p in payments)

        # Calculate days remaining for nearest payment
        try:
            due_dt = datetime.strptime(nearest_payment["due_date"], "%Y-%m-%d")
            days_remaining = (due_dt - datetime.now()).days
        except (ValueError, TypeError):
            days_remaining = 0

        # Build template variables
        variables = {
            "company_name": ad.get("company_name") or "Valued Client",
            "payment_amount": f"{total_amount:,.2f}",
            "payment_due_date": nearest_payment.get("due_date") or "",
            "days_remaining": str(max(0, days_remaining)),
            "currency": ad.get("currency") or "₹",
            "agreement_title": ad.get("agreement_title") or "Consulting Agreement",
            "contact_person": ad.get("contact_person") or "Sir/Madam",
        }

        # Render email body and subject
        email_body = render_template(template, variables)
        email_subject = render_template(subject_template, variables)

        # Generate tracking ID and inject tracking pixel
        tracking_id = str(uuid.uuid4())
        email_body_with_pixel = _inject_tracking_pixel(email_body, tracking_id, is_html)

        # Send email with proforma attachment (always HTML now due to tracking pixel)
        result = send_email(
            sender=sender_email,
            to=recipient_email,
            subject=email_subject,
            body=email_body_with_pixel,
            cc=cc_emails if cc_emails.strip() else None,
            is_html=True,
            access_token=access_token,
            attachments=attachments if attachments else None,
        )

        # Log a single entry for this email (not per-payment)
        proforma_path_str = proforma_paths[0] if proforma_paths else None
        cursor.execute("""
            INSERT INTO email_log (payment_id, agreement_id, recipient_email, subject, status, error_message, proforma_invoice_path, tracking_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            dict(payments[0])["id"], company.agreement_id, recipient_email,
            email_subject, result["status"], result.get("error"), proforma_path_str, tracking_id
        ))

        db.commit()

        if result["status"] == "sent":
            stats["sent"] += 1
        else:
            stats["failed"] += 1
            stats["errors"].append(f"Failed to send to {ad.get('company_name', 'Unknown')}: {result.get('error', 'Unknown error')}")

    return {
        "message": f"Emails sent: {stats['sent']}, Failed: {stats['failed']}",
        "stats": stats
    }


# ==========================================
# POST /api/email/test — Send test email
# ==========================================
@router.post("/test")
def send_test_email(
    data: TestEmailRequest,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    cursor = db.cursor()
    settings = cursor.execute("SELECT * FROM email_settings LIMIT 1").fetchone()

    if not settings:
        raise HTTPException(status_code=400, detail="Email settings not configured")

    s = dict(settings)

    # Decrypt credentials
    client_id = s.get("gmail_client_id") or ""
    client_secret = decrypt_value(s.get("gmail_client_secret_encrypted") or "")
    refresh_token = decrypt_value(s.get("gmail_refresh_token_encrypted") or "")
    sender_email = s.get("sender_email") or ""

    if not all([client_id, client_secret, refresh_token, sender_email]):
        raise HTTPException(status_code=400, detail="Gmail credentials are incomplete. Please fill in all fields.")

    # Get access token
    try:
        access_token = get_access_token(client_id, client_secret, refresh_token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Build test email — re-read settings from DB to ensure latest saved template is used
    settings_fresh = cursor.execute("SELECT * FROM email_settings LIMIT 1").fetchone()
    sf = dict(settings_fresh) if settings_fresh else s
    template_type = sf.get("email_template_type") or "text"
    is_html = template_type == "html"

    # Select the correct template based on type
    if is_html:
        template = sf.get("email_template_html") or sf.get("email_template") or DEFAULT_EMAIL_TEMPLATE
    else:
        template = sf.get("email_template") or DEFAULT_EMAIL_TEMPLATE
        # Auto-detect HTML content from the visual rich text editor
        if template and ('<' in template and '>' in template):
            is_html = True

    subject_template = sf.get("email_subject") or "Payment Reminder — {{company_name}}"

    variables = {
        "company_name": "Test Company Pvt. Ltd.",
        "payment_amount": "50,000.00",
        "payment_due_date": "2026-07-15",
        "days_remaining": "7",
        "currency": "₹",
        "agreement_title": "Test Consulting Agreement",
        "contact_person": "Mr. Test User",
    }

    body = render_template(template, variables)
    subject = render_template(subject_template, variables)

    cc = sf.get("cc_emails") or ""

    result = send_email(
        sender=sender_email,
        to=data.recipient_email.strip(),
        subject=f"[TEST] {subject}",
        body=body,
        cc=cc if cc.strip() else None,
        is_html=is_html,
        access_token=access_token
    )

    if result["status"] == "sent":
        return {"message": f"Test email sent successfully to {data.recipient_email}"}
    else:
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to send test email"))


# ==========================================
# GET /api/email/track/{tracking_id}.png — Email open tracking pixel (PUBLIC, no auth)
# ==========================================
@router.get("/track/{tracking_id}.png")
def track_email_open(
    tracking_id: str,
    db=Depends(get_db),
):
    """Public endpoint — called by email clients when loading images.
    Records the first open time and returns a 1x1 transparent PNG."""
    cursor = db.cursor()
    log_entry = cursor.execute(
        "SELECT id, opened_at FROM email_log WHERE tracking_id = ?",
        (tracking_id,)
    ).fetchone()

    if log_entry:
        entry = dict(log_entry)
        if not entry.get("opened_at"):
            cursor.execute(
                "UPDATE email_log SET opened_at = ? WHERE id = ?",
                (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), entry["id"])
            )
            db.commit()

    return Response(
        content=_TRACKING_PIXEL,
        media_type="image/png",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


# ==========================================
# GET /api/email/logs — Get CLIENT email send history
# ==========================================
@router.get("/logs")
def get_email_logs(
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    cursor = db.cursor()

    # Auto-cleanup: delete CLIENT email logs older than 30 days from sent_at timestamp.
    # IMPORTANT: This cleanup is strictly based on when the email was SENT (sent_at),
    # NOT on the payment due_date. Email history must persist even after the payment
    # due date passes — it only gets removed 30 days after the email was sent,
    # or when manually deleted by the admin.
    thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    old_logs = cursor.execute(
        "SELECT proforma_invoice_path FROM email_log WHERE sent_at < ? AND email_type = 'client' AND proforma_invoice_path IS NOT NULL",
        (thirty_days_ago,)
    ).fetchall()
    for ol in old_logs:
        path = dict(ol).get("proforma_invoice_path")
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    cursor.execute("DELETE FROM email_log WHERE sent_at < ? AND email_type = 'client'", (thirty_days_ago,))
    db.commit()

    logs = cursor.execute("""
        SELECT el.*, aa.company_name
        FROM email_log el
        LEFT JOIN agreement_analysis aa ON el.agreement_id = aa.agreement_id
        WHERE el.email_type = 'client'
        ORDER BY el.sent_at DESC
        LIMIT 100
    """).fetchall()

    result_logs = []
    for log in logs:
        d = dict(log)
        # Check if the proforma PDF file still exists on disk
        pf_path = d.get("proforma_invoice_path")
        if pf_path:
            if os.path.exists(pf_path):
                d["proforma_deleted"] = False
            else:
                d["proforma_invoice_path"] = None
                d["proforma_deleted"] = True
        else:
            d["proforma_deleted"] = False
        # Email open status
        d["email_opened"] = d.get("opened_at") is not None
        result_logs.append(d)

    return {
        "logs": result_logs
    }


# ==========================================
# DELETE /api/email/logs — Clear all CLIENT email logs
# ==========================================
@router.delete("/logs")
def clear_all_email_logs(
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    cursor = db.cursor()
    # Clean up associated proforma PDFs (client logs only)
    logs_with_proforma = cursor.execute(
        "SELECT proforma_invoice_path FROM email_log WHERE email_type = 'client' AND proforma_invoice_path IS NOT NULL"
    ).fetchall()
    for log in logs_with_proforma:
        path = dict(log).get("proforma_invoice_path")
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    cursor.execute("DELETE FROM email_log WHERE email_type = 'client'")
    db.commit()
    return {"message": "All client email logs cleared successfully"}


# ==========================================
# DELETE /api/email/logs/{log_id} — Delete a single CLIENT email log
# ==========================================
@router.delete("/logs/{log_id}")
def delete_email_log(
    log_id: int,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    cursor = db.cursor()
    existing = cursor.execute("SELECT id, proforma_invoice_path FROM email_log WHERE id = ? AND email_type = 'client'", (log_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Email log entry not found")

    # Clean up associated proforma PDF
    path = dict(existing).get("proforma_invoice_path")
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass

    cursor.execute("DELETE FROM email_log WHERE id = ?", (log_id,))
    db.commit()
    return {"message": "Email log entry deleted successfully"}


# ==========================================
# GET /api/email/consultant-logs — Get CONSULTANT email send history
# ==========================================
@router.get("/consultant-logs")
def get_consultant_email_logs(
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    cursor = db.cursor()

    # Auto-cleanup: delete CONSULTANT email logs older than 30 days from sent_at
    thirty_days_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("DELETE FROM email_log WHERE sent_at < ? AND email_type = 'consultant'", (thirty_days_ago,))
    db.commit()

    logs = cursor.execute("""
        SELECT el.*, aa.company_name
        FROM email_log el
        LEFT JOIN agreement_analysis aa ON el.agreement_id = aa.agreement_id
        WHERE el.email_type = 'consultant'
        ORDER BY el.sent_at DESC
        LIMIT 100
    """).fetchall()

    result_logs = []
    for log in logs:
        d = dict(log)
        # Email open status
        d["email_opened"] = d.get("opened_at") is not None
        # Try to resolve consultant name from recipient email
        consultant = cursor.execute(
            "SELECT name FROM consultants WHERE email = ? LIMIT 1",
            (d.get("recipient_email") or "",)
        ).fetchone()
        d["consultant_name"] = dict(consultant)["name"] if consultant else (d.get("recipient_email") or "—")
        result_logs.append(d)

    return {
        "logs": result_logs
    }


# ==========================================
# DELETE /api/email/consultant-logs — Clear all CONSULTANT email logs
# ==========================================
@router.delete("/consultant-logs")
def clear_all_consultant_email_logs(
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    cursor = db.cursor()
    cursor.execute("DELETE FROM email_log WHERE email_type = 'consultant'")
    db.commit()
    return {"message": "All consultant email logs cleared successfully"}


# ==========================================
# DELETE /api/email/consultant-logs/{log_id} — Delete single CONSULTANT email log
# ==========================================
@router.delete("/consultant-logs/{log_id}")
def delete_consultant_email_log(
    log_id: int,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    cursor = db.cursor()
    existing = cursor.execute("SELECT id FROM email_log WHERE id = ? AND email_type = 'consultant'", (log_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Consultant email log entry not found")

    cursor.execute("DELETE FROM email_log WHERE id = ?", (log_id,))
    db.commit()
    return {"message": "Consultant email log entry deleted successfully"}


# ==========================================
# Consultant Email Template Request Model
# ==========================================
class ConsultantTemplateUpdate(BaseModel):
    consultant_email_subject: Optional[str] = None
    consultant_email_template_type: Optional[str] = "text"
    consultant_email_template: Optional[str] = None
    consultant_email_template_html: Optional[str] = None


# ==========================================
# GET /api/email/consultant-template — Get consultant email template
# ==========================================
@router.get("/consultant-template")
def get_consultant_template(
    current_user: dict = Depends(require_admin),
    db=Depends(get_db),
):
    cursor = db.cursor()
    settings = cursor.execute("SELECT * FROM email_settings LIMIT 1").fetchone()

    if not settings:
        return {
            "template": {
                "consultant_email_subject": "Payment Reminder — {{company_name}} (Internal)",
                "consultant_email_template_type": "text",
                "consultant_email_template": DEFAULT_CONSULTANT_EMAIL_TEMPLATE,
                "consultant_email_template_html": "",
            }
        }

    s = dict(settings)
    return {
        "template": {
            "consultant_email_subject": s.get("consultant_email_subject") or "Payment Reminder — {{company_name}} (Internal)",
            "consultant_email_template_type": s.get("consultant_email_template_type") or "text",
            "consultant_email_template": s.get("consultant_email_template") or DEFAULT_CONSULTANT_EMAIL_TEMPLATE,
            "consultant_email_template_html": s.get("consultant_email_template_html") or "",
        }
    }


# ==========================================
# PUT /api/email/consultant-template — Save consultant email template
# ==========================================
@router.put("/consultant-template")
def update_consultant_template(
    data: ConsultantTemplateUpdate,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db),
):
    cursor = db.cursor()
    existing = cursor.execute("SELECT * FROM email_settings LIMIT 1").fetchone()

    if existing:
        ex = dict(existing)
        cursor.execute("""
            UPDATE email_settings SET
                consultant_email_subject = ?,
                consultant_email_template_type = ?,
                consultant_email_template = ?,
                consultant_email_template_html = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            data.consultant_email_subject or "Payment Reminder — {{company_name}} (Internal)",
            data.consultant_email_template_type or "text",
            data.consultant_email_template if data.consultant_email_template is not None else (ex.get("consultant_email_template") or DEFAULT_CONSULTANT_EMAIL_TEMPLATE),
            data.consultant_email_template_html if data.consultant_email_template_html is not None else (ex.get("consultant_email_template_html") or ""),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            existing["id"],
        ))
    else:
        cursor.execute("""
            INSERT INTO email_settings (
                gmail_client_id, gmail_client_secret_encrypted, gmail_refresh_token_encrypted,
                sender_email, cc_emails, email_subject, email_template_type, email_template,
                email_template_html, is_enabled,
                consultant_email_subject, consultant_email_template_type,
                consultant_email_template, consultant_email_template_html,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "", "", "", "", "",
            "Payment Reminder — {{company_name}}",
            "text",
            "",
            "",
            1,
            data.consultant_email_subject or "Payment Reminder — {{company_name}} (Internal)",
            data.consultant_email_template_type or "text",
            data.consultant_email_template or DEFAULT_CONSULTANT_EMAIL_TEMPLATE,
            data.consultant_email_template_html or "",
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

    db.commit()
    return {"message": "Consultant email template saved successfully"}


# ==========================================
# Request Model for Consultant Email Send
# ==========================================
class ConsultantCompanyEmail(BaseModel):
    agreement_id: int
    payment_ids: List[int] = []


class ConsultantSendEmailRequest(BaseModel):
    companies: List[ConsultantCompanyEmail]


# ==========================================
# GET /api/email/consultant-companies — List companies with pending payments
# that have assigned consultants (for manual send)
# ==========================================
@router.get("/consultant-companies")
def get_consultant_companies_for_email(
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    """
    Fetch agreements with pending payments due within the next 60 days
    that have active consultants assigned. For manual consultant email sending.
    """
    cursor = db.cursor()
    now = datetime.now()
    today_str = now.strftime("%Y-%m-%d")
    sixty_days_str = (now + timedelta(days=60)).strftime("%Y-%m-%d")

    rows = cursor.execute("""
        SELECT
            p.id AS payment_id,
            p.agreement_id,
            p.due_date,
            p.amount AS payment_amount,
            p.status AS payment_status,
            aa.company_name,
            aa.contact_person,
            aa.currency,
            aa.agreement_title
        FROM payments p
        JOIN agreements a ON p.agreement_id = a.id
        LEFT JOIN agreement_analysis aa ON a.id = aa.agreement_id
        WHERE p.status = 'pending'
          AND a.status != 'expired'
          AND p.due_date BETWEEN ? AND ?
        ORDER BY p.due_date ASC
    """, (today_str, sixty_days_str)).fetchall()

    # Group payments by agreement and compute totals
    companies_map = {}
    for row in rows:
        r = dict(row)
        aid = r["agreement_id"]
        amount = r["payment_amount"] or 0

        if aid not in companies_map:
            companies_map[aid] = {
                "agreement_id": aid,
                "company_name": r.get("company_name") or "Unknown",
                "contact_person": r.get("contact_person") or "",
                "currency": r.get("currency") or "₹",
                "agreement_title": r.get("agreement_title") or "Agreement",
                "payments": [],
                "total_amount": 0,
                "nearest_due_date": r["due_date"],
                "consultants": [],
            }

        companies_map[aid]["payments"].append({
            "payment_id": r["payment_id"],
            "due_date": r["due_date"],
            "amount": amount,
        })
        companies_map[aid]["total_amount"] += amount

        if r["due_date"] < companies_map[aid]["nearest_due_date"]:
            companies_map[aid]["nearest_due_date"] = r["due_date"]

    # For each agreement, fetch assigned active consultants
    # Remove agreements that have no consultants assigned
    agreements_to_remove = []
    for aid, company in companies_map.items():
        consultants = cursor.execute("""
            SELECT c.id, c.name, c.email
            FROM agreement_consultants ac
            JOIN consultants c ON ac.consultant_id = c.id
            WHERE ac.agreement_id = ? AND c.is_active = 1
        """, (aid,)).fetchall()

        if not consultants:
            agreements_to_remove.append(aid)
            continue

        company["consultants"] = [
            {"id": dict(c)["id"], "name": dict(c)["name"], "email": dict(c)["email"]}
            for c in consultants
        ]

    for aid in agreements_to_remove:
        del companies_map[aid]

    # Convert to list and sort by nearest due date
    companies = sorted(companies_map.values(), key=lambda c: c["nearest_due_date"])

    return {"companies": companies}


# ==========================================
# POST /api/email/consultant-send — Manually send emails to consultants
# ==========================================
@router.post("/consultant-send")
def send_emails_to_consultants(
    data: ConsultantSendEmailRequest,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db)
):
    """
    Send payment reminder emails to assigned consultants for selected agreements.
    Uses the consultant email template. Does NOT send to admin users.
    """
    if not data.companies:
        raise HTTPException(status_code=400, detail="No companies selected")

    cursor = db.cursor()
    settings = cursor.execute("SELECT * FROM email_settings LIMIT 1").fetchone()

    if not settings:
        raise HTTPException(status_code=400, detail="Email settings not configured. Please save credentials first.")

    s = dict(settings)

    # Decrypt credentials
    client_id = s.get("gmail_client_id") or ""
    client_secret = decrypt_value(s.get("gmail_client_secret_encrypted") or "")
    refresh_token = decrypt_value(s.get("gmail_refresh_token_encrypted") or "")
    sender_email = s.get("sender_email") or ""

    if not all([client_id, client_secret, refresh_token, sender_email]):
        raise HTTPException(status_code=400, detail="Gmail credentials are incomplete. Please fill in all credential fields.")

    # Get access token
    try:
        access_token = get_access_token(client_id, client_secret, refresh_token)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Load consultant email template
    template_type = s.get("consultant_email_template_type") or "text"
    is_html = template_type == "html"

    if is_html:
        template = (
            s.get("consultant_email_template_html")
            or s.get("consultant_email_template")
            or DEFAULT_CONSULTANT_EMAIL_TEMPLATE
        )
    else:
        template = s.get("consultant_email_template") or DEFAULT_CONSULTANT_EMAIL_TEMPLATE
        # Auto-detect HTML content
        if template and ('<' in template and '>' in template):
            is_html = True

    subject_template = (
        s.get("consultant_email_subject")
        or "Payment Reminder — {{company_name}} (Internal)"
    )

    stats = {"sent": 0, "failed": 0, "errors": []}

    for company in data.companies:
        agreement_id = company.agreement_id

        # Get agreement/payment data for template variables
        agreement_data = cursor.execute("""
            SELECT aa.company_name, aa.contact_person, aa.currency, aa.agreement_title
            FROM agreement_analysis aa
            WHERE aa.agreement_id = ?
        """, (agreement_id,)).fetchone()

        if not agreement_data:
            stats["failed"] += 1
            stats["errors"].append(f"Agreement #{agreement_id} analysis not found")
            continue

        ad = dict(agreement_data)

        # Get payment details
        payment_ids = company.payment_ids
        if payment_ids:
            placeholders = ",".join(["?"] * len(payment_ids))
            payments = cursor.execute(
                f"SELECT * FROM payments WHERE id IN ({placeholders}) AND status = 'pending'",
                payment_ids
            ).fetchall()
        else:
            now = datetime.now()
            payments = cursor.execute("""
                SELECT * FROM payments
                WHERE agreement_id = ? AND status = 'pending'
                  AND due_date BETWEEN ? AND ?
                ORDER BY due_date ASC
            """, (
                agreement_id,
                now.strftime("%Y-%m-%d"),
                (now + timedelta(days=60)).strftime("%Y-%m-%d")
            )).fetchall()

        if not payments:
            stats["failed"] += 1
            stats["errors"].append(f"No pending payments found for {ad.get('company_name', 'Unknown')}")
            continue

        # Use the nearest pending payment for template variables
        nearest_payment = dict(payments[0])

        # Calculate total amount across all pending payments (base amount)
        total_amount = sum(dict(p)["amount"] for p in payments)

        # Calculate days remaining for nearest payment
        try:
            due_dt = datetime.strptime(nearest_payment["due_date"], "%Y-%m-%d")
            days_remaining = (due_dt - datetime.now()).days
        except (ValueError, TypeError):
            days_remaining = 0

        # Build base template variables
        base_variables = {
            "company_name": ad.get("company_name") or "Unknown",
            "payment_amount": f"{total_amount:,.2f}",
            "payment_due_date": nearest_payment.get("due_date") or "",
            "days_remaining": str(max(0, days_remaining)),
            "currency": ad.get("currency") or "₹",
            "agreement_title": ad.get("agreement_title") or "Consulting Agreement",
            "contact_person": ad.get("contact_person") or "N/A",
        }

        # Get assigned consultants for this agreement
        consultants = cursor.execute("""
            SELECT c.id, c.name, c.email
            FROM agreement_consultants ac
            JOIN consultants c ON ac.consultant_id = c.id
            WHERE ac.agreement_id = ? AND c.is_active = 1
        """, (agreement_id,)).fetchall()

        if not consultants:
            stats["failed"] += 1
            stats["errors"].append(f"No active consultants assigned to {ad.get('company_name', 'Unknown')}")
            continue

        # Send to each consultant
        for consultant_row in consultants:
            c = dict(consultant_row)
            recipient_email = (c.get("email") or "").strip()
            if not recipient_email:
                continue

            # Personalize template with consultant name
            variables = {**base_variables, "consultant_name": c["name"]}
            email_body = render_template(template, variables)
            email_subject = render_template(subject_template, variables)

            # Generate tracking ID and inject tracking pixel
            tracking_id = str(uuid.uuid4())
            email_body_with_pixel = _inject_tracking_pixel(email_body, tracking_id, is_html)

            # Send email (no CC for consultant reminders, always HTML due to pixel)
            result = send_email(
                sender=sender_email,
                to=recipient_email,
                subject=email_subject,
                body=email_body_with_pixel,
                is_html=True,
                access_token=access_token,
            )

            # Log the email
            cursor.execute("""
                INSERT INTO email_log
                    (payment_id, agreement_id, recipient_email, subject, status, error_message, email_type, tracking_id)
                VALUES (?, ?, ?, ?, ?, ?, 'consultant', ?)
            """, (
                dict(payments[0])["id"],
                agreement_id,
                recipient_email,
                email_subject,
                result["status"],
                result.get("error"),
                tracking_id,
            ))
            db.commit()

            if result["status"] == "sent":
                stats["sent"] += 1
            else:
                stats["failed"] += 1
                stats["errors"].append(f"Failed to send to {c['name']} ({recipient_email}): {result.get('error', 'Unknown error')}")

    return {
        "message": f"Emails sent: {stats['sent']}, Failed: {stats['failed']}",
        "stats": stats
    }


# ==========================================
# Pro Forma Invoice Endpoints
# ==========================================

class ProformaGenerateRequest(BaseModel):
    agreement_id: int
    payment_id: int
    invoice_no: Optional[str] = None
    date: Optional[str] = None
    mode_of_payment: Optional[str] = "ADVANCE"
    reference_no: Optional[str] = "-"
    reference_date: Optional[str] = "-"
    sales_person: Optional[str] = "-"
    buyer_name: Optional[str] = None
    state_code: Optional[str] = "24"
    state_name: Optional[str] = "Gujarat"
    city: Optional[str] = None
    area: Optional[str] = None
    buyer_gstin: Optional[str] = None
    description: Optional[str] = "Professional Fees - Time"
    sub_note: Optional[str] = None
    hsn_sac: Optional[str] = "998311"
    gst_rate: Optional[float] = 18
    quantity: Optional[float] = 1
    quantity_unit: Optional[str] = "Time"
    rate: Optional[float] = 0


def _get_fiscal_year_prefix():
    """Return the fiscal year prefix like '25-26' for the current date."""
    now = datetime.now()
    year = now.year
    month = now.month
    # Indian fiscal year starts in April
    if month >= 4:
        fy_start = year % 100  # e.g. 26
        fy_end = (year + 1) % 100  # e.g. 27
    else:
        fy_start = (year - 1) % 100
        fy_end = year % 100
    return f"{fy_start:02d}-{fy_end:02d}"


def _get_next_invoice_number(cursor):
    """Generate the next auto-incremented invoice number in YYYY-YY/XXXX format."""
    prefix = _get_fiscal_year_prefix()

    # Find the highest invoice number with this prefix
    rows = cursor.execute(
        "SELECT invoice_no FROM proforma_invoices WHERE invoice_no LIKE ? ORDER BY id DESC",
        (f"{prefix}/%",)
    ).fetchall()

    max_num = 0
    for row in rows:
        inv_no = dict(row).get("invoice_no") or ""
        if "/" in inv_no:
            try:
                num = int(inv_no.split("/")[-1])
                if num > max_num:
                    max_num = num
            except (ValueError, IndexError):
                pass

    next_num = max_num + 1
    return f"{prefix}/{next_num:04d}"


# ==========================================
# GET /api/email/proforma/next-invoice-no — Get auto-generated next invoice number
# ==========================================
@router.get("/proforma/next-invoice-no")
def get_next_invoice_no(
    current_user: dict = Depends(require_admin),
    db=Depends(get_db),
):
    cursor = db.cursor()
    invoice_no = _get_next_invoice_number(cursor)
    return {"invoice_no": invoice_no}


# ==========================================
# POST /api/email/proforma/generate — Generate a pro forma invoice PDF
# ==========================================
@router.post("/proforma/generate")
def generate_proforma(
    data: ProformaGenerateRequest,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db),
):
    cursor = db.cursor()

    # Validate agreement and payment exist
    payment = cursor.execute("SELECT id, amount FROM payments WHERE id = ?", (data.payment_id,)).fetchone()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    agreement = cursor.execute("SELECT id FROM agreements WHERE id = ?", (data.agreement_id,)).fetchone()
    if not agreement:
        raise HTTPException(status_code=404, detail="Agreement not found")

    # Delete any existing draft proforma for this payment
    existing_drafts = cursor.execute(
        "SELECT id, file_path FROM proforma_invoices WHERE payment_id = ? AND status = 'draft'",
        (data.payment_id,)
    ).fetchall()
    for draft in existing_drafts:
        d = dict(draft)
        if d["file_path"] and os.path.exists(d["file_path"]):
            try:
                os.remove(d["file_path"])
            except OSError:
                pass
        cursor.execute("DELETE FROM proforma_invoices WHERE id = ?", (d["id"],))

    # Auto-generate invoice number if not provided
    invoice_no = (data.invoice_no or "").strip()
    if not invoice_no:
        invoice_no = _get_next_invoice_number(cursor)

    # Use payment amount as rate if not provided
    rate = data.rate or 0
    if rate == 0:
        rate = dict(payment).get("amount") or 0

    # Build form data dict for PDF generation
    form_data = {
        "agreement_id": data.agreement_id,
        "payment_id": data.payment_id,
        "invoice_no": invoice_no,
        "date": data.date or datetime.now().strftime("%d-%b-%Y"),
        "mode_of_payment": data.mode_of_payment or "ADVANCE",
        "reference_no": data.reference_no or "-",
        "reference_date": data.reference_date or "-",
        "sales_person": data.sales_person or "-",
        "buyer_name": data.buyer_name or "Unknown",
        "state_code": data.state_code or "24",
        "state_name": data.state_name or "Gujarat",
        "city": data.city or "",
        "area": data.area or "",
        "buyer_gstin": data.buyer_gstin or "",
        "description": data.description or "Professional Fees - Time",
        "sub_note": data.sub_note or "",
        "hsn_sac": data.hsn_sac or "998311",
        "gst_rate": data.gst_rate or 18,
        "quantity": data.quantity or 1,
        "quantity_unit": data.quantity_unit or "Time",
        "rate": rate,
    }

    # Save original form_data before build_proforma_pdf mutates dates
    import copy
    form_data_for_db = copy.deepcopy(form_data)

    try:
        from backend.invoice_generator import build_proforma_pdf
        output_path = build_proforma_pdf(form_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate invoice PDF: {str(e)}")

    # Save to database (using original un-mutated form_data)
    cursor.execute("""
        INSERT INTO proforma_invoices (agreement_id, payment_id, invoice_no, file_path, form_data, status)
        VALUES (?, ?, ?, ?, ?, 'draft')
    """, (
        data.agreement_id, data.payment_id, invoice_no,
        output_path, json.dumps(form_data_for_db)
    ))
    db.commit()

    invoice_id = cursor.lastrowid

    return {
        "invoice_id": invoice_id,
        "invoice_no": invoice_no,
        "file_path": output_path,
        "preview_url": f"/api/email/proforma/{invoice_id}/preview",
    }


# ==========================================
# PUT /api/email/proforma/{invoice_id}/regenerate — Edit and regenerate invoice
# ==========================================
@router.put("/proforma/{invoice_id}/regenerate")
def regenerate_proforma(
    invoice_id: int,
    data: ProformaGenerateRequest,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db),
):
    cursor = db.cursor()

    existing = cursor.execute("SELECT * FROM proforma_invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Pro forma invoice not found")

    ex = dict(existing)

    # Delete old PDF
    if ex["file_path"] and os.path.exists(ex["file_path"]):
        try:
            os.remove(ex["file_path"])
        except OSError:
            pass

    # Use payment amount as rate if not provided
    rate = data.rate or 0
    if rate == 0:
        payment = cursor.execute("SELECT amount FROM payments WHERE id = ?", (data.payment_id,)).fetchone()
        if payment:
            rate = dict(payment).get("amount") or 0

    invoice_no = (data.invoice_no or "").strip() or ex.get("invoice_no") or _get_next_invoice_number(cursor)

    form_data = {
        "agreement_id": data.agreement_id,
        "payment_id": data.payment_id,
        "invoice_no": invoice_no,
        "date": data.date or datetime.now().strftime("%d-%b-%Y"),
        "mode_of_payment": data.mode_of_payment or "ADVANCE",
        "reference_no": data.reference_no or "-",
        "reference_date": data.reference_date or "-",
        "sales_person": data.sales_person or "-",
        "buyer_name": data.buyer_name or "Unknown",
        "state_code": data.state_code or "24",
        "state_name": data.state_name or "Gujarat",
        "city": data.city or "",
        "area": data.area or "",
        "buyer_gstin": data.buyer_gstin or "",
        "description": data.description or "Professional Fees - Time",
        "sub_note": data.sub_note or "",
        "hsn_sac": data.hsn_sac or "998311",
        "gst_rate": data.gst_rate or 18,
        "quantity": data.quantity or 1,
        "quantity_unit": data.quantity_unit or "Time",
        "rate": rate,
    }

    # Save original form_data before build_proforma_pdf mutates dates
    import copy
    form_data_for_db = copy.deepcopy(form_data)

    try:
        from backend.invoice_generator import build_proforma_pdf
        output_path = build_proforma_pdf(form_data)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to regenerate invoice PDF: {str(e)}")

    cursor.execute("""
        UPDATE proforma_invoices SET invoice_no = ?, file_path = ?, form_data = ?, status = 'draft', created_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (invoice_no, output_path, json.dumps(form_data_for_db), invoice_id))
    db.commit()

    return {
        "invoice_id": invoice_id,
        "invoice_no": invoice_no,
        "file_path": output_path,
        "preview_url": f"/api/email/proforma/{invoice_id}/preview",
    }


# ==========================================
# PUT /api/email/proforma/{invoice_id}/confirm — Confirm the invoice
# ==========================================
@router.put("/proforma/{invoice_id}/confirm")
def confirm_proforma(
    invoice_id: int,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db),
):
    cursor = db.cursor()
    existing = cursor.execute("SELECT id, status FROM proforma_invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Pro forma invoice not found")

    cursor.execute("UPDATE proforma_invoices SET status = 'confirmed' WHERE id = ?", (invoice_id,))
    db.commit()

    return {"message": "Pro forma invoice confirmed", "invoice_id": invoice_id}


# ==========================================
# DELETE /api/email/proforma/{invoice_id} — Delete draft invoice
# ==========================================
@router.delete("/proforma/{invoice_id}")
def delete_proforma(
    invoice_id: int,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db),
):
    cursor = db.cursor()
    existing = cursor.execute("SELECT id, file_path FROM proforma_invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Pro forma invoice not found")

    ex = dict(existing)
    if ex["file_path"] and os.path.exists(ex["file_path"]):
        try:
            os.remove(ex["file_path"])
        except OSError:
            pass

    cursor.execute("DELETE FROM proforma_invoices WHERE id = ?", (invoice_id,))
    db.commit()

    return {"message": "Pro forma invoice deleted"}


# ==========================================
# GET /api/email/proforma/{invoice_id}/preview — Serve the PDF file
# ==========================================
@router.get("/proforma/{invoice_id}/preview")
def preview_proforma(
    invoice_id: int,
    token: str = None,
    db=Depends(get_db),
):
    # Authenticate via query parameter token (iframes can't send headers)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication token required")
    payload = decode_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    cursor = db.cursor()
    user = cursor.execute("SELECT role, is_active FROM users WHERE id = ?", (int(user_id),)).fetchone()
    if not user or not user["is_active"] or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    existing = cursor.execute("SELECT file_path, invoice_no FROM proforma_invoices WHERE id = ?", (invoice_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Pro forma invoice not found")

    ex = dict(existing)
    if not ex["file_path"] or not os.path.exists(ex["file_path"]):
        raise HTTPException(status_code=404, detail="Invoice PDF file not found on disk")

    filename = f"ProForma_Invoice_{(ex.get('invoice_no') or 'draft').replace('/', '_')}.pdf"
    return FileResponse(
        ex["file_path"],
        media_type="application/pdf",
        filename=filename,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ==========================================
# GET /api/email/proforma/log-preview/{log_id} — Preview proforma from email log
# ==========================================
@router.get("/proforma/log-preview/{log_id}")
def preview_proforma_from_log(
    log_id: int,
    token: str = None,
    db=Depends(get_db),
):
    # Authenticate via query parameter token (iframes can't send headers)
    if not token:
        raise HTTPException(status_code=401, detail="Authentication token required")
    payload = decode_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    cursor = db.cursor()
    user = cursor.execute("SELECT role, is_active FROM users WHERE id = ?", (int(user_id),)).fetchone()
    if not user or not user["is_active"] or user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")

    log_entry = cursor.execute("SELECT proforma_invoice_path FROM email_log WHERE id = ?", (log_id,)).fetchone()
    if not log_entry:
        raise HTTPException(status_code=404, detail="Email log entry not found")

    path = dict(log_entry).get("proforma_invoice_path")
    if not path or not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Pro forma invoice PDF not found")

    filename = f"ProForma_Invoice_{log_id}.pdf"
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=filename,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


# ==========================================
# GET /api/email/proforma/{invoice_id}/form-data — Get stored form data for editing
# ==========================================
@router.get("/proforma/{invoice_id}/form-data")
def get_proforma_form_data(
    invoice_id: int,
    current_user: dict = Depends(require_admin),
    db=Depends(get_db),
):
    cursor = db.cursor()
    existing = cursor.execute(
        "SELECT form_data FROM proforma_invoices WHERE id = ?", (invoice_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Pro forma invoice not found")

    form_data_str = dict(existing).get("form_data") or "{}"
    try:
        form_data = json.loads(form_data_str)
    except (json.JSONDecodeError, TypeError):
        form_data = {}

    return {"form_data": form_data}
