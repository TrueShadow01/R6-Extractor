"""Composite model resolution and export"""

# Yo Isaac some Poses are off for later operators + some materials are fucked still, like usual
# Ash throws a error for not detecting a compiled mesh obj, tell Aiden to look into it, maybe some hex got fucked with the new season that i didnt check
# - shadow

from __future__ import annotations

import struct
import math
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Mapping

from PIL import Image

from src.skeleton import resolve_model_skeletons
from src.tint import bake_tinted_material
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

# Fuck me, I hate this - Aiden
# Brother what - Shadow
COMPILED_MESH_OBJ = 0xABEB2DFB

TEXTURE_TYPES = {
    0x13237FE9, # CompiledTextureMap
    0x9F492D22, # UltraResTexMap
    0x3876CCDF, # FutureResTexMap
    0x59CE4D13, # HiResTexMap
    0xF9C80707, # MedResTexMap
    0xD7B5C478, # LowResTexMap
}

BONE_RECORD_TAG = 0x3761F6E4
BONE_RECORD_SIZE = 80

POSE_GROUP_TAG = 0x01C8F539
POSE_TRANSFORM_TAG = 0x4A0A58DA
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

def complete_mesh_binding(binding: MeshBinding, required_bone_count: int, palette_indices: Iterable[int]) -> MeshBinding:
    """Add identity joints for implicit slots declared by a mesh palette"""

    current_bone_count = len(binding.bone_ids)

    if required_bone_count <= current_bone_count:
        return binding

    missing_indices = set(range(current_bone_count, required_bone_count))

    if not missing_indices.issubset(set(palette_indices)):
        highest_joint = required_bone_count - 1

        raise ValueError(f"Geometry {binding.geometry_uid:016X} uses joint {highest_joint} but its binding contains only {current_bone_count} bones")

    identity = (
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )
    missing_count = required_bone_count - current_bone_count

    return replace(
        binding,
        bone_ids=(binding.bone_ids + (0xFFFFFFFF,) * missing_count),
        inverse_bind_matrices=(binding.inverse_bind_matrices + (identity,) * missing_count),
        joint_node_matrices=(binding.joint_node_matrices + (identity,) * missing_count if binding.joint_node_matrices else ())
    )

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

def align_holster_root(payload, offset, bindings, matrix):
    """Place a holster subtree in the matching body root's frame"""
    marker = struct.pack("<I", 0xF6ECC8A9)
    seen = set()

    while True:
        if offset in seen or offset + 10 > len(payload):
            raise ValueError("Invalid holster root chain")
        seen.add(offset)

        kind = payload[offset + 8]
        if kind == 3:
            break
        if kind != 2 or offset + 18 > len(payload):
            raise ValueError("Invalid holster parent record")

        parent_uid = struct.unpack_from("<Q", payload, offset + 9)[0]
        signature = struct.pack("<Q", parent_uid) + marker
        parent = payload.find(signature)
        if parent < 0 or payload.find(signature, parent + 1) >= 0:
            raise ValueError("Missing or ambiguous holster parent node")
        offset = parent + 8

    root_id = struct.unpack_from("<I", payload, offset + 4)[0]
    hosts = [
        pose
        for uid, binding in bindings.items()
        if uid != 0x7B8293A4C
        for bone, pose in zip(binding.bone_ids, _default_joint_node_matrices(binding))
        if bone == root_id
    ]
    if not hosts:
        return matrix
    if len(hosts) != 1:
        raise ValueError("Ambiguous holster body root")

    if offset + 86 > len(payload) or payload[offset + 9] != 0:
        raise ValueError("Truncated holster root pose")

    tag, zero, pose_type = struct.unpack_from("<III", payload, offset + 10)
    if (tag & 0xFFFF0000) != 0xFBF80000 or zero or pose_type != 0x23F349BE:
        raise ValueError("Unsupported holster root pose")

    values = struct.unpack_from("16f", payload, offset + 22)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Non-finite holster root pose")
    if values[3] != 0.0 or values[11] != 0.0:
        raise ValueError("Unsupported holster root pose layout")
    if abs(sum(value * value for value in values[4:8]) - 1.0) > 0.001:
        raise ValueError("Invalid holster root quaternion")

    root = _pose_to_gltf_matrix(BoneTransform(root_id, values[:3], values[4:8]))
    correction = _gltf_multiply(hosts[0], invert_gltf_matrix(root))
    return _gltf_multiply(correction, matrix)

def resolve_holster_binding(payload, bindings):
    """Place the shared holster using its package-owned skeleton pose"""
    # Полная хрень

    from src.skeleton import read_skeleton_parents

    geometry_uid = 0x7B8293A4C
    bone_id = 0x8E904DC4
    binding = bindings.get(geometry_uid)
    if binding is None or binding.joint_node_matrices:
        return bindings
    if binding.bone_ids != (bone_id,):
        raise ValueError("Unexpected shared holster binding")

    graphs = read_skeleton_parents(payload)
    if not any(bone_id in graph for graph in graphs):
        raise ValueError("Shared holster has no package-owned skeleton")

    signature = struct.pack("<II", 0xF6ECC8A9, bone_id)
    cursor = 0
    matrices = []

    while True:
        offset = payload.find(signature, cursor)
        if offset < 0:
            break
        cursor = offset + 1

        if offset + 94 > len(payload):
            continue
        if payload[offset + 8] != 2 or payload[offset + 17] != 0:
            continue

        marker, zero, kind = struct.unpack_from("<III", payload, offset + 18)
        if (marker & 0xFFFF0000) != 0xFBF80000 or zero or kind != 0x23F349BE:
            continue

        values = struct.unpack_from("<16f", payload, offset + 30)
        if not all(math.isfinite(value) for value in values):
            raise ValueError("Non-finite holster pose")
        if values[3] != 0.0 or values[11] != 0.0:
            raise ValueError("Unsupported holster pose layout")

        rotation = values[4:8]
        if abs(sum(value * value for value in rotation) - 1.0) > 0.001:
            raise ValueError("Invalid holster quaternion")

        matrix = _pose_to_gltf_matrix(BoneTransform(bone_id, values[:3], rotation))
        matrices.append(align_holster_root(payload, offset, bindings, matrix))

    if not matrices:
        raise ValueError("Missing shared holster pose")
    if any(max(abs(a - b) for a, b in zip(matrices[0], other)) > 1e-4 for other in matrices[1:]):
        raise ValueError("Conflicting shared holster poses")

    resolved = dict(bindings)
    resolved[geometry_uid] = replace(binding, joint_node_matrices=(matrices[0],))
    return resolved

def resolve_static_attachment_bindings(payload, bindings):
    """Place single-bone attachments using their embedded global pose"""

    resolved = dict(bindings)
    skeleton_type = 0x299DF12C

    for entry in scan_nested_entries(payload):
        if entry.metadata.file_type != CURRENT_MESH:
            continue

        blob = payload[entry.data_offset:entry.end]
        if len(blob) < 109 or struct.unpack_from("<I", blob, 8)[0] != 1:
            continue

        geometry_uid = struct.unpack_from("<Q", blob, 101)[0]
        binding = bindings.get(geometry_uid)
        if binding is None or len(binding.bone_ids) != 1:
            continue
        if binding.joint_node_matrices:
            continue

        skeleton_starts = []
        for offset in range(len(blob) - 24):
            length, container = struct.unpack_from("<HH", blob, offset)
            data_start = offset + 20 + length
            if container != 2 or length > 4096 or data_start + 4 > len(blob):
                continue

            if struct.unpack_from("<I", blob, offset + 8 + length)[0] == skeleton_type == struct.unpack_from("<I", blob, data_start)[0]:
                skeleton_starts.append(data_start)

        if not skeleton_starts:
            continue
        if len(skeleton_starts) != 1:
            raise ValueError("Ambiguous attachment skeleton")

        bone_id = binding.bone_ids[0]
        signature = struct.pack("<II", 0x41899311, bone_id)
        cursor = skeleton_starts[0] + 4
        poses = []

        while True:
            offset = blob.find(signature, cursor)
            if offset < 0:
                break

            cursor = offset + 1

            if offset + 94 > len(blob):
                continue
            if blob[offset + 8] != 2 or blob[offset + 17] != 0:
                continue

            marker, zero, kind = struct.unpack_from("<III", blob, offset + 18)
            if (marker & 0xFFFF0000) != 0xFBF80000 or zero or kind != 0x18471F43:
                continue

            values = struct.unpack_from("<16f", blob, offset + 30)
            if not all(math.isfinite(v) for v in values):
                raise ValueError("Non-finite attachment pose")
            if values[3] != 0.0 or values[11] != 0.0:
                raise ValueError("Unexpected attachment pose layout")

            rotation = values[4:8]
            if abs(sum(v * v for v in rotation) - 1.0) > 0.001:
                raise ValueError("Invalid attachment quaternion")

            poses.append(BoneTransform(bone_id, values[:3], rotation))

        if len(poses) > 1:
            raise ValueError("Ambiguous attachment bone pose")

        if poses:
            resolved[geometry_uid] = replace(binding, joint_node_matrices=(_pose_to_gltf_matrix(poses[0]),))
    return resolve_holster_binding(payload, resolved)

def resolve_attachment_frames(bindings: Mapping[int, MeshBinding]) -> dict[int, MeshBinding]:
    """Align attachment axis conventions using matching source bind bones"""

    resolved = dict(bindings)
    tolerance = 1e-4
    identity = tuple(1.0 if index % 5 == 0 else 0.0 for index in range(16))

    def agrees(left, right):
        return all(abs(a - b) <= tolerance for a, b in zip(left, right))

    for binding in bindings.values():
        if binding.joint_node_matrices or HOST_HEAD_ROOT not in binding.bone_ids:
            continue

        matrices = _default_joint_node_matrices(binding)
        own = dict(zip(binding.bone_ids, matrices))
        corrections = []

        for host in bindings.values():
            if len(host.bone_ids) <= len(binding.bone_ids):
                continue

            host_matrices = dict(zip(host.bone_ids, _default_joint_node_matrices(host)))
            common = own.keys() & host_matrices.keys()
            if HOST_HEAD_ROOT not in common or len(common) < 3:
                continue

            delta = _gltf_multiply(host_matrices[HOST_HEAD_ROOT], invert_gltf_matrix(own[HOST_HEAD_ROOT]))
            if not all(agrees(delta, _gltf_multiply(host_matrices[bone_id], invert_gltf_matrix(own[bone_id]))) for bone_id in common):
                continue

            # fix axis conventions, not intentional attachment offsets - Aiden
            basis = tuple(float(round(value)) for value in delta)
            if not agrees(delta, basis):
                continue
            if basis[12:15] != (0.0, 0.0, 0.0) or basis[15] != 1.0:
                continue
            if any(basis[index] != 0.0 for index in (3, 7, 11)):
                continue
            if any(sum(abs(basis[column * 4 + row]) for column in range(3)) != 1.0 for row in range(3)) or any(sum(abs(basis[column * 4 + row]) for row in range(3)) != 1.0 for column in range(3)):
                continue

            if not agrees(delta, identity):
                corrections.append(delta)

        if corrections and all(agrees(corrections[0], delta) for delta in corrections[1:]):
            resolved[binding.geometry_uid] = replace(binding, joint_node_matrices=tuple(_gltf_multiply(corrections[0], matrix) for matrix in matrices))

    # Tachanka's head and helmet share bones but use different frames
    # Derive the correction from those bones, not a guessed offset - Issac
    head = resolved.get(0x9D00E00C7)
    helmet = resolved.get(0x9D00E00BB)

    if head is not None and helmet is not None and not head.joint_node_matrices:
        head_matrices = _default_joint_node_matrices(head)
        helmet_matrices = _default_joint_node_matrices(helmet)

        head_by_bone = dict(zip(head.bone_ids, head_matrices))
        helmet_by_bone = dict(zip(helmet.bone_ids, helmet_matrices))
        common = head_by_bone.keys() & helmet_by_bone.keys()

        if HOST_HEAD_ROOT in common and len(common) == 16:
            correction = _gltf_multiply(helmet_by_bone[HOST_HEAD_ROOT], invert_gltf_matrix(head_by_bone[HOST_HEAD_ROOT]))

            if all(agrees(correction, _gltf_multiply(helmet_by_bone[bone_id], invert_gltf_matrix(head_by_bone[bone_id]))) for bone_id in common):
                resolved[head.geometry_uid] = replace(
                    head,
                    joint_node_matrices=tuple(_gltf_multiply(correction, matrix) for matrix in head_matrices)
                )

    return resolved

def resolve_static_face_bindings(bindings: Mapping[int, MeshBinding], payload: bytes | None = None) -> dict[int, MeshBinding]:
    """Apply the package's neutral pose to its shared facial geometry"""

    resolved = dict(bindings)

    shared = next(
        (
            binding
            for binding in bindings.values()
            if SHARED_FACE_BONES.issubset(binding.bone_ids)
        ),
        None
    )

    if shared is None:
        return resolved

    host = next(
        (
            binding
            for binding in bindings.values()
            if (REQUIRED_HOST_BONES.issubset(binding.bone_ids) if len(shared.bone_ids) == len(SHARED_FACE_BONES) else HOST_HEAD_ROOT in binding.bone_ids) and SHARED_FACE_BONES.issubset(transform.bone_id for transform in binding.pose_transforms)
        ),
        None
    )

    package_root = None

    if host is None and payload is not None and len(shared.bone_ids) > len(SHARED_FACE_BONES):
        # Some newer Heads keep their neutral face pose at package level - Isaac
        signature = struct.pack("<II", POSE_GROUP_TAG, 0xFFB49035)
        groups = set()
        cursor = 0

        while True:
            offset = payload.find(signature, cursor)
            if offset < 0:
                break

            cursor = offset + len(signature)
            transforms = _read_pose_transforms(payload, offset)
            if transforms and transforms[0].bone_id == 0x40CDE165 and sum(t.bone_id == 0x40CDE165 for t in transforms) == 1 and SHARED_FACE_BONES.issubset(t.bone_id for t in transforms):
                groups.add(transforms)

        if len(groups) == 1:
            transforms = next(iter(groups))
            pose_bones = {t.bone_id for t in transforms}
            candidates = [
                binding for binding in bindings.values()
                if HOST_HEAD_ROOT in binding.bone_ids
            ]
            scores = [
                len(pose_bones.intersection(binding.bone_ids))
                for binding in candidates
            ]

            if scores and max(scores) > 0 and scores.count(max(scores)) == 1:
                host = replace(candidates[scores.index(max(scores))], pose_transforms=transforms)

                package_root = transforms[0]

    if host is None:
        return resolved

    host_matrices = _default_joint_node_matrices(host)
    face_matrices = list(_default_joint_node_matrices(shared))

    pose_by_bone = {
        transform.bone_id: transform
        for transform in host.pose_transforms
    }

    root_index = host.bone_ids.index(HOST_HEAD_ROOT)
    root_matrix = host_matrices[root_index]

    if package_root is not None:
        root_matrix = _gltf_multiply(root_matrix, _pose_to_gltf_matrix(package_root))

    for face_bone in FACE_LEFT_EYE, FACE_RIGHT_EYE:
        face_index = shared.bone_ids.index(face_bone)

        target = _gltf_multiply(root_matrix, _pose_to_gltf_matrix(pose_by_bone[face_bone]))

        # Static neutral gaze: retain the shader eye's bind orientation
        # while placing its pivot at the operator-specific position
        neutral = list(face_matrices[face_index])
        neutral[12:15] = target[12:15]
        face_matrices[face_index] = tuple(neutral)

    if len(shared.bone_ids) != len(SHARED_FACE_BONES):
        # Newer face rigs have extra mouth joints, only move the eyes here - Isaac
        resolved[shared.geometry_uid] = replace(shared, joint_node_matrices=tuple(face_matrices))
        return resolved

    upper_mouth_index = shared.bone_ids.index(FACE_UPPER_MOUTH)

    upper_mouth_target = _gltf_multiply(root_matrix, _pose_to_gltf_matrix(pose_by_bone[FACE_UPPER_MOUTH]))

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
            palette_indices = {
                joint
                for island in islands
                for joint in island.bone_palette
            }

            binding = complete_mesh_binding(binding, highest_joint + 1, palette_indices)

        if len(uvs) != len(vertices):
            raise ValueError(f"Geometry {record.uid:016X} has {len(vertices)} vertices but {len(uvs)} UV coordinates")

        invalid_uvs = sum(not all(math.isfinite(component) for component in uv) for uv in uvs)
        if invalid_uvs:
            # Nyx: keep the mesh. Make the missing UV data visible in the log
            uvs = [
                tuple(component if math.isfinite(component) else 0.0 for component in uv)
                for uv in uvs
            ]
            print(
                f"WARNING: Geometry {record.uid:016X}: replaced non-finite UV components with 0 in {invalid_uvs} vertices. "
                "Texture Placement on affected faces is approximate.",
                flush=True
            )

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
            solid_color=texture_set.solid_color,
            detail_normals=tuple(detail_normals),
            shader_textures=tuple(shader_textures),
            shader_uid=texture_set.shader_uid,
            material_uid=texture_set.material_uid,
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
        resolve_static_face_bindings(resolve_attachment_frames(resolve_static_attachment_bindings(model_payload, read_mesh_bindings(model_payload))), model_payload)
        if model_payload is not None
        else {}
    )

    if model_uid == 0x156B734234:
        body_record = index.primary(0x156B73543D)
        if body_record is None:
            raise ValueError("Tachanka head alignment requires body model 156B73543D")

        body_bindings = read_mesh_bindings(load_asset_payload(body_record))
        body = body_bindings.get(0x9D00E0074)
        helmet = mesh_bindings.get(0x9D00E00BB)
        if body is None or helmet is None:
            raise ValueError("Tachanka attachment bindings have changed")

        body_by_bone = dict(zip(body.bone_ids, _default_joint_node_matrices(body)))
        helmet_by_bone = dict(zip(helmet.bone_ids, _default_joint_node_matrices(helmet)))
        common = body_by_bone.keys() & helmet_by_bone.keys()

        if HOST_HEAD_ROOT not in common or len(common) < 3:
            raise ValueError("Insufficient shared bones for Tachanka head alignment")

        correction = _gltf_multiply(body_by_bone[HOST_HEAD_ROOT], invert_gltf_matrix(helmet_by_bone[HOST_HEAD_ROOT]))

        if any(max(abs(a - b) for a, b in zip(correction, _gltf_multiply(body_by_bone[bone_id], invert_gltf_matrix(helmet_by_bone[bone_id])))) > 1e-4 for bone_id in common):
            raise ValueError("Tachanka body and helmet attachment frames disagree")

        # Keep the assembled face, eyes and helmet together - Aiden
        mesh_bindings = {
            uid: replace(binding, joint_node_matrices=tuple(_gltf_multiply(correction, matrix) for matrix in _default_joint_node_matrices(binding)))
            for uid, binding in  mesh_bindings.items()
        }

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

    material_textures = tuple(
        bake_tinted_material(slot, output_directory)
        for slot in material_textures
    )

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
        material_textures=material_textures or None,
        skeletons=resolve_model_skeletons(model_payload, index) if model_payload is not None else {}
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