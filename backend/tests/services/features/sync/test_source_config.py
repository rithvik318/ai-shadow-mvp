"""Reading the list of folders to synchronise.

Configuration errors surface here or they surface at 3am in a scheduled job,
so every one of these is about producing a message somebody can act on.
"""

import pytest

from app.core.exceptions import SyncNotConfiguredError, SyncSourceNotFoundError
from app.services.features.sync.source_config import get_source, load_sources
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
