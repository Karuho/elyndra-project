# Elyndra 0.7.16-alpha

## Controlled SQL and database toolchain

Elyndra 0.7.16-alpha adds a controlled SQL and SQLite toolchain for deterministic project inspection, static validation, migration review, read-only schema inspection and query-plan analysis.

The release keeps database execution separate from knowledge. Alexandria explains SQL and database concepts; skills inspect and verify authorized local material. Importing the optional knowledge package never grants access to files or databases.

## Skills

The release registers:

- `sql.project_inspect`
- `sql.static_validate`
- `sql.migration_validate`
- `sqlite.schema_inspect`
- `sqlite.query_plan`
- `sql.verify_project`

## Read-only boundaries

Static validation does not connect to a database. It identifies statement categories, malformed delimiters, mutating SQL, destructive migrations and sensitive constructs without applying them.

SQLite schema inspection opens databases with `mode=ro`, enables `PRAGMA query_only`, installs an authorizer that rejects write and schema actions, and reads only catalog and PRAGMA metadata. It does not count or return user rows.

`sqlite.query_plan` accepts exactly one read-only `SELECT` or `WITH` statement and executes only `EXPLAIN QUERY PLAN` against a read-only SQLite connection.

## Explicitly excluded operations

This toolchain does not:

- execute migrations;
- execute DDL or DML;
- connect automatically to PostgreSQL, MySQL or MariaDB;
- restore or create backups;
- install database clients or drivers;
- accept arbitrary SQL execution;
- enable SQLite extensions or attach databases.

## Profiles, history and control center

SQL profiles store dialect, enabled stages, file limits, exclusions and destructive-operation policy without granting authorization. Verification results are stored in the generic history repository and exposed through CLI and the loopback-only control center.

## Alexandria package

The optional `programming.sql-databases.modern-basic` package covers SQL categories, migrations, SQLite read-only inspection, query plans, indexes, transactions, parameters, backups and trust boundaries. Installation is local, checksum-verified and never marks the source as reviewed automatically.

## Migration

The SQLite application schema advances to version 24 with the additive `sql_project_profiles` table. Existing profiles, knowledge, conversations and verification history remain intact.
