"""The HTTP routes, one module per audience.

`session` is the patient's and is unauthenticated by design — a patient arrives
with a link and never holds a credential. `interviews` and `patients` are the
clinician's and every route on them is behind `require_role`.

That is the whole reason for the split: the two audiences have opposite auth
postures, and a file that mixes them is a file where the next route lands on the
wrong side of the line by accident.
"""

from services.core.routes import interviews, patients, session

__all__ = ["interviews", "patients", "session"]
