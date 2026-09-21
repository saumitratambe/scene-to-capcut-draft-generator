import sys
import time
import base64
from pathlib import Path
from collections import deque

import requests

from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QFileDialog,
    QProgressBar, QLineEdit, QComboBox
)
from PySide6.QtCore import QThread, Signal, QTimer


# ================= CONFIG =================
SARVAM_URL = "https://api.sarvam.ai/text-to-speech"

MAX_TRIES = 3
RETRY_DELAY = 2
KEY_SWITCH_ERRORS = ("403", "429")

WATCH_INTERVAL_MS = 60000  # 🔥 faster check (1 min)


# ================= WORKER =================
class TTSWorker(QThread):
    log = Signal(str)
    progress = Signal(int)
    finished = Signal()

    def __init__(self, keys, output_root, speaker, language):
        super().__init__()
        self.keys = deque(keys)
        self.output_root = Path(output_root)
        self.speaker = speaker
        self.language = language

        self.running = True
        self.processed_files = set()
        self.failed_files = {}

    def stop(self):
        self.running = False

    def rotate_key(self):
        if len(self.keys) > 1:
            self.keys.rotate(-1)
            self.log.emit("🔁 API key rotated")

    def tts_file(self, script_path: Path):
        audio_dir = self.output_root / "audio"
        audio_dir.mkdir(exist_ok=True)

        import re

        name = script_path.stem

        # Match "Part<number>"
        match = re.match(r"(Part)(\d+)", name, re.IGNORECASE)

        if match:
            prefix = match.group(1)
            number = int(match.group(2))
            formatted_name = f"{prefix}{number:02d}"
        else:
            formatted_name = name

        audio_path = audio_dir / f"{formatted_name}.mp3"

        if script_path.stem in self.processed_files:
            return True

        if audio_path.exists():
            self.processed_files.add(script_path.stem)
            return True

        text = script_path.read_text(encoding="utf-8").strip()
        if not text:
            self.failed_files[script_path.name] = "empty"
            return False

        # 🔥 chunk if too long (fix 2500 limit)
        chunks = [text[i:i+2400] for i in range(0, len(text), 2400)]

        full_audio = b""

        for chunk in chunks:
            tries = 0

            while tries < MAX_TRIES and self.running:
                try:
                    headers = {
                        "api-subscription-key": self.keys[0],
                        "Content-Type": "application/json"
                    }

                    payload = {
                        "text": chunk,
                        "target_language_code": self.language,
                        "model": "bulbul:v3",
                        "speaker": self.speaker,
                        "pace": 1.0,
                        "output_audio_codec": "mp3"
                    }

                    r = requests.post(
                        SARVAM_URL,
                        json=payload,
                        headers=headers,
                        timeout=30
                    )

                    if r.status_code != 200:
                        raise RuntimeError(f"{r.status_code}: {r.text}")

                    data = r.json()
                    audio_base64 = data["audios"][0]
                    audio_bytes = base64.b64decode(audio_base64)

                    full_audio += audio_bytes
                    break

                except Exception as e:
                    err = str(e)
                    self.log.emit(f"❌ {script_path.name} → {err}")

                    if any(code in err for code in KEY_SWITCH_ERRORS):
                        self.rotate_key()
                        time.sleep(RETRY_DELAY)
                        continue

                    tries += 1
                    time.sleep(RETRY_DELAY)

        if full_audio:
            audio_path.write_bytes(full_audio)
            self.log.emit(f"✅ {script_path.name}")
            self.processed_files.add(script_path.stem)
            return True

        self.failed_files[script_path.name] = "failed"
        return False

    def scan_and_process(self):
        perfect_dir = self.output_root / "perfect"
        if not perfect_dir.exists():
            return 0

        scripts = sorted(perfect_dir.glob("*.txt"))
        new_scripts = [s for s in scripts if s.stem not in self.processed_files]

        if not new_scripts:
            return 0

        count = 0
        for script in new_scripts:
            if not self.running:
                break
            if self.tts_file(script):
                count += 1

        return count

    def run(self):
        self.log.emit("🚀 Starting...")

        self.scan_and_process()

        self.finished.emit()


# ================= GUI =================
class App(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Sarvam TTS – Fast Auto Generator")
        self.resize(720, 600)

        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("API Keys:"))
        self.keys_input = QTextEdit()
        layout.addWidget(self.keys_input)

        layout.addWidget(QLabel("Language:"))
        self.lang = QComboBox()
        self.lang.addItems(["hi-IN", "en-IN"])
        layout.addWidget(self.lang)

        layout.addWidget(QLabel("Voice (Speaker):"))
        self.voice = QComboBox()
        self.voice.addItems([
            "shubh", "priya", "neha", "amit", "rahul", "rohan"
        ])
        layout.addWidget(self.voice)

        btns = QHBoxLayout()
        self.pick_btn = QPushButton("Select Folder")
        self.start_btn = QPushButton("Start")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)

        btns.addWidget(self.pick_btn)
        btns.addWidget(self.start_btn)
        btns.addWidget(self.stop_btn)

        layout.addLayout(btns)

        self.progress = QProgressBar()
        layout.addWidget(self.progress)

        self.log = QTextEdit(readOnly=True)
        layout.addWidget(self.log)

        self.output_root = None
        self.worker = None
        self.timer = None

        self.pick_btn.clicked.connect(self.pick_folder)
        self.start_btn.clicked.connect(self.start)
        self.stop_btn.clicked.connect(self.stop)

    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self)
        if folder:
            self.output_root = folder
            (Path(folder) / "perfect").mkdir(exist_ok=True)
            self.log.append(f"📁 {folder}")

    def start(self):
        keys = [k.strip() for k in self.keys_input.toPlainText().split() if k.strip()]

        if not keys or not self.output_root:
            self.log.append("❌ Missing data")
            return

        self.worker = TTSWorker(
            keys,
            self.output_root,
            self.voice.currentText(),
            self.lang.currentText()
        )

        self.worker.log.connect(self.log.append)
        self.worker.finished.connect(self.start_watch)

        self.worker.start()

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def start_watch(self):
        self.log.append("👀 Watching folder...")

        self.timer = QTimer()
        self.timer.timeout.connect(self.check)
        self.timer.start(WATCH_INTERVAL_MS)

    def check(self):
        if self.worker:
            count = self.worker.scan_and_process()
            if count:
                self.log.append(f"✅ {count} new files processed")

    def stop(self):
        if self.worker:
            self.worker.stop()
        if self.timer:
            self.timer.stop()

        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.log.append("🛑 Stopped")


# ================= MAIN =================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec())
