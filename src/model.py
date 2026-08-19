"""Composite model resolution and export"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable, Mapping

from PIL import Image

from src.gltf import write_gltf, MaterialTextures
from src.index import (
    AssetIndex,
    AssetRecord
)
from src.material import (
    MaterialTextureSet,
    NORMAL_ROLE,
    embedded_texture_uids,
    resolve_material_texture_sets
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

def decode_mesh_parts(records: Iterable[AssetRecord]) -> tuple[MeshPart, ...]:
    parts: list[MeshPart] = []

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
                weights=weights
            )
        )

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

    parts = decode_mesh_parts(geometry_records)

    (
        decoded_textures,
        diffuse,
        normal,
        specular
    ) = decode_model_textures(model_uid, children, index, output_directory)

    export_parts = parts
    material_textures: tuple[MaterialTextures, ...] = ()
    model_record = index.primary(model_uid)

    if model_record is not None:
        part_texture_sets = resolve_material_texture_sets(load_asset_payload(model_record), resolve_texture_uids(model_uid, children, index), (record.uid for record in geometry_records))

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