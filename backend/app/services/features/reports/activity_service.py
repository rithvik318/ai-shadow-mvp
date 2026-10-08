"""Answering "what happened in this window" at most once per window.

The endpoint used to be a pipeline with a URL: every request read a mailbox,
classified and threaded it, made a dozen model calls and composed a document —
including for a week that had closed a fortnight earlier and could not possibly
have a different answer. Opening 7–13 September twice cost those minutes twice.

This module is the layer that stops that. Four outcomes, and a caller can tell
which it got:

    STORED         the report was read from the database. No mailbox, no model.
    REVALIDATED    the window is still open, the mailbox was re-read, the
                   correspondence is unchanged, so the stored document stands.
                   A provider call, no model calls.
    GENERATED      the report was built now and written down.
    (raises)       ReportBeingGenerated — somebody else is building this exact
                   report, and the API says so rather than starting a second.

`refresh=True` is what the Regenerate button sends. It skips the freshness
question but not the lock: a deliberate rebuild is still one rebuild.

The rules about *when* a stored report may be served are not here — they are in
`activity_store`, stated once, so this module reads as the sequence of steps it
performs.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy.orm import Session

from app.models.activity_report import ActivityReport
from app.services.email.provider.base import EmailProvider
from app.services.features.reports import activity_store
from app.services.features.reports import correspondence as report_correspondence
from app.services.features.reports import pipeline as report_pipeline
from app.services.features.reports.activity_store import Policy, ReportBeingGenerated
from app.services.features.reports.period import Period

logger = logging.getLogger(__name__)


class Source(StrEnum):
    """How the report in hand came to be. Shown to the reader."""

    STORED = "stored"
    REVALIDATED = "revalidated"
    GENERATED = "generated"


@dataclass(frozen=True)
class Outcome:
    """A report and the story of how it was obtained."""

    row: ActivityReport
    source: Source

    @property
    def content(self) -> dict:
        return self.row.content

    @property
    def reused(self) -> bool:
        return self.source is not Source.GENERATED


def obtain(
    db: Session,
    *,
    user_id: uuid.UUID,
    owner_name: str,
    period: Period,
    provider: EmailProvider,
    mailbox: str,
    policy: Policy,
    now: datetime,
    build,
    refresh: bool = False,
) -> Outcome:
    """Return this window's report, generating it only if it must be.

    `build(correspondence) -> dict` composes the stored payload. It is passed
    in rather than imported so that this module owns the caching decision and
    the route owns the response shape — and so a test can count exactly how
    many times generation actually happened.
    """

    stored = activity_store.find(db, user_id=user_id, period=period)

    if not refresh:
        servable = activity_store.servable(
            stored, period=period, policy=policy, now=now
        )

        if servable is not None:
            logger.info(
                "activity_report_served_from_store user_id=%s period=%s..%s "
                "generated_at=%s provisional=%s",
                user_id,
                period.start.date(),
                period.end.date(),
                servable.generated_at,
                servable.is_provisional,
            )

            return Outcome(row=servable, source=Source.STORED)

    # Past this line the report is going to be rebuilt or at least rechecked,
    # so the right to do it has to be taken first. Raises when somebody else
    # holds it.
    row = activity_store.claim(
        db, user_id=user_id, period=period, policy=policy, now=now
    )

    started = time.monotonic()

    try:
        correspondence = report_correspondence.from_provider(
            provider, mailbox=mailbox, period=period
        )

        logger.info(
            "activity_report_fetched user_id=%s period=%s..%s messages=%d "
            "elapsed=%.1fs",
            user_id,
            period.start.date(),
            period.end.date(),
            len(correspondence.messages),
            time.monotonic() - started,
        )

        # The cheap half of the work is now done, and it is enough to answer
        # the question the freshness clock could not: has anything actually
        # arrived? If not, the stored document is still the right document and
        # no model is asked anything.
        if (
            not refresh
            and stored is not None
            and stored.content
            and stored.evidence_fingerprint
            == activity_store.fingerprint(correspondence)
        ):
            logger.info(
                "activity_report_revalidated user_id=%s period=%s..%s messages=%d",
                user_id,
                period.start.date(),
                period.end.date(),
                len(correspondence.messages),
            )

            return Outcome(
                row=activity_store.revalidate(db, row, now=now),
                source=Source.REVALIDATED,
            )

        content = build(correspondence)
    except ReportBeingGenerated:
        raise
    except Exception as exc:
        # The claim must not outlive the attempt, and a failure must not look
        # like a report. Both are recorded, and the exception goes on to the
        # error handler that knows its status code.
        activity_store.fail(db, row, detail=f"{type(exc).__name__}: {exc}", now=now)
        raise

    stored_row = activity_store.complete(
        db,
        row,
        content=content,
        correspondence=correspondence,
        period=period,
        now=now,
    )

    logger.info(
        "activity_report_generated user_id=%s period=%s..%s messages=%d elapsed=%.1fs",
        user_id,
        period.start.date(),
        period.end.date(),
        len(correspondence.messages),
        time.monotonic() - started,
    )

    return Outcome(row=stored_row, source=Source.GENERATED)


def generate_content(
    correspondence,
    *,
    owner_name: str,
    context: str,
    use_model: bool,
    budget_seconds: float | None,
    now: datetime,
) -> dict:
    """Run the pipeline and render the payload that gets stored.

    The stored shape is the response shape minus the parts that describe *this*
    request — `source`, and the period block the route rebuilds — so that a
    reply served from storage and one served fresh differ in provenance and in
    nothing else.
    """

    result = report_pipeline.generate(
        correspondence,
        owner=owner_name,
        context=context,
        use_model=use_model,
        budget_seconds=budget_seconds,
    )

    return {
        "title": result.document.title,
        "markdown": result.markdown,
        "word_count": result.document.word_count,
        "sections": result.document.section_titles,
        "workstream_count": result.document.workstream_count,
        "thread_count": result.document.thread_count,
        "messages_analysed": len(correspondence.messages),
        "substantive_threads": len(result.inventory.substantive),
        "routine_threads": len(result.inventory.routine),
        "narrative": result.analysis.generated,
        "quality_ok": result.verdict.ok,
        "quality_notes": [*result.verdict.failures, *result.verdict.warnings],
        "limitations": correspondence.limitations,
    }
