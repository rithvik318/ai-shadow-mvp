"""Choose which corpus documents belong in the MVP knowledge base.

Walks the corpus read-only, applies an ordered set of explicit rules to every
file, and writes a manifest recording what was selected, what was not, and why
in every case. Nothing is uploaded, moved, renamed or deleted here — the
manifest is a decision, and `ingest_kb_manifest.py` is what acts on it.

Deterministic: the same corpus produces byte-identical output. Files are walked
in sorted order, every rule is a pure function of path, size and content hash,
and no clock or filesystem timestamp is consulted.

**Modification times are never used.** OneDrive rewrote them across this
corpus, so a file's mtime says when it synced, not when it was written. A
document's year is taken from its name or its folder or it is left null.

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

# Formats the ingestion pipeline can read today. Everything else is excluded
# rather than converted: legacy conversion is a later phase, not this one.
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
)

# How many documents each category may contribute. The corpus holds far more
# than an MVP needs — 60-odd capability statements alone, most of them the same
# document at different dates. The cap forces a choice; `_PRIORITY` below makes
# that choice the same one every time, and every dropped file is recorded with
# the reason it lost.
CATEGORY_CAPS = {
    "capabilities": 20,
    "past_performance": 20,
    "proposals": 10,
    "analytics_bi": 18,
    "technical_methodology": 24,
}

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
    ("/z. archive/", "archive folder"),
    ("/archive/", "archive folder"),
]


# --- filename rules ------------------------------------------------------

# Order matters: the first pattern that matches decides, and the categories are
# listed most-sensitive first so that a "Pricing Template" is excluded as
# pricing rather than as a template.
SENSITIVE_PATTERNS: list[tuple[str, str]] = [
    (r"email\s*address|contact\s*list|contacts\b", "contact list"),
    (r"\bresume|\bcv\b|key team members", "personal resume"),
    (r"pricing|rate\s*card|\bclin\b|price\s*schedule", "pricing or rate information"),
    (r"direct\s*deposit|\bw-?9\b|bank\s*details", "financial account details"),
    (
        r"incentive model|compensation|\bsalary\b",
        "internal compensation material",
    ),
    (
        r"representations and certifications|\bsigned\b",
        "signed contractual form, not knowledge content",
    ),
]

TEMPLATE_PATTERNS: list[tuple[str, str]] = [
    (r"\btemplate\b", "empty template"),
    (r"\bblank\b", "blank form"),
    (r"proposal form", "submission form, not knowledge content"),
    (r"header template|color palette", "layout asset"),
    (
        r"meeting notes|work session",
        "meeting notes rather than a deliverable",
    ),
    (
        r"physical data model",
        "schema dump: column definitions rather than methodology",
    ),
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

# The capability story has to be current: an MVP that answers "what does
# SunRadia do?" from a 2022 one-pager is worse than one that declines. Anything
# in this category older than this, where the year can be established at all,
# is treated as superseded.
CURRENT_CAPABILITY_YEAR = 2025

# Undated capability documents kept anyway, because each addresses an audience
# no current-year document covers and there is no newer equivalent to prefer.
DISTINCT_UNDATED_CAPABILITIES = (
    "sun radia govt capabilities",
    "sun radia capabilities statement - commercial",
    "sun radia capability statement",
)


# --- categories ----------------------------------------------------------

# Evaluated in order against the lowercased relative path; first match wins.
CATEGORY_RULES: list[tuple[str, str]] = [
    (
        r"case stud|past performance|\brtr\b|sf-?dart|performance requirements",
        "past_performance",
    ),
    (
        r"rfp|rfq|rfi|proposal|solicitation|final submission|submission folder|\bsow\b",
        "proposals",
    ),
    (
        r"analytics|power\s*bi|\bbi\b|tableau|quicksight|datastage|data lineage"
        r"|dashboard|reporting",
        "analytics_bi",
    ),
    (
        r"data governance|\bdg\b|\bmdm\b|\bepim\b|\bedg\b|\becm\b|data quality"
        r"|data model|taxonomy|architecture|stewardship|metadata|master data",
        "technical_methodology",
    ),
    (r"capabilit|one\s*pager|whitepaper|white paper|flyer", "capabilities"),
]

_YEAR_IN_NAME = re.compile(r"(?<!\d)(20[0-2]\d)(?!\d)")


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
    reason: str = ""
    content_hash: str | None = None
    rank: tuple = field(default=(), repr=False, compare=False)

    def as_include(self) -> dict:
        return {
            "path": self.path,
            "category": self.category,
            "brand": self.brand,
            "year": self.year,
            "extension": self.extension,
            "size_bytes": self.size_bytes,
            "content_hash": self.content_hash,
            "reason": self.reason,
        }

    def as_exclude(self) -> dict:
        return {
            "path": self.path,
            "category": self.category,
            "extension": self.extension,
            "size_bytes": self.size_bytes,
            "reason": self.reason,
        }


def _first_match(name: str, rules: list[tuple[str, str]]) -> str | None:
    for pattern, reason in rules:
        if re.search(pattern, name, flags=re.IGNORECASE):
            return reason

    return None


def derive_year(relative_path: str) -> int | None:
    """The document's year from its name or folder, or None.

    Never from the filesystem. OneDrive rewrote every modification time in this
    corpus, so mtime records when a file synced, not when it was authored, and
    a guess dressed up as a date is worse than no date at all.
    """

    parts = PurePosixPath(relative_path).parts
    # Filename first: "Capabilities Statement June 2026" beats the folder it
    # happens to be filed under.
    years = _YEAR_IN_NAME.findall(parts[-1])
    if years:
        return max(int(year) for year in years)

    for part in reversed(parts[:-1]):
        years = _YEAR_IN_NAME.findall(part)
        if years and "earlier" not in part.lower():
            return max(int(year) for year in years)

    return None


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


def categorise(relative_path: str) -> str | None:
    return _first_match(relative_path, CATEGORY_RULES)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)

    return digest.hexdigest()


def screen(record: Record) -> str | None:
    """Return the reason this file is excluded, or None to keep considering it.

    Cheap rules only — nothing here opens a file.

    Sensitivity is tested first, ahead of format and size. A pricing schedule
    that happens to be a spreadsheet would be excluded either way, but recorded
    as "unsupported format" it would vanish from the count of sensitive
    material — and that count is the one someone will want to audit.
    """

    stem = PurePosixPath(record.path).stem.lower()

    reason = _first_match(stem, SENSITIVE_PATTERNS)
    if reason:
        return reason

    if record.extension not in SUPPORTED_EXTENSIONS:
        return f"unsupported format ({record.extension or 'no extension'})"

    if record.size_bytes == 0:
        return "empty file, or a OneDrive placeholder that is not downloaded"

    if record.size_bytes > MAX_UPLOAD_BYTES:
        return (
            f"exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MiB upload limit "
            f"({record.size_bytes // (1024 * 1024)} MiB)"
        )

    lowered = record.path.lower()

    for fragment, reason in EXCLUDED_DIRECTORIES:
        if fragment in f"/{lowered}":
            return reason

    for rules in (TEMPLATE_PATTERNS, THIRD_PARTY_PATTERNS):
        reason = _first_match(stem, rules)
        if reason:
            return reason

    if record.category is None:
        return "outside the five MVP knowledge categories"

    if record.category == "capabilities":
        if record.year is not None and record.year < CURRENT_CAPABILITY_YEAR:
            return (
                f"superseded capability material from {record.year}; "
                f"{CURRENT_CAPABILITY_YEAR}+ versions exist"
            )

        if record.year is None and not any(
            marker in stem for marker in DISTINCT_UNDATED_CAPABILITIES
        ):
            return "undated capability material with no distinct audience"

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


def collapse_variants(candidates: list[Record]) -> tuple[list[Record], list[Record]]:
    """Drop marked copies and older versions of documents we already have.

    Both rules need to see the whole candidate set, which is why they are not
    in `screen()`: whether `X (2).pdf` is a duplicate depends entirely on
    whether `X.pdf` survived, and which version of a deliverable is newest
    depends on what the other versions are.
    """

    by_stem: dict[str, str] = {}
    for record in candidates:
        by_stem.setdefault(PurePosixPath(record.path).stem.strip().lower(), record.path)

    kept: list[Record] = []
    dropped: list[Record] = []

    for record in candidates:
        stem = PurePosixPath(record.path).stem.strip()
        marker = strip_variant_marker(stem)

        if marker and marker[0].lower() in by_stem:
            record.reason = f"{marker[1]} of {by_stem[marker[0].lower()]}"
            dropped.append(record)
            continue

        kept.append(record)

    # Newest version per family wins; the rest are recorded against it.
    newest: dict[str, tuple[tuple[int, ...], Record]] = {}
    for record in kept:
        family = version_family(PurePosixPath(record.path).stem)
        if not family:
            continue

        key = f"{PurePosixPath(record.path).parent}:{family[0]}"
        current = newest.get(key)
        if current is None or family[1] > current[0]:
            newest[key] = (family[1], record)

    survivors: list[Record] = []
    for record in kept:
        family = version_family(PurePosixPath(record.path).stem)
        if family:
            key = f"{PurePosixPath(record.path).parent}:{family[0]}"
            winner = newest[key][1]
            if winner is not record:
                record.reason = f"superseded by {winner.path}"
                dropped.append(record)
                continue

        survivors.append(record)

    return survivors, dropped


def _priority(record: Record) -> tuple:
    """Ordering within a category: newest first, then best format, then the
    shallowest path — which is reliably the filed copy rather than a copy left
    in a working subfolder."""

    return (
        -(record.year or 0),
        _FORMAT_RANK.get(record.extension, 9),
        record.path.count("/"),
        record.path,
    )


def build(corpus: Path, recorded_root: str | None = None) -> dict:
    included: list[Record] = []
    excluded: list[Record] = []

    for path in sorted(
        (entry for entry in corpus.rglob("*") if entry.is_file()),
        key=lambda entry: entry.as_posix(),
    ):
        relative = path.relative_to(corpus).as_posix()
        record = Record(
            path=relative,
            filename=path.name,
            extension=path.suffix.lower(),
            size_bytes=path.stat().st_size,
            brand=derive_brand(relative),
            year=derive_year(relative),
        )
        record.category = categorise(relative)

        reason = screen(record)
        if reason:
            record.reason = reason
            excluded.append(record)
            continue

        included.append(record)

    included, dropped = collapse_variants(included)
    excluded.extend(dropped)

    # Only survivors are hashed. Reading 800 files over a synced network folder
    # to find duplicates among the 150 that matter is time spent for nothing.
    for record in included:
        record.content_hash = _hash_file(corpus / record.path)

    included.sort(key=_priority)

    kept: list[Record] = []
    seen_hashes: dict[str, str] = {}
    seen_documents: dict[str, str] = {}
    counts: dict[str, int] = defaultdict(int)

    for record in included:
        duplicate_of = seen_hashes.get(record.content_hash or "")
        if duplicate_of:
            record.reason = f"byte-identical to {duplicate_of}"
            excluded.append(record)
            continue

        # The same document exported twice — "X.docx" and "X.pdf". Same words,
        # two sets of chunks, two of the five places a search has to give.
        document_key = f"{record.category}:{PurePosixPath(record.path).stem.lower()}"
        twin = seen_documents.get(document_key)
        if twin:
            record.reason = f"same document as {twin}, in a less useful format"
            excluded.append(record)
            continue

        if counts[record.category] >= CATEGORY_CAPS[record.category]:
            record.reason = (
                f"{record.category} cap of {CATEGORY_CAPS[record.category]} reached; "
                "ranked below the documents kept"
            )
            excluded.append(record)
            continue

        record.reason = f"{record.category}: selected for the MVP knowledge base"
        seen_hashes[record.content_hash or ""] = record.path
        seen_documents[document_key] = record.path
        counts[record.category] += 1
        kept.append(record)

    kept.sort(key=lambda record: record.path)
    excluded.sort(key=lambda record: record.path)

    return {
        "corpus_root": recorded_root or str(corpus),
        "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "category_caps": CATEGORY_CAPS,
        "summary": {
            "files_inspected": len(kept) + len(excluded),
            "included": len(kept),
            "excluded": len(excluded),
            "included_by_category": {
                category: counts[category] for category in CATEGORIES
            },
            "included_by_extension": {
                extension: sum(1 for r in kept if r.extension == extension)
                for extension in sorted({r.extension for r in kept})
            },
            "included_by_brand": {
                brand: sum(1 for r in kept if r.brand == brand)
                for brand in sorted({r.brand for r in kept})
            },
            "included_bytes": sum(r.size_bytes for r in kept),
        },
        "include": [record.as_include() for record in kept],
        "exclude": [record.as_exclude() for record in excluded],
    }


def print_summary(manifest: dict) -> None:
    summary = manifest["summary"]
    print(f"  files inspected : {summary['files_inspected']}")
    print(f"  selected        : {summary['included']}")
    print(f"  excluded        : {summary['excluded']}")
    print(f"  selected bytes  : {summary['included_bytes'] / (1024 * 1024):.1f} MiB")

    print("\n  by category:")
    for category, count in summary["included_by_category"].items():
        print(f"    {category:<24} {count:>4}")

    print("\n  by extension:")
    for extension, count in summary["included_by_extension"].items():
        print(f"    {extension:<24} {count:>4}")

    print("\n  by brand:")
    for brand, count in summary["included_by_brand"].items():
        print(f"    {brand:<24} {count:>4}")

    reasons: dict[str, int] = defaultdict(int)
    for entry in manifest["exclude"]:
        reasons[re.sub(r"\d+", "N", entry["reason"])] += 1

    print("\n  top exclusion reasons:")
    for reason, count in sorted(reasons.items(), key=lambda item: -item[1])[:15]:
        print(f"    {count:>4}  {reason[:88]}")


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
