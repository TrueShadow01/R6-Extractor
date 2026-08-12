"""Tests for CLI routing and model discovery."""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from src.cli import main, bundle_files
from src.index import (
    AssetIndex,
    AssetRecord,
)
from src.metadata import FileMetadata
from src.gltf import write_gltf
from src.model import (
    MeshPart,
    resolve_dependency_uids
)
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

    def test_index_command_and_unchanged_skip(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            archive = root / "fixture.forge"
            database = root / "assets.sqlite"

            archive.write_bytes(make_archive())

            first_output = StringIO()

            with redirect_stdout(first_output):
                first_result = main(
                    [
                        "index",
                        str(archive),
                        "-o",
                        str(database)
                    ]
                )

            self.assertEqual(first_result, 0)
            self.assertIn("Status: indexed", first_output.getvalue())
            self.assertIn("Containers: 2", first_output.getvalue())
            self.assertIn("Assets: 1", first_output.getvalue())
            self.assertIn("Companion containers: 1", first_output.getvalue())
            self.assertTrue(database.is_file())

            second_output = StringIO()

            with redirect_stdout(second_output):
                second_result = main(
                    [
                        "index",
                        str(archive),
                        "-o",
                        str(database)
                    ]
                )

            self.assertEqual(second_result, 0)
            self.assertIn("Status: unchanged", second_output.getvalue())
            self.assertIn("Assets: 1", second_output.getvalue())

    def test_index_command_accepts_directory(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            database = root / "assets.sqlite"

            (root / "first.forge").write_bytes(make_archive())
            (root / "second.forge").write_bytes(make_archive())

            output = StringIO()

            with redirect_stdout(output):
                result = main(
                    [
                        "index",
                        str(root),
                        "--pattern",
                        "*.forge",
                        "-o",
                        str(database)
                    ]
                )

            text = output.getvalue()

            self.assertEqual(result, 0)
            self.assertIn("Archives: 2", text)
            self.assertIn("Indexed: 2", text)
            self.assertIn("Unchanged: 0", text)
            self.assertIn("Failed: 0", text)
            self.assertIn("Assets: 2", text)

    def test_name_import_and_search_commands(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            catalog = root / "names.csv"
            database = root / "assets.sqlite"

            catalog.write_text(
                "\n".join(
                    [
                        "UID,Name,Category,Source,Confidence",
                        (
                            "0x000000009B2CAF32,"
                            "Rook Armor Pack,"
                            "model,manual,100"
                        ),
                    ]
                ) + "\n",
                encoding="utf-8"
            )

            import_output = StringIO()

            with redirect_stdout(import_output):
                import_result = main(
                    [
                        "names",
                        "import",
                        str(catalog),
                        "--database",
                        str(database)
                    ]
                )

            self.assertEqual(import_result, 0)
            self.assertIn("Imported: 1", import_output.getvalue())

            search_output = StringIO()

            with redirect_stdout(search_output):
                search_result = main(
                    [
                        "search",
                        "rook armor",
                        "--database",
                        str(database)
                    ]
                )

            text = search_output.getvalue()

            self.assertEqual(search_result, 0)
            self.assertIn("000000009B2CAF32", text)
            self.assertIn("Rook Armor Pack", text)
            self.assertIn("confidence=100", text)
            self.assertIn("source=manual", text)
            self.assertIn("0 locations", text)
            self.assertIn("Matches: 1", text)


    def test_name_import_column_layout(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            catalog = root / "community.csv"
            database = root / "assets.sqlite"

            catalog.write_text(
                "\n".join(
                    [
                        "Aruni Default body:,Valkyrie Elite headgear:",
                        "281035272186,91997028720",
                        "309301454265,91997033050",
                    ]
                ) + "\n",
                encoding="utf-8",
            )

            import_output = StringIO()

            with redirect_stdout(import_output):
                import_result = main(
                    [
                        "names",
                        "import",
                        str(catalog),
                        "--layout",
                        "columns",
                        "--database",
                        str(database),
                        "--source",
                        "r6-uid-sheet-2022",
                        "--category",
                        "character-model",
                        "--confidence",
                        "50"
                    ]
                )

            text = import_output.getvalue()

            self.assertEqual(import_result, 0)
            self.assertIn("Layout: columns", text)
            self.assertIn("Imported: 4", text)

            search_output = StringIO()

            with redirect_stdout(search_output):
                search_result = main(
                    [
                        "search",
                        "Aruni Default body",
                        "--database",
                        str(database)
                    ]
                )

            text = search_output.getvalue()

            self.assertEqual(search_result, 0)
            self.assertIn("Aruni Default body", text)
            self.assertIn("[character-model]", text)
            self.assertIn("confidence=50", text)
            self.assertIn("source=r6-uid-sheet-2022", text)
            self.assertIn("Matches: 2", text)

    def test_cross_bundle_depgraph_with_archive_only(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)

            archive = root / "datapc64_merged_bnk_mesh.forge"
            sibling = root / "datapc64_merged_bnk_textures.forge"
            depgraph = root / "datapc64_ondemand.depgraphbin"

            archive.write_bytes(b"mesh")
            sibling.write_bytes(b"texture")
            depgraph.write_bytes(b"dependencies")

            archives, selected_depgraph = bundle_files(archive, depgraph_path=depgraph, archive_only=True)

            self.assertEqual(archives, [archive.resolve()])
            self.assertNotIn(sibling.resolve(), archives)
            self.assertEqual(selected_depgraph, depgraph.resolve())

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

    def test_dependency_uids_include_indirect_children_and_cycles(self):
        children = {
            0x1000: [0x2000, 0x3000],
            0x2000: [0x4000],
            0x4000: [0x1000]
        }

        resolved = resolve_dependency_uids(0x1000, children)

        self.assertEqual(resolved,
            (
                0x1000,
                0x2000,
                0x3000,
                0x4000
            )
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

class GltfExportTests(unittest.TestCase):
    def test_gltf_structure_axis_and_uv_conversion(self):
        part = MeshPart(
            uid=0x2000,
            vertices=[
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (0.0, 1.0, 0.0)
            ],
            uvs=[
                (0.25, 0.75),
                (0.50, 0.25),
                (0.00, 1.00)
            ],
            normals=[
                (0.0, 0.0, 1.0),
                (0.0, 0.0, 1.0),
                (0.0, 0.0, 1.0)
            ],
            faces=[
                (0, 1, 2)
            ]
        )

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)

            gltf_path = write_gltf(
                TEST_UID,
                [part],
                root,
                diffuse="diffuse.png",
                normal="normal.png",
                specular="specular.png"
            )

            document = json.loads(gltf_path.read_text(encoding="utf-8"))

            binary_path = root / document["buffers"][0]["uri"]
            binary = binary_path.read_bytes()

            self.assertEqual(len(document["nodes"]), 1)
            self.assertEqual(len(document["meshes"]), 1)
            self.assertEqual(len(document["accessors"]), 4)
            self.assertEqual(len(document["bufferViews"]), 4)

            self.assertEqual(document["buffers"][0]["byteLength"], len(binary))

            primitive = document["meshes"][0]["primitives"][0]

            accessor_references = [
                *primitive["attributes"].values(),
                primitive["indices"]
            ]

            for accessor_index in accessor_references:
                self.assertLess(accessor_index, len(document["accessors"]))

            position_accessor = document["accessors"][primitive["attributes"]["POSITION"]]
            position_view = document["bufferViews"][position_accessor["bufferView"]]
            position_offset = position_view.get("byteOffset", 0) + position_accessor.get("byteOffset", 0)

            exported_positions = struct.unpack_from("<9f", binary, position_offset)

            self.assertEqual(
                exported_positions, (
                    0.0, 0.0, 0.0,
                    1.0, 0.0, 0.0,
                    0.0, 0.0, -1.0
                )
            )

            normal_accessor = document["accessors"][primitive["attributes"]["NORMAL"]]
            normal_view = document["bufferViews"][normal_accessor["bufferView"]]
            normal_offset = normal_view.get("byteOffset", 0) + normal_accessor.get("byteOffset", 0)

            exported_normals = struct.unpack_from("<9f", binary, normal_offset)

            self.assertEqual(
                exported_normals,
                (
                    0.0, 1.0, 0.0,
                    0.0, 1.0, 0.0,
                    0.0, 1.0, 0.0
                )
            )

            uv_accessor = document["accessors"][primitive["attributes"]["TEXCOORD_0"]]
            uv_view = document["bufferViews"][uv_accessor["bufferView"]]

            uv_offset = (
                uv_view.get("byteOffset", 0)
                + uv_accessor.get("byteOffset", 0)
            )

            exported_uvs = struct.unpack_from("<6f", binary, uv_offset)

            self.assertEqual(
                exported_uvs,
                (
                    0.25, 0.25,
                    0.50, 0.75,
                    0.00, 0.00
                )
            )

            self.assertEqual([image["uri"] for image in document["images"]], [
                "diffuse.png",
                "normal.png"
            ])

            material = document["materials"][0]

            self.assertIn("pbrMetallicRoughness", material)
            self.assertEqual(material["extras"]["siegeSpecularTexture"], "specular.png")

if __name__ == "__main__":
    unittest.main()