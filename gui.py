"""Basic Desktop UI for browsing default operator registry entries"""

import sys
import codecs
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QProcess
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QPlainTextEdit, QPushButton,
    QHBoxLayout, QVBoxLayout, QSplitter, QTabWidget, QWidget
)

from src.operator_registry import read_operator_registry

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
        self.worker = None
        self.export_process = None
        self.registry_game_path = None
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

        self.browse = QPushButton("Browse...")
        self.browse.clicked.connect(self.choose_folder)
        self.load = QPushButton("Load operators")
        self.load.clicked.connect(self.load_registry)
        self.export_button = QPushButton("Export selected operator")
        self.export_button.clicked.connect(self.export_selected)
        self.install_button = QPushButton("Install Blender 4.5 add-on")
        self.install_button.clicked.connect(self.install_blender_addon)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Game Folder"))
        toolbar.addWidget(self.game_path, 1)
        toolbar.addWidget(self.browse)
        toolbar.addWidget(self.load)
        toolbar.addWidget(self.export_button)
        toolbar.addWidget(self.install_button)
        layout.addLayout(toolbar)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search operators...")
        self.search.textChanged.connect(self.filter_operators)
        self.operators = QListWidget()
        self.operators.currentItemChanged.connect(self.show_operator)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(self.search)
        left_layout.addWidget(self.operators)

        self.preview = QLabel("Select an operator\n\n3D preview will be added next.")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumWidth(300)
        self.preview.setStyleSheet("background: #24282d; color: #cbd1d8; border-radius: 6px;")

        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("Default appearance details")

        inspector = QTabWidget()
        inspector.addTab(self.details, "Details")
        inspector.addTab(QLabel("Materials and textures will appear after the model preparation."), "Materials / Textures")

        panels = QSplitter(Qt.Orientation.Horizontal)
        panels.addWidget(left)
        panels.addWidget(self.preview)
        panels.addWidget(inspector)
        panels.setSizes([230, 650, 340])
        layout.addWidget(panels, 1)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(140)
        self.log.document().setMaximumBlockCount(500)
        layout.addWidget(self.log)

        self.statusBar().showMessage("Choose the game folder and load operators.")

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
        self.statusBar().showMessage("Reading operator registry...")
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
        self.preview.setText(f"{operator.name}\n\n3D preview not yet implemented.")

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

        project = Path(__file__).resolve().parent
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

        folder = QFileDialog.getExistingDirectory(self, "Choose export destination", str(project / "output"))
        if not folder:
            return

        folder_name = "".join(
            "_" if character in '<>:"/\\|?*' or ord(character) < 32 else character
            for character in operator.name
        ).strip().rstrip(".")
        if not folder_name:
            self.report_error("Operator name cannot form an export folder.")
        self.export_directory = Path(folder) / folder_name
        self.export_jobs = [
            (label, uid, self.export_directory / label / f"{uid:016X}")
            for label, uid in parts
        ]
        self.export_total = len(self.export_jobs)
        self.export_arguments = [
            "-u", "-B", "-X", "utf8", str(project / "main.py"),
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
        for widget in (self.export_button, self.install_button, self.load, self.browse, self.game_path, self.operators, self.search):
            widget.setEnabled(not busy)

    def start_export_part(self):
        label, uid, destination = self.export_jobs.pop(0)
        number = self.export_total - len(self.export_jobs)
        self.export_expected_files = destination / f"{uid:016X}.gltf"
        self.export_decoder = codecs.getincrementaldecoder("utf-8")("replace")

        self.statusBar().showMessage(f"Exporting {number}/{self.export_total}: {label} {uid:016X}")
        self.export_process.start(sys.executable, self.export_arguments + ["--uid", f"{uid:016X}", "-o", str(destination)],)

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

        blender = Path(r"C:\Program Files\Blender Foundation\Blender 4.5\blender.exe")
        if not blender.is_file():
            filename, _ = QFileDialog.getOpenFileName(self, "Choose Blender 4.5", "", "Blender executable (blender.exe)")
            if not filename:
                return
            blender = Path(filename)

        script = Path(__file__).resolve().parent / "install_blender_addon.py"
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
            sys.executable,
            ["-u", "-B", "-X", "utf8", str(script), str(blender)]
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
