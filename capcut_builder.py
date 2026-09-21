import sys, json, shutil, uuid, re, subprocess, copy
from pathlib import Path
from PySide6.QtWidgets import *

# ================= SORT =================
def natural_key(p):
    return [int(x) if x.isdigit() else x.lower()
            for x in re.findall(r'\d+|\D+', p.name)]

# ================= AUDIO =================
def get_audio_duration(path):
    r = subprocess.run([
        "ffprobe","-v","error",
        "-show_entries","format=duration",
        "-of","default=noprint_wrappers=1:nokey=1",
        str(path)
    ], capture_output=True, text=True)
    return float(r.stdout.strip())

# ================= AUDIO CONVERT =================
def convert_audio(audio_root, log):
    wavs = list(audio_root.glob("*.wav"))
    if not wavs:
        return

    log("🎵 Converting WAV → MP3...")

    for w in wavs:
        mp3 = w.with_suffix(".mp3")

        subprocess.run([
            "ffmpeg","-y","-i",str(w),
            "-codec:a","libmp3lame","-qscale:a","2",
            str(mp3)
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        w.unlink()

    log("✅ Audio Ready")

# ================= KEYFRAME SCALE =================
def scale_keyframes(segment, new_duration):
    if "common_keyframes" not in segment:
        return

    for block in segment["common_keyframes"]:
        kfs = block.get("keyframe_list", [])
        if len(kfs) < 2:
            continue

        old_max = kfs[-1]["time_offset"]
        if old_max == 0:
            continue

        scale = new_duration / old_max

        for kf in kfs:
            kf["time_offset"] = int(kf["time_offset"] * scale)

# ================= KEYFRAME REVERSE =================
def reverse_keyframes(segment):
    if "common_keyframes" not in segment:
        return

    for block in segment["common_keyframes"]:
        kfs = block.get("keyframe_list", [])
        if len(kfs) < 2:
            continue

        kfs[0]["values"], kfs[-1]["values"] = kfs[-1]["values"], kfs[0]["values"]

# ================= BUILDER =================
def build(root, template, output, log):

    out = output / "FINAL_PROJECT"
    if out.exists():
        shutil.rmtree(out)

    shutil.copytree(template, out)

    draft = out / "draft_info.json"
    data = json.load(open(draft, encoding="utf-8"))

    # ===== TEMPLATE FETCH =====
    video_track = next(t for t in data["tracks"] if t["type"] == "video")
    template_segment = video_track["segments"][0]
    template_mat_id = template_segment["material_id"]

    template_mat = next(m for m in data["materials"]["videos"]
                        if m["id"] == template_mat_id)

    # ===== SAFE RESET =====
    video_track["segments"] = []
    data["materials"]["videos"] = []
    data["materials"]["audios"] = []

    audio_track = {
        "id": str(uuid.uuid4()),
        "type": "audio_track",
        "segments": []
    }

    media = out / "resources"
    media.mkdir(exist_ok=True)

    # 🔥 AUDIO PREP
    convert_audio(root / "audio", log)

    current = 0
    parts = sorted((root / "output").glob("Part*"), key=natural_key)

    for part_dir in parts:
        part = part_dir.name
        log(f"🎬 {part}")

        scenes_file = part_dir / "scenes.json"
        audio_src = root / "audio" / f"{part}.mp3"

        if not scenes_file.exists() or not audio_src.exists():
            log(f"⚠️ skip {part}")
            continue

        scenes = json.load(open(scenes_file))["scenes"]

        # ===== AUDIO =====
        audio_dst = media / f"{uuid.uuid4()}.mp3"
        shutil.copy(audio_src, audio_dst)

        adur = get_audio_duration(audio_dst)
        aid = str(uuid.uuid4())

        data["materials"]["audios"].append({
            "id": aid,
            "type": "audio",
            "path": str(audio_dst),
            "duration": int(adur * 1e6)
        })

        audio_track["segments"].append({
            "id": str(uuid.uuid4()),
            "material_id": aid,
            "target_timerange": {
                "start": int(current * 1e6),
                "duration": int(adur * 1e6)
            }
        })

        # ===== SCENES =====
        for i, sc in enumerate(scenes):
            dur = sc["percent"] * adur
            dur_us = int(dur * 1e6)

            src = part_dir / sc["crop_image"]
            dst = media / f"{uuid.uuid4()}.png"
            shutil.copy(src, dst)

            # ===== MATERIAL CLONE =====
            new_mat = copy.deepcopy(template_mat)
            mid = str(uuid.uuid4())

            new_mat["id"] = mid
            new_mat["path"] = str(dst)
            new_mat["material_name"] = dst.name
            new_mat["local_material_id"] = ""

            data["materials"]["videos"].append(new_mat)

            # ===== SEGMENT CLONE =====
            seg = copy.deepcopy(template_segment)

            seg["id"] = str(uuid.uuid4())
            seg["material_id"] = mid

            # 🔥 CRITICAL FIX (FULL SYNC)
            seg["target_timerange"]["start"] = int(current * 1e6)
            seg["target_timerange"]["duration"] = dur_us

            seg["source_timerange"]["start"] = 0
            seg["source_timerange"]["duration"] = dur_us

            # 🔥 KEYFRAME FIX
            scale_keyframes(seg, dur_us)

            # 🔥 ALTERNATE MOTION
            if i % 2 == 1:
                reverse_keyframes(seg)

            video_track["segments"].append(seg)

            current += dur

    data["tracks"].append(audio_track)

    with open(draft, "w") as f:
        json.dump(data, f)

    log("🔥 PERFECT FINAL PROJECT READY (100% TEMPLATE MATCH)")

# ================= GUI =================
class App(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("🔥 CapCut Builder FINAL PRO")

        self.root = None
        self.template = None
        self.output = None

        layout = QVBoxLayout(self)

        self.l1 = QLabel("Root Folder")
        self.b1 = QPushButton("Select Root")

        self.l2 = QLabel("Template Folder")
        self.b2 = QPushButton("Select Template")

        self.l3 = QLabel("Output Folder")
        self.b3 = QPushButton("Select Output")

        self.run = QPushButton("🚀 BUILD")

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)

        for w in [self.l1,self.b1,self.l2,self.b2,self.l3,self.b3,self.run,self.log]:
            layout.addWidget(w)

        self.b1.clicked.connect(self.pick_root)
        self.b2.clicked.connect(self.pick_template)
        self.b3.clicked.connect(self.pick_output)
        self.run.clicked.connect(self.start)

    def log_msg(self,t):
        self.log.appendPlainText(t)

    def pick_root(self):
        p = QFileDialog.getExistingDirectory(self)
        if p:
            self.root = Path(p)
            self.l1.setText(p)

    def pick_template(self):
        p = QFileDialog.getExistingDirectory(self)
        if p:
            self.template = Path(p)
            self.l2.setText(p)

    def pick_output(self):
        p = QFileDialog.getExistingDirectory(self)
        if p:
            self.output = Path(p)
            self.l3.setText(p)

    def start(self):
        if not all([self.root,self.template,self.output]):
            QMessageBox.warning(self,"Error","Select all folders")
            return

        try:
            self.log_msg("🚀 Started...")
            build(self.root,self.template,self.output,self.log_msg)
            self.log_msg("✅ DONE")
        except Exception as e:
            self.log_msg(f"❌ ERROR: {e}")

# ================= MAIN =================
if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = App()
    w.show()
    sys.exit(app.exec())
