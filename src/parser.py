import struct

from src.decompress import oodle_decompress

CONTAINER_MAGIC = bytes.fromhex("37aafb5799fa1510")

def parse_header(path):
    with open(path, "rb") as f:
        if f.read(9) != b"scimitar\x00":
            raise ValueError("Magic invalid, not a scimitar file")
    print("Magic OK")

def read_container(data, offset):
    p = offset + 8 # skip container magic
    p += 2 # u16 type
    p += 2 # u16 (0x0F)
    p += 1 # u8 (0)
    p += 2 # u16 xD

    num_chunks = struct.unpack("<I", data[p:p + 4])[0]
    p += 4

    chunks = []
    for _ in range(num_chunks):
        unpacked = struct.unpack("<I", data[p:p + 4])[0]
        packed = struct.unpack("<I", data[p + 4:p + 8])[0]
        chunks.append((unpacked, packed))
        p += 8

        out = bytearray()
        for unpacked, packed in chunks:
            p += 4 # u32 hash 
            blob = data[p:p + packed]
            p += packed
            if unpacked > packed:
                out += oodle_decompress(blob, unpacked)
            else:
                out += blob
        
        return bytes(out)