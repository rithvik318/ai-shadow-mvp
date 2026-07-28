# Every stored resource is scoped to an owning user from the first migration,
# so that adding authentication later is a change of where this value comes
# from — not a schema change and not a backfill. Until auth exists, all
# ingestion runs as this single placeholder owner.
MVP_USER_ID = "mvp-user"

SUPPORTED_CONTENT_TYPES: dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "text/plain": "txt",
    "text/markdown": "md",
    "text/x-markdown": "md",
}

SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".txt": "txt",
    ".md": "md",
    ".markdown": "md",
}
