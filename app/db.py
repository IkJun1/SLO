from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


settings = get_settings()

engine = create_engine(
    f"sqlite:///{settings.db_path}",
    connect_args={"check_same_thread": False},
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def _apply_sqlite_migrations() -> None:
    with engine.begin() as conn:
        table_names = {
            str(row[0])
            for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
        }

        if "graph_nodes" in table_names:
            conn.execute(text("DROP TABLE graph_nodes"))
            table_names.remove("graph_nodes")

        if "messages" in table_names:
            columns = {
                str(row["name"])
                for row in conn.execute(text("PRAGMA table_info(messages)")).mappings().all()
            }
            if "source_doc_paths" not in columns:
                conn.execute(text("ALTER TABLE messages ADD COLUMN source_doc_paths TEXT NOT NULL DEFAULT '[]'"))


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _apply_sqlite_migrations()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
