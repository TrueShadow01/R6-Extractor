# Parse .forge mesh payloads

import struct

COMPILED_MESH = struct.pack("<I", 0xFC9E1595)
FLOAT_VERT_LENS = (0x24, 0x28, 0x2C) # float 32 position layouts
PACKED_VERT_LENS = (0x18, 0x1C) # int16 + scale packed positions

# Decode mesh payload
def read_mesh(payload):
    mPayload = payload.find(COMPILED_MESH)
    if mPayload == -1:
        raise ValueError("Not a mesh payload")
    p = mPayload + 4
    f = struct.unpack("<20I", payload[p:p + 80])
    vert_len, verts_len, face_len = f[3], f[4], f[5]
    if vert_len == 0:
        raise ValueError("Bad Mesh Header")
    num_verts = verts_len // vert_len
    vbo = p + 80 # vertices start right after 20 header fields
    tris = vbo + verts_len # face block follows vertex block

    verts = []
    if vert_len in FLOAT_VERT_LENS:
        for i in range(num_verts):
            verts.append(struct.unpack_from("<fff", payload, vbo + i * 12))
    elif vert_len in PACKED_VERT_LENS:
        for i in range(num_verts): # 4x int16: x, y, z, scale
            x, y, z, s = struct.unpack_from("<hhhh", payload, vbo + i * 8)
            verts.append((x * s / 32767.0, y * s / 32767.0, z * s / 32767.0))
    else:
        raise ValueError(f"Unsupported vertLen{vert_len:X}")

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