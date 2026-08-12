"""Command-line interface for the Siege Forge extractor"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Sequence

from src.database import index_archive
from src.depgraph import load_depgraph
from src.extractor import extract_raw_archive
from src.index import (
    ScanDiagnostics,
    build_index,
    scan_archive
)
from src.model import export_model
from src.model_catalog import (
    build_model_catalog,
    write_model_catalog
)

def game_directory() -> Path | None:
    try:
        import config
    except ImportError:
        return None

    value = getattr(config, "GAME_DIR", None)

    if not value:
        return None

    return Path(value).resolve()

def resolve_input(value: str, *, allow_directory: bool = True) -> Path:
    direct = Path(value).expanduser()

    if direct.exists():
        resolved = direct.resolve()

        if resolved.is_dir() and not allow_directory:
            raise ValueError(f"Expected a file, received: {resolved}")

        return resolved

    game_dir = game_directory()

    if game_dir is not None:
        name = value

        if not name.lower().endswith(".forge"):
            name += ".forge"

        candidate = game_dir / name

        if candidate.exists():
            return candidate.resolve()

    raise FileNotFoundError(f"Input not found: {value}")

def resolve_depgraph(value: str | Path) -> Path:
    direct = Path(value).expanduser()
    candidates = [direct]

    if not str(direct).lower().endswith(".depgraphbin"):
        candidates.append(Path(f"{direct}.depgraphbin"))

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()

    game_dir = game_directory()

    if game_dir is not None:
        for candidate in candidates:
            if candidate.is_absolute():
                continue

            game_candidate = game_dir / candidate

            if game_candidate.is_file():
                return game_candidate.resolve()

    raise FileNotFoundError(f"Dependency graph was not found: {value}")

def discover_archives(value: str | None, *, all_archives: bool, pattern: str) -> tuple[Path, ...]:
    if value is None:
        if not all_archives:
            raise ValueError("Provide an archive or directory or use --all")

        source = game_directory()

        if source is None:
            raise ValueError("--all requires GAME_DIR in config.py")
    else:
        source = resolve_input(value)

    if source.is_file():
        return (source,)

    archives = tuple(sorted(source.glob(pattern)))

    if not archives:
        raise FileNotFoundError(f"No files matching {pattern!r} under {source}")

    return archives

def bundle_files(forge_path: str | Path, *, depgraph_path: str | Path | None=None, archive_only: bool = False) -> tuple[list[Path], Path]:
    forge_path = Path(forge_path).resolve()

    if "_bnk_" not in forge_path.name:
        raise ValueError("Expected a bundle archive containing '_bnk_'")

    prefix = forge_path.name.split("_bnk_", 1)[0]
    directory = forge_path.parent

    if archive_only:
        archives = [forge_path]
    else:
        mesh_files = list(directory.glob(f"{prefix}_bnk_*mesh.forge"))
        texture_files = list(directory.glob(f"{prefix}_bnk_*textures*.forge"))
        archives = sorted(set(mesh_files + texture_files))

    if not archives:
        raise FileNotFoundError(f"No bundle archives found for {prefix}")

    if depgraph_path is None:
        depgraph = directory / f"{prefix}.depgraphbin"

        if not depgraph.is_file():
            raise FileNotFoundError(f"Dependency graph not found: {depgraph}")
    else:
        depgraph = resolve_depgraph(depgraph_path)

    return archives, depgraph

def command_scan(args: argparse.Namespace) -> int:
    archives = discover_archives(args.input, all_archives=args.all, pattern=args.pattern)

    total_assets = 0
    total_containers = 0
    total_companions = 0
    total_errors = 0
    file_types: Counter[int] = Counter()

    for archive in archives:
        diagnostics = ScanDiagnostics()
        archive_assets = 0

        for record in scan_archive(archive, diagnostics):
            archive_assets += 1
            file_types[record.file_type] += 1

        errors = diagnostics.invalid_containers + diagnostics.metadata_errors
        total_assets += archive_assets
        total_containers += diagnostics.containers
        total_companions += diagnostics.auxiliary_containers
        total_errors += errors

        print(f"{archive.name}: {archive_assets} assets, {diagnostics.containers} containers, {diagnostics.auxiliary_containers} companions, {errors} errors")

        if args.verbose:
            for message, count in diagnostics.errors.items():
                print(f"  {count}x {message}")

    print()
    print(f"Archives: {len(archives)}")
    print(f"Containers: {total_containers}")
    print(f"Assets: {total_assets}")
    print(f"Companion containers: {total_companions}")
    print(f"Errors: {total_errors}")
    print(f"File types:")

    for file_type, count in file_types.most_common():
        print(f"  0x{file_type:08X}: {count}")

    return 1 if total_errors else 0

def command_extract(args: argparse.Namespace) -> int:
    archives = discover_archives(args.input, all_archives=args.all, pattern=args.pattern)

    output = Path(args.output).resolve()

    scanned = 0
    extracted = 0
    resumed = 0
    failed = 0
    scan_errors = 0
    bytes_written = 0

    for archive in archives:
        print(f"Extracting {archive.name}")

        try:
            summary = extract_raw_archive(archive, output, resume=args.resume, verbose=args.verbose)
        except Exception as error:
            failed += 1
            print(f"  archive error: {error}")
            continue

        scanned += summary.scanned_assets
        extracted += summary.extracted
        resumed += summary.resumed
        failed += summary.failed
        scan_errors += summary.scan_errors
        bytes_written += summary.bytes_written

        print(f"  scanned={summary.scanned_assets} extracted={summary.extracted} resumed={summary.resumed} failed={summary.failed} scan_errors={summary.scan_errors}")

    print()
    print(f"Archives: {len(archives)}")
    print(f"Scanned assets: {scanned}")
    print(f"Extracted: {extracted}")
    print(f"Resumed: {resumed}")
    print(f"Failed: {failed}")
    print(f"Scan errors: {scan_errors}")
    print(f"Bytes written: {bytes_written}")
    print(f"Output: {output}")

    return 1 if failed or scan_errors else 0

def command_index(args: argparse.Namespace) -> int:
    archives = discover_archives(args.input, all_archives=args.all, pattern=args.pattern)

    database = Path(args.output).resolve()

    indexed = 0
    unchanged = 0
    failed = 0
    total_containers = 0
    total_assets = 0
    total_companions = 0
    total_errors = 0

    for archive in archives:
        print(f"Indexing {archive.name}", flush=True)

        try:
            result = index_archive(archive, database, force=args.force)
        except Exception as error:
            failed += 1
            print("    Status: failed")
            print(f"    Error: {error}")
            continue

        diagnostics = result.diagnostics
        errors = diagnostics.invalid_containers + diagnostics.metadata_errors

        if result.skipped:
            unchanged += 1
            status = "unchanged"
        else:
            indexed += 1
            status = "indexed"

        total_containers += diagnostics.containers
        total_assets += result.asset_count
        total_companions += diagnostics.auxiliary_containers
        total_errors += errors

        print(f"    Status: {status}")
        print(f"    Containers: {diagnostics.containers}")
        print(f"    Assets: {result.asset_count}")
        print(f"    Companion containers: {diagnostics.auxiliary_containers}")
        print(f"    Errors: {errors}")

    print()
    print(f"Archives: {len(archives)}")
    print(f"Indexed: {indexed}")
    print(f"Unchanged: {unchanged}")
    print(f"Failed: {failed}")
    print(f"Containers: {total_containers}")
    print(f"Assets: {total_assets}")
    print(f"Companion containers: {total_companions}")
    print(f"Errors: {total_errors}")
    print(f"Database: {database}")

    return 1 if failed or total_errors else 0

def command_models(args: argparse.Namespace) -> int:
    archive = resolve_input(args.input, allow_directory=False)

    catalog = build_model_catalog(archive)

    print(f"Bundle: {catalog.prefix}")
    print(f"Mesh Archives: {len(catalog.mesh_archives)}")
    print(f"Geometry assets: {catalog.geometry_asset_count}")
    print(f"Models: {len(catalog.models)}")
    print(f"Part references: {catalog.total_part_references}")
    print(f"Scan errors: {catalog.scan_errors}")
    print(f"Part distribution:", sorted(catalog.part_distribution().items()))

    selected = [
        model
        for model in catalog.models
        if model.part_count >= args.minimum_parts
    ]

    if args.limit > 0:
        selected = selected[:args.limit]

    if selected:
        print()

    for model in selected:
        print(f"{model.uid:016X} parts={model.part_count} bytes={model.geometry_bytes}")

        if args.verbose:
            for geometry_uid in model.geometry_uids:
                print(f"  geometry {geometry_uid:016X}")

    if args.json_output:
        path = write_model_catalog(catalog, args.json_output)
        print(f"Catalog written: {path}")

    return 1 if catalog.scan_errors else 0

def command_model(args: argparse.Namespace) -> int:
    archive = resolve_input(args.input, allow_directory=False)

    uid_text = args.uid

    if uid_text.lower().startswith("0x"):
        uid_text = uid_text[2:]

    uid = int(uid_text, 16)

    archives, depgraph = bundle_files(archive, depgraph_path=args.depgraph, archive_only=args.archive_only)

    children, _ = load_depgraph(depgraph)
    index = build_index(archives)

    result = export_model(uid, children, index, args.output)


    print(f"Model: {result.model_uid:016X}")
    print(f"Parts: {result.part_count}")
    print(f"Vertices: {result.vertex_count}")
    print(f"Triangles: {result.triangle_count}")
    print(f"Textures: {result.texture_count}")
    print(f"Diffuse: {result.diffuse}")
    print(f"Normal: {result.normal}")
    print(f"Specular: {result.specular}")
    print(f"OBJ: {result.obj_path}")
    print(f"MTL: {result.mtl_path}")
    print(f"glTF: {result.gltf_path}")

    return 0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect and extract Rainbow Six Siege Forge archives")

    commands = parser.add_subparsers(dest="command")

    scan = commands.add_parser("scan", help="inventory assets without extracting")
    scan.add_argument("input", nargs="?", help="archive or directory")
    scan.add_argument("--all", action="store_true", help="scan GAME_DIR")
    scan.add_argument("--pattern", default="*.forge")
    scan.add_argument("-v", "--verbose", action="store_true")
    scan.set_defaults(handler=command_scan)

    extract = commands.add_parser("extract", help="losslessly extract raw assets")
    extract.add_argument("input", nargs="?", help="archive or directory")
    extract.add_argument("--all", action="store_true", help="extract GAME_DIR")
    extract.add_argument("--pattern", default="*.forge")
    extract.add_argument("-o", "--output", default="output/raw")
    extract.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    extract.add_argument("-v", "--verbose", action="store_true")
    extract.set_defaults(handler=command_extract)

    index = commands.add_parser("index", help="build or update the asset index")
    index.add_argument("input", nargs="?", help="Forge archive or directory")
    index.add_argument("--all", action="store_true", help="index every Forge archive under GAME_DIR")
    index.add_argument("--pattern", default="*.forge", help="archive filename pattern")
    index.add_argument("-o", "--output", default="output/r6-assets.sqlite", help="SQLite database path")
    index.add_argument("--force", action="store_true", help="rescan the archives even when they appear unchanged")
    index.set_defaults(handler=command_index)

    models = commands.add_parser("models", help="discover model UIDs and geometry parts")
    models.add_argument("input", help="mesh Forge archive")
    models.add_argument("--minimum-parts", type=int, default=1)
    models.add_argument("--limit", type=int, default=20)
    models.add_argument("--json-output")
    models.add_argument("-v", "--verbose", action="store_true")
    models.set_defaults(handler=command_models)

    model = commands.add_parser("model", help="export one model as OBJ and glTF")
    model.add_argument("input", help="mesh Forge archive")
    model.add_argument("--uid", required=True, help="hexadecimal Mesh UID")
    model.add_argument("-o", "--output", default="output/model")
    model.add_argument("--depgraph", help="dependency graph path or GAME_DIR filename")
    model.add_argument("--archive-only", action="store_true", help="index only the input mesh archive")
    model.set_defaults(handler=command_model)

    return parser

def main(arguments: Sequence[str] | None = None) -> int:
    if arguments is None:
        arguments = sys.argv[1:]

    parser = build_parser()
    parsed = parser.parse_args(arguments)

    if not hasattr(parsed, "handler"):
        parser.print_help()
        return 0

    try:
        return parsed.handler(parsed)
    except (FileNotFoundError, ValueError) as error:
        parser.error(str(error))

    return 2