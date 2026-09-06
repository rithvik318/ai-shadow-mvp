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

    `uri` is the human-facing address of the folder — the link somebody would
    paste from a browser. It is carried for display only and never used to
    address Graph.

    `share_url` is different, and the difference matters. It is a sharing link
    that Graph itself is asked to resolve, through `/shares/{token}`, when the
    folder's drive is not known — content shared from another site or another
    person's OneDrive has a drive id nobody has written down. The link is
    handed to Graph whole and encoded; the `drive_id` and `item_id` come back
    in Graph's answer. Nothing decodes the id that appears inside the URL,
    which is a different act with the same shape and is how these integrations
    end up pointed at the wrong folder.

    `enabled` is how a source is taken out of the rotation without deleting
    its configuration. A disabled source keeps its key, and therefore its
    stored delta token, so re-enabling it resumes rather than re-indexing.
    """

    key: str
    label: str
    drive_id: str | None = None
    path: str | None = None
    item_id: str | None = None
    share_url: str | None = None
    uri: str | None = None
    enabled: bool = True

    @property
    def addressed_by_share_link(self) -> bool:
        """True when only Graph can say which drive this folder is in."""

        return bool(self.share_url) and not (self.item_id or self.path)


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
        share_url = entry.get("share_url")

        if not path and not item_id and not share_url:
            raise SyncNotConfiguredError(
                f"ONEDRIVE_SOURCES entry {key!r} needs `path`, `item_id` or "
                "`share_url`."
            )

        drive_id = entry.get("drive_id") or settings.ONEDRIVE_DRIVE_ID

        # A share link names its own drive once Graph resolves it, so requiring
        # one up front would mean inventing a drive id to satisfy a check —
        # and an invented drive id addressed a real drive that was the wrong
        # one. Path and id addressing still need it: they are relative to a
        # drive and mean nothing without one.
        if not drive_id and not share_url:
            raise SyncNotConfiguredError(
                f"ONEDRIVE_SOURCES entry {key!r} has no `drive_id`, and "
                "ONEDRIVE_DRIVE_ID is not set."
            )

        enabled = entry.get("enabled", True)

        if not isinstance(enabled, bool):
            # A string "false" is truthy, which would silently keep a source
            # somebody believed they had switched off.
            raise SyncNotConfiguredError(
                f"ONEDRIVE_SOURCES entry {key!r} has a non-boolean `enabled`. "
                "Use JSON true or false."
            )

        sources.append(
            OneDriveSource(
                key=key,
                label=str(entry.get("label") or path or key),
                drive_id=str(drive_id) if drive_id else None,
                path=str(path) if path else None,
                item_id=str(item_id) if item_id else None,
                share_url=str(share_url) if share_url else None,
                uri=str(entry["uri"]) if entry.get("uri") else None,
                enabled=enabled,
            )
        )

    return sources


def enabled_sources(raw: str | None = None) -> list[OneDriveSource]:
    """The sources a scheduled or unqualified run should touch.

    Separate from `load_sources` because status screens need to show a
    disabled source — "configured but switched off" is information, and a
    source that vanished from the list would read as a configuration mistake.
    """

    return [source for source in load_sources(raw) if source.enabled]


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
