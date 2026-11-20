"""Resolve Siege material slots to compiled texture assets"""

from __future__ import annotations
from dataclasses import dataclass, replace

import struct
import math
from typing import Collection, Iterable

from src.tint import read_tint_parameters, LEGACY_TINT_MATERIALS
from src.metadata import (
    FileMetadata,
    InvalidFileMetadata,
    parse_file_metadata
)

CURRENT_MATERIAL = 0x505A2021
CURRENT_TEXTURE_MAP_SPEC = 0x87CE3D94
CURRENT_TEXTURE_MAP = 0x07CA0C90
CURRENT_TEXTURE_SELECTOR = 0x3B9A54D9
CURRENT_MESH = 0x7DFEB735
CURRENT_SHADER_DEFINES = 0xD01611C7
CURRENT_SHADER_UNIFORMS = 0xD8F8C3E5

RECOGNIZED_TYPES = {
    CURRENT_MATERIAL,
    CURRENT_TEXTURE_MAP_SPEC,
    CURRENT_TEXTURE_MAP,
    CURRENT_MESH,
    CURRENT_SHADER_DEFINES,
    CURRENT_SHADER_UNIFORMS
}

UNIFORM_MARKER = 0xFBF80000
TEXTURE_UNIFORM_CLASS = 0x6186E596

DIFFUSE_ROLE = 0
NORMAL_ROLE = 1
SPECULAR_ROLE = 2
MASK_ROLE = 7

SOLID_COSMETIC_SHADER = 0x0000001397A32F38
TINTED_HEADGEAR_SHADER = 0x0000000841DC11F9
SOLID_COLOR_PARAMETERS = {
    SOLID_COSMETIC_SHADER: 2,
    TINTED_HEADGEAR_SHADER: 0
}

UNTEXTURED_COLOR_PARAMETERS = {
    0x0000000099E2C926: 0,
    0x0000000099E2C947: 0,
    0x0000000099E2C950: 0,
    0x0000005DB6637AD7: 2
}

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
    solid_color: tuple[float, float, float, float] | None = None
    selectors: tuple[MaterialTextureSelector, ...] = ()
    shader_uid: int | None = None
    shader_bindings: tuple[ShaderBinding, ...] = ()
    shader_uniforms: tuple[ShaderUniform, ...] = ()
    material_uid: int | None = None

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

            if uniform_type == 0 or _uniform_class in { TEXTURE_UNIFORM_CLASS, 0x5AC16360 }:
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

def referenced_uids(blob: bytes, candidates: Collection[int], *, preserve_duplicates: bool = False) -> tuple[int, ...]:
    """Return candidate UIDs in their serialized order"""

    matches = []

    for uid in candidates:
        needle = struct.pack("<Q", uid)
        position = 0

        while True:
            position = blob.find(needle, position)

            if position < 0:
                break

            matches.append(
                (
                    position,
                    uid
                )
            )

            if not preserve_duplicates:
                break

            position += len(needle)

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

def read_solid_material_color(material_blob: bytes, shader_uid: int | None, *, has_diffuse: bool) -> tuple[float, float, float, float] | None:
    """Read a packed material color when it is safe to use as base color"""

    parameter = SOLID_COLOR_PARAMETERS.get(shader_uid)

    if parameter is None and not has_diffuse:
        parameter = UNTEXTURED_COLOR_PARAMETERS.get(shader_uid)

    if parameter is None:
        return None

    marker = struct.pack("<I", UNIFORM_MARKER | parameter)
    marker_offset = material_blob.find(marker)

    if marker_offset < 0:
        return None

    color_offset = marker_offset + 40
    color_end = color_offset + 16

    if color_end > len(material_blob):
        return None

    color = struct.unpack_from("<4f", material_blob, color_offset)

    if not all(0.0 <= component <= 1.0 for component in color):
        return None

    return color

def apply_material_uniform_overrides(material_blob: bytes, uniforms: Iterable[ShaderUniform], bindings: Iterable[ShaderBinding], *, parameter_index: int | None = None) -> tuple[ShaderUniform, ...]:
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

    if parameter_index is None:
        first_uniform = min(applicable, key=lambda uniform: uniform.index)
        parameter_index = first_uniform.index

    marker = struct.pack("<I",  UNIFORM_MARKER | parameter_index)
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

def read_clothing_preview_colors(material_blob):
    """Read colors from the verified 28-property clothing layout"""

    prefix = struct.pack("<IQHIH", 28, 0xB89D11A6, 0, 13, 0)
    start = material_blob.find(prefix)
    if start < 0:
        return ()

    kinds = (13, 28, 10, 10, 10, 10, 10, 28, 10) * 3 + (10,)
    color_keys = (0xB89D11A6, 0xDA050C32, 0x5A5F42A7)
    names = ("MaskRed_Color", "MaskGreen_Color", "MaskBlue_Color")
    cursor = start + 4
    result = []

    for index, expected_kind in enumerate(kinds):
        size = {13: 16, 28: 8, 10: 4}[expected_kind]
        if cursor + 16 + size > len(material_blob):
            raise ValueError("Truncated clothing property table")

        key, zero1, kind, zero2 = struct.unpack_from("<QHIH", material_blob, cursor)
        if zero1 or zero2 or kind != expected_kind:
            raise ValueError("Unexpected clothing property layout")

        cursor += 16

        if kind == 13:
            if key != color_keys[len(result)]:
                raise ValueError("Unexpected clothing color key")

            values = struct.unpack_from("<4f", material_blob, cursor)
            if not all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in values):
                raise ValueError("Invalid clothing color")

            result.append(ShaderUniform(
                owner_uid=SOLID_COSMETIC_SHADER,
                index=index,
                name=names[len(result)],
                uniform_type=1,
                values=values,
            ))
        elif index == 27:
            if key != 0x7E59E3AB:
                raise ValueError("Unexpected clothing mask mode key")

            values = struct.unpack_from("<f", material_blob, cursor)
            if not math.isfinite(values[0]):
                raise ValueError("Invalid clothing mask mode")

            result.append(
                ShaderUniform(
                    owner_uid=SOLID_COSMETIC_SHADER,
                    index=index,
                    name="ClothingMaskMode",
                    uniform_type=1,
                    values=values
                )
            )

        cursor += size

    return tuple(result)

def read_clothing_preview_patterns(material_blob):
    """Read source pattern references and UV transforms for shader 1397A32F38"""
    prefix = struct.pack("<IQHIH", 28, 0xB89D11A6, 0, 13, 0)
    start = material_blob.find(prefix)
    if start < 0:
        return (), ()

    keys = (
        (0xB89D11A6, 0x8C9802AD5CC18D4A, 0x03A322F5, 0xEA47F3F7, 0x2E4B1B11, 0xD69C93D8, 0x2FF77BD1, 0x8C9802AD26B0A71D, 0x621DBE76),
        (0xDA050C32, 0x8C9802AD88579D79, 0xEA7F8337, 0xB25A3C99, 0x036BCA72, 0xF4A27D27, 0x0DC9952E, 0x8C9802ADD1B0685D, 0x3A007118),
        (0x5A5F42A7, 0x8C9802AD9F0FCEAF, 0xCF7FF6B8, 0x2E2E18AE, 0xEC865FEC, 0xCC27C964, 0x354C216D, 0x8C9802AD515555D8, 0xA674552F)
    )
    kinds = (13, 28, 10, 10, 10, 10, 10, 28, 10)
    cursor = start + 4
    uniforms = []
    patterns = []

    for layer, layer_keys in zip(("Red", "Green", "Blue"), keys):
        fields = []
        for key, kind in zip(layer_keys, kinds):
            fmt = {13: "<4f", 28: "<Q", 10: "<f"}[kind]
            size = struct.calcsize(fmt)
            if cursor + 16 + size > len(material_blob):
                raise ValueError("Truncated clothing pattern properties")
            if struct.unpack_from("<QHIH", material_blob, cursor) != (key, 0, kind, 0):
                raise ValueError("Unexpected clothing pattern property layout")

            fields.append(struct.unpack_from(fmt, material_blob, cursor + 16))
            cursor += 16 + size

        uv = tuple(fields[index][0] for index in (2, 3, 4))
        if not all(math.isfinite(value) for value in uv):
            raise ValueError("Invalid clothing pattern UV transform")

        uniforms.append(ShaderUniform(
            owner_uid=SOLID_COSMETIC_SHADER,
            index=0,
            name=f"Mask{layer}_UV",
            uniform_type=1,
            values=uv
        ))

        spec_uid = fields[1][0]
        if spec_uid == 0x0000000119CC47A7:
            # Source spec -> map 119CC47A9 -> compiled 359E631C7
            # all 64 src pixels are RGBA (128, 128, 128, 255)
            uniforms.append(ShaderUniform(
                owner_uid=SOLID_COSMETIC_SHADER,
                index=0,
                name=f"Mask{layer}_PatternConstant",
                uniform_type=1,
                values=(128.0 / 255.0,) * 3
            ))
        elif spec_uid:
            patterns.append((f"Mask{layer}_Pattern", spec_uid))

    return tuple(uniforms), tuple(patterns)

def apply_eye_property_overrides(material_blob, uniforms):
    """Read the verified 12-property layout used by shader 557005948D"""

    fields = (
        (0x8C9802ADD1C4E883, 28, None),
        (0x8C9802AD30F7E9F3, 28, None),
        (0x4F6D05D9, 13, "ScleraColorWhite"),
        (0xABA880A5, 13, "ScleraColorBlack"),
        (0x53851558, 13, "IrisColorWhite"),
        (0xB7409024, 13, "IrisColorBlack"),
        (0x5EB4415B, 10, "IrisGlossiness"),
        (0x425C51DA, 10, "ScleraGlossiness"),
        (0x2FB94CE9, 10, "DepthScale"),
        (0x90F42B7A, 10, "IrisUVRadius"),
        (0x1106D434, 10, "LimbusDarkScale"),
        (0x36BA7687, 10, "LimbusPow"),
    )
    prefix = struct.pack("<IQHIH", 12, fields[0][0], 0, 28, 0)
    start = material_blob.find(prefix)
    if start < 0:
        return tuple(uniforms)

    cursor = start + 4
    values = {}

    for key, kind, name in fields:
        size = {28: 8, 13: 16, 10: 4}[kind]

        if cursor + 16 + size > len(material_blob):
            raise ValueError("Truncated eye property table")

        header = struct.unpack_from("<QHIH", material_blob, cursor)
        if header != (key, 0, kind, 0):
            raise ValueError("Unexpected eye property layout")

        cursor += 16

        if name is not None:
            value = struct.unpack_from("<4f" if kind == 13 else "<f", material_blob, cursor)
            if not all(math.isfinite(component) for component in value):
                raise ValueError("Non-finite eye property")

            values[name] = value

        cursor += size

    return tuple(replace(uniform, values=values.get(uniform.name, uniform.values)) for uniform in uniforms)

def apply_eye_overlay_property_overrides(material_blob, uniforms):
    """Read properties of shader 2B188360D9"""
    fields = (
        (0x2FB94CE9, 10, "DepthScale"),
        (0x3DBB5683, 10, "FresnelExponent"),
        (0x8C9802ADD1C4E883, 28, None)
    )

    prefix = struct.pack("<IQHIH", 3,fields[0][0], 0, 10, 0)
    start = material_blob.find(prefix)

    if start < 0 or material_blob.find(prefix, start + 1) >= 0:
        raise ValueError("Missing or ambiguous eye overlay property table")

    cursor = start + 4
    values = {}

    for key, kind, name in fields:
        size = 4 if kind == 10 else 8

        if cursor + 16 + size > len(material_blob):
            raise ValueError("Truncated eye overlay property table")

        header = struct.unpack_from("<QHIH", material_blob, cursor)

        if header != (key, 0, kind, 0):
            raise ValueError("Unexpected eye overlay property layout")

        cursor += 16

        if name is not None:
            value = struct.unpack_from("<f", material_blob, cursor)[0]

            if not math.isfinite(value) or value <= 0:
                raise ValueError("Invalid eye overlay opacity property")

            values[name] = (value,)

        cursor += size

    found = {uniform.name for uniform in uniforms}

    if not values.keys() <= found:
        raise ValueError("Missing eye overlay shader bindings")

    return tuple(
        replace(uniform, values=values.get(uniform.name, uniform.values),)
        for uniform in uniforms
    )

def _material_selector_source(material_blob: bytes, spec_uid: int, bindings_by_spec: dict[int, str], bindings_by_index: dict[int, str]) -> tuple[str, str | None]:
    position = material_blob.find(struct.pack("<Q", spec_uid))

    if position < 0:
        return "unknown", None

    parameter_start = material_blob.find(struct.pack("<III", UNIFORM_MARKER, 0, 0xF2CE7E39))
    if parameter_start >= 0:
        for offset, name in ((32, "TintCustom0"), (100, "TintCustom1")):
            if position == parameter_start + offset:
                marker = struct.unpack_from("<I", material_blob, position - 12)[0]
                if marker == CURRENT_TEXTURE_SELECTOR:
                    return "shader", name

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

def _read_required_header_color(payload: bytes, entry: NestedEntry, label: str) -> tuple[float, float, float, float]:
    """Read 4 RGBA floats immediately after the material's shader UID"""
    color_offset = entry.data_offset + 12
    if color_offset + 16 > entry.end:
        raise ValueError(f"Truncated {label} color")

    color = struct.unpack_from("<4f", payload, color_offset)
    if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in color):
        raise ValueError(f"Invalid {label} color")
    return color

def _material_shader_uniforms(entry, blob, shader_uid, bindings, parsed_uniforms, clothing_uniforms):
    names = {binding.name for binding in bindings}
    defaults = tuple(
        uniform for uniform in parsed_uniforms
        if uniform.uniform_type == 1 and uniform.values and uniform.name in names
    )

    if shader_uid == 0x2B188360D9:
        return apply_eye_overlay_property_overrides(blob, defaults)

    parameter = SOLID_COLOR_PARAMETERS.get(shader_uid, UNTEXTURED_COLOR_PARAMETERS.get(shader_uid))
    uniforms = apply_material_uniform_overrides(blob, defaults, bindings, parameter_index=parameter)
    material_uid = entry.metadata.uid

    if shader_uid == TINTED_HEADGEAR_SHADER or (shader_uid == 0x99E2C947 and material_uid in LEGACY_TINT_MATERIALS):
        uniforms += tuple(
            ShaderUniform(material_uid, index, name, 1, values=values)
            for index, (name, values) in enumerate(read_tint_parameters(blob))
        )
    elif shader_uid == SOLID_COSMETIC_SHADER:
        uniforms += read_clothing_preview_colors(blob) + clothing_uniforms
    elif shader_uid == 0x557005948D:
        uniforms = apply_eye_property_overrides(blob, uniforms)
    elif shader_uid in (0x3051C028, 0x55B93242F6):
        # offsets are relative to the material data inside the entry's blob
        start = entry.metadata.data_offset
        if start + 174 <= len(blob):
            marker, kind = struct.unpack_from("<I4xI", blob, start + 130)
            if marker == UNIFORM_MARKER and kind == 0xF6348091:
                values = struct.unpack_from("<8f", blob, start + 142)
                if -3.0 <= values[0] <= 3.0 and all(0.0 <= v <= 1.0 for v in values[1:]):
                    uniforms += (ShaderUniform(material_uid, 0, "ExperimentalHairSourceV1", 1, values=values),)
    return uniforms

# each pair is (shader UID, material UID)
_REQUIRED_HEADER_COLORS = {
    (0x3BD13B9E, 0x6EA53F13A): "Black eye material",
    (0x40D7FCF0, 0x6EA53F15A): "Black eye material",
    (0x3327005AD, 0x73FA82522): "Black eye material",
    (0x3327005AD, 0x5BC241708): "Castle core material",
    (0x40D7FCF0, 0xBB1FAC464): "Candela indicator",
    (0x40D7FCF0, 0xBB1FAC470): "Candela indicator",
    (0x40D7FCF0, 0xBB1FAC47C): "Candela indicator",
    (0x1397A3EEB7, 0x2D1BEFAC46): "gadget material",
    (0x959B5D7CD, 0xBA5A08957): "gadget material",
}

_UNTEXTURED_GADGET_COLORS = {
    (0x3327005AD, 0x5B96E63987), (0x3327005AD, 0x731C35563),
    (0x3327005AD, 0x5CE744A9E5), (0x3327005AD, 0x25B13DCCC1),
    (0x3327005AD, 0x2591BE9437), (0x3327005AD, 0x316F5CF0D0),
    (0x3327005AD, 0x5C82A0B701), (0x3327005AD, 0x2591BC2AA2),
    (0x3327005AD, 0x1FE0103D4B), (0x3327005AD, 0x25B13DCDDD),
    (0x40D7FCF0, 0x18A4B0B612), (0x40D7FCF0, 0x18A4B0B61E),
    (0x40D7FCF0, 0x2B2488E8E7), (0x1397A3EEB7, 0x2DE308A6A1),
    (0x1397A3EEB7, 0x2E84C7F63A), (0x1397A3EEB7, 0x2EEE2AD9A6),
    (0x14BCCBDB, 0x33BAEC4606),
}

def _material_shader_color(payload, entry, blob, shader_uid, *, has_diffuse):
    material_uid = entry.metadata.uid
    key = (shader_uid, material_uid)
    label = _REQUIRED_HEADER_COLORS.get(key)
    if label is None and not has_diffuse and key in _UNTEXTURED_GADGET_COLORS:
        label = "gadget material"
    if label is not None:
        return _read_required_header_color(payload, entry, label)

    untextured_header = not has_diffuse and (
        shader_uid in (0x523BA2BF, 0x3BD13B9E, 0x2B188360D9)
        or key == (0xF2BB85C7E, 0x5978CB7154)
    )

    # source header color used to approximate texture modulation on legacy gear
    tinted_texture = has_diffuse and shader_uid == 0x99E2C947 and material_uid in (
        0xA8715F23D, 0x995497897, 0x995497909, 0x99549791D,
    )
    if (untextured_header or tinted_texture) and entry.data_offset + 28 <= entry.end:
        color = struct.unpack_from("<4f", payload, entry.data_offset + 12)
        if all(0.0 <= value <= 1.0 for value in color):
            return color

    return read_solid_material_color(blob, shader_uid, has_diffuse=has_diffuse)

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

    def lookup_texture_map(spec_uid: int) -> tuple[int, int, tuple[int, ...]] | None:
        """Return the role, map UID and compiled texture UIDs for a specification"""
        spec_entry = spec_entries.get(spec_uid)
        if spec_entry is None:
            return None

        spec = read_texture_map_spec(payload, spec_entry)
        if spec is None:
            return None

        role, map_uid = spec
        map_entry = texture_map_entries.get(map_uid)
        if map_entry is None:
            return None

        map_blob = payload[map_entry.offset:map_entry.end]
        compiled_uids = referenced_uids(map_blob, texture_uids)
        if not compiled_uids:
            return None

        return role, map_uid, compiled_uids

    mesh_entries = [
        entry
        for entry in entries
        if entry.metadata.file_type == CURRENT_MESH
    ]

    resolved_materials: dict[int, MaterialTextureSet] = {}
    textured_material_uids: set[int] = set()

    for material_uid, material_entry in material_entries.items():
        material_blob = payload[material_entry.offset:material_entry.end]

        shader_uid = None
        material_start = material_entry.data_offset

        if material_start + 12 <= material_entry.end and struct.unpack_from("<I", payload, material_start)[0] == CURRENT_MATERIAL:
            shader_uid = struct.unpack_from("<Q", payload, material_start + 4)[0]

        roles: dict[int, tuple[int, ...]] = {}
        base_roles: set[int] = set()
        selectors = []

        for spec_uid in referenced_uids(material_blob, spec_entries.keys()):
            texture_map = lookup_texture_map(spec_uid)
            if texture_map is None:
                continue

            texture_role, texture_map_uid, compile_uids = texture_map

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

            # Custom tint maps must not replace the base albedo - Blake
            if selector_binding in ("TintCustom0", "TintCustom1"):
                continue

            # Reflection texture, retain its selector but not as base albedo
            if shader_uid == 0x0000002B188360D9 and selector_binding == "SecondaryEnv":
                continue

            # Explicit base selectors take priority over custom shader maps
            if selector_source == "base" and texture_role not in base_roles:
                roles[texture_role] = compile_uids
                base_roles.add(texture_role)
            else:
                roles.setdefault(texture_role, compile_uids)

        clothing_uniforms = ()

        if shader_uid == SOLID_COSMETIC_SHADER:
            clothing_uniforms, clothing_patterns = read_clothing_preview_patterns(material_blob)
            for binding, spec_uid in clothing_patterns:
                texture_map = lookup_texture_map(spec_uid)
                if texture_map is None:
                    continue

                role, map_uid, compiled_uids = texture_map
                selectors.append(MaterialTextureSelector(
                    role=role,
                    spec_uid=spec_uid,
                    texture_map_uid=map_uid,
                    texture_uids=compiled_uids,
                    source="shader",
                    shader_binding=binding
                ))

        material_bindings = tuple(bindings_by_shader.get(shader_uid, ()))

        material_uniforms = _material_shader_uniforms(material_entry, material_blob, shader_uid, material_bindings, parsed_uniforms, clothing_uniforms)
        solid_color = _material_shader_color(payload, material_entry, material_blob, shader_uid, has_diffuse=bool(roles.get(DIFFUSE_ROLE)))

        if (shader_uid, material_uid) == (0xE09ED870, 0x7185D293):
            # handset's scrambled screen artwork lives in texture role 3
            for selector in selectors:
                if selector.role == 3 and selector.spec_uid == 0x576A64E9D:
                    roles[DIFFUSE_ROLE] = selector.texture_uids
                    break

        resolved_materials[material_uid] = MaterialTextureSet(
            diffuse_uids=roles.get(DIFFUSE_ROLE, ()),
            normal_uids=roles.get(NORMAL_ROLE, ()),
            specular_uids=roles.get(SPECULAR_ROLE, ()),
            mask_uids=roles.get(MASK_ROLE, ()),
            solid_color=solid_color,
            selectors=tuple(selectors),
            shader_uid=shader_uid,
            shader_bindings=material_bindings,
            shader_uniforms=material_uniforms,
            material_uid=material_uid,
        )

        if selectors:
            textured_material_uids.add(material_uid)

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

                if override_uid in textured_material_uids or override_uid in resolved_materials and resolved_materials[override_uid].solid_color is not None:
                    material_overrides[base_uid] = override_uid
                    break

            position += 1

    # Override can reference a base material outside this package
    override_tag = struct.pack("<I", 0x73F86CAC)
    position = 0
    while True:
        position = header.find(override_tag, position)
        if position < 0:
            break

        if position >= 8 and position + 20 <= len(header):
            marker, reserved = struct.unpack_from("<II", header, position - 8)
            if marker & 0xFFFF0000 == UNIFORM_MARKER and reserved == 0:
                base_uid, override_uid = struct.unpack_from("<QQ", header, position + 4)
                if base_uid not in material_entries and (override_uid in textured_material_uids or (override_uid in resolved_materials and resolved_materials[override_uid].solid_color is not None)):
                    material_overrides[base_uid] = override_uid

        position += 4

    return _resolve_mesh_material_slots(payload, geometry_uids, mesh_entries, material_entries, material_overrides, resolved_materials)

def _resolve_mesh_material_slots(payload: bytes, geometry_uids: Iterable[int], mesh_entries: list[NestedEntry], material_entries: dict[int, NestedEntry], material_overrides: dict[int, int], resolved_materials: dict[int, MaterialTextureSet]) -> tuple[tuple[MaterialTextureSet, ...], ...]:
    """Return one ordered material-slot tuple per requested geometry"""
    candidate_material_uids = material_entries.keys() | material_overrides.keys()
    part_materials = []

    for geometry_uid in geometry_uids:
        geometry_reference = struct.pack("<Q", geometry_uid)
        mesh_entry = next(
            (
                entry for entry in mesh_entries
                if payload.find(geometry_reference, entry.offset, entry.end) >= 0
            ),
            None,
        )

        if mesh_entry is None:
            part_materials.append(())
            continue

        if geometry_uid in {0x690524BB81, 0x690524BB88} and parse_file_metadata(payload).uid == 0x69837CC04E:
            start = mesh_entry.data_offset
            if start + 12 > mesh_entry.end:
                raise ValueError("Truncated Noor projectile mesh")
            bone_count = struct.unpack_from("<I", payload, start + 8)[0]
            geometry_offset = start + 21 + bone_count * 80
            if geometry_offset + 20 > mesh_entry.end:
                raise ValueError("Truncated Noor projectile material table")

            geometry, count, base_material = struct.unpack_from("<QIQ", payload, geometry_offset)
            override = struct.pack("<IIIQQ", 0xFBF80001, 0, 0x73F86CAC, 0x289DC8ED8C, 0x6885BA3B92,)
            source = resolved_materials.get(0x6885BA3B92)

            if geometry != geometry_uid or count != 1 or base_material != 0x289DC8ED8C or payload.count(override) != 1 or source is None or source.shader_uid != 0x3327005AD or not source.diffuse_uids:
                raise ValueError("Noor projectile material mapping changed")

            part_materials.append((source,))
            continue

        if geometry_uid == 0x5514C1781B and parse_file_metadata(payload).uid == 0x55DC2F2157:
            # Alternate state overrides must not turn the intact form into glass
            expected = (
                0x50C666F569, 0x56572D7F93, 0x54469B20F4, 0x54469B20C5,
                0x54469B21D4, 0x5481EA7080, 0x5481EA7081, 0x5481EA7086,
                0x5481EA7084, 0x5481EA7083, 0x5481EA7085, 0x54FE600A5D,
            )
            matching_tables = []
            for candidate_entry in mesh_entries:
                start = candidate_entry.data_offset
                if start + 12 > candidate_entry.end:
                    continue
                bone_count = struct.unpack_from("<I", payload, start + 8)[0]
                geometry_offset = start + 21 + bone_count * 80
                table = geometry_offset + 8
                if table + 4 > candidate_entry.end:
                    continue
                if struct.unpack_from("<Q", payload, geometry_offset)[0] != geometry_uid:
                    continue
                if table + 4 + len(expected) * 8 > candidate_entry.end:
                    raise ValueError("Truncated Osa material slots")
                count = struct.unpack_from("<I", payload, table)[0]
                slots = struct.unpack_from(f"<{len(expected)}Q", payload, table + 4)
                if count != len(expected) or slots != expected:
                    raise ValueError("Osa intact material slots changed")
                matching_tables.append(slots)

            if len(matching_tables) != 1:
                raise ValueError("Missing or ambiguous Osa intact mesh")

            slots = []
            for material_uid in expected:
                source = resolved_materials.get(material_uid)
                if source is None:
                    raise ValueError("Missing Osa intact material")
                if material_uid in {0x54469B20F4, 0x54469B20C5, 0x54469B21D4}:
                    identity = struct.pack("<IQQ", 0x73F86CAC, material_uid, material_uid)
                    entry = material_entries.get(material_uid)
                    if payload.count(identity) != 1 or entry is None or source.shader_uid != 0x3327005AD or source.diffuse_uids or entry.data_offset + 28 > entry.end:
                        raise ValueError("Osa intact frame material changed")
                    color = struct.unpack_from("<4f", payload, entry.data_offset + 12)
                    if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in color):
                        raise ValueError("Invalid Osa frame material color")
                    source = replace(source, solid_color=color)
                slots.append(source)

            part_materials.append(tuple(slots))
            continue

        if geometry_uid == 0x278FADDBCA and parse_file_metadata(payload).uid == 0x24F948E738:
            # Preserve external sight slots instead of silently dropping them
            start = mesh_entry.data_offset
            if start + 12 > mesh_entry.end:
                raise ValueError("Truncated Mozzie launcher mesh")

            bone_count = struct.unpack_from("<I", payload, start + 8)[0]
            geometry_offset = start + 21 + bone_count * 80
            table = geometry_offset + 8

            expected = (
                0x289DC8ED8C, 0x3CEBBFD78B, 0x2B2488E8E7,
                0x36D604A6, 0x257CB37667,
            )
            if table + 4 + 8 * len(expected) > mesh_entry.end:
                raise ValueError("Truncated Mozzie launcher material slots")
            if struct.unpack_from("<Q", payload, geometry_offset)[0] != geometry_uid or struct.unpack_from("<I", payload, table)[0] != len(expected) or struct.unpack_from("<5Q", payload, table + 4) != expected:
                raise ValueError("Mozzie launcher material slots changed")

            slots = tuple(
                resolved_materials.get(material_overrides.get(uid, uid))
                for uid in expected[:3]
            )
            if any(slot is None for slot in slots) or tuple(slot.material_uid for slot in slots) != ( 0x278FAE0CD6, 0x278FAEC203, 0x2B2488E8E7)or any(uid in resolved_materials or uid in material_overrides for uid in expected[3:]):
                raise ValueError("Mozzie launcher material definitions changed")

            part_materials.append((*slots, MaterialTextureSet(material_uid=expected[3]), MaterialTextureSet(material_uid=expected[4])))
            continue

        # Ash's launcher uses a shared placeholder in its actual slot table
        # Entity overrides occur after the mesh entries in this package
        if geometry_uid in (0x000000053984EC3C, 0x000000053984ECF8) and parse_file_metadata(payload).uid == 0x000000009B2CAF2E:
            start = mesh_entry.data_offset
            bone_count = struct.unpack_from("<I", payload, start + 8)[0]
            table = start + 21 + bone_count * 80 + 8
            expected_count = 4 if geometry_uid == 0x000000053984EC3C else 1
            if table + 4 + expected_count * 8 > mesh_entry.end:
                raise ValueError("Truncated Ash launcher material slots")
            count = struct.unpack_from("<I", payload, table)[0]
            slots = struct.unpack_from(f"{expected_count}Q", payload, table + 4)
            override = struct.pack("<IQQ", 0x73F86CAC, 0x00000005F82132EC, 0x00000000A2F22E46)
            source = resolved_materials.get(0x00000000A2F22E46)
            if count != expected_count or slots != (0x00000005F82132EC,) * expected_count or payload.count(override) != 1 or source is None or not source.diffuse_uids:
                raise ValueError("Ash launcher material mapping changed")
            part_materials.append((source,) * expected_count)
            continue

        mesh_blob = payload[mesh_entry.offset:mesh_entry.end]
        base_uids = referenced_uids(mesh_blob, candidate_material_uids, preserve_duplicates=True)
        slot_uids = (material_overrides.get(uid, uid) for uid in base_uids)
        part_materials.append(tuple(resolved_materials.get(uid) or MaterialTextureSet() for uid in slot_uids))

    return tuple(part_materials)