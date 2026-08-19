"""Minimal dependency free glTF 2.0 writer for Siege models"""

from __future__ import annotations

from PIL import Image

import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol, Sequence

ARRAY_BUFFER = 3492
ELEMENT_ARRAY_BUFFER = 3493

FLOAT = 5126
UNSIGNED_INT = 5125

TRIANGLES = 4

# Confirmed Siege shader transparency behavior
# Unknown shaders still use the image alpha fallback
OPAQUE_SHADER_UIDS = {
    0x0000001397A32F38, # solid cosmetic mask
    0x000000557005948D, # eye shader
}

ALPHA_MASK_SHADER_UIDS = {
    0x000000003051C028, # hair and eyelashes
}

class MeshPartLike(Protocol):
    uid: int
    vertices: Sequence[tuple[float, float, float]]
    uvs: Sequence[tuple[float, float]]
    normals: Sequence[tuple[float, float, float]]
    islands: Sequence
    tangents: Sequence[tuple[float, float, float, float]]

@dataclass(frozen=True)
class MaterialTextures:
    diffuse: str | None = None
    normal: str | None = None
    specular: str | None = None
    mask: str | None = None
    detail_normals: tuple[str, ...] = ()
    shader_textures: tuple[tuple[str, str], ...] = ()
    shader_uid: int | None = None
    shader_uniforms: tuple[tuple[str, tuple[float, ...]], ...] = ()

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

        self.views.append(
            {
                "name": name,
                "buffer": 0,
                "byteOffset": offset,
                "byteLength": len(raw),
                "target": target
            }
        )

        return index

def pack_floats(values: Sequence[float]) -> bytes:
    if not values:
        return b""

    if not all(math.isfinite(value) for value in values):
        raise ValueError("glTF attributes contain NaN or infinity")

    return struct.pack(f"<{len(values)}f", *values)

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
    used_material_ids: set[int] = set()
    used_extensions: set[str] = set()

    def add_accessor(raw: bytes, *, target: int, component_type: int, count: int, value_type: str, name: str, minimum: list[float] | None = None, maximum: list[float] | None = None) -> int:
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

        if converted_tangents:
            tangent_accessor = add_accessor(pack_floats(tangents), target=ARRAY_BUFFER, component_type=FLOAT, count=len(converted_tangents), value_type="VEC4", name=f"{prefix}_tangents")

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

        nodes.append(
            {
                "name": prefix,
                "mesh": mesh_index
            }
        )

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
            "baseColorFactor": [
                1.0,
                1.0,
                1.0,
                1.0
            ],
            "metallicFactor": 0.0,
            "roughnessFactor": 0.8
        }

        material = {
            "name": f"SiegeMaterial_{material_id}",
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

        if slot_textures.normal:
            material["normalTexture"] = {
                "index": add_texture(slot_textures.normal),
                "scale": 1.0
            }

        extras = {}

        if slot_textures.shader_uid is not None:
            extras["siegeShaderUid"] = f"{slot_textures.shader_uid:016X}"

        if slot_textures.shader_uniforms:
            extras["siegeShaderUniforms"] = {
                name: list(values)
                for name, values in slot_textures.shader_uniforms
            }

        if slot_textures.specular:
            material["extensions"] = {
                "KHR_materials_specular": {
                    "specularColorFactor": [0.4, 0.4, 0.4],
                    "specularColorTexture": {
                        "index": add_texture(slot_textures.specular)
                    }
                }
            }

            used_extensions.add("KHR_materials_specular")

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

    if used_extensions:
        document["extensionsUsed"] = sorted(used_extensions)

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