"""Generate contact sheets without changing audit results or reviews"""
import base64
import hashlib
import html
import json
import sys
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from app_runtime import application_directory, resource_directory
from devtools.audit.runner import save_json
from desktop.preview_widget import PreviewWidget

VIEWS = ("front", "back", "head")
CAPTURE_VERSION = 1

class GalleryCapture(PreviewWidget):
    def __init__(self, report_path):
        super().__init__()
        self.reload_timer.stop()

        self.report = json.loads(report_path.read_text(encoding="utf-8"))
        self.folder = report_path.parent
        self.records = list(self.report["operators"].values())
        self.results = {}
        self.queue = list(self.records)
        self.current = None
        self.waiting_for_load = False
        self.busy = False
        self.generation = 0
        self.completed = False

        self.resize(700, 900)
        self.setWindowTitle("R6 audit thumbnails - close to stop")

        digest = hashlib.sha256()
        viewer = resource_directory() / "viewer"
        for path in sorted(viewer.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(viewer).as_posix().encode())
                digest.update(path.read_bytes())
        self.viewer_hash = digest.hexdigest()

        self.view.loadFinished.connect(self.loaded)
        QTimer.singleShot(0, self.next_operator)

    def next_operator(self):
        while self.queue:
            self.current = self.queue.pop(0)
            record = self.current

            if record.get("state") != "checked":
                self.results[record["uid"]] = {
                    "error": record.get("error", "Audit incomplete")
                }
                continue
            identity = [
                CAPTURE_VERSION,
                self.viewer_hash,
                record["cache_key"],
                [
                    (model["model"], model["fingerprint"])
                    for model in record["models"]
                ]
            ]
            key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]

            self.destination = self.folder / "images" / record["uid"] / key
            paths = [
                self.destination / (view + ".png")
                for view in VIEWS
            ]

            if all(
                path.is_file() and not QImage(str(path)).isNull()
                for path in paths
            ):
                self.results[record["uid"]] = {
                    "images": [str(path) for path in paths]
                }
                continue

            try:
                self.waiting_for_load = True
                self.busy = True
                self.generation += 1
                token = self.generation
                QTimer.singleShot(120000, lambda t=token: self.timeout(t))
                self.load_manifest(record["preview_manifest"])
                return
            except Exception as error:
                self.results[record["uid"]] = {"error": str(error)}
                self.waiting_for_load = False
                self.busy = False
        self.write_gallery()
        self.completed = True
        print("Gallery ready:", self.folder / "gallery.html", flush=True)
        QTimer.singleShot(0, self.close)

    def timeout(self, token):
        if self.busy and token == self.generation:
            self.failed("Timed out loading or capturing preview")

    def callback(self, method):
        token = self.generation

        def received(value):
            if not self.busy or token != self.generation:
                return
            try:
                if not isinstance(value, str) or not value:
                    raise RuntimeError("Viewer returned an empty response")
                method(json.loads(value))
            except Exception as error:
                self.failed("Capture callback failed: " + str(error))

        return received

    def loaded(self, ok):
        if not self.waiting_for_load:
            return
        self.waiting_for_load = False

        if not ok:
            self.failed("Viewer page failed to load")
        else:
            self.poll()

    def poll(self):
        self.view.page().runJavaScript(
            """
            JSON.stringify(
                window.r6Audit ? {ready: r6Audit.ready, error: r6Audit.error} : null
            )
            """,
            self.callback(self.ready)
        )

    def ready(self, state):
        if state and state.get("error"):
            self.failed(state["error"])
        elif state and state.get("ready"):
            self.view.page().runJavaScript(
                """
                (() => {
                    try {
                        return JSON.stringify({
                            images: ["front", "back", "head"].map(
                            view => window.r6Audit.capture(view)
                            )
                        });
                    }
                    catch(error) {
                        return JSON.stringify({error: String(error)});
                    }
                })()
                """,
                self.callback(self.captured)
            )
        else:
            token = self.generation
            QTimer.singleShot(
                250, lambda: self.poll()
                if self.busy and token == self.generation
                else None
            )

    def captured (self, result):
        try:
            if not result or result.get("error"):
                raise RuntimeError((result or {}).get("error", "Empty capture"))
            images = result["images"]
            if len(images) != len(VIEWS):
                raise RuntimeError("Incomplete capture")

            decoded = []
            for value in images:
                if not value.startswith("data:image/png;base64,"):
                    raise RuntimeError("Invalid capture format")

                image = QImage.fromData(base64.b64decode(value.split(",", 1)[1]))
                if image.isNull() or image.width() != 640 or image.height() != 800:
                    raise RuntimeError("Invalid capture dimensions")
                decoded.append(image)
            self.destination.mkdir(parents=True, exist_ok=True)
            paths = []

            for view, image in zip(VIEWS, decoded):
                path = self.destination / (view + ".png")
                temporary = path.with_suffix(".png.tmp")
                if not image.save(str(temporary), "PNG"):
                    raise RuntimeError("Could not save " + str(path))
                temporary.replace(path)
                paths.append(str(path))
            self.results[self.current["uid"]] = {"images": paths}
        except Exception as error:
            self.failed(str(error))
            return

        self.busy = False
        self.write_gallery()
        QTimer.singleShot(0, self.next_operator)

    def failed(self, message):
        self.busy = False
        self.waiting_for_load = False
        self.view.stop()

        print(self.current["name"] + ": " + message, flush=True)
        self.results[self.current["uid"]] = {"error": message}
        self.write_gallery()
        QTimer.singleShot(0, self.next_operator)

    def write_gallery(self):
        escape = html.escape
        cards = []

        for record in self.records:
            result = self.results.get(record["uid"], {})
            pictures = []

            for view, path in zip(VIEWS, result.get("images", [])):
                relative = escape(
                    Path(path).relative_to(self.folder).as_posix(),
                    quote=True
                )
                pictures.append(
                    f'<a href="{relative}">'
                    f'<img src="{relative}" alt="{view}"></a>'
                )
            issues = [
                issue
                for model in record.get("models", [])
                for issue in model.get("issues", [])
            ]
            message = result.get("error", "" if pictures else "Capture pending")

            cards.append(
                "<article><h2>" + escape(record["name"]) + "</h2>"
                "<p>Resource checks: "
                + escape(record.get("resource_checks", "not completed"))
                + "</p><div class='images'>"
                + "".join(pictures)
                + "</div><p>" + escape(message) + "</p><ul>"
                + "".join("<li>" + escape(issue) + "</li>" for issue in issues)
                + "</ul></article>"
            )
        document = """
        <!doctype html>
        <meta charset="utf-8">
        <title>R6 Material Audit</title>
        <style>
            body {
                background: #202428; color: #eee;
                font: 15px system-ui; margin: 24px;
            }
            main {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(420px, 1fr));
                gap: 20px;
            }
            article { 
                background: #30363c;
                padding: 16px;
                border-radius: 8px; 
            }
            .images {
                display: flex;
                gap: 4px;
            }
            .images a {
                width: 33.33%;
            }
            img {
                width: 100%; 
                display: block;
            }
        </style>
        <h1>R6 Material Audit</h1>
        <p>Front · Back · Head. Click an image to enlarge it.
        Resource checks do not establish visual correctness.
        Head framing is approximate.
        No visual approvals or manual notes are recorded here.</p>
        <main>
        """ + "".join(cards) + "</main>"

        path = self.folder / "gallery.html"
        temporary = path.with_suffix(".html.tmp")
        temporary.write_text(document, encoding="utf-8")
        temporary.replace(path)

        save_json(
            self.folder / "thumbnails.json",
            {
                "run_id": self.report["run_id"],
                "capture_version": CAPTURE_VERSION,
                "results": self.results
            }
        )

def main(arguments=None):
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if len(arguments) > 1:
        raise ValueError("Gallery accepts at most 1 audit report path")

    path = (
        Path(arguments[0]).resolve()
        if arguments else application_directory() / "output" / "material-audit" / "audit.json"
    )

    app = QApplication([sys.argv[0]])
    window = GalleryCapture(path)
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    app.exec()

    if not window.completed:
        return 2
    return 1 if any(result.get("error") for result in window.results.values()) else 0

if __name__ == "__main__":
    raise SystemExit(main())