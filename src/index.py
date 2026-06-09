import os
import struct
from src.parser import CONTAINER_MAGIC
from src.decompress import oodle_decompress

def first_chunk(data, off):
    p = off + 15
    num_chunks = struct.unpack_from("<I", data, p)[0]
    p += 4
    u0, p0 = struct.unpack_from("<II", data, p)
    p += 8 * num_chunks + 4
    blob = data[p:p + p0]
    return oodle_decompress(blob, u0) if u0 > p0 else blob

def read_meta(payload):
    name_len = struct.unpack_from("<H", payload, 0)[0]
    p = 8 + name_len
    file_type, = struct.unpack_from("<I", payload, p)
    uid,       = struct.unpack_from("<Q", payload, p + 4)
    return uid, file_type

def build_index(forge_paths):
    index = {}
    for path in forge_paths:
        with open(path, "rb") as f:
            data = f.read()
        i = 0
        while True:
            j = data.find(CONTAINER_MAGIC, i)
            if j == -1:
                break
            i = j + 8
            try:
                uid, ft = read_meta(first_chunk(data, j))
                index[uid] = (ft, path, j)
            except Exception:
                pass
    return index