import sys
import time
import requests
from pathlib import Path
from collections import deque

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QLabel,
    QPushButton, QTextEdit, QFileDialog,
    QProgressBar, QMessageBox
)
from PySide6.QtCore import QThread, Signal


# ================= CONFIG =================
API_URL = "https://api.sarvam.ai/translate"
MODEL = "mayura:v1"
MAX_CHARS = 1000
MAX_RETRIES = 3
RETRY_DELAY = 5


# ================= WORKER =================
class TranslatorWorker(QThread):
    log = Signal(str)
    progress = Signal(int)
    finished = Signal()

    def __init__(self, keys, root):
        super().__init__()
        self.keys = deque(keys)
        self.root = Path(root)
        self.failed = []

    # ---------- KEY ROTATION ----------
    def rotate_key(self):
        if len(self.keys) > 1:
            self.keys.rotate(-1)
            self.log.emit("🔄 Switched API key")

    # ---------- SMART CHUNK ----------
    def chunk_text(self, text):
        """
        Try to split at sentence boundaries first.
        Falls back to hard split if needed.
        """
        if len(text) <= MAX_CHARS:
            return [text]

        chunks = []
        start = 0

        while start < len(text):
            end = min(start + MAX_CHARS, len(text))

            # Try to cut at sentence end
            cut = text.rfind(".", start, end)
            if cut == -1 or cut <= start:
                cut = end

            chunks.append(text[start:cut].strip())
            start = cut

        return chunks

    # ---------- API CALL ----------
    def translate_chunk(self, text):
        payload = {
            "input": text,
            "source_language_code": "auto",
            "target_language_code": "hi-IN",
            "model": MODEL,
            "mode": "modern-colloquial",
            "speaker_gender": "Male",
            "numerals_format": "international"
        }

        headers = {
            "api-subscription-key": self.keys[0],
            "Content-Type": "application/json"
        }

        attempts = 0

        while attempts < MAX_RETRIES:
            try:
                r = requests.post(API_URL, json=payload, headers=headers, timeout=30)

                if r.status_code == 200:
                    return r.json()["translated_text"]

                if r.status_code == 429:
                    self.log.emit("⏳ Rate limited — waiting 5 sec")
                    time.sleep(RETRY_DELAY)
                    attempts += 1
                    continue

                if r.status_code == 403:
                    self.log.emit("⚠️ Invalid key — rotating")
                    self.rotate_key()
                    attempts += 1
                    continue

                self.log.emit(f"❌ HTTP {r.status_code}")
                attempts += 1
                time.sleep(RETRY_DELAY)

            except Exception as e:
                self.log.emit(f"⚠️ Network error: {e}")
                attempts += 1
                time.sleep(RETRY_DELAY)

        self.rotate_key()
        return None

    # ---------- PROCESS FILE ----------
    def process_file(self, file):
        out_dir = self.root / "perfect"
        out_dir.mkdir(exist_ok=True)

        out_path = out_dir / file.name

        if out_path.exists():
            self.log.emit(f"⏭ Skipped: {file.name}")
            return True

        text = file.read_text(encoding="utf-8").strip()
        if not text:
            self.failed.append(file.name)
            return False

        chunks = self.chunk_text(text)
        translated_parts = []

        for chunk in chunks:
            result = self.translate_chunk(chunk)
            if not result:
                self.failed.append(file.name)
                return False
            translated_parts.append(result.strip())
            time.sleep(0.5)

        # 🔥 CLEAN JOIN (No broken words)
        final_text = "\n".join(translated_parts)

        out_path.write_text(final_text, encoding="utf-8")

        self.log.emit(f"✅ Done: {file.name}")
        return True

    # ---------- RUN ----------
    def run(self):
        script_dir = self.root / "script"

        if not script_dir.exists():
            self.log.emit("❌ script folder missing")
            self.finished.emit()
            return

        files = sorted(script_dir.glob("*.txt"))
        total = len(files)

        if total == 0:
            self.log.emit("⚠️ No files found")
            self.finished.emit()
            return

        for i, f in enumerate(files, 1):
            self.process_file(f)
            self.progress.emit(int(i / total * 100))

        self.log.emit(f"🎉 Completed {total - len(self.failed)}/{total}")

        if self.failed:
            self.log.emit("❌ Failed:")
            for f in self.failed:
                self.log.emit(f" - {f}")

        self.finished.emit()


# ================= GUI =================
class App(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sarvam Mayura Translator (Modern Colloquial)")
        self.resize(800, 600)

        layout = QVBoxLayout(self)

        self.api = QTextEdit()
        self.api.setPlaceholderText("Enter API keys (comma separated)")
        layout.addWidget(self.api)

        self.pick = QPushButton("Select Root Folder")
        self.pick.clicked.connect(self.pick_folder)
        layout.addWidget(self.pick)

        self.label = QLabel("No folder selected")
        layout.addWidget(self.label)

        self.start_btn = QPushButton("Start")
        self.start_btn.clicked.connect(self.start)
        layout.addWidget(self.start_btn)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)

        self.log = QTextEdit(readOnly=True)
        layout.addWidget(self.log)

        self.root = None
        self.worker = None

    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self)
        if folder:
            self.root = folder
            self.label.setText(folder)

    def start(self):
        keys = [k.strip() for k in self.api.toPlainText().split(",") if k.strip()]

        if not keys:
            QMessageBox.critical(self, "Error", "API key missing")
            return

        if not self.root:
            QMessageBox.critical(self, "Error", "Select folder")
            return

        self.worker = TranslatorWorker(keys, self.root)
        self.worker.log.connect(self.log.append)
        self.worker.progress.connect(self.progress.setValue)
        self.worker.finished.connect(lambda: self.start_btn.setEnabled(True))

        self.start_btn.setEnabled(False)
        self.progress.setValue(0)
        self.worker.start()


# ================= MAIN =================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec())
