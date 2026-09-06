"""Reading the list of folders to synchronise.

Configuration errors surface here or they surface at 3am in a scheduled job,
so every one of these is about producing a message somebody can act on.
"""

import pytest

from app.core.exceptions import SyncNotConfiguredError, SyncSourceNotFoundError
from app.services.features.sync.source_config import (
    enabled_sources,
    get_source,
    load_sources,
)
from tests.support.graph import DRIVE_ID, sources_json


def _one(**overrides) -> str:
    entry = {"key": "capabilities", "path": "Capabilities", "drive_id": DRIVE_ID}
    entry.update(overrides)

    return sources_json(entry)


def test_no_configuration_is_not_an_error() -> None:
    """The state of every deployment that has not been given credentials. The
    application still has to import and serve."""

    assert load_sources("") == []
    assert load_sources("   ") == []


def test_a_source_is_parsed() -> None:
    source = load_sources(_one())[0]

    assert source.key == "capabilities"
    assert source.path == "Capabilities"
    assert source.drive_id == DRIVE_ID


def test_the_label_defaults_to_the_path() -> None:
    assert load_sources(_one())[0].label == "Capabilities"


def test_an_explicit_label_is_kept() -> None:
    assert load_sources(_one(label="Case Studies"))[0].label == "Case Studies"


def test_a_source_may_be_addressed_by_item_id() -> None:
    """Preferred once known: a path is only correct until somebody renames a
    parent folder."""

    source = load_sources(_one(path=None, item_id="01ABCDEF"))[0]

    assert source.item_id == "01ABCDEF"


def test_several_sources_are_parsed_in_order() -> None:
    raw = sources_json(
        {"key": "one", "path": "One", "drive_id": DRIVE_ID},
        {"key": "two", "path": "Two", "drive_id": DRIVE_ID},
    )

    assert [source.key for source in load_sources(raw)] == ["one", "two"]


def test_a_missing_drive_falls_back_to_the_configured_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_DRIVE_ID", "default-drive")

    assert load_sources(_one(drive_id=None))[0].drive_id == "default-drive"


# --- configuration that cannot work --------------------------------------


def test_malformed_json_says_where() -> None:
    with pytest.raises(SyncNotConfiguredError) as error:
        load_sources("[{key: 'capabilities'}]")

    assert "not valid JSON" in str(error.value)


def test_a_bare_object_is_rejected() -> None:
    with pytest.raises(SyncNotConfiguredError):
        load_sources('{"key": "capabilities"}')


def test_a_source_without_a_key_is_rejected() -> None:
    """The key is what sync state is stored against, so there is no safe
    default to invent for it."""

    with pytest.raises(SyncNotConfiguredError) as error:
        load_sources(sources_json({"path": "Capabilities", "drive_id": DRIVE_ID}))

    assert "key" in str(error.value)


def test_duplicate_keys_are_rejected() -> None:
    """Two sources sharing a key would share one delta token and overwrite
    each other's progress on alternate runs."""

    raw = sources_json(
        {"key": "same", "path": "One", "drive_id": DRIVE_ID},
        {"key": "same", "path": "Two", "drive_id": DRIVE_ID},
    )

    with pytest.raises(SyncNotConfiguredError) as error:
        load_sources(raw)

    assert "same" in str(error.value)


def test_a_source_with_neither_path_nor_id_is_rejected() -> None:
    with pytest.raises(SyncNotConfiguredError):
        load_sources(sources_json({"key": "capabilities", "drive_id": DRIVE_ID}))


def test_a_source_with_no_drive_anywhere_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_DRIVE_ID", None)

    with pytest.raises(SyncNotConfiguredError) as error:
        load_sources(_one(drive_id=None))

    assert "ONEDRIVE_DRIVE_ID" in str(error.value)


# --- lookup --------------------------------------------------------------


def test_a_source_can_be_looked_up_by_key() -> None:
    assert get_source("capabilities", _one()).path == "Capabilities"


def test_an_unknown_key_lists_the_ones_that_exist() -> None:
    with pytest.raises(SyncSourceNotFoundError) as error:
        get_source("nope", _one())

    assert "capabilities" in str(error.value)


# --- enabling, disabling, and the human-facing address -------------------


def test_a_source_is_enabled_unless_it_says_otherwise() -> None:
    assert load_sources(_one())[0].enabled is True


def test_a_source_can_be_switched_off_without_deleting_it() -> None:
    """Disabling keeps the key, and therefore the stored delta token, so
    re-enabling resumes rather than re-indexing the folder."""

    source = load_sources(_one(enabled=False))[0]

    assert source.enabled is False
    assert source.key == "capabilities"


def test_a_string_instead_of_a_boolean_is_rejected() -> None:
    """`"false"` is truthy in Python, so accepting it would silently keep a
    source somebody believed they had switched off."""

    with pytest.raises(SyncNotConfiguredError) as error:
        load_sources(_one(enabled="false"))

    assert "enabled" in str(error.value)


def test_only_enabled_sources_are_offered_for_a_run() -> None:
    raw = sources_json(
        {"key": "on", "path": "One", "drive_id": DRIVE_ID},
        {"key": "off", "path": "Two", "drive_id": DRIVE_ID, "enabled": False},
    )

    assert [source.key for source in enabled_sources(raw)] == ["on"]
    # A status screen still needs to see the disabled one.
    assert [source.key for source in load_sources(raw)] == ["on", "off"]


def test_a_human_facing_uri_is_carried_but_never_required() -> None:
    with_uri = load_sources(_one(uri="https://example.sharepoint.com/x"))[0]

    assert with_uri.uri == "https://example.sharepoint.com/x"
    assert load_sources(_one())[0].uri is None


# --- the deployment's own configuration ----------------------------------


FIVE = sources_json(
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


def test_the_five_configured_folders_parse_as_five_distinct_sources() -> None:
    """The Amtrack folder appeared twice in the original list. Configuring it
    twice would not merely duplicate work — the two entries would share a key,
    which `load_sources` refuses, or differ by key and race for the same
    documents."""

    sources = load_sources(FIVE)

    assert len(sources) == 5
    assert len({source.key for source in sources}) == 5
    assert len({source.path for source in sources}) == 5


def test_the_five_are_ordinary_configuration_not_a_special_case() -> None:
    """Nothing in application code names any of them, so a sixth is an
    environment change."""

    sources = load_sources(FIVE)

    assert all(source.enabled for source in sources)
    assert [source.label for source in sources] == [
        "CFTC / DQ-DA",
        "Amtrack / AWS Migration",
        "Case Study",
        "Freddie Mac 2026",
        "Capabilities",
    ]


# --- sources named by a sharing link -------------------------------------


def test_a_source_can_be_named_by_a_sharing_link() -> None:
    """Content that arrived as a share has a drive id nobody has written
    down, so the link is the only thing there is to configure."""

    source = load_sources(
        sources_json(
            {
                "key": "capabilities",
                "label": "Capabilities",
                "share_url": "https://1drv.ms/f/s!AbCdEf",
            }
        )
    )[0]

    assert source.share_url == "https://1drv.ms/f/s!AbCdEf"
    assert source.addressed_by_share_link


def test_a_share_link_source_needs_no_drive_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requiring one would mean inventing a drive id to satisfy a check, and
    an invented drive id addresses a real drive that is the wrong one."""

    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_DRIVE_ID", None)

    source = load_sources(
        sources_json({"key": "k", "share_url": "https://1drv.ms/f/s!A"})
    )[0]

    assert source.drive_id is None


def test_a_path_still_needs_a_drive(monkeypatch: pytest.MonkeyPatch) -> None:
    """A path is relative to a drive and means nothing without one, so the
    share-link relaxation must not have loosened this."""

    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_DRIVE_ID", None)

    with pytest.raises(SyncNotConfiguredError):
        load_sources(sources_json({"key": "k", "path": "Capabilities"}))


def test_a_source_naming_no_location_at_all_is_rejected() -> None:
    with pytest.raises(SyncNotConfiguredError) as error:
        load_sources(sources_json({"key": "k", "drive_id": DRIVE_ID}))

    assert "share_url" in str(error.value)


def test_a_pinned_source_is_no_longer_addressed_by_its_link() -> None:
    """Once resolution has filled in the ids, they take precedence — the link
    is history, and asking Graph about it again every run would be a second
    request per source for an answer already stored."""

    source = load_sources(
        sources_json(
            {
                "key": "k",
                "drive_id": DRIVE_ID,
                "item_id": "real-1",
                "share_url": "https://1drv.ms/f/s!A",
            }
        )
    )[0]

    assert not source.addressed_by_share_link
