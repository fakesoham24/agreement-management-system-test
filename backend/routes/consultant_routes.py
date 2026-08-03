"""
Consultant Routes — CRUD for consultant persons and agreement-consultant assignments.
Admin-only for managing consultants; all users can assign consultants to agreements.
"""
import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from typing import List, Optional
from backend.auth import get_current_user, require_admin, hash_password
from backend.database import get_db

router = APIRouter(prefix="/api/consultants", tags=["Consultants"])

# Valid designation options
VALID_DESIGNATIONS = [
    "Project Manager",
    "Lead Consultant",
    "Senior Consultant",
    "Lean Consultant",
    "Consultant",
    "Junior Consultant",
    "Associate Consultant",
]


# ==========================================
# Request Models
# ==========================================
class ConsultantCreate(BaseModel):
    name: str
    designation: str
    email: str
    password: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        v = v.strip()
        if not v or len(v) < 2:
            raise ValueError("Name must be at least 2 characters")
        return v

    @field_validator("designation")
    @classmethod
    def validate_designation(cls, v):
        if v not in VALID_DESIGNATIONS:
            raise ValueError(f"Designation must be one of: {', '.join(VALID_DESIGNATIONS)}")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        v = v.strip().lower()
        if not v:
            raise ValueError("Email is required")
        # Comprehensive email validation
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(pattern, v):
            raise ValueError("Please enter a valid email address")
        # Block obviously fake/disposable patterns
        local_part = v.split("@")[0]
        domain = v.split("@")[1]
        if len(local_part) < 2:
            raise ValueError("Email local part is too short")
        if domain.count(".") < 1:
            raise ValueError("Please enter a valid email domain")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if not v or len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        return v


class ConsultantUpdate(BaseModel):
    name: Optional[str] = None
    designation: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if v is not None:
            v = v.strip()
            if len(v) < 2:
                raise ValueError("Name must be at least 2 characters")
        return v

    @field_validator("designation")
    @classmethod
    def validate_designation(cls, v):
        if v is not None and v not in VALID_DESIGNATIONS:
            raise ValueError(f"Designation must be one of: {', '.join(VALID_DESIGNATIONS)}")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v):
        if v is not None:
            v = v.strip().lower()
            if not v:
                raise ValueError("Email is required")
            pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(pattern, v):
                raise ValueError("Please enter a valid email address")
            local_part = v.split("@")[0]
            if len(local_part) < 2:
                raise ValueError("Email local part is too short")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if v is not None and v != "" and len(v) < 6:
            raise ValueError("Password must be at least 6 characters")
        return v


class AssignConsultants(BaseModel):
    consultant_ids: List[int]

    @field_validator("consultant_ids")
    @classmethod
    def validate_ids(cls, v):
        if not v or len(v) == 0:
            raise ValueError("At least one consultant must be selected")
        return v


# ==========================================
# Admin-only: CRUD for Consultant Persons
# ==========================================

@router.get("/")
def list_consultants(
    admin: dict = Depends(require_admin),
    db=Depends(get_db),
):
    """List all consultants with full details (admin only)."""
    cursor = db.cursor()
    consultants = cursor.execute("""
        SELECT c.*,
               (SELECT COUNT(*) FROM agreement_consultants ac
                JOIN agreements a ON ac.agreement_id = a.id
                WHERE ac.consultant_id = c.id AND a.status = 'active') as active_agreements
        FROM consultants c
        ORDER BY c.created_at DESC
    """).fetchall()
    result = []
    for c in consultants:
        cd = dict(c)
        # Check if this consultant has a linked user account (has login capability)
        linked_user = cursor.execute(
            "SELECT id FROM users WHERE consultant_id = ? AND role = 'consultant'", (cd["id"],)
        ).fetchone()
        cd["has_login"] = linked_user is not None
        result.append(cd)
    return {"consultants": result}


@router.post("/")
def create_consultant(
    data: ConsultantCreate,
    admin: dict = Depends(require_admin),
    db=Depends(get_db),
):
    """Create a new consultant with login account (admin only). Email must be OTP-verified."""
    # Import OTP verification from admin_routes
    from backend.routes.admin_routes import _is_email_verified

    cursor = db.cursor()

    # Verify email via OTP
    if not _is_email_verified(data.email):
        raise HTTPException(status_code=400, detail="Email must be verified via OTP before creating a consultant account")

    # Check duplicate email in consultants
    existing = cursor.execute(
        "SELECT id FROM consultants WHERE email = ?", (data.email,)
    ).fetchone()
    if existing:
        raise HTTPException(status_code=400, detail="A consultant with this email already exists")

    # Check duplicate email in users table
    existing_user = cursor.execute(
        "SELECT id FROM users WHERE email = ?", (data.email,)
    ).fetchone()
    if existing_user:
        raise HTTPException(status_code=400, detail="A user with this email already exists")

    # Hash password
    pw_hash = hash_password(data.password)

    # Insert into consultants table
    cursor.execute(
        "INSERT INTO consultants (name, designation, email, password_hash) VALUES (?, ?, ?, ?)",
        (data.name, data.designation, data.email, pw_hash),
    )
    db.commit()
    consultant_id = cursor.lastrowid

    # Create linked user account for login
    # Generate unique username from email
    username = data.email.split("@")[0].lower()
    base_username = username
    counter = 1
    while cursor.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
        username = f"{base_username}{counter}"
        counter += 1

    cursor.execute(
        """INSERT INTO users (username, email, full_name, password_hash, role, consultant_id)
           VALUES (?, ?, ?, ?, 'consultant', ?)""",
        (username, data.email, data.name, pw_hash, consultant_id),
    )
    db.commit()

    return {
        "message": "Consultant added successfully",
        "consultant": {
            "id": consultant_id,
            "name": data.name,
            "designation": data.designation,
            "email": data.email,
        },
    }


@router.put("/{consultant_id}")
def update_consultant(
    consultant_id: int,
    data: ConsultantUpdate,
    admin: dict = Depends(require_admin),
    db=Depends(get_db),
):
    """Update consultant details (admin only). Syncs changes to linked user account."""
    cursor = db.cursor()
    existing = cursor.execute(
        "SELECT * FROM consultants WHERE id = ?", (consultant_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Consultant not found")

    existing_dict = dict(existing)
    update_data = data.model_dump(exclude_unset=True)

    # Find linked user account
    linked_user = cursor.execute(
        "SELECT * FROM users WHERE consultant_id = ? AND role = 'consultant'", (consultant_id,)
    ).fetchone()

    # --- Handle email change with OTP verification ---
    email_changed = False
    new_email = None
    if "email" in update_data and update_data["email"] is not None:
        new_email = update_data["email"]
        if new_email != existing_dict.get("email"):
            email_changed = True
            # Verify new email via OTP
            from backend.routes.admin_routes import _is_email_verified
            if not _is_email_verified(new_email):
                raise HTTPException(status_code=400, detail="New email must be verified via OTP before updating")

            # Check duplicate email in consultants
            dup = cursor.execute(
                "SELECT id FROM consultants WHERE email = ? AND id != ?",
                (new_email, consultant_id),
            ).fetchone()
            if dup:
                raise HTTPException(status_code=400, detail="A consultant with this email already exists")

            # Check duplicate email in users
            if linked_user:
                dup_user = cursor.execute(
                    "SELECT id FROM users WHERE email = ? AND id != ?",
                    (new_email, linked_user["id"]),
                ).fetchone()
            else:
                dup_user = cursor.execute(
                    "SELECT id FROM users WHERE email = ?",
                    (new_email,),
                ).fetchone()
            if dup_user:
                raise HTTPException(status_code=400, detail="A user with this email already exists")

    # --- Build consultant table updates ---
    updates = []
    params = []

    if "name" in update_data and update_data["name"] is not None:
        updates.append("name = ?")
        params.append(update_data["name"])
    if "designation" in update_data and update_data["designation"] is not None:
        updates.append("designation = ?")
        params.append(update_data["designation"])
    if email_changed:
        updates.append("email = ?")
        params.append(new_email)
    if "password" in update_data and update_data["password"] is not None and update_data["password"] != "":
        pw_hash = hash_password(update_data["password"])
        updates.append("password_hash = ?")
        params.append(pw_hash)

    has_password_update = "password" in update_data and update_data["password"] is not None and update_data["password"] != ""

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = ?")
    params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    params.append(consultant_id)
    cursor.execute(
        f"UPDATE consultants SET {', '.join(updates)} WHERE id = ?", params
    )

    # --- Sync changes to linked user account ---
    if linked_user:
        user_updates = []
        user_params = []

        if "name" in update_data and update_data["name"] is not None:
            user_updates.append("full_name = ?")
            user_params.append(update_data["name"])
        if email_changed:
            user_updates.append("email = ?")
            user_params.append(new_email)
            # Update username too
            new_username = new_email.split("@")[0].lower()
            base_username = new_username
            counter = 1
            while True:
                dup = cursor.execute(
                    "SELECT id FROM users WHERE username = ? AND id != ?", (new_username, linked_user["id"])
                ).fetchone()
                if not dup:
                    break
                new_username = f"{base_username}{counter}"
                counter += 1
            user_updates.append("username = ?")
            user_params.append(new_username)
        if has_password_update:
            user_updates.append("password_hash = ?")
            user_params.append(hash_password(update_data["password"]))

        if user_updates:
            user_updates.append("updated_at = ?")
            user_params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            user_params.append(linked_user["id"])
            cursor.execute(
                f"UPDATE users SET {', '.join(user_updates)} WHERE id = ?", user_params
            )
    else:
        # No linked user yet — if password is provided, create one now
        if has_password_update:
            pw_hash = hash_password(update_data["password"])
            email_for_user = new_email if email_changed else existing_dict.get("email")
            name_for_user = update_data.get("name") or existing_dict.get("name")

            # Check if email already exists in users
            dup_user = cursor.execute(
                "SELECT id FROM users WHERE email = ?", (email_for_user,)
            ).fetchone()
            if dup_user:
                raise HTTPException(status_code=400, detail="A user with this email already exists. Cannot create login account.")

            # Generate unique username
            username = email_for_user.split("@")[0].lower()
            base_username = username
            counter = 1
            while cursor.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
                username = f"{base_username}{counter}"
                counter += 1

            cursor.execute(
                """INSERT INTO users (username, email, full_name, password_hash, role, consultant_id)
                   VALUES (?, ?, ?, ?, 'consultant', ?)""",
                (username, email_for_user, name_for_user, pw_hash, consultant_id),
            )

    db.commit()
    return {"message": "Consultant updated successfully"}


@router.delete("/{consultant_id}")
def delete_consultant(
    consultant_id: int,
    admin: dict = Depends(require_admin),
    db=Depends(get_db),
):
    """Delete a consultant and their linked user account (admin only)."""
    cursor = db.cursor()
    existing = cursor.execute(
        "SELECT * FROM consultants WHERE id = ?", (consultant_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Consultant not found")

    # Remove all assignments first (CASCADE should handle but be explicit)
    cursor.execute(
        "DELETE FROM agreement_consultants WHERE consultant_id = ?", (consultant_id,)
    )
    # Delete linked user account
    cursor.execute(
        "DELETE FROM users WHERE consultant_id = ? AND role = 'consultant'", (consultant_id,)
    )
    # Delete consultant
    cursor.execute("DELETE FROM consultants WHERE id = ?", (consultant_id,))
    db.commit()
    return {"message": "Consultant deleted successfully"}


# ==========================================
# All-user: Consultant dropdown list (no email)
# ==========================================

@router.get("/list")
def list_consultants_for_dropdown(
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List consultants for dropdown selection (no email shown)."""
    cursor = db.cursor()
    consultants = cursor.execute(
        "SELECT id, name, designation FROM consultants WHERE is_active = 1 ORDER BY name"
    ).fetchall()
    return {"consultants": [dict(c) for c in consultants]}


@router.get("/designations")
def get_designations(current_user: dict = Depends(get_current_user)):
    """Return valid designation options."""
    return {"designations": VALID_DESIGNATIONS}


# ==========================================
# Agreement-Consultant Assignment Endpoints
# ==========================================

@router.get("/agreement/{agreement_id}")
def get_agreement_consultants(
    agreement_id: int,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get consultants assigned to an agreement."""
    cursor = db.cursor()

    # Verify agreement exists and user has access
    agreement = cursor.execute(
        "SELECT * FROM agreements WHERE id = ?", (agreement_id,)
    ).fetchone()
    if not agreement:
        raise HTTPException(status_code=404, detail="Agreement not found")
    # Allow access for admin, owner, or assigned consultant
    if current_user["role"] != "admin" and agreement["user_id"] != current_user["id"]:
        if current_user["role"] == "consultant" and current_user.get("consultant_id"):
            assigned = cursor.execute(
                "SELECT id FROM agreement_consultants WHERE agreement_id = ? AND consultant_id = ?",
                (agreement_id, current_user["consultant_id"])
            ).fetchone()
            if not assigned:
                raise HTTPException(status_code=403, detail="Access denied")
        else:
            raise HTTPException(status_code=403, detail="Access denied")

    consultants = cursor.execute("""
        SELECT c.id, c.name, c.designation, ac.assigned_at
        FROM agreement_consultants ac
        JOIN consultants c ON ac.consultant_id = c.id
        WHERE ac.agreement_id = ?
        ORDER BY c.name
    """, (agreement_id,)).fetchall()

    return {"consultants": [dict(c) for c in consultants]}


@router.get("/agreement/{agreement_id}/has-consultants")
def check_has_consultants(
    agreement_id: int,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Check if an agreement has consultants assigned."""
    cursor = db.cursor()

    agreement = cursor.execute(
        "SELECT * FROM agreements WHERE id = ?", (agreement_id,)
    ).fetchone()
    if not agreement:
        raise HTTPException(status_code=404, detail="Agreement not found")
    if current_user["role"] != "admin" and agreement["user_id"] != current_user["id"]:
        if current_user["role"] == "consultant" and current_user.get("consultant_id"):
            assigned = cursor.execute(
                "SELECT id FROM agreement_consultants WHERE agreement_id = ? AND consultant_id = ?",
                (agreement_id, current_user["consultant_id"])
            ).fetchone()
            if not assigned:
                raise HTTPException(status_code=403, detail="Access denied")
        else:
            raise HTTPException(status_code=403, detail="Access denied")

    count = cursor.execute(
        "SELECT COUNT(*) as cnt FROM agreement_consultants WHERE agreement_id = ?",
        (agreement_id,),
    ).fetchone()["cnt"]

    return {"has_consultants": count > 0, "count": count}


@router.post("/agreement/{agreement_id}")
def assign_consultants(
    agreement_id: int,
    data: AssignConsultants,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Assign consultants to an agreement (replaces existing assignments)."""
    cursor = db.cursor()

    agreement = cursor.execute(
        "SELECT * FROM agreements WHERE id = ?", (agreement_id,)
    ).fetchone()
    if not agreement:
        raise HTTPException(status_code=404, detail="Agreement not found")
    if current_user["role"] != "admin" and agreement["user_id"] != current_user["id"]:
        raise HTTPException(status_code=403, detail="Access denied")

    # Validate all consultant IDs exist
    for cid in data.consultant_ids:
        exists = cursor.execute(
            "SELECT id FROM consultants WHERE id = ? AND is_active = 1", (cid,)
        ).fetchone()
        if not exists:
            raise HTTPException(
                status_code=400, detail=f"Consultant with ID {cid} not found or inactive"
            )

    # Remove existing assignments and re-assign
    cursor.execute(
        "DELETE FROM agreement_consultants WHERE agreement_id = ?", (agreement_id,)
    )
    for cid in data.consultant_ids:
        cursor.execute(
            "INSERT INTO agreement_consultants (agreement_id, consultant_id) VALUES (?, ?)",
            (agreement_id, cid),
        )

    db.commit()
    return {"message": f"{len(data.consultant_ids)} consultant(s) assigned successfully"}


@router.put("/agreement/{agreement_id}")
def update_agreement_consultants(
    agreement_id: int,
    data: AssignConsultants,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update consultant assignments for an agreement."""
    # Same logic as assign — replace all
    return assign_consultants(agreement_id, data, current_user, db)
