"""Parse decimal community UIDs and hexadecimal extractor UIDs"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from src.database import (
    AssetName,
    uid_to_text,
    upsert_asset_names
)

_UID_COLUMNS = {
    "uid",
    "id",
    "assetuid",
    "objectuid"
}

_NAME_COLUMNS = {
    "name",
    "assetname",
    "objectname",
    "description"
}

_CATEGORY_COLUMN = {
    "category",
    "assettype",
    "type"
}

_SOURCE_COLUMNS = {
    "source"
}

_CONFIDENCE_COLUMN = {
    "confidence"
}

@dataclass(frozen=True)
class NameImportResult:
    catalog: Path
    database: Path
    rows: int
    imported: int
    skipped: int

def _normalize_header(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())

def _find_column(fieldnames: list[str], accepted: set[str]) -> str | None:
    for fieldname in fieldnames:
        if _normalize_header(fieldname) in accepted:
            return fieldname

    return None

def _row_value(row: Mapping[str, str | None], column: str | None) -> str:
    if column is None:
        return ""

    return (row.get(column) or "").strip()

def parse_catalog_uid(value: str) -> int:
    """Parse decimal community UIDs and hexadecimal extractor UIDs"""

    text = value.strip().replace("_", "")

    if not text:
        raise ValueError("UID cannot be empty")

    if text.lower().startswith("0x"):
        uid = int(text[2:], 16)
    elif any(character in "abcdefABCDEF" for character in text):
        uid = int(text, 16)
    elif len(text) == 16 and text.startswith("0"):
        uid = int(text, 16)
    else:
        uid = int(text, 10)

    uid_to_text(uid)

    return uid

def import_name_catalog(catalog: str | Path, database: str | Path, *, default_source: str = "csv", default_category: str = "unknown", default_confidence: int = 60) -> NameImportResult:
    catalog_path = Path(catalog).expanduser().resolve()
    database_path = Path(database).expanduser().resolve()

    if not catalog_path.is_file():
        raise FileNotFoundError(f"Name catalog not found: {catalog_path}")

    if not 0 <= default_confidence <= 100:
        raise ValueError("Default confidence must be between 0 and 100")

    entries: list[AssetName] = []
    row_count = 0
    skipped = 0

    with catalog_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)

        if not reader.fieldnames:
            raise ValueError("Name catalog has no header row")

        uid_column = _find_column(reader.fieldnames, _UID_COLUMNS)
        name_column = _find_column(reader.fieldnames, _NAME_COLUMNS)

        if uid_column is None:
            raise ValueError("Name catalog requires a UID column")

        if name_column is None:
            raise ValueError("Name catalog requires a name column")

        category_column = _find_column(reader.fieldnames, _CATEGORY_COLUMN)
        source_column = _find_column(reader.fieldnames, _SOURCE_COLUMNS)
        confidence_column = _find_column(reader.fieldnames, _CONFIDENCE_COLUMN)

        for line_number, row in enumerate(reader, start=2):
            row_count += 1

            uid_text = _row_value(row, uid_column)
            name = _row_value(row, name_column)

            if not uid_text and not name:
                skipped += 1
                continue

            if not uid_text:
                raise ValueError(f"Catalog row {line_number} has no UID")

            if not name:
                raise ValueError(f"Catalog row {line_number} has no name")

            category = _row_value(row, category_column) or default_category
            source = _row_value(row, source_column) or default_source
            confidence_text = _row_value(row, confidence_column)

            try:
                uid = parse_catalog_uid(uid_text)
                confidence = int(confidence_text) if confidence_text else default_confidence
            except ValueError as error:
                raise ValueError(f"Invalid catalog row {line_number}: {error}") from error

            entries.append(
                AssetName (
                    uid=uid,
                    name=name,
                    category=category,
                    source=source,
                    confidence=confidence
                )
            )

    imported = upsert_asset_names(database_path, entries)

    return NameImportResult (
        catalog=catalog_path,
        database=database_path,
        rows=row_count,
        imported=imported,
        skipped=skipped
    )

def import_column_name_catalog(catalog: str | Path, database: str | Path, *, default_source: str="csv-columns", default_category: str = "unknown", default_confidence: int = 50) -> NameImportResult:
    """Import a catalog with asset names in columns and UIDs beneath them"""

    catalog_path = Path(catalog).expanduser().resolve()
    database_path = Path(database).expanduser().resolve()

    if not catalog_path.is_file():
        raise FileNotFoundError(f"Name catalog not found: {catalog_path}")

    if not 0 <= default_confidence <= 100:
        raise ValueError("Default confidence must be between 0 and 100")

    entries: list[AssetName] = []
    cell_count = 0
    skipped = 0

    with catalog_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream)

        try:
            header_row = next(reader)
        except StopIteration as error:
            raise ValueError("Name catalog is empty") from error

        names = [
            heading.strip().rstrip(":").strip()
            for heading in header_row
        ]

        if not any(names):
            raise ValueError("Name catalog has no asset-name headings")

        for line_number, row in enumerate(reader, start=2):
            for column_index, value in enumerate(row):
                uid_text = value.strip()

                if not uid_text:
                    continue

                cell_count += 1

                if column_index >= len(names) or not names[column_index]:
                    skipped += 1
                    continue

                try:
                    uid = parse_catalog_uid(uid_text)
                except ValueError as error:
                    column_number = column_index + 1
                    raise ValueError(f"Invalid catalog cell at row {line_number}, column {column_number}: {error}") from error

                entries.append(
                    AssetName (
                        uid=uid,
                        name=names[column_index],
                        category=default_category,
                        source=default_source,
                        confidence=default_confidence
                    )
                )

    imported = upsert_asset_names(database_path, entries)

    return NameImportResult(
        catalog=catalog_path,
        database=database_path,
        rows=cell_count,
        imported=imported,
        skipped=skipped
    )