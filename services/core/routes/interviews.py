"""The clinician's read of the record, and the two writes that queue a call.

Five routes, all behind `require_role`, and all handing the identity on to
`reads.py` or `dispatch.py` rather than stopping at the door with it. The
writes live here rather than in a router of their own so that they inherit the
same standing guard the reads do: a file split by *audience* keeps that
property, and a file split by verb would not.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from services.agent.config.protocol import PROTOCOLS
from services.core import dispatch, invitations, reads
from services.core.db import pool
from shared.auth import READS_THE_RECORD, ClinicalReader, require_role
from shared.contracts.models import (
    DispatchRequest,
    InterviewDetail,
    InterviewPage,
    InterviewSort,
    InterviewSummary,
    Invitation,
    Overview,
    ProtocolOption,
)

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
    sort: InterviewSort = "urgency",
    q: str | None = Query(default=None, max_length=120),
    protocol: str | None = Query(default=None, max_length=120),
    page: int = Query(default=0, ge=0),
    limit: int = Query(default=reads.DEFAULT_LIMIT, ge=1, le=reads.MAX_LIMIT),
) -> InterviewPage:
    """One page of the review table, ordered and filtered by the database.

    Every parameter used to be a decision the browser made about a hundred rows
    it had already been given. That was workable while the record was small and
    silently wrong once it was not — a caller cannot triage a window it cannot
    see past — so the sort, the search, the protocol filter and the page all
    happen where the whole record is, and `total` comes back with the rows.

    `sort` is a `Literal`, so an unknown one is a 422 from the validator rather
    than a branch here.
    """
    return await reads.interviews(
        user, sort=sort, search=q, protocol_id=protocol, page=page, limit=limit
    )


@router.get("/overview")
async def get_overview(user: ClinicalReader) -> Overview:
    """The three tiles, the escalation band, what is still out, and the protocol
    filter's options — every dashboard number that is about the whole caseload
    rather than about one page of it.

    A separate route rather than fields on the page above, because these do not
    change when a clinician sorts a table or types in its search box, and a
    dashboard that recounted its escalations on every keystroke would be
    spending a query to redraw a number that cannot have moved.
    """
    return await reads.overview(user)


@router.get("/patients/{patient_id}/interviews")
async def patient_history(patient_id: str, user: ClinicalReader) -> list[InterviewSummary]:
    """One patient's interviews, oldest first — scoped like everything else.

    The id arrives from a row the caller was already shown, and a route that
    treated that as permission would be one guessed id away from reading
    somebody else's history.
    """
    return await reads.history(user, patient_id)


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


@router.get("/protocols")
async def list_protocols(_user: ClinicalReader) -> list[ProtocolOption]:
    """What the composer's *what* field offers.

    Read from `PROTOCOLS` rather than from `config.protocols`, because that
    table is seeded from this dict on every boot and a protocol nothing has run
    yet still has to be dispatchable — a fresh database has no interviews, so a
    list derived from the review table would be empty exactly when it is needed.
    """
    return [ProtocolOption(id=p.id, label=p.label) for p in PROTOCOLS.values()]


@router.post("/interviews", status_code=201)
async def dispatch_interview(
    body: DispatchRequest,
    user: ClinicalReader,
) -> InterviewSummary:
    """Queue a call for someone. Returns the row the review table draws for it.

    No link is minted here. Creating the interview and issuing the invitation
    are separate acts because they have different lifetimes — a queued call
    outlives the link that was sent for it, and a link can be reissued without
    the call being re-queued.
    """
    try:
        return await dispatch.create_interview(user, body)
    except dispatch.Refused as exc:
        raise HTTPException(400, str(exc)) from exc
    except reads.NotFound as exc:
        # A patient outside this caller's scope, answered exactly as a read
        # outside it is: 403 would confirm the id names somebody.
        raise HTTPException(404, f"no patient {body.patient_id}") from exc


@router.post("/interviews/{interview_id}/invitation")
async def mint_invitation(
    interview_id: str,
    user: ClinicalReader,
) -> Invitation:
    """The link for this interview — the live one if there is one, else a new one.

    The scope check is the read: `reads.summary` refuses an interview this
    caller may not see, so a clinician cannot mint a working link into someone
    else's caseload. Reused rather than re-minted on a second click, because the
    first URL may already be in a patient's inbox — see `invitations.py`.
    """
    try:
        await reads.summary(user, interview_id)
    except reads.NotFound as exc:
        raise HTTPException(404, f"no interview {interview_id}") from exc

    live = pool()
    if live is None:
        raise HTTPException(503, "this deployment has no database, so it issues no links")
    return await invitations.mint(live, interview_id)
