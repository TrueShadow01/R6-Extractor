"""Minimal dependency free glTF 2.0 writer for Siege models"""

# Yo Nyx, wait for blake to the glass shader done and implement it into here tho and help him to not go insane lol - Shadow

from __future__ import annotations

from PIL import Image

import json
import math
import struct
import zlib
from dataclasses import dataclass, field
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
}

ALPHA_MASK_SHADER_UIDS = {
    0x000000003051C028, # hair and eyelashes
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

def write_gltf(model_uid: int, parts: Iterable[MeshPartLike], output_directory: str | Path, *, diffuse: str | None = None, normal: str | None = None, specular: str | None = None, material_textures: Sequence[MaterialTextures] | None = None) -> Path:
    """Write a multi-part glTF using external PNG textures"""

    output_directory = Path(output_directory).resolve()
    output_directory.mkdir(parents=True, exist_ok=True)

    parts = tuple(parts)

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
        if not part.vertices:
            raise ValueError(f"Part {part.uid:016X} has no vertices")

        if len(part.uvs) != len(part.vertices):
            raise ValueError(f"Part {part.uid:016X} has mismatched UVs")

        if len(part.normals) != len(part.vertices):
            raise ValueError(f"Part {part.uid:016X} has mismatched normals")

        if part.tangents and len(part.tangents) != len(part.vertices):
            raise ValueError(f"Part {part.uid:016X} has mismatched tangents")

        if bool(part.joints) != bool(part.weights):
            raise ValueError(f"Part {part.uid:016X} must contain both joints and weights")

        if part.joints and len(part.joints) != len(part.vertices):
            raise ValueError(f"Part {part.uid:016X} has mismatched joint indices")

        if part.weights and len(part.weights) != len(part.vertices):
            raise ValueError(f"Part {part.uid:016X} has mismatched skin weights")

        binding = part.binding

        if binding is not None:
            if not part.joints:
                raise ValueError(f"Part {part.uid:016X} has a binding but no joints")

            if len(binding.bone_ids) != len(binding.inverse_bind_matrices):
                raise ValueError(f"Part {part.uid:016X} has mismatched bone IDs and inverse bind matrices")

            if binding.joint_node_matrices and len(binding.joint_node_matrices) != len(binding.bone_ids):
                raise ValueError(f"Part {part.uid:016X} has mismatched joint node matrices")

            used_joints = [
                joint
                for vertex_joints, vertex_weights in zip(part.joints, part.weights)
                for joint, weight in zip(vertex_joints, vertex_weights)
                if weight > 0.0
            ]

            if used_joints and max(used_joints) >= len(binding.bone_ids):
                raise ValueError(f"Part {part.uid:016X} references a joint outside its binding table")

        converted_vertices = [
            siege_to_gltf_vector(vertex)
            for vertex in part.vertices
        ]

        converted_normals = [
            siege_to_gltf_vector(normal)
            for normal in part.normals
        ]

        converted_tangents = [
            (
                *siege_to_gltf_vector(
                    (
                        tangent[0],
                        tangent[1],
                        tangent[2]
                    )
                ),
                tangent[3]
            )
            for tangent in part.tangents
        ]

        positions = [
            component
            for vertex in converted_vertices
            for component in vertex
        ]

        normals = [
            component
            for normal_value in converted_normals
            for component in normal_value
        ]

        tangents = [
            component
            for tangent in converted_tangents
            for component in tangent
        ]

        joint_values = [
            joint
            for vertex_joints in part.joints
            for joint in vertex_joints
        ]

        weight_values = [
            weight
            for vertex_weights in part.weights
            for weight in vertex_weights
        ]

        # The mesh parser flips Siege UVs vertically.
        # Convert them for glTF's upper-left texture origin
        texture_coordinates = [
            component
            for u, v in part.uvs
            for component in (u, 1.0 - v)
        ]

        island_groups = [
            (
                island.material_id,
                island.faces
            )
            for island in part.islands
            if island.faces
        ]

        if not island_groups:
            raise ValueError(f"Part {part.uid:016X} has no material islands")

        prefix = f"part_{part.uid:016X}"

        position_accessor = add_accessor(pack_floats(positions), target=ARRAY_BUFFER, component_type=FLOAT, count=len(part.vertices), value_type="VEC3", name=f"{prefix}_positions", minimum=component_minimums(converted_vertices), maximum=component_maximums(converted_vertices))
        normal_accessor = add_accessor(pack_floats(normals), target=ARRAY_BUFFER, component_type=FLOAT, count=len(part.normals), value_type="VEC3", name=f"{prefix}_normals")
        uv_accessor = add_accessor(pack_floats(texture_coordinates), target=ARRAY_BUFFER, component_type=FLOAT, count=len(part.uvs), value_type="VEC2", name=f"{prefix}_uvs")
        tangent_accessor = None
        joint_accessor = None
        weight_accessor = None

        if converted_tangents:
            tangent_accessor = add_accessor(pack_floats(tangents), target=ARRAY_BUFFER, component_type=FLOAT, count=len(converted_tangents), value_type="VEC4", name=f"{prefix}_tangents")

        if joint_values:
            joint_accessor = add_accessor(pack_unsigned_bytes(joint_values), target=ARRAY_BUFFER, component_type=UNSIGNED_BYTE, count=len(part.joints), value_type="VEC4", name=f"{prefix}_joints")
            weight_accessor = add_accessor(pack_floats(weight_values), target=ARRAY_BUFFER, component_type=FLOAT, count=len(part.weights), value_type="VEC4", name=f"{prefix}_weights")

        primitives = []

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

            index_accessor = add_accessor(pack_unsigned_ints(indices), target=ELEMENT_ARRAY_BUFFER, component_type=UNSIGNED_INT, count=len(indices), value_type="SCALAR", name=f"{prefix}_island_{island_index}_indices")

            primitives.append(
                {
                    "attributes": {
                        "POSITION": position_accessor,
                        "NORMAL": normal_accessor,
                        "TEXCOORD_0": uv_accessor,
                        **(
                            {"TANGENT": tangent_accessor}
                            if tangent_accessor is not None
                            else {}
                        ),
                        **(
                            {
                                "JOINTS_0": joint_accessor,
                                "WEIGHTS_0": weight_accessor
                            }
                            if joint_accessor is not None and weight_accessor is not None
                            else {}
                        )
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

        skin_index = None

        if binding is not None:
            converted_inverse_bind_matrices = tuple(
                siege_to_gltf_matrix(matrix)
                for matrix in binding.inverse_bind_matrices
            )

            inverse_bind_values = [
                value
                for matrix in converted_inverse_bind_matrices
                for value in matrix
            ]

            inverse_bind_accessor = add_accessor(pack_floats(inverse_bind_values), target=None, component_type=FLOAT, count=len(converted_inverse_bind_matrices), value_type="MAT4", name=f"{prefix}_inverse_bind_matrices")

            joint_node_matrices = (
                tuple(
                    tuple(matrix)
                    for matrix in binding.joint_node_matrices
                )
                if binding.joint_node_matrices
                else tuple(
                    invert_gltf_matrix(inverse_bind_matrix)
                    for inverse_bind_matrix in converted_inverse_bind_matrices
                )
            )

            joint_nodes = []

            for bone_index, (bone_id, joint_node_matrix) in enumerate(zip(binding.bone_ids, joint_node_matrices)):
                joint_nodes.append(len(nodes))

                nodes.append(
                    {
                        "name": f"{prefix}_join_{bone_index:03d}_{BONE_NAMES.get(bone_id, f'{bone_id:08X}')}",
                        "extras": {'siegeBoneId': f"{bone_id:08X}"},
                        "matrix": list(joint_node_matrix)
                    }
                )

            skin_index = len(skins)

            skins.append(
                {
                    "name": f"{prefix}_skin",
                    "joints": joint_nodes,
                    "inverseBindMatrices": inverse_bind_accessor
                }
            )

        mesh_node = {
            "name": prefix,
            "mesh": mesh_index
        }

        if skin_index is not None:
            mesh_node["skin"] = skin_index

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

        pbr = {
            "baseColorFactor": list(
                siege_color_to_gltf(slot_textures.solid_color)
                if slot_textures.solid_color is not None
                else (
                    1.0,
                    1.0,
                    1.0,
                    1.0
                )
            ),
            "metallicFactor": 0.0,
            "roughnessFactor": 0.8
        }

        material = {
            "name": f"{model_uid:016X}_SiegeMaterial_{material_id}",
            "pbrMetallicRoughness": pbr,
            "alphaMode": "OPAQUE"
        }

        if slot_textures.diffuse:
            pbr["baseColorTexture"] = {
                "index": add_texture(slot_textures.diffuse)
            }

            if slot_textures.shader_uid in ALPHA_MASK_SHADER_UIDS:
                alpha_mask = True
            elif slot_textures.shader_uid in OPAQUE_SHADER_UIDS:
                alpha_mask = False
            else:
                alpha_mask = uses_alpha(slot_textures.diffuse)

            if alpha_mask:
                material["alphaMode"] = "MASK"
                material["alphaCutoff"] = 0.1
                material["doubleSided"] = True

        if slot_textures.shader_uid in GLASS_SHADER_UIDS:
            material["alphaMode"] = "BLEND"
            material["doubleSided"] = True
            material.pop("alphaCutoff", None)
            pbr["baseColorFactor"][3] = 0.1

        if slot_textures.shader_uid in HIDDEN_BY_DEFAULT_SHADER_UIDS:
            material["alphaMode"] = "BLEND"
            material["doubleSided"] = True
            material.pop("alphaCutoff", None)
            pbr["baseColorFactor"][3] = 0.0

        paired_transition_base = (
            material_textures is not None
            and 0 < material_id < len(material_textures)
            and material_textures[material_id - 1].shader_uid in HIDDEN_BY_DEFAULT_SHADER_UIDS
        )

        if paired_transition_base:
            material["alphaMode"] = "BLEND"
            material["doubleSided"] = True
            material.pop("alphaCutoff", None)

        if slot_textures.normal:
            material["normalTexture"] = {
                "index": add_texture(slot_textures.normal),
                "scale": 1.0
            }

        if slot_textures.material_uid == 0x00000001134CB4E0 and slot_textures.shader_uid == 0x000000003BD13B9E and slot_textures.diffuse == "0000000C9C9CCB4F.png":
            # Ace headlamp lens: use its low, nonzero texture alpha.
            # material-specific compatibility rule
            material["alphaMode"] = "BLEND"
            material.pop("alphaCutoff", None)
            pbr["baseColorFactor"][3] = 1.0

        extras = {}

        if slot_textures.material_uid is not None:
            extras["siegeMaterialUid"] = f"{slot_textures.material_uid:016X}"

        if slot_textures.shader_uid is not None:
            extras["siegeShaderUid"] = f"{slot_textures.shader_uid:016X}"

        if slot_textures.shader_uniforms:
            extras["siegeShaderUniforms"] = {
                name: list(values)
                for name, values in slot_textures.shader_uniforms
            }

        if slot_textures.specular:
            extras["siegePackedMaterialTexture"] = slot_textures.specular

        if slot_textures.mask:
            extras["siegeMaskTexture"] = slot_textures.mask

        if slot_textures.detail_normals:
            extras["siegeDetailNormalTextures"] = slot_textures.detail_normals

        if slot_textures.shader_textures:
            extras["siegeShaderTextures"] = {
                binding: filename
                for binding, filename in slot_textures.shader_textures
            }

        if extras:
            material["extras"] = extras

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