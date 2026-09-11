from backend.app.db.session import Base, engine
from backend.app.models import entities  # noqa: F401


def init_db() -> None:
    # PostgreSQL is migrated by the container entrypoint with Alembic.
    # SQLite create_all is only for isolated local development and tests.
    if engine.dialect.name != "sqlite":
        return
    Base.metadata.create_all(bind=engine)

