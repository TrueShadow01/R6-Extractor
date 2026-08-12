"""Minimal dependency free glTF 2.0 writer for Siege models"""

from __future__ import annotations

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

class MeshPartLike(Protocol):
    uid: int
    vertices: Sequence[tuple[float, float, float]]
    uvs: Sequence[tuple[float, float]]
    normals: Sequence[tuple[float, float, float]]
    faces: Sequence[tuple[int, int, int]]

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

def write_gltf(model_uid: int, parts: Iterable[MeshPartLike], output_directory: str | Path, *, diffuse: str | None = None, normal: str | None=None, specular: str | None=None) -> Path:
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

        converted_vertices = [
            siege_to_gltf_vector(vertex)
            for vertex in part.vertices
        ]

        converted_normals = [
            siege_to_gltf_vector(normal)
            for normal in part.normals
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

        # read_mesh converts Siege UVs for OBJ's convention.
        # Convert them back for glTF's upper-left texture origin
        texture_coordinates = [
            component
            for u, v in part.uvs
            for component in (u, 1.0 - v)
        ]

        indices = [
            index
            for face in part.faces
            for index in face
        ]

        if indices and max(indices) >= len(part.vertices):
            raise ValueError(f"Part {part.uid:016X} contains an out of range face index")

        prefix = f"part_{part.uid:016X}"

        position_accessor = add_accessor(pack_floats(positions), target=ARRAY_BUFFER, component_type=FLOAT, count=len(part.vertices), value_type="VEC3", name=f"{prefix}_positions", minimum=component_minimums(converted_vertices), maximum=component_maximums(converted_vertices))
        normal_accessor = add_accessor(pack_floats(normals), target=ARRAY_BUFFER, component_type=FLOAT, count=len(part.normals), value_type="VEC3", name=f"{prefix}_normals")
        uv_accessor = add_accessor(pack_floats(texture_coordinates), target=ARRAY_BUFFER, component_type=FLOAT, count=len(part.uvs), value_type="VEC2", name=f"{prefix}_uvs")
        index_accessor = add_accessor(pack_unsigned_ints(indices), target=ELEMENT_ARRAY_BUFFER, component_type=UNSIGNED_INT, count=len(indices), value_type="SCALAR", name=f"{prefix}_indices")

        mesh_index = len(meshes)

        meshes.append(
            {
                "name": prefix,
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": position_accessor,
                            "NORMAL": normal_accessor,
                            "TEXCOORD_0": uv_accessor
                        },
                        "indices": index_accessor,
                        "material": 0,
                        "mode": TRIANGLES
                    }
                ]
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

    def add_texture(filename: str) -> int:
        image_index= len(images)

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

        return texture_index

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
        "name": "SiegeMaterial",
        "pbrMetallicRoughness": pbr,
        "alphaMode": "OPAQUE"
    }

    if diffuse:
        pbr["baseColorTexture"] = {
            "index": add_texture(diffuse)
        }

    if normal:
        material["normalTexture"] = {
            "index": add_texture(normal),
            "scale": 1.0
        }

    if specular:
        # Preserve the relationship without guessing its packed
        # channel layout, not connected to PBR yet
        material["extras"] = {
            "siegeSpecularTexture": specular
        }

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
        "materials": [material],
        "buffers" : [
            {
                "uri": binary_path.name,
                "byteLength": len(binary.data)
            }
        ],
        "bufferViews": binary.views,
        "accessors": accessors
    }

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