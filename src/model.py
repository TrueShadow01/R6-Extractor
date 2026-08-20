"""Composite model resolution and export"""

from __future__ import annotations

import struct
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Mapping

from PIL import Image

from src.gltf import (
    MaterialTextures,
    invert_gltf_matrix,
    multiply_matrices,
    siege_to_gltf_matrix,
    transpose_matrix,
    write_gltf
)
from src.index import (
    AssetIndex,
    AssetRecord
)
from src.material import (
    MaterialTextureSet,
    NORMAL_ROLE,
    CURRENT_MESH,
    embedded_texture_uids,
    resolve_material_texture_sets,
    scan_nested_entries
)
from src.mesh import read_mesh_with_islands, MeshIsland
from src.parser import (
    map_archive,
    read_container
)
from src.texture import save_png

COMPILED_MESH_OBJ = 0xABEB2DFB

TEXTURE_TYPES = {
    0x13237FE9, # CompiledTextureMap
    0x9F492D22, # UltraResTexMap
    0x3876CCDF, # FutureResTexMap
    0x59CE4D13, # HiResTexMap
    0xF9C80707, # MedResTexMap
    0xD7B5C478, # LowResTexMap
}

BONE_RECORD_TAG = 0xB883D0BA
BONE_RECORD_SIZE = 80

POSE_GROUP_TAG = 0x922E539C
POSE_TRANSFORM_TAG = 0x914532EB
POSE_ENTRY_PREFIX_SIZE = 9
POSE_TRANSFORM_SIZE = 45

FACE_LEFT_EYE = 0x22FE4DA9
FACE_RIGHT_EYE = 0xD8F170CA
FACE_LOWER_MOUTH = 0x29A684AC
FACE_UPPER_MOUTH = 0x4ED9C94E

HOST_LEFT_EYE = 0x88575789
HOST_RIGHT_EYE = 0x72586AEA
HOST_HEAD_ROOT = 0x07C159A2

SHARED_FACE_BONES = {
    FACE_LEFT_EYE,
    FACE_RIGHT_EYE,
    FACE_LOWER_MOUTH,
    FACE_UPPER_MOUTH,
}

REQUIRED_HOST_BONES = {
    HOST_LEFT_EYE,
    HOST_RIGHT_EYE,
    HOST_HEAD_ROOT,
}

@dataclass(frozen=True)
class BoneTransform:
    bone_id: int
    translation: tuple[float, float, float]
    rotation: tuple[float, float, float, float]

@dataclass(frozen=True)
class MeshBinding:
    geometry_uid: int
    bone_ids: tuple[int, ...]
    inverse_bind_matrices: tuple[tuple[float, ...], ...]
    pose_transforms: tuple[BoneTransform, ...] = ()
    joint_node_matrices: tuple[tuple[float, ...], ...] = ()

@dataclass(frozen=True)
class MeshPart:
    uid: int
    vertices: list[tuple[float, float, float]]
    uvs: list[tuple[float, float]]
    normals: list[tuple[float, float, float]]
    islands: tuple[MeshIsland, ...]
    tangents: tuple[tuple[float, float, float, float], ...] = ()
    joints: tuple[tuple[int, int, int, int], ...] = ()
    weights: tuple[tuple[float, float, float, float], ...] = ()
    binding: MeshBinding | None = None

@dataclass(frozen=True)
class ModelExportResult:
    model_uid: int
    part_count: int
    vertex_count: int
    triangle_count: int
    texture_count: int
    diffuse: str | None
    normal: str | None
    specular: str | None
    gltf_path: Path

def load_asset_payload(record: AssetRecord) -> bytes:
    """Load one asset without reading its complete archive"""

    with map_archive(record.archive) as data:
        return read_container(data, record.container_offset)

def _read_pose_transforms(blob: bytes, start: int) -> tuple[BoneTransform, ...]:
    """Read the first embedded bone pose group after a mesh binding"""

    group_marker = struct.pack("<I", POSE_GROUP_TAG)
    group_offset = blob.find(group_marker, start)

    if group_offset < 0:
        return ()

    if group_offset + 12 > len(blob):
        raise ValueError("Mesh pose group is truncated")

    pose_count = struct.unpack_from("<I", blob, group_offset + 8)[0]
    cursor = group_offset + 12
    transforms = []

    for pose_index in range(pose_count):
        tag_offset = cursor + POSE_ENTRY_PREFIX_SIZE
        record_end = tag_offset + 36

        if record_end > len(blob):
            raise ValueError(f"Mesh pose transform {pose_index} is truncated")

        (
            record_tag,
            bone_id,
            tx,
            ty,
            tz,
            qx,
            qy,
            qz,
            qw
        ) = struct.unpack_from("<II3f4f", blob, tag_offset)

        if record_tag != POSE_TRANSFORM_TAG:
            raise ValueError(f"Mesh pose transform {pose_index} has an invalid tag")

        transforms.append(
            BoneTransform(
                bone_id=bone_id,
                translation=(tx, ty, tz),
                rotation=(qx, qy, qz, qw)
            )
        )

        cursor += POSE_TRANSFORM_SIZE

    return tuple(transforms)

def read_mesh_bindings(payload: bytes) -> dict[int, MeshBinding]:
    """Read per geometry bone identifiers and inverse bind matrices"""

    bindings: dict[int, MeshBinding] = {}

    for entry in scan_nested_entries(payload):
        if entry.metadata.file_type != CURRENT_MESH:
            continue

        blob = payload[entry.data_offset:entry.end]

        if len(blob) < 20:
            raise ValueError(f"Mesh binding {entry.metadata.uid:016X} is truncated")

        embedded_type = struct.unpack_from("<I", blob, 0)[0]

        if embedded_type != CURRENT_MESH:
            raise ValueError(f"Mesh binding {entry.metadata.uid:016X} has an invalid type")

        bone_count = struct.unpack_from("<I", blob, 8)[0]
        records_end = 20 + bone_count * BONE_RECORD_SIZE

        # Siege places one separator byte between the bone table and UID
        geometry_offset = records_end + 1

        if geometry_offset +8 > len(blob):
            raise ValueError(f"Mesh binding {entry.metadata.uid:016X} has a truncated bone table")

        bone_ids = []
        inverse_bind_matrices = []

        for bone_index in range(bone_count):
            record_offset = 20 + bone_index * BONE_RECORD_SIZE
            record_tag, bone_id = struct.unpack_from("<II", blob, record_offset)

            if record_tag != BONE_RECORD_TAG:
                raise ValueError(f"Mesh binding {entry.metadata.uid:016X} bone {bone_index} has an invalid record tag")

            inverse_bind_matrix = struct.unpack_from("<16f", blob, record_offset + 8)

            bone_ids.append(bone_id)
            inverse_bind_matrices.append(inverse_bind_matrix)

        geometry_uid = struct.unpack_from("<Q", blob, geometry_offset)[0]
        pose_transforms = _read_pose_transforms(blob, geometry_offset + 8)

        if geometry_uid in bindings:
            raise ValueError(f"Geometry {geometry_uid:016X} has duplicate mesh bindings")

        bindings[geometry_uid] = MeshBinding(
            geometry_uid=geometry_uid,
            bone_ids=tuple(bone_ids),
            inverse_bind_matrices=tuple(inverse_bind_matrices),
            pose_transforms=pose_transforms
        )

    return bindings

def _gltf_multiply(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, ...]:
    """Multiply two glTF column-major matrices"""

    return transpose_matrix(
        multiply_matrices(
            transpose_matrix(left),
            transpose_matrix(right)
        )
    )

def _pose_to_gltf_matrix(transform: BoneTransform) -> tuple[float, ...]:
    """Convert a Siege translation and quaternion into a glTF matrix"""

    x, y, z, w = transform.rotation

    xx = x * x
    yy = y * y
    zz = z * z
    xy = x * y
    xz = x * z
    yz = y * z
    wx = w * x
    wy = w * y
    wz = w * z

    siege_row_matrix = (
        1.0 - 2.0 * (yy + zz),
        2.0 * (xy + wz),
        2.0 * (xz - wy),
        0.0,

        2.0 * (xy - wz),
        1.0 - 2.0 * (xx + zz),
        2.0 * (yz + wx),
        0.0,

        2.0 * (xz + wy),
        2.0 * (yz - wx),
        1.0 - 2.0 * (xx + yy),
        0.0,

        *transform.translation,
        1.0,
    )

    return siege_to_gltf_matrix(siege_row_matrix)

def _default_joint_node_matrices(binding: MeshBinding) -> tuple[tuple[float, ...], ...]:
    """Return explicit glTF joint matrices for one binding"""

    if binding.joint_node_matrices:
        return binding.joint_node_matrices

    return tuple(
        invert_gltf_matrix(siege_to_gltf_matrix(inverse_bind_matrix))
        for inverse_bind_matrix in binding.inverse_bind_matrices
    )

def resolve_static_face_bindings(bindings: Mapping[int, MeshBinding]) -> dict[int, MeshBinding]:
    """Apply the package's neutral pose to its shared facial geometry"""

    resolved = dict(bindings)

    shared = next(
        (
            binding
            for binding in bindings.values()
            if len(binding.bone_ids) == len(SHARED_FACE_BONES) and set(binding.bone_ids) == SHARED_FACE_BONES
        ),
        None
    )

    if shared is None:
        return resolved

    host = next(
        (
            binding
            for binding in bindings.values()
            if REQUIRED_HOST_BONES.issubset(binding.bone_ids) and SHARED_FACE_BONES.issubset(transform.bone_id for transform in binding.pose_transforms)
        ),
        None
    )

    if host is None:
        return resolved

    host_matrices = _default_joint_node_matrices(host)
    face_matrices = list(_default_joint_node_matrices(shared))

    for face_bone, host_bone in (FACE_LEFT_EYE, HOST_LEFT_EYE), (FACE_RIGHT_EYE, HOST_RIGHT_EYE):
        face_index = shared.bone_ids.index(face_bone)
        host_index = host.bone_ids.index(host_bone)

        face_matrices[face_index] = host_matrices[host_index]

    pose_by_bone = {
        transform.bone_id: transform
        for transform in host.pose_transforms
    }

    root_index = host.bone_ids.index(HOST_HEAD_ROOT)
    upper_mouth_index = shared.bone_ids.index(FACE_UPPER_MOUTH)

    upper_mouth_target = _gltf_multiply(
        host_matrices[root_index],
        _pose_to_gltf_matrix(pose_by_bone[FACE_UPPER_MOUTH])
    )

    upper_mouth_matrix = face_matrices[upper_mouth_index]

    mouth_delta = tuple(
        upper_mouth_target[index] - upper_mouth_matrix[index]
        for index in (12, 13, 14)
    )

    # Both facial mouth joints influence shared vertices
    # Moving them by different amounts stretches the teeth, gums and tongue
    for bone_id in FACE_LOWER_MOUTH, FACE_UPPER_MOUTH:
        bone_index = shared.bone_ids.index(bone_id)
        matrix = list(face_matrices[bone_index])

        for matrix_index, amount in zip((12, 13, 14), mouth_delta):
            matrix[matrix_index] += amount

        face_matrices[bone_index] = tuple(matrix)

    resolved[shared.geometry_uid] = replace(
        shared,
        joint_node_matrices=tuple(face_matrices)
    )

    return resolved

def resolve_direct_texture_uids(model_uid: int, index: AssetIndex) -> tuple[int, ...]:
    """Read texture UIDs embedded directly in a model package"""

    record = index.primary(model_uid)

    if record is None:
        return ()

    return embedded_texture_uids(load_asset_payload(record))

def resolve_dependency_uids(model_uid: int, children: Mapping[int, Iterable[int]]) -> tuple[int, ...]:
    """Return the model UID and every recursively reachable child UID"""

    seen: set[int] = set()
    queue = deque([model_uid])

    while queue:
        uid = queue.popleft()

        if uid in seen:
            continue

        seen.add(uid)
        queue.extend(children.get(uid, ()))

    return tuple(sorted(seen))

def resolve_texture_uids(model_uid: int, children: Mapping[int, Iterable[int]], index: AssetIndex) -> tuple[int, ...]:
    """Collect indexed texture assets from the depgraph and model package"""

    candidate_uids = set(resolve_dependency_uids(model_uid, children))

    candidate_uids.update(resolve_direct_texture_uids(model_uid, index))

    textures = {
        uid
        for uid in candidate_uids
        if (record := index.primary(uid)) is not None and record.file_type in TEXTURE_TYPES
    }

    return tuple(sorted(textures))

def resolve_geometry_records(model_uid: int, children: Mapping[int, Iterable[int]], index: AssetIndex) -> tuple[AssetRecord, ...]:
    """Return every direct compiled-geometry child of a model"""

    records: list[AssetRecord] = []
    seen: set[int] = set()

    for child_uid in children.get(model_uid, ()):
        if child_uid in seen:
            continue

        seen.add(child_uid)
        record = index.primary(child_uid)

        if (record is not None and record.file_type == COMPILED_MESH_OBJ):
            records.append(record)

    if not records:
        raise ValueError(f"No CompiledMeshObject children found for model {model_uid:016X}")

    return tuple(sorted(records, key=lambda record: record.uid))

def decode_mesh_parts(records: Iterable[AssetRecord], bindings: Mapping[int, MeshBinding] | None = None) -> tuple[MeshPart, ...]:
    parts: list[MeshPart] = []
    bindings = bindings or {}

    for record in records:
        payload = load_asset_payload(record)

        (
            vertices,
            uvs,
            normals,
            tangents,
            joints,
            weights,
            islands
        ) = read_mesh_with_islands(payload)

        binding = bindings.get(record.uid) if joints else None

        if binding is not None and joints:
            used_joint_indices = [
                joint
                for joint_values, weight_values in zip(joints, weights)
                for joint, weight in zip(joint_values, weight_values)
                if weight > 0.0
            ]

            highest_joint = max(used_joint_indices, default=-1)

            if highest_joint >= len(binding.bone_ids):
                raise ValueError(f"Geometry {record.uid:016X} uses joint {highest_joint} but its binding contains only {len(binding.bone_ids)} bones")

        if len(uvs) != len(vertices):
            raise ValueError(f"Geometry {record.uid:016X} has {len(vertices)} vertices but {len(uvs)} UV coordinates")

        if len(normals) != len(vertices):
            raise ValueError(f"Geometry {record.uid:016X} has {len(vertices)} vertices but {len(normals)} normals")

        if len(tangents) != len(vertices):
            raise ValueError(f"Geometry {record.uid:016X} has {len(vertices)} vertices but {len(tangents)} tangents")

        parts.append(
            MeshPart(
                uid=record.uid,
                vertices=vertices,
                uvs=uvs,
                normals=normals,
                islands=islands,
                tangents=tangents,
                joints=joints,
                weights=weights,
                binding=binding
            )
        )

    return tuple(parts)

def is_blank_texture(path: Path) -> bool:
    with Image.open(path) as source:
        preview = source.convert("RGB").resize((16, 16))
        extrema= preview.getextrema()

    return all(maximum < 8 for _, maximum in extrema)

def decode_model_textures(texture_uids: Iterable[int], index: AssetIndex, output_directory: Path) -> tuple[list[tuple[int, int, str]], str | None, str | None, str | None]:
    decoded: list[tuple[int, int, str]] = []

    for texture_uid in texture_uids:
        record = index.primary(texture_uid)
        if record is None:
            continue

        filename = f"{texture_uid:016X}.png"
        path = output_directory / filename

        try:
            payload = load_asset_payload(record)
            width, height, _, texture_type = (save_png(path, payload))

            decoded.append(
                (
                    width * height,
                    texture_type,
                    filename
                )
            )
        except ValueError:
            # streamed, partial or unsupported texture tier
            continue

    diffuse = None
    normal = None
    specular = None

    for _, texture_type, filename in sorted(decoded, key=lambda item: item[0], reverse=True):
        path = output_directory / filename

        if (texture_type == 0 and diffuse is None and not is_blank_texture(path)):
            diffuse = filename
        elif (texture_type == 1 and normal is None):
            normal = filename
        elif (texture_type == 2 and specular is None):
            specular = filename

    return decoded, diffuse, normal, specular

def resolve_export_material_textures(texture_sets: Iterable[MaterialTextureSet], decoded_textures: Iterable[tuple[int, int, str]]) -> tuple[MaterialTextures, ...]:
    """Choose the largest decoded texture tier for each material role"""

    decoded_by_uid: dict[int, tuple[int, str]] = {}

    for area, _, filename in decoded_textures:
        try:
            uid = int(Path(filename).stem, 16)
        except ValueError:
            continue

        current = decoded_by_uid.get(uid)

        if current is None or area > current[0]:
            decoded_by_uid[uid] = (area, filename)

    def choose(candidates: Iterable[int]) -> str | None:
        matches = [
            decoded_by_uid[uid]
            for uid in candidates
            if uid in decoded_by_uid
        ]

        if not matches:
            return None

        return max(matches, key=lambda item: item[0])[1]

    def resolve(texture_set: MaterialTextureSet) -> MaterialTextures:
        detail_normals = []

        for selector in texture_set.selectors:
            if selector.source != "detail" or selector.role != NORMAL_ROLE:
                continue

            filename = choose(selector.texture_uids)

            if filename is not None and filename not in detail_normals:
                detail_normals.append(filename)

        shader_candidates: dict[str, list[int]] = {}

        for selector in texture_set.selectors:
            if selector.source != "shader" or selector.shader_binding is None:
                continue

            shader_candidates.setdefault(selector.shader_binding, []).extend(selector.texture_uids)

        shader_textures = []

        for binding in sorted(shader_candidates):
            filename = choose(shader_candidates[binding])

            if filename is not None:
                shader_textures.append((binding, filename))

        return MaterialTextures(
            diffuse=choose(texture_set.diffuse_uids),
            normal=choose(texture_set.normal_uids),
            specular=choose(texture_set.specular_uids),
            mask=choose(texture_set.mask_uids),
            detail_normals=tuple(detail_normals),
            shader_textures=tuple(shader_textures),
            shader_uid=texture_set.shader_uid,
            shader_uniforms=tuple(
                (uniform.name, uniform.values)
                for uniform in texture_set.shader_uniforms
            ),
        )

    return tuple(
        resolve(texture_set)
        for texture_set in texture_sets
    )

def export_model(model_uid: int, children: Mapping[int, Iterable[int]], index: AssetIndex, output_directory: str | Path) -> ModelExportResult:
    """Export every geometry child and linked decodable texture"""

    output_directory = Path(output_directory).resolve()
    output_directory.mkdir(parents=True, exist_ok=True)

    geometry_records = resolve_geometry_records(model_uid, children, index)
    model_record = index.primary(model_uid)
    model_payload = (
        load_asset_payload(model_record)
        if model_record is not None
        else None
    )
    mesh_bindings = (
        resolve_static_face_bindings(read_mesh_bindings(model_payload))
        if model_payload is not None
        else {}
    )

    parts = decode_mesh_parts(geometry_records, mesh_bindings)
    texture_uids = resolve_texture_uids(model_uid, children, index)

    (
        decoded_textures,
        diffuse,
        normal,
        specular
    ) = decode_model_textures(texture_uids, index, output_directory)

    export_parts = parts
    material_textures: tuple[MaterialTextures, ...] = ()

    if model_payload is not None:
        part_texture_sets = resolve_material_texture_sets(model_payload, texture_uids, (record.uid for record in geometry_records))

        if any(part_texture_sets):
            rebased_parts = []
            resolved_materials = []
            material_offset = 0

            for part, texture_set in zip(parts, part_texture_sets):
                local_material_ids = tuple(dict.fromkeys(island.material_id for island in part.islands))
                local_material_count = max(local_material_ids, default=-1) + 1

                slot_sets = [
                    MaterialTextureSet()
                    for _ in range(local_material_count)
                ]

                for material_id, texture_set_for_slot in zip(local_material_ids, texture_set):
                    slot_sets[material_id] = texture_set_for_slot

                rebased_parts.append(
                    replace(
                        part,
                        islands=tuple(
                            replace(
                                island,
                                material_id=(
                                    island.material_id + material_offset
                                )
                            )
                            for island in part.islands
                        )
                    )
                )

                resolved_materials.extend(resolve_export_material_textures(slot_sets, decoded_textures))

                material_offset += local_material_count

            export_parts = tuple(rebased_parts)
            material_textures = tuple(resolved_materials)

    part_count = len(parts)
    vertex_count = sum(len(part.vertices) for part in parts)
    triangle_count = sum(len(island.faces) for part in parts for island in part.islands)

    gltf_path = write_gltf(
        model_uid,
        export_parts,
        output_directory,
        diffuse=diffuse,
        normal=normal,
        specular=specular,
        material_textures=material_textures or None
    )

    return ModelExportResult(
        model_uid=model_uid,
        part_count=part_count,
        vertex_count=vertex_count,
        triangle_count=triangle_count,
        texture_count=len(decoded_textures),
        diffuse=diffuse,
        normal=normal,
        specular=specular,
        gltf_path=gltf_path
    )