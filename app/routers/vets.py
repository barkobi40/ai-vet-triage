from uuid import uuid4

from fastapi import APIRouter, status

from app.db import dynamodb, schema
from app.models.vet import VetListItem, VetListResponse, VetRegisterRequest, VetRegisterResponse

router = APIRouter(prefix="/vets", tags=["vets"])


@router.post("/register", response_model=VetRegisterResponse, status_code=status.HTTP_201_CREATED)
async def register_vet(payload: VetRegisterRequest) -> VetRegisterResponse:
    """
    Adds a vet/clinic to the shared directory so pet owners (on any
    browser, not just the one that registered it) can select it when
    submitting a case — see web/dashboard.html's clinic dropdown. This is
    deliberately open/unauthenticated: anyone can add a directory entry,
    and there's no password here at all (see VetRegisterRequest). The vet
    portal's own "login" (app/routers/ws.py-adjacent client JS) stays a
    client-side demo lookup by username against this same directory.
    """
    vet_id = str(uuid4())
    await dynamodb.put_item(
        {
            "PK": schema.vet_pk(vet_id),
            "SK": schema.VET_SK,
            "GSI1PK": schema.VET_DIRECTORY_GSI1PK,
            "GSI1SK": schema.clinic_gsi1sk(payload.clinic_name, vet_id),
            "vet_id": vet_id,
            "vet_name": payload.vet_name,
            "clinic_name": payload.clinic_name,
            "clinic_address": payload.clinic_address,
            "phone": payload.phone,
            "license_number": payload.license_number,
            "username": payload.username,
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
