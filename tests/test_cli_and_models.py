"""Tests for CLI routing and model discovery."""

from __future__ import annotations

import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from src.cli import main
from src.index import (
    AssetIndex,
    AssetRecord,
)
from src.metadata import FileMetadata
from src.model_catalog import (
    COMPILED_MESH_OBJECT,
    discover_models,
)
from src.parser import (
    CONTAINER_MAGIC,
    SCIMITAR_MAGIC,
)


TEST_UID = 0x123456789ABCDEF0
TEST_FILE_TYPE = COMPILED_MESH_OBJECT


def make_container(payload: bytes) -> bytes:
    header = CONTAINER_MAGIC

    header += struct.pack("<HHBHI", 3, 0x000F, 0, 0x8004, 1)
    header += struct.pack("<II",  len(payload), len(payload))
    header += struct.pack("<I", 0x12345678,)

    return header + payload

def make_file_payload() -> bytes:
    name_hash = b"cli-test"

    return (struct.pack("<HHI", len(name_hash), 2, 0) + name_hash + struct.pack("<IQ", TEST_FILE_TYPE, TEST_UID) + b"synthetic asset")

def make_archive() -> bytes:
    return (SCIMITAR_MAGIC + b"\x00" * 16 + make_container(b"\x00" * 16) + b"\x00" * 8 + make_container(make_file_payload()))

class CliTests(unittest.TestCase):
    def test_scan_command(self):
        with tempfile.TemporaryDirectory() as root:
            archive = Path(root) / "fixture.forge"
            archive.write_bytes(make_archive())

            output = StringIO()

            with redirect_stdout(output):
                result = main(
                    [
                        "scan",
                        str(archive),
                    ]
                )

            text = output.getvalue()

            self.assertEqual(result, 0)
            self.assertIn("Archives: 1", text)
            self.assertIn("Containers: 2", text)
            self.assertIn("Assets: 1", text)
            self.assertIn("Companion containers: 1", text)
            self.assertIn("Errors: 0", text)
            self.assertIn("0xABEB2DFB: 1", text)

    def test_extract_command_and_resume(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            archive = root / "fixture.forge"
            destination = root / "output"

            archive.write_bytes(make_archive())

            first_output = StringIO()

            with redirect_stdout(first_output):
                first_result = main(
                    [
                        "extract",
                        str(archive),
                        "-o",
                        str(destination),
                    ]
                )

            self.assertEqual(first_result, 0)
            self.assertIn("Extracted: 1", first_output.getvalue())
            self.assertIn("Failed: 0",first_output.getvalue())

            second_output = StringIO()

            with redirect_stdout(second_output):
                second_result = main(
                    [
                        "extract",
                        str(archive),
                        "-o",
                        str(destination),
                    ]
                )

            self.assertEqual(second_result, 0)
            self.assertIn("Extracted: 0", second_output.getvalue())
            self.assertIn("Resumed: 1", second_output.getvalue())


class ModelDiscoveryTests(unittest.TestCase):
    def make_record(self, uid: int, file_type: int, size: int) -> AssetRecord:
        metadata = FileMetadata(
            name_length=0,
            container_type=2,
            flags=0,
            name_hash=b"",
            file_type=file_type,
            uid=uid,
            data_offset=20
        )

        return AssetRecord(
            uid=uid,
            file_type=file_type,
            archive=Path(f"geometry_{uid:016X}.forge"),
            container_offset=uid & 0xFFFF,
            container_size=size,
            unpacked_size=size,
            metadata=metadata,
        )

    def test_composite_model_discovery(self):
        model_uid = 0x1000
        geometry_a = 0x2000
        geometry_b = 0x3000
        unrelated = 0x4000

        index = AssetIndex()

        index.add(self.make_record(geometry_a, COMPILED_MESH_OBJECT, 100))
        index.add(self.make_record(geometry_b, COMPILED_MESH_OBJECT, 200))
        index.add( self.make_record(unrelated, 0xDEADBEEF, 300))

        children = {
            model_uid: [
                geometry_a,
                geometry_b,
                unrelated,
                geometry_a,
            ]
        }

        models = discover_models(children, index)

        self.assertEqual(len(models), 1)

        model = models[0]

        self.assertEqual(model.uid, model_uid)
        self.assertEqual(model.part_count, 2)
        self.assertEqual(model.geometry_uids,
            (
                geometry_a,
                geometry_b,
            )
        )
        self.assertEqual(model.geometry_bytes, 300)

if __name__ == "__main__":
    unittest.main()