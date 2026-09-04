"""Read default operator equipment from the inspected Siege registry layout"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from pathlib import Path

from src.metadata import InvalidFileMetadata, parse_file_metadata
from src.parser import iter_container_offsets, map_archive, read_container

REGISTRY_UID = 0x0000005B9ACA7913
REGISTRY_TYPE = 0xFA676C75
ROSTER_COUNT_OFFSET = 5932 # Relative to the registry data, not the archive
OPERATOR_TYPE = 0xFD67B59C
BODY_TYPE = 0x3B60D129
HEAD_TYPE = 0xDF39B87F
APPEARANCE_TYPE = 0x533975EC
NAME_TYPE = 0x112E7C4A
DEFAULT_NAME_KEY = 0x6500000000031543
SLOT_TAG = bytes.fromhex("081bd4c0")
TEXT_TAG = bytes.fromhex("4dcec95f")
KEEP_TYPES = {
    REGISTRY_TYPE, OPERATOR_TYPE, BODY_TYPE, HEAD_TYPE,
    APPEARANCE_TYPE, NAME_TYPE, 0xCF9144A0, 0xD672A5BA,
}
NAME_ALIASES = {
    "HEIST": "Denari",
    "FIREWORKS": "Noor",
    "PANACHE": "Sens",
    "DIAMOND": "Solid Snake",
    "IQ": "IQ",
}

@dataclass(frozen=True)
class RegistryRecord:
    uid: int
    file_type: int
    container_offset: int
    payload_offset: int
    data: bytes

@dataclass(frozen=True)
class DefaultPart:
    item_uid: int
    appearance_uid: int
    model_groups: tuple[tuple[int, ...], ...]

@dataclass(frozen=True)
class DefaultOperator:
    uid: int
    name: str
    container_offset: int
    payload_offset: int
    head: DefaultPart
    body: DefaultPart

def _unpack(fmt: str, data: bytes, offset: int) -> tuple:
    size = struct.calcsize(fmt)
    if offset < 0 or offset + size > len(data):
        raise(ValueError(f"Truncated registry field at payload offset {offset}"))

    return struct.unpack_from(fmt, data, offset)

def _u32(data: bytes, offset: int) -> int:
    return _unpack("<I", data, offset)[0]

def _u64(data: bytes, offset: int) -> int:
    return _unpack("<Q", data, offset)[0]

def _read_records(archive: Path) -> dict[int, list[RegistryRecord]]:
    records: dict[int, list[RegistryRecord]] = {}

    with map_archive(archive) as mapped:
        for container_offset in iter_container_offsets(mapped):
            payload = read_container(mapped, container_offset)
            try:
                parse_file_metadata(payload)
            except InvalidFileMetadata:
                continue

            headers = []
            for match in re.finditer(b"\x02\x00", payload):
                offset = match.start() - 2
                if offset < 0:
                    continue

                name_length = _unpack("<H", payload, offset)[0]
                type_offset = offset + 8 + name_length
                if name_length > 4096 or type_offset + 16 > len(payload):
                    continue

                file_type, uid, repeated_type = _unpack("<IQI", payload, type_offset)
                if file_type and file_type == repeated_type:
                    headers.append((offset, type_offset + 12, file_type, uid))

            for index, (offset, start, file_type, uid) in enumerate(headers):
                if not uid or file_type not in KEEP_TYPES:
                    continue

                end = (
                    headers[index + 1][0]
                    if index + 1 < len(headers) else len(payload)
                )
                record = RegistryRecord(uid, file_type, container_offset, offset, payload[start:end])
                variants = records.setdefault(uid, [])
                if not any(old.file_type == file_type and old.data == record.data for old in variants):
                    variants.append(record)

    return records

def _record(records: dict[int, list[RegistryRecord]], uid: int, file_type: int) -> RegistryRecord:
    variants = records.get(uid, [])
    if len(variants) != 1 or variants[0].file_type != file_type:
        raise ValueError(
            f"Missing, conflicting or unexpected registry record {uid:016X}, expected type {file_type:08X}"
        )
    return variants[0]

def _localized_text(data: bytes) -> tuple[str, int]:
    offset = data.find(TEXT_TAG)
    if offset < 0:
        raise ValueError("Registry UI record has no localized text field")

    length = _u32(data, offset + 4)
    start = offset + 8
    end = start + length
    if length > 4096 or end > len(data):
        raise ValueError("Invalid registry text length")

    text = data[start:end].decode("utf-8")
    if length:
        if end >= len(data) or data[end] != 0:
            raise ValueError("Registry text is missing its terminator")
        end += 1
    return text, _u64(data, end)

def _default_part(records: dict[int, list[RegistryRecord]], item_uid: int, slot: int) -> DefaultPart:
    item_type = BODY_TYPE if slot == 0 else HEAD_TYPE
    ui_type = 0xD672A5BA if slot == 0 else 0xCF9144A0
    item = _record(records, item_uid, item_type)
    ui = _record(records, _u64(item.data, 53), ui_type)
    if _localized_text(ui.data)[1] != DEFAULT_NAME_KEY:
        raise ValueError(f"Item {item_uid:016X} is not labeled as a base equipment")

    appearance_uid = _u64(item.data, 8)
    appearance = _record(records, appearance_uid, APPEARANCE_TYPE)
    groups = []
    cursor = 79
    for _ in range(7):
        count = _u32(appearance.data, cursor)
        cursor += 4
        if count > 4096:
            raise ValueError(f"Invalid model count in {appearance_uid:016X}")

        group = _unpack(f"<{count}Q", appearance.data, cursor)
        cursor += count * 8
        if any(uid == 0 for uid in group):
            raise ValueError(f"Null model UID in {appearance_uid:016X}")

        groups.append(group)

    if appearance.data[cursor:] != b"\x00" or not groups[0]:
        raise ValueError(f"Unsupported appearance layout {appearance_uid:016X}")

    return DefaultPart(item_uid, appearance_uid, tuple(groups))

def read_operator_registry(archive: str | Path) -> tuple[DefaultOperator, ...]:
    records = _read_records(Path(archive).expanduser().resolve())
    registry = _record(records, REGISTRY_UID, REGISTRY_TYPE)
    count = _u32(registry.data, ROSTER_COUNT_OFFSET)
    if not 1 <= count <= 512:
        raise ValueError("Unsupported operator roster count or registry layout")

    roster = _unpack(f"<{count}Q", registry.data, ROSTER_COUNT_OFFSET + 4)
    if len(set(roster)) != count:
        raise ValueError("Duplicate UIDs in the operator roster")

    operators = []
    for uid in roster:
        operator = _record(records, uid, OPERATOR_TYPE)
        dependency_count = _u32(operator.data, 4)
        if dependency_count > 4096:
            raise ValueError(f"Invalid operator header {uid:016X}")

        base = 8 + dependency_count * 8
        slots = []
        for slot in range(23):
            offset = base + 32 + slot * 37
            if operator.data[offset:offset + 4] != SLOT_TAG or _unpack("<B", operator.data, offset + 12)[0] != slot:
                raise ValueError(f"Unsupported equipment layout {uid:016X}")

            slots.append(_u64(operator.data, offset + 4))

        name_record = _record(records, _u64(operator.data, base + 888), NAME_TYPE)
        label, key = _localized_text(name_record.data)
        label = label.lstrip("!")
        name = "Thorn" if key == 0x65000000000554DD else NAME_ALIASES.get(label, label.title())
        if not name:
            name = f"Unknown-{uid:016X}"

        operators.append(DefaultOperator(
            uid=uid,
            name=name,
            container_offset=operator.container_offset,
            payload_offset=operator.payload_offset,
            head=_default_part(records, slots[1], 1),
            body=_default_part(records, slots[0], 0)
        ))

    return tuple(operators)