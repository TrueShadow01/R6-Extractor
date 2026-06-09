import struct
from collections import defaultdict
from src.parser import read_container, CONTAINER_MAGIC

def load_depgraph(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != CONTAINER_MAGIC:
        raise ValueError("Not a depgraph container")
    body = read_container(data, 0)[1:] # skip leading flag byte
    if len(body) % 24:
        raise ValueError("Depgraph body not a multiple of 24 bytes")

    children = defaultdict(list) # parent uid -> [child uids]
    parents = defaultdict(list) # child uid -> [parent uids]
    for k in range(len(body) // 24):
        parent, child = struct.unpack_from("<QQ", body, k * 24) # u64 parent, u64 child
        children[parent].append(child)
        parents[child].append(parent)
    return children, parents