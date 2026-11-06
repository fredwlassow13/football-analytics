from alembic import context
from alembic.operations.ops import MigrationScript
from alembic.runtime.migration import MigrationContext

from football_analytics.infrastructure.database import Base, get_engine


def guard_autogenerate(
    migration_context: MigrationContext,
    revision: tuple[str, ...],
    directives: list[MigrationScript],
) -> None:
    if not Base.metadata.tables:
        raise RuntimeError(
            "Autogenerate is disabled: SQLAlchemy metadata has no registered tables. "
            "Create explicit migrations with alembic revision -m instead."
        )


def run_migrations() -> None:
    if context.is_offline_mode():
        from football_analytics.infrastructure.settings import get_settings

        context.configure(
            url=str(get_settings().database_url),
            target_metadata=Base.metadata,
            literal_binds=True,
            include_schemas=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    else:
        with get_engine().connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=Base.metadata,
                include_schemas=True,
                process_revision_directives=guard_autogenerate,
            )
            with context.begin_transaction():
                context.run_migrations()


run_migrations()
