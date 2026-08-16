# Parse .forge mesh payloads

import math
import struct
from dataclasses import dataclass

COMPILED_MESH = struct.pack("<I", 0xFC9E1595)
FLOAT_VERT_LENS = (0x24, 0x28, 0x2C) # float 32 position layouts
PACKED_VERT_LENS = (0x18, 0x1C) # int16 + scale packed positions

# bytes before UV block (planar): position + normal + tangent + binormal (+ color)
_PRE_UV = {0x24: 24, 0x28: 24, 0x2C: 24, 0x18: 20, 0x1C: 24}

@dataclass(frozen=True)
class MeshIsland:
    material_id: int
    faces: tuple[tuple[int, int, int], ...]
    bone_palette: tuple[int, ...] = ()

def read_lod0_islands(payload, tris, tail, num_islands):
    """Read LOD0 faces while preserving Siege material IDs"""

    islands = []

    for index in range(num_islands):
        record = struct.unpack_from("<9I", payload, tail + index * 36)

        first_triangle = record[3] * 64
        triangle_count = record[4] * 64
        material_id = record[5]

        faces = []

        for triangle in range(first_triangle, first_triangle + triangle_count):
            a, b, c = struct.unpack_from("<HHH", payload, tris + triangle * 6)

            if a != b and b != c and a != c:
                faces.append((a, b, c))

        islands.append(
            MeshIsland(
                material_id=material_id,
                faces=tuple(faces)
            )
        )
    return tuple(islands)

def read_skin_weights(payload, vbo, verts_len, num_verts, vert_len):
    """Read four joint indices and normalized weights from a skinned vertex buffer"""

    if vert_len != 0x24:
        return (), ()

    joints_offset = vbo + verts_len - num_verts * 8
    weight_offsets = joints_offset + num_verts * 4

    joints = []
    weights = []

    for index in range(num_verts):
        joint_values = struct.unpack_from("<4B", payload, joints_offset + index * 4)
        weight_values = struct.unpack_from("<4B", payload, weight_offsets + index * 4)

        total = sum(weight_values)

        if total:
            normalized = tuple(
                value / total
                for value in weight_values
            )
        else:
            normalized = (1.0, 0.0, 0.0, 0.0)

        joints.append(joint_values)
        weights.append(normalized)

    return tuple(joints), tuple(weights)

def read_bone_palettes(payload, tail, num_lods, num_islands):
    """Read each LOD0 island's global bone IDs"""

    palette_table = tail + num_lods * num_islands * 36 + num_islands * 32

    palettes = [None] * num_islands

    for record_index in range(num_islands):
        record = palette_table + record_index * 268

        if record + 9 > len(payload):
            raise ValueError(f"Bone palette record {record_index} is truncated")

        (
            enabled,
            bone_count,
            stored_island,
            _,
            repeated_count
        ) = struct.unpack_from("<HBBIB", payload, record)

        if enabled != 1:
            raise ValueError(f"Bone palette record {record_index} has unsupported state {enabled}")

        if stored_island >= num_islands:
            raise ValueError(f"Bone palette record {record_index} targets invalid island {stored_island}")

        if repeated_count != bone_count:
            raise ValueError(f"Bone palette record {record_index} has conflicting counts")

        start = record + 9
        end = start + bone_count

        if end > len(payload):
            raise ValueError(f"Bone palette record {record_index} data is truncated")

        if palettes[stored_island] is not None:
            raise ValueError(f"Duplicate bone palette for island {stored_island}")

        palettes[stored_island] = tuple(payload[start:end])

    if any(palette is None for palette in palettes):
        raise ValueError("One or more mesh islands have no bone palettes")

    return tuple(palettes)

def normalize_direction(value: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(sum(component * component for component in value))

    if length <= 1e-8:
        raise ValueError("Mesh contains a zero length direction")

    return tuple(component / length for component in value)

def read_tangent_frame(payload, normal_offset, num_vertices):
    """Decode Siege normal, tangent and binormal vertex blocks"""

    tangent_offset = normal_offset + num_vertices * 4
    binormal_offset = tangent_offset + num_vertices * 4

    normals = []
    tangents = []

    for index in range(num_vertices):
        def read_direction(offset):
            values = struct.unpack_from("<3B", payload, offset + index * 4)

            return normalize_direction(tuple(component / 127.5 - 1.0 for component in values))

        normal = read_direction(normal_offset)
        tangent = read_direction(tangent_offset)
        binormal = read_direction(binormal_offset)

        # Remove quantization induced normal components from the tangent
        projection = sum(
            normal_component * tangent_component
            for normal_component, tangent_component in zip(normal, tangent)
        )

        orthogonal_tangent = tuple(
            tangent_component - normal_component * projection
            for tangent_component, normal_component in zip(tangent, normal)
        )

        tangent_length_squared = sum(component * component for component in orthogonal_tangent)

        if tangent_length_squared <= 1e-16:
            reference = (
                (0.0, 0.0, 1.0)
                if abs(normal[2]) < 0.999
                else (0.0, 1.0, 0.0)
            )

            orthogonal_tangent = (
                reference[1] * normal[2]
                - reference[2] * normal[1],
                reference[2] * normal[0]
                - reference[0] * normal[2],
                reference[0] * normal[1]
                - reference[1] * normal[0]
            )

        tangent = normalize_direction(orthogonal_tangent)

        cross = (
            normal[1] * tangent[2] - normal[2] * tangent[1],
            normal[2] * tangent[0] - normal[0] * tangent[2],
            normal[0] * tangent[1] - normal[1] * tangent[0]
        )

        handedness = (
            1.0
            if sum(cross_component * binormal_component for cross_component, binormal_component in zip(cross, binormal)) >= 0.0
            else -1.0
        )

        normals.append(normal)
        tangents.append(
            (
                tangent[0],
                tangent[1],
                tangent[2],
                handedness
            )
        )

    return normals, tangents

def read_mesh_with_islands(payload):
    mPayload = payload.find(COMPILED_MESH)
    if mPayload == -1:
        raise ValueError("Not a mesh payload")
    p = mPayload + 4
    f = struct.unpack("<20I", payload[p:p + 80])
    vert_len, verts_len, face_len = f[3], f[4], f[5]
    num_islands = f[15] # f[13]=numLods, f[15]=numIslands
    if vert_len not in _PRE_UV:
        raise ValueError(f"Unsupported vertLen 0x{vert_len:X}")
    num_verts = verts_len // vert_len
    vbo = p + 80 # vertices start right after 20 header fields
    tris = vbo + verts_len # face block follows vertex block

    # positions
    verts = []
    if vert_len in FLOAT_VERT_LENS:
        for i in range(num_verts):
            verts.append(struct.unpack_from("<fff", payload, vbo + i * 12))
    else:
        for i in range(num_verts): # 4x int16: x, y, z, scale
            x, y, z, s = struct.unpack_from("<hhhh", payload, vbo + i * 8)
            verts.append((x * s / 32767.0, y * s / 32767.0, z * s / 32767.0))

    # normals: 4 bytes per vertex, planar block after positions
    nrm_off = vbo + num_verts * (12 if vert_len in FLOAT_VERT_LENS else 8)
    normals, tangents = read_tangent_frame(payload, nrm_off, num_verts)

    # UVs: 2 half floats per vertex, planar block after pos/normal/tangent/binormal(/color)
    uv_block = vbo + num_verts * _PRE_UV[vert_len]
    uvs = []
    for i in range(num_verts):
        u, v = struct.unpack_from("<ee", payload, uv_block + i * 4) # <e = float16
        uvs.append((u, 1.0 - v))

    # LOD0 faces only, ObjectHeader table (footer) sits after all 6 data blocks
    tail = vbo + sum(f[4:10]) # verts+face+vertmaps+unk1+faceStat+faceUnk

    joints, weights = read_skin_weights(payload, vbo, verts_len, num_verts, vert_len)

    islands = read_lod0_islands(payload, tris, tail, num_islands)

    if joints:
        palettes = read_bone_palettes(payload, tail, f[13], num_islands)

        islands = tuple(
            MeshIsland(
                material_id=island.material_id,
                faces=island.faces,
                bone_palette=palette
            )
            for island, palette in zip(islands, palettes)
        )

    return verts, uvs, normals, tangents, joints, weights, islands