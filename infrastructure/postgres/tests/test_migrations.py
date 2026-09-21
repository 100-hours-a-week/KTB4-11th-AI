import configparser
import pathlib

REPO_ROOT = next(
    parent
    for parent in pathlib.Path(__file__).resolve().parents
    if (parent / "alembic.ini").is_file()
)
MIGRATIONS_DIR = "infrastructure/postgres/migrations"


def _alembic_config_without_interpolation() -> configparser.RawConfigParser:
    parser = configparser.RawConfigParser()
    parser.read(REPO_ROOT / "alembic.ini")
    return parser


def test_alembic_ini_lives_at_the_repository_root():
    assert (REPO_ROOT / "alembic.ini").is_file()


def test_script_location_points_at_infrastructure():
    location = _alembic_config_without_interpolation()["alembic"]["script_location"]

    assert location.endswith(MIGRATIONS_DIR)


def test_no_database_url_is_committed():
    assert "sqlalchemy.url" not in _alembic_config_without_interpolation()["alembic"]


def test_versions_directory_exists():
    assert (REPO_ROOT / MIGRATIONS_DIR / "versions").is_dir()
