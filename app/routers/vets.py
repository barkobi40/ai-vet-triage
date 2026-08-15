from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from app.db import dynamodb, schema
from app.models.vet import VetListItem, VetListResponse, VetRegisterRequest, VetRegisterResponse
from app.services.security import hash_password

router = APIRouter(prefix="/vets", tags=["vets"])


@router.post("/register", response_model=VetRegisterResponse, status_code=status.HTTP_201_CREATED)
async def register_vet(payload: VetRegisterRequest) -> VetRegisterResponse:
    """
    Adds a vet/clinic to the shared directory so pet owners (on any
    browser, not just the one that registered it) can select it when
    submitting a case — see web/dashboard.html's clinic dropdown — and
    creates a real login account for it (see app/routers/auth.py). The
    directory listing itself (GET /vets below) stays open/unauthenticated
    on purpose — anyone can see which clinics exist, same as a real
    clinic directory would be public; only the password is ever secret,
    and it's hashed before being stored (see app/services/security.py),
    never kept or logged in plaintext.
    """
    existing = await dynamodb.get_by_gsi2(schema.email_gsi2pk(payload.email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists")

    vet_id = str(uuid4())
    await dynamodb.put_item(
        {
            "PK": schema.vet_pk(vet_id),
            "SK": schema.VET_SK,
            "GSI1PK": schema.VET_DIRECTORY_GSI1PK,
            "GSI1SK": schema.clinic_gsi1sk(payload.clinic_name, vet_id),
            "GSI2PK": schema.email_gsi2pk(payload.email),
            "GSI2SK": schema.ACCOUNT_GSI2SK,
            "role": "vet",
            "vet_id": vet_id,
            "vet_name": payload.vet_name,
            "clinic_name": payload.clinic_name,
            "clinic_address": payload.clinic_address,
            "phone": payload.phone,
            "license_number": payload.license_number,
            "username": payload.username,
            "email": payload.email,
            "password_hash": hash_password(payload.password),
        }
    )
    return VetRegisterResponse(vet_id=vet_id)


@router.get("", response_model=VetListResponse)
async def list_vets() -> VetListResponse:
    """Powers the pet owner dashboard's "Select Your Veterinarian / Clinic"
    dropdown. Sorted alphabetically by clinic name via GSI1SK — see
    app/db/schema.py for why the vet directory shares GSI1 with the triage
    priority-queue index rather than needing its own."""
    items = await dynamodb.query_gsi1(schema.VET_DIRECTORY_GSI1PK)
    return VetListResponse(
        vets=[
            VetListItem(
                vet_id=item["vet_id"],
                vet_name=item["vet_name"],
                clinic_name=item["clinic_name"],
                clinic_address=item["clinic_address"],
                phone=item["phone"],
                license_number=item["license_number"],
                username=item["username"],
            )
            for item in items
        ]
    )
