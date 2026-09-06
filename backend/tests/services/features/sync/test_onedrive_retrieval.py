"""From five OneDrive folders to an answer with the right citation.

The other sync tests assert what happened to rows and delta tokens. These
assert the thing the milestone is actually for: a document that arrived from
one of the five configured folders is retrievable, and the citation attached
to it names the file it really came from.

Retrieval is the production `retrieval_service.search` — the same call
`/search` and `/chat` make. There is no OneDrive-specific retriever, and the
absence of one is part of what is being asserted: nothing below distinguishes
a synced document from an uploaded one.
"""

from collections.abc import Callable

import pytest
from sqlalchemy.orm import Session

from app.models.document import Document
from app.services.features.retrieval.retrieval_service import search
from app.services.features.sync.onedrive_sync_service import sync_all, sync_source
from app.services.features.sync.source_config import OneDriveSource
from tests.fixtures.factories import build_text
from tests.support.embeddings import deterministic_vector
from tests.support.graph import DRIVE_ID, FakeGraphClient, drive_item, sources_json

# One sentence per folder, distinct enough that the fake embedding provider
# ranks the right one first.
CONTENT = {
    "cftc-dq-da": "The CFTC data quality assessment covers reference data lineage.",
    "amtrack-aws-migration": "The Amtrack AWS migration moved signalling telemetry.",
    "case-study": "The case study describes a substation refurbishment in 2023.",
    "freddie-mac-2026": "Freddie Mac 2026 scope covers mortgage servicing controls.",
    "capabilities": "SunRadia capabilities include transformer condition monitoring.",
}

FILENAMES = {
    "cftc-dq-da": "cftc-dq-da.txt",
    "amtrack-aws-migration": "amtrack-aws.txt",
    "case-study": "case-study.txt",
    "freddie-mac-2026": "freddie-mac.txt",
    "capabilities": "capabilities.txt",
}


def _five(**overrides: object) -> str:
    return sources_json(
        *[
            {"key": key, "label": key, "path": key, "drive_id": DRIVE_ID, **overrides}
            for key in CONTENT
        ]
    )


class _PerSourceClient:
    """A Graph that answers each source with that source's one file.

    Each `sync_source` call takes the next delta page, and the sources are
    processed in configured order, so the pages line up with the keys.
    """

    def __init__(self, keys: list[str]) -> None:
        self._inner = FakeGraphClient(
            delta_pages=[([self._item(key)], f"delta:{key}") for key in keys]
        )

        for key in keys:
            item = self._item(key)
            url = item["@microsoft.graph.downloadUrl"]
            self._inner.downloads[url] = build_text(CONTENT[key])

    @staticmethod
    def _item(key: str) -> dict:
        return drive_item(f"item-{key}", FILENAMES[key])

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


def _retrieve(
    db: Session, text: str, embed_query_as: Callable[[list[float]], list[str]]
):
    embed_query_as(deterministic_vector(text))

    return search(db, "any question", similarity_threshold=None)


@pytest.fixture
def five_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings as settings_module

    monkeypatch.setattr(settings_module.settings, "ONEDRIVE_SOURCES", _five())


def test_a_document_from_every_source_is_searchable(
    db_session: Session,
    five_sources: None,
    fake_embeddings: None,
    embed_query_as: Callable[[list[float]], list[str]],
) -> None:
    """The end of the pipeline. Five folders, five files, five answers."""

    sync_all(db_session, client=_PerSourceClient(list(CONTENT)))  # type: ignore[arg-type]

    for key, sentence in CONTENT.items():
        results = _retrieve(db_session, sentence, embed_query_as)

        assert results, f"nothing retrievable for {key}"
        assert any(sentence in result.content for result in results)


def test_the_citation_names_the_file_it_came_from(
    db_session: Session,
    five_sources: None,
    fake_embeddings: None,
    embed_query_as: Callable[[list[float]], list[str]],
) -> None:
    """Attribution has to survive the whole journey — Graph payload, download,
    ingestion, chunking, retrieval — or a citation is decoration."""

    sync_all(db_session, client=_PerSourceClient(list(CONTENT)))  # type: ignore[arg-type]

    results = _retrieve(db_session, CONTENT["freddie-mac-2026"], embed_query_as)
    match = next(
        result for result in results if CONTENT["freddie-mac-2026"] in result.content
    )

    assert match.filename == FILENAMES["freddie-mac-2026"]

    # And the document the citation points at is the one Graph delivered. The
    # identity is drive + item id, not a name and not a path, so a rename does
    # not break the link.
    document = db_session.get(Document, match.document_id)

    assert document is not None
    assert document.source_uri == f"onedrive:{DRIVE_ID}:item-freddie-mac-2026"


def test_no_source_is_attributed_to_the_wrong_folder(
    db_session: Session,
    five_sources: None,
    fake_embeddings: None,
    embed_query_as: Callable[[list[float]], list[str]],
) -> None:
    """Five documents indexed together must not swap their provenance."""

    sync_all(db_session, client=_PerSourceClient(list(CONTENT)))  # type: ignore[arg-type]

    for key, sentence in CONTENT.items():
        results = _retrieve(db_session, sentence, embed_query_as)
        match = next(result for result in results if sentence in result.content)

        assert match.filename == FILENAMES[key]


def test_retrieval_can_only_return_documents_that_exist(
    db_session: Session,
    five_sources: None,
    fake_embeddings: None,
    embed_query_as: Callable[[list[float]], list[str]],
) -> None:
    """A citation cannot be invented, because a citation is a row.

    Retrieval returns passages the database holds. Nothing between Graph and
    an answer can name a document that was never indexed — which is what makes
    "the model made up a source" impossible rather than unlikely.
    """

    sync_all(db_session, client=_PerSourceClient(list(CONTENT)))  # type: ignore[arg-type]

    results = _retrieve(
        db_session, "a subject nobody in this corpus wrote about", embed_query_as
    )

    assert {result.filename for result in results} <= set(FILENAMES.values())


def test_an_empty_corpus_retrieves_nothing_rather_than_something(
    db_session: Session,
    fake_embeddings: None,
    embed_query_as: Callable[[list[float]], list[str]],
) -> None:
    assert _retrieve(db_session, "anything at all", embed_query_as) == []


def test_a_modified_file_changes_the_answer(
    db_session: Session,
    fake_embeddings: None,
    embed_query_as: Callable[[list[float]], list[str]],
) -> None:
    """The old text has to stop being retrievable, not merely be outranked."""

    source = OneDriveSource(
        key="capabilities", label="Capabilities", drive_id=DRIVE_ID, path="Capabilities"
    )
    original = "The original capability statement about water treatment."
    revised = "The revised capability statement about railway signalling."

    client = FakeGraphClient()
    first = drive_item("item-1", "capabilities.txt", version="v1")
    client.downloads[first["@microsoft.graph.downloadUrl"]] = build_text(original)
    client.delta_pages = [([first], "delta:1")]

    sync_source(db_session, client, source)

    assert _retrieve(db_session, original, embed_query_as)

    second = drive_item("item-1", "capabilities.txt", version="v2")
    client.downloads[second["@microsoft.graph.downloadUrl"]] = build_text(revised)
    client.delta_pages = [([second], "delta:2")]
    client._delta_index = 0

    sync_source(db_session, client, source)

    revised_results = _retrieve(db_session, revised, embed_query_as)

    assert any(revised in result.content for result in revised_results)
    assert not any(
        original in result.content
        for result in _retrieve(db_session, original, embed_query_as)
    )


def test_a_deleted_file_stops_being_retrievable(
    db_session: Session,
    fake_embeddings: None,
    embed_query_as: Callable[[list[float]], list[str]],
) -> None:
    source = OneDriveSource(
        key="capabilities", label="Capabilities", drive_id=DRIVE_ID, path="Capabilities"
    )
    text = "A statement that is about to be withdrawn from the corpus."

    client = FakeGraphClient()
    item = drive_item("item-1", "capabilities.txt")
    client.downloads[item["@microsoft.graph.downloadUrl"]] = build_text(text)
    client.delta_pages = [([item], "delta:1")]

    sync_source(db_session, client, source)
    assert _retrieve(db_session, text, embed_query_as)

    client.delta_pages = [
        ([drive_item("item-1", "capabilities.txt", deleted=True)], "delta:2")
    ]
    client._delta_index = 0

    sync_source(db_session, client, source)

    assert not any(
        text in result.content for result in _retrieve(db_session, text, embed_query_as)
    )
