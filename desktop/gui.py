"""Basic Desktop UI for browsing default operator registry entries"""

import sys
import codecs
import re
import json
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QProcess, QSettings
from PySide6.QtGui import QTextCursor, QIcon, QColor, QPalette, QFont
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QPlainTextEdit, QPushButton,
    QHBoxLayout, QVBoxLayout, QSplitter, QTabWidget, QWidget,
    QCheckBox, QMessageBox
)

from src.operator_registry import read_operator_registry
from src.model import operator_model_jobs
from desktop.preview_widget import PreviewWidget
from desktop.preview_cache import CacheLease, clean_preview_cache
from desktop.material_inspector import MaterialInspector
from app_runtime import (
    worker_arguments,
    worker_executable,
    application_directory,
    resource_directory,
    external_program_environment
)

class LogOutput(QPlainTextEdit):
    def appendPlainText(self, text):
        # Isaac, separate messages. Not every chunk is a new line - Nyx
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        if not cursor.atBlockStart():
            cursor.insertText("\n")

        cursor.insertText(text)

        if not cursor.atBlockStart():
            cursor.insertText("\n")

        self.setTextCursor(cursor)
        self.ensureCursorVisible()

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

class CacheCleaner(QThread):
    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.options = options
        self.result = None

    def run(self):
        try:
            self.result = clean_preview_cache(**self.options)
        except Exception as error:
            self.result = {
                "count": 0,
                "bytes": 0,
                "skipped": 0,
                "errors": [str(error)]
            }

class MainWindow(QMainWindow):
    def __init__(self, *, developer_mode=False):
        super().__init__()
        self.settings = QSettings("R6ForgeExtractor", "Desktop")
        self.worker = None
        self.preview_job_lease = None
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

        self.settings_button = QPushButton("Settings")
        self.settings_button.setCheckable(True)
        self.settings_button.setToolTip("Paths, asset index and maintenance")

        toolbar = QHBoxLayout()
        toolbar.addWidget(self.load)
        toolbar.addStretch()
        toolbar.addWidget(self.preview_button)
        toolbar.addWidget(self.export_button)
        toolbar.addWidget(self.open_button)
        toolbar.addWidget(self.settings_button)
        layout.addLayout(toolbar)

        self.settings_panel = QWidget()
        settings_layout = QVBoxLayout(self.settings_panel)
        settings_layout.setContentsMargins(0, 8, 0, 8)

        game_row = QHBoxLayout()
        game_row.addWidget(QLabel("Game folder"))
        game_row.addWidget(self.game_path, 1)
        game_row.addWidget(self.browse)
        settings_layout.addLayout(game_row)

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
        settings_layout.addLayout(blender_row)

        self.clear_cache_button = QPushButton("Clear preview cache")
        self.clear_cache_button.clicked.connect(self.clear_preview_cache)

        maintenance_row = QHBoxLayout()
        maintenance_row.addWidget(self.index_button)
        maintenance_row.addWidget(self.install_button)
        maintenance_row.addWidget(self.clear_cache_button)
        maintenance_row.addStretch()
        settings_layout.addLayout(maintenance_row)

        layout.addWidget(self.settings_panel)
        self.settings_button.toggled.connect(self.settings_panel.setVisible)

        needs_setup = not (Path(self.game_path.text().strip()) / "datapc64.forge").is_file()
        self.settings_button.setChecked(needs_setup)
        self.settings_panel.setVisible(needs_setup)

        self.blender_options_button = QPushButton("Blender options")
        self.blender_options_button.setCheckable(True)
        toolbar.insertWidget(toolbar.indexOf(self.open_button) + 1, self.blender_options_button)

        self.blender_options_panel = QWidget()
        blender_options_layout = QVBoxLayout(self.blender_options_panel)
        blender_options_layout.setContentsMargins(0, 4, 0, 8)
        blender_options_layout.addWidget(QLabel("These options apply when opening in Blender."))

        self.ik_checkbox = QCheckBox("Create experimental IK controls")
        self.ik_checkbox.setToolTip("Save as .blend to retain the controls.")
        blender_options_layout.addWidget(self.ik_checkbox)

        self.portable_checkbox = QCheckBox("Save as portable .blend")
        self.portable_checkbox.setToolTip("Pack textures, show the report and confirm before replacing an existing file.")
        blender_options_layout.addWidget(self.portable_checkbox)

        layout.addWidget(self.blender_options_panel)
        self.blender_options_panel.hide()
        self.blender_options_button.toggled.connect(self.blender_options_panel.setVisible)

        def update_blender_options_label():
            enabled = (
                int(self.ik_checkbox.isChecked()) 
                + int(self.portable_checkbox.isChecked())
            )
            self.blender_options_button.setText(
                f"Blender options ({enabled})"
                if enabled else "Blender options"
            )

        self.ik_checkbox.toggled.connect(update_blender_options_label)
        self.portable_checkbox.toggled.connect(update_blender_options_label)

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
        self.details.setPlaceholderText("Select an operator to see its default appearance and asset IDs.")

        details_page = QWidget()
        details_layout = QVBoxLayout(details_page)
        details_layout.setContentsMargins(10, 10, 10, 10)
        details_layout.setSpacing(8)
        details_heading = QLabel("Default Appearance")
        details_heading.setObjectName("inspectorHeading")
        details_layout.addWidget(details_heading)
        details_layout.addWidget(self.details, 1)

        inspector = QTabWidget()
        inspector.addTab(details_page, "Details")
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

        self.log = LogOutput()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
        self.log.document().setMaximumBlockCount(5000)
        layout.addWidget(self.log)
        self.log.hide()

        self.log_toggle = QPushButton("Show Log")
        self.log_toggle.setCheckable(True)

        def toggle_log(visible):
            self.log.setVisible(visible)
            self.log_toggle.setText("Hide Log" if visible else "Show Log")
            if visible:
                self.log.moveCursor(QTextCursor.MoveOperation.End)
                self.log.ensureCursorVisible()

        self.log_toggle.toggled.connect(toggle_log)

        self.copy_log_button = QPushButton("Copy Log")
        self.copy_log_button.setEnabled(False)
        self.copy_log_button.clicked.connect(lambda: QApplication.clipboard().setText(self.log.toPlainText()))
        self.log.textChanged.connect(lambda: self.copy_log_button.setEnabled(not self.log.document().isEmpty()))

        self.statusBar().addPermanentWidget(self.log_toggle)
        self.statusBar().addPermanentWidget(self.copy_log_button)

        self.statusBar().showMessage("Choose the game folder and load operators.")

        self.audit_actions = None
        if developer_mode:
            from devtools.audit.actions import AuditActions
            self.audit_actions = AuditActions(self)

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
        self.log_toggle.setChecked(True)
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
        try:
            parts = operator_model_jobs(operator, database)
        except (OSError, ValueError) as error:
            self.report_error(str(error))
            return

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
            self.blender_edit, self.blender_browse, self.ik_checkbox,
            self.portable_checkbox, self.clear_cache_button
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
        if self.preview_job_lease is not None:
            self.preview_job_lease.close()
            self.preview_job_lease = None

        process = self.export_process
        self.export_process = None
        self.export_jobs = []
        process.deleteLater()
        self.set_export_busy(False)

        if success:
            self.log.appendPlainText(message)
            self.statusBar().showMessage(message)
        else:
            if self.preview.preparing:
                self.preview.show_failure(message)
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

        script = resource_directory() / "blender" / "install_addon.py"
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

        script = resource_directory() / "blender" / "open_operator.py"
        if not script.is_file():
            self.report_error(f"Launch script not found: {script}")
            return

        self.blender_launch_save = None
        if self.portable_checkbox.isChecked():
            folder = models[0].parents[2]
            filename, _ = QFileDialog.getSaveFileName(self, "Save portable Blender model", str(folder / f"{folder.name}.blend"), "Blender files (*.blend)", options=QFileDialog.Option.DontConfirmOverwrite)
            if not filename:
                return
            if not filename.lower().endswith(".blend"):
                filename += ".blend"
            destination = Path(filename).expanduser().resolve()
            if destination.exists():
                if not destination.is_file():
                    self.report_error("Choose a file, not a directory.")
                    return
                answer = QMessageBox.question(self, "Replace Blender File?", f"Replace the existing file?\n\n{destination}", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
                if answer != QMessageBox.StandardButton.Yes:
                    return
            self.blender_launch_save = str(destination)

        self.blender_path = blender
        self.blender_launch_script = script
        self.blender_launch_models = models
        self.blender_launch_ik = self.ik_checkbox.isChecked()
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
            *(["--experimental-ik"] if self.blender_launch_ik else []),
            *(["--save-blend", self.blender_launch_save] if self.blender_launch_save else []),
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
            self.report_error("Reload operators after changing the game folder")
            return

        operator = item.data(Qt.ItemDataRole.UserRole)

        try:
            self.preview_job_lease = CacheLease()
        except Exception as error:
            self.report_error(str(error))
            return

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
        self.preview.show_loading(operator.name)
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
        self.start_cache_cleanup(keep=self.preview.model_root, remove=True)

    def clear_preview_cache(self):
        self.start_cache_cleanup()

    def start_cache_cleanup(self, *, keep=None, remove=False):
        if self.close_requested or self.worker is not None or self.export_process is not None:
            return

        protected = [path for models in self.load_completed_exports().values() for path in models]
        protected.extend(getattr(self, "blender_launch_models", ()))

        worker = CacheCleaner(
            {
                "keep": keep,
                "protected": protected,
                "remove": remove
            },
            self
        )
        self.worker = worker
        self.set_export_busy(True)
        if keep is None:
            self.statusBar().showMessage("Cleaning preview cache..." if remove else "Checking preview cache...")
        worker.finished.connect(self.cache_cleanup_finished)
        worker.start()

    def cache_cleanup_finished(self):
        worker = self.worker
        result = worker.result
        options = worker.options
        self.worker = None
        worker.deleteLater()
        self.set_export_busy(False)

        if options["keep"] is None or result["count"] or result["errors"]:
            size = result["bytes"] / (1024 ** 2)
            verb = "Removed" if options["remove"] else "Can remove"
            message = f"{verb} {result['count']} preview generations ({size:.1f} MiB)."
            self.log.appendPlainText(message)
            if result["errors"]:
                self.log_toggle.setChecked(True)

            for error in result["errors"]:
                self.log.appendPlainText(f"Cache: {error}")
            self.statusBar().showMessage(message)

        if self.close_requested:
            self.close()
            return

        if not options["remove"] and result["count"]:
            answer = QMessageBox.question(
                self,
                "Clear preview cache",
                f"Remove {result['count']} unused preview generations and reclaim approximately {size:.1f} MiB?\n\n"
                "The displayed preview is retained. Removed previews will be generated again when needed.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.start_cache_cleanup(remove=True)

    def closeEvent(self, event):
        if self.audit_actions is not None and self.audit_actions.process is not None:
            self.audit_actions.request_stop()
        if self.worker is not None or self.export_process is not None:
            self.close_requested = True
            self.statusBar().showMessage("Closing after the current operation finishes...")
            event.ignore()
            return

        super().closeEvent(event)

def apply_modern_theme(app, window):
    app.setFont(QFont("Segoe UI", 10))

    palette = QPalette()
    colors = {
        QPalette.ColorRole.Window: "#181c22",
        QPalette.ColorRole.WindowText: "#e8edf4",
        QPalette.ColorRole.Base: "#202630",
        QPalette.ColorRole.AlternateBase: "#262e39",
        QPalette.ColorRole.Text: "#e8edf4",
        QPalette.ColorRole.Button: "#29323e",
        QPalette.ColorRole.ButtonText: "#e8edf4",
        QPalette.ColorRole.Highlight: "#42c8bd",
        QPalette.ColorRole.HighlightedText: "#102320",
        QPalette.ColorRole.ToolTipBase: "#29323e",
        QPalette.ColorRole.ToolTipText: "#e8edf4",
        QPalette.ColorRole.PlaceholderText: "#94a3b8",
        QPalette.ColorRole.Link: "#42c8bd",
    }
    for role, color in colors.items():
        palette.setColor(role, QColor(color))

    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#798596"))

    app.setPalette(palette)

    window.preview_button.setObjectName("primaryAction")
    window.export_button.setText("Export")
    window.search.setClearButtonEnabled(True)
    window.operators.setSpacing(2)
    window.log.setObjectName("activityLog")
    window.centralWidget().layout().setContentsMargins(16, 12, 16, 10)
    window.centralWidget().layout().setSpacing(10)

    app.setStyleSheet("""
        QWidget {
            font-family: "Segoe UI";
            font-size: 10pt;
        }
        QPlainTextEdit#activityLog {
            font-family: "Consolas";
            font-size: 9pt;
        }
        QPushButton {
            padding: 7px 12px;
            border: 1px solid #3b4656;
            border-radius: 6px;
            background: palette(button);
            color: palette(button-text);
        }
        QPushButton:hover {
            background: #354152;
        }
        QPushButton:pressed {
            background: #1c2530;
        }
        QPushButton:checked {
            background: #223e40;
            border-color: #42c8bd;
        }
        QPushButton:focus {
            border-color: #42c8bd;
        }

        QPushButton#primaryAction {
            background: #42c8bd;
            color: #102320;
            border-color: #42c8bd;
            font-weight: 600;
        }
        QPushButton#primaryAction:hover {
            background: #69d8cf;
        }
        QPushButton#primaryAction:pressed {
            background: #2ca79e;
        }
        QPushButton#primaryAction:focus {
            border-color: #e8edf4;
        }

        QPushButton:disabled, QPushButton#primaryAction:disabled {
            background: #242b35;
            color: #798596;
            border-color: #303947;
        }

        QLineEdit, QPlainTextEdit, QListWidget {
            background: palette(base);
            color: palette(text);
            border: 1px solid #354050;
            border-radius: 6px;
            padding: 6px;
            selection-background-color: palette(highlight);
            selection-color: palette(highlighted-text);
        }
        QLineEdit:focus, QPlainTextEdit:focus, QListWidget:focus {
            border-color: #42c8bd;
        }
        QListWidget::item {
            padding: 6px;
            border-radius: 4px;
        }
        QListWidget::item:hover {
            background: #2b3745;
        }
        QListWidget::item:selected {
            background: #42c8bd;
            color: #102320;
        }

        QTabWidget::pane {
            border: 1px solid #354050;
            border-radius: 6px;
        }
        QTabBar::tab {
            padding: 8px 12px;
            margin-right: 3px;
            background: #242c37;
            color: #aab7c8;
        }
        QTabBar::tab:selected {
            background: #334252;
            color: #e8edf4;
        }
        QTabBar::tab:focus {
            border: 1px solid #42c8bd;
        }

        QLabel#inspectorHeading {
            color: #e8edf4;
            font-weight: 600;
            padding-top: 4px;
        }
        QLabel#inspectorHint {
            color: #94e3b8;
        }
        QComboBox#inspectorLayers {
            background: #29323e;
            color: #e8edf4;
            border: 1px solid #3b4656;
            border-radius: 6px;
            padding: 7px 10px;
            min-height: 20px;
        }
        QComboBox#inspectorLayers:hover, QComboBox#inspectorLayers:focus {
            border-color: #42c8bd;
        }
        QComboBox#inspectorLayers:disabled {
            background: #242b35;
            color: #798596;
            border-color: #303947;
        }
        QComboBox#inspectorLayers QAbstractItemView {
            background: #202630;
            color: #e8edf4;
            border: 1px solid #3b4656;
            selection-background-color: #42c8bd;
            selection-color: #102320;
            padding: 4px;
        }
        QListWidget#textureCards::item {
            background: #262e39;
            border: 1px solid #354050;
            border-radius: 6px;
            padding: 8px;
        }
        QListWidget#textureCards::item:hover {
            background: #2b3745;
        }
        QListWidget#textureCards::item:selected {
            background: #223e40;
            border-color: #42c8bd;
            color: #e8edf4;
        }

        QCheckBox {
            spacing: 8px;
            padding: 3px;
        }
        QSplitter::handle {
            background: #181c22;
        }
        QSplitter::handle:hover {
            background: #42c8bd;
        }
        QStatusBar {
            border-top: 1px solid #303947;
        }
        QStatusBar::item  {
            border: none;
        }
    """)

def main():
    developer_mode = "--dev" in sys.argv[1:]
    sys.argv = [
        sys.argv[0],
        *(argument for argument in sys.argv[1:] if argument != "--dev")
    ]

    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TrueShadow01.R6ForgeExtractor")

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(str(resource_directory() / "docs" / "images" / "app.ico")))
    window = MainWindow(developer_mode=developer_mode)
    apply_modern_theme(app, window)
    window.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())
