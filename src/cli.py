"""Command-line interface for the Siege Forge extractor"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Sequence

from src.database import (
    index_archive,
    load_asset_index,
    search_asset_names
)
from src.depgraph import load_depgraph
from src.extractor import extract_raw_archive
from src.index import (
    ScanDiagnostics,
    build_index,
    scan_archive
)
from src.name_catalog import (
    import_column_name_catalog,
    import_name_catalog
)
from src.model import (
    export_model,
    resolve_dependency_uids,
    resolve_direct_texture_uids
)
from src.model_catalog import (
    COMPILED_MESH_OBJECT,
    build_model_catalog,
    resolve_bundle_paths,
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

def command_names_import(args: argparse.Namespace) -> int:
    if args.layout == "columns":
        importer = import_column_name_catalog
    else:
        importer = import_name_catalog

    result = importer(args.catalog, args.database, default_source=args.source, default_category=args.category, default_confidence=args.confidence)

    print(f"Catalog: {result.catalog}")
    print(f"Layout: {args.layout}")
    print(f"Rows: {result.rows}")
    print(f"Imported: {result.imported}")
    print(f"Skipped: {result.skipped}")
    print(f"Database: {result.database}")

    return 0

def command_search(args: argparse.Namespace) -> int:
    matches = search_asset_names(args.database, args.query, limit=args.limit)

    model_locations = {}
    dependency_uids = set()
    game_dir = game_directory()

    if game_dir is not None:
        for depgraph in sorted(game_dir.glob("*.depgraphbin")):
            children = load_depgraph(depgraph)

            for match in matches:
                if match.uid not in children:
                    continue

                dependencies = resolve_dependency_uids(match.uid, children)

                model_locations.setdefault(match.uid, []).append(
                    (
                        depgraph,
                        dependencies
                    )
                )

                dependency_uids.update(dependencies)

    asset_index = (
        load_asset_index(args.database, dependency_uids)
        if dependency_uids
        else None
    )

    database_path = Path(args.database).expanduser().resolve()

    for match in matches:
        availability = (
            f"{match.locations} location"
            if match.locations == 1
            else f"{match.locations} locations"
        )

        print(f"{match.uid:016X} {match.name} [{match.category}] confidence={match.confidence} source={match.source} {availability}")

        for depgraph, dependencies in model_locations.get(match.uid, ()):
            dependency_set = set(dependencies)

            geometry_records = (
                tuple(
                    sorted(
                        (
                            record
                            for record in asset_index.records()
                            if record.uid in dependency_set and record.file_type == COMPILED_MESH_OBJECT
                        ),
                        key=lambda record: (
                            record.uid,
                            str(record.archive)
                        )
                    )
                )
                if asset_index is not None
                else ()
            )

            print(f"  Depgraph: {depgraph}")

            for record in geometry_records:
                print(f"  Geometry: {record.uid:016X} -> {record.archive}")

            if geometry_records:
                archive = geometry_records[0].archive

                print(
                    "  Export: "
                    f'py -3 -B main.py model "{archive}" --depgraph "{depgraph}" --database "{database_path}" --uid {match.uid:016X} -o output/model-{match.uid:016X}'
                )

    print()
    print(f"Matches: {len(matches)}")

    return 0

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

    (
        _,
        depgraph,
        archives
    ) = resolve_bundle_paths(archive, depgraph_path=args.depgraph, include_textures=True, archive_only=args.archive_only)

    children = load_depgraph(depgraph)

    if args.database:
        dependency_uids = set(resolve_dependency_uids(uid, children))
        index = load_asset_index(args.database, dependency_uids)
        direct_texture_uids = set(resolve_direct_texture_uids(uid, index))
        missing_texture_uids = {
            texture_uid
            for texture_uid in direct_texture_uids
            if texture_uid not in index
        }

        if missing_texture_uids:
            additional_index = load_asset_index(args.database, missing_texture_uids)

            for record in additional_index.records():
                index.add(record)
    else:
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

    names = commands.add_parser("names", help="manage human-readable asset names")
    name_commands = names.add_subparsers(dest="name_command")

    names_import = name_commands.add_parser("import", help="Import names from a CSV catalog")
    names_import.add_argument("catalog", help="CSV name catalog")
    names_import.add_argument("--layout", choices=("rows", "columns"), default="rows", help="CSV layout: one asset per row or names above UID columns")
    names_import.add_argument("-d", "--database", default="output/r6-assets.sqlite", help="SQLite database path")
    names_import.add_argument("--source", default="csv", help="source used when the CSV has no Source column")
    names_import.add_argument("--category", default="unknown", help="category used when the CSV has no Category column")
    names_import.add_argument("--confidence", type=int, default=60, help="confidence used when the CSV has no Confidence column")
    names_import.set_defaults(handler=command_names_import)

    search = commands.add_parser("search", help="search human-readable asset names and UIDs")
    search.add_argument("query", help="partial name or UID")
    search.add_argument("-d", "--database", default="output/r6-assets.sqlite", help="SQLite database path")
    search.add_argument("--limit", type=int, default=20, help="maximum number of matches")
    search.set_defaults(handler=command_search)

    models = commands.add_parser("models", help="discover model UIDs and geometry parts")
    models.add_argument("input", help="mesh Forge archive")
    models.add_argument("--minimum-parts", type=int, default=1)
    models.add_argument("--limit", type=int, default=20)
    models.add_argument("--json-output")
    models.add_argument("-v", "--verbose", action="store_true")
    models.set_defaults(handler=command_models)

    model = commands.add_parser("model", help="export one model as glTF")
    model.add_argument("input", help="mesh Forge archive")
    model.add_argument("--uid", required=True, help="hexadecimal Mesh UID")
    model.add_argument("-o", "--output", default="output/model")
    model.add_argument("--depgraph", help="dependency graph path or GAME_DIR filename")
    model.add_argument("--archive-only", action="store_true", help="index only the input mesh archive")
    model.add_argument("--database", help="use a SQLite asset index")
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