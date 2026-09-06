"""Rendering a stored activity report as a Word document.

Word rather than PDF for one reason: `python-docx` is already a dependency of
this project — the ingestion pipeline parses `.docx` uploads with it — so this
adds a renderer rather than a package. A PDF would mean a new runtime
dependency for a format nobody has asked for over the one every manager here
already edits.

The renderer reads the **stored** report and nothing else. That is what keeps a
downloaded document identical to the one on screen, and identical again a month
later: it cannot reach a task table or a mailbox, so there is no path by which
downloading a historical report could produce different figures from viewing
it. The sections arrive already decided by `activity_report`; this module
chooses fonts.
"""

from __future__ import annotations

import io

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt, RGBColor

#: The one colour in the document, used for the organisation name only.
_INK = RGBColor(0x1F, 0x2A, 0x37)

ORGANISATION = "SUN RADIA"


def _heading(document: Document, text: str, *, size: int, spacing: int = 6) -> None:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(spacing)
    paragraph.paragraph_format.space_after = Pt(2)

    run = paragraph.add_run(text)
    run.bold = True
    run.font.size = Pt(size)
    run.font.color.rgb = _INK


def _meta(document: Document, label: str, value: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(0)

    run = paragraph.add_run(f"{label}: ")
    run.bold = True
    run.font.size = Pt(10)

    plain = paragraph.add_run(value)
    plain.font.size = Pt(10)


def render(content: dict, *, report_title: str) -> bytes:
    """One stored report as `.docx` bytes.

    `content` is the JSON written when the report was generated. A row with no
    `activity` block — one written before this renderer existed — produces a
    document saying so rather than an empty one, because a blank page is
    indistinguishable from a period in which nothing happened.
    """

    activity = content.get("activity") or {}
    sections = activity.get("sections") or []

    document = Document()

    normal = document.styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)

    _heading(document, ORGANISATION, size=13, spacing=0)
    _heading(document, report_title.upper(), size=16, spacing=0)

    if activity.get("user_name"):
        _meta(document, "Employee", activity["user_name"])

    if activity.get("period_label"):
        _meta(document, "Reporting period", activity["period_label"])

    if activity.get("generated_at"):
        _meta(document, "Generated", activity["generated_at"][:16].replace("T", " "))

    document.add_paragraph()

    if not sections:
        note = document.add_paragraph(
            "This report has no recorded content. It was generated before the "
            "activity sections existed, or for a period in which nothing was "
            "recorded."
        )
        note.alignment = WD_ALIGN_PARAGRAPH.LEFT

        return _bytes(document)

    for index, section in enumerate(sections, start=1):
        _heading(document, f"{index}. {section['title']}", size=11)

        if section.get("note"):
            paragraph = document.add_paragraph(section["note"])
            paragraph.paragraph_format.space_after = Pt(4)
            for run in paragraph.runs:
                run.italic = True

        for line in section.get("lines") or []:
            paragraph = document.add_paragraph(style="List Bullet")
            paragraph.paragraph_format.space_after = Pt(0)
            paragraph.add_run(line["text"])

            if line.get("detail"):
                detail = paragraph.add_run(f" — {line['detail']}")
                detail.italic = True

    return _bytes(document)


def _bytes(document: Document) -> bytes:
    buffer = io.BytesIO()
    document.save(buffer)

    return buffer.getvalue()


def filename_for(*, report_title: str, period_key: str) -> str:
    """A filename somebody can find again in their downloads folder."""

    stem = report_title.lower().replace(" ", "-")

    return f"sunradia-{stem}-{period_key}.docx"
