# Every stored resource is scoped to an owning user from the first migration,
# so that adding authentication later is a change of where this value comes
# from — not a schema change and not a backfill. Until auth exists, all
# ingestion runs as this single placeholder owner.
MVP_USER_ID = "mvp-user"

SUPPORTED_CONTENT_TYPES: dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (
        "pptx"
    ),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.ms-excel.sheet.macroenabled.12": "xlsx",
    "text/html": "html",
    "application/xhtml+xml": "html",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/x-markdown": "md",
}

SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".pptx": "pptx",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".html": "html",
    ".htm": "html",
    ".txt": "txt",
    ".md": "md",
    ".markdown": "md",
}

#: What a person is shown when a format is not supported. Assembled from the
#: table rather than written out, so a format added above cannot be missing here.
SUPPORTED_FORMAT_SUMMARY = "PDF, DOCX, PPTX, XLSX, HTML, TXT and Markdown"

# Formats this application meets in a real corpus, recognises, and deliberately
# does **not** parse. Naming them is the point: a generic "unsupported type"
# tells somebody nothing about whether the file is junk or a document they need,
# and these each have a specific answer. Half-parsing a legacy binary would be
# worse than refusing it — it produces a document full of the wrong characters
# that then gets embedded, retrieved and cited as though it were the text.
#
# Each value completes the sentence "... and was not indexed."
KNOWN_UNSUPPORTED_EXTENSIONS: dict[str, str] = {
    ".doc": (
        "Legacy Word (.doc) is a binary OLE2 format this application does not "
        "read. Save it as .docx"
    ),
    ".xls": (
        "Legacy Excel (.xls) is a binary OLE2 format this application does not "
        "read. Save it as .xlsx"
    ),
    ".ppt": (
        "Legacy PowerPoint (.ppt) is a binary OLE2 format this application does "
        "not read. Save it as .pptx"
    ),
    ".mmap": (
        "MindManager (.mmap) is a proprietary mind-map format with no text "
        "layer this application can read. Export it as PDF or DOCX"
    ),
    ".msg": (
        "Outlook message (.msg) is a binary format this application does not "
        "read. Save the message as PDF, or use the Email Agent"
    ),
    ".zip": (
        "An archive is a container rather than a document. Extract it and let "
        "the files inside synchronise individually"
    ),
    ".vsd": ("Visio (.vsd) is not read. Export it as PDF"),
    ".vsdx": ("Visio (.vsdx) is not read. Export it as PDF"),
    ".one": ("OneNote (.one) is not read. Export the section as PDF"),
}


# A document whose format is supported and whose bytes were read perfectly
# well, but which carries no text layer: a scan, a photographed page, a deck of
# images. It is not a failure of this application and not an unsupported
# format, and counting it as either was misleading — 30 files in the SunRadia
# corpus are in exactly this state.
#
# The tag leads the sentence so the classification is greppable in a log, a
# `documents.error_message` column and a sync report alike, and names the
# remedy — OCR — that this phase deliberately does not implement.
NO_TEXT_LAYER_TAG = "OCR_REQUIRED / NO_TEXT_LAYER"

NO_TEXT_LAYER_REASON = (
    f"{NO_TEXT_LAYER_TAG}: no text could be extracted. The file was read "
    "successfully but contains no text layer — a scanned or image-only "
    "document. Optical character recognition would be required to index it, "
    "and is not implemented."
)
