"""The project's public signing keys.

Asymmetric verification is the whole reason this file exists. A shared secret
would be one string in Fly secrets that both signs and verifies, and rotating it
would mean a redeploy timed against a Supabase setting change. Public keys
fetched from the project mean the secret never leaves Supabase and a rotation is
something this process discovers on the next unknown `kid`.

So the refetch below is the feature, not a fallback. It is rate-limited because
the other thing that produces an unknown `kid` is someone handing us tokens
signed by a key we have never seen, and that must not become a way to make us
hammer the auth endpoint.

And a refetch that fails is not an error the caller sees. Every key already held
is still valid, so an unreachable auth endpoint costs a rotation — not every
clinician who is already signed in, and never a 500 where a refusal belongs.
"""

import asyncio
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

from jwt import PyJWK

#: How often the key set may be fetched at most. Rotation is measured in months
#: and a real one arrives with a `kid` we do not have, so a minute of staleness
#: costs one round of 401s; anything shorter is an amplifier.
MIN_REFETCH_INTERVAL_S = 60.0

#: A JWKS endpoint that hangs must not hang a request holding a bearer token.
FETCH_TIMEOUT_S = 5.0


class UnknownKey(LookupError):
    """The token names a signing key this project does not publish."""


async def _fetch_over_http(url: str) -> dict[str, Any]:
    import httpx

    async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S) as http:
        response = await http.get(url)
        response.raise_for_status()
        return response.json()


class Jwks:
    """The key set, cached by `kid`.

    `fetch` is injectable so the auth suite can serve a key set from memory: an
    EC keypair in a fixture is the whole of what the tests need, and a test that
    reached the network would be testing Supabase.
    """

    def __init__(
        self,
        url: str,
        *,
        fetch: Callable[[str], Awaitable[dict[str, Any]]] = _fetch_over_http,
        min_refetch_interval_s: float = MIN_REFETCH_INTERVAL_S,
    ) -> None:
        self._url = url
        self._fetch = fetch
        self._min_interval = min_refetch_interval_s
        self._keys: dict[str, PyJWK] = {}
        self._last_fetch: float | None = None
        self._lock = asyncio.Lock()

    async def warm(self) -> None:
        """Fetch once at boot, so the first clinician of the morning does not
        pay for it — and so a misconfigured `SUPABASE_URL` is a line in the
        startup log rather than a 401 nobody can explain."""
        await self._refresh(force=True)

    async def key(self, kid: str) -> PyJWK:
        key = self._keys.get(kid)
        if key is not None:
            return key
        await self._refresh()
        key = self._keys.get(kid)
        if key is None:
            raise UnknownKey(kid)
        return key

    async def _refresh(self, *, force: bool = False) -> None:
        async with self._lock:
            now = time.monotonic()
            if (
                not force
                and self._last_fetch is not None
                and now - self._last_fetch < self._min_interval
            ):
                return
            # Stamped before the fetch, not after, so a failing endpoint is
            # throttled exactly like a succeeding one — a JWKS host having a bad
            # minute must not be retried on every request that arrives during it.
            self._last_fetch = now
            try:
                document = await self._fetch(self._url)
            except Exception as exc:
                if force:
                    # Boot. The caller wants the reason in the startup log.
                    raise
                # Mid-request. The keys already in hand are still valid, so an
                # unreachable endpoint costs us a rotation, not every signed-in
                # clinician — and the caller gets a refusal rather than a 500.
                print(f"[jwks] could not refresh {self._url}: {exc}", file=sys.stderr)
                return

        # Outside the lock: parsing is pure, and a malformed entry must not cost
        # us the keys we already hold.
        parsed: dict[str, PyJWK] = {}
        for entry in document.get("keys", []):
            kid = entry.get("kid")
            if not kid:
                continue
            try:
                parsed[kid] = PyJWK(entry)
            except Exception:  # noqa: BLE001 — one bad key is not all of them
                continue
        if parsed:
            self._keys = parsed
