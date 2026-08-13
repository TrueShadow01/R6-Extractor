"""Tests for the Forge extraction foundation"""

from __future__ import annotations

import json
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from src.decompress import OodleUnavailableError
from src.extractor import extract_raw_archive
from src.database import (
    AssetName,
    index_archive,
    load_asset_index,
    search_asset_names,
    upsert_asset_names
)
from src.index import (
    ScanDiagnostics,
    build_index,
    scan_archive
)
from src.metadata import (
    InvalidFileMetadata,
    parse_file_metadata
)
from src.parser import (
    CONTAINER_MAGIC,
    SCIMITAR_MAGIC,
    ForgeFormatError,
    iter_container_offsets,
    map_archive,
    parse_container,
    read_container
)

TEST_UID = 0x123456789ABCDEF0
TEST_FILE_TYPE = 0xABEB2DFB
TEST_ASSET_TYPE = b"synthetic mesh asset data"

def make_container(payload: bytes) -> bytes:
    """Build one uncompressed synthetic Siege container"""

    header = CONTAINER_MAGIC
    header += struct.pack(
        "<HHBHI",
        3,          # block type
        0x000F,     # marker
        0,          # flag
        0x8004,     # descriptor
        1           # chunk content
    )

    header += struct.pack("<II", len(payload), len(payload))
    header += struct.pack("<I", 0x12345678)

    return header + payload

def make_file_payload(asset_data: bytes = TEST_ASSET_TYPE) -> bytes:
    name_hash = b"synthetic-name"

    metadata = struct.pack("<HHI", len(name_hash), 2, 0)
    metadata += name_hash
    metadata += struct.pack("<IQ", TEST_FILE_TYPE, TEST_UID)

    return metadata + asset_data

def make_archive() -> bytes:
    companion_payload = b"\x00" * 16
    file_payload = make_file_payload()

    return SCIMITAR_MAGIC + b"\x00" * 23 + make_container(companion_payload) + b"\x00" * 11 + make_container(file_payload)

class ParserTests(unittest.TestCase):
    def test_uncompressed_container_round_trip(self):
        payload = b"container payload"
        encoded = make_container(payload)

        offsets = list(iter_container_offsets(encoded))

        self.assertEqual(offsets, [0])

        container = parse_container(encoded, offsets[0])

        self.assertEqual(container.unpacked_size, len(payload))
        self.assertEqual(container.packed_size, len(payload))
        self.assertEqual(read_container(encoded, container), payload)

    def test_truncated_container_is_rejected(self):
        encoded = make_container(b"truncated payload")

        truncated = encoded[:-1]

        with self.assertRaises(ForgeFormatError):
            parse_container(truncated, 0)

class MetadataTests(unittest.TestCase):
    def test_valid_file_metadata(self):
        payload = make_file_payload()
        metadata = parse_file_metadata(payload)

        self.assertEqual(metadata.container_type, 2)
        self.assertEqual(metadata.file_type, TEST_FILE_TYPE)
        self.assertEqual(metadata.uid, TEST_UID)
        self.assertEqual(payload[metadata.data_offset:], TEST_ASSET_TYPE)

    def test_companion_metadata_is_rejected(self):
        companion = struct.pack("<HHI", 1, 0, 0) + b"\x00" * 20

        with self.assertRaises(InvalidFileMetadata):
            parse_file_metadata(companion)

class IndexTests(unittest.TestCase):
    def test_archive_index_excludes_companion_container(self):
        with tempfile.TemporaryDirectory() as root:
            archive = Path(root) / "fixture.forge"
            archive.write_bytes(make_archive())

            diagnostics = ScanDiagnostics()

            records = list(scan_archive(archive, diagnostics))

            self.assertEqual(diagnostics.containers, 2)
            self.assertEqual(diagnostics.assets, 1)
            self.assertEqual(diagnostics.auxiliary_containers, 1)
            self.assertEqual(diagnostics.invalid_containers, 0)
            self.assertEqual(diagnostics.metadata_errors, 0)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].uid, TEST_UID)
            self.assertEqual(records[0].file_type, TEST_FILE_TYPE)
            
            index = build_index([archive])

            self.assertEqual(len(index), 1)
            self.assertEqual(index.total_records, 1)
            self.assertEqual(index.duplicate_uids, 0)

            indexed_record = index.primary(TEST_UID)

            self.assertIsNotNone(indexed_record)
            self.assertEqual(indexed_record.file_type, TEST_FILE_TYPE)
            self.assertEqual(indexed_record.archive, archive.resolve())
            self.assertEqual(indexed_record.container_offset, records[0].container_offset)

    def test_sqlite_index_persists_unsigned_uid_and_skips_unchanged(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            archive = root / "fixture.forge"
            database = root / "assets.sqlite"

            archive.write_bytes(make_archive())

            first = index_archive(archive, database)

            self.assertFalse(first.skipped)
            self.assertEqual(first.asset_count, 1)
            self.assertEqual(first.diagnostics.containers, 2)
            self.assertEqual(first.diagnostics.assets, 1)

            connection = sqlite3.connect(database)

            try:
                asset = connection.execute(
                    """
                    SELECT uid, file_type, container_offset
                    FROM assets
                    """
                ).fetchone()

                archive_count = connection.execute(
                    "SELECT COUNT(*) FROM archives"
                ).fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(asset[0], f"{TEST_UID:016X}")
            self.assertEqual(asset[1], TEST_FILE_TYPE)
            self.assertGreater(asset[2], 0)
            self.assertEqual(archive_count, 1)

            loaded = load_asset_index(
                database,
                {
                    TEST_UID,
                    0xFFFFFFFFFFFFFFFF
                }
            )

            self.assertEqual(loaded.total_records, 1)

            loaded_record = loaded.primary(TEST_UID)

            self.assertIsNotNone(loaded_record)
            self.assertEqual(loaded_record.uid, TEST_UID)
            self.assertEqual(loaded_record.file_type, TEST_FILE_TYPE)
            self.assertEqual(loaded_record.archive, archive.resolve())
            self.assertEqual(loaded_record.container_offset, asset[2])
            self.assertEqual(loaded_record.metadata.name_hash, b"synthetic-name")

            second = index_archive(archive, database)

            self.assertTrue(second.skipped)
            self.assertEqual(second.asset_count, 1)

    def test_sqlite_index_rescans_previous_error_result(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            archive = root / "fixture.forge"
            database = root / "assets.sqlite"

            archive.write_bytes(make_archive())

            first = index_archive(archive, database)

            self.assertFalse(first.skipped)

            connection = sqlite3.connect(database)

            try:
                connection.execute(
                    """
                    UPDATE archives
                    SET metadata_errors = 1
                    WHERE path = ?
                    """,
                    (
                        str(archive.resolve()),
                    )
                )
                connection.commit()
            finally:
                connection.close()

            second = index_archive(archive, database)

            self.assertFalse(second.skipped)
            self.assertEqual(second.diagnostics.metadata_errors, 0)

            third = index_archive(archive, database)

            self.assertTrue(third.skipped)

    def test_asset_names_support_sources_confidence_and_availability(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            archive = root / "fixture.forge"
            database = root / "assets.sqlite"

            archive.write_bytes(make_archive())
            index_archive(archive, database)

            written = upsert_asset_names(
                database,
                [
                    AssetName(
                        uid=TEST_UID,
                        name="Rook Armor Pack",
                        category="model",
                        source="manual",
                        confidence=100,
                    ),
                    AssetName(
                        uid=0x2000,
                        name="Rook Character Body",
                        category="character",
                        source="community",
                        confidence=70,
                    ),
                ]
            )

            self.assertEqual(written, 2)

            matches = search_asset_names(database, "rook")

            self.assertEqual(len(matches), 2)

            confirmed = matches[0]

            self.assertEqual(confirmed.uid, TEST_UID)
            self.assertEqual(confirmed.name, "Rook Armor Pack")
            self.assertEqual(confirmed.category, "model")
            self.assertEqual(confirmed.source, "manual")
            self.assertEqual(confirmed.confidence, 100)
            self.assertEqual(confirmed.locations, 1)

            unavailable = matches[1]

            self.assertEqual(unavailable.uid, 0x2000)
            self.assertEqual(unavailable.locations, 0)

            uid_match = search_asset_names(database, f"0x{TEST_UID:016X}")

            self.assertEqual(len(uid_match), 1)
            self.assertEqual(uid_match[0].name, "Rook Armor Pack")

            connection = sqlite3.connect(database)

            try:
                schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(schema_version, 2)

    def test_archive_scan_propagates_missing_oodle_runtime(self):
        with tempfile.TemporaryDirectory() as root:
            archive = Path(root) / "fixture.forge"
            archive.write_bytes(make_archive())

            with patch("src.index._record_from_container", side_effect=OodleUnavailableError("runtime missing")):
                with self.assertRaises(OodleUnavailableError):
                    list(scan_archive(archive))

class ExtractorTests(unittest.TestCase):
    def test_raw_extraction_and_resume(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            archive = root / "fixture.forge"
            output = root / "output"

            archive.write_bytes(make_archive())

            first = extract_raw_archive(archive, output, resume=True)

            self.assertEqual(first.scanned_assets, 1)
            self.assertEqual(first.extracted, 1)
            self.assertEqual(first.resumed, 0)
            self.assertEqual(first.failed, 0)
            self.assertEqual(first.scan_errors, 0)

            extracted_files = list((output / archive.stem).glob("*bin"))

            self.assertEqual(len(extracted_files), 1)
            self.assertEqual(extracted_files[0].read_bytes(), make_file_payload())
            self.assertEqual(list(output.rglob("*.part")), [])

            manifest_path = output / "manifest.jsonl"

            manifest_entries = [
                json.loads(line)
                for line in manifest_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertEqual(len(manifest_entries), 1)
            self.assertEqual(manifest_entries[0]["status"], "extracted")

            second = extract_raw_archive(archive, output, resume=True)

            self.assertEqual(second.scanned_assets, 1)
            self.assertEqual(second.extracted, 0)
            self.assertEqual(second.resumed, 1)
            self.assertEqual(second.failed, 0)
            self.assertEqual(second.scan_errors, 0)

    def test_progress_reports_every_extracted_asset(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            archive = root / "multiple.forge"
            output = root / "output"

            archive.write_bytes(SCIMITAR_MAGIC + b"\x00" * 23 + make_container(make_file_payload(b"first")) + make_container(make_file_payload(b"second")))

            events = []

            summary = extract_raw_archive(archive, output, progress=lambda record, status: events.append(
                (
                    record.container_offset,
                    status
                )
            ))

            self.assertEqual(summary.scanned_assets, 2)
            self.assertEqual(len(events), 2)
            self.assertEqual(
                [status for _, status in events],
                [
                    "extracted",
                    "extracted"
                ]
            )

if __name__ == "__main__":
    unittest.main()