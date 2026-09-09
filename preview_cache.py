"""Prepare primary operator models for the desktop preview"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

from app_runtime import (
    application_directory,
    worker_arguments,
    worker_executable
)
from src.operator_registry import read_operator_registry

CACHE_VERSION = 4

def model_files_exist(model):
    """Check the glTF and its external buffers/images"""
    if not model.is_file():
        return False

    try:
        document = json.loads(model.read_text(encoding="utf-8"))
        for group in ("buffers", "images"):
            for resource in document.get(group, []):
                uri = resource.get("uri", "")
                if not uri or uri.startswith("data:"):
                    continue
                if not (model.parent / unquote(uri)).is_file():
                    return False
    except (OSError, ValueError, TypeError, AttributeError):
        return False

    return True

def prepare_preview(game, operator_uid):
    game = Path(game).resolve()
    project = application_directory()
    database = project / "output" / "r6-assets.sqlite"
    registry = game / "datapc64.forge"
    mesh_archive = game / "datapc64_merged_bnk_mesh.forge"
    depgraph = game / "datapc64_ondemand.depgraphbin"

    for path in (database, registry, mesh_archive, depgraph):
        if not path.is_file():
            raise FileNotFoundError(path)

    # include archive state because textures may come from other bundles
    sources = sorted(game.glob("*.forge")) + [depgraph, database]
    signature = [
        (str(path), path.stat().st_size, path.stat().st_mtime_ns)
        for path in sources
    ]
    key = hashlib.sha256(
        json.dumps(
            [CACHE_VERSION, operator_uid, signature],
            separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:24]

    directory = project / "output" / "preview-cache" / f"{operator_uid:016X}" / key
    manifest = directory / "preview.json"

    if manifest.is_file():
        try:
            saved = json.loads(manifest.read_text(encoding="utf-8"))
            models = [
                directory / relative
                for relative in saved["models"]
            ]
            if models and all(model_files_exist(model) for model in models):
                print(f"Using cached preview: {manifest}", flush=True)
                return manifest
        except (OSError, ValueError, KeyError, TypeError):
            pass

    operator = next(
        (
            entry for entry in read_operator_registry(registry)
            if entry.uid == operator_uid
        ),
        None,
    )
    if operator is None:
        raise ValueError(f"Operator not found: {operator_uid:016X}")

    jobs = []
    for label, part in (("body", operator.body), ("head", operator.head)):
        if not part.model_groups or not part.model_groups[0]:
            raise ValueError(f"No primary {label} models for {operator.name}")
        jobs.extend((label, uid) for uid in dict.fromkeys(part.model_groups[0]))

    # incomplete preparation must never retain a success marker
    directory.mkdir(parents=True, exist_ok=True)
    manifest.unlink(missing_ok=True)

    models = []
    for number, (label, uid) in enumerate(jobs, 1):
        destination = directory / label / f"{uid:016X}"
        print(f"Preparing preview {number}/{len(jobs)}: {operator.name} {label} {uid:016X}")

        arguments = worker_arguments(
            "cli",
            [
                "model", str(mesh_archive),
                "--depgraph", str(depgraph),
                "--database", str(database),
                "--uid", f"{uid:016X}",
                "-o", str(destination)
            ]
        )
        subprocess.run(
            [worker_executable(), *arguments],
            cwd=str(project),
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        )

        model = destination / f"{uid:016X}.gltf"
        if not model_files_exist(model):
            raise RuntimeError(f"Incomplete preview model: {model}")
        models.append(model.relative_to(directory).as_posix())

    document = {
        "version": CACHE_VERSION,
        "operator_uid": f"{operator_uid:016X}",
        "operator_name": operator.name,
        "models": models,
    }

    temporary = directory / "preview.json.tmp"
    temporary.write_text(json.dumps(document, indent=2), encoding="utf-8")
    temporary.replace(manifest)
    print(f"Preview ready: {manifest}", flush=True)
    return manifest