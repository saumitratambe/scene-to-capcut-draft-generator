import sys
import csv
import json
import subprocess
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

import cv2
import numpy as np

from PySide6.QtWidgets import (
    QApplication, QWidget, QPushButton, QFileDialog,
    QVBoxLayout, QPlainTextEdit, QLabel
)
from PySide6.QtCore import QObject, Signal

# =========================
# CONFIG
# =========================
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"
SCENEDETECT = "scenedetect"

VIDEO_EXTS = (".mp4", ".mkv", ".mov")

MAX_WORKERS = 6
EDGE_THRESHOLD_RATIO = 0.06
SAFE_PADDING = 2
BIN_THRESHOLD = 5

# =========================
# LOGGER
# =========================
class Logger(QObject):
    log = Signal(str)

# =========================
# UTILS
# =========================
def run(cmd):
    r = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    if r.returncode != 0:
        raise RuntimeError(r.stdout)
    return r.stdout


def ensure(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def time_to_seconds(t: str) -> float:
    t = t.strip().replace('"', '').replace("'", "")
    parts = t.split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(t)


def get_duration(video: Path) -> float:
    r = subprocess.run(
        [
            FFPROBE, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(video)
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    return float(r.stdout.strip())

# =========================
# SMART CROPPING (OPTIMIZED)
# =========================
cv2.setUseOptimized(True)
cv2.setNumThreads(4)


def tight_trim(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(gray, BIN_THRESHOLD, 255, cv2.THRESH_BINARY)
    coords = cv2.findNonZero(binary)
    if coords is None:
        return img
    x, y, w, h = cv2.boundingRect(coords)
    return img[y:y + h, x:x + w]


def smart_crop_all_sides(img_path: Path, out_path: Path):
    img = cv2.imread(str(img_path))
    if img is None:
        return False

    h, w = img.shape[:2]
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 80, 200)

    v_sum = edges.sum(axis=0)
    h_sum = edges.sum(axis=1)

    v_thresh = v_sum.max() * EDGE_THRESHOLD_RATIO
    h_thresh = h_sum.max() * EDGE_THRESHOLD_RATIO

    xs = np.where(v_sum > v_thresh)[0]
    ys = np.where(h_sum > h_thresh)[0]

    if len(xs) == 0 or len(ys) == 0:
        cw, ch = int(w * 0.9), int(h * 0.9)
        x0 = (w - cw) // 2
        y0 = (h - ch) // 2
        crop = img[y0:y0 + ch, x0:x0 + cw]
    else:
        x1 = max(xs[0] - SAFE_PADDING, 0)
        x2 = min(xs[-1] + SAFE_PADDING, w)
        y1 = max(ys[0] - SAFE_PADDING, 0)
        y2 = min(ys[-1] + SAFE_PADDING, h)
        crop = img[y1:y2, x1:x2]

    crop = tight_trim(crop)

    if crop is None or crop.size == 0:
        return False

    cv2.imwrite(str(out_path), crop)
    return True

# =========================
# PROCESS PART (PARALLEL SAFE)
# =========================
def process_part(idx, start, end, root, video, logger):
    part = f"Part{idx:02d}"
    out = root / "output" / part
    json_path = out / "scenes.json"

    if json_path.exists():
        logger.log.emit(f"⏭️ {part} skipped")
        return

    logger.log.emit(f"⚙️ {part} started")

    work = root / "work" / part
    ensure(work)
    ensure(out / "crops")

    raw = work / "raw.mp4"


    run([
        FFMPEG, "-y",
        "-ss", str(start), "-to", str(end),
        "-i", str(video), "-an",
        "-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq",
        "-rc", "vbr", "-b:v", "5M",
        str(raw)
    ])

    scenes_dir = work / "scenes"
    ensure(scenes_dir)

    run([
        SCENEDETECT,
        "-i", str(raw),
        "detect-content",
        "split-video",
        "-o", str(scenes_dir)
    ])

    scenes = sorted(scenes_dir.glob("*.mp4"))

    json_out = {
        "part": part,
        "source_video": video.name,
        "scene_count": len(scenes),
        "scenes": []
    }

    total_duration = 0.0

    for sidx, scene in enumerate(scenes, 1):
        dur = get_duration(scene)
        mid = dur / 2

        frame = work / f"scene{sidx}.jpg"

        run([
            FFMPEG, "-y",
            "-ss", str(mid),
            "-i", str(scene),
            "-frames:v", "1",
            "-q:v", "2",
            str(frame)
        ])

        crop_path = out / "crops" / f"crop{sidx}.png"
        ok = smart_crop_all_sides(frame, crop_path)

        if not ok:
            logger.log.emit(f"⚠️ {part} scene {sidx} skipped")
            continue

        json_out["scenes"].append({
            "scene_id": sidx,
            "duration": round(dur, 3),
            "frame_time": round(mid, 3),
            "percent": 0,
            "crop_image": f"crops/crop{sidx}.png"
        })

        total_duration += dur

    for s in json_out["scenes"]:
        s["percent"] = round(s["duration"] / total_duration, 6)

    json_out["total_raw_duration"] = round(total_duration, 3)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_out, f, indent=2)

    logger.log.emit(f"✅ {part} done")

# =========================
# PIPELINE (PARALLEL)
# =========================
class Pipeline:
    def __init__(self, root: Path, logger: Logger):
        self.root = root
        self.logger = logger

    def run(self):
        try:
            video = next(
                (f for f in self.root.iterdir()
                 if f.suffix.lower() in VIDEO_EXTS),
                None
            )

            csv_file = next(self.root.glob("*.csv"), None)

            parts = []
            with open(csv_file, newline="", encoding="utf-8", errors="ignore") as f:
                for row in csv.reader(f):
                    if len(row) >= 2:
                        parts.append((
                            time_to_seconds(row[0]),
                            time_to_seconds(row[1])
                        ))

            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as exe:
                futures = [
                    exe.submit(
                        process_part,
                        idx, s, e,
                        self.root, video, self.logger
                    )
                    for idx, (s, e) in enumerate(parts, 1)
                ]

                for _ in as_completed(futures):
                    pass

            self.logger.log.emit("🎉 ALL DONE")

        except Exception as e:
            self.logger.log.emit(f"❌ ERROR:\n{e}")

# =========================
# GUI
# =========================
class App(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Manhua Cropper ⚡ GPU Boost")
        self.resize(850, 600)

        self.label = QLabel("No folder selected")
        self.pick = QPushButton("Select Folder")
        self.start = QPushButton("Start")
        self.log = QPlainTextEdit(readOnly=True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.label)
        layout.addWidget(self.pick)
        layout.addWidget(self.start)
        layout.addWidget(self.log)

        self.root = None

        self.pick.clicked.connect(self.choose)
        self.start.clicked.connect(self.run_pipeline)

    def choose(self):
        p = QFileDialog.getExistingDirectory(self, "Select Folder")
        if p:
            self.root = Path(p)
            self.label.setText(p)

    def run_pipeline(self):
        if not self.root:
            return

        self.log.clear()

        logger = Logger()
        logger.log.connect(self.write)

        threading.Thread(
            target=lambda: Pipeline(self.root, logger).run(),
            daemon=True
        ).start()

    def write(self, msg):
        self.log.appendPlainText(msg)

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec())
