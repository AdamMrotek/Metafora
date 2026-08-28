"""Who the caller is, according to `config.accounts`.

The one clinical route that reads no clinical data. It exists because the
dashboard has to greet a person by name and knows nothing about the account
directory — and because the alternative is a frontend that derives a display
name from an email address and a role from whichever request happened to
succeed. The second of those is a client-side authorisation check waiting to be
written.

Nothing here decides anything. `require_role` has already decided; this only
reports the identity it decided about.
"""

from fastapi import APIRouter, Depends

from shared.auth import READS_THE_RECORD, ClinicalReader, require_role
from shared.contracts.models import Account

# Guarded like the other two clinical routers, and for the same reason: the
# standing guarantee is on the router so a route added here tomorrow inherits
# it, and the annotation on the handler is how the identity arrives.
router = APIRouter(
    tags=["clinical"],
    dependencies=[Depends(require_role(*READS_THE_RECORD))],
)


@router.get("/me")
async def whoami(user: ClinicalReader) -> Account:
    """The caller's own row. Deliberately not `CurrentUser` on the wire: `sub`
    is the Supabase auth id, it names the credential rather than the person, and
    a browser has no use for it."""
    return Account(email=user.email, role=user.role, display_name=user.display_name)
