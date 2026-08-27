"""What a role is and what it grants — the one place an authorisation decision lives.

Deliberately knows nothing about this repo's services. It is handed its issuer,
its key source and its account directory by whoever boots the process
(`services/core/app.py`'s `lifespan`), which is what keeps `shared/` a base
layer rather than a second copy of the application.

`frontend/call` does not use any of this, and must not: a patient arrives with a
link and never holds a credential (system-map §3, rule 3).
"""

from shared.auth.accounts import (
    AccountSource,
    CurrentUser,
    Denied,
    DirectoryUnavailable,
    PostgresAccounts,
)
from shared.auth.deps import (
    READS_THE_RECORD,
    UNAUTHENTICATED,
    UNCONFIGURED,
    Authenticator,
    ClinicalReader,
    configure,
    configured,
    current_user,
    require_role,
)
from shared.auth.jwks import Jwks, UnknownKey
from shared.auth.verify import Claims, InvalidToken, verify

__all__ = [
    "READS_THE_RECORD",
    "UNAUTHENTICATED",
    "UNCONFIGURED",
    "AccountSource",
    "Authenticator",
    "Claims",
    "ClinicalReader",
    "CurrentUser",
    "Denied",
    "DirectoryUnavailable",
    "InvalidToken",
    "Jwks",
    "PostgresAccounts",
    "UnknownKey",
    "configure",
    "configured",
    "current_user",
    "require_role",
    "verify",
]
