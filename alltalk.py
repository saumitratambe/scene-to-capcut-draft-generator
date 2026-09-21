import sys
import time
import re
import json
import requests
from pathlib import Path
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QFileDialog,
    QProgressBar, QLineEdit, QMessageBox
)
from PySide6.QtCore import QThread, Signal, QTimer

# ================= CONFIG =================
ALLTALK_URL = "http://127.0.0.1:7851/api/tts-generate"
MAX_CHUNK_CHARS = 2400
WATCH_INTERVAL_MS = 60000

# Fixed XTTS v2 Settings matching your AllTalk UI
BASE_PAYLOAD = {
    "character_voice_gen": "male_01",  # Will be overridden by GUI
    "narrator_enabled": False,
    "narrator_voice_gen": "male_01",
    "text_not_inside": "character",
    "language": "en",
    "output_type": "wav",  # Faster than MP3, compose.py handles conversion
    "continue_generation": False,
    "output_file_timestamp": False,
    "autoplay": False,
    "autoplay_volume": 0.8,
    "speed": 1.0,
    "pitch": 1,
    "temperature": 0.5,
    "repetition_penalty": 5,
    "text_filtering": "standard"
}

# ================= WORKER =================
class AllTalkWorker(QThread):
    log = Signal(str)
    finished = Signal()

    def __init__(self, output_root, voice_name):
        super().__init__()
        self.output_root = Path(output_root)
        self.voice_name = voice_name.replace(".wav", "").strip()
        self.running = True
        self.processed_files = set()

    def stop(self):
        self.running = False

    def check_server(self):
        try:
            response = requests.get("http://127.0.0.1:7851/", timeout=5)
            return response.status_code == 200
        except:
            return False

    def tts_file(self, script_path: Path):
        audio_dir = self.output_root / "audio"
        audio_dir.mkdir(exist_ok=True)

        name = script_path.stem
        match = re.match(r"(Part)(\d+)", name, re.IGNORECASE)
        if match:
            formatted_name = f"{match.group(1)}{int(match.group(2)):02d}"
        else:
            formatted_name = name

        audio_path = audio_dir / f"{formatted_name}.wav"

        if script_path.stem in self.processed_files or audio_path.exists():
            if audio_path.exists():
                self.processed_files.add(script_path.stem)
            return True

        try:
            text = script_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            self.log.emit(f"❌ Could not read {script_path.name}: {e}")
            return False

        if not text:
            self.log.emit(f"⚠️ Skipping {script_path.name}: Empty file")
            return False

        chunks = [text[i:i+MAX_CHUNK_CHARS] for i in range(0, len(text), MAX_CHUNK_CHARS)]
        full_audio = bytearray()

        self.log.emit(f"📝 Processing: {script_path.name} ({len(chunks)} chunks)...")

        for i, chunk in enumerate(chunks):
            if not self.running:
                break
            
            current_text = str(chunk).strip()
            if not current_text:
                continue

            tries = 0
            success = False
            
            # List of possible field names the API might expect
            possible_keys = ["text_input", "text", "prompt", "input_text"]
            
            while tries < 3 and self.running:
                try:
                    # Build Base Payload
                    payload = BASE_PAYLOAD.copy()
                    payload["character_voice_gen"] = self.voice_name
                    payload["narrator_voice_gen"] = self.voice_name
                    payload["output_file_name"] = f"temp_chunk_{i}"

                    # 🔥 ATTEMPT STRATEGY: Try different key names until one works
                    last_error = ""
                    for key in possible_keys:
                        # Create a fresh copy for each attempt
                        test_payload = payload.copy()
                        
                        # Set the text using the current candidate key
                        test_payload[key] = current_text
                        
                        # Remove other potential text keys to avoid conflict
                        for k in possible_keys:
                            if k != key and k in test_payload:
                                del test_payload[k]

                        # Convert to JSON string manually to ensure strict formatting
                        json_data = json.dumps(test_payload)
                        
                        headers = {
                            "Content-Type": "application/json",
                            "Accept": "application/json"
                        }

                        # Send as RAW data, not dict
                        response = requests.post(
                            ALLTALK_URL, 
                            data=json_data, 
                            headers=headers, 
                            timeout=120
                        )
                        
                        if response.status_code == 200:
                            if i == 0 and tries == 0:
                                self.log.emit(f"✅ Success using key: '{key}'")
                            full_audio.extend(response.content)
                            success = True
                            break # Break inner loop (key search)
                        else:
                            # Parse error to see if we should try next key
                            err_msg = ""
                            try:
                                err_json = response.json()
                                if "detail" in err_json:
                                    err_msg = str(err_json["detail"])
                            except:
                                err_msg = response.text
                            
                            last_error = err_msg
                            
                            # If error says "field required" or "none", try next key
                            if "required" in err_msg.lower() or "none" in err_msg.lower():
                                continue # Try next key in list
                            else:
                                # If it's a different error (e.g., voice not found), stop trying keys
                                raise RuntimeError(f"API Error {response.status_code}: {err_msg}")

                    if not success:
                        # If we tried all keys and none worked
                        raise RuntimeError(f"All field names failed. Last error: {last_error}")

                    break # Break retry loop if successful

                except Exception as e:
                    self.log.emit(f"⚠️ Chunk {i+1} failed (try {tries+1}): {str(e)}")
                    tries += 1
                    time.sleep(2)

            if not success:
                self.log.emit(f"❌ Chunk {i+1} failed permanently.")
                return False

        if full_audio:
            audio_path.write_bytes(full_audio)
            self.log.emit(f"✅ Saved: {audio_path.name} (WAV)")
            self.processed_files.add(script_path.stem)
            return True
        
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
        self.log.emit("🔗 Connecting to AllTalk Server (XTTS v2)...")
        
        if not self.check_server():
            self.log.emit("❌ AllTalk server not found! Please start AllTalk first.")
            self.finished.emit()
            return

        self.log.emit(f"🎙️ Using Voice: {self.voice_name}")
        self.log.emit("⚙️ Mode: Multi-Key Fallback (Bypasses Beta Schema Issues)")
        self.log.emit("👀 Scanning for new scripts...")
        
        self.scan_and_process()
        self.finished.emit()

# ================= GUI =================
class App(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AllTalk XTTS v2 – Robust Automation")
        self.resize(720, 650)
        layout = QVBoxLayout(self)

        layout.addWidget(QLabel("Voice Name (as seen in AllTalk UI):"))
        self.voice_input = QLineEdit()
        self.voice_input.setPlaceholderText("e.g., male_01, daniel, fin")
        self.voice_input.setText("male_01") 
        layout.addWidget(self.voice_input)
        
        layout.addWidget(QLabel("💡 Ensure AllTalk is running with XTTSv2 + DeepSpeed"))
        layout.addWidget(QLabel("🚀 Output format: .wav (Fastest, compatible with Compose)"))

        btns = QHBoxLayout()
        self.pick_btn = QPushButton("Select Root Folder")
        self.start_btn = QPushButton("Start Generation")
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        btns.addWidget(self.pick_btn)
        btns.addWidget(self.start_btn)
        btns.addWidget(self.stop_btn)
        layout.addLayout(btns)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)

        self.log_output = QTextEdit(readOnly=True)
        self.log_output.setFontPointSize(10)
        layout.addWidget(self.log_output)

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
            (Path(folder) / "audio").mkdir(exist_ok=True)
            self.log_output.append(f"📁 Working Directory: {folder}")
            self.log_output.append("💡 Place .txt scripts in 'perfect' subfolder")

    def start(self):
        if not self.output_root:
            QMessageBox.warning(self, "Error", "Select folder first.")
            return
        
        voice = self.voice_input.text().strip()
        if not voice:
            QMessageBox.warning(self, "Error", "Enter a voice name.")
            return

        self.worker = AllTalkWorker(self.output_root, voice)
        self.worker.log.connect(self.log_output.append)
        self.worker.finished.connect(self.start_watch)
        self.worker.start()

        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.pick_btn.setEnabled(False)

    def start_watch(self):
        self.log_output.append("👀 Watching folder (60s intervals)...")
        self.timer = QTimer()
        self.timer.timeout.connect(self.check)
        self.timer.start(WATCH_INTERVAL_MS)
        
        self.pick_btn.setEnabled(True)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

    def check(self):
        if self.worker:
            count = self.worker.scan_and_process()
            if count:
                self.log_output.append(f"✅ Processed {count} new file(s)")

    def stop(self):
        if self.worker:
            self.worker.stop()
        if self.timer:
            self.timer.stop()
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.log_output.append("🛑 Stopped.")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec())