"""Staff authentication.

The check lives here, on the server, because a password checked in the browser
is not a check at all — anyone can read it in the page source or call the API
directly. The browser only ever learns yes or no.

The password is read from the environment, never committed:

    TICKET_MAKER_DESK_PASSWORD=...     the shared desk password
    TICKET_MAKER_SECRET_KEY=...        signs the session cookie

If no password is set, staff mode stays open and the API says so in
/api/session. That keeps development friction-free while making the unprotected
state visible rather than silent.
"""

import hmac
import os
import secrets

SESSION_FLAG = "staff"


def configured():
    """True when a desk password has been set."""
    return bool(os.environ.get("TICKET_MAKER_DESK_PASSWORD"))


def secret_key():
    key = os.environ.get("TICKET_MAKER_SECRET_KEY")
    if key:
        return key
    # A generated key means sessions end when the server restarts. Fine for
    # development, wrong for anything deployed, which is why it is flagged.
    return secrets.token_hex(32)


def verify(password):
    """Constant-time comparison, so the answer cannot be guessed by timing."""
    expected = os.environ.get("TICKET_MAKER_DESK_PASSWORD")
    if not expected:
        return False
    return hmac.compare_digest(str(password or ""), expected)
