"""No database fixtures here on purpose.

The Graph client is HTTP and nothing else, and its tests should not need
SQLAlchemy imported to collect — the same reasoning that keeps the parser
tests out of the database fixtures.
"""

__all__: list[str] = []
