"""Persistent SQLite asset index"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.index import ScanDiagnostics, scan_archive

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS archives (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL COLLATE NOCASE UNIQUE,
    size INTEGER NOT NULL,
    modified_ns INTEGER NOT NULL,
    scanned_at TEXT NOT NULL,
    containers INTEGER NOT NULL,
    assets INTEGER NOT NULL,
    auxiliary_containers INTEGER NOT NULL,
    invalid_containers INTEGER NOT NULL,
    metadata_errors INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS assets (
    archive_id INTEGER NOT NULL,
    uid TEXT NOT NULL COLLATE NOCASE,
    file_type INTEGER NOT NULL,
    container_offset INTEGER NOT NULL,
    container_size INTEGER NOT NULL,
    unpacked_size INTEGER NOT NULL,
    name_hash BLOB NOT NULL,
    flags INTEGER NOT NULL,
    data_offset INTEGER NOT NULL,

    PRIMARY KEY (archive_id, container_offset),

    FOREIGN KEY (archive_id)
        REFERENCES archives(id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS assets_uid
ON assets(uid);

CREATE INDEX IF NOT EXISTS assets_file_type
ON assets(file_type);
"""

@dataclass(frozen=True)
class ArchiveIndexResult:
    database: Path
    archive: Path
    asset_count: int
    skipped: bool
    diagnostics: ScanDiagnostics

def uid_to_text(uid: int) -> str:
    if not 0 <= uid <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError(f"UID is outside the unsigned 64-bit range: {uid}")

    return f"{uid:016X}"

def _diagnostics_from_row(row: sqlite3.Row) -> ScanDiagnostics:
    return ScanDiagnostics(
        containers=row["containers"],
        assets=row["assets"],
        auxiliary_containers=row["auxiliary_containers"],
        invalid_containers=row["invalid_containers"],
        metadata_errors=row["metadata_errors"]
    )

def _open_database(path: str | Path) -> tuple[Path, sqlite3.Connection]:
    database = Path(path).expanduser().resolve()
    database.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_SCHEMA)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    return database, connection

def index_archive(archive: str | Path, database: str | Path, *, force: bool = False) -> ArchiveIndexResult:
    archive_path = Path(archive).expanduser().resolve()

    if not archive_path.is_file():
        raise FileNotFoundError(f"Archive not found: {archive_path}")

    archive_stat = archive_path.stat()
    database_path, connection = _open_database(database)

    try:
        existing = connection.execute(
            """
            SELECT *
            FROM archives
            WHERE path = ?
            """,
            (str(archive_path),)
        ).fetchone()

        if (not force and existing is not None and existing["size"] == archive_stat.st_size and existing["modified_ns"] == archive_stat.st_mtime_ns):
            return ArchiveIndexResult(
                database=database_path,
                archive=archive_path,
                asset_count=existing["assets"],
                skipped=True,
                diagnostics=_diagnostics_from_row(existing)
            )

        diagnostics = ScanDiagnostics()
        asset_count = 0

        with connection:
            connection.execute("DELETE FROM archives WHERE path = ?", (str(archive_path),))

            cursor = connection.execute(
                """
                INSERT INTO archives (
                    path,
                    size,
                    modified_ns,
                    scanned_at,
                    containers,
                    assets,
                    auxiliary_containers,
                    invalid_containers,
                    metadata_errors
                )
                VALUES (?, ?, ?, ?, 0, 0, 0, 0, 0)
                """,
                (
                    str(archive_path),
                    archive_stat.st_size,
                    archive_stat.st_mtime_ns,
                    datetime.now(timezone.utc).isoformat()
                )
            )

            archive_id = cursor.lastrowid

            for record in scan_archive(archive_path, diagnostics):
                connection.execute(
                    """
                    INSERT INTO assets (
                        archive_id,
                        uid,
                        file_type,
                        container_offset,
                        container_size,
                        unpacked_size,
                        name_hash,
                        flags,
                        data_offset
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        archive_id,
                        uid_to_text(record.uid),
                        record.file_type,
                        record.container_offset,
                        record.container_size,
                        record.unpacked_size,
                        record.metadata.name_hash,
                        record.metadata.flags,
                        record.metadata.data_offset
                    )
                )

                asset_count += 1

            connection.execute(
                """
                UPDATE archives
                SET
                    containers = ?,
                    assets = ?,
                    auxiliary_containers = ?,
                    invalid_containers = ?,
                    metadata_errors = ?
                WHERE id = ?
                """,
                (
                    diagnostics.containers,
                    diagnostics.assets,
                    diagnostics.auxiliary_containers,
                    diagnostics.invalid_containers,
                    diagnostics.metadata_errors,
                    archive_id
                )
            )

        return ArchiveIndexResult(
            database=database_path,
            archive=archive_path,
            asset_count=asset_count,
            skipped=False,
            diagnostics=diagnostics
        )
    finally:
        connection.close()