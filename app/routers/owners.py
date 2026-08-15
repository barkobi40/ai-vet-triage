from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from app.db import dynamodb, schema
from app.models.owner import OwnerAccount, OwnerRegisterRequest, OwnerRegisterResponse, OwnerUpdateRequest
from app.services.security import hash_password

router = APIRouter(prefix="/owners", tags=["owners"])


def item_to_owner_account(item: dict[str, Any]) -> OwnerAccount:
    pet_weight = item.get("pet_weight")
    return OwnerAccount(
        owner_id=item["owner_id"],
        owner_name=item["owner_name"],
        email=item["email"],
        phone=item["phone"],
        pet_name=item["pet_name"],
        species=item["species"],
        breed=item.get("breed"),
        pet_age=float(item["pet_age"]),
        pet_weight=float(pet_weight) if pet_weight is not None else None,
        vet_id=item["vet_id"],
        clinic_name=item.get("clinic_name"),
    )


@router.post("/register", response_model=OwnerRegisterResponse, status_code=status.HTTP_201_CREATED)
async def register_owner(payload: OwnerRegisterRequest) -> OwnerRegisterResponse:
    """
    Creates a real owner account (email + hashed password), replacing the
    earlier client-side-only localStorage profile — a random per-browser
    id had no way to follow an owner to a different browser or device;
    a real account, looked up by email at login (see app/routers/auth.py),
    does. Pet/clinic details collected here are exactly what the old local
    profile form collected, now persisted server-side instead.
    """
    existing = await dynamodb.get_by_gsi2(schema.email_gsi2pk(payload.email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists")

    owner_id = str(uuid4())
    item = {
        "PK": schema.owner_pk(owner_id),
        "SK": schema.OWNER_SK,
        "GSI2PK": schema.email_gsi2pk(payload.email),
        "GSI2SK": schema.ACCOUNT_GSI2SK,
        "role": "owner",
        "owner_id": owner_id,
        "owner_name": payload.owner_name,
        "email": payload.email,
        "phone": payload.phone,
        "password_hash": hash_password(payload.password),
        "pet_name": payload.pet_name,
        "species": payload.species,
        "breed": payload.breed,
        # DynamoDB's boto3 resource rejects native Python float — see the
        # identical conversion in app/routers/triage.py.
        "pet_age": Decimal(str(payload.pet_age)),
        "pet_weight": Decimal(str(payload.pet_weight)) if payload.pet_weight is not None else None,
        "vet_id": payload.vet_id,
        "clinic_name": payload.clinic_name,
    }
    await dynamodb.put_item(item)
    return OwnerRegisterResponse(account=item_to_owner_account(item))


@router.patch("/{owner_id}", response_model=OwnerAccount)
async def update_owner(owner_id: str, payload: OwnerUpdateRequest) -> OwnerAccount:
    """Backs the Profile edit modal — see web/dashboard.html. Email,
    password, and vet_id/clinic_name aren't editable here (identity and
    clinic assignment are fixed at registration)."""
    item = await dynamodb.get_item(schema.owner_pk(owner_id), schema.OWNER_SK)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Owner account not found")

    updated_item = {
        **item,
        "owner_name": payload.owner_name,
        "pet_name": payload.pet_name,
        "species": payload.species,
        "breed": payload.breed,
        "pet_age": Decimal(str(payload.pet_age)),
        "pet_weight": Decimal(str(payload.pet_weight)) if payload.pet_weight is not None else None,
    }
    await dynamodb.put_item(updated_item)
    return item_to_owner_account(updated_item)
