"""The clinician's read of the record.

Two routes, both behind `require_role`, and both handing the identity on to
`reads.py` rather than stopping at the door with it.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from services.core import reads
from shared.auth import READS_THE_RECORD, ClinicalReader, require_role
from shared.contracts.models import InterviewDetail, InterviewSummary

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


@router.get("/interviews")
async def list_interviews(
    user: ClinicalReader,
    limit: int = Query(default=reads.DEFAULT_LIMIT, ge=1, le=reads.MAX_LIMIT),
) -> list[InterviewSummary]:
    """The review table: status, outcome, patient first name, protocol, times."""
    return await reads.interviews(user, limit=limit)


@router.get("/interviews/{interview_id}")
async def get_interview(
    interview_id: str,
    user: ClinicalReader,
) -> InterviewDetail:
    """One interview, its results, and its transcript inline in `seq` order —
    **including the safety scans that matched nothing**. The dashboard's detail
    screen is specified on those, and a route that dropped them could not be
    un-dropped from the UI."""
    try:
        return await reads.interview(user, interview_id)
    except reads.NotFound as exc:
        # 404 rather than 403 for an interview belonging to someone else: a 403
        # would confirm the id exists, which is most of what a fishing caller
        # wanted to learn.
        raise HTTPException(404, f"no interview {interview_id}") from exc
