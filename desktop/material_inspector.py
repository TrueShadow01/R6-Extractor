"""Selected material details and cached texture thumbnails"""

import json
from pathlib import Path
from urllib.parse import unquote

from PySide6.QtCore import QSize
from PySide6.QtGui import QIcon, QImageReader, QPixmap
from PySide6.QtWidgets import QWidget, QVBoxLayout, QPlainTextEdit, QListWidget, QListWidgetItem, QComboBox

class MaterialInspector(QWidget):
    def __init__(self, preview, parent=None):
        super().__init__(parent)
        self.preview = preview
        self.cached_root = None
        self.documents = []

        self.details = QPlainTextEdit()
        self.capture_id = None
        self.layer_choices = QComboBox()
        self.layer_choices.setPlaceholderText("Shift-click to capture layers")
        self.layer_choices.setEnabled(False)
        self.layer_choices.currentIndexChanged.connect(self.choose_layer)
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("Load a preview then Shift-click a surface.")

        self.textures = QListWidget()
        self.textures.setIconSize(QSize(128, 128))
        self.textures.setWordWrap(True)
        self.textures.setSpacing(6)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.details, 2)
        layout.addWidget(self.layer_choices)
        layout.addWidget(self.textures, 3)

    def choose_layer(self, index):
        if index < 0 or self.capture_id is None:
            return

        arguments = json.dumps([index, self.capture_id])
        self.preview.view.page().runJavaScript(f"window.selectInspectorLayer?.(...{arguments});")

    def clear(self):
        self.capture_id = None
        self.layer_choices.blockSignals(True)
        self.layer_choices.clear()
        self.layer_choices.setEnabled(False)
        self.layer_choices.blockSignals(False)
        self.details.clear()
        self.textures.clear()
        self.cached_root = None
        self.documents = []

    def setPlainText(self, text):
        try:
            report = json.loads(text)
        except (ValueError, TypeError):
            report = None

        if isinstance(report, dict) and isinstance(report.get("layers"), list):
            self.layer_choices.blockSignals(True)
            try:
                self.layer_choices.clear()
                self.layer_choices.addItems(report["layers"])
                self.layer_choices.setCurrentIndex(report["selected"])
                self.layer_choices.setEnabled(bool(report["layers"]))
                self.capture_id = report["captureId"]
            finally:
                self.layer_choices.blockSignals(False)
            text = report["text"]

        self.details.setPlainText(text)
        self.textures.clear()

        root = self.preview.model_root
        if root is None:
            return
        root = Path(root).resolve()

        material_name = next(
            (
                line.removeprefix("Material: ")
                for line in text.splitlines()
                if line.startswith("Material: ")
            ),
            None
        )
        if not material_name:
            return

        try:
            if root != self.cached_root:
                self.documents = [
                    (path, json.loads(path.read_text(encoding="utf-8")))
                    for path in sorted(root.rglob("*.gltf"))
                ]
                self.cached_root = root

            for path, document in self.documents:
                for material in document.get("materials", []):
                    if material.get("name") == material_name:
                        self.show_textures(root, path, document, material)
                        return

            self.textures.addItem("No matching cached material found.")
        except (OSError, ValueError, KeyError, IndexError, TypeError) as error:
            self.textures.addItem(f"Texture inspection failed: {error}")

    def show_textures(self, root, gltf_path, document, material):
        references = []

        def gltf_texture(role, info):
            if info is None:
                return
            texture = document["textures"][info["index"]]
            image = document["images"][texture["source"]]
            if image.get("uri"):
                references.append((role, image["uri"]))

        pbr = material.get("pbrMetallicRoughness", {})
        gltf_texture("Base Color", pbr.get("baseColorTexture"))
        gltf_texture("Normal", material.get("normalTexture"))
        gltf_texture("Metallic / Roughness", pbr.get("metallicRoughnessTexture"))
        gltf_texture("Emissive", material.get("emissiveTexture"))
        gltf_texture("Occlusion", material.get("occlusionTexture"))

        extras = material.get("extras", {})
        for role, key in (("Packed: R metalness, G gloss", "siegePackedMaterialTexture"), ("Clothing mask", "siegeMaskTexture")):
            if extras.get(key):
                references.append((role, extras[key]))

        for index, filename in enumerate(extras.get("siegeDetailNormalTextures", []), start=1):
            references.append((f"Detail normal {index}", filename))

        references.extend(extras.get("siegeShaderTextures", {}).items())

        if not references:
            self.textures.addItem("This material has no exported textures.")
            return

        # Victor brough thumbnails. Blake can stop guessing from UIDs - Nyx
        for role, uri in dict.fromkeys(references):
            path = (gltf_path.parent / unquote(uri)).resolve()
            item = QListWidgetItem(f"{role}\n{Path(unquote(uri)).name}")
            self.textures.addItem(item)

            if not path.is_relative_to(root) or not path.is_file():
                item.setText(item.text() + "\nMissing or unsupported file")
                continue

            item.setToolTip(str(path))
            reader = QImageReader(str(path))
            size = reader.size()

            if size.isValid():
                item.setText(item.text() + f"\n{size.width()} x {size.height()}")
                scale = min(128 / size.width(), 128 / size.height(), 1.0)
                reader.setScaledSize(QSize(
                    max(1, round(size.width() * scale)),
                    max(1, round(size.height() * scale))
                ))

            image = reader.read()
            if image.isNull():
                item.setText(item.text() + "\nThumbnail unavailable")
            else:
                item.setIcon(QIcon(QPixmap.fromImage(image)))