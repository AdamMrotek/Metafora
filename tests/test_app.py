"""The application itself — `services/core/app.py`.

What is left here once the routes are `test_session_routes.py`'s and the call
is `test_lifecycle.py`'s: the Sentry `before_send` filter, which is the only
thing in `app.py` that is neither wiring nor a route, and `/config`, which is
the only route in it — both belong to the process rather than to an audience.

Invariant 3 says nothing medical goes to telemetry. The back door into that is
a stack frame from the pipeline, whose locals are a transcript, so the filter
is load-bearing and both of its directions are held: what it must drop, and
what it must not.
"""

import httpx

from services.core import app as app_module
from services.core.app import _drop_agent_events


def test_nothing_raised_inside_the_agent_is_reported():
    """Invariant 3 — nothing medical goes to telemetry — arriving through the
    back door is a stack frame from the pipeline, whose locals are a transcript.
    """
    event = {
        "exception": {
            "values": [
                {
                    "stacktrace": {
                        "frames": [
                            {"module": "services.core.routes.session"},
                            {"module": "services.agent.pipeline"},
                        ]
                    }
                }
            ]
        }
    }
    assert _drop_agent_events(event, {}) is None


def test_an_agent_frame_is_caught_by_path_when_the_module_is_missing():
    event = {
        "exception": {
            "values": [
                {"stacktrace": {"frames": [{"abs_path": "/app/services/agent/tts.py"}]}}
            ]
        }
    }
    assert _drop_agent_events(event, {}) is None


def test_a_failure_in_the_http_layer_is_still_reported():
    """The filter must not be a way of reporting nothing at all."""
    event = {
        "exception": {
            "values": [{"stacktrace": {"frames": [{"module": "services.core.db"}]}}]
        }
    }
    assert _drop_agent_events(event, {}) is event


def test_an_event_with_no_stack_survives():
    """`logger.error` with no exception has no frames, and dropping those would
    silently discard most of what is worth seeing."""
    event = {"message": "pool exhausted"}
    assert _drop_agent_events(event, {}) is event


# ─── `/config` ───────────────────────────────────────────────────────────────


async def get_config() -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app_module.app), base_url="http://testserver"
    ) as http:
        return await http.get("/config")


async def test_the_browser_is_handed_the_project_and_the_anon_key(monkeypatch):
    """The dashboard holds one piece of configuration — where `/api` goes — and
    reads the rest from here, so rotating the publishable key is a secret change and
    not a rebuild of a static site."""
    monkeypatch.setattr(app_module, "SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setattr(app_module, "SUPABASE_PUBLISHABLE_KEY", "sb_publishable_abc")

    response = await get_config()

    assert response.status_code == 200
    assert response.json() == {
        "supabaseUrl": "https://project.supabase.co",
        "supabasePublishableKey": "sb_publishable_abc",
    }


async def test_it_needs_no_credential(monkeypatch):
    """It is the thing you read in order to authenticate, so requiring
    authentication for it would be a loop with no way in."""
    monkeypatch.setattr(app_module, "SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setattr(app_module, "SUPABASE_PUBLISHABLE_KEY", "sb_publishable_abc")

    assert (await get_config()).status_code == 200


async def test_an_unconfigured_server_refuses_with_a_sentence(monkeypatch):
    """`make dev` on a laptop with no project. A 503 the dashboard can render,
    rather than an empty string it would try to sign in against."""
    monkeypatch.setattr(app_module, "SUPABASE_URL", "")
    monkeypatch.setattr(app_module, "SUPABASE_PUBLISHABLE_KEY", "")

    response = await get_config()

    assert response.status_code == 503
    assert response.json()["error"] == "this server has no sign-in configured"


async def test_half_configured_is_also_a_refusal(monkeypatch):
    """A project with no publishable key cannot sign anyone in, and handing the browser
    an empty key would fail later and further away."""
    monkeypatch.setattr(app_module, "SUPABASE_URL", "https://project.supabase.co")
    monkeypatch.setattr(app_module, "SUPABASE_PUBLISHABLE_KEY", "")

    assert (await get_config()).status_code == 503
