"""Rendering retrieved passages into the block of context the model reads.

Kept apart from `chat_service` because it answers a different question. That
service decides *what* to retrieve and what to do with the answer; this one
decides how a passage is presented — how it is labelled, how sources are
separated, and how many of them fit. Those are the details worth changing
independently when answers cite badly or contexts grow too long.

Pure: a list of retrieved chunks in, a string and the chunks it covers out.
No database, no configuration read at import time, no provider.
"""

from dataclasses import dataclass

from app.services.features.retrieval.retrieval_service import RetrievedChunk


@dataclass(frozen=True)
class AssembledContext:
    """The rendered context and the passages it actually contains.

    `chunks` is not always everything retrieved: a passage dropped for budget
    never reaches the model, and listing it as a source would claim the answer
    drew on something the model never saw.
    """

    text: str
    chunks: list[RetrievedChunk]

    @property
    def truncated(self) -> bool:
        return not self.text


def _describe(chunk: RetrievedChunk, position: int) -> str:
    """Render one passage as a labelled, delimited source block.

    The label carries the document and whichever locator the format has, so
    the model can tell two passages apart, can qualify an answer that holds in
    one document but not another, and has an identifier to cite.
    """

    lines = [f"[SOURCE {position}]", f"Document: {chunk.filename}"]

    if chunk.section_title:
        lines.append(f"Section: {chunk.section_title}")

    if chunk.page_number is not None:
        lines.append(f"Page: {chunk.page_number}")

    lines.append(f"Content: {chunk.content}")

    return "\n".join(lines)


def build_context(chunks: list[RetrievedChunk], *, max_chars: int) -> AssembledContext:
    """Render `chunks` as numbered source blocks, most relevant first.

    Retrieval already returns chunks in descending similarity, and that order
    is preserved rather than re-sorted: the ranking is the database's, computed
    against the index, and re-deriving it here would be a second opinion with
    less information.

    `max_chars` bounds the block. Passages are taken in order until the next
    one would not fit, so the budget costs the least relevant material first.
    A single passage larger than the budget is still included — dropping it
    would leave the model with nothing to answer from, and the chunk size that
    produced it is already bounded by `CHUNK_SIZE`.
    """

    blocks: list[str] = []
    used: list[RetrievedChunk] = []
    length = 0

    for position, chunk in enumerate(chunks, start=1):
        block = _describe(chunk, position)
        # `+ 2` for the blank line between blocks, so the budget describes the
        # string that is actually sent rather than the sum of its parts.
        cost = len(block) + (2 if blocks else 0)

        if blocks and length + cost > max_chars:
            break

        blocks.append(block)
        used.append(chunk)
        length += cost

    return AssembledContext(text="\n\n".join(blocks), chunks=used)
