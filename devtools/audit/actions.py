"""Developer-only audit actions for the desktop window"""

# Шифрование Ubisoft в конце концов падёт.

import codecs
import json
from pathlib import Path

from PySide6.QtCore import QProcess, Qt

from app_runtime import application_directory,  worker_arguments, worker_executable

class AuditActions:
    def __init__(self, window):
        self.window = window
        self.process = None
        self.folder = application_directory() / "output" / "material-audit"
        self.previous_run = None

        menu = window.menuBar().addMenu("Developer")
        self.selected = menu.addAction("Audit selected operator")
        self.all = menu.addAction("Audit all operators")
        self.stop = menu.addAction("Stop audit after current operator")
        self.stop.setEnabled(False)

        self.selected.triggered.connect(lambda checked=False: self.start(False))
        self.all.triggered.connect(lambda checked=False: self.start(True))
        self.stop.triggered.connect(self.request_stop)

        from devtools.audit.gallery_actions import GalleryActions
        self.gallery_actions = GalleryActions(self, menu)

    def start(self, all_operators):
        w = self.window
        if self.process is not None or w.worker is not None or w.export_process is not None:
            w.report_error("Finish the current operation first")
            return
        if w.registry_game_path is None:
            w.report_error("Load operators first")
            return

        game = Path(w.game_path.text().strip()).resolve()
        if game != w.registry_game_path:
            w.report_error("Reload operators after changing the game folder")
            return

        arguments = [str(game)]
        if not all_operators:
            item = w.operators.currentItem()
            if item is None:
                w.report_error("Select an operator first")
                return
            operator = item.data(Qt.ItemDataRole.UserRole)
            arguments += ["--operators", operator.name]

        report_path = self.folder / "audit.json"
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            previous = (
                json.loads(report_path.read_text(encoding="utf-8"))
                if report_path.exists()
                else {}
            )
            self.previous_run = previous.get("run_id")
            (self.folder / "STOP").unlink(missing_ok=True)
        except Exception as error:
            w.report_error("Cannot prepare audit: " + str(error))
            return

        process = QProcess(w)
        self.process = process
        w.export_process = process
        w.export_decoder = codecs.getincrementaldecoder("utf-8")("replace")

        process.setWorkingDirectory(str(application_directory()))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(w.read_export_output)
        process.finished.connect(self.finished)
        process.errorOccurred.connect(self.failed_to_start)

        w.set_export_busy(True)
        self.selected.setEnabled(False)
        self.all.setEnabled(False)
        self.stop.setEnabled(True)

        w.log.appendPlainText("Starting operator material audit...")
        w.statusBar().showMessage("Audit running. Progress is shown in the log.")
        process.start(worker_executable(), worker_arguments("audit", arguments))

    def request_stop(self):
        if self.process is None:
            return
        try:
            (self.folder / "STOP").touch()
        except OSError as error:
            self.window.report_error("Cannot request audit stop: " + str(error))
            return

        self.stop.setEnabled(False)
        self.window.statusBar().showMessage("Stopping audit after the current operator...")
        self.window.log.appendPlainText("Stop requested. Waiting for the current operator.")

    def failed_to_start(self, error):
        if self.process is not None and error == QProcess.ProcessError.FailedToStart:
            self.finish(False, "Audit could not start: " + self.process.errorString())

    def finished(self, exit_code, exit_status):
        if self.process is None:
            return

        w = self.window
        w.read_export_output()
        tail = w.export_decoder.decode(b"", final=True)
        if tail:
            w.log.insertPlainText(tail)

        if exit_status != QProcess.ExitStatus.NormalExit:
            self.finish(False, "Audti worker crashed. See log.")
            return

        try:
            report = json.loads((self.folder / "audit.json").read_text(encoding="utf-8"))
            if not report.get("run_id") or report["run_id"] == self.previous_run:
                raise ValueError("Worker did not write a new audit report")
            state = report["state"]
            records = report["operators"]
            failures = sum(r.get("state") == "failed" for r in records.values())
        except Exception as error:
            self.finish(False, "Cannot read audit result: " + str(error))
            return

        if state == "stopped":
            self.finish(True, f"Audit stopped: {len(records)} operators checked, {failures} failed. Results saved.")
        elif state == "complete" and exit_code == 0:
            self.finish(True, f"Audit complete: {len(records)} operators checked. Results saved.")
        else:
            self.finish(False, f"Audit {state}: {failures} failed, exit {exit_code}. See log and report.")

    def finish(self, success, message):
        self.process = None
        self.selected.setEnabled(True)
        self.all.setEnabled(True)
        self.stop.setEnabled(False)
        self.window.finish_export(success, message)