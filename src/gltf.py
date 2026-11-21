"""Minimal dependency free glTF 2.0 writer for Siege models"""
from __future__ import annotations

from PIL import Image
from src.glass import apply_optical_preview, split_atlas_lenses

import json
import math
import struct
import zlib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Protocol, Sequence

_BONE_NAME_CANDIDATES = (
    ("Head", "Hips", "Spine", "Spine1", "Spine2")
    + tuple(
        side + part
        for side in ("Left", "Right")
        for part in (
            "Arm", "ForeArm", "Hand", "Shoulder",
            "UpLeg", "Leg", "Foot", "ToeBase",
        )
    )
    + tuple(
        side + "Hand" + finger + str(segment)
        for side in ("Left", "Right")
        for finger in ("Index", "Middle", "Pinky", "Ring", "Thumb")
        for segment in (1, 2, 3)
    )
    + (
        "LeftInHandIndex", "LeftInHandMiddle",
        "LeftInHandPinky", "LeftInHandRing",
        "RightInHandPinky", "RightInHandRing",
    )
)

BONE_NAMES = {
    zlib.crc32(name.encode("ascii")): name
    for name in _BONE_NAME_CANDIDATES
}

ARRAY_BUFFER = 34962
ELEMENT_ARRAY_BUFFER = 34963

UNSIGNED_BYTE = 5121
FLOAT = 5126
UNSIGNED_INT = 5125

TRIANGLES = 4

# Confirmed Siege shader transparency behavior
# Unknown shaders still use the image alpha fallback
OPAQUE_SHADER_UIDS = {
    0x000000003CAE6B71, # skin
    0x0000000841DC11F9, # tinted headgear
    0x0000001397A32F38, # solid cosmetic mask
    0x000000557005948D, # eye shader
    0x0000000099E2C926, # cutout uses vertex alpha, not diffuse alpha
    0x0000000099E2C947, # diffuse alpha selects detail effects
}

ALPHA_MASK_SHADER_UIDS = {
    0x000000003051C028, # hair and eyelashes
    0x00000055B93242F6, # newer hair variant
}

GLASS_SHADER_UIDS = {
    0x0000000099E2C950,
}

HIDDEN_BY_DEFAULT_SHADER_UIDS = {
    0x0000000F2BB85C7E, # Warden Smart Glance transition overlay
}

def siege_color_to_gltf(color: Sequence[float]) -> tuple[float, float, float, float]:
    """Convert stored sRGB material colors to linear glTF factors"""

    linear = tuple(
        component / 12.92
        if component <= 0.04045
        else ((component + 0.055) / 1.055) ** 2.4
        for component in color[:3]
    )

    return linear + (color[3],)

SIEGE_TO_GLTF_BASIS = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, -1.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)

GLTF_TO_SIEGE_BASIS = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 0.0, -1.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)

class MeshBindingLike(Protocol):
    bone_ids: Sequence[int]
    inverse_bind_matrices: Sequence[Sequence[float]]
    joint_node_matrices: Sequence[Sequence[float]]

class MeshPartLike(Protocol):
    uid: int
    vertices: Sequence[tuple[float, float, float]]
    uvs: Sequence[tuple[float, float]]
    normals: Sequence[tuple[float, float, float]]
    islands: Sequence
    tangents: Sequence[tuple[float, float, float, float]]
    joints: Sequence[tuple[int, int, int, int]]
    weights: Sequence[tuple[float, float, float, float]]
    binding: MeshBindingLike | None

@dataclass(frozen=True)
class MaterialTextures:
    diffuse: str | None = None
    normal: str | None = None
    specular: str | None = None
    mask: str | None = None
    solid_color: tuple[float, float, float, float] | None = None
    detail_normals: tuple[str, ...] = ()
    shader_textures: tuple[tuple[str, str], ...] = ()
    shader_uid: int | None = None
    shader_uniforms: tuple[tuple[str, tuple[float, ...]], ...] = ()
    material_uid: int | None = None

@dataclass
class BinaryBuffer: 
    data: bytearray = field(default_factory=bytearray)
    views: list[dict] = field(default_factory=list)

    def align(self, alignment: int = 4) -> None:
        while len(self.data) % alignment:
            self.data.append(0)

    def add(self, raw: bytes, *, target: int, name: str) -> int:
        self.align()

        offset = len(self.data)
        self.data.extend(raw)

        index = len(self.views)

        view = {
            "name": name,
            "buffer": 0,
            "byteOffset": offset,
            "byteLength": len(raw)
        }

        if target is not None:
            view["target"] = target

        self.views.append(view)

        return index

def pack_floats(values: Sequence[float]) -> bytes:
    if not values:
        return b""

    if not all(math.isfinite(value) for value in values):
        raise ValueError("glTF attributes contain NaN or infinity")

    return struct.pack(f"<{len(values)}f", *values)

def pack_unsigned_bytes(values: Sequence[int]) -> bytes:
    if not values:
        return b""

    if min(values) < 0 or max(values) > 255:
        raise ValueError("Unsigned byte values must be between 0 and 255")

    return struct.pack(f"<{len(values)}B", *values)

def pack_unsigned_ints(values: Sequence[int]) -> bytes:
    if not values:
        return b""

    if min(values) < 0:
        raise ValueError("glTF indices cannot be negative")

    return struct.pack(f"<{len(values)}I", *values)

def component_minimums(values: Sequence[tuple[float, ...]]) -> list[float]:
    width = len(values[0])

    return [
        min(value[index] for value in values)
        for index in range(width)
    ]

def component_maximums(values: Sequence[tuple[float, ...]])-> list[float]:
    width = len(values[0])

    return [
        max(value[index] for value in values)
        for index in range(width)
    ]

def transpose_matrix(values: Sequence[float]) -> tuple[float, ...]:
    if len(values) != 16:
        raise ValueError("A 4x4 matrix must contain 16 values")

    return tuple(
        values[column * 4 + row]
        for row in range(4)
        for column in range(4)
    )

def multiply_matrices(left: Sequence[float], right: Sequence[float]) -> tuple[float, ...]:
    if len(left) != 16 or len(right) != 16:
        raise ValueError("A 4x4 matrix must contain 16 values")

    return tuple(
        sum(
            left[row * 4 + index] * right[index * 4 + column]
            for index in range(4)
        )
        for row in range(4)
        for column in range(4)
    )

def invert_matrix(values: Sequence[float]) -> tuple[float, ...]:
    if len(values) != 16:
        raise ValueError("A 4x4 matrix must contain 16 values")

    rows = [
        [
            *(
                float(values[row * 4 + column])
                for column in range(4)
            ),
            *(
                1.0 if row == column else 0.0
                for column in range(4)
            )
        ]
        for row in range(4)
    ]

    for column in range(4):
        pivot = max(range(column, 4), key=lambda row: abs(rows[row][column]))

        if abs(rows[pivot][column]) <= 1e-12:
            raise ValueError("Matrix is not invertible")

        rows[column], rows[pivot] = rows[pivot], rows[column]

        divisor = rows[column][column]

        rows[column] = [
            value / divisor
            for value in rows[column]
        ]

        for row in range(4):
            if row == column:
                continue

            factor = rows[row][column]

            rows[row] = [
                value - factor * pivot_value
                for value, pivot_value in zip(rows[row], rows[column])
            ]

    return tuple(
        rows[row][column + 4]
        for row in range(4)
        for column in range(4)
    )

def siege_to_gltf_matrix(values: Sequence[float]) -> tuple[float, ...]:
    """Convert a Siege row vector matrix to glTF column major form"""

    siege_column_matrix = transpose_matrix(values)

    converted = multiply_matrices(
        SIEGE_TO_GLTF_BASIS,
        multiply_matrices(
            siege_column_matrix,
            GLTF_TO_SIEGE_BASIS
        )
    )

    return transpose_matrix(converted)

def invert_gltf_matrix(values: Sequence[float]) -> tuple[float, ...]:
    """Invert a glTF column major matrix"""

    row_major = transpose_matrix(values)
    inverted = invert_matrix(row_major)

    return transpose_matrix(inverted)

def siege_to_gltf_vector(value: tuple[float, float, float]) -> tuple[float, float, float]:
    """Convert Siege Z-up coordinates to glTF Y-up coordinates"""

    x, y, z = value

    return x, z, -y

def _validate_mesh_part(part: MeshPartLike) -> None:
    label = f"Part {part.uid:016X}"
    vertex_count = len(part.vertices)
    if vertex_count == 0:
        raise ValueError(f"{label} has no vertices")
    if len(part.uvs) != vertex_count:
        raise ValueError(f"{label} has mismatched UVs")
    if len(part.normals) != vertex_count:
        raise ValueError(f"{label} has mismatched normals")
    if part.tangents and len(part.tangents) != vertex_count:
        raise ValueError(f"{label} has mismatched tangents")

    if bool(part.joints) != bool(part.weights):
        raise ValueError(f"{label} must contain both joints and weights")
    if part.joints and len(part.joints) != vertex_count:
        raise ValueError(f"{label} has mismatched joint indices")
    if part.weights and len(part.weights) != vertex_count:
        raise ValueError(f"{label} has mismatched skin weights")

    binding = part.binding
    if binding is None:
        return

    bone_count = len(binding.bone_ids)
    if not part.joints:
        raise ValueError(f"{label} has a binding but no joints")
    if len(binding.inverse_bind_matrices) != bone_count:
        raise ValueError(f"{label} has mismatched bone IDs and inverse bind matrices")
    if binding.joint_node_matrices and len(binding.joint_node_matrices) != bone_count:
        raise ValueError(f"{label} has mismatched joint node matrices")

    if any(joint >= bone_count for joints, weights in zip(part.joints, part.weights) for joint, weight in zip(joints, weights) if weight > 0.0):
        raise ValueError(f"{label} references a joint outside its binding table")

def _write_vertex_attributes(part: MeshPartLike, prefix: str, add_accessor) -> dict[str, int]:
    vertex_count = len(part.vertices)

    def write_floats(rows, value_type, suffix, **bounds):
        values = [value for row in rows for value in row]
        return add_accessor(
            pack_floats(values), target=ARRAY_BUFFER, component_type=FLOAT,
            count=vertex_count, value_type=value_type, name=f"{prefix}_{suffix}",
            **bounds
        )

    positions = [siege_to_gltf_vector(vertex) for vertex in part.vertices]
    attributes = {
        "POSITION": write_floats(
            positions, "VEC3", "positions",
            minimum=component_minimums(positions), maximum=component_maximums(positions)
        ),
        "NORMAL": write_floats(
            (siege_to_gltf_vector(normal) for normal in part.normals), "VEC3", "normals"
        ),
        # mesh parser flips V, convert it back for glTF
        "TEXCOORD_0": write_floats(((u, 1.0 - v) for u, v in part.uvs), "VEC2", "uvs")
    }

    if part.tangents:
        tangents = ((*siege_to_gltf_vector((x, y, z)), w) for x, y, z, w in part.tangents)
        attributes["TANGENT"] = write_floats(tangents, "VEC4", "tangents")

    if not part.joints:
        return attributes

    influence_count = len(part.joints[0])
    if influence_count not in (4, 8) or any(len(joints) != influence_count or len(weights) != influence_count for joints, weights in zip(part.joints, part.weights)):
        raise ValueError(f"Part {part.uid:016X} has unsupported skin influence counts")

    for group in range(influence_count // 4):
        start = group * 4
        joints = [joint for row in part.joints for joint in row[start:start + 4]]
        attributes[f"JOINTS_{group}"] = add_accessor(
            pack_unsigned_bytes(joints), target=ARRAY_BUFFER, component_type=UNSIGNED_BYTE,
            count=vertex_count, value_type="VEC4", name=f"{prefix}_joints_{group}"
        )
        weights = (row[start:start + 4] for row in part.weights)
        attributes[f"WEIGHTS_{group}"] = write_floats(weights, "VEC4", f"weights_{group}")
    return attributes

def _write_skin(part: MeshPartLike, prefix: str, nodes, skins, add_accessor, head_bone_ids) -> int | None:
    binding = part.binding
    if binding is None:
        return None

    inverse_binds = [siege_to_gltf_matrix(matrix) for matrix in binding.inverse_bind_matrices]
    values = [value for matrix in inverse_binds for value in matrix]
    accessor = add_accessor(
        pack_floats(values), target=None, component_type=FLOAT,
        count=len(inverse_binds), value_type="MAT4", name=f"{prefix}_inverse_bind_matrices"
    )

    # explicit joint matrices are already in glTF space
    joint_matrices = binding.joint_node_matrices
    if not joint_matrices:
        joint_matrices = [invert_gltf_matrix(matrix) for matrix in inverse_binds]

    joint_nodes = []
    for index, (bone_id, matrix) in enumerate(zip(binding.bone_ids, joint_matrices)):
        bone_hex = f"{bone_id:08X}"
        bone_name = BONE_NAMES.get(bone_id, bone_hex)
        extras = {"siegeBoneId": bone_hex}
        if bone_id in head_bone_ids:
            extras["siegeHeadDescendant"] = True

        joint_nodes.append(len(nodes))
        nodes.append({
            "name": f"{prefix}_join_{index:03d}_{bone_name}",
            "extras": extras,
            "matrix": list(matrix)
        })

    skin_index = len(skins)
    skins.append({
        "name": f"{prefix}_skin",
        "joints": joint_nodes,
        "inverseBindMatrices": accessor,
        "extras": {"siegeGeometryUid": f"{part.uid:016X}"}
    })
    return skin_index

_OPAQUE_DIFFUSE_MATERIALS = {
    # (model UID, material UID, shader UID)
    (0x510F6B21D, 0xC0B911CA, 0x4787DF5D80), # Single doorway
    (0x540CFA01B, 0xC0B911CA, 0x4787DF5D80), # Double-door hardware
    (0x540CFA01B, 0x5CEE64A224, 0x99E2E5B2), # Double-door boards
    (0x5545CBAAC, 0xC0B911CA, 0x4787DF5D80), # Window hardware
    (0x5545CBAAC, 0x5CEE64A224, 0x99E2E5B2), # Window boards
    (0x67B7BE27EC, 0x6885BA3B92, 0x3327005AD), # Noor
    (0x69837CC04E, 0x6885BA3B92, 0x3327005AD), # Noor projectile
    (0x46B1161F22, 0x671B677CF, 0x3327005AD), # Blitz
    (0x2E78D1D7D0, 0xA251826E, 0x3327005AD), # Montagne retracted
    (0x6175F5EF50, 0x60E0180952, 0xE09ED870), # Rauora single-door hardware
    (0x6175F5EF52, 0x60E0180952, 0xE09ED870), # Rauora double-door hardware
    (0x3F3936A334, 0xA251826E, 0x3327005AD), # Montagne
    (0x59A6BD7EA9, 0x5909BEFCD3, 0x3327005AD), # Grim
    (0x5E8C093B6C, 0x5E1104D61B, 0x3327005AD), # Deimos
    (0x5D048D31A9, 0x5D597E1AB1, 0x4787DF5D80), # Tubarão
}

def _build_base_material(model_uid, material_id, slot, add_texture, uses_alpha, *, paired_transition):
    color = (1.0, 1.0, 1.0, 1.0)
    if slot.solid_color is not None:
        color = siege_color_to_gltf(slot.solid_color)

    pbr = {
        "baseColorFactor": list(color),
        "metallicFactor": 0.0,
        "roughnessFactor": 0.8,
    }
    material = {
        "name": f"{model_uid:016X}_SiegeMaterial_{material_id}",
        "pbrMetallicRoughness": pbr,
        "alphaMode": "OPAQUE",
    }

    if slot.diffuse:
        pbr["baseColorTexture"] = {"index": add_texture(slot.diffuse)}
        key = (model_uid, slot.material_uid, slot.shader_uid)
        if key in _OPAQUE_DIFFUSE_MATERIALS:
            masked = False
        elif slot.shader_uid in ALPHA_MASK_SHADER_UIDS:
            masked = True
        elif slot.shader_uid in OPAQUE_SHADER_UIDS:
            masked = False
        else:
            masked = uses_alpha(slot.diffuse)

        if key == (0xAF48BFE3D, 0x16F5E86868, 0x3BD13B9E): # rotor motion blur
            material["alphaMode"] = "BLEND"
            material["doubleSided"] = True
        elif masked:
            material["alphaMode"] = "MASK"
            material["alphaCutoff"] = 0.1
            material["doubleSided"] = True

    glass = slot.shader_uid in GLASS_SHADER_UIDS
    hidden = slot.shader_uid in HIDDEN_BY_DEFAULT_SHADER_UIDS
    if glass or hidden or paired_transition:
        material["alphaMode"] = "BLEND"
        material["doubleSided"] = True
        material.pop("alphaCutoff", None)
        if hidden:
            pbr["baseColorFactor"][3] = 0.0
        elif glass:
            pbr["baseColorFactor"][3] = 0.1

    if slot.normal:
        material["normalTexture"] = {"index": add_texture(slot.normal), "scale": 1.0}
    return material

def _apply_material_approximations(material: dict, slot: MaterialTextures, extras: dict) -> None:
    if "ExperimentalLegacyABTintV1" in extras.get("siegeShaderUniforms", {}):
        material["name"] += "_Experimental_AB_Tint"
        extras["siegeMaterialApproximation"] = "Source A/B colors. Approximate legacy tint blending."

    if slot.shader_uid == 0x99E2C947 and slot.solid_color is not None:
        if slot.material_uid == 0xA8715F23D:
            material["name"] += "_Approx_Source_Color"
            extras["siegeMaterialApproximation"] = "Source header color. Approximate multiplication with the bandana texture"
        elif slot.material_uid in {0x995497897, 0x995497909, 0x99549791D}:
            material["name"] += "_Approx_Source_Color"
            extras["siegeMaterialApproximation"] = "Source A/B colors and head color. Approximate legacy tint and texture modulation."

    if (slot.material_uid, slot.shader_uid, slot.diffuse) in {
        (0x43F942F4DD, 0x3051C028, "00000043F942F4BE.png"),
        (0x684ADFEF6B, 0x55B93242F6, "000000684ADFEF83.png"),
    }:
        material["alphaMode"] = "BLEND"
        material.pop("alphaCutoff", None)
        material["doubleSided"] = True
        material["name"] += "_Approx_Hair_Coverage"
        extras["siegeHairCoverageApproximationV1"] = True
        extras["siegeMaterialApproximation"] = "Original texture alpha. Approximate hair coverage."

def _write_shader_metadata(material: dict, slot: MaterialTextures) -> None:
    extras = {}
    if slot.material_uid is not None:
        extras["siegeMaterialUid"] = f"{slot.material_uid:016X}"
    if slot.shader_uid is not None:
        extras["siegeShaderUid"] = f"{slot.shader_uid:016X}"
    if slot.shader_uniforms:
        extras["siegeShaderUniforms"] = {
            name: list(values) for name, values in slot.shader_uniforms
        }

    _apply_material_approximations(material, slot, extras)

    if slot.specular:
        extras["siegePackedMaterialTexture"] = slot.specular
    if slot.mask:
        extras["siegeMaskTexture"] = slot.mask
    if slot.detail_normals:
        extras["siegeDetailNormalTextures"] = slot.detail_normals
    if slot.shader_textures:
        extras["siegeShaderTextures"] = dict(slot.shader_textures)

    if extras:
        material["extras"] = extras

# (model UID, material UID, shader UID) -> static display alpha
_STATIC_DISPLAY_ALPHA = {
    (0x2A4CEB7295, 0x2EA41517D, 0xBE258F7C): 1.0, # IQ
    (0x2A4CEB7295, 0x53328BB931, 0xE09ED870): 0.0,
    (0x64935FA57D, 0x5CE744A9E5, 0x3327005AD): 1.0, # Thatcher
    (0x64935FA57D, 0x64C21D7647, 0xE09ED870): 0.0,
    (0x24F948E738, 0x257CB37667, None): 0.0, # Mozzie
    (0x547BC75B93, 0xBE25903E, 0xE3683314): 1.0, # Pulse
    (0x547BC75B93, 0x5842481E97, 0xE09ED870): 0.0,
    (0x64D62E744E, 0x5CE744A9E5, 0x3327005AD): 1.0, # Solid Snake
    (0x13D0BCD6A8, 0x67683E37D0, 0xE09ED870): 0.0, # Dokkaebi
    (0x67B7BE27EC, 0x694AFF0888, 0x4299B60FE3): 0.0, # Noor
    (0x67B7BE27EC, 0x6A22BCB5B9, 0x4299B60FE3): 0.0,
    (0x67B7BE27EC, 0x6A22BCB5AB, 0x4299B60FE3): 0.0,
    (0x5A363A3B24, 0x5A542B77DB, 0xE09ED870): 1.0, # Solis
    (0x532A0CC9AF, 0x542BDF6BFF, 0x1E972CCA2D): 0.0, # Thunderbird
}


_STATIC_DISPLAY_NOTES = {
    0x2A4CEB7295: (
        "IQ static display approximation: blank dark screen. "
        "Extra effect layer hidden. Live scan rendering is not reproduced."
    ),
    0x64935FA57D: (
        "Thatcher static display approximation: blank dark screen. "
        "Live scan overlay and runtime scan effects are omitted."
    ),
}

def _apply_static_display(material: dict, model_uid: int, slot: MaterialTextures) -> None:
    alpha = _STATIC_DISPLAY_ALPHA.get((model_uid, slot.material_uid, slot.shader_uid))
    if alpha is None or slot.diffuse:
        return

    pbr = material["pbrMetallicRoughness"]
    pbr["baseColorFactor"] = [0.005, 0.015, 0.03, alpha]
    pbr["metallicFactor"] = 0.0
    pbr["roughnessFactor"] = 0.3
    material["alphaMode"] = "OPAQUE" if alpha else "BLEND"
    material.pop("alphaCutoff", None)
    material["name"] += "_Approx_Static_Display"
    material.setdefault("extras", {})["siegeMaterialApproximation"] = _STATIC_DISPLAY_NOTES.get(model_uid, "Static gadget display approximation. Blank screen or hidden runtime overlay. Live readings and animations are not reproduced.")

def write_gltf(model_uid: int, parts: Iterable[MeshPartLike], output_directory: str | Path, *, diffuse: str | None = None, normal: str | None = None, specular: str | None = None, material_textures: Sequence[MaterialTextures] | None = None, skeletons=None, head_bone_ids=()) -> Path:
    """Write a multi-part glTF using external PNG textures"""

    output_directory = Path(output_directory).resolve()
    output_directory.mkdir(parents=True, exist_ok=True)

    parts, material_textures = split_atlas_lenses(tuple(parts), material_textures)

    if not parts:
        raise ValueError("Cannot write a model without mesh parts")

    name = f"{model_uid:016X}"
    gltf_path = output_directory / f"{name}.gltf"
    binary_path = output_directory / f"{name}.bin"

    binary = BinaryBuffer()
    accessors: list[dict] = []
    meshes: list[dict] = []
    nodes: list[dict] = []
    skins: list[dict] = []
    used_material_ids: set[int] = set()

    def add_accessor(raw: bytes, *, target: int | None, component_type: int, count: int, value_type: str, name: str, minimum: list[float] | None = None, maximum: list[float] | None = None) -> int:
        view = binary.add(raw, target=target, name=name)

        accessor = {
            "name": name,
            "bufferView": view,
            "byteOffset": 0,
            "componentType": component_type,
            "count": count,
            "type": value_type
        }

        if minimum is not None:
            accessor["min"] = minimum

        if maximum is not None:
            accessor["max"] = maximum

        index = len(accessors)
        accessors.append(accessor)

        return index

    for part in parts:
        _validate_mesh_part(part)

        island_groups = [
            (island.material_id, island.faces)
            for island in part.islands
            if island.faces
        ]
        if not island_groups:
            raise ValueError(f"Part {part.uid:016X} has no material islands")

        prefix = f"part_{part.uid:016X}"
        vertex_attributes = _write_vertex_attributes(part, prefix, add_accessor)

        primitives = []
        alpha_accessor = None

        for island_index, (material_id, island_faces) in enumerate(island_groups):
            if material_id < 0:
                raise ValueError(f"Part {part.uid:016X} contains negative material ID {material_id}")

            indices = [
                vertex_index
                for face in island_faces
                for vertex_index in face
            ]

            if indices and max(indices) >= len(part.vertices):
                raise ValueError(f"Part {part.uid:016X} island {island_index} contains an out of range face index")

            color_attributes = {}
            if material_textures is not None and material_id < len(material_textures) and material_textures[material_id].shader_uid == 0x0000002B188360D9:
                alpha = getattr(part, "vertex_alpha", ())

                if len(alpha) != len(part.vertices) or any(
                    not math.isfinite(value) or not 0.0 <= value <= 1.0
                    for value in alpha
                ):
                    raise ValueError(f"Eye overlay {part.uid:016X} has no valid source vertex alpha")
            
                if alpha_accessor is None:
                    colors = [
                        component
                        for value in alpha
                        for component in (1.0, 1.0, 1.0, value)
                    ]
                    alpha_accessor = add_accessor(
                        pack_floats(colors),
                        target=ARRAY_BUFFER,
                        component_type=FLOAT,
                        count=len(alpha),
                        value_type="VEC4",
                        name=f"{prefix}_eye_vertex_alpha"
                    )

                color_attributes["COLOR_0"] = alpha_accessor

            index_accessor = add_accessor(pack_unsigned_ints(indices), target=ELEMENT_ARRAY_BUFFER, component_type=UNSIGNED_INT, count=len(indices), value_type="SCALAR", name=f"{prefix}_island_{island_index}_indices")

            primitives.append(
                {
                    "attributes": {
                        **vertex_attributes,
                        **color_attributes
                    },
                    "indices": index_accessor,
                    "material": material_id,
                    "mode": TRIANGLES
                }
            )

            used_material_ids.add(material_id)
        mesh_index = len(meshes)

        meshes.append(
            {
                "name": prefix,
                "primitives": primitives
            }
        )

        skin_index = _write_skin(part, prefix, nodes, skins, add_accessor, head_bone_ids)

        mesh_node = {
            "name": prefix,
            "mesh": mesh_index
        }

        if skin_index is not None:
            mesh_node["skin"] = skin_index
        elif model_uid in {0x5545CBAAC, 0x540CFA01B}:
            # stand static wooden barricades upright about their mounting point
            half_sqrt = math.sqrt(0.5)
            mesh_node["rotation"] = [half_sqrt, 0.0, 0.0, half_sqrt]

        nodes.append(mesh_node)

    images: list[dict] = []
    textures: list[dict] = []
    texture_cache: dict[str, int] = {}

    def add_texture(filename: str) -> int:
        cached = texture_cache.get(filename)

        if cached is not None:
            return cached

        image_index = len(images)

        images.append(
            {
                "name": Path(filename).stem,
                "uri": filename
            }
        )

        texture_index = len(textures)

        textures.append(
            {
                "source": image_index,
                "sampler": 0
            }
        )

        texture_cache[filename] = texture_index

        return texture_index

    fallback_textures = MaterialTextures(
        diffuse=diffuse,
        normal=normal,
        specular=specular
    )

    def uses_alpha(filename: str) -> bool:
        path = output_directory / filename

        if not path.is_file():
            return False

        with Image.open(path) as source:
            if "A" not in source.getbands():
                return False

            minimum, _ = source.getchannel("A").getextrema()

        # Some opaque Siege maps use alpha as packed material data
        # zero values indicate genuine transparent regions
        return minimum == 0

    material_count = max(used_material_ids, default=0) + 1
    materials = []

    for material_id in range(material_count):
        if material_textures is not None and material_id < len(material_textures):
            slot_textures = material_textures[material_id]
        else:
            slot_textures = fallback_textures

        if model_uid == 0x000000156B734198 and slot_textures.material_uid == 0x00000001134CB4E0 and slot_textures.shader_uid == 0x000000003BD13B9E:
            slot_textures = replace(
                slot_textures,
                shader_uniforms=(
                    *slot_textures.shader_uniforms,
                    ("ExperimentalGlassLensV1", (0.60, 0.08, 1.5))
                )
            )

        paired_transition = (
            material_textures is not None
            and 0 < material_id < len(material_textures)
            and material_textures[material_id - 1].shader_uid in HIDDEN_BY_DEFAULT_SHADER_UIDS
        )
        material = _build_base_material(model_uid, material_id, slot_textures, add_texture, uses_alpha, paired_transition=paired_transition)
        pbr = material["pbrMetallicRoughness"]

        if slot_textures.material_uid == 0x00000001134CB4E0 and slot_textures.shader_uid == 0x000000003BD13B9E and slot_textures.diffuse == "0000000C9C9CCB4F.png":
            # Ace headlamp lens: use its low, nonzero texture alpha.
            # material-specific compatibility rule
            material["alphaMode"] = "BLEND"
            material.pop("alphaCutoff", None)
            pbr["baseColorFactor"][3] = 1.0

        _write_shader_metadata(material, slot_textures)

        _apply_static_display(material, model_uid, slot_textures)

        if model_uid == 0x532A0CC9AF:
            material.setdefault("extras", {})["siegeMaterialApproximation"] = (
                "Kona Station static approximation. The Runtime healing "
                "cross projection is hidden. Live projection and "
                "animation is not reproduced."
            )

        # Scratch decals, not the shield's transparent pane
        if model_uid in {
                0x3F3936A334, 0x2E78D1D7D0, 0x46B1161F22,
                0x60C4EE7E3B, 0x5DB5BC2B2A,
            } and (slot_textures.material_uid, slot_textures.shader_uid) == (0x6A9F01154, 0x3BD13B9E) and slot_textures.diffuse:
            material["alphaMode"] = "BLEND"
            material.pop("alphaCutoff", None)

        shield_note = None
        pane_uid = {
            0x5DB5BC2B2A: 0x62FCA06100,
            0x60C4EE7E3B: 0x2B225339E,
        }.get(model_uid)

        if pane_uid is not None:
            damage_materials = {
                0x2B225339E, 0x2B22533AA, 0x2B22533B6,
                0x2B22533C2, 0x2B22533CE,
            }

            if slot_textures.shader_uid == 0x83A0CD9B3 and slot_textures.material_uid in damage_materials:
                shield_note = (
                    "Static undamaged shield approximation. Source glass texture "
                    "format is unsupported. Neutral tinted pane. Overlapping "
                    "damage states hidden."
                )
                if slot_textures.material_uid == pane_uid:
                    pbr["baseColorFactor"] = [0.65, 0.8, 0.88, 0.18]
                    slot_textures = replace(
                        slot_textures,
                        shader_uniforms=(*slot_textures.shader_uniforms, ("ExperimentalGlassLensV1", (0.18, 0.08, 1.5)),),
                    )
                else:
                    pbr["baseColorFactor"][3] = 0.0
                    material["alphaMode"] = "BLEND"
                    material.pop("alphaCutoff", None)
                material["name"] += "_Approx_Undamaged_Shield"
            elif model_uid == 0x5DB5BC2B2A and (slot_textures.material_uid, slot_textures.shader_uid) == (0x65669876F, 0x3BD13B9E):
                pbr["baseColorFactor"][3] = 0.0
                material["alphaMode"] = "BLEND"
                material.pop("alphaCutoff", None)
                shield_note = (
                    "Static undamaged shield approximation. Impact overlay hidden."
                )

        if model_uid == 0x55DC2F2157 and slot_textures.shader_uid == 0x83A0CD9B3 and slot_textures.material_uid in {
            0x5481EA7080, 0x5481EA7081, 0x5481EA7086,
            0x5481EA7084, 0x5481EA7083, 0x5481EA7085,
        }:
            pbr["baseColorFactor"][3] = 0.0
            material["alphaMode"] = "BLEND"
            material.pop("alphaCutoff", None)
            material["name"] += "_Approx_Undamaged_Shield"
            shield_note = (
                "Static undamaged Osa shield approximation. Overlapping "
                "damage layers hidden. Runtime damage progression is not reproduced."
            )

        if model_uid == 0x5DB5BC2B2A and (slot_textures.material_uid, slot_textures.shader_uid) == (0x62FCA06100, 0x3BD13B9E):
            slot_textures = replace(
                slot_textures,
                shader_uniforms=(
                    *slot_textures.shader_uniforms,
                    ("ExperimentalGlassLensV1", (0.18, 0.08, 1.5)),
                ),
            )
            material["name"] += "_Approx_Undamaged_Shield"
            shield_note = (
                "Static undamaged Blackbeard shield approximation. "
                "Source pane tint retained. Coverage, Rougenss and IOR "
                "approximated. Overlapping damage states hidden."
            )

        if model_uid == 0x55DC2F2157 and slot_textures.shader_uid == 0x3327005AD and slot_textures.material_uid in {
            0x54469B20F4, 0x54469B20C5, 0x54469B21D4,
        }:
            slot_textures = replace(
                slot_textures,
                shader_uniforms=(
                    *slot_textures.shader_uniforms,
                    ("ExperimentalGlassLensV1", (0.22, 0.08, 1.5)),
                ),
            )
            material["name"] += "_Approx_Tinted_Shield_Glass"
            shield_note = (
                "Osa shield glass approximation. Source green tint retained. "
                "Coverage, Roughness and IOR approximated. Runtime damage "
                "progression is not reproduced."
            )

        if model_uid == 0x573DBEB520 and (slot_textures.material_uid, slot_textures.shader_uid) == (0x557222D2AA, 0xE09ED870):
            pbr["baseColorFactor"][3] = 0.0
            material["alphaMode"] = "BLEND"
            material.pop("alphaCutoff", None)
            material["name"] += "_Approx_Inactive_Overlay"
            material.setdefault("extras", {})["siegeMaterialApproximation"] = (
                "Static inactive Razorbloom approximation. Runtime activation "
                "overlay hidden. Activation lighting is not reproduced."
            )

        if (model_uid, slot_textures.material_uid, slot_textures.shader_uid) in {
            (0x233A0B4871, 0x8ABBDCFD2, 0x3327005AD),
            (0x2FF67F4533, 0x8ABBDCFBF, 0x3327005AD),
        }:
            material["name"] += "_Approx_Bolt_UV"
            material.setdefault("extras", {})["siegeMaterialApproximation"] = (
                "Capitao bolt UV approximation. Source textures use a vertical "
                "UV flip. The exact game UV transformation has not been recovered."
            )

        apply_optical_preview(material, slot_textures)

        if model_uid == 0x24F948E738 and slot_textures.shader_uid is None and slot_textures.material_uid in {0x36D604A6, 0x257CB37667}:
            note = (
                "Source sight definitions unavailable. Neutral thin-glass lens "
                "approximation."
            )
            material["name"] += "_Approx_Sight"
            material.setdefault("extras", {})["siegeMaterialApproximation"] = note
            if slot_textures.material_uid == 0x36D604A6:
                material["extras"]["siegeOpticalApproximation"] = note

        if shield_note is not None:
            material.setdefault("extras", {})["siegeMaterialApproximation"] = shield_note
            if "siegeGlassPreviewV1" in material["extras"]:
                material["extras"]["siegeOpticalApproximation"] = shield_note

        if "siegeGlassPreviewV1" in material.get("extras", {}) and slot_textures.diffuse:
            # Atlas alpha may encode selectors rather than  transparency
            # Preserve the original texture for the other material slots
            source_path = output_directory / slot_textures.diffuse
            glass_filename = source_path.stem + "_glass_rgb.png"

            if glass_filename not in texture_cache:
                with Image.open(source_path) as source_image:
                    source_image.convert("RGB").save(output_directory / glass_filename)

            material["extras"]["siegeGlassSourceBaseColor"] = slot_textures.diffuse
            pbr["baseColorTexture"] = {
                "index": add_texture(glass_filename)
            }
        materials.append(material)

    document = {
        "asset": {
            "version": "2.0",
            "generator": "R6 Forge Extractor"
        },
        "scene": 0,
        "scenes": [
            {
                "name": name,
                "nodes": list(range(len(nodes)))
            }
        ],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "buffers" : [
            {
                "uri": binary_path.name,
                "byteLength": len(binary.data)
            }
        ],
        "bufferViews": binary.views,
        "accessors": accessors
    }

    if skins:
        document["skins"] = skins

    if images:
        document["samplers"] = [
            {
                "magFilter": 9729,
                "minFilter": 9987,
                "wrapS": 10497,
                "wrapT": 10497
            }
        ]
        document["images"] = images
        document["textures"] = textures

    from src.skeleton import apply_skeleton_hierachy

    apply_skeleton_hierachy(document, skeletons)

    if model_uid == 0x614D71100B:
        scene = document["scenes"][document["scene"]]
        half_sqrt = math.sqrt(0.5)
        root = {
            "name": "Wall mounting frame",
            "rotation": [half_sqrt, 0.0, 0.0, half_sqrt],
            "children": scene["nodes"],
        }
        scene["nodes"] = [len(nodes)]
        nodes.append(root)

    binary_path.write_bytes(binary.data)

    gltf_path.write_text(
        json.dumps(
            document,
            indent=2,
            ensure_ascii=False
        ) + "\n",
        encoding="utf-8"
    )

    return gltf_path