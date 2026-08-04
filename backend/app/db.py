from collections.abc import Iterator
import csv
from datetime import datetime
from typing import Any

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.schema import CreateColumn, CreateIndex, CreateTable
from sqlalchemy import MetaData

from .config import RUNTIME_DIR, get_settings


class Base(DeclarativeBase):
    pass


def make_engine(database_url: str | None = None):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    url = database_url or get_settings().database_url
    connect_args = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


engine = make_engine()


@event.listens_for(engine, "connect")
def configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


def sqlite_literal(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def create_sqlite_add_column_sql(table_name: str, column) -> str | None:
    column_sql = str(CreateColumn(column).compile(dialect=engine.dialect))
    default = getattr(column, "default", None)
    if default is not None and getattr(default, "is_scalar", False) and " DEFAULT " not in column_sql.upper():
        default_sql = f" DEFAULT {sqlite_literal(default.arg)}"
        if " NOT NULL" in column_sql:
            column_sql = column_sql.replace(" NOT NULL", f"{default_sql} NOT NULL", 1)
        else:
            column_sql = f"{column_sql}{default_sql}"

    upper_sql = column_sql.upper()
    can_add = column.nullable or " DEFAULT " in upper_sql
    if not can_add:
        return None
    if "DEFAULT CURRENT_TIMESTAMP" in upper_sql:
        return None

    return f'ALTER TABLE "{table_name}" ADD COLUMN {column_sql}'


def migrate_sqlite_schema(bind=engine) -> list[str]:
    if bind.dialect.name != "sqlite":
        return []

    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    applied: list[str] = []

    with bind.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing_columns:
                    continue
                sql = create_sqlite_add_column_sql(table.name, column)
                if not sql:
                    continue
                connection.execute(text(sql))
                applied.append(f"{table.name}.{column.name}")

    return applied


def migrate_sqlite_app_config_scope(bind=engine) -> bool:
    if bind.dialect.name != "sqlite":
        return False

    inspector = inspect(bind)
    if "app_config" not in set(inspector.get_table_names()):
        return False

    columns = inspector.get_columns("app_config")
    column_names = {column["name"] for column in columns}
    id_column = next((column for column in columns if column["name"] == "id"), None)
    key_column = next((column for column in columns if column["name"] == "key"), None)
    already_migrated = (
        id_column is not None
        and id_column.get("primary_key")
        and key_column is not None
        and not key_column.get("primary_key")
        and "workspace_id" in column_names
    )
    if already_migrated:
        return False

    workspace_expr = "workspace_id" if "workspace_id" in column_names else "'default'"
    with bind.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS app_config_migration_new"))
        connection.execute(
            text(
                """
                CREATE TABLE app_config_migration_new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    key VARCHAR NOT NULL,
                    value TEXT NOT NULL,
                    workspace_id VARCHAR NOT NULL DEFAULT 'default',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(workspace_id) REFERENCES workspaces (id)
                )
                """
            )
        )
        connection.execute(
            text(
                f"""
                INSERT INTO app_config_migration_new (key, value, workspace_id, created_at, updated_at)
                SELECT key, value, {workspace_expr}, created_at, updated_at
                FROM app_config
                """
            )
        )
        connection.execute(text("DROP TABLE app_config"))
        connection.execute(text("ALTER TABLE app_config_migration_new RENAME TO app_config"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_app_config_workspace_id ON app_config (workspace_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_app_config_key ON app_config (key)"))
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_app_config_workspace_key ON app_config (workspace_id, key)"
            )
        )
    return True


def migrate_legacy_conversation_stages(bind=engine) -> int:
    if bind.dialect.name != "sqlite":
        return 0

    inspector = inspect(bind)
    if "conversations" not in set(inspector.get_table_names()):
        return 0

    with bind.begin() as connection:
        result = connection.execute(
            text("UPDATE conversations SET current_stage = 'unit_matching' WHERE current_stage = 'triage'")
        )
        return result.rowcount or 0


def sqlite_index_columns(connection, index_name: str) -> list[str]:
    return [row[2] for row in connection.execute(text(f'PRAGMA index_info("{index_name}")')).all()]


def sqlite_unique_indexes(connection, table_name: str) -> list[tuple[str, list[str]]]:
    indexes: list[tuple[str, list[str]]] = []
    for row in connection.execute(text(f'PRAGMA index_list("{table_name}")')).all():
        if not row[2]:
            continue
        indexes.append((row[1], sqlite_index_columns(connection, row[1])))
    return indexes


def rebuild_sqlite_table(connection, table_name: str) -> None:
    """Recreate a SQLite table from current SQLAlchemy metadata while preserving rows."""
    table = Base.metadata.tables[table_name]
    temp_name = f"{table_name}_migration_new"
    temp_metadata = MetaData()
    temp_table = table.to_metadata(temp_metadata, name=temp_name)
    common_columns = [column.name for column in table.columns]
    quoted_columns = ", ".join(f'"{column}"' for column in common_columns)

    connection.execute(text("PRAGMA foreign_keys=OFF"))
    connection.execute(text(f'DROP TABLE IF EXISTS "{temp_name}"'))
    connection.execute(
        text(
            str(
                CreateTable(temp_table, include_foreign_key_constraints=[]).compile(
                    dialect=connection.engine.dialect
                )
            )
        )
    )
    connection.execute(
        text(
            f'INSERT INTO "{temp_name}" ({quoted_columns}) '
            f'SELECT {quoted_columns} FROM "{table_name}"'
        )
    )
    connection.execute(text(f'DROP TABLE "{table_name}"'))
    connection.execute(text(f'ALTER TABLE "{temp_name}" RENAME TO "{table_name}"'))
    for index in table.indexes:
        connection.execute(text(str(CreateIndex(index).compile(dialect=connection.engine.dialect))))
    connection.execute(text("PRAGMA foreign_keys=ON"))


def migrate_sqlite_drop_legacy_global_unique_indexes(bind=engine) -> list[str]:
    if bind.dialect.name != "sqlite":
        return []

    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    legacy_unique_shapes = {
        "contacts": {("chat_jid",)},
        "messages": {("chat_jid", "message_id")},
        "properties": {("property_id",)},
        "property_media": {("property_id", "file_path")},
        "swing_candidates": {("source_property_id", "candidate_property_id")},
    }
    applied: list[str] = []
    with bind.begin() as connection:
        for table_name, legacy_shapes in legacy_unique_shapes.items():
            if table_name not in existing_tables:
                continue
            unique_shapes = {tuple(columns) for _, columns in sqlite_unique_indexes(connection, table_name)}
            if not unique_shapes.intersection(legacy_shapes):
                continue
            rebuild_sqlite_table(connection, table_name)
            applied.append(table_name)
    return applied


def export_sqlite_table_to_csv(connection, table_name: str, backup_dir) -> str | None:
    rows = connection.execute(text(f'SELECT * FROM "{table_name}"')).mappings().all()
    columns = list(rows[0].keys()) if rows else [row[1] for row in connection.execute(text(f'PRAGMA table_info("{table_name}")')).all()]
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    path = backup_dir / f"{table_name}-{timestamp}.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
    return str(path)


def migrate_sqlite_drop_draft_tables(bind=engine) -> list[str]:
    if bind.dialect.name != "sqlite":
        return []

    inspector = inspect(bind)
    existing_tables = set(inspector.get_table_names())
    draft_tables = [table for table in ("draft_attachments", "drafts") if table in existing_tables]
    if not draft_tables:
        return []

    backup_dir = RUNTIME_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    applied: list[str] = []
    with bind.begin() as connection:
        for table_name in draft_tables:
            export_sqlite_table_to_csv(connection, table_name, backup_dir)
            backup_table = f"{table_name}_backup_{timestamp}"
            connection.execute(text(f'CREATE TABLE IF NOT EXISTS "{backup_table}" AS SELECT * FROM "{table_name}"'))
            applied.append(f"{table_name}->{backup_table}")
        connection.execute(text('DROP TABLE IF EXISTS "draft_attachments"'))
        connection.execute(text('DROP TABLE IF EXISTS "drafts"'))
    return applied


def migrate_sqlite_drop_template_table(bind=engine) -> list[str]:
    if bind.dialect.name != "sqlite":
        return []

    inspector = inspect(bind)
    if "templates" not in set(inspector.get_table_names()):
        return []

    backup_dir = RUNTIME_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    with bind.begin() as connection:
        export_sqlite_table_to_csv(connection, "templates", backup_dir)
        backup_table = f"templates_backup_{timestamp}"
        connection.execute(text(f'CREATE TABLE IF NOT EXISTS "{backup_table}" AS SELECT * FROM "templates"'))
        connection.execute(text('DROP TABLE IF EXISTS "templates"'))
    return [f"templates->{backup_table}"]


def migrate_sqlite_drop_prompt_table(bind=engine) -> list[str]:
    if bind.dialect.name != "sqlite":
        return []

    inspector = inspect(bind)
    if "prompts" not in set(inspector.get_table_names()):
        return []

    backup_dir = RUNTIME_DIR / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    with bind.begin() as connection:
        export_sqlite_table_to_csv(connection, "prompts", backup_dir)
        backup_table = f"prompts_backup_{timestamp}"
        connection.execute(text(f'CREATE TABLE IF NOT EXISTS "{backup_table}" AS SELECT * FROM "prompts"'))
        connection.execute(text('DROP TABLE IF EXISTS "prompts"'))
    return [f"prompts->{backup_table}"]


def init_db() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    migrate_sqlite_schema(engine)
    migrate_sqlite_app_config_scope(engine)
    migrate_sqlite_drop_legacy_global_unique_indexes(engine)
    migrate_legacy_conversation_stages(engine)
    migrate_sqlite_drop_draft_tables(engine)
    migrate_sqlite_drop_template_table(engine)
    migrate_sqlite_drop_prompt_table(engine)
