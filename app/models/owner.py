import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# Deliberately not pydantic's EmailStr, which needs the email-validator
# dependency — this project prefers stdlib/no-new-dependency where a
# simple check is enough (see app/services/security.py for the same
# reasoning about password hashing). Good enough to catch typos, not a
# full RFC 5322 validator — real deliverability is checked at signup by
# nothing here, same as most apps' registration forms.
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class OwnerRegisterRequest(BaseModel):
    """Real account registration — see app/routers/owners.py and
    app/services/security.py. Replaces the earlier client-side-only
    profile (localStorage, no backend account at all): a real account is
    what makes "log in from any browser and see your data" possible."""

    owner_name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., min_length=3, max_length=254)
    phone: str = Field(..., min_length=1, max_length=50)
    password: str = Field(..., min_length=8, max_length=200)
    pet_name: str = Field(..., min_length=1, max_length=200)
    species: str = Field(..., min_length=1, max_length=100)
    breed: Optional[str] = Field(default=None, max_length=200)
    pet_age: float = Field(..., ge=0)
    pet_weight: Optional[float] = Field(default=None, ge=0)
    vet_id: str = Field(..., min_length=1, max_length=100)
    clinic_name: Optional[str] = Field(default=None, max_length=200)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not EMAIL_PATTERN.match(v):
            raise ValueError("Not a valid email address")
        return v.strip().lower()


class OwnerUpdateRequest(BaseModel):
    """Payload for PATCH /owners/{owner_id} — the Profile edit modal.
    Deliberately excludes email/password/vet_id: identity and clinic
    assignment aren't editable from this form (see web/dashboard.html)."""

    owner_name: str = Field(..., min_length=1, max_length=200)
    pet_name: str = Field(..., min_length=1, max_length=200)
    species: str = Field(..., min_length=1, max_length=100)
    breed: Optional[str] = Field(default=None, max_length=200)
    pet_age: float = Field(..., ge=0)
    pet_weight: Optional[float] = Field(default=None, ge=0)


class OwnerAccount(BaseModel):
    """Never includes password_hash — this is the shape returned to the
    client after register/login/update."""

    owner_id: str
    owner_name: str
    email: str
    phone: str
    pet_name: str
    species: str
    breed: Optional[str] = None
    pet_age: float
    pet_weight: Optional[float] = None
    vet_id: str
    clinic_name: Optional[str] = None


class OwnerRegisterResponse(BaseModel):
    account: OwnerAccount
