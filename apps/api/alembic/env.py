"""Alembic runtime setup: safe by default.

* Credentials come from app.credentials (vt_migrator), loaded only when a migration really
  connects. `--sql` (offline) never needs one.
* A run applies everything and then ROLLS BACK unless `-x commit=1` is given exactly.
* The connection must be the vt_migrator role (not a superuser, no BYPASSRLS).
* Downgrades are refused unless `-x allow_downgrade=1` is given exactly.
"""

from sqlalchemy import create_engine, pool

from alembic import context
from app.db.migrate import (
    MigrationError,
    assert_migration_role,
    engine_url,
    guard_downgrade,
    parse_options,
    redact_error,
)
from app.db.models import metadata

config = context.config
options = parse_options(context.get_x_argument(as_dictionary=True))
VERSION_SCHEMA = "vt"


def on_version_apply(ctx, step, heads, run_args, **kw):  # type: ignore[no-untyped-def]
    guard_downgrade(not step.is_upgrade, options)


def run_offline() -> None:
    context.configure(
        url="postgresql+psycopg://",
        target_metadata=metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table_schema=VERSION_SCHEMA,
        on_version_apply=on_version_apply,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_with(connection) -> None:  # type: ignore[no-untyped-def]
    outer = connection.begin()
    try:
        assert_migration_role(connection)
        connection.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
        context.configure(
            connection=connection,
            target_metadata=metadata,
            version_table_schema=VERSION_SCHEMA,
            on_version_apply=on_version_apply,
        )
        with context.begin_transaction():
            context.run_migrations()
    except BaseException:
        outer.rollback()
        raise
    if options.commit:
        outer.commit()
        print("Migrations COMMITTED.")
    else:
        outer.rollback()
        print(
            "DRY RUN OK: every migration applied and was ROLLED BACK. NOTHING WAS CHANGED. "
            "Re-run with -x commit=1 to apply."
        )


def run_online() -> None:
    supplied = config.attributes.get("connection")
    if supplied is not None:  # tooling that already holds a vt_migrator connection
        run_with(supplied)
        return
    url = engine_url()
    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            run_with(connection)
    except MigrationError:
        raise
    except Exception as exc:
        raise MigrationError(redact_error(exc, url.render_as_string(hide_password=False))) from None


if context.is_offline_mode():
    run_offline()
else:
    run_online()
