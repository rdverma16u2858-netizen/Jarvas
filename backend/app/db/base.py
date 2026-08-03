"""
The declarative base every database model inherits from.
═══════════════════════════════════════════════════════════════════════════

WHY THIS FILE EXISTS
    Two reasons, both about avoiding repetition later:

    1. `Base.metadata` is the registry of every table. Alembic reads it to
       auto-generate migrations, so models must inherit from THIS Base — not a
       second one declared elsewhere, or migrations will silently miss tables.

    2. `TimestampMixin` puts `created_at` / `updated_at` on every table without
       copying two columns into a dozen models.

WHY THE TABLE NAME IS AUTOMATIC
    A model named `ChatSession` becomes table `chat_sessions`. Deriving it
    removes a naming decision (and a naming inconsistency) from every model.
"""

import re
from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column

# Explicit naming conventions for constraints and indexes.
#
# Without these, databases auto-generate names like `ck_1a2b3c`, and Alembic
# then cannot write a migration that drops a constraint by name — a migration
# that works on SQLite and fails on Postgres. Setting them once here makes
# every constraint name deterministic and portable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",  # index
    "uq": "uq_%(table_name)s_%(column_0_name)s",  # unique
    "ck": "ck_%(table_name)s_%(constraint_name)s",  # check
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",  # primary key
}


def _to_snake_case(name: str) -> str:
    """`ChatSession` -> `chat_session`, `MCQOption` -> `mcq_option`.

    The two substitutions handle the acronym case: a naive regex turns
    `MCQOption` into `m_c_q_option`. The first pass splits `MCQO` + `ption`,
    the second splits the remaining lower->upper boundaries.
    """
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()


class Base(DeclarativeBase):
    """Parent class for every ORM model in the project."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    @declared_attr.directive
    def __tablename__(cls) -> str:  # noqa: N805
        """Derive a plural snake_case table name from the class name.

        `Problem` -> `problems`, `ChatSession` -> `chat_sessions`.
        Override by declaring `__tablename__` explicitly on the model.
        """
        name = _to_snake_case(cls.__name__)
        # Crude but sufficient pluralisation for the nouns this project uses.
        if name.endswith(("s", "x", "z", "ch", "sh")):
            return name + "es"
        if name.endswith("y") and not name.endswith(("ay", "ey", "iy", "oy", "uy")):
            return name[:-1] + "ies"
        return name + "s"


class TimestampMixin:
    """Adds `created_at` and `updated_at` to any model that inherits it.

    Both are set by the DATABASE (`server_default=func.now()`), not by Python.
    That matters: rows written by a migration, a psql session, or a second app
    instance on a different machine all get consistent timestamps from one
    clock, instead of whatever each writer's system time happened to be.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
