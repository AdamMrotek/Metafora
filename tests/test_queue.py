"""Dispatch with no database configured.

The claim degrades to yes and the mint is all that runs, which is what lets
`make dev` work on a laptop with nothing provisioned. The database half is
`tests/test_persistence.py`.
"""

import pytest

from services.core import db
from services.core.queue import UnknownInvitation, resolve_interview, synthetic_interview


def test_the_pure_suite_really_has_no_database():
    """`no_database` in conftest.py is load-bearing, not decorative: this repo's
    own `.env` names a real project, and `config.py` reads it at import."""
    assert db.enabled() is False


async def test_every_caller_gets_their_own_patient():
    """With no database there is no roster to draw from, so the mint is the
    whole answer and two visitors must not collide. The database path picks a
    seeded person instead — `tests/test_persistence.py`."""
    a, b = await resolve_interview(), await resolve_interview()

    assert a.id != b.id
    assert a.patient.id != b.patient.id


async def test_a_resolved_interview_names_a_protocol_that_exists():
    from services.agent.config.protocol import PROTOCOLS

    interview = await resolve_interview()
    assert interview.protocol_id in PROTOCOLS


def test_the_mint_is_usable_without_awaiting_anything():
    """`synthetic_interview` is sync so callers that only need the shape of an
    interview — the prompt tests, dev with no database — need no connection."""
    assert synthetic_interview().patient.first_name


async def test_a_token_is_refused_where_no_link_was_ever_issued():
    """With no database there is no `clinical.invitations`, so no token can be
    ours — and the refusal is what matters, because the alternative to refusing
    an unrecognised token is quietly running somebody else's interview.

    The honoured path needs a real table and is `tests/test_invitations.py`."""
    with pytest.raises(UnknownInvitation):
        await resolve_interview("some-token")
