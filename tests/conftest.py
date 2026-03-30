"""
Shared pytest fixtures for the archery tracker test suite.
"""

import os
import sys
import pytest

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

SAMPLE_HTML_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'eksempelresultat_1200.html'
)


@pytest.fixture
def sample_html() -> str:
    """Return the contents of the sample archer HTML file."""
    with open(SAMPLE_HTML_PATH, encoding='utf-8') as f:
        return f.read()


@pytest.fixture
def tmp_db(monkeypatch, tmp_path):
    """
    Redirect all database operations to a fresh temporary SQLite file.
    Returns the path to the temp DB file.
    """
    db_file = str(tmp_path / 'test_archery.db')
    monkeypatch.setattr('src.database.DATABASE_PATH', db_file)
    # Re-run init so tables are created in the temp file
    from src.database import init_database
    init_database()
    return db_file


@pytest.fixture
def flask_client(monkeypatch, tmp_path):
    """
    Flask test client wired to a temporary database.
    Patches DATABASE_PATH before the app module is used so no real DB is touched.
    """
    db_file = str(tmp_path / 'test_archery.db')
    monkeypatch.setattr('src.database.DATABASE_PATH', db_file)

    from src.database import init_database
    init_database()

    from src.app import app
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client
