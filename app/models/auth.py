from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.models.owner import OwnerAccount
from app.models.vet import VetListItem


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=200)


class LoginResponse(BaseModel):
    """Exactly one of owner/vet is set, matching `role` — see
    app/routers/auth.py, which looks the account up by email (unique
    across both roles) and only then knows which one to populate."""

    role: Literal["owner", "vet"]
    owner: Optional[OwnerAccount] = None
    vet: Optional[VetListItem] = None
