"""Turning configured folders into ids Graph will answer about.

The property under test is the one that keeps a five-folder deployment
honest: **every id comes from Graph**. There is no code path here that reads
an id out of a URL, and the test that matters most is the one asserting a
misspelled folder is reported as an error rather than resolved to something
plausible.

The real `GraphClient` is used against a mock transport rather than a stub
client, because the translation from "a path a person typed" to "the URL Graph
is asked" is exactly what could be wrong.
"""

import pytest

from app.core.exceptions import GraphError
from app.services.features.sync.source_config import OneDriveSource, load_sources
from app.services.features.sync.source_resolver import (
    Verdict,
    find_user_drive,
    resolve_source,
    resolve_sources,
    to_configuration,
)
from app.services.graph.client import GraphClient
from app.services.graph.share_link import SharedResourceKind
from tests.support.graph import DRIVE_ID, drive_item, graph_transport, sources_json

# The five SunRadia folders, written the way `.env` carries them. Kept here
# rather than in application code on purpose: the point of the configuration
# mechanism is that these are a deployment fact, and a sixth folder must not
# need a code change.
FIVE_SOURCES = sources_json(
    {
        "key": "cftc-dq-da",
        "label": "CFTC / DQ-DA",
        "drive_id": DRIVE_ID,
        "path": "Documents/CFTC/DQ-DA/Final Submission Apr 29th 2024",
    },
    {
        "key": "amtrack-aws-migration",
        "label": "Amtrack / AWS Migration",
        "drive_id": DRIVE_ID,
        "path": "Documents/Amtrack/AWS - Migration/Final/Submission folder",
    },
    {
        "key": "case-study",
        "label": "Case Study",
        "drive_id": DRIVE_ID,
        "path": "Documents/Capabilities/2024 and Earlier/Case Study",
    },
    {
        "key": "freddie-mac-2026",
        "label": "Freddie Mac 2026",
        "drive_id": DRIVE_ID,
        "path": "Documents/Freddie Mac 2026",
    },
    {
        "key": "capabilities",
        "label": "Capabilities",
        "drive_id": DRIVE_ID,
        "path": "Documents/Capabilities",
    },
)


def _client(routes, **kwargs) -> GraphClient:
    return GraphClient(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        base_url="https://graph.example/v1.0",
        authority="https://login.example",
        transport=graph_transport(routes, **kwargs),
    )


def _source(key: str = "capabilities", **overrides) -> OneDriveSource:
    values = {
        "key": key,
        "label": "Capabilities",
        "drive_id": DRIVE_ID,
        "path": "Documents/Capabilities",
    }
    values.update(overrides)

    return OneDriveSource(**values)  # type: ignore[arg-type]


# --- one source ----------------------------------------------------------


def test_a_path_is_resolved_to_an_id_graph_supplied() -> None:
    client = _client(
        {
            "root:/Documents/Capabilities:": drive_item(
                "cap-1", "Capabilities", folder=True
            )
        }
    )

    resolved = resolve_source(client, _source())

    assert resolved.resolved
    assert resolved.item_id == "cap-1"
    assert resolved.drive_id == DRIVE_ID


def test_the_configured_path_is_kept_alongside_the_id() -> None:
    """The id is what sync uses; the path is what a person reads to decide
    whether the configuration still says what they meant."""

    client = _client({"root:/": drive_item("cap-1", "Capabilities", folder=True)})

    resolved = resolve_source(client, _source())

    assert resolved.path == "Documents/Capabilities"


def test_a_folder_that_does_not_exist_is_an_error_not_a_guess() -> None:
    """The failure that matters. A resolver that fell back to something
    plausible would index the wrong folder and never say so."""

    client = _client({})  # every path 404s

    resolved = resolve_source(client, _source(path="Documents/Nope"))

    assert not resolved.resolved
    assert resolved.error
    assert resolved.item_id is None


def test_a_file_is_refused_where_a_folder_was_expected() -> None:
    client = _client({"root:/": drive_item("f-1", "brochure.pdf")})

    resolved = resolve_source(client, _source())

    assert not resolved.resolved
    assert "not a folder" in (resolved.error or "")


def test_a_source_already_addressed_by_id_is_confirmed_not_re_derived() -> None:
    client = _client(
        {"/items/known-1": drive_item("known-1", "Capabilities", folder=True)}
    )

    resolved = resolve_source(client, _source(path=None, item_id="known-1"))

    assert resolved.resolved
    assert resolved.item_id == "known-1"


def test_an_error_never_carries_a_query_string() -> None:
    """Graph URLs carry tokens in their query strings, and a resolution report
    is something people paste into tickets."""

    client = _client({"root:/": (500, {"error": {"code": "internalError"}})})

    resolved = resolve_source(client, _source())

    assert "?" not in (resolved.error or "")


# --- every source --------------------------------------------------------


def test_all_five_configured_folders_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_SOURCES", FIVE_SOURCES)

    client = _client(
        {
            "DQ-DA": drive_item(
                "id-cftc", "Final Submission Apr 29th 2024", folder=True
            ),
            "Amtrack": drive_item("id-amtrack", "Submission folder", folder=True),
            "Case%20Study": drive_item("id-case", "Case Study", folder=True),
            "Freddie": drive_item("id-freddie", "Freddie Mac 2026", folder=True),
            "root:/Documents/Capabilities:": drive_item(
                "id-cap", "Capabilities", folder=True
            ),
        }
    )

    resolved = resolve_sources(client, load_sources())

    assert [source.key for source in resolved] == [
        "cftc-dq-da",
        "amtrack-aws-migration",
        "case-study",
        "freddie-mac-2026",
        "capabilities",
    ]
    assert all(source.resolved for source in resolved)
    # Five distinct folders, not one folder found five times.
    assert len({source.item_id for source in resolved}) == 5


def test_one_bad_path_does_not_stop_the_others() -> None:
    client = _client(
        {
            "root:/Documents/Capabilities:": drive_item(
                "cap-1", "Capabilities", folder=True
            )
        }
    )

    resolved = resolve_sources(
        client, [_source(), _source("missing", path="Documents/Nope")]
    )

    assert resolved[0].resolved
    assert not resolved[1].resolved


def test_the_configuration_produced_pins_ids_and_omits_failures() -> None:
    client = _client(
        {
            "root:/Documents/Capabilities:": drive_item(
                "cap-1", "Capabilities", folder=True
            )
        }
    )

    entries = to_configuration(
        resolve_sources(client, [_source(), _source("missing", path="Documents/Nope")])
    )

    assert entries == [
        {
            "key": "capabilities",
            "label": "Capabilities",
            "drive_id": DRIVE_ID,
            "item_id": "cap-1",
            "path": "Documents/Capabilities",
        }
    ]


def test_a_disabled_source_stays_disabled_through_resolution() -> None:
    client = _client({"root:/": drive_item("cap-1", "Capabilities", folder=True)})

    entries = to_configuration(resolve_sources(client, [_source(enabled=False)]))

    assert entries[0]["enabled"] is False


def test_the_source_uri_survives_resolution() -> None:
    client = _client({"root:/": drive_item("cap-1", "Capabilities", folder=True)})

    entries = to_configuration(
        resolve_sources(client, [_source(uri="https://example.sharepoint.com/x")])
    )

    assert entries[0]["uri"] == "https://example.sharepoint.com/x"


# --- finding the drive ---------------------------------------------------


def test_a_users_drive_is_found_by_principal_name() -> None:
    client = _client(
        {"/users/someone@example.com/drive": {"id": "drive-9", "name": "OneDrive"}}
    )

    drive_id, name = find_user_drive(client, "someone@example.com")

    assert (drive_id, name) == ("drive-9", "OneDrive")


def test_a_missing_drive_is_named_rather_than_returned_empty() -> None:
    client = _client({"/users/someone@example.com/drive": {"name": "OneDrive"}})

    with pytest.raises(GraphError):
        find_user_drive(client, "someone@example.com")


# --- sharing links -------------------------------------------------------
#
# The five knowledge-base folders arrived as sharing links, so the question
# these answer is the one that decides the whole integration: when a link
# cannot be read, is that this application's fault or somebody else's?


def _shared(key: str = "capabilities", **overrides) -> OneDriveSource:
    values = {
        "key": key,
        "label": "Capabilities",
        "share_url": "https://contoso.sharepoint.com/:f:/s/KB/AbC",
    }
    values.update(overrides)

    return OneDriveSource(**values)  # type: ignore[arg-type]


_SHARED_FOLDER = {
    "id": "shared-item-1",
    "name": "Capabilities",
    "folder": {"childCount": 9},
    "parentReference": {"driveId": "library-drive-1"},
}


def test_a_sharing_link_is_resolved_by_graph_not_by_parsing() -> None:
    client = _client({"/shares/": _SHARED_FOLDER})

    resolved = resolve_source(client, _shared())

    assert resolved.verdict is Verdict.RESOLVED
    assert (resolved.drive_id, resolved.item_id) == ("library-drive-1", "shared-item-1")


def test_a_share_link_needs_no_drive_id_to_be_configured() -> None:
    """The drive is what the link is being asked about. Requiring one up front
    would mean inventing a drive id, and an invented one addressed a real
    drive that was the wrong one."""

    source = load_sources(
        sources_json(
            {
                "key": "capabilities",
                "share_url": "https://contoso.sharepoint.com/:f:/s/KB/AbC",
            }
        )
    )[0]

    assert source.drive_id is None
    assert source.addressed_by_share_link


def test_a_short_link_is_followed_before_it_is_resolved() -> None:
    """`/shares` is told a URL, and a short link is a redirect rather than the
    URL of anything."""

    destination = "https://contoso.sharepoint.com/:f:/s/KB/RealPath"
    client = _client(
        {"/shares/": _SHARED_FOLDER, "RealPath": {}},
        redirects={"1drv.ms": destination},
    )

    resolved = resolve_source(client, _shared(share_url="https://1drv.ms/f/s!AbCdEf"))

    assert resolved.resolved_url == destination
    assert resolved.kind is SharedResourceKind.SHAREPOINT_LIBRARY
    assert resolved.verdict is Verdict.RESOLVED


def test_a_short_link_to_a_consumer_account_is_named_as_out_of_reach() -> None:
    """The verdict that ends the permission-granting loop.

    An application token is issued by a tenant and has authority only inside
    it. A consumer OneDrive belongs to a Microsoft account that is in no
    tenant, so no consent granted to this application can ever reach it. Graph
    is not even asked, because the 403 it would return reads exactly like a
    missing permission and would send somebody granting more of them.
    """

    client = _client(
        {}, redirects={"1drv.ms": "https://onedrive.live.com/?id=root&cid=ABC"}
    )

    resolved = resolve_source(client, _shared(share_url="https://1drv.ms/f/s!AbCdEf"))

    assert resolved.verdict is Verdict.OUTSIDE_TENANT
    assert resolved.kind is SharedResourceKind.CONSUMER_ONEDRIVE
    assert "personal Microsoft account" in (resolved.error or "")
    assert "copied or moved" in (resolved.remedy or "")


def test_an_in_tenant_link_that_is_refused_is_a_consent_problem() -> None:
    """Distinct from the previous verdict, and the distinction is the point:
    this one an administrator can fix, that one nobody can."""

    client = _client({"/shares/": (403, {"error": {"code": "accessDenied"}})})

    resolved = resolve_source(client, _shared())

    assert resolved.verdict is Verdict.ACCESS_DENIED
    assert "Files.Read.All" in (resolved.remedy or "")


def test_a_link_that_graph_cannot_find_is_neither_of_those() -> None:
    client = _client({"/shares/": (404, {"error": {"code": "itemNotFound"}})})

    resolved = resolve_source(client, _shared())

    assert resolved.verdict is Verdict.NOT_FOUND
    assert resolved.remedy is None


def test_a_link_to_a_file_is_refused_as_a_source() -> None:
    client = _client(
        {
            "/shares/": {
                "id": "f-1",
                "name": "brochure.pdf",
                "file": {"mimeType": "application/pdf"},
                "parentReference": {"driveId": "d-1"},
            }
        }
    )

    resolved = resolve_source(client, _shared())

    assert resolved.verdict is Verdict.NOT_A_FOLDER


def test_a_short_link_that_does_not_redirect_is_not_blamed_on_a_tenant() -> None:
    """Nothing redirected, so nothing is known about the destination. Calling
    that "outside the tenant" would blame content nobody has seen."""

    client = _client({})  # no redirect registered

    resolved = resolve_source(client, _shared(share_url="https://1drv.ms/f/s!Gone"))

    assert resolved.verdict is Verdict.UNREACHABLE
    assert resolved.drive_id is None


def test_a_destination_that_refuses_an_anonymous_fetch_is_still_classified() -> None:
    """The host that answered is the fact worth having, whatever it answered.

    A consumer link routinely returns 403 to a signed-out request. Treating
    that as a failure to follow would discard the one piece of evidence that
    settles which tenant owns the content.
    """

    client = _client(
        {"live.com": (403, {"error": "sign in"})},
        redirects={"1drv.ms": "https://onedrive.live.com/?id=root&cid=ABC"},
    )

    resolved = resolve_source(client, _shared(share_url="https://1drv.ms/f/s!AbC"))

    assert resolved.verdict is Verdict.OUTSIDE_TENANT
    assert resolved.kind is SharedResourceKind.CONSUMER_ONEDRIVE


def test_a_resolved_share_link_becomes_ordinary_configuration() -> None:
    """Once Graph has named the drive and item, a share-linked folder is
    addressed exactly like any other — which is what stops it needing its own
    code path in sync, delta, ingestion or retrieval."""

    client = _client({"/shares/": _SHARED_FOLDER})

    entries = to_configuration(resolve_sources(client, [_shared()]))

    assert entries == [
        {
            "key": "capabilities",
            "label": "Capabilities",
            "drive_id": "library-drive-1",
            "item_id": "shared-item-1",
            "uri": "https://contoso.sharepoint.com/:f:/s/KB/AbC",
        }
    ]


def test_one_unreachable_link_does_not_stop_the_reachable_ones() -> None:
    client = _client(
        {
            "/shares/": _SHARED_FOLDER,
            "root:/Documents/Capabilities:": drive_item(
                "cap-1", "Capabilities", folder=True
            ),
        },
        redirects={"1drv.ms": "https://onedrive.live.com/?id=root"},
    )

    resolved = resolve_sources(
        client,
        [
            _source(),
            _shared("shared-ok"),
            _shared("shared-consumer", share_url="https://1drv.ms/f/s!X"),
        ],
    )

    assert [item.verdict for item in resolved] == [
        Verdict.RESOLVED,
        Verdict.RESOLVED,
        Verdict.OUTSIDE_TENANT,
    ]


def test_a_path_without_a_drive_says_so_rather_than_asking_graph() -> None:
    client = _client({})

    resolved = resolve_source(
        client, OneDriveSource(key="k", label="k", path="Capabilities")
    )

    assert resolved.verdict is Verdict.NOT_ADDRESSABLE
    assert "drive_id" in (resolved.error or "")


def test_a_source_naming_nothing_is_refused() -> None:
    resolved = resolve_source(_client({}), OneDriveSource(key="k", label="k"))

    assert resolved.verdict is Verdict.NOT_ADDRESSABLE
