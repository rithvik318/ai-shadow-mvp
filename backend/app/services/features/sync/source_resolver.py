"""Turning "the folder a person means" into ids Graph will answer about.

Configuration wants a `drive_id` and an `item_id`. Neither is something anybody
knows, and both are printed in a browser's address bar in a form that looks
like it could be decoded. Nothing here parses a URL for identifiers. Every id
in the output came from Graph answering a question.

There are three ways a folder can be named, and they need different questions:

- **a path in a known drive** — `/drives/{drive}/root:/{path}:`
- **an item id in a known drive** — `/drives/{drive}/items/{id}`
- **a sharing link** — `/shares/{token}/driveItem`, where the token is the
  whole URL encoded. This is the only route when the folder lives in a drive
  nobody has written down, which is the normal case for content that arrived
  as a share.

A sharing link may also be *short*. A short link is a redirect and not the URL
of anything, so it is followed first — which is also what reveals whether the
content is inside this tenant at all. That question decides whether a failure
is a bug or a permission somebody else has to grant, and it is answered here
rather than guessed at.

One more shape is handled by `drive_service`: a shortcut to a shared folder,
which appears in somebody's own drive as a stub carrying a `remoteItem`. Its
own id addresses the stub, which has no children — so a sync pointed at it
finds an empty folder and reports success. The target's drive and item are
taken instead.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum

from app.core.exceptions import GraphAuthError, GraphError
from app.services.features.sync.source_config import OneDriveSource
from app.services.graph import drive_service, share_link
from app.services.graph.client import GraphClient
from app.services.graph.share_link import SharedResourceKind

logger = logging.getLogger(__name__)


class Verdict(StrEnum):
    """Why a source resolved, or what kind of thing stopped it.

    The distinction that matters is the last three. `NOT_FOUND` is a wrong
    path, which is ours to fix. `OUTSIDE_TENANT` is content no permission
    granted to this application can ever reach, which is a decision for whoever
    owns the content. `ACCESS_DENIED` is in-tenant content the application is
    not yet consented to read, which is a decision for the tenant
    administrator. Collapsing them into "it failed" is what turns this into a
    week of guessing.
    """

    RESOLVED = "resolved"
    NOT_FOUND = "not_found"
    ACCESS_DENIED = "access_denied"
    OUTSIDE_TENANT = "outside_tenant"
    NOT_A_FOLDER = "not_a_folder"
    UNREACHABLE = "unreachable"
    NOT_ADDRESSABLE = "not_addressable"


@dataclass(frozen=True)
class ResolvedSource:
    """What Graph said about one configured folder.

    Carries a verdict rather than raising, because resolving five folders where
    one is unreachable should report four addresses and one precise reason, not
    stop at the first problem.
    """

    key: str
    label: str
    drive_id: str | None
    item_id: str | None
    path: str | None
    uri: str | None
    enabled: bool
    verdict: Verdict = Verdict.RESOLVED
    error: str | None = None
    #: Where the sharing link actually pointed, once followed. Recorded because
    #: a short link's destination is the single fact that decides everything
    #: else, and because it is what a person needs to see to believe the answer.
    resolved_url: str | None = None
    kind: SharedResourceKind | None = None
    remedy: str | None = None

    @property
    def resolved(self) -> bool:
        return self.verdict is Verdict.RESOLVED and bool(self.drive_id and self.item_id)


def _failure(
    source: OneDriveSource,
    verdict: Verdict,
    error: str,
    *,
    resolved_url: str | None = None,
    kind: SharedResourceKind | None = None,
    remedy: str | None = None,
) -> ResolvedSource:
    return ResolvedSource(
        key=source.key,
        label=source.label,
        drive_id=source.drive_id,
        item_id=source.item_id,
        path=source.path,
        uri=source.uri,
        enabled=source.enabled,
        verdict=verdict,
        error=error,
        resolved_url=resolved_url,
        kind=kind,
        remedy=remedy,
    )


_OUTSIDE_TENANT_REMEDY = (
    "Nothing in this application can be changed to reach it. The folder has to "
    "be copied or moved into a SunRadia SharePoint library or a SunRadia "
    "OneDrive, or re-shared from one."
)

_ACCESS_DENIED_REMEDY = (
    "In-tenant content the application has not been consented to read. An "
    "administrator grants the application permission Files.Read.All admin "
    "consent, or adds the application to this specific site."
)


# --- resolving one source -------------------------------------------------


def _resolve_by_share_link(
    client: GraphClient, source: OneDriveSource
) -> ResolvedSource:
    """Ask Graph to turn a sharing link into a real drive item.

    The link is followed first when it is short. That is not politeness: a
    short link is a redirect, `/shares` is told a URL rather than a redirect,
    and the destination host is the only evidence of which tenant the content
    is in.
    """

    url = str(source.share_url)
    resolved_url = url

    if share_link.is_short_link(url):
        try:
            resolved_url = client.resolve_redirect(url)
        except GraphError as exc:
            return _failure(
                source,
                Verdict.UNREACHABLE,
                f"The shortened link could not be followed: {exc}",
                resolved_url=None,
                kind=SharedResourceKind.SHORT_LINK,
            )

    kind = share_link.classify(resolved_url)
    host = share_link.host_of(resolved_url)

    if kind is SharedResourceKind.SHORT_LINK:
        # Still short after being followed: the redirect did not happen, so
        # nothing is known about the destination. Reporting this as "outside
        # the tenant" would blame content nobody has seen.
        return _failure(
            source,
            Verdict.UNREACHABLE,
            "The shortened link did not redirect anywhere, so its destination "
            "is unknown. Open it in a browser and configure the address it "
            "lands on.",
            resolved_url=resolved_url,
            kind=kind,
        )

    if not share_link.reachable_app_only(kind):
        # Decided before asking Graph. Asking anyway would produce a 403 that
        # reads like a missing permission, and somebody would spend a day
        # granting permissions that cannot possibly help.
        return _failure(
            source,
            Verdict.OUTSIDE_TENANT,
            share_link.explain(kind, host=host),
            resolved_url=resolved_url,
            kind=kind,
            remedy=_OUTSIDE_TENANT_REMEDY,
        )

    try:
        item = drive_service.resolve_shared_item(client, resolved_url)
    except GraphAuthError as exc:
        return _failure(
            source,
            Verdict.ACCESS_DENIED,
            str(exc),
            resolved_url=resolved_url,
            kind=kind,
            remedy=_ACCESS_DENIED_REMEDY,
        )
    except GraphError as exc:
        return _failure(
            source,
            Verdict.NOT_FOUND,
            str(exc),
            resolved_url=resolved_url,
            kind=kind,
        )

    if not item.is_folder:
        return _failure(
            source,
            Verdict.NOT_A_FOLDER,
            f"{item.name!r} is a file, not a folder. A source must name a folder.",
            resolved_url=resolved_url,
            kind=kind,
        )

    if not (item.drive_id and item.item_id):
        return _failure(
            source,
            Verdict.NOT_FOUND,
            "Graph resolved the link but returned no drive or item id.",
            resolved_url=resolved_url,
            kind=kind,
        )

    return ResolvedSource(
        key=source.key,
        label=source.label,
        # Graph's answer wins over anything configured: a shared folder's drive
        # is routinely not the drive somebody wrote down.
        drive_id=item.drive_id,
        item_id=item.item_id,
        path=source.path,
        uri=source.uri or resolved_url,
        enabled=source.enabled,
        resolved_url=resolved_url,
        kind=kind,
    )


def _resolve_in_drive(client: GraphClient, source: OneDriveSource) -> ResolvedSource:
    try:
        folder = drive_service.resolve_folder(
            client,
            drive_id=source.drive_id,
            path=source.path,
            item_id=source.item_id,
        )
    except GraphAuthError as exc:
        return _failure(
            source, Verdict.ACCESS_DENIED, str(exc), remedy=_ACCESS_DENIED_REMEDY
        )
    except (GraphError, ValueError) as exc:
        # Graph errors already have their query strings stripped, so no token
        # or pre-authorised URL can reach a caller through this.
        return _failure(source, Verdict.NOT_FOUND, str(exc))

    if not folder.is_folder:
        return _failure(
            source,
            Verdict.NOT_A_FOLDER,
            f"{source.path or source.item_id!r} is a file, not a folder. "
            "A source must name a folder to synchronise.",
        )

    return ResolvedSource(
        key=source.key,
        label=source.label,
        drive_id=folder.drive_id,
        item_id=folder.item_id,
        path=source.path,
        uri=source.uri,
        enabled=source.enabled,
    )


def resolve_source(client: GraphClient, source: OneDriveSource) -> ResolvedSource:
    """Ask Graph where one configured folder is, by whichever route it named."""

    if source.item_id or source.path:
        if not source.drive_id:
            return _failure(
                source,
                Verdict.NOT_ADDRESSABLE,
                "A path or item id is relative to a drive, and no `drive_id` "
                "is configured for this source.",
            )

        return _resolve_in_drive(client, source)

    if source.share_url:
        return _resolve_by_share_link(client, source)

    return _failure(
        source,
        Verdict.NOT_ADDRESSABLE,
        "The source names no `path`, `item_id` or `share_url`.",
    )


def resolve_sources(
    client: GraphClient, sources: list[OneDriveSource]
) -> list[ResolvedSource]:
    """Resolve every source, reporting failures alongside successes."""

    resolved = [resolve_source(client, source) for source in sources]

    logger.info(
        "onedrive_sources_resolved",
        extra={
            "requested": len(sources),
            "resolved": sum(1 for item in resolved if item.resolved),
            "outside_tenant": sum(
                1 for item in resolved if item.verdict is Verdict.OUTSIDE_TENANT
            ),
        },
    )

    return resolved


def to_configuration(resolved: list[ResolvedSource]) -> list[dict]:
    """The `ONEDRIVE_SOURCES` value these resolutions describe.

    Only the drive and item ids: once Graph has named them, the path and the
    sharing link are history. A resolved source is addressed the same way
    whether it began as a path in a known drive or as a link to somebody
    else's, which is what stops a share-linked folder needing its own code
    path anywhere downstream.
    """

    entries: list[dict] = []

    for source in resolved:
        if not source.resolved:
            continue

        entry: dict = {
            "key": source.key,
            "label": source.label,
            "drive_id": source.drive_id,
            "item_id": source.item_id,
        }

        if source.path:
            entry["path"] = source.path
        if source.uri:
            entry["uri"] = source.uri
        if not source.enabled:
            entry["enabled"] = False

        entries.append(entry)

    return entries


def find_user_drive(client: GraphClient, user_principal_name: str) -> tuple[str, str]:
    """The drive id of one person's OneDrive, and its name.

    The drive is asked for by user principal name because that is what an
    administrator has to hand. Application permissions mean no one has to be
    signed in for this to work.
    """

    payload = client.get(f"/users/{user_principal_name}/drive")

    drive_id = str(payload.get("id") or "")

    if not drive_id:
        raise GraphError(
            f"Graph returned no drive id for {user_principal_name!r}. "
            "Check that the account has a provisioned OneDrive."
        )

    return drive_id, str(payload.get("name") or "")
