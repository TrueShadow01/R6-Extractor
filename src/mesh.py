# Parse .forge mesh payloads. WIP

import struct

COMPILED_MESH = struct.pack("<I", 0xFC9E1595)

# Anchor on CompiledMesh (0xFC9E1595), the header u32 fields follow the magic
def parse_mesh_header(payload):
    md = payload.find(COMPILED_MESH)
    if md == -1:
        raise ValueError("Not a Mesh Payload (no CompiledMesh Magic)")
    p = md + 4 # skip magic, header fields

    def u32(off):
        return struct.unpack("<I", payload[p + off:p + off + 4])[0]
    
    h = {
        "magic_off": md,
        "size_until_footer": u32(0),
        "revision": u32(8),
        "vert_len": u32(12),
        "verts_data_len": u32(16),
        "face_data_len": u32(20),
        "vertmaps_len": u32(24),
        "num_lods": u32(52),
        "mesh_type": struct.unpack("<i", payload[p + 56:p + 60])[0],
        "num_islands": u32(60),
    }
    h["num_verts"] = h["verts_data_len"] // h["vert_len"]
    return h