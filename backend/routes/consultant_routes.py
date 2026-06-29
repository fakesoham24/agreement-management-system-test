"""
Consultant Routes — CRUD for consultant persons and agreement-consultant assignments.
Admin-only for managing consultants; all users can assign consultants to agreements.
"""
import re
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from typing import List, Optional
from backend.auth import get_current_user, require_admin
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


class ConsultantUpdate(BaseModel):
    name: Optional[str] = None
    designation: Optional[str] = None
    email: Optional[str] = None

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
    return {"consultants": [dict(c) for c in consultants]}


@router.post("/")
def create_consultant(
    data: ConsultantCreate,
    admin: dict = Depends(require_admin),
    db=Depends(get_db),
):
    """Create a new consultant (admin only)."""
    cursor = db.cursor()

    # Check duplicate email
    existing = cursor.execute(
        "SELECT id FROM consultants WHERE email = ?", (data.email,)
    ).fetchone()
    if existing:
        raise HTTPException(status_code=400, detail="A consultant with this email already exists")

    cursor.execute(
        "INSERT INTO consultants (name, designation, email) VALUES (?, ?, ?)",
        (data.name, data.designation, data.email),
    )
    db.commit()

    new_id = cursor.lastrowid
    return {
        "message": "Consultant added successfully",
        "consultant": {
            "id": new_id,
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
    """Update consultant details (admin only)."""
    cursor = db.cursor()
    existing = cursor.execute(
        "SELECT * FROM consultants WHERE id = ?", (consultant_id,)
    ).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Consultant not found")

    updates = []
    params = []
    update_data = data.model_dump(exclude_unset=True)

    if "name" in update_data and update_data["name"] is not None:
        updates.append("name = ?")
        params.append(update_data["name"])
    if "designation" in update_data and update_data["designation"] is not None:
        updates.append("designation = ?")
        params.append(update_data["designation"])
    if "email" in update_data and update_data["email"] is not None:
        # Check duplicate email (excluding current)
        dup = cursor.execute(
            "SELECT id FROM consultants WHERE email = ? AND id != ?",
            (update_data["email"], consultant_id),
        ).fetchone()
        if dup:
            raise HTTPException(status_code=400, detail="A consultant with this email already exists")
        updates.append("email = ?")
        params.append(update_data["email"])

    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")

    updates.append("updated_at = ?")
    params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    params.append(consultant_id)

    cursor.execute(
        f"UPDATE consultants SET {', '.join(updates)} WHERE id = ?", params
    )
    db.commit()
    return {"message": "Consultant updated successfully"}


@router.delete("/{consultant_id}")
def delete_consultant(
    consultant_id: int,
    admin: dict = Depends(require_admin),
    db=Depends(get_db),
):
    """Delete a consultant (admin only)."""
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
    if current_user["role"] != "admin" and agreement["user_id"] != current_user["id"]:
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
