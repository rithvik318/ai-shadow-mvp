"""Builders for realistic upload fixtures, generated in memory.

Binary fixtures are built rather than committed so the test suite has no
opaque blobs, and so page and heading structure is visible in the test that
depends on it.
"""

import io

import docx


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


def build_markdown(sections: list[tuple[str, str]]) -> bytes:
    """Return Markdown bytes built from (heading, body) pairs."""

    parts = [f"# {heading}\n\n{body}\n" for heading, body in sections]
    return "\n".join(parts).encode("utf-8")


def build_text(body: str) -> bytes:
    return body.encode("utf-8")
