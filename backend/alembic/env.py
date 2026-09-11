from logging.config import fileConfig
import os
from pathlib import Path

from alembic import context
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import engine_from_config, inspect, pool, text
from sqlalchemy.engine import make_url

from backend.app.core.config import settings
from backend.app.db.session import Base
from backend.app.models import entities  # noqa: F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _validate_migration_invocation() -> None:
    """拒绝从 Codex 工作树直接迁移，并限制迁移目标数据库。

    Docker 容器内没有 Git 元数据，因此 Compose 为 Backend/Worker 显式传入
    ``RECRUIT_SYSTEM_MIGRATION_CONTEXT=container``。主机直接执行 Alembic 时，
    则必须位于真实项目根目录，且该目录的 ``.git`` 必须是主检出目录而非
    Codex 工作树使用的 gitdir 文件。
    """
    database_name = make_url(settings.database_url).database
    allowed_databases = {
        item.strip()
        for item in os.getenv(
            "RECRUIT_SYSTEM_MIGRATION_ALLOWED_DATABASE", "recruit_ai"
        ).split(",")
        if item.strip()
    }
    if database_name not in allowed_databases:
        raise RuntimeError(
            "拒绝 Alembic 迁移：目标数据库不在允许名单中。"
            f" 当前={database_name!r}，允许={sorted(allowed_databases)!r}。"
        )

    if os.getenv("RECRUIT_SYSTEM_MIGRATION_CONTEXT") == "container":
        return

    project_root = Path(__file__).resolve().parents[2]
    current_directory = Path.cwd().resolve()
    git_marker = project_root / ".git"
    if current_directory != project_root or not git_marker.is_dir():
        raise RuntimeError(
            "拒绝 Alembic 迁移：只能从真实项目根目录执行。"
            " Codex 隔离工作树不得连接开发数据库执行迁移。"
        )

def run_migrations_offline() -> None:
    _validate_migration_invocation()
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    _validate_migration_invocation()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # The original baseline migration creates tables from the current ORM
        # metadata, so replaying every historical migration on an empty database
        # would attempt to create newer tables and columns twice. For a truly
        # empty database, create the current schema and stamp it at head. Existing
        # databases still follow the normal incremental Alembic path below.
        if not inspect(connection).get_table_names():
            # Alembic 默认把 version_num 建为 varchar(32)，但本项目的 revision ID
            # 可能超过 32 字符。空库初始化先建立足够长的版本表，避免 stamp head
            # 在所有业务表已创建后因版本号截断而整体失败。
            connection.execute(text(
                "CREATE TABLE alembic_version (version_num VARCHAR(128) NOT NULL PRIMARY KEY)"
            ))
            target_metadata.create_all(connection)
            MigrationContext.configure(connection).stamp(
                ScriptDirectory.from_config(config),
                "head",
            )
            connection.commit()
            return
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        connection.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
