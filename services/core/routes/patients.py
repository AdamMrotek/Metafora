"""The caller's own caseload.

Scoped by `clinical.patients.clinician_email`, which is the column that exists
so "own" is a `where` clause rather than a migration run under a live dashboard.
"""

from fastapi import APIRouter, Depends, Query

from services.core import reads
from shared.auth import READS_THE_RECORD, ClinicalReader, require_role
from shared.contracts.models import PatientSummary

# The guard is declared twice on purpose. On the router it is the standing
# guarantee — a route added to this file tomorrow is behind it whether or not
# whoever adds it remembers. On the handler it is how the identity arrives, so
# it can be handed to the query. FastAPI caches the sub-dependency that does the
# work (verify, then the `config.accounts` lookup), so the second is a set
# membership test and not a second round trip.
router = APIRouter(
    tags=["clinical"],
    dependencies=[Depends(require_role(*READS_THE_RECORD))],
)


@router.get("/patients")
async def list_patients(
    user: ClinicalReader,
    limit: int = Query(default=reads.DEFAULT_LIMIT, ge=1, le=reads.MAX_LIMIT),
) -> list[PatientSummary]:
    """This clinician's list plus the unowned demo rows, each carrying `origin`
    so a demo visitor is distinguishable from a dispatched patient."""
    return await reads.patients(user, limit=limit)
