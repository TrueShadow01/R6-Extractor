"""Read Rainbow Six Siege dependency graphs"""

from __future__ import annotations

import struct
from collections import defaultdict
from pathlib import Path

from src.parser import (
    CONTAINER_MAGIC,
    read_container
)

def load_depgraph(path: str | Path) -> dict[int, list[int]]:
    """Return child UIDs grouped by parent UID"""

    data = Path(path).read_bytes()

    if data[:8] != CONTAINER_MAGIC:
        raise ValueError("Not a depgraph container")

    # Dependency graph payloads begin with one flag byte
    body = read_container(data, 0)[1:]

    if len(body) % 24:
        raise ValueError("Depgraph body is not a multiple of 24 bytes")

    children: defaultdict[
        int,
        list[int]
    ] = defaultdict(list)

    for parent, child, _ in struct.iter_unpack("<QQQ", body):
        children[parent].append(child)

    return dict(children)