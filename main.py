import sys
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout,
    QTabWidget
)

# import your existing apps
from sarvam_translator import App as HindiApp  # MOVED TO TOP
from scene_extractor import App as ScenesApp
from sarvam_tts import App as TTSApp
from capcut_builder import App as ComposeApp


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Manhwa Auto Pipeline – ALL IN ONE")
        self.resize(1000, 700)

        layout = QVBoxLayout(self)

        tabs = QTabWidget()

        # ---------- TAB 1: Hindi Maker ----------  # NOW FIRST
        self.hindi_tab = HindiApp()
        tabs.addTab(self.hindi_tab, "🇮🇳 Hindi Maker")

        # ---------- TAB 2: Scene Cropper ----------
        self.scenes_tab = ScenesApp()
        tabs.addTab(self.scenes_tab, "🖼 Scene Cropper")

        # ---------- TAB 3: TTS ----------
        self.tts_tab = TTSApp()
        tabs.addTab(self.tts_tab, "🎙 TTS Generator")

        # ---------- TAB 4: Composer ----------
        self.compose_tab = ComposeApp()
        tabs.addTab(self.compose_tab, "🎬 Auto Composer")

        layout.addWidget(tabs)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())
