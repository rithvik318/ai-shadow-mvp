"""Choose which corpus documents belong in the MVP knowledge base.

Walks the corpus read-only, applies an ordered set of explicit rules to every
file, and writes a manifest recording what was selected, what was not, and why
in every case. Nothing is uploaded, moved, renamed or deleted here — the
manifest is a decision, and `ingest_kb_manifest.py` is what acts on it.

**The policy is inclusive.** Everything first-party and readable is selected
unless a rule excludes it, and the only reasons to exclude are: the format
cannot be read, the content is sensitive or personal, the file is a template or
a form with nothing to say, it belongs to another organisation, or it is a
duplicate or a superseded version of something already selected. There are no
per-category limits: a document is not dropped for being the twenty-first of
its kind.

Anything sensitive-looking but ambiguous goes to a third bucket, `review`,
rather than being silently excluded — the point of a manifest is that a person
can disagree with it.

Deterministic: the same corpus produces byte-identical output. Files are walked
in sorted order, every rule is a pure function of path, size and content hash,
and no clock or filesystem timestamp is consulted.

**Modification times are never used.** OneDrive rewrote them across this
corpus, so a file's mtime says when it synced, not when it was written. A
document's date comes from its name or its folder, or it is left null.

Run from `backend/`:

    python -m scripts.build_kb_manifest --corpus "D:/ai-shadow-knowledgebase"
    python -m scripts.build_kb_manifest --corpus ... --summary-only

Writes `knowledge_base_manifest.json` at the repository root by default.
"""

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# Formats the ingestion pipeline can read today. Everything else is reported
# under `unsupported_formats` rather than converted: legacy conversion is a
# later phase, and the report is what says whether it is worth doing.
SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md", ".markdown"}

# Mirrors MAX_UPLOAD_SIZE_BYTES. Kept as a literal rather than imported from
# settings so that building a manifest needs no application configuration.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

CATEGORIES = (
    "capabilities",
    "past_performance",
    "proposals",
    "analytics_bi",
    "technical_methodology",
    "project_documentation",
    "general",
)

# Preferred format when the same document exists in several. DOCX first because
# it carries headings and tables the parser turns into section titles; PDF next
# because it carries page numbers; PPTX next; plain text last.
_FORMAT_RANK = {".docx": 0, ".pdf": 1, ".pptx": 2, ".md": 3, ".markdown": 3, ".txt": 4}


# --- directories ---------------------------------------------------------

# Matched case-insensitively against the posix relative path. The reason is
# what lands in the manifest, so it has to say why rather than restate the rule.
EXCLUDED_DIRECTORIES: list[tuple[str, str]] = [
    (
        "/ptac/",
        "capability statements belonging to other firms, collected for reference",
    ),
    ("/casestudy samples/", "third-party case studies used as formatting samples"),
    (
        "/a. background info/",
        "client system documentation supplied to the engagement, not SunRadia work",
    ),
    ("/logo - color palette/", "brand assets, no prose to answer questions from"),
    ("/corrections/", "signed submission forms, superseded by the final submission"),
    ("/z. archive/", "filed by its author in an archive folder"),
    ("/archive/", "filed by its author in an archive folder"),
]


# --- filename rules ------------------------------------------------------

# Checked before format and size, so that a pricing spreadsheet is counted as
# pricing rather than disappearing into "unsupported format" — that count is
# the one someone will want to audit.
SECRET_PATTERNS: list[tuple[str, str]] = [
    (r"password|credential|\bsecret\b|api[\s_-]?key|access[\s_-]?token", "credentials"),
    (r"\bprivate[\s_-]?key\b|\.pem\b|\bkeystore\b", "key material"),
]

PERSONAL_PATTERNS: list[tuple[str, str]] = [
    (r"email\s*address|contact\s*list|contacts\b|address\s*book", "contact list"),
    (r"\bresume|\bcv\b|curriculum vitae|key team members", "personal resume"),
    (r"direct\s*deposit|\bw-?9\b|bank\s*details|\bssn\b", "personal financial data"),
    (
        r"incentive model|compensation|\bsalary\b|\bpayroll\b|timesheet",
        "employee compensation material",
    ),
]

COMMERCIAL_PATTERNS: list[tuple[str, str]] = [
    (
        r"pricing|rate\s*card|\bclin\b|price\s*schedule|\bcost\s*proposal\b",
        "pricing or rate information",
    ),
]

# Ambiguous on the name alone. Not excluded and not ingested: reported for a
# person to decide, because guessing either way is worse than asking.
REVIEW_PATTERNS: list[tuple[str, str]] = [
    (r"\binvoice\b|\bbudget\b|\bfinancials?\b|\bp&l\b", "may contain financial detail"),
    (r"\bconfidential\b|\bnda\b|proprietary", "marked confidential on its face"),
    (r"\bpersonnel\b|\bstaffing plan\b", "may name individuals"),
]

TEMPLATE_PATTERNS: list[tuple[str, str]] = [
    (r"\btemplate\b", "empty template"),
    (r"\bblank\b", "blank form"),
    (r"proposal form", "submission form, not knowledge content"),
    (r"header template|color palette", "layout asset"),
]

THIRD_PARTY_PATTERNS: list[tuple[str, str]] = [
    (r"\bnascio\b", "third-party publication (NASCIO)"),
    (r"\bpub28\b|postal standards", "third-party standard (USPS Publication 28)"),
    (r"dun\s*&\s*bradstreet|optimizer_br", "third-party vendor brochure"),
    (r"patent_\d|_patent_", "third-party patent filing"),
    (r"eotdregistry", "third-party registry documentation"),
    (r"\bcmmc\b", "third-party compliance checklist"),
    (r"ventera", "third-party case study"),
    (
        r"essentialassetsgroup|glcc |incaruss|konvivial|supplier capability statement",
        "another firm's capability statement",
    ),
    (r"conference next week", "event announcement, not knowledge content"),
    (
        r"gartner|forrester|magic quadrant|\breprint\b",
        "third-party analyst publication",
    ),
]

# Markers that make a filename a variant of another file. Applied only when the
# unmarked name actually exists among the candidates: `Case Study CFTC (003)`
# is the *only* copy of that case study, and dropping it on the strength of the
# suffix would lose the document the marker was supposed to protect.
VARIANT_MARKERS: list[tuple[str, str]] = [
    (r"\s*-\s*copy\s*$", "working copy"),
    (r"[\s_.-]*\bold\b\s*$", "superseded version"),
    (r"\s*\(\d{1,3}\)\s*$", "numbered duplicate export"),
    (r"^\s*working copy of\s+", "working copy"),
]

# "LN03. Conceptual & Logical Data Model_v0.3" and "..._v0.2" are the same
# deliverable twice. Deliberately narrow: a version token is a `v` prefix or a
# dotted number, never a bare integer, so a year or a document number in a
# filename is not mistaken for one.
_VERSION_TOKENS = (
    re.compile(r"^(?P<base>.{5,}?)[\s_.-]*v[\s_.-]?(?P<num>\d+(?:[._]\d+)*)$", re.I),
    re.compile(r"^(?P<base>.{5,}?)[\s_-]+(?P<num>\d+[._]\d+(?:[._]\d+)*)$"),
)


# --- categories ----------------------------------------------------------

# Evaluated in order against the lowercased relative path; first match wins.
# The last rule is a catch-all: a first-party document that survived every
# exclusion belongs in the knowledge base whether or not its name fits a
# category, and `general` says so honestly rather than dropping it.
CATEGORY_RULES: list[tuple[str, str]] = [
    (
        r"case stud|past performance|\brtr\b|sf-?dart|performance requirements",
        "past_performance",
    ),
    (
        r"rfp|rfq|rfi|proposal|solicitation|final submission|submission folder"
        r"|\bsow\b|statement of work|\bpws\b|sources sought|offerors",
        "proposals",
    ),
    (
        r"analytics|power\s*bi|\bbi\b|tableau|quicksight|datastage|data lineage"
        r"|dashboard|reporting|\betl\b|visuali[sz]",
        "analytics_bi",
    ),
    (
        r"data governance|\bdg\b|\bmdm\b|\bepim\b|\bedg\b|\becm\b|data quality"
        r"|data model|taxonomy|architecture|stewardship|metadata|master data"
        r"|data integration|conceptual|logical|physical|standards|framework",
        "technical_methodology",
    ),
    (
        r"project plan|readiness assessment|roadmap|work session|workshop"
        r"|status report|deliverable|charter|kick-?off|lessons learned",
        "project_documentation",
    ),
    (
        r"capabilit|one\s*pager|whitepaper|white paper|flyer|overview|offering"
        r"|who we are|what do we do|services",
        "capabilities",
    ),
]

_YEAR_IN_NAME = re.compile(r"(?<!\d)(20[0-2]\d)(?!\d)")

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}  # fmt: skip
_MONTH_IN_NAME = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b", re.I
)

# Words that distinguish one issue of a document from another rather than one
# document from another. Stripped to find the family a file belongs to.
_EDITION_WORDS = re.compile(
    r"\b(final|new|old|latest|draft|revised|updated|copy|version|v\d+(\.\d+)*"
    r"|20[0-2]\d|" + "|".join(_MONTHS) + r")\b",
    re.I,
)


@dataclass
class Record:
    """One corpus file and the decision made about it."""

    path: str
    filename: str
    extension: str
    size_bytes: int
    category: str | None = None
    brand: str = "Unknown"
    year: int | None = None
    month: int | None = None
    outcome: str = "include"
    reason: str = ""
    duplicate_of: str | None = None
    superseded_by: str | None = None
    content_hash: str | None = None
    rank: tuple = field(default=(), repr=False, compare=False)

    def as_entry(self) -> dict:
        entry = {
            "path": self.path,
            "included": self.outcome == "include",
            "category": self.category,
            "brand": self.brand,
            "file_type": self.extension.lstrip("."),
            "size_bytes": self.size_bytes,
            "year": self.year,
            "selection_reason": self.reason if self.outcome == "include" else None,
            "exclusion_reason": self.reason if self.outcome != "include" else None,
            "duplicate_of": self.duplicate_of,
            "superseded_by": self.superseded_by,
        }

        if self.outcome == "include":
            entry["content_hash"] = self.content_hash

        if self.outcome == "review":
            entry["outcome"] = "review"

        return entry


def _first_match(name: str, rules: list[tuple[str, str]]) -> str | None:
    for pattern, reason in rules:
        if re.search(pattern, name, flags=re.IGNORECASE):
            return reason

    return None


def derive_date(relative_path: str) -> tuple[int | None, int | None]:
    """The document's year and month from its name or folder, or (None, None).

    Never from the filesystem. OneDrive rewrote every modification time in this
    corpus, so mtime records when a file synced, not when it was authored, and
    a guess dressed up as a date is worse than no date at all.
    """

    parts = PurePosixPath(relative_path).parts
    name = parts[-1]

    # The filename first: "Capabilities Statement June 2026" beats the folder it
    # happens to be filed under.
    years = _YEAR_IN_NAME.findall(name)
    if years:
        month = _MONTH_IN_NAME.search(name)
        return max(int(year) for year in years), (
            _MONTHS[month.group(1).lower()] if month else None
        )

    for part in reversed(parts[:-1]):
        years = _YEAR_IN_NAME.findall(part)
        if years and "earlier" not in part.lower():
            return max(int(year) for year in years), None

    return None, None


def derive_brand(relative_path: str) -> str:
    """SunRadia, Aikya, or Unknown.

    Aikya is the historical organisation identity, and its case studies are
    real past performance — kept, but labelled, so a later feature can present
    them as history rather than as a current capability.
    """

    lowered = relative_path.lower()

    if "aikya" in lowered:
        return "Aikya"

    if "sun radia" in lowered or "sunradia" in lowered or "sun_radia" in lowered:
        return "SunRadia"

    return "Unknown"


def categorise(relative_path: str) -> str:
    """Categorise on the filename first, then on the folders above it.

    The corpus is filed under a top-level `capabilities/` folder, so matching
    the whole path would file every technical deliverable and every case study
    under it as a capability statement. The filename is what the author called
    the document; the folder is only where it ended up.
    """

    path = PurePosixPath(relative_path)

    return (
        _first_match(path.stem, CATEGORY_RULES)
        or _first_match(str(path.parent), CATEGORY_RULES)
        or "general"
    )


def document_family(stem: str) -> str:
    """The document a file is an issue of, ignoring date and edition words.

    `Sun Radia Capabilities Statement - Final June 2024` and `... Mar 2026` are
    the same document reissued. Collapsing them is what lets the newest win
    without a hand-maintained list of which one is current.
    """

    without_edition = _EDITION_WORDS.sub(" ", stem.lower())
    return re.sub(r"[^a-z0-9]+", " ", without_edition).strip()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest()


def screen(record: Record) -> tuple[str, str] | None:
    """Return `(outcome, reason)` if this file is not plainly includable.

    Cheap rules only — nothing here opens a file. Sensitivity is tested ahead
    of format so that a pricing spreadsheet is recorded as pricing rather than
    as an unreadable file.
    """

    stem = PurePosixPath(record.path).stem.lower()

    for rules in (SECRET_PATTERNS, PERSONAL_PATTERNS, COMMERCIAL_PATTERNS):
        reason = _first_match(stem, rules)
        if reason:
            return "exclude", reason

    reason = _first_match(stem, REVIEW_PATTERNS)
    if reason:
        return "review", reason

    if record.extension not in SUPPORTED_EXTENSIONS:
        return "exclude", f"unsupported format ({record.extension or 'no extension'})"

    if record.size_bytes == 0:
        return "exclude", "empty file, or a OneDrive placeholder not downloaded"

    if record.size_bytes > MAX_UPLOAD_BYTES:
        return "exclude", (
            f"exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB upload limit "
            f"({record.size_bytes // (1024 * 1024)} MiB)"
        )

    lowered = f"/{record.path.lower()}"

    for fragment, reason in EXCLUDED_DIRECTORIES:
        if fragment in lowered:
            return "exclude", reason

    for rules in (TEMPLATE_PATTERNS, THIRD_PARTY_PATTERNS):
        reason = _first_match(stem, rules)
        if reason:
            return "exclude", reason

    return None


def strip_variant_marker(stem: str) -> tuple[str, str] | None:
    """Return the unmarked name and what the marker was, or None."""

    for pattern, reason in VARIANT_MARKERS:
        stripped, count = re.subn(pattern, "", stem, flags=re.IGNORECASE)
        if count:
            return stripped.strip(), reason

    return None


def version_family(stem: str) -> tuple[str, tuple[int, ...]] | None:
    """Split `LN03. Data Model_v0.3` into its family and `(0, 3)`."""

    for pattern in _VERSION_TOKENS:
        match = pattern.match(stem.strip())
        if match:
            number = tuple(
                int(part) for part in re.split(r"[._]", match.group("num")) if part
            )
            return match.group("base").strip().lower(), number

    return None


def _issue_key(record: Record) -> tuple[int, int]:
    return (record.year or 0, record.month or 0)


def collapse_variants(candidates: list[Record]) -> tuple[list[Record], list[Record]]:
    """Drop marked copies, older versions, and superseded issues.

    All three rules need to see the whole candidate set, which is why they are
    not in `screen()`: whether `X (2).pdf` is a duplicate depends entirely on
    whether `X.pdf` survived, and which issue of a document is current depends
    on what the other issues are.

    Only *dated* issues supersede each other. A document with no derivable date
    is never dropped in favour of one that has a date — the corpus is full of
    undated files that are not reissues of anything, and losing a case study to
    a date-guessing rule is the expensive mistake here.
    """

    by_stem: dict[str, str] = {}
    for record in candidates:
        by_stem.setdefault(PurePosixPath(record.path).stem.strip().lower(), record.path)

    kept: list[Record] = []
    dropped: list[Record] = []

    for record in candidates:
        marker = strip_variant_marker(PurePosixPath(record.path).stem.strip())

        if marker and marker[0].lower() in by_stem:
            record.outcome = "exclude"
            record.reason = f"{marker[1]} of another selected file"
            record.duplicate_of = by_stem[marker[0].lower()]
            dropped.append(record)
            continue

        kept.append(record)

    # Newest numbered version per family, within one directory.
    newest_version: dict[str, tuple[tuple[int, ...], Record]] = {}
    for record in kept:
        family = version_family(PurePosixPath(record.path).stem)
        if not family:
            continue

        key = f"{PurePosixPath(record.path).parent}:{family[0]}"
        current = newest_version.get(key)
        if current is None or family[1] > current[0]:
            newest_version[key] = (family[1], record)

    survivors: list[Record] = []
    for record in kept:
        family = version_family(PurePosixPath(record.path).stem)
        if family:
            key = f"{PurePosixPath(record.path).parent}:{family[0]}"
            winner = newest_version[key][1]
            if winner is not record:
                record.outcome = "exclude"
                record.reason = "an explicitly numbered later version exists"
                record.superseded_by = winner.path
                dropped.append(record)
                continue

        survivors.append(record)

    # Newest dated issue per document family, across the whole corpus. This is
    # what stops sixty reissues of one capability statement from all landing in
    # the knowledge base; it does not touch anything undated.
    newest_issue: dict[str, Record] = {}
    for record in survivors:
        if record.year is None:
            continue

        family = f"{record.category}:{document_family(PurePosixPath(record.path).stem)}"
        current = newest_issue.get(family)
        if current is None or _issue_key(record) > _issue_key(current):
            newest_issue[family] = record

    current_issues: list[Record] = []
    for record in survivors:
        if record.year is not None:
            family = (
                f"{record.category}:{document_family(PurePosixPath(record.path).stem)}"
            )
            winner = newest_issue[family]
            if winner is not record and _issue_key(winner) > _issue_key(record):
                record.outcome = "exclude"
                record.reason = (
                    f"superseded issue of the same document; "
                    f"{winner.year}-{winner.month or 0:02d} is current"
                )
                record.superseded_by = winner.path
                dropped.append(record)
                continue

        current_issues.append(record)

    return current_issues, dropped


def _priority(record: Record) -> tuple:
    """Ordering within a family: newest first, then the format that carries the
    most provenance, then the shallowest path — reliably the filed copy rather
    than one left behind in a working subfolder."""

    return (
        -(record.year or 0),
        -(record.month or 0),
        _FORMAT_RANK.get(record.extension, 9),
        record.path.count("/"),
        record.path,
    )


def unsupported_report(records: list[Record]) -> dict:
    """What is being left behind, and whether it is worth converting.

    A knowledge base is not complete because everything readable was read. This
    is the list of what a later conversion phase would have to reach, ranked so
    that the argument for doing it is visible.
    """

    unsupported = [
        record
        for record in records
        if record.outcome == "exclude" and record.reason.startswith("unsupported")
    ]

    by_extension: dict[str, dict] = {}
    for record in unsupported:
        entry = by_extension.setdefault(
            record.extension or "(none)",
            {"count": 0, "bytes": 0, "categories": defaultdict(int), "high_value": []},
        )
        entry["count"] += 1
        entry["bytes"] += record.size_bytes
        entry["categories"][record.category or "general"] += 1

    # High value means: it survived every rule except the format one, and it is
    # big enough to hold something worth converting. Listed largest first,
    # capped per format so the report stays readable.
    for record in sorted(unsupported, key=lambda r: -r.size_bytes):
        if record.size_bytes < 200 * 1024:
            continue

        entry = by_extension[record.extension or "(none)"]
        if len(entry["high_value"]) < 25:
            entry["high_value"].append(
                {
                    "path": record.path,
                    "category": record.category,
                    "size_bytes": record.size_bytes,
                    "year": record.year,
                }
            )

    return {
        extension: {
            "count": entry["count"],
            "bytes": entry["bytes"],
            "categories": dict(sorted(entry["categories"].items())),
            "high_value_examples": entry["high_value"],
        }
        for extension, entry in sorted(
            by_extension.items(), key=lambda item: -item[1]["count"]
        )
    }


def build(corpus: Path, recorded_root: str | None = None) -> dict:
    candidates: list[Record] = []
    decided: list[Record] = []

    for path in sorted(
        (entry for entry in corpus.rglob("*") if entry.is_file()),
        key=lambda entry: entry.as_posix(),
    ):
        relative = path.relative_to(corpus).as_posix()
        year, month = derive_date(relative)
        record = Record(
            path=relative,
            filename=path.name,
            extension=path.suffix.lower(),
            size_bytes=path.stat().st_size,
            brand=derive_brand(relative),
            year=year,
            month=month,
        )
        record.category = categorise(relative)

        verdict = screen(record)
        if verdict:
            record.outcome, record.reason = verdict
            decided.append(record)
            continue

        candidates.append(record)

    candidates, dropped = collapse_variants(candidates)
    decided.extend(dropped)

    # Only survivors are hashed. Reading 800 files over a synced network folder
    # to find duplicates among the few hundred that matter is time for nothing.
    for record in candidates:
        record.content_hash = _hash_file(corpus / record.path)

    candidates.sort(key=_priority)

    included: list[Record] = []
    seen_hashes: dict[str, str] = {}
    seen_documents: dict[str, str] = {}

    for record in candidates:
        duplicate_of = seen_hashes.get(record.content_hash or "")
        if duplicate_of:
            record.outcome = "exclude"
            record.reason = "byte-identical to another selected file"
            record.duplicate_of = duplicate_of
            decided.append(record)
            continue

        # The same document exported twice — "X.docx" and "X.pdf". Same words,
        # two sets of chunks, two of the five places a search has to give.
        document_key = f"{record.category}:{PurePosixPath(record.path).stem.lower()}"
        twin = seen_documents.get(document_key)
        if twin:
            record.outcome = "exclude"
            record.reason = "the same document in a format that carries less"
            record.duplicate_of = twin
            decided.append(record)
            continue

        record.outcome = "include"
        record.reason = f"{record.category}: first-party knowledge, readable today"
        seen_hashes[record.content_hash or ""] = record.path
        seen_documents[document_key] = record.path
        included.append(record)

    review = [record for record in decided if record.outcome == "review"]
    excluded = [record for record in decided if record.outcome == "exclude"]

    included.sort(key=lambda record: record.path)
    excluded.sort(key=lambda record: record.path)
    review.sort(key=lambda record: record.path)

    counts: dict[str, int] = defaultdict(int)
    for record in included:
        counts[record.category or "general"] += 1

    return {
        "corpus_root": recorded_root or str(corpus),
        "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "summary": {
            "files_inspected": len(included) + len(excluded) + len(review),
            "included": len(included),
            "excluded": len(excluded),
            "needs_review": len(review),
            "included_by_category": {
                category: counts[category]
                for category in CATEGORIES
                if counts[category]
            },
            "included_by_extension": {
                extension: sum(1 for r in included if r.extension == extension)
                for extension in sorted({r.extension for r in included})
            },
            "included_by_brand": {
                brand: sum(1 for r in included if r.brand == brand)
                for brand in sorted({r.brand for r in included})
            },
            "included_bytes": sum(r.size_bytes for r in included),
        },
        "unsupported_formats": unsupported_report(excluded),
        "include": [record.as_entry() for record in included],
        "review": [record.as_entry() for record in review],
        "exclude": [record.as_entry() for record in excluded],
    }


def print_summary(manifest: dict) -> None:
    summary = manifest["summary"]
    print(f"  files inspected : {summary['files_inspected']}")
    print(f"  selected        : {summary['included']}")
    print(f"  needs review    : {summary['needs_review']}")
    print(f"  excluded        : {summary['excluded']}")
    print(f"  selected bytes  : {summary['included_bytes'] / (1024 * 1024):.1f} MiB")

    for label, key in (
        ("by category", "included_by_category"),
        ("by extension", "included_by_extension"),
        ("by brand", "included_by_brand"),
    ):
        print(f"\n  {label}:")
        for name, count in summary[key].items():
            print(f"    {name:<26} {count:>4}")

    reasons: dict[str, int] = defaultdict(int)
    for entry in manifest["exclude"]:
        reasons[re.sub(r"\d+", "N", entry["exclusion_reason"] or "")] += 1

    print("\n  exclusion reasons:")
    for reason, count in sorted(reasons.items(), key=lambda item: -item[1]):
        print(f"    {count:>4}  {reason[:86]}")

    print("\n  unsupported formats left behind:")
    for extension, entry in manifest["unsupported_formats"].items():
        categories = ", ".join(
            f"{name} {count}" for name, count in entry["categories"].items()
        )
        print(
            f"    {extension:<10} {entry['count']:>4} files  "
            f"{entry['bytes'] / (1024 * 1024):>7.1f} MiB  "
            f"high-value {len(entry['high_value_examples']):>3}   {categories[:60]}"
        )

    if manifest["review"]:
        print("\n  flagged for review, not ingested:")
        for entry in manifest["review"]:
            print(f"    {entry['exclusion_reason'][:34]:<36} {entry['path'][:70]}")


def main() -> int:
    repository_root = Path(__file__).resolve().parents[2]

    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True, help="knowledge base root")
    parser.add_argument(
        "--out",
        default=str(repository_root / "knowledge_base_manifest.json"),
        help="where to write the manifest",
    )
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="print the summary without writing the manifest",
    )
    parser.add_argument(
        "--record-root",
        default=None,
        help=(
            "path to record as corpus_root, when the corpus is read through a "
            "different mount of the same folder. Selection is unaffected: every "
            "path in the manifest is relative to the corpus root."
        ),
    )
    arguments = parser.parse_args()

    corpus = Path(arguments.corpus)
    if not corpus.is_dir():
        print(f"corpus not found: {corpus}", file=sys.stderr)
        return 2

    print("=" * 78)
    print(f"KNOWLEDGE BASE MANIFEST — {corpus}")
    print("=" * 78)

    manifest = build(corpus, recorded_root=arguments.record_root)
    print_summary(manifest)

    if not arguments.summary_only:
        Path(arguments.out).write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\n  wrote {arguments.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
