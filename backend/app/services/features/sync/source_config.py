"""Which OneDrive folders to synchronise, read from configuration.

No folder is named anywhere in application code. Adding one is an environment
change, which is the whole point: the five SunRadia folders are deployment
facts, and a deployment fact in a source file is a redeploy every time it
changes.
"""

import json
from dataclasses import dataclass

from app.config.settings import settings
from app.core.exceptions import SyncNotConfiguredError, SyncSourceNotFoundError


@dataclass(frozen=True)
class OneDriveSource:
    """One configured folder.

    `key` is identity and must not change: it is what sync state is stored
    against, so renaming it orphans the delta token and forces a full resync.
    `path` and `item_id` are two ways of saying where the folder is, and an
    id is preferred once known — a path is only correct until somebody renames
    a parent.
    """

    key: str
    label: str
    drive_id: str | None = None
    path: str | None = None
    item_id: str | None = None


def _require(entry: dict, index: int) -> str:
    key = entry.get("key")

    if not key or not str(key).strip():
        raise SyncNotConfiguredError(
            f"ONEDRIVE_SOURCES entry {index} has no `key`. Every source needs a "
            "stable key, because sync state is stored against it."
        )

    return str(key).strip()


def load_sources(raw: str | None = None) -> list[OneDriveSource]:
    """Parse `ONEDRIVE_SOURCES`, or return nothing if none are configured.

    An empty configuration is not an error. It is the state of every
    deployment that has not been given OneDrive credentials, and the
    application has to import and serve in exactly that state.
    """

    text = (raw if raw is not None else settings.ONEDRIVE_SOURCES or "").strip()

    if not text:
        return []

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SyncNotConfiguredError(
            f"ONEDRIVE_SOURCES is not valid JSON: {exc.msg} at position {exc.pos}."
        ) from exc

    if not isinstance(parsed, list):
        raise SyncNotConfiguredError(
            "ONEDRIVE_SOURCES must be a JSON array of source objects."
        )

    sources: list[OneDriveSource] = []
    seen: set[str] = set()

    for index, entry in enumerate(parsed):
        if not isinstance(entry, dict):
            raise SyncNotConfiguredError(
                f"ONEDRIVE_SOURCES entry {index} is not an object."
            )

        key = _require(entry, index)

        if key in seen:
            # Two sources sharing a key would share one delta token and
            # overwrite each other's state on alternate runs.
            raise SyncNotConfiguredError(
                f"ONEDRIVE_SOURCES contains more than one source keyed {key!r}."
            )
        seen.add(key)

        path = entry.get("path")
        item_id = entry.get("item_id")

        if not path and not item_id:
            raise SyncNotConfiguredError(
                f"ONEDRIVE_SOURCES entry {key!r} needs either `path` or `item_id`."
            )

        drive_id = entry.get("drive_id") or settings.ONEDRIVE_DRIVE_ID

        if not drive_id:
            raise SyncNotConfiguredError(
                f"ONEDRIVE_SOURCES entry {key!r} has no `drive_id`, and "
                "ONEDRIVE_DRIVE_ID is not set."
            )

        sources.append(
            OneDriveSource(
                key=key,
                label=str(entry.get("label") or path or key),
                drive_id=str(drive_id),
                path=str(path) if path else None,
                item_id=str(item_id) if item_id else None,
            )
        )

    return sources


def get_source(key: str, raw: str | None = None) -> OneDriveSource:
    """Return one configured source by key, or say which keys exist."""

    sources = load_sources(raw)

    for source in sources:
        if source.key == key:
            return source

    known = ", ".join(sorted(source.key for source in sources)) or "none"

    raise SyncSourceNotFoundError(
        f"No OneDrive source is configured with key {key!r}. Configured: {known}."
    )
