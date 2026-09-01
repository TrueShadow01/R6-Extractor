"""Tests for CLI routing and model discovery."""

from __future__ import annotations
from PIL import Image

import json
import struct
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from src.database import AssetName
from src.cli import main
from src.index import (
    AssetIndex,
    AssetRecord,
)
from src.metadata import FileMetadata
from src.gltf import (
    MaterialTextures,
    invert_gltf_matrix,
    siege_to_gltf_matrix,
    write_gltf
)
from src.model import (
    BoneTransform,
    MeshPart,
    MeshBinding,
    complete_mesh_binding,
    read_mesh_bindings,
    resolve_dependency_uids,
    resolve_export_material_textures,
    resolve_static_face_bindings
)
from src.model_catalog import (
    COMPILED_MESH_OBJECT,
    discover_models,
    discover_default_operator_candidates,
    discover_unknown_operator_candidates,
    resolve_bundle_paths
)
from src.parser import (
    CONTAINER_MAGIC,
    SCIMITAR_MAGIC,
)
from src.mesh import MeshIsland
from src.material import (
    CURRENT_MESH,
    MaterialTextureSet,
    MaterialTextureSelector,
    ShaderUniform
)
from src.depgraph import load_depgraph

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

            uid_output = StringIO()

            with redirect_stdout(uid_output):
                uid_result = main(
                    [
                        "search",
                        f"{TEST_UID:016X}",
                        "--database",
                        str(database)
                    ]
                )

            uid_text = uid_output.getvalue()

            self.assertEqual(uid_result, 0)
            self.assertIn(f"{TEST_UID:016X} Unknown", uid_text)
            self.assertIn("[unknown]", uid_text)
            self.assertIn("source=unresolved", uid_text)
            self.assertIn("1 location", uid_text)
            self.assertIn("Matches: 1", uid_text)
            self.assertIn("Asset: CompiledMeshObject (0xABEB2DFB)", uid_text)
            self.assertIn(f"Archive: {archive.resolve()}", uid_text)
            self.assertIn("Container: 0x", uid_text)
            self.assertIn("bytes=", uid_text)

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

    def test_bundle_paths_support_depgraph_name_and_archive_only(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)

            archive = root / "datapc64_merged_bnk_mesh.forge"
            sibling = root / "datapc64_merged_bnk_textures.forge"
            depgraph = root / "datapc64_ondemand.depgraphbin"

            archive.write_bytes(b"mesh")
            sibling.write_bytes(b"texture")
            depgraph.write_bytes(b"dependencies")

            (
                prefix,
                selected_depgraph,
                archives
            ) = resolve_bundle_paths(archive, depgraph_path=depgraph.name, archive_only=True)

            self.assertEqual(prefix, "datapc64_merged")
            self.assertEqual(
                archives,
                (
                    archive.resolve(),
                )
            )
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

    def test_reads_mesh_bindings(self):
        geometry_uid = 0x123456789ABCDEF0
        binding_uid = 0x1111222233334444

        first_matrix = (
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            1.0, 2.0, 3.0, 1.0,
        )
        second_matrix = (
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            4.0, 5.0, 6.0, 1.0,
        )

        def bone_record(bone_id, matrix):
            return struct.pack("<II16fQ", 0x7B33D284, bone_id, *matrix, 0)

        def pose_record(index, bone_id, translation, rotation):
            prefix = b"\x00" + struct.pack("<H", index) + b"\xF8\xFB\x00\x00\x00\x00"
            return prefix + struct.pack("<II3f4f", 0x18A85CDA, bone_id, *translation, *rotation)

        pose_blob = struct.pack("<III", 0xC7197C69, 0xFFB49035, 2) + pose_record(1, 0x29A684AC, (1.0, 2.0, 3.0), (0.0, 0.0, 0.0, 1.0)) + pose_record(2, 0x4ED9C94E, (4.0, 5.0, 6.0), (0.0, 0.5, 0.0, 0.5))
        binding_blob = struct.pack("<IIIII", CURRENT_MESH, 1, 2, 0, 0) + bone_record(0xAAAABBBB, first_matrix) + bone_record(0xCCCCDDDD, second_matrix) + b"\x00" + struct.pack("<Q", geometry_uid) + pose_blob
        payload = struct.pack("<HHI", 0, 2, 0) + struct.pack("<IQ", CURRENT_MESH, binding_uid) + binding_blob

        bindings = read_mesh_bindings(payload)

        self.assertEqual(tuple(bindings), (geometry_uid,))

        binding = bindings[geometry_uid]

        self.assertEqual(
            binding.bone_ids,
            (
                0xAAAABBBB,
                0xCCCCDDDD
            )
        )
        self.assertEqual(
            binding.inverse_bind_matrices,
            (
                first_matrix,
                second_matrix
            )
        )

        self.assertEqual(
            tuple(
                transform.bone_id
                for transform in binding.pose_transforms
            ),
            (
                0x29A684AC,
                0x4ED9C94E
            )
        )
        self.assertEqual(binding.pose_transforms[0].translation, (1.0, 2.0, 3.0))
        self.assertEqual(binding.pose_transforms[1].rotation, (0.0, 0.5, 0.0, 0.5))

    def test_completes_palette_declared_implicit_joints(self):
        identity = (
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        )

        binding = MeshBinding(
            geometry_uid=0x2000,
            bone_ids=(0x11111111,),
            inverse_bind_matrices=(identity,),
            joint_node_matrices=(identity,)
        )

        completed = complete_mesh_binding(
            binding,
            3,
            {
                0,
                1,
                2
            }
        )

        self.assertEqual(
            completed.bone_ids,
            (
                0x11111111,
                0xFFFFFFFF,
                0xFFFFFFFF
            )
        )
        self.assertEqual(
            completed.inverse_bind_matrices,
            (
                identity,
                identity,
                identity
            )
        )
        self.assertEqual(
            completed.joint_node_matrices,
            (
                identity,
                identity,
                identity
            )
        )

        with self.assertRaisesRegex(ValueError, "uses joint 2"):
            complete_mesh_binding(
                binding,
                3,
                {
                    0,
                    1
                }
            )

    def test_resolves_static_shared_face_pose(self):
        identity = (
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0,
        )

        def translated_inverse_bind(x, y, z):
            return (*identity[:12], x, y, z, 1.0,)

        face_bone_ids = (
            0x22FE4DA9,
            0xD8F170CA,
            0x29A684AC,
            0x4ED9C94E,
        )

        host = MeshBinding(
            geometry_uid=0x3000,
            bone_ids=(
                0x88575789,
                0x72586AEA,
                0x07C159A2,
            ),
            inverse_bind_matrices=(
                translated_inverse_bind(1.0, 0.0, 0.0),
                translated_inverse_bind(-1.0, 0.0, 0.0),
                identity,
            ),
            pose_transforms=tuple(
                BoneTransform(
                    bone_id=bone_id,
                    translation=(0.0, 0.0, 0.0),
                    rotation=(0.0, 0.0, 0.0, 1.0),
                )
                for bone_id in face_bone_ids
            ),
        )

        shared = MeshBinding(
            geometry_uid=0x2000,
            bone_ids=face_bone_ids,
            inverse_bind_matrices=(
                identity,
                identity,
                translated_inverse_bind(0.0, 2.0, 0.0),
                translated_inverse_bind(0.0, 1.0, 0.0),
            ),
        )

        resolved = resolve_static_face_bindings(
            {
                shared.geometry_uid: shared,
                host.geometry_uid: host,
            }
        )

        corrected = resolved[shared.geometry_uid]

        self.assertIs(resolved[host.geometry_uid], host)
        self.assertEqual(corrected.inverse_bind_matrices, shared.inverse_bind_matrices,)
        self.assertEqual(
            tuple(
                matrix[12:15]
                for matrix in corrected.joint_node_matrices
            ),
            (
                (-1.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (0.0, 0.0, 1.0),
                (0.0, 0.0, 0.0),
            ),
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
        index.add(self.make_record(unrelated, 0xDEADBEEF, 300))

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

        self.assertEqual(model.to_dict()["part_count"], 2)
        self.assertEqual(model.uid, model_uid)
        self.assertEqual(model.part_count, 2)
        self.assertEqual(model.geometry_uids,
            (
                geometry_a,
                geometry_b,
            )
        )
        self.assertEqual(model.geometry_bytes, 300)

    def test_material_texture_sets_choose_largest_decoded_tier(self):
        texture_sets = (
            MaterialTextureSet(
                diffuse_uids=(0x10, 0x11),
                normal_uids=(0x20,),
                specular_uids=(0x30,),
                mask_uids=(0x40,),
                solid_color=(0.25, 0.5, 0.75, 1.0),
                selectors=(
                    MaterialTextureSelector(
                        role=1,
                        spec_uid=0x50,
                        texture_map_uid=0x51,
                        texture_uids=(0x52,),
                        source="detail"
                    ),
                    MaterialTextureSelector(
                        role=3,
                        spec_uid=0x60,
                        texture_map_uid=0x61,
                        texture_uids=(0x62,),
                        source="shader",
                        shader_binding="NormalDetail"
                    )
                ),
                shader_uid=0x7000,
                shader_uniforms=(
                    ShaderUniform(
                        owner_uid=0x7002,
                        index=0,
                        name="IrisGlossiness",
                        uniform_type=1,
                        values=(0.75,)
                    ),
                ),
            ),
        )

        decoded = [
            (256, 0, "0000000000000010.png"),
            (4096, 0, "0000000000000011.png"),
            (1024, 1, "0000000000000020.png"),
            (1024, 2, "0000000000000030.png"),
            (1024, 7, "0000000000000040.png"),
            (8192, 1, "0000000000000052.png"),
            (4096, 3, "0000000000000062.png")
        ]

        resolved = resolve_export_material_textures(texture_sets, decoded)

        self.assertEqual(
            resolved,
            (
                MaterialTextures(
                    diffuse="0000000000000011.png",
                    normal="0000000000000020.png",
                    specular="0000000000000030.png",
                    mask="0000000000000040.png",
                    solid_color=(0.25, 0.5, 0.75, 1.0),
                    detail_normals=("0000000000000052.png",),
                    shader_textures=(
                        (
                            "NormalDetail",
                            "0000000000000062.png"
                        ),
                    ),
                    shader_uid=0x7000,
                    shader_uniforms=(
                        (
                            "IrisGlossiness",
                            (0.75,)
                        ),
                    ),
                ),
            )
        )

    def test_depgraph_groups_children_by_parent(self):
        relationships = b"".join(
            [
                struct.pack("<QQQ", 0x1000, 0x2000, 0),
                struct.pack("<QQQ", 0x1000, 0x3000, 0),
                struct.pack("<QQQ", 0x4000, 0x5000, 0)
            ]
        )

        with tempfile.TemporaryDirectory() as root:
            depgraph = Path(root) / "fixture.depgraphbin"

            depgraph.write_bytes(make_container(b"\x00" + relationships))

            children = load_depgraph(depgraph)

        self.assertEqual(
            children,
            {
                0x1000: [
                    0x2000,
                    0x3000
                ],
                0x4000: [
                    0x5000
                ]
            }
        )

    def test_default_operator_candidates_use_parent_labels_and_normalize_categories(self):
        default_head_parent = 0x1000
        cosmetic_parent = 0x2000
        default_mesh = 0x3000
        missing_parent = 0x4000
        metadata_body_parent = 0x5000

        depgraph_path = Path("default.depgraphbin")

        names = (
            AssetName(
                uid=default_head_parent,
                name="Example default headgear model",
                category="operator-body",
                source="derived-community",
                confidence=60,
                locations=1
            ),
            AssetName(
                uid=cosmetic_parent,
                name="Example event body model",
                category="operator-body",
                source="derived-community",
                confidence=60,
                locations=1
            ),
            AssetName(
                uid=default_mesh,
                name="Example default body",
                category="operator-body",
                source="rainbowforge-community-2022",
                confidence=70,
                locations=1
            ),
            AssetName(
                uid=missing_parent,
                name="Missing default headgear model",
                category="operator-headgear",
                source="derived-community",
                confidence=60,
                locations=1
            ),
            AssetName(
                uid=metadata_body_parent,
                name="Example Default body",
                category="operator-metadata",
                source="r6-uid-sheet-2022",
                confidence=40,
                locations=0
            )
        )

        candidates = discover_default_operator_candidates(
            {
                depgraph_path: {
                    default_head_parent: [default_mesh],
                    cosmetic_parent: [default_mesh],
                    default_mesh: [0x6000],
                    metadata_body_parent: [default_mesh]
                }
            },
            names
        )

        self.assertEqual(
            tuple(candidate.uid for candidate in candidates),
            (
                default_head_parent,
                metadata_body_parent
            )
        )

        head_candidate, body_candidate = candidates

        self.assertEqual(head_candidate.category, "operator-headgear")
        self.assertEqual(head_candidate.evidence, (names[0],))
        self.assertEqual(head_candidate.depgraphs, (depgraph_path,))
        self.assertEqual(body_candidate.category, "operator-body")
        self.assertEqual(body_candidate.evidence, (names[4],))

    def test_verified_defaults_replace_matching_candidate_groups(self):
        verified_head = 0x1000
        verified_body = 0x1001
        stale_aruni_body = 0x2000
        stale_aruni_head = 0x2001
        unresolved_caveira_body = 0x3000
        depgraph_path = Path("defaults.depgraphbin")

        names = (
            AssetName(
                uid=verified_head,
                name="Aruni Default Head",
                category="operator-headgear",
                source="manual-verified",
                confidence=100,
                locations=1
            ),
            AssetName(
                uid=verified_body,
                name="Aruni Default Body",
                category="operator-body",
                source="manual-verified",
                confidence=100,
                locations=1
            ),
            AssetName(
                uid=stale_aruni_head,
                name="Aruni Default headgear model",
                category="operator-headgear",
                source="derived-community",
                confidence=60,
                locations=1
            ),
            AssetName(
                uid=stale_aruni_body,
                name="Aruni Default body",
                category="operator-metadata",
                source="r6-uid-sheet-2022",
                confidence=40,
                locations=1
            ),
            AssetName(
                uid=unresolved_caveira_body,
                name="Caveira Default body",
                category="operator-metadata",
                source="r6-uid-sheet-2022",
                confidence=40,
                locations=1
            )
        )

        candidates = discover_default_operator_candidates(
            {
                depgraph_path: {
                    verified_head: [0x9000],
                    verified_body: [0x9001],
                    stale_aruni_head: [0x9004],
                    stale_aruni_body: [0x9002],
                    unresolved_caveira_body: [0x9003]
                }
            },
            names
        )

        self.assertEqual(
            tuple(candidate.uid for candidate in candidates),
            (
                verified_head,
                verified_body,
                unresolved_caveira_body
            )
        )

    def test_manual_rejection_excludes_derived_default_candidate(self):
        rejected_parent = 0x1000
        depgraph_path = Path("defaults.depgraphbin")

        candidates = discover_default_operator_candidates(
            {
                depgraph_path: {
                    rejected_parent: [0x2000]
                }
            },
            (
                AssetName(
                    uid=rejected_parent,
                    name="Rook headgear default (maybe) model",
                    category="operator-headgear",
                    source="derived-community",
                    confidence=40,
                    locations=1
                ),
                AssetName(
                    uid=rejected_parent,
                    name="Rook Skull Headgear Variant",
                    category="operator-headgear",
                    source="manual-rejected-default",
                    confidence=100,
                    locations=1
                )
            )
        )

        self.assertEqual(candidates, ())

    def test_unknown_operator_candidates_use_named_mesh_children(self):
        operator_mesh = 0x2000
        gadget_mesh = 0x3000
        named_parent = 0x4000
        unknown_parent = 0x5000

        names = (
            AssetName(
                uid=operator_mesh,
                name="Example operator body",
                category="operator-body",
                source="community",
                confidence=70,
                locations=1
            ),
            AssetName(
                uid=gadget_mesh,
                name="Example gadget",
                category="gadget",
                source="community",
                confidence=70
            ),
            AssetName(
                uid=named_parent,
                name="Already named model",
                category="operator-body",
                source="manual",
                confidence=100
            )
        )

        candidates = discover_unknown_operator_candidates(
            {
                Path("example.depgraphbin"): {
                    unknown_parent: [
                        operator_mesh,
                        gadget_mesh
                    ],
                    named_parent: [
                        operator_mesh
                    ],
                    0x6000: [
                        gadget_mesh
                    ]
                }
            },
            names,
            max_parent_references=2
        )

        self.assertEqual(len(candidates), 1)

        candidate = candidates[0]

        self.assertEqual(candidate.uid, unknown_parent)
        self.assertEqual(candidate.category, "operator-body")
        self.assertEqual(
            tuple(entry.uid for entry in candidate.evidence),
            (
                operator_mesh,
            )
        )

class GltfExportTests(unittest.TestCase):
    def test_gltf_structure_axis_and_uv_conversion(self):
        siege_inverse_bind = (
            1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            1.0, 2.0, 3.0, 1.0,
        )

        gltf_inverse_bind = siege_to_gltf_matrix(siege_inverse_bind)

        self.assertEqual(
            gltf_inverse_bind,
            (
                1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                1.0, 3.0, -2.0, 1.0,
            )
        )

        self.assertEqual(
            invert_gltf_matrix(gltf_inverse_bind),
            (
                1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                -1.0, -3.0, 2.0, 1.0,
            )
        )

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
            islands=(
                MeshIsland(
                    material_id=0,
                    faces=(
                        (0, 1, 2),
                    )
                ),
            ),
            joints=(
                (0, 1, 0, 0),
                (1, 0, 0, 0),
                (0, 1, 0, 0),
            ),
            weights=(
                (1.0, 0.0, 0.0, 0.0),
                (0.25, 0.75, 0.0, 0.0),
                (0.0, 1.0, 0.0, 0.0),
            ),
            binding=MeshBinding(
                geometry_uid=0x2000,
                bone_ids=(
                    0xAAAABBBB,
                    0xCCCCDDDD,
                ),
                inverse_bind_matrices=(
                    (
                        1.0, 0.0, 0.0, 0.0,
                        0.0, 1.0, 0.0, 0.0,
                        0.0, 0.0, 1.0, 0.0,
                        0.0, 0.0, 0.0, 1.0,
                    ),
                    siege_inverse_bind
                )
            )
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

            self.assertEqual(len(document["nodes"]), 3)
            self.assertEqual(len(document["skins"]), 1)
            self.assertEqual(len(document["meshes"]), 1)
            self.assertEqual(len(document["accessors"]), 7)
            self.assertEqual(len(document["bufferViews"]), 7)

            self.assertEqual(document["buffers"][0]["byteLength"], len(binary))

            primitive = document["meshes"][0]["primitives"][0]

            mesh_node = next(
                node
                for node in document["nodes"]
                if "mesh" in node
            )

            skin = document["skins"][mesh_node["skin"]]

            self.assertEqual(len(skin["joints"]), 2)

            first_joint = document["nodes"][skin["joints"][0]]
            second_joint = document["nodes"][skin["joints"][1]]

            self.assertEqual(
                first_joint["matrix"],
                [
                    1.0, 0.0, 0.0, 0.0,
                    0.0, 1.0, 0.0, 0.0,
                    0.0, 0.0, 1.0, 0.0,
                    0.0, 0.0, 0.0, 1.0,
                ]
            )

            self.assertEqual(second_joint["matrix"], list(invert_gltf_matrix(gltf_inverse_bind)))

            inverse_bind_accessor = document["accessors"][skin["inverseBindMatrices"]]
            inverse_bind_view = document["bufferViews"][inverse_bind_accessor["bufferView"]]

            self.assertNotIn("target", inverse_bind_view)
            self.assertEqual(inverse_bind_accessor["type"], "MAT4")
            self.assertEqual(inverse_bind_accessor["count"], 2)

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

            joint_accessor = document["accessors"][primitive["attributes"]["JOINTS_0"]]
            joint_view = document["bufferViews"][joint_accessor["bufferView"]]
            joint_offset = joint_view.get("byteOffset", 0) + joint_accessor.get("byteOffset", 0)

            exported_joints = struct.unpack_from("<12B", binary, joint_offset)

            self.assertEqual(
                exported_joints,
                (
                    0, 1, 0, 0,
                    1, 0, 0, 0,
                    0, 1, 0, 0,
                )
            )

            self.assertEqual(joint_accessor["componentType"], 5121)

            weight_accessor = document["accessors"][primitive["attributes"]["WEIGHTS_0"]]
            weight_view = document["bufferViews"][weight_accessor["bufferView"]]
            weight_offset = weight_view.get("byteOffset", 0) + weight_accessor.get("byteOffset", 0)

            exported_weights = struct.unpack_from("<12f", binary, weight_offset)

            self.assertEqual(
                exported_weights,
                (
                    1.0, 0.0, 0.0, 0.0,
                    0.25, 0.75, 0.0, 0.0,
                    0.0, 1.0, 0.0, 0.0,
                )
            )

            self.assertEqual([image["uri"] for image in document["images"]], [
                "diffuse.png",
                "normal.png"
            ])

            material = document["materials"][0]

            self.assertIn("pbrMetallicRoughness", material)

            self.assertEqual(material["extras"]["siegePackedMaterialTexture"], "specular.png")
            self.assertNotIn("extensions", material)
            self.assertNotIn("extensionsUsed", document)

    def test_gltf_exports_material_islands_as_primitives(self):
        part = MeshPart(
            uid=0x2000,
            vertices=[
                (0.0, 0.0, 0.0),
                (1.0, 0.0, 0.0),
                (1.0, 1.0, 0.0),
                (0.0, 1.0, 0.0)
            ],
            uvs=[
                (0.0, 0.0),
                (1.0, 0.0),
                (1.0, 1.0),
                (0.0, 1.0)
            ],
            normals=[
                (0.0, 0.0, 1.0),
                (0.0, 0.0, 1.0),
                (0.0, 0.0, 1.0),
                (0.0, 0.0, 1.0)
            ],
            islands=(
                MeshIsland(
                    material_id=0,
                    faces=((0, 1, 2),)
                ),
                MeshIsland(
                    material_id=3,
                    faces=((0, 2, 3),)
                )
            )
        )

        with tempfile.TemporaryDirectory() as root:
            gltf_path = write_gltf(TEST_UID, [part], root)
            document = json.loads(gltf_path.read_text(encoding="utf-8"))

            primitives = document["meshes"][0]["primitives"]

            self.assertEqual(len(primitives), 2)
            self.assertEqual([primitive["material"] for primitive in primitives], [0, 3])
            self.assertEqual(len(document["materials"]), 4)

            for filename in ("slot0_diffuse.png", "slot1_diffuse.png", "slot3_diffuse.png"):
                Image.new("RGBA", (1, 1), (255, 255, 255, 0)).save(Path(root) / filename)

            textured_path = write_gltf(
                TEST_UID,
                [part],
                root,
                material_textures=(
                    MaterialTextures(
                        diffuse="slot0_diffuse.png",
                        normal="shared_normal.png",
                        shader_uid=0x0841DC11F9
                    ),
                    MaterialTextures(
                        diffuse="slot1_diffuse.png",
                        shader_uid=0x3051C028
                    ),
                    MaterialTextures(
                        solid_color=(0.73, 0.73, 0.73, 1.0),
                        shader_uid=0x99E2C950
                    ),
                    MaterialTextures(
                        diffuse="slot3_diffuse.png",
                        normal="shared_normal.png",
                        specular="slot3_specular.png",
                        mask="slot3_mask.png",
                        detail_normals=("slot3_detail_normal.png",),
                        shader_textures=(
                            (
                                "NormalDetail",
                                "slot3_shader.png"
                            ),
                        ),
                        shader_uid=0x557005948D,
                        shader_uniforms=(
                            (
                                "IrisGlossiness",
                                (0.75,)
                            ),
                        ),
                    )
                )
            )

            textured_document = json.loads(textured_path.read_text(encoding="utf-8"))

            slot0 = textured_document["materials"][0]
            slot1 = textured_document["materials"][1]
            slot2 = textured_document["materials"][2]
            slot3 = textured_document["materials"][3]

            self.assertEqual(slot0["alphaMode"], "OPAQUE")
            self.assertEqual(slot1["alphaMode"], "MASK")
            self.assertEqual(slot2["alphaMode"], "BLEND")
            self.assertTrue(slot2["doubleSided"])
            self.assertEqual(slot3["alphaMode"], "OPAQUE")

            base_color = slot2["pbrMetallicRoughness"]["baseColorFactor"]

            for actual in base_color[:3]:
                self.assertAlmostEqual(actual, 0.491905, places=6)

            self.assertEqual(base_color[3], 0.1)

            slot0_diffuse_texture = slot0["pbrMetallicRoughness"]["baseColorTexture"]["index"]
            slot3_diffuse_texture = slot3["pbrMetallicRoughness"]["baseColorTexture"]["index"]

            slot0_diffuse_image = textured_document["textures"][slot0_diffuse_texture]["source"]
            slot3_diffuse_image = textured_document["textures"][slot3_diffuse_texture]["source"]

            self.assertEqual(textured_document["images"][slot0_diffuse_image]["uri"], "slot0_diffuse.png")
            self.assertEqual(textured_document["images"][slot3_diffuse_image]["uri"], "slot3_diffuse.png")
            self.assertEqual(slot0["normalTexture"]["index"], slot3["normalTexture"]["index"])

            self.assertEqual(slot3["extras"]["siegePackedMaterialTexture"], "slot3_specular.png")
            self.assertEqual(slot3["extras"]["siegeMaskTexture"], "slot3_mask.png")
            self.assertEqual(slot3["extras"]["siegeDetailNormalTextures"], ["slot3_detail_normal.png"])
            self.assertEqual(slot3["extras"]["siegeShaderTextures"], {
                "NormalDetail": "slot3_shader.png"
            })
            self.assertEqual(slot3["extras"]["siegeShaderUid"], "000000557005948D")
            self.assertEqual(slot3["extras"]["siegeShaderUniforms"], {
                "IrisGlossiness": [0.75]
            })

            for filename in ("warden_transition.png", "warden_lens.png"):
                Image.new("RGBA", (1, 1), (255, 255, 255, 128)).save(
                    Path(root) / filename
                )

            inactive_path = write_gltf(
                TEST_UID,
                [part],
                root,
                material_textures=(
                    MaterialTextures(
                        diffuse="warden_transition.png",
                        shader_uid=0x0F2BB85C7E
                    ),
                    MaterialTextures(
                        diffuse="warden_lens.png",
                        shader_uid=0x1397A32F38
                    ),
                    MaterialTextures(
                        diffuse="warden_lens.png",
                        shader_uid=0x1397A32F38
                    ),
                )
            )

            inactive_document = json.loads(inactive_path.read_text(encoding="utf-8"))
            transition = inactive_document["materials"][0]
            lens = inactive_document["materials"][1]
            unrelated = inactive_document["materials"][2]

            self.assertEqual(transition["alphaMode"], "BLEND")
            self.assertTrue(transition["doubleSided"])
            self.assertNotIn("alphaCutoff", transition)
            self.assertEqual(transition["pbrMetallicRoughness"]["baseColorFactor"][3], 0.0)
            self.assertIn("baseColorTexture", transition["pbrMetallicRoughness"])

            self.assertEqual(lens["alphaMode"], "BLEND")
            self.assertTrue(lens["doubleSided"])
            self.assertNotIn("alphaCutoff", lens)
            self.assertEqual(lens["pbrMetallicRoughness"]["baseColorFactor"][3], 1.0)
            self.assertIn("baseColorTexture", lens["pbrMetallicRoughness"])
            self.assertEqual(unrelated["alphaMode"], "OPAQUE")
            self.assertEqual(
                [
                    document["accessors"][primitive["indices"]]["count"]
                    for primitive in primitives
                ], [3, 3])

if __name__ == "__main__":
    unittest.main()