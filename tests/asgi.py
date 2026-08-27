"""The ASGI client the two HTTP-surface suites share.

`tests/fakes.py` removes the SFU; this removes the network. Together they are
why `test_session_routes.py` and `test_lifecycle.py` run with no key, no
LiveKit and no socket — the app is called in-process, and the caller's address
is something we choose rather than something a kernel assigns.
"""

import httpx

from services.core.app import app


def client(ip: str = "203.0.113.7") -> httpx.AsyncClient:
    """An ASGI client with a stable caller identity, so the per-IP limiter can
    be exercised without inventing a network.
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(ip, 4242)),
        base_url="http://testserver",
    )


async def start(http: httpx.AsyncClient) -> httpx.Response:
    return await http.post("/session")
