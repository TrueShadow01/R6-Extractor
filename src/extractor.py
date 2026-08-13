"""Lossless, resumable extraction of validated Forge assets"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from src.index import (
    AssetRecord,
    ScanDiagnostics,
    scan_archive
)

from src.parser import (
    map_archive,
    parse_container,
    read_container
)

@dataclass
class ExtractionSummary:
    archive: Path
    scanned_assets: int = 0
    extracted: int = 0
    resumed: int = 0
    failed: int = 0
    bytes_written: int = 0
    scan_errors: int = 0

class Manifest:
    """Append-only JSONL extraction manifest"""

    COMPLETE_STATUSES = {
        "extracted",
        "existing"
    }

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.completed: dict[str, dict] = {}

        if self.path.is_file():
            self._load()

    def _load(self) -> None:
        with self.path.open("r", encoding="utf-8", errors="replace") as stream:
            for line in stream:
                line = line.strip()

                if not line:
                    continue

                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    # Ignore a partial final line left by interruption
                    continue

                if entry.get("status") in self.COMPLETE_STATUSES:
                    key = entry.get("key")

                    if key:
                        self.completed[key] = entry

    def is_complete(self, key: str, output_root: Path, expected_size: int) -> bool:
        entry = self.completed.get(key)

        if entry is None:
            return False

        relative_output = entry.get("output")

        if not relative_output:
            return False

        output = output_root / relative_output

        try:
            return output.is_file() and output.stat().st_size == expected_size
        except OSError:
            return False

    def append(self, entry: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)

        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line)
            stream.write("\n")
            stream.flush()

        if entry.get("status") in self.COMPLETE_STATUSES:
            self.completed[entry["key"]] = entry

def _record_key(record: AssetRecord) -> str:
    return f"{record.archive.resolve()}::{record.container_offset:016X}"

def _output_path(output_root: Path, record: AssetRecord) -> Path:
    archive_directory = output_root / record.archive.stem

    filename = f"{record.uid:016X}_{record.file_type:08X}_{record.container_offset:016X}.bin"

    return archive_directory / filename

def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    temporary = path.with_name(path.name + ".part")

    try:
        with temporary.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

def _manifest_entry(record: AssetRecord, output_root: Path, output: Path, status: str, error: str | None = None) -> dict:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "key": _record_key(record),
        "archive": str(record.archive),
        "container_offset": f"0x{record.container_offset:X}",
        "uid": f"0x{record.uid:016X}",
        "file_type": f"0x{record.file_type:08X}",
        "unpacked_size": record.unpacked_size,
        "status": status,
        "output": output.relative_to(output_root).as_posix(),
        "error": error
    }

def extract_raw_archive(archive: str | Path, output_root: str | Path, *, resume: bool = True, verbose: bool = False, progress: Callable[[AssetRecord, str], None] | None = None) -> ExtractionSummary:
    """Extract every validated asset as a decompressed binary"""

    archive = Path(archive).resolve()
    output_root = Path(output_root).resolve()

    output_root.mkdir(parents=True, exist_ok=True)

    manifest = Manifest(output_root / "manifest.jsonl")

    diagnostics = ScanDiagnostics()
    records = list(scan_archive(archive, diagnostics))
    summary = ExtractionSummary(archive=archive, scanned_assets=len(records), scan_errors=(
        diagnostics.invalid_containers + diagnostics.metadata_errors
    ))

    with map_archive(archive) as data:
        for record in records:
            key = _record_key(record)
            output = _output_path(output_root, record)

            if (resume and manifest.is_complete(key, output_root, record.unpacked_size)):
                summary.resumed += 1
                status = "resumed"

                if verbose:
                    print(f"resume {record.uid:016X} -> {output.name}")

                if progress:
                    progress(record, status)

                continue

            if (resume and output.is_file() and output.stat().st_size == record.unpacked_size):
                entry = _manifest_entry(record, output_root, output, "existing")
                manifest.append(entry)
                summary.resumed += 1

                if verbose:
                    print(f"existing {record.uid:016X} -> {output.name}")

                if progress:
                    progress(record, "existing")

                continue

            try:
                container = parse_container(data, record.container_offset)
                payload = read_container(data, container)

                if len(payload) != record.unpacked_size:
                    raise ValueError(f"Decoded {len(payload)} bytes, expected {record.unpacked_size}")

                _atomic_write(output, payload)
                manifest.append(_manifest_entry(record, output_root, output, "extracted"))

                summary.extracted += 1
                summary.bytes_written += len(payload)
                status = "extracted"

                if verbose:
                    print(f"extract {record.uid:016X} type={record.file_type:08X} size={len(payload)} -> {output.name}")
            except Exception as error:
                manifest.append(_manifest_entry(record, output_root, output, "error", str(error)))
                summary.failed += 1
                status = "error"
                print(f"error {record.archive.name} offset=0x{record.container_offset:X}: {error}")

            if progress:
                progress(record, status)

    return summary