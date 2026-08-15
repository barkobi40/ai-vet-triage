from pydantic import BaseModel, Field


class VetRegisterRequest(BaseModel):
    """Payload for adding a vet/clinic to the directory. Deliberately has no
    password field — this is an open, unauthenticated demo directory (see
    app/routers/vets.py), consistent with the rest of the vet portal's
    client-side-only "auth." Storing even a demo password server-side would
    contradict that and risk being mistaken for real credential handling."""

    vet_name: str = Field(..., min_length=1, max_length=200)
    clinic_name: str = Field(..., min_length=1, max_length=200)
    clinic_address: str = Field(..., min_length=1, max_length=300)
    phone: str = Field(..., min_length=1, max_length=50)
    license_number: str = Field(..., min_length=1, max_length=100)
    username: str = Field(..., min_length=1, max_length=100)


class VetRegisterResponse(BaseModel):
    vet_id: str


class VetListItem(BaseModel):
    vet_id: str
    vet_name: str
    clinic_name: str
    clinic_address: str
    phone: str
    license_number: str
    username: str


class VetListResponse(BaseModel):
    vets: list[VetListItem]
