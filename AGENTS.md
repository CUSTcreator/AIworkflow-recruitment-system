# 招聘系统项目操作规范

## 唯一真实源码目录

本项目唯一允许默认写入、构建和迁移的目录为：

`D:\For studying-or-working\Develop-Project\recruit-system-V1.0`

除非用户明确要求在 Codex 隔离工作树中修改，否则任何 Agent 都必须只在上述真实源码目录修改项目文件。不得把 Agent 当前被平台分配的工作目录自动视为可写的项目目录；当当前目录属于 `.codex\worktrees\...` 时，必须改用上述真实源码目录。

`C:\Users\86138\.codex\worktrees\<id>\recruit-system-V1.0` 是 Codex 隔离工作树，仅可用于只读检查、差异审查和经用户明确授权的实验性改动；不得将其当作默认开发目录。

## Docker 与数据库迁移

- 只允许在唯一真实源码目录执行 `docker compose`、镜像构建、容器重建和 Alembic 数据库迁移。
- 在任何 Codex 隔离工作树中，禁止执行 `docker compose up`、`docker compose build`、`docker compose down`、`alembic upgrade`、`alembic downgrade`，以及直接对开发 PostgreSQL 执行写操作。
- 每次容器更新前，先确认 Compose 的 build context 指向唯一真实源码目录，并确认 `.env` 完整可用。
- 涉及 `drop_table`、`drop_column`、批量删除数据的迁移，执行前必须向用户说明影响并获得明确确认。

## 交付验证

- 修改完成后，说明实际写入的绝对路径。
- 仅在真实源码目录重建和验证 Docker 容器。
- 不得把隔离工作树中的未审查改动自动覆盖到真实源码目录；必须先进行逐文件差异审查。
