"""Validated UID indexing for Rainbow Six Siege Forge archives"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

from src.decompress import OodleUnavailableError
from src.metadata import (
    FileMetadata,
    InvalidFileMetadata,
    parse_file_metadata
)
from src.parser import (
    ContainerInfo,
    ForgeFormatError,
    iter_container_offsets,
    map_archive,
    parse_container,
    read_first_chunk
)

@dataclass
class AssetRecord:
    uid: int
    file_type: int
    archive: Path
    container_offset: int
    container_size: int
    unpacked_size: int
    metadata: FileMetadata

@dataclass
class ScanDiagnostics:
    containers: int = 0
    assets: int = 0
    auxiliary_containers: int = 0
    invalid_containers: int = 0
    metadata_errors: int = 0
    errors: Counter[str] = field(default_factory=Counter)

    def add_error(self, category: str, error: Exception) -> None:
        message = (f"{category}: {type(error).__name__}: {error}")
        self.errors[message] += 1

@dataclass
class AssetIndex:
    by_uid: dict[int, list[AssetRecord]] = field(default_factory=dict)
    diagnostics: dict[Path, ScanDiagnostics] = field(default_factory=dict)

    def add(self, record: AssetRecord) -> None:
        self.by_uid.setdefault(record.uid, []).append(record)

    def primary(self, uid: int) -> AssetRecord | None:
        records = self.by_uid.get(uid)

        if not records:
            return None

        # the old dictionary index retained the last occurrence
        return records[-1]

    def records(self) -> Iterator[AssetRecord]:
        for records in self.by_uid.values():
            yield from records

    def __len__(self) -> int:
        return len(self.by_uid)

    def __contains__(self, uid: int) -> bool:
        return uid in self.by_uid

    @property
    def total_records(self) -> int:
        return sum(len(records) for records in self.by_uid.values())

    @property
    def duplicate_uids(self) -> int:
        return sum(1 for records in self.by_uid.values() if len(records) > 1)

def _record_from_container(archive: Path, data, container: ContainerInfo) -> AssetRecord:
    first_chunk = read_first_chunk(data, container)
    metadata = parse_file_metadata(first_chunk)

    return AssetRecord(
        uid=metadata.uid,
        file_type=metadata.file_type,
        archive=archive,
        container_offset=container.offset,
        container_size=(container.end_offset - container.offset),
        unpacked_size=container.unpacked_size,
        metadata=metadata
    )

def scan_archive(path: str | Path, diagnostics: ScanDiagnostics | None = None) -> Iterator[AssetRecord]:
    """Yield every validated file asset from one archive"""

    archive = Path(path).resolve()

    if diagnostics is None:
        diagnostics = ScanDiagnostics()

    with map_archive(archive) as data:
        for offset in iter_container_offsets(data):
            diagnostics.containers += 1

            try:
                container = parse_container(data, offset)
            except ForgeFormatError as error:
                diagnostics.invalid_containers += 1
                diagnostics.add_error("container", error)
                continue

            try:
                record = _record_from_container(archive, data, container)
            except InvalidFileMetadata:
                # Companion metadata containers are expected and
                # are deliberately excluded from the asset index
                diagnostics.auxiliary_containers += 1
                continue
            except OodleUnavailableError:
                # A missing runtime affects the entire scan rather
                # than one malformed asset
                raise
            except Exception as error:
                diagnostics.metadata_errors += 1
                diagnostics.add_error("metadata", error)
                continue

            diagnostics.assets += 1
            yield record


def build_index(forge_paths: Iterable[str | Path]) -> AssetIndex:
    index = AssetIndex()

    for path in forge_paths:
        archive = Path(path).resolve()
        diagnostics = ScanDiagnostics()
        index.diagnostics[archive] = diagnostics

        for record in scan_archive(archive, diagnostics):
            index.add(record)

    return index