"""The patient-experience panel.

A clinician's read like the other three, and scoped like them — `reads.py` takes
the identity and joins through `clinical.patients`, so a sentiment reaches the
same door as the record it belongs to. It is not a clinical fact, which is
exactly why it would have been easy to leave unscoped.

Nothing writes `metrics.experience_responses`; the rows are a migration's seed.
The route exists anyway because the shape of the read is the part worth having
right, and the day a survey exists only its writer is new.
"""

from fastapi import APIRouter, Depends

from services.core import reads
from shared.auth import READS_THE_RECORD, ClinicalReader, require_role
from shared.contracts.models import ExperienceRange, ExperienceSummary

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


@router.get("/experience")
async def get_experience(
    user: ClinicalReader,
    range: ExperienceRange = "week",
) -> ExperienceSummary:
    """Responses per day for the chosen window, plus the caption naming it.

    `range` is a `Literal`, so an unknown one is a 422 from the validator rather
    than a branch here. The window ends at the newest response rather than at
    `now()` — see `reads.experience`.
    """
    return await reads.experience(user, range)
