"""FileMetadata parsing for current Rainbow Six Siege Forge payloads"""

from __future__ import annotations

import struct
from dataclasses import dataclass

class InvalidFileMetadata(ValueError):
    """Raised when a payload is not a modern Siege file payload"""

@dataclass(frozen=True)
class FileMetadata:
    name_length: int
    container_type: int
    flags: int
    name_hash: bytes
    file_type: int
    uid: int
    data_offset: int

def parse_file_metadata(payload: bytes) -> FileMetadata:
    """Parse the FileMetadata header at the start of an asset payload"""

    if len(payload) < 20:
        raise InvalidFileMetadata("Payload is too short for FileMetadata")

    name_length, container_type, flags = struct.unpack_from("<HHI", payload, 0)

    # Modern file payloads use container type 2. Small companion
    # Metadata containers use other values and must not be indexed
    if container_type != 2:
        raise InvalidFileMetadata(f"Container type {container_type} is not a file payload")

    if name_length > 4096:
        raise InvalidFileMetadata(f"Implausible name length {name_length}")

    type_offset = 8 + name_length
    required_size = type_offset + 12

    if required_size > len(payload):
        raise InvalidFileMetadata(f"Metadata needs {required_size} bytes, payload contains {len(payload)}")

    file_type = struct.unpack_from("<I", payload, type_offset)[0]

    uid = struct.unpack_from("<Q", payload, type_offset + 4)[0]

    return FileMetadata(
        name_length=name_length,
        container_type=container_type,
        flags=flags,
        name_hash=payload[8:type_offset],
        file_type=file_type,
        uid=uid,
        data_offset=required_size
    )