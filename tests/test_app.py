"""The application itself — `services/core/app.py`.

What is left here once the routes are `test_session_routes.py`'s and the call
is `test_lifecycle.py`'s: the Sentry `before_send` filter, which is the only
thing in `app.py` that is neither wiring nor a route. It needs no client and no
fakes — it is a function over an event dict.

Invariant 3 says nothing medical goes to telemetry. The back door into that is
a stack frame from the pipeline, whose locals are a transcript, so the filter
is load-bearing and both of its directions are held: what it must drop, and
what it must not.
"""

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
