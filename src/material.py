"""Resolve Siege material slots to compiled texture assets"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Collection, Iterable

from src.metadata import (
    FileMetadata,
    InvalidFileMetadata,
    parse_file_metadata
)

CURRENT_MATERIAL = 0x9BFBCAA8
CURRENT_TEXTURE_MAP_SPEC = 0x7C7D57AE
CURRENT_TEXTURE_MAP = 0x3C7E34FD
CURRENT_MESH = 0xF5C0AFD3

RECOGNIZED_TYPES = {
    CURRENT_MATERIAL,
    CURRENT_TEXTURE_MAP_SPEC,
    CURRENT_TEXTURE_MAP,
    CURRENT_MESH
}

DIFFUSE_ROLE = 0
NORMAL_ROLE = 1
SPECULAR_ROLE = 2
MASK_ROLE = 7

@dataclass(frozen=True)
class NestedEntry:
    offset: int
    end: int
    metadata: FileMetadata

    @property
    def data_offset(self) -> int:
        return self.offset + self.metadata.data_offset

@dataclass(frozen=True)
class MaterialTextureSet:
    diffuse_uids: tuple[int, ...] = ()
    normal_uids: tuple[int, ...] = ()
    specular_uids: tuple[int, ...] = ()
    mask_uids: tuple[int, ...] = ()

def scan_nested_entries(payload: bytes) -> tuple[NestedEntry, ...]:
    """Locate relevant FileMetadata records inside a model package"""

    candidates: list[tuple[int, FileMetadata]] = []

    for offset in range(max(0, len(payload) - 19)):
        name_length, container_type = struct.unpack_from("<HH", payload, offset)

        if container_type != 2 or name_length > 4096:
            continue

        header_size = 20 + name_length

        if offset + header_size > len(payload):
            continue

        file_type_offset = offset + 8 + name_length
        file_type = struct.unpack_from("<I", payload, file_type_offset)[0]

        if file_type not in RECOGNIZED_TYPES:
            continue

        try:
            metadata = parse_file_metadata(payload[offset:offset + header_size])
        except InvalidFileMetadata:
            continue

        candidates.append((offset, metadata))

    entries = []

    for index, (offset, metadata) in enumerate(candidates):
        if index + 1 < len(candidates):
            end = candidates[index + 1][0]
        else:
            end = len(payload)

        entries.append(
            NestedEntry(
                offset=offset,
                end=end,
                metadata=metadata
            )
        )

    return tuple(entries)

def referenced_uids(
    blob: bytes,
    candidates: Collection[int]
) -> tuple[int, ...]:
    """Return candidate UIDs in their serialized order"""

    matches = []

    for uid in candidates:
        position = blob.find(struct.pack("<Q", uid))

        if position >= 0:
            matches.append((position, uid))

    matches.sort()

    return tuple(uid for _, uid in matches)

def embedded_texture_uids(payload: bytes) -> tuple[int, ...]:
    """Return compiled texture UIDs stored directly in TextureMap records"""

    found = []
    seen = set()

    for entry in scan_nested_entries(payload):
        if entry.metadata.file_type != CURRENT_TEXTURE_MAP:
            continue

        # Current TextureMap records contain five resolution-tiers
        # UID slots beginning 105 bytes into their data
        first_uid = entry.data_offset + 105
        end = first_uid + 40

        if end > entry.end:
            continue

        for offset in range(first_uid, end, 8):
            uid = struct.unpack_from("<Q", payload, offset)[0]

            if uid and uid not in seen:
                seen.add(uid)
                found.append(uid)

    return tuple(found)

def read_texture_map_spec(payload: bytes, entry: NestedEntry) -> tuple[int, int] | None:
    """Return the texture role and referenced TextureMap UID"""

    start = entry.data_offset

    if start + 19 > entry.end:
        return None

    magic = struct.unpack_from("<I", payload, start)[0]

    if magic != CURRENT_TEXTURE_MAP_SPEC:
        return None

    texture_role = struct.unpack_from("<I", payload, start + 4)[0]
    texture_map_uid = struct.unpack_from("<Q", payload, start + 11)[0]

    return texture_role, texture_map_uid

def resolve_material_texture_sets(payload: bytes, texture_uids: Collection[int], geometry_uids: Iterable[int]) -> tuple[tuple[MaterialTextureSet, ...], ...]:
    """Resolve local mesh material slots for each geometry part"""

    geometry_uids = tuple(geometry_uids)
    entries = scan_nested_entries(payload)

    if not entries:
        return tuple(() for _ in geometry_uids)

    material_entries = {
        entry.metadata.uid: entry
        for entry in entries
        if entry.metadata.file_type == CURRENT_MATERIAL
    }

    spec_entries = {
        entry.metadata.uid: entry
        for entry in entries
        if entry.metadata.file_type == CURRENT_TEXTURE_MAP_SPEC
    }

    texture_map_entries = {
        entry.metadata.uid: entry
        for entry in entries
        if entry.metadata.file_type == CURRENT_TEXTURE_MAP
    }

    mesh_entries = [
        entry
        for entry in entries
        if entry.metadata.file_type == CURRENT_MESH
    ]

    textured_materials: dict[int, MaterialTextureSet] = {}

    for material_uid, material_entry in material_entries.items():
        material_blob = payload[material_entry.offset:material_entry.end]

        roles: dict[int, tuple[int, ...]] = {}

        for spec_uid in referenced_uids(material_blob, spec_entries.keys()):
            spec = read_texture_map_spec(payload, spec_entries[spec_uid])

            if spec is None:
                continue

            texture_role, texture_map_uid = spec

            # The first selector for a role is the base material map
            # Later selectors with the same role are detail/shared maps
            if texture_role in roles:
                continue

            texture_map_entry = texture_map_entries.get(texture_map_uid)

            if texture_map_entry is None:
                continue

            texture_map_blob = payload[texture_map_entry.offset:texture_map_entry.end]

            compiled_uids = referenced_uids(texture_map_blob, texture_uids)

            if compiled_uids:
                roles[texture_role] = compiled_uids

        if roles:
            textured_materials[material_uid] = MaterialTextureSet(
                diffuse_uids=roles.get(DIFFUSE_ROLE, ()),
                normal_uids=roles.get(NORMAL_ROLE, ()),
                specular_uids=roles.get(SPECULAR_ROLE, ()),
                mask_uids=roles.get(MASK_ROLE, ())
            )

    # The model header contains adjacent pairs:
    #
    # base mesh material UID -> texture-bearing override UID
    header = payload[:entries[0].offset]
    material_overrides: dict[int, int] = {}

    for base_uid in material_entries:
        needle = struct.pack("<Q", base_uid)
        position = 0

        while True:
            position = header.find(needle, position)

            if position < 0:
                break

            if position + 16 <= len(header):
                override_uid = struct.unpack_from("<Q", header, position + 8)[0]

                if override_uid in textured_materials:
                    material_overrides[base_uid] = override_uid
                    break

            position += 1

    part_materials = []

    for geometry_uid in geometry_uids:
        geometry_reference = struct.pack("<Q", geometry_uid)

        mesh_entry = next(
            (
                entry
                for entry in mesh_entries
                if geometry_reference in payload[entry.offset:entry.end]
            ),
            None
        )


        if mesh_entry is None:
            part_materials.append(())
            continue

        mesh_blob = payload[mesh_entry.offset:mesh_entry.end]

        base_material_uids = referenced_uids(mesh_blob, material_entries.keys())

        part_materials.append(
            tuple(
                textured_materials.get(material_overrides.get(base_uid, base_uid), MaterialTextureSet())
                for base_uid in base_material_uids
            )
        )

    return tuple(part_materials)