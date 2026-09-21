"""Batch resource audit. Never writes visual approvals or manual notes"""
import argparse
import hashlib
import json
import sys
import time
import traceback
from contextlib import redirect_stdout
from pathlib import Path
from urllib.parse import unquote

from PIL import Image
from app_runtime import application_directory
from preview_cache import CACHE_VERSION, prepare_preview
from src.operator_registry import read_operator_registry

AUDIT_VERSION = 1

def save_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temporary.replace(path)

def check_model(path):
    document = json.loads(path.read_text(encoding="utf-8"))
    issues, resources = [], {}

    def resource(uri, image=False):
        if not isinstance(uri, str) or not uri:
            return
        if uri.startswith("data:"):
            return

        candidate = (path.parent / unquote(uri)).resolve()
        if not candidate.is_relative_to(path.parent.resolve()):
            issues.append("Reference outside model folder: " + uri)
            return

        resources[candidate] = resources.get(candidate, False) or image

    for item in document.get("buffers", []):
        resource(item.get("uri"))
    for item in document.get("images", []):
        resource(item.get("uri"), True)

    def extra_images(value):
        if isinstance(value, str):
            resource(value, True)
        elif isinstance(value, dict):
            for item in value.values():
                extra_images(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                extra_images(item)

    materials = document.get("materials", [])
    for material in materials:
        extras = material.get("extras", {})
        for key in (
            "siegePackedMaterialTexture",
            "siegeMaskTexture",
            "siegeDetailNormalTextures",
            "siegeShaderTextures"
        ):
            extra_images(extras.get(key))

    signatures = []
    for candidate, is_image in sorted(resources.items()):
        if not candidate.is_file():
            issues.append("Missing resource: " + candidate.name)
            continue

        stat = candidate.stat()
        signatures.append((candidate.name, stat.st_size, stat.st_mtime_ns))
        if is_image:
            try:
                with Image.open(candidate) as image:
                    image.verify()
            except Exception as error:
                issues.append(f"Unreadable image {candidate.name}: {error}")

    for mesh_index, mesh in enumerate(document.get("meshes", [])):
        for primitive_index, primitive in enumerate(mesh.get("primitives", [])):
            index = primitive.get("material")
            label = f"Mesh {mesh_index}, primitive {primitive_index}"

            if index is None:
                issues.append(label + ": no explicit material assignment")
            elif not isinstance(index, int) or not 0 <= index < len(materials):
                issues.append(label + ": invalid material index")

    fingerprint = hashlib.sha256(path.read_bytes() + json.dumps(signatures, separators=(",", ":")).encode()).hexdigest()

    return {
        "model": str(path),
        "fingerprint": fingerprint,
        "material_count": len(materials),
        "resource_count": len(resources),
        "resource_checks": "warnings" if issues else "passed",
        "issues": issues
    }

class Tee:
    def __init__(self, log, console):
        self.log, self.console = log, console

    def write(self, text):
        self.log.write(text)
        self.console.write(text)
        return len(text)

    def flush(self):
        self.log.flush()
        self.console.flush()

def main(arguments=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("game", type=Path)
    parser.add_argument("--operators", nargs="+")
    args = parser.parse_args(arguments)

    game = args.game.resolve()
    operators = read_operator_registry(game / "datapc64.forge")

    if args.operators:
        wanted = {name.casefold() for name in args.operators}
        known = {operator.name.casefold() for operator in operators}
        if wanted - known:
            parser.error("Unknown operators: " + ", ".join(sorted(wanted - known)))

        operators = [op for op in operators if op.name.casefold() in wanted]

    folder = application_directory() / "output" / "material-audit"
    folder.mkdir(parents=True, exist_ok=True)

    run_id = str(time.time_ns())
    log_folder = folder / "logs" / run_id
    log_folder.mkdir(parents=True)

    report_path = folder / "audit.json"
    history = folder / ("audit-" + run_id + ".json")
    stop_path = folder / "STOP"

    report = {
        "audit_version": AUDIT_VERSION,
        "cache_version": CACHE_VERSION,
        "game": str(game),
        "run_id": run_id,
        "state": "running",
        "requested": [f"{op.uid:016X}" for op in operators],
        "operators": {}
    }

    def checkpoint():
        save_json(history, report)
        save_json(report_path, report)

    checkpoint()
    failures = 0

    try:
        for number, operator in enumerate(operators, 1):
            if stop_path.exists():
                report["state"] = "stopped"
                break

            uid = f"{operator.uid:016X}"
            print(f"Audit {number}/{len(operators)}: {operator.name}", flush=True)

            log_path = log_folder / (uid + ".log")
            result = {
                "name": operator.name,
                "uid": uid,
                "log": str(log_path)
            }

            with log_path.open("w", encoding="utf-8") as log:
                with redirect_stdout(Tee(log, sys.stdout)):
                    try:
                        manifest = prepare_preview(game, operator.uid, operator=operator)
                        data = json.loads(manifest.read_text(encoding="utf-8"))
                        models = [check_model(manifest.parent / relative) for relative in data["models"]]

                        result.update(
                            state="checked",
                            preview_manifest=str(manifest),
                            cache_key=manifest.parent.name,
                            models=models,
                            resource_checks="warnings" if any(model["issues"] for model in models) else "passed"
                        )
                    except Exception as error:
                        failures += 1
                        result.update(
                            state="failed",
                            error=str(error)
                        )
                        traceback.print_exc(file=sys.stdout)
            report["operators"][uid] = result
            checkpoint()
        else:
            report["state"] = "complete"
    except KeyboardInterrupt:
        report["state"] = "interrupted"
    finally:
        checkpoint()

    print(f"Audit {report['state']}: {report_path}", flush=True)
    return 1 if failures or report["state"] != "complete" else 0

if __name__ == "__main__":
    raise SystemExit(main())