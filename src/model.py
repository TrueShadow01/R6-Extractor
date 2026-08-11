"""Composite model resolution and bounded-memory OBJ export"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from PIL import Image

from src.gltf import write_gltf
from src.index import (
    AssetIndex,
    AssetRecord
)
from src.mesh import read_mesh
from src.parser import (
    map_archive,
    read_container
)
from src.texture import save_png

MESH = 0x415D9568
COMPILED_MESH_OBJ = 0xABEB2DFB

TEXTURE_TYPES = {
    0x13237FE9, # CompiledTextureMap
    0x9F492D22, # UltraResTexMap
    0x3876CCDF, # FutureResTexMap
    0x59CE4D13, # HiResTexMap
    0xF9C80707, # MedResTexMap
    0xD7B5C478, # LowResTexMap
}

@dataclass(frozen=True)
class MeshPart:
    uid: int
    vertices: list[tuple[float, float, float]]
    uvs: list[tuple[float, float]]
    normals: list[tuple[float, float, float]]
    faces: list[tuple[int, int, int]]

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
    obj_path: Path
    mtl_path: Path
    gltf_path: Path

def load_asset_payload(record: AssetRecord) -> bytes:
    """Load one asset without reading its complete archive"""

    with map_archive(record.archive) as data:
        return read_container(data, record.container_offset)

def resolve_texture_uids(model_uid: int, children: Mapping[int, Iterable[int]], index: AssetIndex) -> tuple[int, ...]:
    """Walk the dependency graph and collect indexed texture assets"""

    seen: set[int] = set()
    textures: set[int] = set()
    queue = deque([model_uid])

    while queue:
        uid = queue.popleft()

        if uid in seen:
            continue

        seen.add(uid)

        record = index.primary(uid)

        if (record is not None and record.file_type in TEXTURE_TYPES):
            textures.add(uid)

        queue.extend(children.get(uid, ()))

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

def decode_mesh_parts(records: Iterable[AssetRecord]) -> tuple[MeshPart, ...]:
    parts: list[MeshPart] = []

    for record in records:
        payload = load_asset_payload(record)

        (
            vertices,
            uvs,
            normals,
            faces
        ) = read_mesh(payload)

        if len(uvs) != len(vertices):
            raise ValueError(f"Geometry {record.uid:016X} has {len(vertices)} vertices but {len(uvs)} UV coordinates")

        if len(normals) != len(vertices):
            raise ValueError(f"Geometry {record.uid:016X} has {len(vertices)} vertices but {len(normals)} normals")

        parts.append(MeshPart(uid=record.uid, vertices=vertices, uvs=uvs, normals=normals, faces=faces))

    return tuple(parts)

def is_blank_texture(path: Path) -> bool:
    with Image.open(path) as source:
        preview = source.convert("RGB").resize((16, 16))
        extrema= preview.getextrema()

    return all(maximum < 8 for _, maximum in extrema)

def decode_model_textures(model_uid: int, children: Mapping[int, Iterable[int]], index: AssetIndex, output_directory: Path) -> tuple[list[tuple[int, int, str]], str | None, str | None, str | None]:
    decoded: list[tuple[int, int, str]] = []

    for texture_uid in resolve_texture_uids(model_uid, children, index):
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

def write_composite_obj(model_uid: int, parts: Iterable[MeshPart], output_directory: Path, diffuse: str | None, normal: str | None) -> tuple[Path, Path, int, int, int]:
    name = f"{model_uid:016X}"
    obj_path = output_directory / f"{name}.obj"
    mtl_path = output_directory / f"{name}.mtl"

    parts = tuple(parts)
    vertex_count = 0
    triangle_count = 0

    with obj_path.open("w", encoding="utf-8", newline="\n") as output:
        output.write(f"mtllib {name}.mtl\n")

        vertex_offset = 0

        for part in parts:
            part_name = f"part_{part.uid:016X}"

            output.write(f"o {part_name}\n")
            output.write(f"g {part_name}\n")

            for x, y, z in part.vertices:
                output.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")

            for u, v in part.uvs:
                output.write(f"vt {u:.6f} {v:.6f}\n")

            for nx, ny, nz in part.normals:
                output.write(f"vn {nx:.6f} {ny:.6f} {nz:.6f}\n")

            output.write("usemtl material_0\n")

            for a, b, c in part.faces:
                a += vertex_offset + 1
                b += vertex_offset + 1
                c += vertex_offset + 1

                output.write(f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}\n")

            vertex_offset += len(part.vertices)
            vertex_count += len(part.vertices)
            triangle_count += len(part.faces)

    with mtl_path.open("w", encoding="utf-8", newline="\n") as output:
        output.write("newmtl material_0\n")
        output.write("Kd 1.0 1.0 1.0\n")

        if diffuse:
            output.write(f"map_Kd {diffuse}\n")

        if normal:
            output.write(f"map_Bump {normal}\n")

    return (
        obj_path,
        mtl_path,
        len(parts),
        vertex_count,
        triangle_count
    )

def export_model(model_uid: int, children: Mapping[int, Iterable[int]], index: AssetIndex, output_directory: str | Path) -> ModelExportResult:
    """Export every geometry child and linked decodable texture"""

    output_directory = Path(output_directory).resolve()
    output_directory.mkdir(parents=True, exist_ok=True)

    geometry_records = resolve_geometry_records(model_uid, children, index)

    parts = decode_mesh_parts(geometry_records)

    (
        decoded_textures,
        diffuse,
        normal,
        specular
    ) = decode_model_textures(model_uid, children, index, output_directory)

    (
        obj_path,
        mtl_path,
        part_count,
        vertex_count,
        triangle_count
    ) = write_composite_obj(model_uid, parts, output_directory, diffuse, normal)

    gltf_path = write_gltf(
        model_uid,
        parts,
        output_directory,
        diffuse=diffuse,
        normal=normal,
        specular=specular
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
        obj_path=obj_path,
        mtl_path=mtl_path,
        gltf_path=gltf_path
    )