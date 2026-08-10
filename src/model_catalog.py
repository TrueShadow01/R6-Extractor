"""Discover model parents and their compiled geometry children"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from src.depgraph import load_depgraph
from src.index import AssetIndex, build_index

COMPILED_MESH_OBJECT = 0xABEB2DFB

@dataclass(frozen=True)
class ModelRecord:
    uid: int
    geometry_uids: tuple[int, ...]
    source_archives: tuple[str, ...]
    geometry_bytes: int

    @property
    def part_count(self) -> int:
        return len(self.geometry_uids)

    def to_dict(self) -> dict:
        return {
            "uid": f"0x{self.uid:016X}",
            "part_count": self.part_cout,
            "geometry_uids": [
                f"0x{uid:016X}"
                for uid in self.geometry_uids
            ],
            "source_archives": list(self.source_archives),
            "geometry_bytes": self.geometry_bytes
        }

@dataclass
class ModelCatalog:
    prefix: str
    depgraph_path: Path
    mesh_archives: tuple[Path, ...]
    index: AssetIndex
    models: tuple[ModelRecord, ...]

    @property
    def geometry_asset_count(self) -> int:
        return sum(
            1
            for record in self.index.records()
            if record.file_type == COMPILED_MESH_OBJECT
        )

    @property
    def total_part_references(self) -> int:
        return sum(model.part_count for model in self.models)

    @property
    def scan_errors(self) -> int:
        return sum(
            diagnostics.invalid_containers + diagnostics.metadata_errors
            for diagnostics in self.index.diagnostics.values()
        )

    def part_distribution(self) -> Counter[int]:
        return Counter(
            model.part_count
            for model in self.models
        )

    def get(self, uid: int) -> ModelRecord | None:
        return next(
            (
                model
                for model in self.models
                if model.uid == uid
            ),
            None
        )

    def to_dict(self) -> dict:
        return {
            "prefix": self.prefix,
            "depgraph": str(self.depgraph_path),
            "mesh_archives": [
                str(path)
                for path in self.mesh_archives
            ],
            "model_count": len(self.models),
            "geometry_asset_count": self.geometry_asset_count,
            "total_part_references": self.total_part_references,
            "scan_errors": self.scan_errors,
            "part_distribution": {
                str(parts): count
                for parts, count in sorted(self.part_distribution().items())
            },
            "models": [
                model.to_dict()
                for model in self.models
            ]
        }

def resolve_bundle_paths(mesh_archive: str | Path) -> tuple[str, Path, tuple[Path, ...]]:
    """Resolve a mesh archive to its bundled depgraph and mesh files"""

    mesh_archive = Path(mesh_archive).resolve()

    if not mesh_archive.is_file():
        raise FileNotFoundError(f"Mesh archive not found: {mesh_archive}")

    if "_bnk_" not in mesh_archive.name:
        raise ValueError(f"Expected a bundle archive containing '_bnk_' in its name: {mesh_archive.name}")

    prefix = mesh_archive.name.split("_bnk_", 1)[0]
    depgraph_path = mesh_archive.parent / f"{prefix}.depgraphbin"

    if not depgraph_path.is_file():
        raise FileNotFoundError(f"Dependency graph not found: {depgraph_path}")

    mesh_archives = tuple(
        sorted(
            mesh_archive.parent.glob(f"{prefix}_bnk_*mesh.forge")
        )
    )

    if not mesh_archives:
        raise FileNotFoundError(f"No mesh archives found for bundle {prefix}")

    return (
        prefix,
        depgraph_path,
        mesh_archives
    )

def discover_models(children: Mapping[int, Iterable[int]], index: AssetIndex) -> tuple[ModelRecord, ...]:
    """Find parents with one or more compiled geometry children"""
    models: list[ModelRecord] = []

    for parent_uid, child_uids, in children.items():
        geometry_uids: list[int] = []

        for child_uid in set(child_uids):
            record = index.primary(child_uid)

            if (record is not None and record.file_type == COMPILED_MESH_OBJECT):
                geometry_uids.append(child_uid)

        if not geometry_uids:
            continue

        geometry_uids.sort()

        source_archives: set[str] = set()
        geometry_bytes = 0

        for geometry_uid in geometry_uids:
            record = index.primary(geometry_uid)

            if record is None:
                continue

            source_archives.add(record.archive.name)
            geometry_bytes += record.unpacked_size

        models.append(ModelRecord(uid=parent_uid, geometry_uids=tuple(geometry_uids), source_archives=tuple(sorted(source_archives)), geometry_bytes=geometry_bytes))

    return tuple(
        sorted(
            models,
            key=lambda model: model.uid
        )
    )

def build_model_catalog(mesh_archive: str | Path) -> ModelCatalog:
    """Build a complete model catalog for one bundle"""

    (
        prefix,
        depgraph_path,
        mesh_archives
    ) = resolve_bundle_paths(mesh_archive)

    children, _ = load_depgraph(depgraph_path)

    index = build_index(mesh_archives)
    models = discover_models(children, index)

    return ModelCatalog(
        prefix=prefix,
        depgraph_path=depgraph_path,
        mesh_archives=mesh_archives,
        index=index,
        models=models
    )

def write_model_catalog(catalog: ModelCatalog, output_path: str | Path) -> Path:
    """Write a model catalog as a formatted JSON"""

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(json.dumps(catalog.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return output_path