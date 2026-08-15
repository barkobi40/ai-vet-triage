from fastapi import APIRouter, HTTPException, status

from app.db import dynamodb, schema
from app.models.auth import LoginRequest, LoginResponse
from app.models.vet import VetListItem
from app.routers.owners import item_to_owner_account
from app.services.security import verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest) -> LoginResponse:
    """
    Unified login: looks the account up once by email (owner and vet
    accounts share GSI2, see app/db/schema.py — email is unique across
    both roles), verifies the password against its stored hash, and
    returns the matching role so the client knows whether to land on the
    owner dashboard or the vet portal. Identical error for "no such
    email" and "wrong password" — a login endpoint that behaves
    differently for the two cases leaks whether an email is registered
    at all, which real login flows deliberately avoid.
    """
    item = await dynamodb.get_by_gsi2(schema.email_gsi2pk(payload.email))
    if item is None or not verify_password(payload.password, item.get("password_hash", "")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")

    if item.get("role") == "vet":
        return LoginResponse(
            role="vet",
            vet=VetListItem(
                vet_id=item["vet_id"],
                vet_name=item["vet_name"],
                clinic_name=item["clinic_name"],
                clinic_address=item["clinic_address"],
                phone=item["phone"],
                license_number=item["license_number"],
                username=item["username"],
            ),
        )

    return LoginResponse(role="owner", owner=item_to_owner_account(item))
