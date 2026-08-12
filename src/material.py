"""Resolve Siege material slots to compiled texture assets"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Collection

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

def resolve_material_texture_sets(payload: bytes, texture_uids: Collection[int], material_count: int) -> tuple[MaterialTextureSet, ...]:
    """Resolve each mesh material slot to its compiled texture tiers"""

    if material_count < 0:
        raise ValueError("Material count cannot be negative")

    if material_count == 0:
        return ()

    entries = scan_nested_entries(payload)

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

    materials = []

    for material_entry in entries:
        if material_entry.metadata.file_type != CURRENT_MATERIAL:
            continue

        material_blob = payload[material_entry.offset:material_entry.end]

        spec_uids = referenced_uids(material_blob, spec_entries.keys())

        if not spec_uids:
            continue

        roles: dict[int, tuple[int, ...]] = {}

        for spec_uid in spec_uids:
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

        if not roles:
            continue

        materials.append(
            MaterialTextureSet(
                diffuse_uids=roles.get(DIFFUSE_ROLE, ()),
                normal_uids=roles.get(NORMAL_ROLE, ()),
                specular_uids=roles.get(SPECULAR_ROLE, ()),
                mask_uids=roles.get(MASK_ROLE, ())
            )
        )

        if len(materials) == material_count:
            break

    return tuple(materials)