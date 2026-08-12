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

    islands = read_lod0_islands(payload, tris, tail, num_islands)
    return verts, uvs, normals, islands

def read_mesh(payload):
    """Compatibility wrapper returning the previous flattened face list"""

    verts, uvs, normals, islands = read_mesh_with_islands(payload)

    faces = [
        face
        for island in islands
        for face in island.faces
    ]

    return verts, uvs, normals, faces

def save_obj(path, payload):
    verts, uvs, normals, faces = read_mesh(payload)
    with open(path, "w") as fh:
        for x, y, z in verts:
            fh.write(f"v {x:.6f} {y:.6f} {z:.6f}\n")
        for u, v in uvs:
            fh.write(f"vt {u:.6f} {v:.6f}\n")
        for nx, ny, nz in normals:
            fh.write(f"vn {nx:.6f} {ny:.6f} {nz:.6f}\n")
        for a, b, c in faces:
            fh.write(f"f {a + 1}/{a + 1}/{a + 1} {b + 1}/{b + 1}/{b + 1} {c + 1}/{c + 1}/{c + 1}\n")
    return len(verts), len(faces)