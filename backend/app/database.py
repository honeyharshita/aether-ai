import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import SQLAlchemyError
from app.config import settings


def _build_engine(database_url: str):
    if database_url.startswith("sqlite"):
        return create_engine(database_url, pool_pre_ping=True)

    try:
        import psycopg2  # noqa: F401
    except ModuleNotFoundError:
        fallback_url = "sqlite:///./aetherai.db"
        print(
            "psycopg2 is not installed; falling back to SQLite at aetherai.db",
            flush=True,
        )
        return create_engine(fallback_url, pool_pre_ping=True)

    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except (SQLAlchemyError, OSError, ImportError):
        fallback_url = "sqlite:///./aetherai.db"
        print(
            "PostgreSQL is not reachable in the current environment; falling back to SQLite at aetherai.db",
            flush=True,
        )
        return create_engine(fallback_url, pool_pre_ping=True)


engine = _build_engine(settings.DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def ensure_commit_files_column():
    with engine.begin() as connection:
        columns = {column["name"] for column in inspect(connection).get_columns("commits")}
        if "files" not in columns:
            connection.execute(text("ALTER TABLE commits ADD COLUMN files JSON"))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
