"""The door, in plain `make test`.

Everything here is real except the network. An EC keypair is generated in a
fixture, the JWKS is served out of a dict, and the tokens are signed and
verified by the same PyJWT the deployment uses — so this exercises the actual
ES256 path rather than a stub of it, with no Supabase project and nothing to
configure.

What is being held is the list of ways in, and that all but one of them is shut:
no token, a token we cannot verify, a token that verified and names nobody. The
data behind the routes is `tests/test_reads.py` under the postgres marker; this
file is only about who gets through.
"""

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

from services.core import reads
from services.core.app import app
from shared import auth
from shared.auth import CurrentUser
from shared.auth.verify import InvalidToken

ISSUER = "https://project.supabase.co/auth/v1"
CLINICIAN = "clinician@example.test"

#: Every route that reads the record. Parametrised rather than tested one at a
#: time: "which routes are behind the door" is the property, and a route added
#: to this list is a route the whole file is asserted against.
GUARDED = ["/interviews", "/interviews/iv_anything", "/patients", "/me"]


# ─── a signing key, and a key set served from memory ─────────────────────────


@pytest.fixture(scope="module")
def signing_key():
    """One EC P-256 key, as Supabase's asymmetric signing keys are."""
    return ec.generate_private_key(ec.SECP256R1())


@pytest.fixture(scope="module")
def jwks_document(signing_key):
    import json

    from jwt.algorithms import ECAlgorithm

    entry = json.loads(ECAlgorithm.to_jwk(signing_key.public_key()))
    entry.update(kid="key-1", use="sig", alg="ES256")
    return {"keys": [entry]}


@pytest.fixture
def keys(jwks_document):
    """A real `Jwks` whose fetch returns the document above.

    The injection point is the whole reason `Jwks` takes a `fetch`: the caching,
    the `kid` lookup and the rate-limited refetch are the code under test, and a
    test that reached over the network would be testing Supabase instead.
    """
    fetched: list[str] = []

    async def fetch(url: str):
        fetched.append(url)
        return jwks_document

    keyset = auth.Jwks("https://project.supabase.co/jwks", fetch=fetch)
    keyset.fetches = fetched  # what the refetch tests count
    return keyset


def token(
    signing_key,
    *,
    email: str = CLINICIAN,
    sub: str = "auth-user-1",
    issuer: str = ISSUER,
    audience: str = "authenticated",
    expires_in: int = 3600,
    kid: str = "key-1",
    **claims,
) -> str:
    now = int(time.time())
    payload = {
        "sub": sub,
        "email": email,
        "aud": audience,
        "iss": issuer,
        "iat": now,
        "exp": now + expires_in,
        **claims,
    }
    return jwt.encode(payload, signing_key, algorithm="ES256", headers={"kid": kid})


# ─── an account directory, without a database ────────────────────────────────


class Directory:
    """`config.accounts` as a dict. `PostgresAccounts` — the `sub` binding and
    the query — is tested against a real table in `tests/test_reads.py`."""

    def __init__(self, accounts: dict[str, str] | None = None) -> None:
        self.accounts = accounts if accounts is not None else {CLINICIAN: "clinician"}

    async def resolve(self, claims) -> CurrentUser:
        role = self.accounts.get(claims.email)
        if role is None:
            raise auth.Denied(f"{claims.email} is not a Metafora account")
        return CurrentUser(
            email=claims.email, sub=claims.sub, role=role, display_name="Test Clinician"
        )


@pytest.fixture
def directory():
    return Directory()


@pytest.fixture
def door(keys, directory):
    """A configured backend: real verification, an account directory, no network."""
    auth.configure(auth.Authenticator(jwks=keys, issuer=ISSUER, accounts=directory))
    return directory


def _stale(keys) -> None:
    """Wind the refetch clock back past the throttle window.

    Rather than sleeping a minute: the throttle is a real property and the tests
    that assert it should not be the slow ones.
    """
    keys._last_fetch = None


def client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def get(path: str, bearer: str | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {bearer}"} if bearer else {}
    async with client() as http:
        return await http.get(path, headers=headers)


# ─── who is refused ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", GUARDED)
async def test_an_anonymous_caller_is_refused(door, path):
    """The roadmap's own acceptance line: every clinical route refuses a caller
    who brought nothing."""
    response = await get(path)

    assert response.status_code == 401
    # The portal's body shape, which the clinical routes inherit.
    assert response.json()["error"] == auth.UNAUTHENTICATED
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "header", ["", "Bearer", "Bearer   ", "Basic abc123", "token abc123", "abc123"]
)
async def test_anything_that_is_not_a_bearer_token_is_refused(door, header):
    async with client() as http:
        response = await http.get("/interviews", headers={"Authorization": header})
    assert response.status_code == 401


async def test_an_expired_token_is_refused(door, signing_key):
    response = await get("/interviews", token(signing_key, expires_in=-60))

    assert response.status_code == 401


async def test_a_token_from_another_project_is_refused(door, signing_key):
    """Same key, same shape, different issuer. Without the `iss` assertion this
    is a valid token — which is what makes it worth a test rather than a
    comment."""
    response = await get(
        "/interviews", token(signing_key, issuer="https://someone-else.supabase.co/auth/v1")
    )

    assert response.status_code == 401


async def test_a_token_for_another_audience_is_refused(door, signing_key):
    response = await get("/interviews", token(signing_key, audience="service_role"))

    assert response.status_code == 401


async def test_a_token_signed_by_a_key_we_do_not_publish_is_refused(door, signing_key):
    """A well-formed token signed by someone else's key. The signature check is
    what fails, and the caller is told nothing more than 401."""
    other = ec.generate_private_key(ec.SECP256R1())
    forged = jwt.encode(
        jwt.decode(token(signing_key), options={"verify_signature": False}),
        other,
        algorithm="ES256",
        headers={"kid": "key-1"},  # names a key we *do* have, and does not match it
    )

    assert (await get("/interviews", forged)).status_code == 401


async def test_a_token_asking_to_be_verified_with_no_algorithm_is_refused(door, signing_key):
    """`alg: none` is the oldest way in, and the closed algorithm list in
    `verify.py` is the only thing that refuses it."""
    unsigned = jwt.encode(
        jwt.decode(token(signing_key), options={"verify_signature": False}),
        key=None,
        algorithm="none",
        headers={"kid": "key-1"},
    )

    assert (await get("/interviews", unsigned)).status_code == 401


async def test_a_token_with_no_expiry_is_refused(door, signing_key, keys):
    """PyJWT does not require `exp` on its own — a token without one never
    expires, which is a credential nobody can revoke."""
    now = int(time.time())
    forever = jwt.encode(
        {
            "sub": "auth-user-1",
            "email": CLINICIAN,
            "aud": "authenticated",
            "iss": ISSUER,
            "iat": now,
        },
        signing_key,
        algorithm="ES256",
        headers={"kid": "key-1"},
    )

    assert (await get("/interviews", forever)).status_code == 401


async def test_garbage_is_refused_rather_than_raised(door):
    assert (await get("/interviews", "not-a-token")).status_code == 401


# ─── verified, and still not allowed in ──────────────────────────────────────


@pytest.mark.parametrize("path", GUARDED)
async def test_a_verified_stranger_is_told_why(door, signing_key, path):
    """Anyone may sign up to a Supabase project, so verifying a token is not the
    same as having a caseload. This is the difference, and it is a 403 with a
    reason because the caller *did* prove who they are — telling them their
    address is not on the list is safe, and it is the only useful next step."""
    response = await get(path, token(signing_key, email="stranger@example.test"))

    assert response.status_code == 403
    assert "stranger@example.test" in response.json()["error"]


async def test_a_role_that_does_not_read_the_record_is_refused(door, signing_key):
    """`require_role` is a set membership test, not a truthiness check on having
    an account at all."""
    door.accounts["billing@example.test"] = "billing"

    response = await get("/interviews", token(signing_key, email="billing@example.test"))

    assert response.status_code == 403
    assert "billing" in response.json()["error"]


async def test_an_admin_reads_the_record_too(door, signing_key, monkeypatch):
    door.accounts["boss@example.test"] = "admin"
    monkeypatch.setattr(reads, "interviews", _empty_list)

    response = await get("/interviews", token(signing_key, email="boss@example.test"))

    assert response.status_code == 200


# ─── who gets in ─────────────────────────────────────────────────────────────


async def _empty_list(user, **_kwargs):
    """Stands in for the query. What it returns is `tests/test_reads.py`'s
    subject; what matters here is that it was reached at all."""
    return []


async def test_a_seeded_clinician_gets_through(door, signing_key, monkeypatch):
    monkeypatch.setattr(reads, "interviews", _empty_list)

    response = await get("/interviews", token(signing_key))

    assert response.status_code == 200
    assert response.json() == []


async def test_a_clinician_is_told_who_they_are(door, signing_key):
    """`GET /me` reads no clinical data — it reports the identity `require_role`
    has already decided about, so the dashboard greets a person by name instead
    of deriving one from an email address."""
    response = await get("/me", token(signing_key))

    assert response.status_code == 200
    assert response.json() == {
        "email": CLINICIAN,
        "role": "clinician",
        "displayName": "Test Clinician",
    }


async def test_me_does_not_hand_back_the_auth_id(door, signing_key):
    """`sub` names the credential rather than the person, and a browser has no
    use for it. Asserted rather than assumed, because `CurrentUser` carries it
    and returning that object directly would have been the shorter route."""
    assert "sub" not in (await get("/me", token(signing_key))).json()


async def test_the_identity_reaches_the_query_not_just_the_door(door, signing_key, monkeypatch):
    """The habit the whole package is built around. A system that checks a role
    at the door and then runs an unscoped query has nowhere to put the scope
    when it needs one, and cannot be retrofitted route by route."""
    seen: list[CurrentUser] = []

    async def capture(user, **_kwargs):
        seen.append(user)
        return []

    monkeypatch.setattr(reads, "patients", capture)

    assert (await get("/patients", token(signing_key))).status_code == 200
    assert [u.email for u in seen] == [CLINICIAN]
    assert seen[0].role == "clinician"


async def test_the_patient_route_never_asks_for_a_credential(door):
    """Rule 3 of the system map, held in a test: a patient arrives with a link
    and holds no account credential. The split into routers exists so this stays
    true by construction, but construction is not an assertion."""
    async with client() as http:
        response = await http.post("/session/s_nope/typed", json={"text": "hello"})

    # 404 — it got past the door because there is no door, and failed on the
    # session id instead. Anything but 401/403 is the property.
    assert response.status_code == 404


# ─── a server that cannot verify anything ────────────────────────────────────


@pytest.mark.parametrize("path", GUARDED)
async def test_an_unconfigured_server_refuses_rather_than_admits(path, signing_key):
    """No `SUPABASE_URL` — `make dev` on a laptop. The refusal is the point: the
    one thing this must never be is an open door, and a 503 is also honest,
    because nothing here can verify anything."""
    auth.configure(None)

    response = await get(path, token(signing_key))

    assert response.status_code == 503
    assert response.json()["error"] == auth.UNCONFIGURED


async def test_an_unconfigured_server_says_so_even_to_a_good_token(signing_key):
    """Answering 401 would send a clinician off to fix a credential that was
    never the problem. The server is what is missing, and it says so."""
    auth.configure(None)

    assert (await get("/interviews", token(signing_key))).status_code == 503


async def test_a_server_with_nowhere_to_look_an_account_up_says_so(keys, signing_key):
    """`SUPABASE_URL` set and no database. Answering 403 would tell a clinician
    with a perfectly good account that they do not have one, and send them off
    to fix the one thing that is not broken."""

    class NoDirectory:
        async def resolve(self, _claims):
            raise auth.DirectoryUnavailable("no database")

    auth.configure(auth.Authenticator(jwks=keys, issuer=ISSUER, accounts=NoDirectory()))

    response = await get("/interviews", token(signing_key))

    assert response.status_code == 503
    assert response.json()["error"] == auth.UNCONFIGURED


async def test_health_still_answers_and_says_whether_the_door_is_installed():
    """The Fly check must never need a credential, and an operator needs to know
    whether this box can answer a clinician at all."""
    auth.configure(None)

    async with client() as http:
        body = (await http.get("/health")).json()

    assert body["ok"] is True
    assert body["auth"] is False


# ─── the key set ─────────────────────────────────────────────────────────────


async def test_the_keys_are_fetched_once_and_then_cached(keys, signing_key):
    """Three tokens, one fetch. A round trip to the auth endpoint per request
    would put Supabase in the path of every read the dashboard makes."""
    await keys.warm()
    for _ in range(3):
        await auth.verify(token(signing_key), keys=keys, issuer=ISSUER)

    assert len(keys.fetches) == 1


async def test_an_unknown_kid_goes_and_looks_again(keys, signing_key):
    """Rotation without a redeploy is the whole reason for choosing JWKS over a
    shared secret, and this is the mechanism: a `kid` we have never seen is what
    a rotation looks like from here, so it sends us back to the endpoint."""
    await keys.warm()
    _stale(keys)

    with pytest.raises(InvalidToken):
        await auth.verify(token(signing_key, kid="rotated"), keys=keys, issuer=ISSUER)

    assert len(keys.fetches) == 2


async def test_an_unknown_kid_moments_after_a_fetch_does_not_send_us_back(keys, signing_key):
    """The throttle counts the warm, because the warm is going and looking. A
    `kid` missing from keys fetched a second ago is overwhelmingly a token that
    was never ours, not a rotation nobody has published yet."""
    await keys.warm()

    with pytest.raises(InvalidToken):
        await auth.verify(token(signing_key, kid="rotated"), keys=keys, issuer=ISSUER)

    assert len(keys.fetches) == 1


async def test_a_stream_of_unknown_kids_does_not_hammer_the_auth_endpoint(keys, signing_key):
    """The other thing that produces an unknown `kid` is someone feeding us
    tokens signed by keys we have never seen. Refetching per request would make
    that an amplifier pointed at Supabase — and the amplifier is what the
    throttle exists for, so it is asserted with the window open, not shut."""
    await keys.warm()

    for i in range(20):
        _stale(keys)
        with pytest.raises(InvalidToken):
            await auth.verify(token(signing_key, kid=f"junk-{i}"), keys=keys, issuer=ISSUER)

    # Twenty requests, and the throttle is what makes it fewer than twenty
    # fetches — each one only got through because the test wound the clock back.
    assert len(keys.fetches) == 21


async def test_the_keys_already_held_survive_a_refetch_that_fails(keys, signing_key):
    """An auth endpoint having a bad minute must not log every clinician out.
    The keys in hand are still valid; only a rotation is unreachable."""
    await keys.warm()
    _stale(keys)

    async def broken(_url):
        raise ConnectionError("auth endpoint is unreachable")

    keys._fetch = broken
    with pytest.raises(InvalidToken):
        await auth.verify(token(signing_key, kid="rotated"), keys=keys, issuer=ISSUER)

    keys._fetch = _unreachable
    claims = await auth.verify(token(signing_key), keys=keys, issuer=ISSUER)
    assert claims.email == CLINICIAN


async def _unreachable(_url):
    raise AssertionError("a cached key must not send us back to the endpoint")


async def test_a_rotated_key_is_picked_up_without_a_restart(jwks_document, signing_key):
    """The refetch is the feature. A key published after this process booted
    verifies on the request that first names it."""
    import json

    from jwt.algorithms import ECAlgorithm

    rotated = ec.generate_private_key(ec.SECP256R1())
    published = {"keys": list(jwks_document["keys"])}

    async def fetch(_url: str):
        return published

    keys = auth.Jwks("https://project.supabase.co/jwks", fetch=fetch, min_refetch_interval_s=0)
    await keys.warm()

    entry = json.loads(ECAlgorithm.to_jwk(rotated.public_key()))
    entry.update(kid="key-2", use="sig", alg="ES256")
    published["keys"].append(entry)

    claims = await auth.verify(
        token(rotated, kid="key-2"), keys=keys, issuer=ISSUER
    )
    assert claims.email == CLINICIAN


async def test_the_email_is_lowered_so_one_mailbox_is_one_account(door, signing_key, monkeypatch):
    """Supabase treats addresses case-insensitively and the seed is written in
    lower case. Matching on the raw claim would make `Clinician@` a stranger."""
    seen: list[str] = []

    async def capture(user, **_kwargs):
        seen.append(user.email)
        return []

    monkeypatch.setattr(reads, "interviews", capture)

    mixed_case = token(signing_key, email="CLINICIAN@Example.Test")

    assert (await get("/interviews", mixed_case)).status_code == 200
    assert seen == [CLINICIAN]
