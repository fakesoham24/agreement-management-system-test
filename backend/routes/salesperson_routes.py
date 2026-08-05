"""
Sales Person Routes — CRUD for sales persons and agreement-salesperson assignments.
Admin-only for managing salespersons; all users can assign salespersons to agreements.
"""
import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from typing import List, Optional
from backend.auth import get_current_user, require_admin, hash_password
from backend.database import get_db

router = APIRouter(prefix="/api/salespersons", tags=["SalesPersons"])

# Valid designation options for sales persons
VALID_DESIGNATIONS = [
    "Sales Manager",
    "Senior Sales Person",
    "Sales Executive",
]


# ==========================================
# Request Models
# ==========================================
class SalesPersonCreate(BaseModel):
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


class SalesPersonUpdate(BaseModel):
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


class AssignSalesPersons(BaseModel):
    salesperson_ids: List[int]

    @field_validator("salesperson_ids")
    @classmethod
    def validate_ids(cls, v):
        if not v or len(v) == 0:
            raise ValueError("At least one sales person must be selected")
        return v


# ==========================================
# Admin-only: CRUD for Sales Persons
# ==========================================

@router.get("/")
def list_salespersons(
    admin: dict = Depends(require_admin),
    db=Depends(get_db),
):
    """List all salespersons with full details (admin only)."""
    cursor = db.cursor()
    salespersons = cursor.execute("""
        SELECT s.*,
               (SELECT COUNT(*) FROM agreement_salespersons asp
                JOIN agreements a ON asp.agreement_id = a.id
                WHERE asp.salesperson_id = s.id AND a.status = 'active') as active_agreements
        FROM salespersons s
        ORDER BY s.created_at DESC
    """).fetchall()
    result = []
    for s in salespersons:
        sd = dict(s)
        # Check if this salesperson has a linked user account (has login capability)
        linked_user = cursor.execute(
            "SELECT id FROM users WHERE salesperson_id = ? AND role = 'salesperson'", (sd["id"],)
        ).fetchone()
        sd["has_login"] = linked_user is not None
        result.append(sd)
    return {"salespersons": result}


@router.post("/")
def create_salesperson(
    data: SalesPersonCreate,
    admin: dict = Depends(require_admin),
    db=Depends(get_db),
):
    """Create a new salesperson with login account (admin only). Email must be OTP-verified."""
    # Import OTP verification from admin_routes
    from backend.routes.admin_routes import _is_email_verified

    cursor = db.cursor()

    # Verify email via OTP
    if not _is_email_verified(data.email):
        raise HTTPException(status_code=400, detail="Email must be verified via OTP before creating a sales person account")

    # Check duplicate email in salespersons
    existing = cursor.execute(
        "SELECT id FROM salespersons WHERE email = ?", (data.email,)
    ).fetchone()
    if existing:
        raise HTTPException(status_code=400, detail="A sales person with this email already exists")

    # Check duplicate email in users table
    existing_user = cursor.execute(
        "SELECT id FROM users WHERE email = ?", (data.email,)
    ).fetchone()
    if existing_user:
        raise HTTPException(status_code=400, detail="A user with this email already exists")

    # Hash password
    pw_hash = hash_password(data.password)

    # Insert into salespersons table
    cursor.execute(
        "INSERT INTO salespersons (name, designation, email, password_hash) VALUES (?, ?, ?, ?)",
        (data.name, data.designation, data.email, pw_hash),
    )
    db.commit()
    salesperson_id = cursor.lastrowid

    # Create linked user account for login
    # Generate unique username from email
    username = data.email.split("@")[0].lower()
    base_username = username
    counter = 1
    while cursor.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone():
        username = f"{base_username}{counter}"
        counter += 1

    cursor.execute(
        """INSERT INTO users (username, email, full_name, password_hash, role, salesperson_id)
           VALUES (?, ?, ?, ?, 'salesperson', ?)""",
        (username, data.email, data.name, pw_hash, salesperson_id),
    )
    db.commit()

    return {
        "message": "Sales person added successfully",
        "salesperson": {
            "id": salesperson_id,
            "name": data.name,
            "designation": data.designation,
            "email": data.email,
        },
    }


@router.put("/{salesperson_id}")
def update_salesperson(
    salesperson_id: int,
    data: SalesPersonUpdate,
    admin: dict = Depends(require_admin),
    db=Depends(get_db),
):
    """Update salesperson details (admin only). Syncs changes to linked user account."""
    cursor = db.cursor()
    existing = cursor.execute(
        "SELECT * FROM salespersons WHERE id = ?", (salesperson_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Sales person not found")

    existing_dict = dict(existing)
    update_data = data.model_dump(exclude_unset=True)

    # Find linked user account
    linked_user = cursor.execute(
        "SELECT * FROM users WHERE salesperson_id = ? AND role = 'salesperson'", (salesperson_id,)
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

            # Check duplicate email in salespersons
            dup = cursor.execute(
                "SELECT id FROM salespersons WHERE email = ? AND id != ?",
                (new_email, salesperson_id),
            ).fetchone()
            if dup:
                raise HTTPException(status_code=400, detail="A sales person with this email already exists")

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

    # --- Build salesperson table updates ---
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
    params.append(salesperson_id)
    cursor.execute(
        f"UPDATE salespersons SET {', '.join(updates)} WHERE id = ?", params
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
                """INSERT INTO users (username, email, full_name, password_hash, role, salesperson_id)
                   VALUES (?, ?, ?, ?, 'salesperson', ?)""",
                (username, email_for_user, name_for_user, pw_hash, salesperson_id),
            )

    db.commit()
    return {"message": "Sales person updated successfully"}


@router.delete("/{salesperson_id}")
def delete_salesperson(
    salesperson_id: int,
    admin: dict = Depends(require_admin),
    db=Depends(get_db),
):
    """Delete a salesperson and their linked user account (admin only)."""
    cursor = db.cursor()
    existing = cursor.execute(
        "SELECT * FROM salespersons WHERE id = ?", (salesperson_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Sales person not found")

    # Remove all assignments first (CASCADE should handle but be explicit)
    cursor.execute(
        "DELETE FROM agreement_salespersons WHERE salesperson_id = ?", (salesperson_id,)
    )
    # Delete linked user account
    cursor.execute(
        "DELETE FROM users WHERE salesperson_id = ? AND role = 'salesperson'", (salesperson_id,)
    )
    # Delete salesperson
    cursor.execute("DELETE FROM salespersons WHERE id = ?", (salesperson_id,))
    db.commit()
    return {"message": "Sales person deleted successfully"}


# ==========================================
# All-user: SalesPerson dropdown list (no email)
# ==========================================

@router.get("/list")
def list_salespersons_for_dropdown(
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """List salespersons for dropdown selection (no email shown)."""
    cursor = db.cursor()
    salespersons = cursor.execute(
        "SELECT id, name, designation FROM salespersons WHERE is_active = 1 ORDER BY name"
    ).fetchall()
    return {"salespersons": [dict(s) for s in salespersons]}


@router.get("/designations")
def get_designations(current_user: dict = Depends(get_current_user)):
    """Return valid designation options for sales persons."""
    return {"designations": VALID_DESIGNATIONS}


# ==========================================
# Agreement-SalesPerson Assignment Endpoints
# ==========================================

@router.get("/agreement/{agreement_id}")
def get_agreement_salespersons(
    agreement_id: int,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Get salespersons assigned to an agreement."""
    cursor = db.cursor()

    # Verify agreement exists and user has access
    agreement = cursor.execute(
        "SELECT * FROM agreements WHERE id = ?", (agreement_id,)
    ).fetchone()
    if not agreement:
        raise HTTPException(status_code=404, detail="Agreement not found")
    # Allow access for admin, owner, assigned consultant, or assigned salesperson
    if current_user["role"] != "admin" and agreement["user_id"] != current_user["id"]:
        if current_user["role"] == "consultant" and current_user.get("consultant_id"):
            assigned = cursor.execute(
                "SELECT id FROM agreement_consultants WHERE agreement_id = ? AND consultant_id = ?",
                (agreement_id, current_user["consultant_id"])
            ).fetchone()
            if not assigned:
                raise HTTPException(status_code=403, detail="Access denied")
        elif current_user["role"] == "salesperson" and current_user.get("salesperson_id"):
            assigned = cursor.execute(
                "SELECT id FROM agreement_salespersons WHERE agreement_id = ? AND salesperson_id = ?",
                (agreement_id, current_user["salesperson_id"])
            ).fetchone()
            if not assigned:
                raise HTTPException(status_code=403, detail="Access denied")
        else:
            raise HTTPException(status_code=403, detail="Access denied")

    salespersons = cursor.execute("""
        SELECT s.id, s.name, s.designation, asp.assigned_at
        FROM agreement_salespersons asp
        JOIN salespersons s ON asp.salesperson_id = s.id
        WHERE asp.agreement_id = ?
        ORDER BY s.name
    """, (agreement_id,)).fetchall()

    return {"salespersons": [dict(s) for s in salespersons]}


@router.get("/agreement/{agreement_id}/has-salespersons")
def check_has_salespersons(
    agreement_id: int,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Check if an agreement has salespersons assigned."""
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
        elif current_user["role"] == "salesperson" and current_user.get("salesperson_id"):
            assigned = cursor.execute(
                "SELECT id FROM agreement_salespersons WHERE agreement_id = ? AND salesperson_id = ?",
                (agreement_id, current_user["salesperson_id"])
            ).fetchone()
            if not assigned:
                raise HTTPException(status_code=403, detail="Access denied")
        else:
            raise HTTPException(status_code=403, detail="Access denied")

    count = cursor.execute(
        "SELECT COUNT(*) as cnt FROM agreement_salespersons WHERE agreement_id = ?",
        (agreement_id,),
    ).fetchone()["cnt"]

    return {"has_salespersons": count > 0, "count": count}


@router.post("/agreement/{agreement_id}")
def assign_salespersons(
    agreement_id: int,
    data: AssignSalesPersons,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Assign salespersons to an agreement (replaces existing assignments)."""
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
        elif current_user["role"] == "salesperson" and current_user.get("salesperson_id"):
            assigned = cursor.execute(
                "SELECT id FROM agreement_salespersons WHERE agreement_id = ? AND salesperson_id = ?",
                (agreement_id, current_user["salesperson_id"])
            ).fetchone()
            if not assigned:
                raise HTTPException(status_code=403, detail="Access denied")
        else:
            raise HTTPException(status_code=403, detail="Access denied")

    # Validate all salesperson IDs exist
    for sid in data.salesperson_ids:
        exists = cursor.execute(
            "SELECT id FROM salespersons WHERE id = ? AND is_active = 1", (sid,)
        ).fetchone()
        if not exists:
            raise HTTPException(
                status_code=400, detail=f"Sales person with ID {sid} not found or inactive"
            )

    # Remove existing assignments and re-assign
    cursor.execute(
        "DELETE FROM agreement_salespersons WHERE agreement_id = ?", (agreement_id,)
    )
    for sid in data.salesperson_ids:
        cursor.execute(
            "INSERT INTO agreement_salespersons (agreement_id, salesperson_id) VALUES (?, ?)",
            (agreement_id, sid),
        )

    db.commit()
    return {"message": f"{len(data.salesperson_ids)} sales person(s) assigned successfully"}


@router.put("/agreement/{agreement_id}")
def update_agreement_salespersons(
    agreement_id: int,
    data: AssignSalesPersons,
    current_user: dict = Depends(get_current_user),
    db=Depends(get_db),
):
    """Update salesperson assignments for an agreement."""
    # Same logic as assign — replace all
    return assign_salespersons(agreement_id, data, current_user, db)
