# miau
import sys
import subprocess
import re
import traceback
from pathlib import Path
from dataclasses import dataclass
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QLineEdit, QPushButton, QVBoxLayout, QHBoxLayout,
    QFileDialog, QComboBox, QCheckBox, QTextEdit, QProgressBar, QSlider,
    QGroupBox, QMessageBox, QSizePolicy, QSpacerItem, QScrollArea,
    QGraphicsOpacityEffect, QTabWidget
)

VIDEO_QUALITIES = ["Best", "8K", "4K", "1440p", "1080p", "720p", "480p", "360p"]
QUALITY_TO_HEIGHT = {"8K": "4320", "4K": "2160", "1440p": "1440", "1080p": "1080", "720p": "720", "480p": "480", "360p": "360"}
VIDEO_CONTAINERS = ["mp4", "webm"]
AUDIO_FORMATS = ["mp3", "m4a", "opus", "ogg", "flac", "wav"]
AUDIO_BITRATES = ["128", "192", "256", "320"]
OGG_OPUS_QUALITY = [f"q{i}" for i in range(0, 11)]
AAC_PROFILES = ["LC", "HE"]
SAMPLE_RATES = ["", "22050", "32000", "44100", "48000", "96000"]
CHANNELS = ["auto", "mono", "stereo"]
DEFAULT_TEMPLATE = "%(title)s.%(ext)s"


@dataclass
class DownloadConfig:
    url: str
    mode: str
    video_quality: str
    video_container: str
    audio_format: str
    audio_bitrate: str
    sample_rate: str
    channels: str
    mp3_normalize: bool
    flac_comp_level: int
    ogg_quality: str
    aac_profile: str
    out_dir: Path
    allow_playlist: bool
    force_single: bool
    name_template: str
    ffmpeg_extra: str
    meta_artist: str
    meta_album: str
    meta_title_override: str


class DownloadWorker(QThread):
    progress = Signal(int)
    log_line = Signal(str)
    finished = Signal(bool, str)

    def __init__(self, cfg: DownloadConfig):
        super().__init__()
        self.cfg = cfg
        self._stop = False

    def stop(self):
        self._stop = True

    def build_cmd(self):
        cmd = [sys.executable, "-m", "yt_dlp", "--newline"]
        cmd.append("--yes-playlist" if self.cfg.allow_playlist and not self.cfg.force_single else "--no-playlist")
        if self.cfg.meta_title_override.strip():
            out_template = str(self.cfg.out_dir / f"{self.cfg.meta_title_override.strip()}.%(ext)s")
        else:
            out_template = str(self.cfg.out_dir / (self.cfg.name_template or DEFAULT_TEMPLATE))
        cmd += ["-o", out_template]

        ff_args = []
        if self.cfg.meta_artist.strip():
            ff_args += ["-metadata", f"artist={self.cfg.meta_artist.strip()}"]
        if self.cfg.meta_album.strip():
            ff_args += ["-metadata", f"album={self.cfg.meta_album.strip()}"]
        if self.cfg.meta_title_override.strip():
            ff_args += ["-metadata", f"title={self.cfg.meta_title_override.strip()}"]

        if self.cfg.mode == "audio":
            fmt = self.cfg.audio_format.lower()
            if fmt == "mp3" and self.cfg.mp3_normalize:
                ff_args += ["-filter:a", "loudnorm"]
            if fmt == "flac":
                ff_args += ["-compression_level", str(self.cfg.flac_comp_level)]
            if fmt in ("ogg", "opus"):
                q = self.cfg.ogg_quality.lower()
                if q.startswith("q"):
                    ff_args += ["-q:a", q[1:]]
            if fmt == "m4a":
                profile = self.cfg.aac_profile.upper()
                if profile == "LC":
                    ff_args += ["-profile:a", "aac_low"]
                elif profile == "HE":
                    ff_args += ["-profile:a", "aac_he"]
            if self.cfg.sample_rate and self.cfg.sample_rate.isdigit():
                ff_args += ["-ar", self.cfg.sample_rate]
            if self.cfg.channels and self.cfg.channels in ("mono", "stereo"):
                ff_args += ["-ac", "1" if self.cfg.channels == "mono" else "2"]

        if self.cfg.ffmpeg_extra.strip():
            ff_args.append(self.cfg.ffmpeg_extra.strip())

        if ff_args:
            cmd += ["--postprocessor-args", " ".join(ff_args)]

        if self.cfg.mode == "audio":
            cmd += [
                "-f", "bestaudio/best",
                "--extract-audio",
                "--audio-format", self.cfg.audio_format.lower(),
                "--audio-quality", f"{self.cfg.audio_bitrate}k",
            ]
        else:
            cont = self.cfg.video_container.lower()
            qual = self.cfg.video_quality
            if qual == "Best":
                cmd += ["-f", "bestvideo+bestaudio/best", "--merge-output-format", cont]
            else:
                h = QUALITY_TO_HEIGHT.get(qual)
                if h:
                    selector = f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"
                else:
                    selector = "bestvideo+bestaudio/best"
                cmd += ["-f", selector, "--merge-output-format", cont]

        cmd.append(self.cfg.url.strip())
        return cmd

    def run(self):
        if not self.cfg.url.strip():
            self.finished.emit(False, "URL is required.")
            return
        try:
            self.cfg.out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            self.finished.emit(False, f"Cannot create output folder: {e}")
            return

        cmd = self.build_cmd()
        self.log_line.emit("Command:")
        self.log_line.emit(" ".join([f'"{c}"' if " " in c else c for c in cmd]))

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        except Exception as e:
            self.finished.emit(False, f"Failed to start yt-dlp: {e}")
            return

        percent = 0
        pat = re.compile(r"\[download\]\s+(\d+(?:\.\d+)?)%")
        try:
            for line in proc.stdout:
                if self._stop:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    self.finished.emit(False, "Download canceled.")
                    return
                line = line.rstrip()
                self.log_line.emit(line)
                m = pat.search(line)
                if m:
                    try:
                        percent = int(float(m.group(1)))
                        self.progress.emit(min(max(percent, 0), 100))
                    except Exception:
                        pass
        except Exception as e:
            self.finished.emit(False, f"Error during download: {e}")
            return

        ret = proc.wait()
        if ret == 0:
            self.progress.emit(100)
            self.finished.emit(True, "Download completed.")
        else:
            self.finished.emit(False, f"yt-dlp exited with code {ret}.")


class DarkBlackUI(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("THE HONORED ONE — Dark Black UI")
        self.resize(980, 760)
        self.out_dir = Path.cwd()
        self.worker = None
        self.setStyleSheet(self.qss_dark_black())
        self.init_ui()

    def init_ui(self):
        outer = QVBoxLayout(self)
        outer.setSpacing(10)
        font = QFont("Segoe UI", 10)
        self.setFont(font)

        self.top_progress = QProgressBar()
        self.top_progress.setRange(0, 100)
        self.top_progress.setValue(0)
        self.top_progress.setTextVisible(True)
        self.top_progress.setFixedHeight(18)
        outer.addWidget(self.top_progress)

        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)

        dl_tab = QWidget()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form = QVBoxLayout(content)
        form.setSpacing(12)

        row_url = QHBoxLayout()
        lbl_url = QLabel("URL")
        lbl_url.setFixedWidth(90)
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("https://...")
        row_url.addWidget(lbl_url)
        row_url.addWidget(self.url_edit)
        form.addLayout(row_url)

        row_mode = QHBoxLayout()
        lbl_mode = QLabel("Mode")
        lbl_mode.setFixedWidth(90)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Video", "Audio"])
        row_mode.addWidget(lbl_mode)
        row_mode.addWidget(self.mode_combo)
        row_mode.addStretch()
        form.addLayout(row_mode)

        row_vq = QHBoxLayout()
        lbl_vq = QLabel("Video quality")
        lbl_vq.setFixedWidth(90)
        self.video_quality_combo = QComboBox()
        self.video_quality_combo.addItems(VIDEO_QUALITIES)
        row_vq.addWidget(lbl_vq)
        row_vq.addWidget(self.video_quality_combo)
        form.addLayout(row_vq)

        row_vc = QHBoxLayout()
        lbl_vc = QLabel("Container")
        lbl_vc.setFixedWidth(90)
        self.video_container_combo = QComboBox()
        self.video_container_combo.addItems(VIDEO_CONTAINERS)
        row_vc.addWidget(lbl_vc)
        row_vc.addWidget(self.video_container_combo)
        form.addLayout(row_vc)

        row_af = QHBoxLayout()
        lbl_af = QLabel("Audio format")
        lbl_af.setFixedWidth(90)
        self.audio_format_combo = QComboBox()
        self.audio_format_combo.addItems(AUDIO_FORMATS)
        row_af.addWidget(lbl_af)
        row_af.addWidget(self.audio_format_combo)
        form.addLayout(row_af)

        row_ab = QHBoxLayout()
        lbl_ab = QLabel("Bitrate (kbps)")
        lbl_ab.setFixedWidth(90)
        self.audio_bitrate_combo = QComboBox()
        self.audio_bitrate_combo.addItems(AUDIO_BITRATES)
        row_ab.addWidget(lbl_ab)
        row_ab.addWidget(self.audio_bitrate_combo)
        form.addLayout(row_ab)

        self.adv_group = QGroupBox("Advanced audio options")
        adv_layout = QVBoxLayout(self.adv_group)
        self.mp3_norm_check = QCheckBox("Normalize MP3 volume (loudnorm)")
        self.flac_label = QLabel("FLAC compression level (0–8)")
        self.flac_slider = QSlider(Qt.Horizontal)
        self.flac_slider.setRange(0, 8)
        self.flac_slider.setValue(5)
        self.ogg_label = QLabel("OGG/Opus quality (q0–q10)")
        self.ogg_combo = QComboBox()
        self.ogg_combo.addItems(OGG_OPUS_QUALITY)
        self.m4a_label = QLabel("M4A (AAC) profile")
        self.m4a_combo = QComboBox()
        self.m4a_combo.addItems(AAC_PROFILES)
        sr_row = QHBoxLayout()
        sr_row.addWidget(QLabel("Sample rate (Hz)"))
        self.sample_rate_combo = QComboBox()
        self.sample_rate_combo.addItems(SAMPLE_RATES)
        sr_row.addWidget(self.sample_rate_combo)
        sr_row.addStretch()
        ch_row = QHBoxLayout()
        ch_row.addWidget(QLabel("Channels"))
        self.channels_combo = QComboBox()
        self.channels_combo.addItems(CHANNELS)
        ch_row.addWidget(self.channels_combo)
        ch_row.addStretch()
        adv_layout.addWidget(self.mp3_norm_check)
        adv_layout.addWidget(self.flac_label)
        adv_layout.addWidget(self.flac_slider)
        adv_layout.addWidget(self.ogg_label)
        adv_layout.addWidget(self.ogg_combo)
        adv_layout.addWidget(self.m4a_label)
        adv_layout.addWidget(self.m4a_combo)
        adv_layout.addLayout(sr_row)
        adv_layout.addLayout(ch_row)
        self.adv_group.setVisible(False)
        form.addWidget(self.adv_group)

        meta_row = QHBoxLayout()
        meta_row.addWidget(QLabel("Artist"))
        self.meta_artist = QLineEdit()
        self.meta_artist.setPlaceholderText("Artist")
        meta_row.addWidget(self.meta_artist)
        meta_row.addWidget(QLabel("Album"))
        self.meta_album = QLineEdit()
        self.meta_album.setPlaceholderText("Album")
        meta_row.addWidget(self.meta_album)
        meta_row.addWidget(QLabel("Title"))
        self.meta_title = QLineEdit()
        self.meta_title.setPlaceholderText("Title override")
        meta_row.addWidget(self.meta_title)
        form.addLayout(meta_row)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output folder"))
        self.out_label = QLabel(str(self.out_dir))
        self.out_btn = QPushButton("Choose...")
        out_row.addWidget(self.out_label)
        out_row.addWidget(self.out_btn)
        out_row.addStretch()
        form.addLayout(out_row)

        tpl_row = QHBoxLayout()
        tpl_row.addWidget(QLabel("Filename template"))
        self.template_edit = QLineEdit()
        self.template_edit.setText(DEFAULT_TEMPLATE)
        tpl_row.addWidget(self.template_edit)
        form.addLayout(tpl_row)

        playlist_row = QHBoxLayout()
        self.allow_playlist = QCheckBox("Allow playlist")
        self.allow_playlist.setChecked(True)
        self.force_single = QCheckBox("Force single")
        playlist_row.addWidget(self.allow_playlist)
        playlist_row.addWidget(self.force_single)
        playlist_row.addStretch()
        form.addLayout(playlist_row)

        ff_row = QHBoxLayout()
        ff_row.addWidget(QLabel("FFmpeg extra args"))
        self.ffmpeg_args = QLineEdit()
        ff_row.addWidget(self.ffmpeg_args)
        form.addLayout(ff_row)

        content.setLayout(form)
        scroll.setWidget(content)
        dl_layout = QVBoxLayout(dl_tab)
        dl_layout.addWidget(scroll)
        self.tabs.addTab(dl_tab, "Download")

        log_tab = QWidget()
        log_layout = QVBoxLayout(log_tab)
        self.log_progress = QProgressBar()
        self.log_progress.setRange(0, 100)
        self.log_progress.setValue(0)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(300)
        log_layout.addWidget(self.log_progress)
        log_layout.addWidget(self.log_view)
        self.tabs.addTab(log_tab, "Log")

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.start_btn = QPushButton("Download")
        self.start_btn.setFixedHeight(44)
        self.start_btn.setStyleSheet(
            "background:#222222; color:#ffffff; border: 1px solid #444444; border-radius:8px; font-weight:bold; font-size:14px;"
        )
        self.start_btn.setVisible(True)
        self.start_btn.setEnabled(True)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setFixedHeight(44)
        self.cancel_btn.setStyleSheet("background:#111111; color:#bbbbbb; border: 1px solid #222222; border-radius:8px;")
        self.cancel_btn.setEnabled(False)
        btn_row.addWidget(self.start_btn)
        btn_row.addWidget(self.cancel_btn)
        outer.addLayout(btn_row)

        self.out_btn.clicked.connect(self.choose_dir)
        self.start_btn.clicked.connect(self.start_download)
        self.cancel_btn.clicked.connect(self.cancel_download)
        self.mode_combo.currentIndexChanged.connect(self.toggle_mode)
        self.audio_format_combo.currentIndexChanged.connect(self.on_audio_format_changed)

        self.video_controls = [self.video_quality_combo, self.video_container_combo]
        self.audio_controls = [self.audio_format_combo, self.audio_bitrate_combo]
        self.all_controls = self.video_controls + self.audio_controls + [self.adv_group]

        self.opacity_effects = {}
        for w in self.all_controls + [self.start_btn, self.cancel_btn]:
            eff = QGraphicsOpacityEffect()
            w.setGraphicsEffect(eff)
            eff.setOpacity(1.0)
            self.opacity_effects[w] = eff

        self.toggle_mode()
        self.on_audio_format_changed()

    def qss_dark_black(self):
        return """
        QWidget { background: #000000; color: #e6eef6; }
        QLineEdit, QComboBox, QTextEdit { background: #0a0a0a; border: 1px solid #222222; padding:6px; border-radius:6px; color:#e6eef6; }
        QPushButton { background: #111111; color: #e6eef6; border: 1px solid #333333; border-radius:8px; padding:8px 14px; }
        QPushButton:disabled { background: #0b0b0b; color:#666666; border: 1px solid #1a1a1a; }
        QGroupBox { border: 1px solid #222222; border-radius:8px; margin-top:8px; padding:8px; background: #050505; }
        QLabel { color:#dfe9f2; }
        QProgressBar { background: #050505; border: 1px solid #222222; border-radius:9px; text-align:center; color:#e6eef6; }
        QProgressBar::chunk { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #7cff6b, stop:1 #2ef08a); border-radius:9px; }
        """

    def choose_dir(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose output folder", str(self.out_dir))
        if folder:
            self.out_dir = Path(folder)
            self.out_label.setText(str(self.out_dir))

    def append_log(self, text: str):
        try:
            if hasattr(self, "log_view") and self.log_view is not None:
                self.log_view.append(text)
        except Exception:
            print("Failed to append to log_view:", text)

    def set_opacity(self, widget, value):
        eff = widget.graphicsEffect()
        if isinstance(eff, QGraphicsOpacityEffect):
            eff.setOpacity(value)

    def update_button_opacity(self):
        self.set_opacity(self.start_btn, 1.0 if self.start_btn.isEnabled() else 0.35)
        self.set_opacity(self.cancel_btn, 1.0 if self.cancel_btn.isEnabled() else 0.35)

    def toggle_mode(self):
        mode = self.mode_combo.currentText().lower()
        is_video = (mode == "video")
        for w in self.video_controls:
            w.setEnabled(is_video)
            self.set_opacity(w, 1.0 if is_video else 0.35)
        for w in self.audio_controls:
            w.setEnabled(not is_video)
            self.set_opacity(w, 1.0 if not is_video else 0.35)
        self.adv_group.setVisible(False)
        self.set_opacity(self.adv_group, 0.35)
        self.start_btn.setVisible(True)
        self.start_btn.raise_()
        self.update_button_opacity()

    def on_audio_format_changed(self):
        fmt = self.audio_format_combo.currentText().lower()
        show = fmt in ("mp3", "flac", "wav", "ogg", "opus", "m4a") and self.mode_combo.currentText().lower() == "audio"
        self.adv_group.setVisible(show)
        self.set_opacity(self.adv_group, 1.0 if show else 0.35)
        self.mp3_norm_check.setVisible(fmt == "mp3")
        self.mp3_norm_check.setEnabled(fmt == "mp3")
        self.set_opacity(self.mp3_norm_check, 1.0 if fmt == "mp3" else 0.35)
        self.flac_label.setVisible(fmt == "flac")
        self.flac_slider.setVisible(fmt == "flac")
        self.flac_slider.setEnabled(fmt == "flac")
        self.set_opacity(self.flac_slider, 1.0 if fmt == "flac" else 0.35)
        self.ogg_label.setVisible(fmt in ("ogg", "opus"))
        self.ogg_combo.setVisible(fmt in ("ogg", "opus"))
        self.ogg_combo.setEnabled(fmt in ("ogg", "opus"))
        self.set_opacity(self.ogg_combo, 1.0 if fmt in ("ogg", "opus") else 0.35)
        self.m4a_label.setVisible(fmt == "m4a")
        self.m4a_combo.setVisible(fmt == "m4a")
        self.m4a_combo.setEnabled(fmt == "m4a")
        self.set_opacity(self.m4a_combo, 1.0 if fmt == "m4a" else 0.35)
        self.sample_rate_combo.setEnabled(fmt in ("wav", "flac", "m4a", "mp3"))
        self.set_opacity(self.sample_rate_combo, 1.0 if fmt in ("wav", "flac", "m4a", "mp3") else 0.35)
        self.channels_combo.setEnabled(fmt in ("wav", "flac", "m4a", "mp3", "ogg", "opus"))
        self.set_opacity(self.channels_combo, 1.0 if fmt in ("wav", "flac", "m4a", "mp3", "ogg", "opus") else 0.35)
        self.start_btn.setVisible(True)
        self.start_btn.raise_()
        self.update_button_opacity()

    def toggle_controls(self, running: bool):
        self.start_btn.setEnabled(not running)
        self.cancel_btn.setEnabled(running)
        for w in (
            self.url_edit, self.mode_combo, self.video_quality_combo, self.video_container_combo,
            self.audio_format_combo, self.audio_bitrate_combo, self.meta_artist, self.meta_album, self.meta_title,
            self.out_btn, self.template_edit, self.allow_playlist, self.force_single, self.mp3_norm_check,
            self.flac_slider, self.ogg_combo, self.m4a_combo, self.sample_rate_combo, self.channels_combo, self.ffmpeg_args
        ):
            w.setEnabled(not running)
        self.update_button_opacity()
        self.start_btn.setVisible(True)
        self.start_btn.raise_()

    def validate_inputs(self):
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.critical(self, "Error", "URL is required.")
            return False
        if not (url.startswith("http://") or url.startswith("https://")):
            QMessageBox.critical(self, "Error", "URL must start with http:// or https://")
            return False
        mode = self.mode_combo.currentText().lower()
        if mode == "video":
            if self.video_quality_combo.currentText() not in VIDEO_QUALITIES:
                QMessageBox.critical(self, "Error", "Invalid video quality.")
                return False
            if self.video_container_combo.currentText() not in VIDEO_CONTAINERS:
                QMessageBox.critical(self, "Error", "Invalid container.")
                return False
        else:
            if self.audio_format_combo.currentText() not in AUDIO_FORMATS:
                QMessageBox.critical(self, "Error", "Invalid audio format.")
                return False
            if self.audio_bitrate_combo.currentText() not in AUDIO_BITRATES:
                QMessageBox.critical(self, "Error", "Invalid bitrate.")
                return False
        if not self.template_edit.text().strip() and not self.meta_title.text().strip():
            self.template_edit.setText(DEFAULT_TEMPLATE)
        return True

    def build_config(self):
        return DownloadConfig(
            url=self.url_edit.text().strip(),
            mode=self.mode_combo.currentText().lower(),
            video_quality=self.video_quality_combo.currentText(),
            video_container=self.video_container_combo.currentText().lower(),
            audio_format=self.audio_format_combo.currentText().lower(),
            audio_bitrate=self.audio_bitrate_combo.currentText(),
            sample_rate=self.sample_rate_combo.currentText().strip(),
            channels=self.channels_combo.currentText(),
            mp3_normalize=self.mp3_norm_check.isChecked(),
            flac_comp_level=int(self.flac_slider.value()),
            ogg_quality=self.ogg_combo.currentText(),
            aac_profile=self.m4a_combo.currentText(),
            out_dir=self.out_dir,
            allow_playlist=self.allow_playlist.isChecked(),
            force_single=self.force_single.isChecked(),
            name_template=self.template_edit.text().strip() or DEFAULT_TEMPLATE,
            ffmpeg_extra=self.ffmpeg_args.text().strip(),
            meta_artist=self.meta_artist.text().strip(),
            meta_album=self.meta_album.text().strip(),
            meta_title_override=self.meta_title.text().strip()
        )

    def start_download(self):
        if not self.validate_inputs():
            return
        try:
            self.top_progress.setValue(0)
        except Exception:
            pass
        if hasattr(self, "log_view") and self.log_view is not None:
            self.log_view.clear()
        if hasattr(self, "log_progress") and self.log_progress is not None:
            self.log_progress.setValue(0)
        self.toggle_controls(True)
        cfg = self.build_config()
        self.worker = DownloadWorker(cfg)
        if hasattr(self, "top_progress") and self.top_progress is not None:
            self.worker.progress.connect(self.top_progress.setValue)
        if hasattr(self, "log_progress") and self.log_progress is not None:
            self.worker.progress.connect(self.log_progress.setValue)
        self.worker.log_line.connect(self.append_log)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()
        self.append_log("=== Summary ===")
        self.append_log(f"URL: {cfg.url}")
        self.append_log(f"Mode: {cfg.mode}")
        if cfg.mode == "audio":
            self.append_log(f"Audio: {cfg.audio_format} @ {cfg.audio_bitrate}k")
            if cfg.sample_rate:
                self.append_log(f"Sample rate: {cfg.sample_rate} Hz")
            if cfg.channels and cfg.channels != "auto":
                self.append_log(f"Channels: {cfg.channels}")
        else:
            self.append_log(f"Video: {cfg.video_quality} in {cfg.video_container}")
        if cfg.meta_artist:
            self.append_log(f"Artist: {cfg.meta_artist}")
        if cfg.meta_album:
            self.append_log(f"Album: {cfg.meta_album}")
        if cfg.meta_title_override:
            self.append_log(f"Title override: {cfg.meta_title_override}")
        if cfg.ffmpeg_extra:
            self.append_log(f"FFmpeg extra: {cfg.ffmpeg_extra}")
        self.append_log("================")
        try:
            if hasattr(self, "tabs") and self.tabs is not None and self.tabs.count() > 1:
                self.tabs.setCurrentIndex(1)
        except Exception:
            pass

    def cancel_download(self):
        if self.worker:
            self.worker.stop()
            self.append_log("Cancel requested...")

    def on_finished(self, success: bool, msg: str):
        try:
            self.append_log(msg)
            self.toggle_controls(False)
            self.worker = None
            if success:
                QMessageBox.information(self, "Done", "Download completed successfully.")
                self.append_log("Download finished successfully.")
            else:
                QMessageBox.critical(self, "Error", msg)
                self.append_log(f"Error: {msg}")
        except Exception:
            tb = traceback.format_exc()
            try:
                if hasattr(self, "log_view") and self.log_view is not None:
                    self.log_view.append("EXCEPTION in on_finished:\n" + tb)
            except Exception:
                pass
            print("EXCEPTION in on_finished:\n", tb)
        finally:
            try:
                self.start_btn.setVisible(True)
                self.start_btn.raise_()
                self.update_button_opacity()
            except Exception:
                pass


def install_exception_hook(app_window):
    def excepthook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            if hasattr(app_window, "log_view") and app_window.log_view is not None:
                app_window.log_view.append("UNCAUGHT EXCEPTION:\n" + text)
        except Exception:
            pass
        print("UNCAUGHT EXCEPTION:\n", text)
    sys.excepthook = excepthook


def main():
    app = QApplication(sys.argv)
    w = DarkBlackUI()
    install_exception_hook(w)
    w.setAttribute(Qt.WA_DeleteOnClose, False)

    def _close_event(event):
        if getattr(w, "worker", None) is not None:
            QMessageBox.warning(w, "Warning", "A download is running. Cancel it before closing.")
            event.ignore()
            return
        reply = QMessageBox.question(w, "Quit", "Do you really want to quit?", QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            event.accept()
        else:
            event.ignore()

    w.closeEvent = _close_event
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()