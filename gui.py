"""Basic Desktop UI for browsing default operator registry entries"""

# Tell Blake to do some reverse engineering of the shaders, some are still fucked, need some for 3d prev. -Victor

import sys
import codecs
import re
import json
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QProcess, QSettings
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QPlainTextEdit, QPushButton,
    QHBoxLayout, QVBoxLayout, QSplitter, QTabWidget, QWidget
)

from src.operator_registry import read_operator_registry
from preview_widget import PreviewWidget
from material_inspector import MaterialInspector
from app_runtime import (
    worker_arguments,
    worker_executable,
    application_directory,
    resource_directory,
    external_program_environment
)

class RegistryLoader(QThread):
    loaded = Signal(object)
    failed = Signal(object)

    def __init__(self, archive, parent=None):
        super().__init__(parent)
        self.archive = archive

    def run(self):
        try:
            self.loaded.emit(read_operator_registry(self.archive))
        except Exception as error:
            self.failed.emit(f"{type(error).__name__}: {error}")

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = QSettings("R6ForgeExtractor", "Desktop")
        self.worker = None
        self.export_process = None
        self.registry_game_path = None
        self.complete_exports = self.load_completed_exports()
        self.blender_path = Path(r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe")
        self.close_requested = False
        self.setWindowTitle("R6 Forge Extractor - Alpha")
        self.resize(1250, 780)

        root = QWidget()
        layout = QVBoxLayout(root)
        self.setCentralWidget(root)

        self.game_path = QLineEdit()
        self.game_path.setPlaceholderText("Rainbow Six Siege Installation Folder")
        try:
            from config import GAME_DIR
            self.game_path.setText(str(GAME_DIR))
        except ImportError:
            pass

        self.game_path.setText(self.settings.value("game_directory", self.game_path.text(), type=str))
        self.game_path.textChanged.connect(lambda text: self.settings.setValue("game_directory", text.strip()))

        self.browse = QPushButton("Browse...")
        self.browse.clicked.connect(self.choose_folder)
        self.load = QPushButton("Load operators")
        self.load.clicked.connect(self.load_registry)
        self.index_button = QPushButton("Build / Update Asset Index")
        self.index_button.clicked.connect(self.build_asset_index)
        self.export_button = QPushButton("Export selected operator")
        self.export_button.clicked.connect(self.export_selected)
        self.install_button = QPushButton("Install Blender 4.5 add-on")
        self.install_button.clicked.connect(self.install_blender_addon)
        self.open_button = QPushButton("Open in Blender")
        self.open_button.clicked.connect(self.open_in_blender)
        self.preview_button = QPushButton("Load Preview")
        self.preview_button.clicked.connect(self.load_preview)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Game Folder"))
        toolbar.addWidget(self.game_path, 1)
        toolbar.addWidget(self.browse)
        toolbar.addWidget(self.load)
        toolbar.addWidget(self.index_button)
        toolbar.addWidget(self.export_button)
        toolbar.addWidget(self.install_button)
        toolbar.addWidget(self.open_button)
        toolbar.addWidget(self.preview_button)
        layout.addLayout(toolbar)

        self.blender_edit = QLineEdit()
        self.blender_edit.setPlaceholderText("Path to Blender 4.5 blender.exe")
        self.blender_edit.setText(self.settings.value("blender_executable", str(self.blender_path), type=str))
        self.blender_edit.textChanged.connect(lambda text: self.settings.setValue("blender_executable", text.strip()))

        self.blender_browse = QPushButton("Browse...")
        self.blender_browse.clicked.connect(self.choose_blender)

        blender_row = QHBoxLayout()
        blender_row.addWidget(QLabel("Blender 4.5"))
        blender_row.addWidget(self.blender_edit, 1)
        blender_row.addWidget(self.blender_browse)
        layout.addLayout(blender_row)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search operators...")
        self.search.textChanged.connect(self.filter_operators)
        self.operators = QListWidget()
        self.operators.currentItemChanged.connect(self.show_operator)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.operators)

        self.preview = PreviewWidget(self)
        QApplication.instance().aboutToQuit.connect(self.preview.shutdown)

        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("Default appearance details")

        inspector = QTabWidget()
        inspector.addTab(self.details, "Details")
        self.material_details = MaterialInspector(self.preview)
        inspector.addTab(self.material_details, "Materials / Textures")

        def show_material(text):
            self.material_details.setPlainText(text)
            inspector.setCurrentWidget(self.material_details)

        self.preview.material_bridge.material_selected.connect(show_material)
        self.preview.view.loadStarted.connect(self.material_details.clear)

        panels = QSplitter(Qt.Orientation.Horizontal)
        panels.addWidget(left)
        panels.addWidget(self.preview)
        panels.addWidget(inspector)
        panels.setSizes([230, 650, 340])
        layout.addWidget(panels, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
        self.log.document().setMaximumBlockCount(5000)
        layout.addWidget(self.log)

        self.statusBar().showMessage("Choose the game folder and load operators.")

    def choose_blender(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Choose Blender 4.5", self.blender_edit.text().strip(), "Blender executable (blender.exe)")
        if filename:
            self.blender_edit.setText(filename)

    def selected_blender(self):
        value = self.blender_edit.text().strip()
        if not value or not Path(value).is_file():
            self.choose_blender()
            value = self.blender_edit.text().strip()

        if not value or not Path(value).is_file():
            self.report_error("Choose an existing Blender 4.5 executable")
            return None

        self.blender_path = Path(value).resolve()
        return self.blender_path

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose Rainbow Six Siege Folder", self.game_path.text())
        if folder:
            self.game_path.setText(folder)

    def load_registry(self):
        if self.worker is not None or self.export_process is not None:
            return

        archive = Path(self.game_path.text().strip()) / "datapc64.forge"
        if not archive.is_file():
            self.report_error(f"Archive not found: {archive}")
            return

        self.load.setEnabled(False)
        self.browse.setEnabled(False)
        self.game_path.setEnabled(False)
        self.export_button.setEnabled(False)
        self.install_button.setEnabled(False)
        self.open_button.setEnabled(False)
        self.index_button.setEnabled(False)
        self.statusBar().showMessage("Reading operator registry...")
        self.preview_button.setEnabled(False)
        self.log.appendPlainText(f"Reading {archive}")

        self.worker = RegistryLoader(archive, self)
        self.worker.loaded.connect(self.registry_loaded)
        self.worker.failed.connect(self.report_error)
        self.worker.finished.connect(self.load_finished)
        self.worker.start()

    def registry_loaded(self, operators):
        self.registry_game_path = Path(self.game_path.text().strip()).resolve()
        self.operators.clear()
        for operator in sorted(operators, key=lambda item: item.name.casefold()):
            item = QListWidgetItem(operator.name)
            item.setData(Qt.ItemDataRole.UserRole, operator)
            self.operators.addItem(item)

        self.filter_operators(self.search.text())
        message = f"Loaded {len(operators)} operators."
        self.log.appendPlainText(message)
        self.statusBar().showMessage(message)

    def load_finished(self):
        worker = self.worker
        self.worker = None
        worker.deleteLater()

        self.load.setEnabled(True)
        self.browse.setEnabled(True)
        self.game_path.setEnabled(True)
        self.export_button.setEnabled(True)
        self.install_button.setEnabled(True)
        self.open_button.setEnabled(True)
        self.index_button.setEnabled(True)
        self.preview_button.setEnabled(True)

        if self.close_requested:
            self.close()

    def report_error(self, message):
        self.log.appendPlainText(f"ERROR: {message}")
        self.statusBar().showMessage("Operation failed, see log.")

    def filter_operators(self, text):
        query = text.strip().casefold()
        for index in range(self.operators.count()):
            item = self.operators.item(index)
            item.setHidden(query not in item.text().casefold())

    def show_operator(self, item, previous=None):
        if item is None:
            self.details.clear()
            self.preview.setText("Select an operator")
            return

        operator = item.data(Qt.ItemDataRole.UserRole)
        self.preview.setText(f"{operator.name}\n\nClick Load Preview.")

        lines = [operator.name, f"Operator UID: {operator.uid:016X}"]
        for label, part in (("Head", operator.head), ("Body", operator.body)):
            lines.extend([
                "", label,
                f"Equipment: {part.item_uid:016X}",
                f"Appearance: {part.appearance_uid:016X}",
            ])
            for index, group in enumerate(part.model_groups):
                if group:
                    lines.append(f"Model group {index}:")
                    lines.extend(f"  {uid:016X}" for uid in group)

        self.details.setPlainText("\n".join(lines))

    def export_selected(self):
        if self.worker is not None or self.export_process is not None:
            return

        item = self.operators.currentItem()
        if item is None:
            self.report_error("Select an operator first.")
            return

        game = Path(self.game_path.text().strip()).resolve()
        if game != self.registry_game_path:
            self.report_error("Reload operators after changing the game folder.")
            return

        project = application_directory()
        archive = game / "datapc64_merged_bnk_mesh.forge"
        depgraph = game / "datapc64_ondemand.depgraphbin"
        database = project / "output" / "r6-assets.sqlite"

        for path in (archive, depgraph, database):
            if not path.is_file():
                self.report_error(f"Required file not found: {path}")
                return

        operator = item.data(Qt.ItemDataRole.UserRole)
        parts = []
        for label, part in (("body", operator.body), ("head", operator.head)):
            if not part.model_groups or not part.model_groups[0]:
                self.report_error(f"No primary {label} models for {operator.name}.")
                return
            parts.extend((label, uid) for uid in dict.fromkeys(part.model_groups[0]))

        folder = QFileDialog.getExistingDirectory(self, "Choose export destination", self.settings.value("export_directory", str(project / "output"), type=str),)
        if not folder:
            return
        self.settings.setValue("export_directory", folder)

        folder_name = "".join(
            "_" if character in '<>:"/\\|?*' or ord(character) < 32 else character
            for character in operator.name
        ).strip().rstrip(".")
        if not folder_name:
            self.report_error("Operator name cannot form an export folder.")
            return # you forgot the nyx, no folder name = no can do - shadow
        self.export_directory = Path(folder) / folder_name
        self.export_jobs = [
            (label, uid, self.export_directory / label / f"{uid:016X}")
            for label, uid in parts
        ]
        self.export_operator_uid = operator.uid
        self.complete_exports.pop(operator.uid, None)
        self.settings.remove(f"exports/{operator.uid:016X}")
        self.settings.sync()
        self.export_model_files = tuple(
            destination / f"{uid:016X}.gltf"
            for _, uid, destination in self.export_jobs
        )
        self.export_total = len(self.export_jobs)
        self.export_arguments = [
            "model", str(archive),
            "--depgraph", str(depgraph),
            "--database", str(database)
        ]
        self.log.appendPlainText(
            f"Exporting {operator.name} primary head/body models to {self.export_directory}"
        )

        self.export_process = QProcess(self)
        self.export_process.setWorkingDirectory(str(project))
        self.export_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.export_process.readyReadStandardOutput.connect(self.read_export_output)
        self.export_process.finished.connect(self.export_part_finished)
        self.export_process.errorOccurred.connect(self.export_process_error)
        self.set_export_busy(True)
        self.start_export_part()

    def set_export_busy(self, busy):
        for widget in (
            self.export_button, self.install_button, self.open_button,
            self.index_button, self.load, self.browse, self.game_path,
            self.operators, self.search, self.preview_button,
            self.blender_edit, self.blender_browse
        ):
            widget.setEnabled(not busy)

    def start_export_part(self):
        label, uid, destination = self.export_jobs.pop(0)
        number = self.export_total - len(self.export_jobs)
        self.export_expected_files = destination / f"{uid:016X}.gltf"
        self.export_decoder = codecs.getincrementaldecoder("utf-8")("replace")

        self.statusBar().showMessage(f"Exporting {number}/{self.export_total}: {label} {uid:016X}")
        self.export_process.start(worker_executable(), worker_arguments(
            "cli",
            self.export_arguments + [
                    "--uid",
                    f"{uid:016X}", "-o", str(destination)
                ],
            ),
        )

    def read_export_output(self):
        if self.export_process is None:
            return

        data = bytes(self.export_process.readAllStandardOutput())
        text = self.export_decoder.decode(data)
        if text:
            self.log.moveCursor(QTextCursor.MoveOperation.End)
            self.log.insertPlainText(text)
            self.log.ensureCursorVisible()

    def export_part_finished(self, exit_code, exit_status):
        if self.export_process is None:
            return

        self.read_export_output()
        tail = self.export_decoder.decode(b"", final=True)
        if tail:
            self.log.moveCursor(QTextCursor.MoveOperation.End)
            self.log.insertPlainText(tail)

        if exit_status != QProcess.ExitStatus.NormalExit or exit_code != 0:
            self.finish_export(False, f"Exporter failed (exit {exit_code}), see log.")
        elif not self.export_expected_files.is_file():
            self.finish_export(False, "Exporter finished without the expected glTF.")
        elif self.export_jobs:
            self.start_export_part()
        else:
            self.complete_exports[self.export_operator_uid] = self.export_model_files
            self.save_completed_export(self.export_operator_uid, self.export_model_files)
            self.finish_export(True, f"Primary head/body export complete: {self.export_directory}")

    def export_process_error(self, error):
        if self.export_process is None:
            return

        if error == QProcess.ProcessError.FailedToStart:
            self.finish_export(False, self.export_process.errorString())

    def finish_export(self, success, message):
        process = self.export_process
        self.export_process = None
        self.export_jobs = []
        process.deleteLater()
        self.set_export_busy(False)

        if success:
            self.log.appendPlainText(message)
            self.statusBar().showMessage(message)
        else:
            self.report_error(message)

        if self.close_requested:
            self.close()

    def install_blender_addon(self):
        if self.worker is not None or self.export_process is not None:
            return

        blender = self.selected_blender()
        if blender is None:
            return
        self.blender_path = blender

        script = resource_directory() / "install_blender_addon.py"
        if not script.is_file():
            self.report_error(f"Installer not found: {script}")
            return

        self.export_jobs = []
        self.export_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.export_process = QProcess(self)
        self.export_process.setWorkingDirectory(str(script.parent))
        self.export_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.export_process.readyReadStandardOutput.connect(self.read_export_output)
        self.export_process.finished.connect(self.blender_install_finished)
        self.export_process.errorOccurred.connect(self.export_process_error)

        self.set_export_busy(True)
        self.log.appendPlainText(f"Checking and installing into {blender}")
        self.statusBar().showMessage("Installing Blender 4.5 add-on...")
        self.export_process.start(
            worker_executable(),
            worker_arguments("install", [str(blender)]),
        )

    def blender_install_finished(self, exit_code, exit_status):
        if self.export_process is None:
            return

        self.read_export_output()
        tail = self.export_decoder.decode(b"", final=True)
        if tail:
            self.log.moveCursor(QTextCursor.MoveOperation.End)
            self.log.insertPlainText(tail)

        success = exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0
        message = "R6 add-on installed and enabled in Blender 4.5" if success else f"Blender installation failed (exit {exit_code}), see log"
        self.finish_export(success, message)

    def load_completed_exports(self):
        exports = {}
        for key in self.settings.allKeys():
            if not key.startswith("exports/"):
                continue
            try:
                uid = int(key.removeprefix("exports/"), 16)
                values = json.loads(self.settings.value(key, "", type=str))
                if not isinstance(values, list) or not values:
                    continue
                if not all(isinstance(value, str) for value in values):
                    continue
                paths = tuple(Path(value) for value in values)
                if not all(path.is_absolute() and path.suffix.lower() == ".gltf" for path in paths):
                    continue
                exports[uid] = paths
            except (ValueError, TypeError):
                continue
        return exports

    def save_completed_export(self, uid, models):
        self.settings.setValue(f"exports/{uid:016X}", json.dumps([str(path.resolve()) for path in models]))
        self.settings.sync()

    def open_in_blender(self):
        if self.worker is not None or self.export_process is not None:
            return

        item = self.operators.currentItem()
        if item is None:
            self.report_error("Select an exported operator first") # duh
            return

        operator = item.data(Qt.ItemDataRole.UserRole)
        models = self.complete_exports.get(operator.uid)
        if not models or any(not path.is_file() for path in models):
            self.report_error("Export this operator successfully first.")
            return

        blender = self.selected_blender()
        if blender is None:
            return

        script = resource_directory() / "open_operator_blender.py"
        if not script.is_file():
            self.report_error(f"Launch script not found: {script}")
            return

        self.blender_path = blender
        self.blender_launch_script = script
        self.blender_launch_models = models
        self.blender_version_output = bytearray()

        self.export_process = QProcess(self)
        self.export_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.export_process.readyReadStandardOutput.connect(self.read_blender_version)
        self.export_process.finished.connect(self.blender_version_finished)
        self.export_process.errorOccurred.connect(self.export_process_error)
        self.set_export_busy(True)
        self.statusBar().showMessage("Checking Blender version...")
        with external_program_environment():
            self.export_process.start(str(blender), ["--version"])

    def read_blender_version(self):
        if self.export_process is not None:
            self.blender_version_output.extend(bytes(self.export_process.readAllStandardOutput()))

    def blender_version_finished(self, exit_code, exit_status):
        if self.export_process is None:
            return

        self.read_blender_version()
        output = self.blender_version_output.decode("utf-8", errors="replace")
        match = re.search(r"^Blender (\d+)\.(\d+)\.", output, re.MULTILINE)
        valid = exit_status == QProcess.ExitStatus.NormalExit and exit_code == 0 and match is not None and match.groups() == ("4", "5")
        if not valid:
            self.finish_export(False, "Select Blender 4.5 to open ths operator")
            return

        arguments = [
            "--factory-startup",
            "--addons", "io_scene_r6",
            "--python", str(self.blender_launch_script),
            "--",
            *[str(path) for path in self.blender_launch_models],
        ]
        with external_program_environment():
            started, _ = QProcess.startDetached(str(self.blender_path), arguments, str(self.blender_launch_script.parent))
        self.finish_export(started, "Blender launched, check the new window for the imported operator." if started else "Could not launch Blender.")

    def build_asset_index(self):
        if self.worker is not None or self.export_process is not None:
            return

        value = self.game_path.text().strip()
        game = Path(value).resolve()
        if not value or not (game / "datapc64.forge").is_file():
            self.report_error("Choose a Siege folder containing datapc64.forge")
            return

        project = application_directory()
        script = resource_directory() / "main.py"
        if not script.is_file():
            self.report_error(f"CLI not found: {script}")
            return

        self.index_database = project / "output" / "r6-assets.sqlite"
        self.export_jobs = []
        self.export_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.export_process = QProcess(self)
        self.export_process.setWorkingDirectory(str(project))
        self.export_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.export_process.readyReadStandardOutput.connect(self.read_export_output)
        self.export_process.finished.connect(self.asset_index_finished)
        self.export_process.errorOccurred.connect(self.export_process_error)

        self.set_export_busy(True)
        self.log.appendPlainText(f"Building/updating asset index from {game}")
        self.statusBar().showMessage("Indexing game archives...")
        self.export_process.start(
            worker_executable(),
            worker_arguments(
                "cli",
                ["index", str(game), "-o", str(self.index_database)]
            ),
        )

    def asset_index_finished(self, exit_code, exit_status):
        if self.export_process is None:
            return

        self.read_export_output()
        tail = self.export_decoder.decode(b"", final=True)
        if tail:
            self.log.moveCursor(QTextCursor.MoveOperation.End)
            self.log.insertPlainText(tail)

        if exit_status != QProcess.ExitStatus.NormalExit or exit_code != 0:
            self.finish_export(False, f"Indexing reported errors (exit {exit_code}), see log. The index may be partial.")
        elif not self.index_database.is_file():
            self.finish_export(False, "Indexing finished without a database.")
        else:
            self.finish_export(True, f"Asset index updated: {self.index_database}")

    def load_preview(self):
        if self.worker is not None or self.export_process is not None:
            return

        item = self.operators.currentItem()
        if item is None:
            self.report_error("Select an operator first")
            return

        game = Path(self.game_path.text().strip()).resolve()
        if game != self.registry_game_path:
            self.report_error("Reload oeprators after changing the game folder")
            return

        operator = item.data(Qt.ItemDataRole.UserRole)
        self.preview_output = ""
        self.export_jobs = []
        self.export_decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.export_process = QProcess(self)
        self.export_process.setWorkingDirectory(str(application_directory()))
        self.export_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.export_process.readyReadStandardOutput.connect(self.read_preview_output)
        self.export_process.finished.connect(self.preview_prepared)
        self.export_process.errorOccurred.connect(self.export_process_error)

        self.set_export_busy(True)
        self.statusBar().showMessage(f"Preparing {operator.name} preview...")
        self.export_process.start(
            worker_executable(),
            worker_arguments("preview", [str(game), f"{operator.uid:016X}"])
        )

    def read_preview_output(self):
        if self.export_process is None:
            return
        text = self.export_decoder.decode(bytes(self.export_process.readAllStandardOutput()))
        self.preview_output += text
        if text:
            self.log.moveCursor(QTextCursor.MoveOperation.End)
            self.log.insertPlainText(text)
            self.log.ensureCursorVisible()

    def preview_prepared(self, exit_code, exit_status):
        if self.export_process is None:
            return

        self.read_preview_output()
        self.preview_output += self.export_decoder.decode(b"", final=True)
        if exit_status != QProcess.ExitStatus.NormalExit or exit_code != 0:
            self.finish_export(False, "Preview preparation failed, see log")
            return

        prefixes = ("Preview ready: ", "Using cached preview: ")
        paths = [
            line[len(prefix):].strip()
            for line in self.preview_output.splitlines()
            for prefix in prefixes
            if line.startswith(prefix)
        ]
        try:
            if not paths:
                raise ValueError("Worker did not return a preview manifest")
            self.preview.load_manifest(paths[-1])
        except Exception as error:
            self.finish_export(False, f"Cannot load preview: {error}")
            return

        self.finish_export(True, "Preview data ready, loading the viewer.")

    def closeEvent(self, event):
        if self.worker is not None or self.export_process is not None:
            self.close_requested = True
            self.statusBar().showMessage("Closing after the current operation finishes...")
            event.ignore()
            return

        super().closeEvent(event)

def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow()
    window.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())
