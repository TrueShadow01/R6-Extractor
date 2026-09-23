"""Gallery capture and manual review actions for developer mode"""

import codecs

from PySide6.QtCore import QProcess, Qt, QUrl
from PySide6.QtGui import QDesktopServices

from app_runtime import application_directory, worker_arguments, worker_executable

class GalleryActions:
    def __init__(self, audit, menu):
        self.audit = audit
        self.window = audit.window
        self.folder = audit.folder
        self.process = None
        self.review = None

        menu.addSeparator()
        self.generate = menu.addAction("Generate audit gallery")
        self.open = menu.addAction("Open audit gallery")
        self.review_action = menu.addAction("Review materials")

        self.generate.triggered.connect(self.start)
        self.open.triggered.connect(self.open_gallery)
        self.review_action.triggered.connect(self.open_review)

    def idle(self):
        w = self.window
        if w.worker is not None or w.export_process is not None:
            w.report_error("Finish the current operation first")
            return False
        return True

    def start(self):
        if not self.idle():
            return

        report = self.folder / "audit.json"
        if not report.is_file():
            self.window.report_error("Run an operator audit first")
            return

        w = self.window
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
        self.audit.selected.setEnabled(False)
        self.audit.all.setEnabled(False)
        self.generate.setEnabled(False)
        self.review_action.setEnabled(False)

        w.log.appendPlainText("Generating gallery. Close the capture window to stop.")
        w.statusBar().showMessage("Capturing audit thumbnails...")
        process.start(worker_executable(), worker_arguments("audit-gallery", [str(report)]))

    def failed_to_start(self, error):
        if self.process is not None and error == QProcess.ProcessError.FailedToStart:
            self.finish(False, "Capture could not start: " + self.process.errorString())

    def finished(self, code, status):
        if self.process is None:
            return

        w = self.window
        w.read_export_output()
        tail = w.export_decoder.decode(b"", final=True)
        if tail:
            w.log.insertPlainText(tail)

        if status != QProcess.ExitStatus.NormalExit:
            self.finish(False, "Capture worker crashed. See log.")
        elif code == 0:
            self.finish(True, "Gallery ready. Use Developer > Open audit gallery")
        elif code == 2:
            self.finish(True, "Capture stopped. Completed thumbnails are retained")
        else:
            self.finish(False, "Gallery capture failed or contains errors. See log and gallery")

    def finish(self, success, message):
        self.process = None
        self.audit.selected.setEnabled(True)
        self.audit.all.setEnabled(True)
        self.generate.setEnabled(True)
        self.review_action.setEnabled(True)
        self.window.finish_export(success, message)

    def open_gallery(self):
        if not self.idle():
            return

        path = self.folder / "gallery.html"
        if not path.is_file():
            self.window.report_error("Generate the audit gallery first")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.window.report_error("Could not open " + str(path))

    def open_review(self):
        if not self.idle():
            return

        if self.review is not None:
            self.review.raise_()
            self.review.activateWindow()
            return

        try:
            from devtools.audit.review import ReviewWindow
            review = ReviewWindow(self.folder)
        except Exception as error:
            self.window.report_error("Cannot open reviews: " + str(error))
            return

        review.setParent(self.window, Qt.WindowType.Window)
        review.setWindowModality(Qt.WindowModality.WindowModal)
        review.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        review.destroyed.connect(self.review_closed)
        self.review = review
        review.show()

    def review_closed(self, *args):
        self.review = None