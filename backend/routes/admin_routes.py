from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from typing import Optional
import os
import re
import shutil
import random
import time
import logging
from backend.auth import require_admin, hash_password
from backend.database import get_db
from backend.config import UPLOAD_DIR
from backend.email_service import (
    encrypt_value, decrypt_value, get_access_token, send_email,
)
from backend.websocket_manager import manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["Admin"])

# ==========================================
# In-Memory OTP Store
# ==========================================
# Structure: { email: { "otp": "1234", "created_at": timestamp, "attempts": 0 } }
_otp_store = {}
# Structure: { email: { "verified_at": timestamp } }
_verified_emails = {}

OTP_EXPIRY_SECONDS = 300       # 5 minutes
OTP_COOLDOWN_SECONDS = 30      # Rate limit: 1 OTP per 30 seconds per email
VERIFIED_EXPIRY_SECONDS = 600  # Verified status valid for 10 minutes


def _cleanup_expired():
    """Remove expired OTPs and verified emails."""
    now = time.time()
    expired_otps = [e for e, d in _otp_store.items() if now - d["created_at"] > OTP_EXPIRY_SECONDS]
    for e in expired_otps:
        del _otp_store[e]
    expired_verified = [e for e, d in _verified_emails.items() if now - d["verified_at"] > VERIFIED_EXPIRY_SECONDS]
    for e in expired_verified:
        del _verified_emails[e]


def _is_email_verified(email: str) -> bool:
    """Check if an email has been verified via OTP and is still valid."""
    _cleanup_expired()
    entry = _verified_emails.get(email)
    if not entry:
        return False
    if time.time() - entry["verified_at"] > VERIFIED_EXPIRY_SECONDS:
        del _verified_emails[email]
        return False
    return True


def _get_gmail_credentials(db):
    """Retrieve Gmail OAuth2 credentials from email_settings table."""
    cursor = db.cursor()
    settings = cursor.execute("SELECT * FROM email_settings LIMIT 1").fetchone()
    if not settings:
        return None

    s = dict(settings)
    client_id = s.get("gmail_client_id") or ""
    client_secret = decrypt_value(s.get("gmail_client_secret_encrypted") or "")
    refresh_token = decrypt_value(s.get("gmail_refresh_token_encrypted") or "")
    sender_email = s.get("sender_email") or ""
    sender_name = s.get("sender_name") or ""

    if not all([client_id, client_secret, refresh_token, sender_email]):
        return None

    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "sender_email": sender_email,
        "sender_name": sender_name,
    }


# ==========================================
# OTP Request Models
# ==========================================
class SendOTPRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        v = v.strip().lower()
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError("Invalid email format")
        return v


class VerifyOTPRequest(BaseModel):
    email: str
    otp: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        return v.strip().lower()

    @field_validator("otp")
    @classmethod
    def validate_otp(cls, v):
        v = v.strip()
        if not v.isdigit() or len(v) != 4:
            raise ValueError("OTP must be a 4-digit number")
        return v


# ==========================================
# OTP Endpoints
# ==========================================
@router.post("/send-otp")
def send_otp(data: SendOTPRequest, admin: dict = Depends(require_admin), db=Depends(get_db)):
    """Send a 4-digit OTP to the given email address for verification."""
    _cleanup_expired()

    # Check Gmail credentials are configured
    creds = _get_gmail_credentials(db)
    if not creds:
        raise HTTPException(
            status_code=400,
            detail="Gmail credentials are not configured. Please configure Gmail OAuth2 credentials in Admin Panel → Email Settings → Credentials tab before creating users."
        )

    # Rate limiting: prevent spamming
    existing = _otp_store.get(data.email)
    if existing and time.time() - existing["created_at"] < OTP_COOLDOWN_SECONDS:
        remaining = int(OTP_COOLDOWN_SECONDS - (time.time() - existing["created_at"]))
        raise HTTPException(
            status_code=429,
            detail=f"Please wait {remaining} seconds before requesting another OTP."
        )

    # Generate 4-digit OTP
    otp_code = str(random.randint(1000, 9999))

    # Store OTP
    _otp_store[data.email] = {
        "otp": otp_code,
        "created_at": time.time(),
        "attempts": 0,
    }

    # Get access token
    try:
        access_token = get_access_token(creds["client_id"], creds["client_secret"], creds["refresh_token"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Failed to authenticate with Gmail: {str(e)}")

    # Build OTP email
    otp_subject = "Your OTP Verification Code — D&V Business Consulting"
    otp_body = f"""
    <html>
    <body style="font-family: 'Segoe UI', Arial, sans-serif; margin:0; padding:0; background-color:#f5f5f5;">
        <div style="max-width:480px; margin:30px auto; background:#ffffff; border-radius:12px; overflow:hidden; box-shadow:0 4px 20px rgba(0,0,0,0.08);">
            <div style="background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); padding:28px 32px; text-align:center;">
                <h2 style="color:#ffffff; margin:0; font-size:20px; font-weight:600; letter-spacing:0.5px;">D&V Business Consulting</h2>
                <p style="color:rgba(255,255,255,0.7); margin:6px 0 0; font-size:13px;">Email Verification</p>
            </div>
            <div style="padding:32px;">
                <p style="color:#333; font-size:15px; margin:0 0 8px; line-height:1.5;">Hello,</p>
                <p style="color:#555; font-size:14px; margin:0 0 24px; line-height:1.6;">Your one-time verification code is:</p>
                <div style="text-align:center; margin:0 0 24px;">
                    <div style="display:inline-block; background:linear-gradient(135deg, #667eea 0%, #764ba2 100%); color:#fff; font-size:32px; font-weight:700; letter-spacing:12px; padding:16px 32px; border-radius:10px; font-family:'Courier New', monospace;">
                        {otp_code}
                    </div>
                </div>
                <p style="color:#888; font-size:13px; margin:0 0 6px; text-align:center;">This code is valid for <strong>5 minutes</strong>.</p>
                <p style="color:#888; font-size:13px; margin:0; text-align:center;">If you did not request this code, please ignore this email.</p>
            </div>
            <div style="background:#f8f9fa; padding:16px 32px; text-align:center; border-top:1px solid #eee;">
                <p style="color:#aaa; font-size:11px; margin:0;">© D&V Business Consulting — Agreement Management System</p>
            </div>
        </div>
    </body>
    </html>
    """

    # Send email
    sender_from = f"{creds['sender_name']} <{creds['sender_email']}>" if creds.get("sender_name") else creds["sender_email"]
    result = send_email(
        sender=sender_from,
        to=data.email,
        subject=otp_subject,
        body=otp_body,
        is_html=True,
        access_token=access_token,
    )

    if result["status"] != "sent":
        # Remove OTP on send failure so user can retry immediately
        _otp_store.pop(data.email, None)
        raise HTTPException(status_code=500, detail=f"Failed to send OTP email: {result.get('error', 'Unknown error')}")

    logger.info(f"OTP sent to {data.email}")
    return {"message": "OTP sent successfully. Please check your email."}


@router.post("/verify-otp")
def verify_otp(data: VerifyOTPRequest, admin: dict = Depends(require_admin), db=Depends(get_db)):
    """Verify the 4-digit OTP for the given email address."""
    _cleanup_expired()

    entry = _otp_store.get(data.email)
    if not entry:
        raise HTTPException(status_code=400, detail="No OTP found for this email. Please request a new OTP.")

    # Check expiry
    if time.time() - entry["created_at"] > OTP_EXPIRY_SECONDS:
        _otp_store.pop(data.email, None)
        raise HTTPException(status_code=400, detail="OTP has expired. Please request a new OTP.")

    # Check max attempts (prevent brute force)
    if entry["attempts"] >= 5:
        _otp_store.pop(data.email, None)
        raise HTTPException(status_code=400, detail="Too many incorrect attempts. Please request a new OTP.")

    # Verify OTP
    if entry["otp"] != data.otp:
        entry["attempts"] += 1
        raise HTTPException(status_code=400, detail="OTP does not match. Please try again.")

    # OTP is correct — mark email as verified
    _otp_store.pop(data.email, None)
    _verified_emails[data.email] = {"verified_at": time.time()}

    logger.info(f"Email verified via OTP: {data.email}")
    return {"message": "Email verified successfully.", "verified": True}


# ==========================================
# Check Gmail credentials status endpoint
# ==========================================
@router.get("/check-gmail-credentials")
def check_gmail_credentials(admin: dict = Depends(require_admin), db=Depends(get_db)):
    """Check if Gmail OAuth2 credentials are configured for OTP sending."""
    creds = _get_gmail_credentials(db)
    return {"configured": creds is not None}


class UserUpdate(BaseModel):
    is_active: Optional[bool] = None
    role: Optional[str] = None
    global_payment_access: Optional[bool] = None


class PasswordReset(BaseModel):
    new_password: str


class CreateUser(BaseModel):
    email: str
    full_name: str
    password: str
    role: str = "user"
    global_payment_access: bool = False

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        v = v.strip().lower()
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError("Invalid email format")
        return v

    @field_validator("full_name")
    @classmethod
    def validate_full_name(cls, v):
        if not v or len(v.strip()) < 2:
            raise ValueError("Full name must be at least 2 characters")
        return v.strip()

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        return v

    @field_validator("role")
    @classmethod
    def validate_role(cls, v):
        if v not in ("admin", "user"):
            raise ValueError("Role must be 'admin' or 'user'")
        return v


class UpdateCredentials(BaseModel):
    email: Optional[str] = None
    full_name: Optional[str] = None
    password: Optional[str] = None
    global_payment_access: Optional[bool] = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        if v is None:
            return v
        v = v.strip().lower()
        if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', v):
            raise ValueError("Invalid email format")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if v is not None and len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        return v


@router.get("/dashboard")
def admin_dashboard(admin: dict = Depends(require_admin), db=Depends(get_db)):
    cursor = db.cursor()

    total_users = cursor.execute("SELECT COUNT(*) as count FROM users WHERE role IN ('user', 'admin')").fetchone()["count"]
    total_agreements = cursor.execute("SELECT COUNT(*) as count FROM agreements").fetchone()["count"]
    active_agreements = cursor.execute("SELECT COUNT(*) as count FROM agreements WHERE status = 'active'").fetchone()["count"]
    expired_agreements = cursor.execute("SELECT COUNT(*) as count FROM agreements WHERE status = 'expired'").fetchone()["count"]
    pending_agreements = cursor.execute("SELECT COUNT(*) as count FROM agreements WHERE status = 'pending'").fetchone()["count"]
    total_payments_pending = cursor.execute("SELECT COUNT(*) as count FROM payments WHERE status = 'pending'").fetchone()["count"]

    # Recent agreements
    recent = cursor.execute("""
        SELECT a.*, aa.company_name, u.username, u.full_name
        FROM agreements a
        LEFT JOIN agreement_analysis aa ON a.id = aa.agreement_id
        JOIN users u ON a.user_id = u.id
        ORDER BY a.uploaded_at DESC LIMIT 10
    """).fetchall()

    return {
        "stats": {
            "total_users": total_users,
            "total_agreements": total_agreements,
            "active_agreements": active_agreements,
            "expired_agreements": expired_agreements,
            "pending_agreements": pending_agreements,
            "pending_payments": total_payments_pending
        },
        "recent_agreements": [dict(r) for r in recent]
    }


@router.get("/users")
def list_users(admin: dict = Depends(require_admin), db=Depends(get_db)):
    cursor = db.cursor()
    users = cursor.execute("""
        SELECT u.id, u.username, u.email, u.full_name, u.role, u.is_active, u.created_at,
               u.global_payment_access,
               COUNT(a.id) as agreement_count
        FROM users u
        LEFT JOIN agreements a ON u.id = a.user_id
        WHERE u.role != 'consultant'
        GROUP BY u.id
        ORDER BY u.created_at DESC
    """).fetchall()
    return {"users": [dict(u) for u in users]}


@router.post("/users")
def create_user(data: CreateUser, admin: dict = Depends(require_admin), db=Depends(get_db)):
    """Admin-only: create a new user. Email must be OTP-verified first."""
    cursor = db.cursor()

    # Enforce OTP email verification
    if not _is_email_verified(data.email):
        raise HTTPException(
            status_code=400,
            detail="Email address has not been verified. Please verify the email with OTP before creating a user."
        )

    # Check duplicate email
    existing = cursor.execute("SELECT id FROM users WHERE email = ?", (data.email,)).fetchone()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Derive username from email prefix (e.g., john.doe@dvconsulting.co.in → john.doe)
    username = data.email.split("@")[0].lower()

    # Ensure username uniqueness (append number if needed)
    base_username = username
    counter = 1
    while cursor.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
        username = f"{base_username}{counter}"
        counter += 1

    password_hash = hash_password(data.password)
    # Only set global_payment_access for user role
    gpa_value = 1 if (data.global_payment_access and data.role == "user") else 0
    cursor.execute(
        "INSERT INTO users (username, email, full_name, password_hash, role, global_payment_access) VALUES (?, ?, ?, ?, ?, ?)",
        (username, data.email, data.full_name, password_hash, data.role, gpa_value)
    )
    db.commit()

    # Broadcast real-time update to all connected clients
    manager.broadcast_sync("user_created", {})

    new_user_id = cursor.lastrowid
    return {
        "message": "User created successfully",
        "user": {
            "id": new_user_id,
            "username": username,
            "email": data.email,
            "full_name": data.full_name,
            "role": data.role
        }
    }


@router.put("/users/{user_id}")
def update_user(user_id: int, data: UserUpdate, admin: dict = Depends(require_admin), db=Depends(get_db)):
    cursor = db.cursor()
    user = cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot modify your own account")

    updates = []
    params = []
    if data.is_active is not None:
        updates.append("is_active = ?")
        params.append(1 if data.is_active else 0)
    if data.role is not None:
        if data.role not in ("admin", "user"):
            raise HTTPException(status_code=400, detail="Invalid role")
        updates.append("role = ?")
        params.append(data.role)
    if data.global_payment_access is not None:
        updates.append("global_payment_access = ?")
        params.append(1 if data.global_payment_access else 0)

    if updates:
        params.append(user_id)
        cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
        db.commit()
        # Broadcast real-time update to all connected clients
        manager.broadcast_sync("user_updated", {})

    return {"message": "User updated"}


@router.put("/users/{user_id}/update-credentials")
def update_user_credentials(
    user_id: int,
    data: UpdateCredentials,
    admin: dict = Depends(require_admin),
    db=Depends(get_db)
):
    """Admin-only: update a user's email, full name, and/or password.
    If the email is being changed, it must be OTP-verified first."""
    cursor = db.cursor()
    user = cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    current_email = dict(user).get("email", "")

    updates = []
    params = []

    if data.email is not None:
        # If email is changing, require OTP verification
        if data.email.strip().lower() != current_email.strip().lower():
            if not _is_email_verified(data.email):
                raise HTTPException(
                    status_code=400,
                    detail="New email address has not been verified. Please verify the email with OTP before updating."
                )

        # Check duplicate email (excluding the current user)
        existing = cursor.execute(
            "SELECT id FROM users WHERE email = ? AND id != ?", (data.email, user_id)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=400, detail="Email already in use by another user")

        updates.append("email = ?")
        params.append(data.email)
        # Also update username to match new email prefix
        new_username = data.email.split("@")[0].lower()
        existing_username = cursor.execute(
            "SELECT id FROM users WHERE username = ? AND id != ?", (new_username, user_id)
        ).fetchone()
        if existing_username:
            new_username = f"{new_username}_{user_id}"
        updates.append("username = ?")
        params.append(new_username)

    if data.full_name is not None:
        updates.append("full_name = ?")
        params.append(data.full_name.strip())

    if data.password is not None:
        updates.append("password_hash = ?")
        params.append(hash_password(data.password))

    # Handle global_payment_access (included in this endpoint for convenience)
    if data.global_payment_access is not None:
        updates.append("global_payment_access = ?")
        params.append(1 if data.global_payment_access else 0)

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = CURRENT_TIMESTAMP")
    params.append(user_id)
    cursor.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", params)
    db.commit()

    return {"message": "User credentials updated successfully"}


@router.put("/users/{user_id}/reset-password")
def reset_password(user_id: int, data: PasswordReset, admin: dict = Depends(require_admin), db=Depends(get_db)):
    if len(data.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")

    cursor = db.cursor()
    user = cursor.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    new_hash = hash_password(data.new_password)
    cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
    db.commit()

    return {"message": "Password reset successful"}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: dict = Depends(require_admin), db=Depends(get_db)):
    cursor = db.cursor()
    user = cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")

    # Delete user's uploaded files from disk
    upload_dir = os.path.normpath(UPLOAD_DIR)
    if not os.path.isabs(upload_dir):
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        upload_dir = os.path.join(project_root, upload_dir)
    user_upload_dir = os.path.join(upload_dir, f"user_{user_id}")
    if os.path.exists(user_upload_dir):
        shutil.rmtree(user_upload_dir, ignore_errors=True)

    cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    # Broadcast real-time update to all connected clients
    manager.broadcast_sync("user_deleted", {})

    return {"message": "User deleted"}
