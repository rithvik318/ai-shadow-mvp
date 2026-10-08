"""Run one activity report against the live configuration and say what happened.

Read-only. It reads the mailbox and calls the model exactly as the endpoint
does — because the point is to reproduce what the endpoint does — and writes
nothing anywhere.

It exists because a report that degrades is *designed* to degrade: a failed
batch of conversations falls back to a structural description rather than
failing the request, which is right for a person reading a report and useless
for somebody working out why the report is thin. This turns those swallowed
failures into the first thing on screen, with the exception, the traceback and
the timing of each stage.

    python -m scripts.diagnose_activity_report --user <uuid> --start 2026-09-01

`--start`/`--end` are inclusive dates, the same as the endpoint's. Omit both
for the current month so far.

Nothing here prints a subject, a body or an address: the output is counts,
identifiers and exception types, so it can be pasted into an issue.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
import traceback
import uuid
from datetime import UTC, date, datetime

from app.config.settings import settings
from app.database.session import SessionLocal
from app.prompts import register_default_prompts
from app.services.engines.analysis import analysis_engine as engine_module
from app.services.features.email import mailbox_config_service
from app.services.features.reports import correspondence as report_correspondence
from app.services.features.reports import pipeline as report_pipeline
from app.services.features.reports.period import PeriodKind, range_between, to_date
from app.services.llm.client import llm_configured

logger = logging.getLogger("diagnose")


def _period(start: date | None, end: date | None):
    now = datetime.now(UTC)

    if start is None and end is None:
        return to_date(PeriodKind.MONTH, now)

    first = datetime.combine(start or date(now.year, now.month, 1), datetime.min.time())
    last = datetime.combine(end or now.date(), datetime.min.time())

    return range_between(
        first.replace(tzinfo=UTC),
        (last.replace(tzinfo=UTC)).replace(hour=23, minute=59, second=59),
    )


def _instrument() -> list[tuple[str, float, BaseException | None]]:
    """Record every model call: which prompt, how long, and what it raised."""

    calls: list[tuple[str, float, BaseException | None]] = []
    original = engine_module.analysis_engine.run

    def run(prompt_name: str, response_model: type, **variables: object):
        started = time.monotonic()

        try:
            result = original(prompt_name, response_model, **variables)
        except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised
            calls.append((prompt_name, time.monotonic() - started, exc))
            raise

        calls.append((prompt_name, time.monotonic() - started, None))

        return result

    engine_module.analysis_engine.run = run  # type: ignore[method-assign]

    return calls


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", required=True, help="X-User-ID to report as")
    parser.add_argument("--start", type=date.fromisoformat, default=None)
    parser.add_argument("--end", type=date.fromisoformat, default=None)
    parser.add_argument(
        "--tracebacks",
        action="store_true",
        help="Print the full traceback of every failed model call.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    register_default_prompts()

    window = _period(args.start, args.end)
    user_id = uuid.UUID(args.user)

    print(f"period           {window.label}  ({window.key})")
    print(f"model            {settings.LLM_PROVIDER}/{settings.LLM_MODEL}")
    print(f"credentials      {'present' if llm_configured() else 'ABSENT'}")
    print(
        f"bounds           timeout={settings.LLM_TIMEOUT_SECONDS}s "
        f"retries={settings.LLM_MAX_RETRIES} "
        f"budget={settings.REPORT_ANALYSIS_BUDGET_SECONDS}s"
    )

    db = SessionLocal()
    calls = _instrument()

    try:
        address = mailbox_config_service.resolve_address(db, user_id=user_id)

        if not address:
            print("mailbox          NONE CONNECTED — the endpoint would answer 409")
            return 2

        try:
            provider = mailbox_config_service.provider_for(db, user_id=user_id)
        except Exception as exc:  # noqa: BLE001 - naming it is the whole job
            print(f"provider         UNAVAILABLE — {type(exc).__name__}: {exc}")
            return 2

        status = provider.status()
        print(
            f"mailbox          {address} via {status.provider} "
            f"(configured={status.configured} connected={status.connected})"
        )

        started = time.monotonic()

        try:
            correspondence = report_correspondence.from_provider(
                provider, mailbox=address, period=window
            )
        except Exception as exc:  # noqa: BLE001 - naming it is the whole job
            print(f"fetch            FAILED after {time.monotonic() - started:.1f}s")
            print(f"  {type(exc).__name__}: {exc}")

            if args.tracebacks:
                traceback.print_exception(type(exc), exc, exc.__traceback__)

            return 3

        fetched = time.monotonic() - started

        print(
            f"fetch            {len(correspondence.messages)} messages "
            f"({len(correspondence.inbound)} in, {len(correspondence.outbound)} out) "
            f"in {fetched:.1f}s  truncated={correspondence.truncated}"
        )

        result = report_pipeline.generate(
            correspondence,
            owner=None,
            use_model=llm_configured(),
            budget_seconds=settings.REPORT_ANALYSIS_BUDGET_SECONDS,
        )
        total = time.monotonic() - started
    finally:
        db.close()

    failed = [call for call in calls if call[2] is not None]

    print(
        f"model calls      {len(calls)} "
        f"({len(failed)} failed) "
        f"totalling {sum(call[1] for call in calls):.1f}s"
    )
    print(
        f"report           {result.document.word_count} words, "
        f"narrative={result.analysis.generated}, quality_ok={result.verdict.ok}"
    )
    print(f"elapsed          {total:.1f}s")

    for note in result.analysis.limitations:
        print(f"limitation       {note}")

    for index, (prompt_name, seconds, exc) in enumerate(failed, start=1):
        print(f"\nfailure {index}: {prompt_name} after {seconds:.1f}s")
        print(f"  {type(exc).__name__}: {exc}")

        cause = exc.__cause__ if exc is not None else None

        if cause is not None:
            print(f"  caused by {type(cause).__name__}: {cause}")

        if args.tracebacks and exc is not None:
            traceback.print_exception(type(exc), exc, exc.__traceback__)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
