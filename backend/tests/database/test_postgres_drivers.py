"""Every PostgreSQL URL form the deployment can be given has an installed driver.

Render supplies `DATABASE_URL` as a bare `postgresql://...`. SQLAlchemy 2.1
maps that to Psycopg 3, not psycopg2, so a requirements file with only
psycopg2 deployed cleanly and then failed at startup with
`ModuleNotFoundError: No module named 'psycopg'`. Building an engine loads the
DBAPI module without connecting, which is exactly the failure to catch here.
"""

import pytest
from sqlalchemy import create_engine


@pytest.mark.parametrize(
    "url",
    [
        # Render's connectionString form.
        "postgresql://user:password@localhost:5432/db",
        # Psycopg 3, named explicitly.
        "postgresql+psycopg://user:password@localhost:5432/db",
        # The local default in settings.py and .env.example.
        "postgresql+psycopg2://user:password@localhost:5432/db",
    ],
)
def test_the_driver_for_each_url_form_is_installed(url: str) -> None:
    engine = create_engine(url)

    try:
        assert engine.dialect.name == "postgresql"
    finally:
        engine.dispose()
