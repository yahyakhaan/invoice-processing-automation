from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine

from invoice_agent.config import ROOT


def sqlalchemy_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def psycopg_database_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@lru_cache(maxsize=8)
def get_engine(database_url: str) -> Engine:
    return create_engine(
        sqlalchemy_database_url(database_url),
        pool_pre_ping=True,
        pool_recycle=300,
    )


def alembic_config(database_url: str) -> Config:
    config = Config(str(Path(ROOT) / "alembic.ini"))
    # ConfigParser treats percent signs as interpolation markers.
    config.set_main_option("sqlalchemy.url", sqlalchemy_database_url(database_url).replace("%", "%%"))
    return config


@lru_cache(maxsize=8)
def run_migrations(database_url: str) -> None:
    command.upgrade(alembic_config(database_url), "head")

