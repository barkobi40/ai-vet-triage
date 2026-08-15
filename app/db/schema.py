"""
Single-table design key helpers for the vet-triage DynamoDB table.

Primary key:
    PK = TRIAGE#{triage_id}        SK = METADATA
Access pattern: fetch one triage case by id. (Using a fixed SK, rather than
just a bare PK, leaves room to later store related items under the same
partition, e.g. TRIAGE#{id} / EVENT#{timestamp} for an audit trail.)

GSI1 (priority queue index):
    GSI1PK = PRIORITY#{priority}   GSI1SK = CREATED_AT#{iso_timestamp}
Access pattern: clinic dashboard queries "all RED cases, newest first" via
Query(GSI1PK == "PRIORITY#RED", ScanIndexForward=False).

Records are created with GSI1PK = PRIORITY#PENDING before the AI worker has
produced a classification, so they still surface in an "awaiting triage"
dashboard view. The worker later updates GSI1PK to the real priority
(RED/YELLOW/GREEN) once the AI assessment completes.

Vet directory (a second entity type in the same table, same GSI1 — this is
the point of single-table design: distinct item types are told apart by
their own PK/SK and GSI key *values*, not by separate tables or indexes):
    PK = VET#{vet_id}              SK = METADATA
    GSI1PK = VET_DIRECTORY         GSI1SK = CLINIC#{clinic_name}#{vet_id}
Access pattern: "list every registered vet/clinic" for the pet owner
dashboard's clinic-selection dropdown, via
Query(GSI1PK == "VET_DIRECTORY"), sorted alphabetically by clinic name.
The vet_id suffix on GSI1SK keeps keys unique even if two clinics share a
name — DynamoDB requires GSI1PK+GSI1SK to be unique together.

Owner accounts (a third entity type, same table):
    PK = OWNER#{owner_id}          SK = METADATA

GSI2 (account-by-email index — shared by both vet and owner accounts, the
only two entity types with a login/email at all):
    GSI2PK = EMAIL#{lowercased_email}   GSI2SK = ACCOUNT
Access pattern: unified login (see app/routers/auth.py) looks up "the
account with this email" via Query(GSI2PK == "EMAIL#...") regardless of
whether it's a vet or an owner, then checks the item's own "role"
attribute to know which. Email is treated as globally unique across both
roles (one account per email either way), so this is a single-item
lookup, not a paginated list like GSI1's directory/priority queries.
"""

GSI1_NAME = "GSI1"
GSI2_NAME = "GSI2"
TRIAGE_SK = "METADATA"
VET_SK = "METADATA"
OWNER_SK = "METADATA"
VET_DIRECTORY_GSI1PK = "VET_DIRECTORY"
ACCOUNT_GSI2SK = "ACCOUNT"


def triage_pk(triage_id: str) -> str:
    return f"TRIAGE#{triage_id}"


def priority_gsi1pk(priority: str) -> str:
    return f"PRIORITY#{priority}"


def created_at_gsi1sk(iso_timestamp: str) -> str:
    return f"CREATED_AT#{iso_timestamp}"


def vet_pk(vet_id: str) -> str:
    return f"VET#{vet_id}"


def clinic_gsi1sk(clinic_name: str, vet_id: str) -> str:
    return f"CLINIC#{clinic_name}#{vet_id}"


def owner_pk(owner_id: str) -> str:
    return f"OWNER#{owner_id}"


def email_gsi2pk(email: str) -> str:
    return f"EMAIL#{email.strip().lower()}"
