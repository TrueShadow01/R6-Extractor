""" Validated, bounded memory parsing for Siege Scimitar containers"""

from __future__ import annotations

import mmap
import struct
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from src.decompress import oodle_decompress

SCIMITAR_MAGIC = b"scimitar\x00"
CONTAINER_MAGIC = bytes.fromhex("37aafb5799fa1510")
MAX_CHUNKS = 1_000_000

class ForgeFormatError(ValueError):
    """Raised when a Forge structure is truncated or invalid"""

@dataclass(frozen=True)
class ChunkInfo:
    unpacked_size: int
    packed_size: int
    checksum: int
    data_offset: int

    @property
    def compressed(self) -> bool:
        return self.unpacked_size > self.packed_size

@dataclass(frozen=True)
class ContainerInfo:
    offset: int
    end_offset: int
    block_type: int
    marker: int
    flag: int
    descriptor: int
    chunks: tuple[ChunkInfo, ...]

    @property
    def unpacked_size(self) -> int:
        return sum(chunk.unpacked_size for chunk in self.chunks)

    @property
    def packed_size(self) -> int:
        return sum(chunk.packed_size for chunk in self.chunks)

def _require(data, offset: int, size: int, label: str) -> None:
    if offset < 0 or size < 0 or offset + size > len(data):
        raise ForgeFormatError(f"Truncated {label} at 0x{offset:X}: need {size} bytes, archive has {len(data)}")

def parse_header(path: str | Path) -> None:
    """Validate the outer Scimitar file signature"""

    with open(path, "rb") as stream:
        magic = stream.read(len(SCIMITAR_MAGIC))

    if magic != SCIMITAR_MAGIC:
        raise ForgeFormatError(f"Invalid Scimitar magic in {path}")

    print("Magic OK")

@contextmanager
def map_archive(path: str | Path) -> Iterator[mmap.mmap]:
    """Map an archive without loading the whole file into Python memory"""

    with open(path, "rb") as stream:
        stream.seek(0, 2)
        file_size = stream.tell()

        if file_size == 0:
            raise ForgeFormatError(f"Archive is empty: {path}")

        stream.seek(0)

        with mmap.mmap(stream.fileno(), length=0, access=mmap.ACCESS_READ) as data:
            yield data

def iter_container_offsets(data, start: int = 0) -> Iterator[int]:
    """Find every current Siege container signature"""

    offset = max(0, start)

    while True:
        offset = data.find(CONTAINER_MAGIC, offset)

        if offset < 0:
            return

        yield offset
        offset += len(CONTAINER_MAGIC)

def parse_container(data, offset: int) -> ContainerInfo:
    """Parse and bounds check a container without decompressing it"""

    _require(data, offset, 19, "container header")

    if data[offset:offset + 8] != CONTAINER_MAGIC:
        raise ForgeFormatError(f"Container magic missing at 0x{offset:X}")

    block_type, marker = struct.unpack_from("<HH", data, offset + 8)
    flag = data[offset + 12]
    descriptor = struct.unpack_from("<H", data, offset + 13)[0]
    num_chunks = struct.unpack_from("<I", data, offset + 15)[0]

    if num_chunks > MAX_CHUNKS:
        raise ForgeFormatError(f"Implausible chunk count {num_chunks} at 0x{offset:X}")

    table_offset = offset + 19
    table_size = num_chunks * 8

    _require(data, table_offset, table_size, "chunk-size table")

    sizes: list[tuple[int, int]] = []

    for index in range(num_chunks):
        unpacked_size, packed_size = struct.unpack_from("<II", data, table_offset + index * 8)

        if unpacked_size < packed_size:
            raise ForgeFormatError(f"Chunk {index} at 0x{offset:X} has packed size {packed_size} larger than unpacked size {unpacked_size}")

        sizes.append((unpacked_size, packed_size))

    cursor = table_offset + table_size
    chunks: list[ChunkInfo] = []

    for index, (unpacked_size, packed_size) in enumerate(sizes):
        _require(data, cursor, 4, f"chunk {index} checksum")

        checksum = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4

        _require(data, cursor, packed_size, f"chunk {index} payload")

        chunks.append(
            ChunkInfo(
                unpacked_size=unpacked_size,
                packed_size=packed_size,
                checksum=checksum,
                data_offset=cursor
            )
        )

        cursor += packed_size

    return ContainerInfo(
        offset=offset,
        end_offset=cursor,
        block_type=block_type,
        marker=marker,
        flag=flag,
        descriptor=descriptor,
        chunks=tuple(chunks)
    )


def _decode_chunk(data, chunk: ChunkInfo, decompressor: Callable[[bytes, int], bytes]) -> bytes:
    start = chunk.data_offset
    end = start + chunk.packed_size
    blob = bytes(data[start:end])

    if chunk.compressed:
        decoded = decompressor(blob, chunk.unpacked_size)
    else:
        decoded = blob

    if len(decoded) != chunk.unpacked_size:
        raise ForgeFormatError(f"Chunk at 0x{chunk.data_offset:X} decoded to {len(decoded)} bytes, expected {chunk.unpacked_size}")

    return decoded

def read_first_chunk(data, container: int | ContainerInfo, decompressor: Callable[[bytes, int], bytes] = oodle_decompress) -> bytes:
    """Decode only the first chunk, primarly for metadata indexing"""

    if isinstance(container, int):
        info = parse_container(data, container)
    else:
        info = container

    if not info.chunks:
        raise ForgeFormatError(f"Container at 0x{info.offset:X} contains no chunks")

    return _decode_chunk(data, info.chunks[0], decompressor)

def read_container(data, container: int | ContainerInfo, decompressor: Callable[[bytes, int], bytes] = oodle_decompress) -> bytes:
    """Reassemble one complete decompressed container payload"""

    if isinstance(container, int):
        info = parse_container(data, container)
    else:
        info = container

    output = bytearray()

    for chunk in info.chunks:
        output.extend(
            _decode_chunk(data, chunk, decompressor)
        )

    return bytes(output)