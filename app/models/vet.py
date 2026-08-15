from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.models.owner import EMAIL_PATTERN


class VetRegisterRequest(BaseModel):
    """Payload for adding a vet/clinic to the directory *and* creating a
    real login account for it — see app/routers/vets.py and
    app/services/security.py. Earlier versions of this had no password
    field at all (an open, unauthenticated demo directory); real
    email+password login replaced that so a vet can sign back in on any
    browser, matching how owner accounts now work too."""

    vet_name: str = Field(..., min_length=1, max_length=200)
    clinic_name: str = Field(..., min_length=1, max_length=200)
    clinic_address: str = Field(..., min_length=1, max_length=300)
    phone: str = Field(..., min_length=1, max_length=50)
    license_number: str = Field(..., min_length=1, max_length=100)
    username: str = Field(..., min_length=1, max_length=100)
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=8, max_length=200)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        if not EMAIL_PATTERN.match(v):
            raise ValueError("Not a valid email address")
        return v.strip().lower()


class VetRegisterResponse(BaseModel):
    vet_id: str


class VetListItem(BaseModel):
    """Public directory shape — deliberately excludes email and
    password_hash. Email is how login looks an account up, not something
    the open, unauthenticated clinic directory needs to expose."""

    vet_id: str
    vet_name: str
    clinic_name: str
    clinic_address: str
    phone: str
    license_number: str
    username: str


class VetListResponse(BaseModel):
    vets: list[VetListItem]
