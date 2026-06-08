# Parse .forge mesh payloads

import struct

COMPILED_MESH = struct.pack("<I", 0xFC9E1595)
FLOAT_VERT_LENS = (0x24, 0x28, 0x2C) # float 32 position layouts

# Decode mesh payload -> (vertices, faces)
#   float positions only
def read_mesh(payload):
    mPayload = payload.find(COMPILED_MESH)
    if mPayload == -1:
        raise ValueError("Not a mesh payload")
    p = mPayload + 4
    f = struct.unpack("<20I", payload[p:p + 80])
    vert_len, verts_len, face_len = f[3], f[4], f[5]
    if vert_len not in FLOAT_VERT_LENS:
        raise ValueError(f"Unsupported vertLen 0x{vert_len:X}") # packed int16, skip
    num_verts = verts_len // vert_len

    vbo = p + 80 # vertices start right after 20 header fields
    tris = vbo + verts_len # face block follows vertex block

    # positions, planar float32 x3 (vertLen 0x24/0x28/0x2C)
    verts = [struct.unpack_from("<fff", payload, vbo + i * 12) for i in range(num_verts)]

    # faces, global triangle indices, skip degenerate triangles
    faces = []
    for t in range(face_len // 6):
        a, b, c = struct.unpack_from("<HHH", payload, tris + t * 6)
        if a != b and b != c and a != c:
            faces.append((a, b, c))
    return verts, faces

def save_obj(path, payload):
    verts, faces = read_mesh(payload)
    with open(path, "w") as fh:
        for x, y, z in verts:
            fh.write(f"v {x:.6f} {y:.6f} {z:.6f}")
        for a, b, c in faces:
            fh.write(f"f {a + 1} {b + 1} {c + 1}") # OBJ 1 indexed
    return len(verts), len(faces)