"""Dialect-aware vector distance for similarity search.

pgvector exposes cosine distance as the `<=>` operator, which only Postgres
understands. The test suite runs on SQLite, where the same column is JSON (see
`app/models/document.py`), so a query written directly against `<=>` would be
untestable — and an untested search query is how PR 1's NULL-semantics defect
survived review.

This module keeps that difference in one place. Production emits `<=>` and uses
the HNSW index; SQLite emits a `cosine_distance(a, b)` call, which the test
fixtures register as a Python function. Service code writes the expression once
and never learns which dialect it is on.
"""

from sqlalchemy import Float, literal
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql import ColumnElement
from sqlalchemy.sql.functions import FunctionElement


class CosineDistance(FunctionElement):
    """Cosine distance between a stored vector column and a query vector.

    Returns a value in `[0, 2]`: 0 for identical direction, 1 for orthogonal,
    2 for opposite. Similarity is `1 - distance`.
    """

    type = Float()
    name = "cosine_distance"
    inherit_cache = True


@compiles(CosineDistance)
def _compile_unsupported(element: CosineDistance, compiler, **kw) -> str:
    raise NotImplementedError(
        f"Cosine distance is not implemented for dialect "
        f"'{compiler.dialect.name}'. Supported: postgresql (pgvector), "
        f"sqlite (test fixture function)."
    )


@compiles(CosineDistance, "postgresql")
def _compile_postgresql(element: CosineDistance, compiler, **kw) -> str:
    column, vector = element.clauses
    return f"({compiler.process(column, **kw)} <=> {compiler.process(vector, **kw)})"


@compiles(CosineDistance, "sqlite")
def _compile_sqlite(element: CosineDistance, compiler, **kw) -> str:
    column, vector = element.clauses
    return (
        f"cosine_distance("
        f"{compiler.process(column, **kw)}, {compiler.process(vector, **kw)})"
    )


def cosine_distance(column: ColumnElement, vector: list[float]) -> CosineDistance:
    """Build a cosine-distance expression between `column` and `vector`.

    The query vector is bound with the column's own type, so pgvector adapts it
    to a `vector` literal on Postgres and the JSON variant serialises it on
    SQLite — without either detail leaking into the caller.

    `.expression` unwraps the ORM attribute to the underlying column, which is
    what carries the dialect-aware type; reading `.type` off the attribute
    directly is not part of the public API.
    """

    expression = column.expression

    return CosineDistance(expression, literal(vector, type_=expression.type))
