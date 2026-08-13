# Parse .forge mesh payloads

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
    """Read the global bone IDs used by each LOD0 material island"""

    palette_table = tail + num_lods * num_islands * 36 + num_islands * 32

    palettes = []

    for island_index in range(num_islands):
        record = palette_table + island_index * 268

        if record + 10 > len(payload):
            raise ValueError(f"Bone palette {island_index} is truncated")

        (
            enabled,
            bone_count,
            stored_island,
            _,
            repeated_count
        ) = struct.unpack_from("<HBBIH", payload, record)

        if enabled != 1:
            raise ValueError(f"Bone palette {island_index} has unsupported state {enabled}")

        if stored_island != island_index:
            raise ValueError(f"Expected bone palette {island_index}, found {stored_island}")

        if repeated_count != bone_count:
            raise ValueError(f"Bone palette {island_index} has conflicting counts {bone_count} and {repeated_count}")

        start = record + 10
        end = start + bone_count

        if end > len(payload):
            raise ValueError(f"Bone palette {island_index} data is truncated")

        palettes.append(tuple(payload[start:end]))

    return tuple(palettes)

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
    normals = []
    for i in range(num_verts):
        x, y, z, _ = struct.unpack_from("<BBBB", payload, nrm_off + i * 4)
        normals.append((x / 127.0 -1, y / 127.0 - 1, z / 127.0 - 1))

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

    return verts, uvs, normals, joints, weights, islands