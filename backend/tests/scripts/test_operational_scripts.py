"""The two operational scripts, held to the contract that makes them useful.

Neither script is a feature, and neither is covered by the application's own
tests — which is precisely why they need these. Both exist to answer "did the
live integration actually work?", and a diagnostic that reports failure while
exiting 0 is worse than no diagnostic at all: it is a green CI badge over a
corpus that ingested nothing.

Nothing here reaches the network. The scripts import their application
dependencies lazily, inside the functions that need them, so the modules
themselves import offline — and the decisions worth pinning were deliberately
written as functions of plain numbers so they can be tested without a tenant.
"""

from types import SimpleNamespace

import pytest

from scripts import diagnose_onedrive_corpus as diagnose
from scripts import validate_live_integration as validate


def _summary(
    *,
    discovered: int,
    indexed: int = 0,
    replaced: int = 0,
    unchanged: int = 0,
    unsupported: int = 0,
    ocr_required: int = 0,
    failed: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        discovered=discovered,
        indexed=indexed,
        replaced=replaced,
        unchanged=unchanged,
        unsupported=unsupported,
        ocr_required=ocr_required,
        failed=failed,
    )


# --- P: the diagnostic script --------------------------------------------


class TestTheDiagnosticScriptImportsWithoutATenant:
    def test_it_imports_offline(self) -> None:
        """The acceptance command is `python -c "import
        scripts.diagnose_onedrive_corpus"`, and it has to hold on a machine
        with no credentials — which is the whole reason the Graph imports sit
        inside `main` rather than at module scope."""

        assert callable(diagnose.main)

    def test_it_does_not_import_the_application_at_module_scope(self) -> None:
        """Importing `app.config.settings` builds `Settings()`, which on a
        machine with no configuration is the difference between a script that
        prints a diagnosis and a script that raises before `main` is reached."""

        import ast
        import inspect

        tree = ast.parse(inspect.getsource(diagnose))
        top_level = [n for n in tree.body if isinstance(n, ast.ImportFrom)]

        assert not [n for n in top_level if (n.module or "").startswith("app.")]


class TestExtensionClassification:
    def test_an_extension_is_lowercased(self) -> None:
        assert diagnose._extension("Report.PDF") == ".pdf"

    def test_a_file_with_no_extension_is_named_rather_than_blank(self) -> None:
        """An empty string in a frequency table is indistinguishable from a
        formatting bug."""

        assert diagnose._extension("Makefile") == "(none)"

    def test_only_the_final_suffix_counts(self) -> None:
        assert diagnose._extension("archive.tar.gz") == ".gz"


class TestTheProbeExitStatus:
    def test_a_corpus_where_nothing_can_be_downloaded_fails(self) -> None:
        """The zero-ingestion run in miniature: 487 files offered, every one
        refused, and the script must not exit 0 on it."""

        assert diagnose.probe_exit_code(readable=0, probed=487) == 1

    def test_one_readable_file_is_enough_to_pass(self) -> None:
        assert diagnose.probe_exit_code(readable=1, probed=487) == 0

    def test_an_empty_corpus_is_not_a_failure(self) -> None:
        """An empty folder and a folder that refuses everything are different
        facts, and the exit status must not merge them."""

        assert diagnose.probe_exit_code(readable=0, probed=0) == 0


class TestTheTableRenderer:
    def test_an_empty_counter_says_none_rather_than_printing_nothing(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        from collections import Counter

        diagnose._table("failures by extension", Counter())

        assert "(none)" in capsys.readouterr().out

    def test_counts_are_ordered_most_common_first(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        from collections import Counter

        diagnose._table("by extension", Counter({".pdf": 2, ".docx": 9}))
        out = capsys.readouterr().out

        assert out.index(".docx") < out.index(".pdf")

    def test_a_long_tail_is_summarised_rather_than_dumped(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        from collections import Counter

        diagnose._table("by name", Counter(str(n) for n in range(60)), limit=5)

        assert "and 55 more distinct values" in capsys.readouterr().out


# --- Q: the validation script --------------------------------------------


class TestTheValidationScriptImportsWithoutATenant:
    def test_it_imports_offline(self) -> None:
        assert callable(validate.main)

    def test_nothing_is_redacted_into_a_leak(self) -> None:
        """A pre-authorised URL carries its token in the query string, and the
        redactor's only job is to cut there."""

        redacted = validate._redact("https://files.example/x?tempauth=SECRET-VALUE")

        assert "SECRET" not in redacted
        assert redacted == "https://files.example/x"


class TestTheIngestionVerdict:
    def test_a_run_that_read_none_of_what_it_found_is_a_failure(self) -> None:
        """The live run that started all of this: discovered 859, failed 487,
        indexed 0 — and called itself a success."""

        verdict, ok = validate._ingestion_verdict(
            _summary(discovered=859, failed=487, unsupported=372)
        )

        assert ok is False
        assert "FAILED" in verdict
        assert "859" in verdict and "487" in verdict

    def test_a_run_with_nothing_to_do_is_not_a_failure(self) -> None:
        """An incremental sync over an unchanged folder legitimately handles
        nothing, and must not be reported as a broken one."""

        _verdict, ok = validate._ingestion_verdict(_summary(discovered=0))

        assert ok is True

    def test_unchanged_files_count_as_read(self) -> None:
        """A second run proving idempotency indexes nothing by design. Counting
        only `indexed` would call that a failure."""

        _verdict, ok = validate._ingestion_verdict(
            _summary(discovered=40, unchanged=40)
        )

        assert ok is True

    def test_a_partial_run_that_read_something_passes(self) -> None:
        _verdict, ok = validate._ingestion_verdict(
            _summary(discovered=100, indexed=60, failed=40)
        )

        assert ok is True

    def test_a_corpus_of_scans_is_not_mistaken_for_a_broken_downloader(
        self,
    ) -> None:
        """Their bytes arrived and their parser ran. Nothing is wrong.

        Excluding them from `handled` would report a folder of thirty scans as
        a run that read nothing — the exact opposite of what this verdict is
        for, and how thirty deferred files would have blocked acceptance.
        """

        _verdict, ok = validate._ingestion_verdict(
            _summary(discovered=30, ocr_required=30)
        )

        assert ok is True

    def test_scans_alongside_real_failures_still_report_the_failures(self) -> None:
        verdict, ok = validate._ingestion_verdict(
            _summary(discovered=40, indexed=8, ocr_required=2, failed=30)
        )

        assert ok is True
        assert "needs-OCR=2" in verdict

    def test_the_verdict_names_the_numbers_rather_than_a_word(self) -> None:
        verdict, _ok = validate._ingestion_verdict(
            _summary(discovered=100, indexed=58, replaced=2, failed=40)
        )

        assert "60 of 100" in verdict


class TestTheOverallExitStatus:
    def test_success_requires_all_three(self) -> None:
        assert (
            validate.overall_exit_code(
                sync_ok=True, calendar_ok=True, retrieval_ok=True
            )
            == 0
        )

    @pytest.mark.parametrize("failing", ["sync_ok", "calendar_ok", "retrieval_ok"])
    def test_any_single_failure_exits_non_zero(self, failing: str) -> None:
        flags = {"sync_ok": True, "calendar_ok": True, "retrieval_ok": True}
        flags[failing] = False

        assert validate.overall_exit_code(**flags) == 1

    def test_a_retrieval_pass_cannot_rescue_a_sync_that_ingested_nothing(self) -> None:
        """The exact shape of the false green: the knowledge base already held
        268 documents, so retrieval answered fine while the sync read nothing.
        Retrieval is not allowed to vote the run into success on its own."""

        assert (
            validate.overall_exit_code(
                sync_ok=False, calendar_ok=True, retrieval_ok=True
            )
            == 1
        )
