"""Persistent SQLite asset index"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.index import (
    AssetIndex,
    AssetRecord,
    ScanDiagnostics,
    scan_archive
)
from src.metadata import FileMetadata

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

def load_asset_index(database: str | Path, uids: set[int]) -> AssetIndex:
    """Load selected UIDs from the asset index"""

    database_path = Path(database).expanduser().resolve()

    if not database_path.is_file():
        raise FileNotFoundError(f"Asset database not found: {database_path}")

    requested = sorted(uid_to_text(uid) for uid in uids)
    index = AssetIndex()

    if not requested:
        return index

    try:
        connection = sqlite3.connect(f"{database_path.as_uri()}?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise ValueError(f"Could not open asset database: {error}") from error

    connection.row_factory = sqlite3.Row

    try:
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]

        if schema_version != SCHEMA_VERSION:
            raise ValueError(f"Unsupported asset database schema {schema_version}, expected {SCHEMA_VERSION}")

        batch_size = 500

        for start in range(0, len(requested), batch_size):
            batch = requested[start:start + batch_size]
            placeholders = ",".join("?" for _ in batch)

            rows = connection.execute(
                f"""
                SELECT
                    assets.uid,
                    assets.file_type,
                    assets.container_offset,
                    assets.container_size,
                    assets.unpacked_size,
                    assets.name_hash,
                    assets.flags,
                    assets.data_offset,
                    archives.path AS archive_path
                FROM assets
                JOIN archives
                    ON archives.id = assets.archive_id
                WHERE assets.uid IN ({placeholders})
                ORDER BY
                    assets.uid,
                    archives.path COLLATE NOCASE,
                    assets.container_offset
                """,
                batch
            )

            for row in rows:
                uid = int(row["uid"], 16)
                name_hash = bytes(row["name_hash"])

                metadata = FileMetadata(
                    name_length=len(name_hash),
                    container_type=2,
                    flags=row["flags"],
                    name_hash=name_hash,
                    file_type=row["file_type"],
                    uid=uid,
                    data_offset=row["data_offset"]
                )

                index.add(
                    AssetRecord(
                        uid=uid,
                        file_type=row["file_type"],
                        archive=Path(row["archive_path"]),
                        container_offset=row["container_offset"],
                        container_size=row["container_size"],
                        unpacked_size=row["unpacked_size"],
                        metadata=metadata
                    )
                )
    except sqlite3.Error as error:
        raise ValueError(f"Could not read asset database: {error}") from error
    finally:
        connection.close()

    return index
