"""Local web viewer for prepared R6 model data"""

import json
import mimetypes
import threading
import uuid
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit
from html import escape

from PySide6.QtCore import QUrl, QTimer, QObject, Signal, Slot
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PySide6.QtGui import QColor
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebChannel import QWebChannel

from app_runtime import resource_directory
from desktop.preview_cache import CacheLease

class MaterialBridge(QObject):
    material_selected = Signal(str)

    @Slot(str)
    def showMaterial(self, text):
        self.material_selected.emit(text)

class PreviewWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.viewer_root = resource_directory() / "viewer"
        self.model_root = None
        self.cache_lease = None
        self.manifest = None
        self.token = uuid.uuid4().hex

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel("Select an operator and click Load Preview.")
        self.view = QWebEngineView()
        self.material_bridge = MaterialBridge(self)
        self.web_channel = QWebChannel(self.view.page())
        self.web_channel.registerObject("materialBridge", self.material_bridge)
        self.view.page().setWebChannel(self.web_channel)
        self.view.page().setBackgroundColor(QColor("#24282d"))
        self.view.setStyleSheet("background-color: #24282d;")
        self.show_welcome()
        layout.addWidget(self.label)
        layout.addWidget(self.view, 1)

        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                path = unquote(urlsplit(self.path).path)
                prefix = f"/{owner.token}/"
                if not path.startswith(prefix):
                    self.send_error(404)
                    return

                relative = path[len(prefix):]
                try:
                    if relative == "manifest.json":
                        if owner.manifest is None:
                            self.send_error(404)
                            return
                        payload = json.dumps(owner.manifest).encode("utf-8")
                        content_type = "application/json"
                    else:
                        category, separator, filename = relative.partition("/")
                        roots = {
                            "viewer": owner.viewer_root,
                            "model": owner.model_root
                        }
                        root = roots.get(category)
                        if not separator or root is None:
                            self.send_error(404)
                            return

                        root = root.resolve()
                        target = (root / filename).resolve()
                        if not target.is_relative_to(root) or not target.is_file():
                            self.send_error(404)
                            return

                        payload = target.read_bytes()
                        content_type = "text/javascript" if target.suffix == ".js" else mimetypes.guess_type(target.name)[0] or "application/octet-stream"

                    self.send_response(200)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(payload)))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    self.send_error(404)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

        self.observed_signature = self.viewer_signature()
        self.loaded_signature = self.observed_signature
        self.viewer_changed_at = time.monotonic()
        self.reload_check_pending = False

        self.reload_timer = QTimer(self)
        self.reload_timer.setInterval(500)
        self.reload_timer.timeout.connect(self.check_viewer_changes)
        if not getattr(sys, "frozen", False):
            self.reload_timer.start()

    def show_welcome(self):
        self.preparing = False
        self.label.hide()
        self.view.setHtml("""
            <!doctype html>
            <html>
                <head>
                    <meta charset="utf-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1">
                    <style>
                        :root {
                            color-scheme: dark;
                        }

                        * {
                            box-sizing: border-box;
                        }

                        body {
                            margin: 0;
                            min-height: 100vh;
                            display: grid;
                            place-items: center;
                            padding: clamp(20px, 5vw, 48px);
                            background: #1c2129;
                            color: #e8edf4;
                            font: 14px / 1.5 "Segoe UI", sans-serif;
                        }

                        main {
                            width: min(100%, 560px);
                        }

                        .brand {
                            display: flex;
                            align-items: center;
                            gap: 10px;
                            margin-bottom: 28px;
                            color: #aab7c8;
                            font-size: 11px;
                            font-weight: 600;
                            letter-spacing: 2px;
                            text-transform: uppercase;
                        }

                        .badge {
                            display: grid;
                            place-items: center;
                            width: 36px;
                            height: 36px;
                            border: 1px solid #37635f;
                            border-radius: 10px;
                            background: #223e40;
                            color: #6edbd1;
                            letter-spacing: 0;
                            font-size: 14px;
                        }

                        h1 {
                            margin: 0 0 12px;
                            font-size: 26px;
                            font-weight: 600;
                            line-height: 1.3;
                        }

                        .intro {
                            margin: 0 0 28px;
                            max-width: 440px;
                            color: #aab7c8;
                            line-height: 1.65;
                        }

                        ol {
                            margin: 0;
                            padding: 0;
                            list-style: none;
                            border: 1px solid #354050;
                            border-radius: 12px;
                            overflow: hidden;
                            background: #202630;
                        }

                        li {
                            display: grid;
                            grid-template-columns: 28px minmax(0, 1fr);
                            gap: 14px;
                            padding: 17px 20px;
                        }

                        li + li {
                            border-top: 1px solid: #303947;
                        }

                        .number {
                            display: grid;
                            place-items: center;
                            align-self: start;
                            width: 28px;
                            height: 28px;
                            border-radius: 8px;
                            background: #293540;
                            color: #6edbd1;
                            font-size: 12px;
                            font-weight: 600;
                        }

                        h2 {
                            margin: 0 0 3px;
                            font-size: 14px;
                            font-weight: 600;
                        }

                        li p {
                            margin: 0;
                            color: #94a3b8;
                            font-size: 13px;
                        }

                        strong {
                            color: #c7d2e1;
                            font-weight: 600;
                        }

                        footer {
                            margin-top: 20px;
                            padding-left: 2px;
                            color: #94a3b8;
                            font-size: 12px;
                        }

                        @media (max-width: 360px) {
                            li {
                                padding: 14px;
                                gap: 10px;
                            }

                            .brand {
                                margin-bottom: 20px;
                            }
                        }
                    </style>
                </head>
                <body>
                    <main>
                    <div class="brand">
                        <span class="badge" aria-hidden="true">R6</span>
                        Forge Extractor
                    </div>

                    <h1>Getting started</h1>
                    <p class="intro">
                        Preview default appearances, inspect materials and export models for Blender.
                    </p>

                    <ol aria-label="Getting started">
                        <li>
                            <span class="number" aria-hidden="true">01</span>
                            <div>
                                <h2>Connect your game</h2>
                                <p>Choose your Siege folder in <strong>Settings</strong>.
                                Already configured? Go to step 2.</p>
                            </div>
                        </li>
                        <li>
                            <span class="number" aria-hidden="true">02</span>
                            <div>
                                <h2>Explore the model</h2>
                                <p>Click <strong>Load Preview</strong>.
                                Shift-click a surface to inspect its material.</p>
                            </div>
                        </li>
                    </ol>

                    <footer>
                        First previews take longer. Cached previews load faster.
                    </footer>
                    </main>
                </body>
            </html>
        """)

    def show_state(self, title, message, *, loading=False):
        self.preparing = loading
        self.label.hide()
        progress = '<progress aria-label="Preparing preview"></progress>' if loading else ""
        self.view.setHtml(f"""
            <!doctype html>
            <html lang="en">
                <head>
                    <meta charset="utf-8">
                    <style>
                        :root {{
                            color-scheme: dark;
                        }}

                        * {{
                            box-sizing: border-box;
                        }}

                        body {{
                            margin: 0;
                            min-height: 100vh;
                            display: grid;
                            place-items: center;
                            padding: 24px;
                            background: #24282d;
                            color: #e8edf4;
                            font: 14px / 1.5 "Segoe UI", sans-serif;
                        }}

                        main {{
                            width: min(100%, 440px);
                            padding: 28px;
                            border: 1px solid #354050;
                            border-radius: 12px;
                            background: #202630;
                        }}

                        h1 {{
                            margin: 0 0 10px;
                            font-size: 22px;
                            font-weight: 600;
                        }}

                        p {{
                            margin: 0;
                            color: #aab7c8;
                            white-space: pre-line;
                            overflow-wrap: anywhere;
                        }}

                        progress {{
                            display: block;
                            width: 100%;
                            height: 6px;
                            margin-top: 24px;
                            accent-color: #42c8bd;
                        }}
                    </style>
                </head>
                <body>
                    <main>
                        <h1>{escape(str(title))}</h1>
                        <p>{escape(str(message))}</p>
                        {progress}
                    </main>
                </body>
            </html>
        """)

    def setText(self, text):
        title, _, message = str(text).partition("\n\n")
        self.show_state(
            title,
            message or "Choose an operator from the list, then click Load Preview."
        )

    def show_loading(self, name):
        self.show_state(
            f"Preparing {name}",
            "Preparing Geometry and Textures.\n"
            "The first preview can take longer. Cached previews load faster.",
            loading=True
        )

    def show_failure(self, message):
        self.show_state(
            "Preview could not load",
            f"{message}\n\nCheck the log, then click Load Preview to try again."
        )
    
    def load_manifest(self, path):
        lease = CacheLease(Path(path).resolve().parent)
        try:
            self._load_manifest(path)
        except Exception:
            lease.close()
            raise

        previous = self.cache_lease
        self.cache_lease = lease
        if previous is not None:
            previous.close()

    def _load_manifest(self, path):
        path = Path(path).resolve()
        document = json.loads(path.read_text(encoding="utf-8"))
        root = path.parent

        models = []
        for relative in document["models"]:
            model = (root / relative).resolve()
            if not model.is_relative_to(root) or not model.is_file():
                raise ValueError(f"Invalid preview model: {relative}")
            models.append(f"/{self.token}/model/{quote(relative, safe='/')}")

        if not models:
            raise ValueError("Preview contains no models")

        self.model_root = root
        self.manifest = {
            "name": document["operator_name"],
            "models": models
        }
        self.label.setText(f"{document['operator_name']} - basic glTF material preview")
        self.label.show()
        self.preparing = False
        port = self.server.server_address[1]
        self.view.load(QUrl(f"http://127.0.0.1:{port}/{self.token}/viewer/index.html"))

    def viewer_signature(self):
        return tuple(
            (path.name, path.stat().st_mtime_ns, path.stat().st_size)
            for path in sorted(self.viewer_root.iterdir())
            if path.is_file() and path.suffix in {".js", ".html", ".css"}
        )

    def check_viewer_changes(self):
        try:
            signature = self.viewer_signature()
        except OSError:
            return # An editor my briefly replace a file during saving

        if signature != self.observed_signature:
            self.observed_signature = signature
            self.viewer_changed_at = time.monotonic()
            return

        if signature == self.loaded_signature or time.monotonic() - self.viewer_changed_at < 1.0 or self.manifest is None or self.reload_check_pending:
            return

        self.reload_check_pending = True

        def finished(reloaded):
            self.reload_check_pending = False
            if reloaded:
                self.loaded_signature = signature

        self.view.page().runJavaScript(
            """
            (() => {
                const button = document.querySelector("#reload");
                if (!button || button.disabled || typeof button.onclick !== "function") {
                    return false;
                }

                button.click();
                return true;
            })()
            """,
            finished
        )

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        if self.cache_lease is not None:
            self.cache_lease.close()
            self.cache_lease = None