"""Working out who a request is for.

**`X-User-ID` is a development identity mechanism for the MVP, and must be
replaced by authenticated identity before production.** It is a claim, not a
proof: anyone who can reach the API can send any user's id and read that
person's Digital Twin. It exists so the multi-user boundary can be built,
tested and relied on now, and so that adding real authentication later is a
change to *this module only* — every service below it already takes the user
it operates on as an argument.

What that later change looks like: `current_user` stops reading a header and
starts reading a verified session or token. Nothing downstream moves, because
nothing downstream trusts the header — it trusts the `User` this returns.
"""

import uuid
from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from app.core.exceptions import (
    MalformedIdentityError,
    MissingIdentityError,
    NotAuthorisedError,
)
from app.database.session import get_db
from app.models.user import User
from app.services.features.users import user_service

USER_ID_HEADER = "X-User-ID"

HEADER_DESCRIPTION = (
    "MVP development identity: the id of the user this request acts as. "
    "Not authentication — it is replaced by a verified session before "
    "production."
)


def current_user(
    db: Annotated[Session, Depends(get_db)],
    x_user_id: Annotated[str | None, Header(description=HEADER_DESCRIPTION)] = None,
) -> User:
    """Resolve `X-User-ID` to a user, or refuse the request.

    Three distinct failures, because they need three distinct fixes: no header
    at all (401 — say who you are), a header that is not a UUID (422 — the
    shape is wrong), and a UUID naming nobody (404 — create the user first).
    Collapsing them into one status would leave a caller guessing which.

    A user is never created here. An identity that appears on first use is an
    identity nobody chose, and the isolation this whole module exists to
    provide would then be one typo deep.
    """

    if x_user_id is None or not x_user_id.strip():
        raise MissingIdentityError(
            f"This endpoint acts on behalf of a user. Send the {USER_ID_HEADER} "
            "header with the id of that user."
        )

    try:
        user_id = uuid.UUID(x_user_id.strip())
    except ValueError as exc:
        raise MalformedIdentityError(
            f"{USER_ID_HEADER} must be a UUID, and {x_user_id!r} is not one."
        ) from exc

    return user_service.get_user(db, user_id)


CurrentUser = Annotated[User, Depends(current_user)]


def current_admin(user: "CurrentUser") -> User:
    """The caller, but only if they are an administrator.

    Reads `is_admin`, never `role`. `role` is a persona label a person types —
    "CEO", "CRM Manager" — and treating it as permission would make anybody who
    writes "Admin" into it one.

    This is the only authorisation check in the codebase, and it guards exactly
    one thing: acting on *another* user. Everything else is self-service and
    already scoped by `CurrentUser`, which is a stronger guarantee than a
    permission check because there is no call shape that reaches another
    person's data at all.
    """

    if not user.is_admin:
        raise NotAuthorisedError(
            "Only an administrator may act on another user's account."
        )

    return user


CurrentAdmin = Annotated[User, Depends(current_admin)]
