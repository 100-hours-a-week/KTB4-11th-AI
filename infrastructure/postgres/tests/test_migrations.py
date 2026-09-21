"""Checks on the migration harness itself.

These do not need a database: they pin the wiring that makes
`alembic upgrade head` work from the repository root with no -c flag.
"""

import configparser
import pathlib

# tests -> postgres -> infrastructure -> repo root
ROOT = pathlib.Path(__file__).resolve().parents[3]


def _config() -> configparser.RawConfigParser:
    # RawConfigParser, not ConfigParser: alembic.ini contains %(here)s, and
    # the interpolating parser raises InterpolationMissingOptionError on it.
    parser = configparser.RawConfigParser()
    parser.read(ROOT / "alembic.ini")
    return parser


def test_alembic_ini_lives_at_the_repository_root():
    assert (ROOT / "alembic.ini").is_file()


def test_script_location_points_at_infrastructure():
    location = _config()["alembic"]["script_location"]

    assert location.endswith("infrastructure/postgres/migrations")


def test_no_database_url_is_committed():
    assert "sqlalchemy.url" not in _config()["alembic"]


def test_versions_directory_exists():
    assert (ROOT / "infrastructure/postgres/migrations/versions").is_dir()
