"""What a Microsoft sharing link is, and how Graph is asked about one.

Lives beside the Graph client rather than with the sync feature: the `u!`
encoding and the meaning of a `*-my.sharepoint.com` host are both facts about
Microsoft, not about this application. Nothing here imports from `app/models`,
`app/schemas` or `app/services/features` — the provider boundary holds.

Two separate things live here, both pure and both offline.

**Encoding.** Graph resolves a sharing URL through `/shares/{token}`, where the
token is the *whole URL* base64url-encoded behind a `u!` marker. That is the
documented mechanism and it is the opposite of decoding an id out of a URL: no
part of the link is interpreted here, and the `driveId` and `id` that come back
are read from Graph's response body, not from anything this module parsed. A
sharing URL contains a string that looks like an item id, and treating it as
one is the classic way to end up confidently pointed at the wrong folder.

**Classification.** The host of a sharing link says which tenant boundary the
content sits behind, and therefore whether an application-only token for *this*
tenant can ever reach it. That question decides whether a failure is a bug to
fix or a permission somebody else has to grant, so it is worth answering
precisely rather than by trial.
"""

import base64
from enum import StrEnum
from urllib.parse import urlsplit

# Microsoft's link shortener. A short link says nothing about what is behind
# it — it has to be followed before anything can be concluded, which is why
# `SHORT_LINK` is a separate answer from "we do not know".
_SHORT_LINK_HOSTS = frozenset({"1drv.ms", "sway.ms"})

# The consumer OneDrive service. A different identity system entirely: these
# accounts are Microsoft accounts, not members of any Entra tenant, so no
# tenant's application token has any standing over them.
_CONSUMER_HOSTS = frozenset({"onedrive.live.com", "skydrive.live.com"})


class SharedResourceKind(StrEnum):
    """Where a sharing link points, judged from its host."""

    SHORT_LINK = "short_link"
    SHAREPOINT_LIBRARY = "sharepoint_library"
    ONEDRIVE_FOR_BUSINESS = "onedrive_for_business"
    CONSUMER_ONEDRIVE = "consumer_onedrive"
    UNKNOWN = "unknown"


#: Kinds an application-only token issued for a work/school tenant can reach,
#: given `Files.Read.All`. Everything else needs a person or another tenant.
REACHABLE_APP_ONLY = frozenset(
    {SharedResourceKind.SHAREPOINT_LIBRARY, SharedResourceKind.ONEDRIVE_FOR_BUSINESS}
)


def sharing_token(url: str) -> str:
    """Encode a sharing URL the way `/shares/{token}` expects.

    Base64, then made URL-safe: padding dropped, `/` to `_`, `+` to `-`, behind
    the `u!` marker Graph uses to recognise an encoded URL rather than a share
    id. The URL is passed through whole and unexamined.
    """

    cleaned = url.strip()

    if not cleaned:
        raise ValueError("A sharing URL is required to build a sharing token.")

    encoded = base64.b64encode(cleaned.encode("utf-8")).decode("ascii")

    return "u!" + encoded.rstrip("=").replace("/", "_").replace("+", "-")


def host_of(url: str) -> str:
    return (urlsplit(url.strip()).hostname or "").lower()


def classify(url: str) -> SharedResourceKind:
    """Say what kind of resource a sharing link points at.

    Judged from the host alone, because the host is the only part of a sharing
    URL that is a fact rather than an opaque token. A short link is reported as
    short rather than guessed at: the whole point is that it hides its target.
    """

    host = host_of(url)

    if not host:
        return SharedResourceKind.UNKNOWN

    if host in _SHORT_LINK_HOSTS:
        return SharedResourceKind.SHORT_LINK

    if host in _CONSUMER_HOSTS:
        return SharedResourceKind.CONSUMER_ONEDRIVE

    if host.endswith("-my.sharepoint.com"):
        # A person's OneDrive for Business. In-tenant, and covered by the
        # application permission Files.Read.All.
        return SharedResourceKind.ONEDRIVE_FOR_BUSINESS

    if host.endswith(".sharepoint.com"):
        return SharedResourceKind.SHAREPOINT_LIBRARY

    return SharedResourceKind.UNKNOWN


def is_short_link(url: str) -> bool:
    return classify(url) is SharedResourceKind.SHORT_LINK


def reachable_app_only(kind: SharedResourceKind) -> bool:
    return kind in REACHABLE_APP_ONLY


def explain(kind: SharedResourceKind, *, host: str = "") -> str:
    """Why a link of this kind can or cannot be read app-only.

    Written to be pasted into a ticket. Each sentence states a consequence, not
    a permission to try.
    """

    where = f" ({host})" if host else ""

    if kind is SharedResourceKind.SHORT_LINK:
        return (
            f"A shortened Microsoft link{where}. It has to be followed to its "
            "destination before anything can be said about it — the short form "
            "hides whether the content is in this tenant or outside it."
        )

    if kind is SharedResourceKind.CONSUMER_ONEDRIVE:
        return (
            f"A consumer OneDrive link{where}, owned by a personal Microsoft "
            "account rather than by this Entra tenant. An application token is "
            "issued by a tenant and has authority only inside it, so no "
            "permission granted to this application can ever read this "
            "content. The folder has to be copied into the tenant."
        )

    if kind is SharedResourceKind.SHAREPOINT_LIBRARY:
        return (
            f"A SharePoint document library{where}. Readable with the "
            "application permission Files.Read.All, which already covers files "
            "in all site collections."
        )

    if kind is SharedResourceKind.ONEDRIVE_FOR_BUSINESS:
        return (
            f"A OneDrive for Business library{where}. Readable with the "
            "application permission Files.Read.All."
        )

    return (
        f"An unrecognised host{where}. Neither a tenant SharePoint domain nor a "
        "known Microsoft consumer domain, so this is not content a Microsoft "
        "Graph application token addresses at all."
    )
