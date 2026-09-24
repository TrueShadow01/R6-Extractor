"""Review captured operator materials without changing the audit results"""

import hashlib
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QApplication, QComboBox, QHBoxLayout,
    QLabel, QListWidget, QMessageBox,
    QPushButton, QTextBrowser, QTextEdit,
    QVBoxLayout, QWidget, QLabel,
    QLineEdit
)

from app_runtime import application_directory
from devtools.audit.runner import save_json

STATUSES = ("Not reviewed", "Looks correct", "Issues found")

def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))

class ReviewWindow(QWidget):
    def __init__(self, folder):
        super().__init__()
        self.folder = folder
        self.review_path = folder / "reviews.json"

        report = read_json(folder / "audit.json")
        captures = read_json(folder / "thumbnails.json")
        if captures["run_id"] != report["run_id"]:
            raise RuntimeError("Thumbnails belong to another audit. Generate the audit gallery first")

        self.records = list(report["operators"].values())
        self.captures = captures["results"]
        self.reviews = (
            read_json(self.review_path)
            if self.review_path.exists()
            else {"version": 1, "operators": {}}
        )
        if self.reviews.get("version") != 1:
            raise RuntimeError("Unsupported reviews.json version")

        self.current = None
        self.dirty = False
        self.loading = False

        self.setWindowTitle("R6 material reviews")
        self.resize(1520, 900)

        self.operators = QListWidget()
        self.operators.setMaximumWidth(280)

        self.info = QLabel()
        self.info.setWordWrap(True)
        self.pictures = QTextBrowser()
        self.pictures.setOpenLinks(False)
        self.pictures.anchorClicked.connect(self.open_image)

        self.status = QComboBox()
        self.status.addItems(STATUSES)
        self.notes = QTextEdit()
        self.notes.setPlaceholderText("Describe missing textures, wrong colors or placement issues.")
        self.notes.setMaximumHeight(150)

        self.save_button = QPushButton("Save review")
        self.message = QLabel("Changes save when switching operators or closing this window.")

        right = QVBoxLayout()
        for widget in (
            self.info, self.pictures, self.status,
            self.notes, self.save_button, self.message
        ):
            right.addWidget(widget)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search operators...")

        self.filter_status = QComboBox()
        self.filter_status.addItems(["All", *STATUSES, "Needs recheck"])

        self.filter_summary = QLabel()
        self.filter_summary.setWordWrap(True)

        left = QVBoxLayout()
        left.addWidget(self.search)
        left.addWidget(self.filter_status)
        left.addWidget(self.filter_summary)
        left.addWidget(self.operators)

        layout = QHBoxLayout(self)
        layout.addLayout(left)
        layout.addLayout(right, 1)

        for record in self.records:
            self.operators.addItem(self.label(record))

        self.operators.currentRowChanged.connect(self.select)
        self.status.currentIndexChanged.connect(self.changed)
        self.notes.textChanged.connect(self.changed)
        self.save_button.clicked.connect(self.save_review)

        if self.records:
            self.operators.setCurrentRow(0)

        self.search.textChanged.connect(self.apply_filters)
        self.filter_status.currentTextChanged.connect(self.apply_filters)
        self.apply_filters()

    def fingerprint(self, record):
        identity = {
            "cache_key": record.get("cache_key"),
            "models": [
                (model["model"], model["fingerprint"])
                for model in record.get("models", [])
            ],
            "images": self.captures.get(record["uid"], {}).get("images", [])
        }
        return hashlib.sha256(json.dumps(identity, sort_keys=True).encode("utf-8")).hexdigest()

    def review_status(self, record):
        review = self.reviews["operators"].get(record["uid"])
        if not review:
            return "Not reviewed"
        if review.get("fingerprint") != self.fingerprint(record):
            return "Needs recheck"
        return review["status"]

    def label(self, record):
        return record["name"] + " - " + self.review_status(record)

    def apply_filters(self, *args):
        query = self.search.text().strip().casefold()
        status = self.filter_status.currentText()
        matches = 0
        current_visible = True

        self.operators.blockSignals(True)
        try:
            for row, record in enumerate(self.records):
                visible = (
                    query in record["name"].casefold()
                    and (
                        status == "All"
                        or self.review_status(record) == status
                    )
                )
                self.operators.item(row).setHidden(not visible)
                matches += int(visible)
                if row == self.current:
                    current_visible = visible
        finally:
            self.operators.blockSignals(False)

        text = f"{matches} of {len(self.records)} operators match."
        if self.current is not None and not current_visible:
            text += " Current review remains open outside the filter."
        self.filter_summary.setText(text)

    def changed(self, *args):
        if not self.loading and self.current is not None:
            self.dirty = True
            self.message.setText("Unsaved changes")

    def select(self, row):
        if row < 0:
            return
        if self.dirty and not self.save_review():
            self.operators.blockSignals(True)
            self.operators.setCurrentRow(self.current)
            self.operators.blockSignals(False)
            return

        self.current = row
        record = self.records[row]
        capture = self.captures.get(record["uid"], {})
        review = self.reviews["operators"].get(record["uid"], {})
        stale = bool(review) and review.get("fingerprint") != self.fingerprint(record)

        self.loading = True
        self.status.setCurrentText("Not reviewed" if stale else review.get("status", "Not reviewed"))
        self.notes.setPlainText(review.get("notes", ""))
        self.loading = False
        self.dirty = False

        self.info.setText(
            record["name"]
            + " | Resource checks: "
            + record.get("resource_checks", "not completed")
            + "\nVisual review only. This does not verify rig behavior."
            + (
                "\nCapture changed. Previous notes are preserved. Select a status and save after checking again."
                if stale else ""
            )
        )

        images = []
        for view, filename in zip(("Front", "Back", "Head"), capture.get("images", [])):
            url = html.escape(QUrl.fromLocalFile(filename).toString(), quote=True)
            images.append(
                f'<td><p>{view}</p><a href="{url}">'
                f'<img src="{url}" width="260" height="325"></a></td>'
            )

        error = capture.get("error", "")
        self.pictures.setHtml(
            "<table><tr>" + "".join(images) + "</tr></table>"
            + "<p>" + html.escape(error or (
                "" if images else "No captures available. Review the full preview."
            )) + "</p>"
        )
        self.message.setText("Click a thumbnail to open it. Save after reviewing.")
        self.apply_filters()

    def open_image(self, url):
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(url)

    def save_review(self):
        if self.current is None:
            return True

        record = self.records[self.current]
        try:
            # Reload to preserve reviews saved for other operators
            data = (
                read_json(self.review_path)
                if self.review_path.exists()
                else {"version": 1, "operators": {}}
            )
            if data.get("version") != 1:
                raise RuntimeError("Unsupported reviews.json version")

            data["operators"][record["uid"]] = {
                "name": record["name"],
                "status": self.status.currentText(),
                "notes": self.notes.toPlainText(),
                "fingerprint": self.fingerprint(record),
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            save_json(self.review_path, data)
        except Exception as error:
            QMessageBox.critical(self, "Review not saved", str(error))
            return False

        self.reviews = data
        self.dirty = False
        self.operators.item(self.current).setText(self.label(record))
        self.message.setText("Saved to " + str(self.review_path))
        self.apply_filters()
        return True

    def closeEvent(self, event):
        if self.dirty and not self.save_review():
            event.ignore()
        else:
            event.accept()

def main():
    app = QApplication(sys.argv)
    folder = application_directory() / "output" / "material-audit"
    window = ReviewWindow(folder)
    window.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())