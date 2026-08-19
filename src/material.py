"""Resolve Siege material slots to compiled texture assets"""

from __future__ import annotations
from dataclasses import dataclass, replace

import struct
from typing import Collection, Iterable

from src.metadata import (
    FileMetadata,
    InvalidFileMetadata,
    parse_file_metadata
)

CURRENT_MATERIAL = 0x9BFBCAA8
CURRENT_TEXTURE_MAP_SPEC = 0x7C7D57AE
CURRENT_TEXTURE_MAP = 0x3C7E34FD
CURRENT_TEXTURE_SELECTOR = 0x7E34C538
CURRENT_MESH = 0xF5C0AFD3
CURRENT_SHADER_DEFINES = 0xF01EABD8
CURRENT_SHADER_UNIFORMS = 0x508156F2

RECOGNIZED_TYPES = {
    CURRENT_MATERIAL,
    CURRENT_TEXTURE_MAP_SPEC,
    CURRENT_TEXTURE_MAP,
    CURRENT_MESH,
    CURRENT_SHADER_DEFINES,
    CURRENT_SHADER_UNIFORMS
}

UNIFORM_MARKER = 0xFBF80000

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
class MaterialTextureSelector:
    role: int
    spec_uid: int
    texture_map_uid: int
    texture_uids: tuple[int, ...] = ()
    source: str = "unknown"
    shader_binding: str | None = None

@dataclass(frozen=True)
class MaterialTextureSet:
    diffuse_uids: tuple[int, ...] = ()
    normal_uids: tuple[int, ...] = ()
    specular_uids: tuple[int, ...] = ()
    mask_uids: tuple[int, ...] = ()
    selectors: tuple[MaterialTextureSelector, ...] = ()
    shader_uid: int | None = None
    shader_bindings: tuple[ShaderBinding, ...] = ()
    shader_uniforms: tuple[ShaderUniform, ...] = ()

@dataclass(frozen=True)
class ShaderUniform:
    owner_uid: int
    index: int
    name: str
    uniform_type: int
    texture_spec_uid: int | None = None
    values: tuple[float, ...] = ()

@dataclass(frozen=True)
class ShaderBinding:
    shader_uid: int
    name: str
    target: str

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

def read_shader_uniforms(payload: bytes) -> tuple[ShaderUniform, ...]:
    """Read named uniforms from embedded Siege shader uniform tables"""

    found = []

    for entry in scan_nested_entries(payload):
        if entry.metadata.file_type != CURRENT_SHADER_UNIFORMS:
            continue

        start = entry.data_offset

        if start + 8 > entry.end:
            continue

        magic, uniform_count = struct.unpack_from("<II", payload, start)

        if magic != CURRENT_SHADER_UNIFORMS or uniform_count > 4096:
            continue

        search_offset = start + 8

        for index in range(uniform_count):
            marker = struct.pack("<I", UNIFORM_MARKER | index)

            uniform_offset = payload.find(marker, search_offset, entry.end)

            if uniform_offset < 0 or uniform_offset + 20 > entry.end:
                break

            (
                _uniform_class,
                uniform_type,
                name_length
            ) = struct.unpack_from("<III", payload, uniform_offset + 8)

            if name_length > 4096:
                break

            name_start = uniform_offset + 20
            name_end = name_start + name_length

            if name_end >= entry.end or payload[name_end] != 0:
                break

            try:
                name = payload[name_start:name_end].decode("utf-8")
            except UnicodeDecodeError:
                break


            texture_spec_uid = None
            values = ()
            value_offset = name_end + 1

            if uniform_type == 0:
                # Texture uniforms finish with their TextureMapSpec UID
                if value_offset + 32 <= entry.end:
                    candidate_uid = struct.unpack_from("<Q", payload, value_offset + 24)[0]

                    if candidate_uid:
                        texture_spec_uid = candidate_uid
            elif uniform_type == 1:
                # Vector/Scalar uniforms store their value
                # after a 24 byte descriptor
                if value_offset + 28 <= entry.end:
                    is_vector = struct.unpack_from("<I", payload, value_offset)[0]

                    value_count = 4 if is_vector else 1
                    values_end = value_offset + 24 + value_count * 4

                    if values_end <= entry.end:
                        values = struct.unpack_from(f"<{value_count}f", payload, value_offset + 24)

            found.append(
                ShaderUniform(
                    owner_uid=entry.metadata.uid,
                    index=index,
                    name=name,
                    uniform_type=uniform_type,
                    texture_spec_uid=texture_spec_uid,
                    values=values
                )
            )

            search_offset = name_end + 1

    return tuple(found)

def read_shader_bindings(payload: bytes) -> tuple[ShaderBinding, ...]:
    """Read names assigned to Siege custom shader parameters"""

    found = []

    for entry in scan_nested_entries(payload):
        if entry.metadata.file_type != CURRENT_SHADER_DEFINES:
            continue

        shader_blob = payload[entry.data_offset:entry.end]

        for raw_line in shader_blob.splitlines():
            line = raw_line.strip()
            define_offset = line.find(b"#define ")

            if define_offset < 0:
                continue

            pieces = line[define_offset:].split()

            if len(pieces) < 3:
                continue

            encoded_name = pieces[1]
            encoded_target = pieces[2]

            if not (encoded_target.startswith(b"_CustomParam") or encoded_target.startswith(b"UM_CustomParam")):
                continue

            try:
                name = encoded_name.decode("utf-8")
                target = encoded_target.decode("utf-8")
            except UnicodeDecodeError:
                continue

            found.append(
                ShaderBinding(
                    shader_uid=entry.metadata.uid,
                    name=name,
                    target=target
                )
            )
    return tuple(found)

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

def _custom_texture_index(target: str) -> int | None:
    normalized = target.lstrip("_")

    if normalized.startswith("UM_"):
        normalized = normalized[3:]

    prefix = "CustomParamTexture"

    if not normalized.startswith(prefix):
        return None

    suffix = normalized[len(prefix):]

    if not suffix.isdigit():
        return None

    return int(suffix)

def _custom_vector_target(target: str) -> tuple[int, int | None] | None:
    """Return the custom vector index and optional component"""

    normalized = target.lstrip("_")

    if normalized.startswith("UM_"):
        normalized = normalized[3:]

    prefix = "CustomParamVector"

    if not normalized.startswith(prefix):
        return None

    suffix = normalized[len(prefix):]

    if "." in suffix:
        vector_text, component_text = suffix.split(".", 1)
        component = {
            "x": 0,
            "y": 1,
            "z": 2,
            "w": 3
        }.get(component_text)

        if component is None:
            return None
    else:
        vector_text = suffix
        component = None

    if not vector_text.isdigit():
        return None

    return int(vector_text), component

def apply_material_uniform_overrides(material_blob: bytes, uniforms: Iterable[ShaderUniform], bindings: Iterable[ShaderBinding]) -> tuple[ShaderUniform, ...]:
    """Apply a material's packed custom vector values to shader uniforms"""

    uniforms = tuple(uniforms)

    targets = {
        binding.name: target
        for binding in bindings
        if (target := _custom_vector_target(binding.target)) is not None
    }

    applicable = [
        uniform
        for uniform in uniforms
        if uniform.name in targets
    ]

    if not applicable:
        return uniforms

    first_uniform = min(applicable, key=lambda uniform: uniform.index)

    marker = struct.pack("<I",  UNIFORM_MARKER | first_uniform.index)
    marker_offset = material_blob.find(marker)

    if marker_offset < 0:
        return uniforms

    maximum_vector = max(targets[uniform.name][0] for uniform in applicable)

    value_count = (maximum_vector + 1) * 4
    values_offset = marker_offset + 40
    values_end = values_offset + value_count * 4

    if values_end > len(material_blob):
        return uniforms

    packed_values = struct.unpack_from(f"<{value_count}f", material_blob, values_offset)

    resolved = []

    for uniform in uniforms:
        target = targets.get(uniform.name)

        if target is None:
            resolved.append(uniform)
            continue

        vector_index, component = target
        vector_start = vector_index * 4
        vector = packed_values[vector_start:vector_start + 4]

        if component is None:
            values = tuple(vector)
        else:
            values = (vector[component],)

        resolved.append(replace(uniform, values=values))

    return tuple(resolved)

def _material_selector_source(material_blob: bytes, spec_uid: int, bindings_by_spec: dict[int, str], bindings_by_index: dict[int, str]) -> tuple[str, str | None]:
    position = material_blob.find(struct.pack("<Q", spec_uid))

    if position < 0:
        return "unknown", None

    if position >= 12:
        marker = struct.unpack_from("<I", material_blob, position - 12)[0]

        if marker == CURRENT_TEXTURE_SELECTOR:
            return "base", None

        if (marker & 0xFFFF0000) == UNIFORM_MARKER:
            return "detail", None

    binding = bindings_by_spec.get(spec_uid)

    if binding is not None:
        return "shader", binding

    # Current custom shader texture records can place their spec UID
    # farther after FBF8 parameter marker
    for distance in range(16, min(position, 64) + 1, 4):
        marker = struct.unpack_from("<I", material_blob, position - distance)[0]

        if (marker & 0xFFFF0000) != UNIFORM_MARKER:
            continue

        parameter_index = marker & 0xFFFF
        binding = bindings_by_index.get(parameter_index)

        if binding is not None:
            return "shader", binding

    return "unknown", None

def resolve_material_texture_sets(payload: bytes, texture_uids: Collection[int], geometry_uids: Iterable[int]) -> tuple[tuple[MaterialTextureSet, ...], ...]:
    """Resolve local mesh material slots for each geometry part"""

    geometry_uids = tuple(geometry_uids)
    entries = scan_nested_entries(payload)

    bindings_by_shader: dict[int, list[ShaderBinding]] = {}
    bindings_by_spec: dict[int, dict[int, str]] = {}
    bindings_by_index: dict[int, dict[int, str]] = {}
    parsed_uniforms = read_shader_uniforms(payload)
    uniform_spec_by_name = {
        uniform.name: uniform.texture_spec_uid
        for uniform in parsed_uniforms
        if uniform.texture_spec_uid is not None
    }

    for binding in read_shader_bindings(payload):
        bindings_by_shader.setdefault(binding.shader_uid, []).append(binding)

        spec_uid = uniform_spec_by_name.get(binding.name)

        if spec_uid is not None:
            bindings_by_spec.setdefault(binding.shader_uid, {})[spec_uid] = binding.name

        parameter_index = _custom_texture_index(binding.target)

        if parameter_index is not None:
            bindings_by_index.setdefault(binding.shader_uid, {})[parameter_index] = binding.name

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

        shader_uid = None
        material_start = material_entry.data_offset

        if material_start + 12 <= material_entry.end and struct.unpack_from("<I", payload, material_start)[0] == CURRENT_MATERIAL:
            shader_uid = struct.unpack_from("<Q", payload, material_start + 4)[0]

        roles: dict[int, tuple[int, ...]] = {}
        selectors = []

        for spec_uid in referenced_uids(material_blob, spec_entries.keys()):
            spec = read_texture_map_spec(payload, spec_entries[spec_uid])

            if spec is None:
                continue

            texture_role, texture_map_uid = spec
            texture_map_entry = texture_map_entries.get(texture_map_uid)

            if texture_map_entry is None:
                continue

            texture_map_blob = payload[texture_map_entry.offset:texture_map_entry.end]
            compile_uids = referenced_uids(texture_map_blob, texture_uids)

            if not compile_uids:
                continue

            selector_source, selector_binding = _material_selector_source(material_blob, spec_uid, bindings_by_spec.get(shader_uid, {}), bindings_by_index.get(shader_uid, {}))

            selectors.append(
                MaterialTextureSelector(
                    role=texture_role,
                    spec_uid=spec_uid,
                    texture_map_uid=texture_map_uid,
                    texture_uids=compile_uids,
                    source=selector_source,
                    shader_binding=selector_binding
                )
            )

            # Keep the first selector as the existing base map choice
            # Later selectors remain available for detail map research
            roles.setdefault(texture_role, compile_uids)

        if selectors:
            material_bindings = tuple(
                bindings_by_shader.get(shader_uid, ())
            )

            binding_names = {
                binding.name
                for binding in material_bindings
            }

            default_uniforms = tuple(
                uniform
                for uniform in parsed_uniforms
                if uniform.uniform_type == 1 and uniform.values and uniform.name in binding_names
            )

            material_uniforms = apply_material_uniform_overrides(material_blob, default_uniforms, material_bindings)

            textured_materials[material_uid] = MaterialTextureSet(
                diffuse_uids=roles.get(DIFFUSE_ROLE, ()),
                normal_uids=roles.get(NORMAL_ROLE, ()),
                specular_uids=roles.get(SPECULAR_ROLE, ()),
                mask_uids=roles.get(MASK_ROLE, ()),
                selectors=tuple(selectors),
                shader_uid=shader_uid,
                shader_bindings=material_bindings,
                shader_uniforms=material_uniforms,
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