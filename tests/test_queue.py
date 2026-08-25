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
    """Everyone being Alice was fine while the record evaporated. It stopped
    being fine when the rows started to persist (deployment.md §4, blocker 6)."""
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


async def test_a_link_is_refused_until_links_are_issued():
    """Phase 5 fills this in. Until then a token is a thing we do not honour,
    and saying so is better than quietly running someone else's interview."""
    with pytest.raises(UnknownInvitation):
        await resolve_interview("some-token")
