"""Builders for realistic upload fixtures, generated in memory.

Binary fixtures are built rather than committed so the test suite has no
opaque blobs, and so page and heading structure is visible in the test that
depends on it.
"""

import io

import docx
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from pptx import Presentation
from pptx.util import Inches

# `python-docx` registers neither prefix, so both are declared by hand.
_MC_NS = 'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
_WPS_NS = (
    'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape"'
)


def build_pdf(pages: list[str]) -> bytes:
    """Return a minimal, valid single-font PDF with one text line per page.

    Written by hand rather than with a PDF generation library so the test
    suite needs no dependency that production code does not already have.
    """

    objects: list[bytes] = []

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    font_id = 3 + 2 * len(pages)
    page_ids = [3 + 2 * index for index in range(len(pages))]

    # Placeholder ordering: catalog (1), pages (2), then page/content pairs.
    add(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    add(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())

    for index, text in enumerate(pages):
        content_id = page_ids[index] + 1
        add(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
                f"/Contents {content_id} 0 R >>"
            ).encode()
        )
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode()
        add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))

    add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []

    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"

    xref_offset = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset

    out += b"trailer\n<< /Size %d /Root 1 0 R >>\n" % (len(objects) + 1)
    out += b"startxref\n%d\n%%%%EOF\n" % xref_offset

    return bytes(out)


def build_docx(sections: list[tuple[str, str]]) -> bytes:
    """Return a DOCX built from (heading, body) pairs."""

    document = docx.Document()
    for heading, body in sections:
        document.add_heading(heading, level=1)
        document.add_paragraph(body)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def build_pptx(slides: list[dict]) -> bytes:
    """Return a PPTX assembled from slide specifications.

    Each slide is a dict, every key optional:

        title  str                     the title placeholder
        body   str                     the content placeholder, newline-split
        boxes  list[(inches, str)]     free text boxes, at the given top offset
        group  list[(inches, str)]     text boxes inside one grouped shape
        table  list[list[str]]         a table, first row treated as a header
        notes  str                     speaker notes
        hidden bool                    marks the slide `show="0"`

    `boxes` and `group` take an explicit vertical offset so a test can add
    shapes in an order that disagrees with their position on the slide, which
    is the only way to prove the parser sorts by geometry rather than by the
    z-order `python-pptx` iterates in.
    """

    presentation = Presentation()
    blank = presentation.slide_layouts[6]
    titled = presentation.slide_layouts[1]

    for spec in slides:
        wants_placeholder = "title" in spec or "body" in spec
        slide = presentation.slides.add_slide(titled if wants_placeholder else blank)

        if wants_placeholder:
            slide.shapes.title.text = spec.get("title", "")
            slide.placeholders[1].text = spec.get("body", "")

        for top, text in spec.get("boxes", []):
            box = slide.shapes.add_textbox(
                Inches(1), Inches(top), Inches(4), Inches(0.8)
            )
            box.text_frame.text = text

        if group := spec.get("group"):
            shape = slide.shapes.add_group_shape()
            for top, text in group:
                child = shape.shapes.add_textbox(
                    Inches(1), Inches(top), Inches(3), Inches(0.8)
                )
                child.text_frame.text = text

        if grid := spec.get("table"):
            frame = slide.shapes.add_table(
                len(grid), len(grid[0]), Inches(1), Inches(6), Inches(6), Inches(1)
            )
            for r, row in enumerate(grid):
                for c, value in enumerate(row):
                    frame.table.cell(r, c).text = value

        if notes := spec.get("notes"):
            slide.notes_slide.notes_text_frame.text = notes

        if spec.get("hidden"):
            # `python-pptx` has no API for slide visibility; `show="0"` is the
            # attribute PowerPoint writes when a slide is hidden.
            slide._element.set("show", "0")

    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def build_pptx_merged_table(grid: list[list[str]]) -> bytes:
    """Return a one-slide PPTX whose table has its first two header cells merged."""

    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    frame = slide.shapes.add_table(
        len(grid), len(grid[0]), Inches(1), Inches(1), Inches(6), Inches(1)
    )
    table = frame.table

    for r, row in enumerate(grid):
        for c, value in enumerate(row):
            table.cell(r, c).text = value

    table.cell(0, 0).merge(table.cell(0, 1))

    buffer = io.BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def build_markdown(sections: list[tuple[str, str]]) -> bytes:
    """Return Markdown bytes built from (heading, body) pairs."""

    parts = [f"# {heading}\n\n{body}\n" for heading, body in sections]
    return "\n".join(parts).encode("utf-8")


def build_text(body: str) -> bytes:
    return body.encode("utf-8")


def build_docx_blocks(blocks: list[tuple[str, object]]) -> bytes:
    """Return a DOCX assembled from ordered blocks.

    Each block is a (kind, payload) pair:

        ("heading",   "Overview")
        ("paragraph", "Body text.")
        ("table",     [["Name", "Role"], ["Ann", "Lead"]])
        ("textbox",   "Text inside a shape")
        ("merged",    [["Name", "Role"], ["Ann", "Lead"]])   # row 0 merged

    Written in the order given, so a test can assert that the parser preserves
    document order rather than grouping by type. `python-docx` has no API for
    text boxes, so those are injected as raw `w:txbxContent` — the same element
    Word writes for both DrawingML and legacy VML shapes.
    """

    document = docx.Document()

    for kind, payload in blocks:
        if kind == "heading":
            document.add_heading(str(payload), level=1)
        elif kind == "paragraph":
            document.add_paragraph(str(payload))
        elif kind == "textbox":
            paragraph = document.add_paragraph()
            lines = "".join(
                f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>"
                for line in str(payload).split("\n")
            )
            paragraph._p.append(
                parse_xml(
                    f"<w:r {nsdecls('w')}><w:pict><w:txbxContent>"
                    f"{lines}"
                    f"</w:txbxContent></w:pict></w:r>"
                )
            )
        elif kind in ("table", "merged"):
            grid = payload
            table = document.add_table(rows=len(grid), cols=len(grid[0]))
            for r, row in enumerate(grid):
                for c, value in enumerate(row):
                    table.cell(r, c).text = value
            if kind == "merged":
                table.cell(0, 0).merge(table.cell(0, 1))
        else:
            raise ValueError(f"unknown block kind: {kind}")

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# The wrappers Word puts between a paragraph and its runs. `python-docx` reads
# through only the first two, so the rest are invisible to `Paragraph.text`.
_RUN_WRAPPERS = {
    "direct": '<w:r {w}><w:t xml:space="preserve">{text}</w:t></w:r>',
    "hyperlink": (
        '<w:hyperlink {w} w:anchor="bookmark">'
        '<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'
        "</w:hyperlink>"
    ),
    "ins": (
        '<w:ins {w} w:id="101" w:author="Reviewer" w:date="2024-01-01T00:00:00Z">'
        '<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'
        "</w:ins>"
    ),
    "fldSimple": (
        '<w:fldSimple {w} w:instr=" PAGEREF _Toc1 ">'
        '<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'
        "</w:fldSimple>"
    ),
    "smartTag": (
        '<w:smartTag {w} w:uri="urn:schemas-microsoft-com:office:smarttags"'
        ' w:element="place">'
        '<w:r><w:t xml:space="preserve">{text}</w:t></w:r>'
        "</w:smartTag>"
    ),
    # Deleted text is stored under a different tag, which is what makes it
    # excludable without a rule of its own.
    "del": (
        '<w:del {w} w:id="102" w:author="Reviewer" w:date="2024-01-01T00:00:00Z">'
        '<w:r><w:delText xml:space="preserve">{text}</w:delText></w:r>'
        "</w:del>"
    ),
}


def build_docx_wrapped_runs(paragraphs: list[list[tuple[str, str]]]) -> bytes:
    """Return a DOCX whose runs sit inside the given WordprocessingML wrappers.

    Each paragraph is a list of (wrapper, text) pairs, written in order:

        [[("direct", "Visible. "), ("ins", "Inserted.")],
         [("del", "Removed.")]]

    Wrappers are `direct`, `hyperlink`, `ins`, `fldSimple`, `smartTag` and
    `del`. `python-docx` has no API for any of them, so they are injected as
    raw XML — the same shapes Word writes for tracked changes, cross-reference
    fields and smart tags.
    """

    document = docx.Document()

    for runs in paragraphs:
        paragraph = document.add_paragraph()
        for wrapper, text in runs:
            if wrapper not in _RUN_WRAPPERS:
                raise ValueError(f"unknown run wrapper: {wrapper}")

            paragraph._p.append(
                parse_xml(_RUN_WRAPPERS[wrapper].format(w=nsdecls("w"), text=text))
            )

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def build_docx_table_with_wrapped_runs(text: str) -> bytes:
    """Return a DOCX whose single table cell holds text inside a `w:ins`."""

    document = docx.Document()
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "Name"
    table.cell(0, 1).text = "Role"
    table.cell(1, 0).text = "Ann"

    cell = table.cell(1, 1)
    cell.paragraphs[0]._p.append(
        parse_xml(_RUN_WRAPPERS["ins"].format(w=nsdecls("w"), text=text))
    )

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def build_docx_alternate_content_text_box(text: str) -> bytes:
    """Return a DOCX whose text box is written in both shape encodings.

    This is what Word itself emits: an `mc:AlternateContent` pair holding the
    same text as DrawingML (`mc:Choice`) and as legacy VML (`mc:Fallback`).
    Only the DrawingML branch is displayed, so a reader that takes both sees
    every shape twice.
    """

    document = docx.Document()
    paragraph = document.add_paragraph()
    body = f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
    paragraph._p.append(
        parse_xml(
            f"<w:r {nsdecls('w')} {_MC_NS} {_WPS_NS}>"
            f'<mc:AlternateContent><mc:Choice Requires="wps">'
            f"<w:drawing><wps:txbx><w:txbxContent>{body}</w:txbxContent>"
            f"</wps:txbx></w:drawing>"
            f"</mc:Choice><mc:Fallback>"
            f"<w:pict><w:txbxContent>{body}</w:txbxContent></w:pict>"
            f"</mc:Fallback></mc:AlternateContent></w:r>"
        )
    )

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def build_docx_table_with_text_box(grid: list[list[str]], text: str) -> bytes:
    """Return a DOCX table whose last cell also contains a text box.

    Word's capability-statement layouts routinely put a shape inside a table
    cell, where `_Cell.text` cannot see it.
    """

    document = docx.Document()
    table = document.add_table(rows=len(grid), cols=len(grid[0]))
    for r, row in enumerate(grid):
        for c, value in enumerate(row):
            table.cell(r, c).text = value

    cell = table.cell(len(grid) - 1, len(grid[0]) - 1)
    cell.paragraphs[0]._p.append(
        parse_xml(
            f"<w:r {nsdecls('w')}><w:pict><w:txbxContent>"
            f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
            f"</w:txbxContent></w:pict></w:r>"
        )
    )

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def build_docx_nested_table(outer: str, inner: list[list[str]]) -> bytes:
    """Return a DOCX whose table cell contains another table."""

    document = docx.Document()
    table = document.add_table(rows=1, cols=1)
    cell = table.cell(0, 0)
    cell.text = outer

    nested = cell.add_table(rows=len(inner), cols=len(inner[0]))
    for r, row in enumerate(inner):
        for c, value in enumerate(row):
            nested.cell(r, c).text = value

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
