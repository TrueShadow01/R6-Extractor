"""Prepare primary operator models for the desktop preview"""

import hashlib
import json
import subprocess
import sys
import re
import shutil
import stat
import uuid
from contextlib import contextmanager
from PySide6.QtCore import QLockFile
from pathlib import Path
from urllib.parse import unquote

from app_runtime import (
    application_directory,
    worker_arguments,
    worker_executable
)
from src.operator_registry import read_operator_registry
from src.model import operator_model_jobs

def cache_root():
    return application_directory().resolve() / "output" / "preview-cache"

def _plain(path):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"Linked cache path: {path}")
    return info

def _lock_folder():
    folder = cache_root().parent / ".preview-cache-locks"
    folder.mkdir(parents=True, exist_ok=True)
    _plain(folder.parent)
    _plain(folder)
    return folder

def _lock(path):
    lock = QLockFile(str(path))
    lock.setStaleLockTime(0)
    return lock

@contextmanager
def _cache_gate():
    folder = _lock_folder()
    gate = _lock(folder / "gate.lock")
    if not gate.tryLock(5000):
        raise RuntimeError("Preview cache is busy. Try again shortly.")
    try:
        yield folder
    finally:
        gate.unlock()

class CacheLease:
    """Hold while generating or serving preview files"""
    def __init__(self, generation=None):
        self.lock = None
        key = "all"
        if generation is not None:
            relative = Path(generation).resolve().relative_to(cache_root().resolve())
            if len(relative.parts) != 2:
                raise ValueError("Invalid cache generation")
            key = "_".join(relative.parts)
            if not re.fullmatch(r"[0-9A-F]{16}_[0-9a-f]{24}", key):
                raise ValueError("Invalid cache generation")

        with _cache_gate() as folder:
            lock = _lock(folder / f"use-{key}-{uuid.uuid4().hex}.lock")
            if not lock.tryLock(0):
                raise RuntimeError("Cannot protect preview cache")
            self.lock = lock

    def close(self):
        if self.lock is not None:
            self.lock.unlock()
            self.lock = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

def _generation_size(directory):
    """Accept only completed generations containing generated file types"""
    total = 0
    pending = [directory]

    while pending:
        path = pending.pop()
        info = _plain(path)

        if stat.S_ISDIR(info.st_mode):
            pending.extend(path.iterdir())
        elif stat.S_ISREG(info.st_mode):
            if path != directory / "preview.json" and path.suffix not in {".gltf", ".bin", ".png"}:
                raise ValueError("Unexpected file in cache generation")
            total += info.st_size
        else:
            raise ValueError("Unexpected cache entry")

    document = json.loads((directory / "preview.json").read_text(encoding="utf-8"))

    if document["operator_uid"] != directory.parent.name or type(document["version"]) is not int:
        raise ValueError("Invalid preview manifest")

    models = document["models"]
    if not isinstance(models, list) or not models:
        raise ValueError("Incomplete preview")

    for relative in models:
        model = (directory / relative).resolve()
        if not model.is_relative_to(directory) or not model_files_exist(model):
            raise ValueError("Incomplete preview")
    return total

def clean_preview_cache(*, keep=None, protected=(), remove=False):
    """keep: automatic cleanup of this operator only. None: all operators"""
    result = {"count": 0, "bytes": 0, "skipped": 0, "errors": []}
    root = cache_root()
    if not root.exists():
        return result

    keep = Path(keep).resolve() if keep is not None else None
    protected = [Path(path).resolve() for path in protected]

    with _cache_gate() as folder:
        _plain(root.parent)
        _plain(root)
        root = root.resolve()

        if keep is not None and (keep.parent.parent != root or not re.fullmatch(r"[0-9A-F]{16}", keep.parent.name) or not re.fullmatch(r"[0-9a-f]{24}", keep.name)):
            raise ValueError("Invalid retained preview")

        active = set()
        for path in folder.glob("use-*.lock"):
            probe = _lock(path)
            if probe.tryLock(0):
                probe.unlock()
            else:
                active.add(path.name[4:].rsplit("-", 1)[0])

        if "all" in active:
            result["errors"].append("Cleanup skipped. A preview is being prepared.")
            return result

        for operator in root.iterdir():
            if not re.fullmatch(r"[0-9A-F]{16}", operator.name):
                continue
            if keep is not None and operator != keep.parent:
                continue

            try:
                if not stat.S_ISDIR(_plain(operator).st_mode):
                    continue

                for directory in operator.iterdir():
                    if not re.fullmatch(r"[0-9a-f]{24}", directory.name):
                        continue

                    key = f"{operator.name}_{directory.name}"
                    if directory == keep or key in active or any(
                        path.is_relative_to(directory)
                        or directory.is_relative_to(path)
                        for path in protected
                    ):
                        result["skipped"] += 1
                        continue

                    try:
                        _plain(directory)
                        try:
                            _plain(directory / "preview.json")
                        except FileNotFoundError:
                            # interrupted export has no completion manifest
                            result["skipped"] += 1
                            continue
                        size = _generation_size(directory)
                        if remove:
                            # Recheck immediately before recursive deletion
                            _plain(directory)
                            if directory.resolve().parent != operator:
                                raise ValueError("Cache path changed")
                            shutil.rmtree(directory)
                        result["count"] += 1
                        result["bytes"] += size
                    except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
                        result["skipped"] += 1
                        result["errors"].append(f"{directory.name}: {error}")
            except (OSError, ValueError) as error:
                result["errors"].append(f"{operator.name}: {error}")
    return result

def prepare_preview(game, operator_uid, *, operator=None):
    with CacheLease():
        return _prepare_preview(game, operator_uid, operator=operator)

CACHE_VERSION = 43

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

def _prepare_preview(game, operator_uid, *, operator=None):
    if operator is not None and operator.uid != operator_uid:
        raise ValueError("Operator record does not match the requested UID")

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
    sources = sorted(game.glob("*.forge")) + sorted(game.glob("*.depgraphbin")) + [database]
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

    if operator is None:
        operator = next(
            (
                entry for entry in read_operator_registry(registry)
                if entry.uid == operator_uid
            ),
            None,
        )
    if operator is None:
        raise ValueError(f"Operator not found: {operator_uid:016X}")

    jobs = operator_model_jobs(operator, database)

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
        with subprocess.Popen(
            [worker_executable(), *arguments],
            cwd=str(project),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        ) as process:
            for line in process.stdout:
                print(line, end="", flush=True)

            return_code = process.wait()
            if return_code:
                raise subprocess.CalledProcessError(return_code, process.args)

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