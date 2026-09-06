"""Making sure a deployment always has somebody who can act as administrator.

There is a bootstrap deadlock in the identity model, and this module is the
only thing standing in front of it. `is_admin` is set on no user by default;
the one operation that requires it — deleting another person's Digital Twin —
is guarded by `CurrentAdmin`; and there is no endpoint that grants it, because
an endpoint that promoted the caller would make the check decorative. So a
deployment can reach a state where nobody can ever be made an administrator,
and the only exit is a manual `UPDATE`.

The rule this resolves it with is deliberately narrow:

**If there are users and none of them is an administrator, exactly one is
promoted, and it is logged.** Nothing else. It never demotes anybody, never
promotes a second person, never runs when an administrator already exists, and
never creates a user — an identity that appears on first boot is an identity
nobody chose.

Which one: the user named by `BOOTSTRAP_ADMIN_EMAIL` if that is set and matches
somebody, otherwise the earliest-created user, which in this deployment is the
CEO — the first Digital Twin, created when the workspace was set up. Falling
back to "the first person" rather than to "whoever has CEO in `role`" is on
purpose: `role` is a persona label somebody types, and reading it as permission
would make anybody who writes "CEO" an administrator, which is exactly the
mistake `is_admin` exists to prevent.

This is a *bootstrap*, not an authorisation model. When real authentication
lands, granting an administrator is an operator action in whatever admin
surface that brings, and this module's reason to exist goes with it.
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import settings
from app.models.user import User

logger = logging.getLogger(__name__)


def find_admins(db: Session) -> list[User]:
    """Everybody who can act on another user's account."""

    return list(db.execute(select(User).where(User.is_admin.is_(True))).scalars().all())


def _preferred(db: Session) -> User | None:
    """The user `BOOTSTRAP_ADMIN_EMAIL` names, if it names one who exists."""

    wanted = (settings.BOOTSTRAP_ADMIN_EMAIL or "").strip().lower()

    if not wanted:
        return None

    return db.execute(select(User).where(User.email == wanted)).scalar_one_or_none()


def ensure_admin(db: Session) -> User | None:
    """Promote one user if nobody can administer this deployment.

    Returns the promoted user, or None when nothing was done — which is the
    ordinary case on every boot after the first.

    Idempotent, and safe to call from a startup hook: it is one indexed query
    when an administrator already exists.
    """

    existing = find_admins(db)

    if existing:
        return None

    candidate = _preferred(db)

    if candidate is None:
        # Earliest-created, and *exactly* `list_users`' ordering so that "the
        # first user" means the same thing here as it does at the top of the
        # twin switcher. Somebody checking why a particular person became the
        # administrator should be able to read the answer off the UI.
        #
        # The tie-break on `id` matters more than it looks: `created_at` comes
        # from the database clock, and on SQLite that is second-resolution, so
        # two users created in the same second sort by a random UUID. On
        # PostgreSQL, `now()` is the transaction timestamp at microsecond
        # resolution and separate commits are genuinely ordered. Set
        # `BOOTSTRAP_ADMIN_EMAIL` for a deployment that needs the choice to be
        # stated rather than inferred.
        candidate = db.execute(
            select(User).order_by(User.created_at, User.id).limit(1)
        ).scalar_one_or_none()

    if candidate is None:
        # No users at all. Creating one here would invent an identity nobody
        # chose, and the first user created through the API becomes the
        # administrator on the next start.
        logger.info("admin_bootstrap_skipped", extra={"reason": "no users"})
        return None

    candidate.is_admin = True
    db.commit()
    db.refresh(candidate)

    # Warning rather than info: a change to who can delete other people's data
    # should be visible in a log somebody reads, not filed with the routine
    # startup chatter.
    logger.warning(
        "admin_bootstrapped",
        extra={
            "user_id": str(candidate.id),
            "role": candidate.role,
            "reason": ("configured" if _preferred(db) is not None else "earliest user"),
        },
    )

    return candidate
