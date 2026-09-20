# IntelliQ database migrations

`0001_initial` is a non-destructive baseline. It uses `IF NOT EXISTS` semantics
so a PostgreSQL database created by the former SQLAlchemy `create_all` startup
can be adopted without dropping or recreating populated tables. Missing tables
are added and the Alembic revision is recorded.

`0002_operational_indexes` adds indexes used by the current organization-scoped
list queries. `0003_scrub_tokens` clears legacy token columns if
an old local schema still has them; it never stores or recreates those columns.

Do not run `stamp` against an unknown schema. Run `pnpm --filter api db:migrate`
or let application startup run the same upgrade. Review a backup and write an
explicit migration for any schema that fails the unversioned compatibility
check. Upgrades do not import SQLite or delete the PostgreSQL volume.
