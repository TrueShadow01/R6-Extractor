"""Discover model parents and their compiled geometry children"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from src.database import AssetName
from src.depgraph import load_depgraph
from src.index import AssetIndex, build_index

COMPILED_MESH_OBJECT = 0xABEB2DFB

OPERATOR_CATEGORIES = frozenset(
    {
        "operator-body",
        "operator-headgear",
        "operator-hands",
        "operator-legs"
    }
)

@dataclass(frozen=True)
class OperatorCandidate:
    uid: int
    category: str
    evidence: tuple[AssetName, ...]
    depgraphs: tuple[Path, ...]

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
            "part_count": self.part_count,
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

def resolve_bundle_paths(mesh_archive: str | Path, *, depgraph_path: str | Path | None = None, include_textures: bool = False, archive_only: bool = False) -> tuple[str, Path, tuple[Path, ...]]:
    """Resolve archives and dependency graph for one bundle"""

    mesh_archive = Path(mesh_archive).resolve()

    if not mesh_archive.is_file():
        raise FileNotFoundError(f"Mesh archive not found: {mesh_archive}")

    if "_bnk_" not in mesh_archive.name:
        raise ValueError(f"Expected a bundle archive containing '_bnk_' in its name: {mesh_archive.name}")

    prefix = mesh_archive.name.split("_bnk_", 1)[0]
    directory = mesh_archive.parent

    if archive_only:
        archives = (mesh_archive,)
    else:
        selected = list(
            directory.glob(f"{prefix}_bnk_*mesh.forge")
        )

        if include_textures:
            selected.extend(
                directory.glob(f"{prefix}_bnk_*textures*.forge")
            )

        archives = tuple(sorted(set(selected)))

    if not archives:
        raise FileNotFoundError(f"No bundled archives found for {prefix}")

    if depgraph_path is None:
        depgraph = directory / f"{prefix}.depgraphbin"
    else:
        requested = Path(depgraph_path).expanduser()
        requested_names = [requested]

        if not str(requested).lower().endswith(".depgraphbin"):
            requested_names.append(Path(f"{requested}.depgraphbin"))

        depgraph = None

        for requested_name in requested_names:
            candidates = [requested_name]

            if not requested_name.is_absolute():
                candidates.append(directory / requested_name)

            for candidate in candidates:
                if candidate.is_file():
                    depgraph = candidate.resolve()
                    break

            if depgraph is not None:
                break

        if depgraph is None:
            raise FileNotFoundError(f"Dependency graph not found: {depgraph_path}")

    depgraph = depgraph.resolve()

    if not depgraph.is_file():
        raise FileNotFoundError(f"Dependency graph not found: {depgraph}")

    return prefix, depgraph, archives

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

def discover_default_operator_candidates(depgraphs: Mapping[Path, Mapping[int, Iterable[int]]], names: Iterable[AssetName]) -> tuple[OperatorCandidate, ...]:
    """Find parent models labeled as default operator bodies or heads"""

    ordered_names = sorted(
        names,
        key=lambda entry: (
            -entry.confidence,
            entry.name.casefold(),
            entry.source.casefold()
        )
    )

    verified_groups = {
        (
            entry.name.casefold().split(" default", 1)[0].strip(),
            "head" if "head" in entry.name.casefold() else "body"
        )
        for entry in ordered_names
        if entry.source == "manual-verified" and entry.category in OPERATOR_CATEGORIES and " default" in entry.name.casefold() and ("body" in entry.name.casefold() or "head" in entry.name.casefold())
    }

    defaults: dict[int, AssetName] = {}

    for entry in ordered_names:
        label = entry.name.casefold()

        verified_parent = entry.source == "manual-verified" and entry.category in OPERATOR_CATEGORIES
        derived_parent = entry.source.startswith("derived-") and entry.category in OPERATOR_CATEGORIES
        metadata_parent = entry.source == "r6-uid-sheet-2022" and entry.category == "operator-metadata"

        if not (verified_parent or derived_parent or metadata_parent):
            continue

        if "default" not in label:
            continue

        if "body" not in label and "head" not in label:
            continue

        if not verified_parent:
            group = label.split(" default", 1)[0].strip(), "head" if "head" in label else "body"

            if group in verified_groups:
                continue

        defaults.setdefault(entry.uid, entry)

    depgraphs_by_parent: dict[int, set[Path]] = {}

    for depgraph, children in depgraphs.items():
        for parent_uid in defaults:
            if parent_uid in children:
                depgraphs_by_parent.setdefault(parent_uid, set()).add(Path(depgraph))

    return tuple(
        OperatorCandidate(
            uid=parent_uid,
            category="operator-headgear" if "head" in defaults[parent_uid].name.casefold() else "operator-body",
            evidence=(defaults[parent_uid],),
            depgraphs=tuple(sorted(depgraphs_by_parent[parent_uid]))
        )
        for parent_uid in sorted(depgraphs_by_parent)
    )

def discover_unknown_operator_candidates(depgraphs: Mapping[Path, Mapping[int, Iterable[int]]], names: Iterable[AssetName], *, max_parent_references: int = 20) -> tuple[OperatorCandidate, ...]:
    """Find unnamed parents that reference sufficiently specific operator meshes"""

    if max_parent_references < 1:
        raise ValueError("Maximum parent references must be at least 1")

    ordered_names = sorted(
        names,
        key=lambda entry: (
            -entry.confidence,
            entry.name.casefold(),
            entry.source.casefold()
        )
    )

    named_uids = {
        entry.uid
        for entry in ordered_names
    }

    operator_names: dict[int, AssetName] = {}

    for entry in ordered_names:
        if entry.category in OPERATOR_CATEGORIES and entry.locations > 0:
            operator_names.setdefault(entry.uid, entry)

    parents_by_child: dict[int, set[int]] = {}

    for children in depgraphs.values():
        for parent_uid, child_uids in children.items():
            for child_uid in set(child_uids):
                parents_by_child.setdefault(child_uid, set()).add(parent_uid)

    evidence_by_parent: dict[int, set[AssetName]] = {}
    depgraphs_by_parent: dict[int, set[Path]] = {}

    for depgraph, children in depgraphs.items():
        for parent_uid, child_uids in children.items():
            if parent_uid in named_uids:
                continue

            evidence = {
                operator_names[child_uid]
                for child_uid in set(child_uids)
                if child_uid in operator_names and len(parents_by_child[child_uid]) <= max_parent_references
            }

            if not evidence:
                continue

            evidence_by_parent.setdefault(parent_uid, set()).update(evidence)
            depgraphs_by_parent.setdefault(parent_uid, set()).add(Path(depgraph))

    candidates: list[OperatorCandidate] = []

    for parent_uid, evidence in evidence_by_parent.items():
        ordered_evidence = tuple(
            sorted(
                evidence,
                key=lambda entry: (
                    entry.category,
                    entry.name.casefold(),
                    entry.uid,
                )
            )
        )

        categories = {
            entry.category
            for entry in ordered_evidence
        }

        category = (
            next(iter(categories))
            if len(categories) == 1
            else "operator-mixed"
        )

        candidates.append(
            OperatorCandidate(
                uid=parent_uid,
                category=category,
                evidence=ordered_evidence,
                depgraphs=tuple(sorted(depgraphs_by_parent[parent_uid]))
            )
        )

    return tuple(
        sorted(
            candidates,
            key=lambda candidate: candidate.uid
        )
    )

def build_model_catalog(mesh_archive: str | Path) -> ModelCatalog:
    """Build a complete model catalog for one bundle"""

    (
        prefix,
        depgraph_path,
        mesh_archives
    ) = resolve_bundle_paths(mesh_archive)

    children = load_depgraph(depgraph_path)

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