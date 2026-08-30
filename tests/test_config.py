"""`REQUIRED_IN_PROD` must stay in step with `_problems()`, in both directions:
a check added without a list entry, and a list entry the check no longer reads.

Patches module globals rather than the environment — `config` verifies itself at
import, and `_load_dotenv` would fold a developer's `.env` into a cleared one.
"""

from __future__ import annotations

from unittest import mock

import pytest

from services.core import config

#: A good value per group, keyed by the group's first name.
GOOD = {
    "GROQ_API_KEY": "gsk_not_a_real_key",
    "LIVEKIT_API_KEY": "APIrealkey",
    "LIVEKIT_API_SECRET": "a-real-secret",
    "DATABASE_URL": "postgresql://user:pw@db.example.com:5432/postgres",
    "SUPABASE_URL": "https://project.supabase.co",
    "INVITE_SECRET": "8f14e45fceea167a5a36dedd4bea2543",
    "PORTAL_URL": "https://portal.example.com",
    "LIVEKIT_PUBLIC_URL": "wss://sfu.example.com",
}

#: How to break each group, and the name the problem must mention. The LiveKit
#: credentials move together: `_problems()` compares the pair, so half of it
#: would assert nothing.
BREAK = {
    "GROQ_API_KEY": ({"GROQ_API_KEY": ""}, "GROQ_API_KEY"),
    "LIVEKIT_API_KEY": (
        {"LIVEKIT_API_KEY": "devkey", "LIVEKIT_API_SECRET": "secret"},
        "LIVEKIT_API_KEY",
    ),
    "LIVEKIT_API_SECRET": (
        {"LIVEKIT_API_KEY": "devkey", "LIVEKIT_API_SECRET": "secret"},
        "LIVEKIT_API_SECRET",
    ),
    "DATABASE_URL": ({"DATABASE_URL": ""}, "DATABASE_URL"),
    "SUPABASE_URL": ({"SUPABASE_URL": ""}, "SUPABASE_URL"),
    "INVITE_SECRET": ({"INVITE_SECRET": config._DEV_INVITE_SECRET}, "INVITE_SECRET"),
    "PORTAL_URL": ({"PORTAL_URL": "http://localhost:5173"}, "PORTAL_URL"),
    "LIVEKIT_PUBLIC_URL": ({"LIVEKIT_PUBLIC_URL": "ws://localhost:7880"}, "LIVEKIT_PUBLIC_URL"),
}


def problems(**overrides: str) -> list[str]:
    """`_problems()` with good values for every listed name, then `overrides`.

    Unlisted names keep their dev defaults, so a new check on one shows up.
    """
    values = {group[0]: GOOD[group[0]] for group in config.REQUIRED_IN_PROD}
    values.update(overrides)
    with mock.patch.multiple(config, **values):
        return config._problems()


def test_every_listed_name_has_a_test_value():
    """A group added without a test value would otherwise fail as a KeyError."""
    for group in config.REQUIRED_IN_PROD:
        assert group[0] in GOOD, f"{group[0]} is in REQUIRED_IN_PROD but not in GOOD"


def test_list_is_complete():
    """Setting exactly the listed names is enough — so nothing is missing."""
    assert problems() == []


@pytest.mark.parametrize("name", list(BREAK))
def test_each_group_is_load_bearing(name):
    """Breaking one group makes `_problems()` say so — so nothing is stale."""
    overrides, expected = BREAK[name]
    found = problems(**overrides)
    assert found, f"breaking {name} produced no problem"
    assert any(expected in p for p in found), f"no problem names {expected}: {found}"


def test_livekit_public_url_falls_back_to_livekit_url():
    """`LIVEKIT_PUBLIC_URL` defaults to `LIVEKIT_URL`, so either alone is fine."""
    assert ("LIVEKIT_PUBLIC_URL", "LIVEKIT_URL") in config.REQUIRED_IN_PROD


def test_dev_verifies_nothing():
    """`_verify()` returns early in dev; the preflight skips for the same reason."""
    with mock.patch.object(config, "IS_DEV", True):
        config._verify()


def test_prod_refuses_a_default_environment():
    """A default value outside dev is refused at import."""
    with (
        mock.patch.object(config, "IS_DEV", False),
        mock.patch.object(config, "INVITE_SECRET", config._DEV_INVITE_SECRET),
        pytest.raises(config.ConfigError) as excinfo,
    ):
        config._verify()
    assert "not deployable" in str(excinfo.value)
    assert "INVITE_SECRET" in str(excinfo.value)
