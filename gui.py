import os
import sys
import json
import time
import subprocess
import collections
import threading
from pathlib import Path
from typing import Optional, List, Dict

from PyQt5 import QtWidgets, QtCore, QtGui

CURRENT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(CURRENT_DIR))
os.chdir(CURRENT_DIR)

from core import __version__
from core.context_buffer import ContextBuffer
from core.arbiter import MemeArbiter, DecisionResult
from core.http_client import get_logger
from core.player import AudioPlayer
from core.audio_listener import AudioListener
from core.system_audio_listener import SystemAudioListener
from core.screen_watcher import ScreenWatcher, capture_screen_base64, capture_screen_for_vision

logger = get_logger("gui")

try:
    import keyboard
    _KEYBOARD_AVAILABLE = True
except ImportError:
    _KEYBOARD_AVAILABLE = False

CONFIG_PATH = CURRENT_DIR / "config.json"
MEME_LIB_PATH = CURRENT_DIR / "meme_library.json"


def load_json(p: Path):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(p: Path, data):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def resolve_audio_file_path(filename_str: str) -> Path:
    p = Path(filename_str)
    if p.is_absolute() and p.exists():
        return p
    candidates = [
        CURRENT_DIR.parent / p,
        CURRENT_DIR / p,
        CURRENT_DIR.parent / "新三国语音包" / p.name,
        CURRENT_DIR.parent / "新三语音包" / p.name,
        CURRENT_DIR.parent / "AutoMemeDetector-main" / "sounds" / p.name,
        p
    ]
    for c in candidates:
        if c.resolve().exists():
            return c.resolve()
    return (CURRENT_DIR.parent / p).resolve()


# ---------------------------------------------------------------------------
# 设计规范：中性灰蓝暗色主题 (Neutral Slate Theme)
# 参考 Linear / Raycast / Windows 11 Settings 的间距与层级规范
# ---------------------------------------------------------------------------
THEME = """
* {
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
}

QMainWindow, QDialog {
    background-color: #14161c;
}

QWidget {
    color: #e8ecf3;
    font-size: 14px;
}

/* ---- 侧边导航 ---- */
#sidebar {
    background-color: #101218;
    border-right: 1px solid #22262f;
}

#brand_title {
    color: #ffffff;
    font-size: 16px;
    font-weight: 700;
    padding: 0;
}

#brand_sub {
    color: #6b7484;
    font-size: 12px;
}

#version_label {
    color: #4a5261;
    font-size: 11px;
}

QPushButton#nav_btn {
    text-align: left;
    padding: 11px 16px;
    background-color: transparent;
    border: none;
    border-radius: 8px;
    color: #98a1b0;
    font-size: 14px;
    font-weight: 500;
}
QPushButton#nav_btn:hover {
    background-color: #1a1e27;
    color: #e8ecf3;
}
QPushButton#nav_btn:checked {
    background-color: #1e2637;
    color: #6cb6ff;
    font-weight: 600;
}

/* ---- 卡片 ---- */
#card {
    background-color: #191c24;
    border: 1px solid #252932;
    border-radius: 10px;
}
#card_flat {
    background-color: #161920;
    border: 1px solid #22262f;
    border-radius: 8px;
}

/* ---- 标题 ---- */
#page_title {
    color: #ffffff;
    font-size: 20px;
    font-weight: 700;
}
#page_desc {
    color: #7b8496;
    font-size: 13px;
}
#section_title {
    color: #9aa4b3;
    font-size: 12px;
    font-weight: 600;
    letter-spacing: 0.5px;
}
QLabel#field_label {
    color: #8892a2;
    font-size: 13px;
}

/* ---- 输入控件 ---- */
QLineEdit, QComboBox, QSpinBox {
    background-color: #101218;
    border: 1px solid #2a2f3a;
    border-radius: 7px;
    padding: 8px 12px;
    color: #e8ecf3;
    font-size: 13px;
    selection-background-color: #2f6fbd;
}
QLineEdit:hover, QComboBox:hover {
    border-color: #3a4150;
}
QLineEdit:focus, QComboBox:focus {
    border: 1px solid #4a8fdb;
    background-color: #14171e;
}
QLineEdit:disabled, QComboBox:disabled {
    color: #5a6272;
    background-color: #14161c;
    border-color: #22262f;
}

QComboBox::drop-down {
    border: none;
    width: 26px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid #7b8496;
    margin-right: 10px;
}
QComboBox QAbstractItemView {
    background-color: #1b1f28;
    border: 1px solid #2a2f3a;
    border-radius: 6px;
    color: #e8ecf3;
    selection-background-color: #2a3444;
    outline: none;
    padding: 4px;
}

/* ---- 按钮 ---- */
QPushButton {
    background-color: #232832;
    border: 1px solid #2f3644;
    border-radius: 7px;
    padding: 8px 16px;
    color: #d5dbe5;
    font-size: 13px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #2b313d;
    border-color: #3d4555;
    color: #ffffff;
}
QPushButton:pressed {
    background-color: #1c212a;
}

QPushButton#btn_primary {
    background-color: #2f6fbd;
    border: 1px solid #3d82d6;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#btn_primary:hover {
    background-color: #3879cc;
    border-color: #4d92e6;
}

QPushButton#btn_start {
    background-color: #2e9e6b;
    border: 1px solid #3fb87d;
    color: #ffffff;
    font-weight: 600;
    padding: 9px 22px;
    font-size: 14px;
}
QPushButton#btn_start:hover {
    background-color: #37ae76;
}

QPushButton#btn_stop {
    background-color: #c9453f;
    border: 1px solid #db5852;
    color: #ffffff;
    font-weight: 600;
    padding: 9px 22px;
    font-size: 14px;
}
QPushButton#btn_stop:hover {
    background-color: #d5534d;
}

QPushButton#btn_ghost {
    background-color: transparent;
    border: 1px solid #2f3644;
    color: #98a1b0;
}
QPushButton#btn_ghost:hover {
    background-color: #232832;
    color: #e8ecf3;
}

QPushButton#chip_btn {
    background-color: #1d222b;
    border: 1px solid #2a2f3a;
    border-radius: 13px;
    padding: 5px 13px;
    font-size: 12px;
    color: #9aa4b3;
    font-weight: 500;
}
QPushButton#chip_btn:hover {
    background-color: #262d39;
    border-color: #4a8fdb;
    color: #6cb6ff;
}

/* ---- 输出区 ---- */
QTextBrowser, QTextEdit {
    background-color: #0e1015;
    border: 1px solid #22262f;
    border-radius: 8px;
    color: #d5dbe5;
    padding: 12px;
    font-family: "Cascadia Mono", "Consolas", "Microsoft YaHei UI", monospace;
    font-size: 13px;
}

QListWidget {
    background-color: #0e1015;
    border: 1px solid #22262f;
    border-radius: 8px;
    color: #e8ecf3;
    padding: 6px;
    outline: none;
}
QListWidget::item {
    padding: 10px 12px;
    border-radius: 6px;
    margin-bottom: 3px;
    color: #c4cbd6;
}
QListWidget::item:hover {
    background-color: #191d26;
}
QListWidget::item:selected {
    background-color: #232d3f;
    color: #ffffff;
}

/* ---- 开关 ---- */
QCheckBox {
    color: #b8c0cd;
    spacing: 8px;
    font-size: 13px;
}
QCheckBox::indicator {
    width: 17px;
    height: 17px;
    border-radius: 4px;
    border: 1px solid #333a48;
    background-color: #101218;
}
QCheckBox::indicator:hover {
    border-color: #4a8fdb;
}
QCheckBox::indicator:checked {
    background-color: #2f6fbd;
    border-color: #4a8fdb;
}

QRadioButton {
    color: #c4cbd6;
    spacing: 8px;
    font-size: 13px;
    padding: 4px 0;
}
QRadioButton::indicator {
    width: 16px;
    height: 16px;
    border-radius: 9px;
    border: 1px solid #333a48;
    background-color: #101218;
}
QRadioButton::indicator:checked {
    background-color: #4a8fdb;
    border: 4px solid #101218;
}

/* ---- 进度条 ---- */
QProgressBar {
    background-color: #191d26;
    border: none;
    border-radius: 4px;
}
QProgressBar::chunk {
    border-radius: 4px;
}

/* ---- 滚动条 ---- */
QScrollBar:vertical {
    background: #14161c;
    width: 10px;
    margin: 0;
    border: none;
}
QScrollBar::handle:vertical {
    background: #2f3644;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #3d4555;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
    background: #14161c;
    border: none;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: #14161c;
    border: none;
}
QScrollBar:horizontal {
    background: #14161c;
    height: 10px;
    margin: 0;
    border: none;
}
QScrollBar::handle:horizontal {
    background: #2f3644;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: #3d4555;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
    background: #14161c;
    border: none;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: #14161c;
    border: none;
}

/* ---- 滚动区域：清除 Fusion 默认的浅色视口 ---- */
QScrollArea {
    border: none;
    background-color: #14161c;
}
QScrollArea > QWidget > QWidget {
    background-color: #14161c;
}
QScrollArea > QWidget > QScrollBar {
    background-color: #14161c;
}
QAbstractScrollArea::corner {
    background-color: #14161c;
    border: none;
}

QStackedWidget, QStackedWidget > QWidget {
    background-color: #14161c;
}

QToolTip {
    background-color: #232832;
    color: #e8ecf3;
    border: 1px solid #3d4555;
    border-radius: 5px;
    padding: 6px 9px;
    font-size: 12px;
}
"""


class Signals(QtCore.QObject):
    log = QtCore.pyqtSignal(str, str)
    audit = QtCore.pyqtSignal(object)
    mic_level = QtCore.pyqtSignal(int)
    speaker_level = QtCore.pyqtSignal(int)


class Stepper(QtWidgets.QWidget):
    """紧凑步进器：[-] 数值 [+]"""
    valueChanged = QtCore.pyqtSignal(float)

    def __init__(self, val, min_v, max_v, step, unit="", is_int=False):
        super().__init__()
        self.val = val
        self.min_v = min_v
        self.max_v = max_v
        self.step = step
        self.unit = unit
        self.is_int = is_int

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self.btn_minus = QtWidgets.QPushButton("−")
        self.btn_minus.setFixedSize(26, 26)
        self.btn_minus.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_minus.clicked.connect(self._dec)
        lay.addWidget(self.btn_minus)

        self.lbl = QtWidgets.QLabel()
        self.lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl.setFixedWidth(60)
        self.lbl.setStyleSheet(
            "background:#101218; border:1px solid #2a2f3a; border-radius:6px;"
            "padding:4px 0; font-weight:600; color:#e8ecf3; font-size:13px;"
        )
        lay.addWidget(self.lbl)

        self.btn_plus = QtWidgets.QPushButton("+")
        self.btn_plus.setFixedSize(26, 26)
        self.btn_plus.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_plus.clicked.connect(self._inc)
        lay.addWidget(self.btn_plus)

        self._refresh()

    def _refresh(self):
        if self.is_int:
            self.lbl.setText(f"{int(self.val)}{self.unit}")
        elif self.step < 1:
            self.lbl.setText(f"{self.val:.1f}{self.unit}")
        else:
            self.lbl.setText(f"{self.val:g}{self.unit}")

    def _dec(self):
        self.val = max(self.min_v, self.val - self.step)
        self._refresh()
        self.valueChanged.emit(self.val)

    def _inc(self):
        self.val = min(self.max_v, self.val + self.step)
        self._refresh()
        self.valueChanged.emit(self.val)

    def setValue(self, v):
        self.val = max(self.min_v, min(self.max_v, v))
        self._refresh()


class SliderRow(QtWidgets.QWidget):
    """音量条：可拖拽的连续滑块 + 实时数值 + 快速定位"""
    valueChanged = QtCore.pyqtSignal(float)

    def __init__(self, val, min_v=0, max_v=100, unit="%", min_width=240):
        super().__init__()
        self.min_v = min_v
        self.max_v = max_v
        self.unit = unit

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        self.slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.slider.setRange(min_v, max_v)
        self.slider.setValue(int(val))
        self.slider.setMinimumWidth(min_width)
        self.slider.setCursor(QtCore.Qt.PointingHandCursor)
        self.slider.setStyleSheet("""
            QSlider::groove:horizontal {
                height: 6px;
                background: #101218;
                border: 1px solid #2a2f3a;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #2f6fbd;
                border: 1px solid #3d82d6;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                width: 14px;
                height: 14px;
                margin: -5px 0;
                border-radius: 7px;
                background: #e8ecf3;
                border: 2px solid #2f6fbd;
            }
            QSlider::handle:horizontal:hover {
                background: #ffffff;
                border-color: #4a8fdb;
            }
        """)
        self.slider.valueChanged.connect(self._on_change)
        lay.addWidget(self.slider, 1)

        self.lbl = QtWidgets.QLabel(f"{int(val)}{unit}")
        self.lbl.setAlignment(QtCore.Qt.AlignCenter)
        self.lbl.setFixedWidth(56)
        self.lbl.setStyleSheet(
            "background:#101218; border:1px solid #2a2f3a; border-radius:6px;"
            "padding:4px 0; font-weight:600; color:#e8ecf3; font-size:13px;"
        )
        lay.addWidget(self.lbl)

    def _on_change(self, v):
        if self.unit == "%":
            self.lbl.setText(f"{v}{self.unit}")
        else:
            self.lbl.setText(f"{v}{self.unit}")
        self.valueChanged.emit(float(v))

    def setValue(self, v):
        self.slider.blockSignals(True)
        self.slider.setValue(int(v))
        self.slider.blockSignals(False)
        self.lbl.setText(f"{int(v)}{self.unit}")

    def value(self):
        return self.slider.value()


class SessionReportDialog(QtWidgets.QDialog):
    def __init__(self, parent, duration_str, calls, total_tok, prompt_tok,
                 output_tok, cached_tok, hit_rate, top_meme, billing_mode="free"):
        super().__init__(parent)
        self.setWindowTitle("会话结算报告")
        self.resize(440, 340)
        self.setStyleSheet(THEME)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(16)

        title = QtWidgets.QLabel("本次会话结算报告")
        title.setStyleSheet("font-size:17px; font-weight:700; color:#ffffff;")
        lay.addWidget(title)

        card = QtWidgets.QFrame()
        card.setObjectName("card")
        form = QtWidgets.QFormLayout(card)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(11)

        form.addRow(self._k("运行时长"), self._v(duration_str))
        form.addRow(self._k("决策次数"), self._v(f"{calls} 次"))
        form.addRow(self._k("消耗 Tokens"), self._v(f"{total_tok}  (输入 {prompt_tok} / 输出 {output_tok})"))

        if cached_tok > 0:
            form.addRow(self._k("前缀缓存命中"), self._v(f"{cached_tok} ({hit_rate:.1f}%)", "#3fb87d"))
        else:
            form.addRow(self._k("前缀缓存"), self._v("未命中或接口未返回统计", "#6b7484"))

        if billing_mode == "free":
            form.addRow(self._k("计费模式"), self._v("免费 / 自建渠道", "#3fb87d"))
        else:
            cost = (total_tok / 1000.0) * 0.002
            form.addRow(self._k("预估成本"), self._v(f"约 ¥{cost:.4f}", "#e0b341"))

        if top_meme:
            form.addRow(self._k("最高频音效"), self._v(f"《{top_meme}》"))

        lay.addWidget(card)

        btn = QtWidgets.QPushButton("确定")
        btn.setObjectName("btn_primary")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn)

    @staticmethod
    def _k(text):
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet("color:#8892a2; font-size:13px;")
        return lbl

    @staticmethod
    def _v(text, color="#e8ecf3"):
        lbl = QtWidgets.QLabel(text)
        lbl.setStyleSheet(f"color:{color}; font-size:13px; font-weight:600;")
        return lbl


class MemeEditorDialog(QtWidgets.QDialog):
    def __init__(self, parent=None, meme_data=None):
        super().__init__(parent)
        self.meme_data = meme_data
        self.setWindowTitle("编辑音效" if meme_data else "添加音效")
        self.resize(600, 440)
        self.setStyleSheet(THEME)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(14)

        form = QtWidgets.QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(QtCore.Qt.AlignLeft)

        self.edit_title = QtWidgets.QLineEdit()
        self.edit_title.setPlaceholderText("例如：泰裤辣 / 这波你在第五层")
        form.addRow("音效名称", self.edit_title)

        file_row = QtWidgets.QHBoxLayout()
        self.edit_file = QtWidgets.QLineEdit()
        self.edit_file.setPlaceholderText("音频文件路径 (.mp3 / .wav)")
        file_row.addWidget(self.edit_file, 1)
        btn_browse = QtWidgets.QPushButton("浏览")
        btn_browse.clicked.connect(self._browse)
        file_row.addWidget(btn_browse)
        form.addRow("音频文件", file_row)

        self.lbl_path = QtWidgets.QLabel("")
        self.lbl_path.setWordWrap(True)
        self.lbl_path.setStyleSheet("color:#6b7484; font-size:12px;")
        form.addRow("", self.lbl_path)

        self.edit_triggers = QtWidgets.QTextEdit()
        self.edit_triggers.setFixedHeight(90)
        self.edit_triggers.setPlaceholderText(
            "描述触发时机，例如：当队友自我感觉良好、盲目自信吹牛时"
        )
        form.addRow("触发时机", self.edit_triggers)

        self.edit_vibe = QtWidgets.QLineEdit()
        self.edit_vibe.setPlaceholderText("例如：反讽 / 自嘲 / 破防")
        form.addRow("情绪标签", self.edit_vibe)

        lay.addLayout(form)

        if meme_data:
            self.edit_title.setText(meme_data.get("title", ""))
            fn = meme_data.get("filename", "")
            self.edit_file.setText(fn)
            self._update_path(fn)
            self.edit_triggers.setPlainText(meme_data.get("triggers", ""))
            self.edit_vibe.setText(meme_data.get("vibe", ""))

        self.edit_file.textChanged.connect(self._update_path)

        lay.addStretch()

        btns = QtWidgets.QHBoxLayout()
        btns.addStretch()
        btn_cancel = QtWidgets.QPushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btns.addWidget(btn_cancel)

        btn_save = QtWidgets.QPushButton("保存" if meme_data else "添加到梗库")
        btn_save.setObjectName("btn_primary")
        btn_save.clicked.connect(self._save)
        btns.addWidget(btn_save)
        lay.addLayout(btns)

    def _update_path(self, text):
        resolved = resolve_audio_file_path(text) if text.strip() else None
        if resolved and resolved.exists():
            self.lbl_path.setText(f"✓ {resolved}")
            self.lbl_path.setStyleSheet("color:#3fb87d; font-size:12px;")
        elif text.strip():
            self.lbl_path.setText(f"✗ 文件不存在：{resolved}")
            self.lbl_path.setStyleSheet("color:#db5852; font-size:12px;")
        else:
            self.lbl_path.setText("")

    def _browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择音频", str(CURRENT_DIR.parent), "音频文件 (*.mp3 *.wav *.ogg *.aac)"
        )
        if path:
            try:
                rel = Path(path).relative_to(CURRENT_DIR.parent)
                self.edit_file.setText(str(rel).replace("\\", "/"))
            except ValueError:
                self.edit_file.setText(path.replace("\\", "/"))

    def _save(self):
        title = self.edit_title.text().strip()
        filename = self.edit_file.text().strip()
        triggers = self.edit_triggers.toPlainText().strip()
        vibe = self.edit_vibe.text().strip()

        if not title:
            QtWidgets.QMessageBox.warning(self, "提示", "请填写音效名称")
            return
        if not filename:
            QtWidgets.QMessageBox.warning(self, "提示", "请选择音频文件")
            return
        if not triggers:
            QtWidgets.QMessageBox.warning(self, "提示", "请填写触发时机描述")
            return

        self.result_data = {
            "title": title,
            "filename": filename,
            "triggers": triggers,
            "vibe": vibe or "网络玩梗",
        }
        self.accept()


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AutoMeme Studio")
        self.resize(1200, 780)
        self.setMinimumSize(1040, 680)
        self.setStyleSheet(THEME)

        self.config = load_json(CONFIG_PATH)
        self.memes = load_json(MEME_LIB_PATH)

        self.sig = Signals()
        self.sig.log.connect(self._log)
        self.sig.audit.connect(self._render_audit)
        self.sig.mic_level.connect(lambda v: self.bar_mic.setValue(v))
        self.sig.speaker_level.connect(lambda v: self.bar_speaker.setValue(v))

        self.is_running = False
        self._eval_lock = threading.Lock()
        self._frame_lock = threading.Lock()
        self._pending_frame = None
        self._frame_worker_stop = False

        self.session_active = False
        self.session_start = 0.0
        self.session_calls = 0
        self.session_total = 0
        self.session_prompt = 0
        self.session_output = 0
        self.session_cached = 0
        self.session_played: List[str] = []

        self._init_core()
        self._build_ui()
        self._load_settings()
        self._start_cd_timer()
        self._register_hotkey()
        self._start_frame_worker()

    # ---------------- 抽帧分析工作线程 ----------------
    def _start_frame_worker(self):
        """独立工作线程：持续取走最新待分析画面，保证高频抽帧时不堆积请求"""
        threading.Thread(target=self._frame_worker_loop, daemon=True).start()

    def _frame_worker_loop(self):
        check_interval = 0.25  # 冷却期或空闲时的轮询步长
        while not self._frame_worker_stop:
            frame = None
            with self._frame_lock:
                if self._pending_frame is not None:
                    frame = self._pending_frame
                    self._pending_frame = None

            if frame is None:
                time.sleep(check_interval)
                continue

            # 冷却中则丢弃该帧，避免无效请求
            in_cd, _ = self.buffer.is_in_cooldown()
            if in_cd or not self.session_active:
                time.sleep(check_interval)
                continue

            check_interval = 0.02  # 有帧待处理时用更细的轮询
            try:
                self._analyze_frame(frame)
            except Exception as e:
                self.sig.log.emit("warn", f"巡帧分析异常：{e}")

            # 处理完立即回到快速轮询，便于低间隔档位持续工作
            with self._frame_lock:
                if self._pending_frame is None:
                    check_interval = 0.25

    def _analyze_frame(self, b64):
        v = self.config.get("vision_api", {})
        if v.get("vision_source", "main") == "custom":
            summary, _vt, vmodel = self.arbiter.analyze_screen_vision(b64)
            if not summary or summary.startswith("[") or "平淡" in summary or "无激烈" in summary:
                return
            self.sig.log.emit("screen", f"自动巡帧 [{vmodel}]：\"{summary}\"")
            self.buffer.add("画面视觉", summary)
            self._trigger_eval()
        else:
            ctx = self.buffer.get_prompt_context()
            prompt = f"{ctx}\n[自动巡帧] 画面出现动态变化，请判断是否有值得接梗的高光或翻车时刻，平淡请返回 0。"
            decision = self.arbiter.decide(prompt, image_base64=b64)
            self._accumulate(decision)
            self.sig.audit.emit(decision)
            if decision.should_play:
                title = decision.meme.title if decision.meme else "?"
                self.sig.log.emit("hit", f"巡帧命中《{title}》 · {decision.reason}")
                self.player.play(decision.meme.filename)
                self.buffer.mark_played(title)

    # ---------------- 核心模块 ----------------
    def _init_core(self):
        audio_cfg = self.config.get("audio", {})
        voice_dir = (CURRENT_DIR / audio_cfg.get("voice_pack_dir", "../新三国语音包")).resolve()
        self.player = AudioPlayer(
            base_voice_dir=str(voice_dir),
            volume=audio_cfg.get("volume", 0.5),
        )
        beh = self.config.get("behavior", {})
        self.buffer = ContextBuffer(
            window_seconds=beh.get("context_window_seconds", 30),
            max_messages=beh.get("max_context_messages", 6),
            cooldown_seconds=beh.get("cooldown_seconds", 8),
        )
        self.arbiter = MemeArbiter(self.config, self.memes)

        sv_dir = CURRENT_DIR / "models" / "sense-voice"
        vad_path = CURRENT_DIR / "models" / "silero_vad.onnx"
        vosk_path = CURRENT_DIR.parent / "AutoMemeDetector-main" / "models" / "vosk-model-small-cn-0.22"

        self.listener = AudioListener(
            on_speech_recognized=self._on_mic_speech,
            sense_voice_dir=str(sv_dir),
            silero_vad_path=str(vad_path),
            vosk_model_path=str(vosk_path),
            on_level_meter=lambda v: self.sig.mic_level.emit(v),
            gain=audio_cfg.get("mic_gain", 3.5),
        )
        self.system_listener = SystemAudioListener(
            on_speech_recognized=self._on_sys_speech,
            is_self_playing_fn=self.player.is_playing,
            sense_voice_dir=str(sv_dir),
            silero_vad_path=str(vad_path),
            on_level_meter=lambda v: self.sig.speaker_level.emit(v),
            gain=audio_cfg.get("speaker_gain", 1.5),
        )
        self.screen_watcher = ScreenWatcher(
            on_event_detected=self._on_screen_event,
            on_vlm_frame=self._on_auto_frame,
        )

    def _register_hotkey(self):
        if not _KEYBOARD_AVAILABLE:
            logger.info("未安装 keyboard 库，F9 全局快捷键不可用（界面按钮仍可截图）")
            return
        try:
            keyboard.add_hotkey("F9", lambda: QtCore.QMetaObject.invokeMethod(
                self, "snap_and_analyze", QtCore.Qt.QueuedConnection))
            logger.info("F9 全局快捷键已注册")
        except Exception as e:
            logger.warning("注册全局快捷键失败: %s", e)

    # ---------------- 界面 ----------------
    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())
        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self._page_console())
        self.stack.addWidget(self._page_library())
        self.stack.addWidget(self._page_inspect())
        self.stack.addWidget(self._page_settings())
        root.addWidget(self.stack, 1)

    def _build_sidebar(self):
        bar = QtWidgets.QFrame()
        bar.setObjectName("sidebar")
        bar.setFixedWidth(208)

        lay = QtWidgets.QVBoxLayout(bar)
        lay.setContentsMargins(16, 20, 16, 18)
        lay.setSpacing(4)

        brand = QtWidgets.QVBoxLayout()
        brand.setSpacing(3)
        t = QtWidgets.QLabel("AutoMeme Studio")
        t.setObjectName("brand_title")
        s = QtWidgets.QLabel("开黑语音接梗引擎")
        s.setObjectName("brand_sub")
        brand.addWidget(t)
        brand.addWidget(s)
        lay.addLayout(brand)
        lay.addSpacing(22)

        self.nav_buttons = []
        group = QtWidgets.QButtonGroup(self)
        group.setExclusive(True)

        items = [("控制台", 0), ("音效库", 1), ("决策审视", 2), ("设置", 3)]
        for text, idx in items:
            btn = QtWidgets.QPushButton(text)
            btn.setObjectName("nav_btn")
            btn.setCheckable(True)
            btn.setCursor(QtCore.Qt.PointingHandCursor)
            if idx == 0:
                btn.setChecked(True)
            btn.clicked.connect(lambda _, i=idx: self.stack.setCurrentIndex(i))
            group.addButton(btn, idx)
            lay.addWidget(btn)
            self.nav_buttons.append(btn)

        lay.addStretch()

        ver = QtWidgets.QLabel(f"v{__version__}")
        ver.setObjectName("version_label")
        lay.addWidget(ver)
        return bar

    def _page_header(self, title, desc):
        box = QtWidgets.QVBoxLayout()
        box.setSpacing(3)
        t = QtWidgets.QLabel(title)
        t.setObjectName("page_title")
        d = QtWidgets.QLabel(desc)
        d.setObjectName("page_desc")
        box.addWidget(t)
        box.addWidget(d)
        return box

    @staticmethod
    def _card():
        f = QtWidgets.QFrame()
        f.setObjectName("card")
        return f

    @staticmethod
    def _section(text):
        lbl = QtWidgets.QLabel(text)
        lbl.setObjectName("section_title")
        return lbl

    # ---------- 控制台 ----------
    def _page_console(self):
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(16)

        lay.addLayout(self._page_header("控制台", "实时监听麦克风与开黑语音，自动卡点播放音效"))

        # 操作栏
        ctl = self._card()
        row = QtWidgets.QHBoxLayout(ctl)
        row.setContentsMargins(18, 14, 18, 14)
        row.setSpacing(10)

        self.btn_toggle = QtWidgets.QPushButton("开始检测")
        self.btn_toggle.setObjectName("btn_start")
        self.btn_toggle.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_toggle.clicked.connect(self._toggle_running)
        row.addWidget(self.btn_toggle)

        btn_mute = QtWidgets.QPushButton("紧急静音")
        btn_mute.setObjectName("btn_ghost")
        btn_mute.clicked.connect(self._panic_mute)
        row.addWidget(btn_mute)

        btn_snap = QtWidgets.QPushButton("截图研判  F9")
        btn_snap.setObjectName("btn_primary")
        btn_snap.setCursor(QtCore.Qt.PointingHandCursor)
        btn_snap.clicked.connect(self.snap_and_analyze)
        row.addWidget(btn_snap)

        row.addSpacing(8)
        self.lbl_status = QtWidgets.QLabel("待机中")
        self.lbl_status.setStyleSheet("color:#7b8496; font-size:13px;")
        row.addWidget(self.lbl_status)
        row.addStretch()

        self.badge_cd = QtWidgets.QLabel("冷却就绪")
        self.badge_cd.setStyleSheet(
            "background:#101218; border:1px solid #2a2f3a; border-radius:6px;"
            "padding:5px 11px; color:#6cb6ff; font-size:12px; font-weight:600;"
        )
        row.addWidget(self.badge_cd)

        self.badge_tok = QtWidgets.QLabel("0 Tokens")
        self.badge_tok.setStyleSheet(
            "background:#101218; border:1px solid #2a2f3a; border-radius:6px;"
            "padding:5px 11px; color:#b18cf0; font-size:12px; font-weight:600;"
        )
        row.addWidget(self.badge_tok)

        self.chk_top = QtWidgets.QCheckBox("窗口置顶")
        self.chk_top.toggled.connect(self._toggle_top)
        row.addWidget(self.chk_top)

        lay.addWidget(ctl)

        # 音频通道
        dev = self._card()
        dv = QtWidgets.QVBoxLayout(dev)
        dv.setContentsMargins(18, 15, 18, 15)
        dv.setSpacing(13)

        dv.addWidget(self._section("音频输入通道"))

        # 麦克风行
        m_row = QtWidgets.QHBoxLayout()
        m_row.setSpacing(12)
        m_lbl = QtWidgets.QLabel("麦克风")
        m_lbl.setFixedWidth(56)
        m_lbl.setStyleSheet("color:#d8b25a; font-size:13px; font-weight:600;")
        m_row.addWidget(m_lbl)

        self.combo_mic = QtWidgets.QComboBox()
        self.combo_mic.addItem("系统默认麦克风", None)
        for d in AudioListener.get_available_mic_devices():
            self.combo_mic.addItem(d["name"], d["index"])
        self.combo_mic.currentIndexChanged.connect(self._on_mic_device)
        m_row.addWidget(self.combo_mic, 5)

        self.bar_mic = QtWidgets.QProgressBar()
        self.bar_mic.setRange(0, 100)
        self.bar_mic.setTextVisible(False)
        self.bar_mic.setFixedHeight(6)
        self.bar_mic.setStyleSheet("QProgressBar{background:#191d26;border-radius:3px;} QProgressBar::chunk{background:#3fb87d;border-radius:3px;}")
        m_row.addWidget(self.bar_mic, 4)

        g_lbl = QtWidgets.QLabel("增益")
        g_lbl.setStyleSheet("color:#7b8496; font-size:13px;")
        m_row.addWidget(g_lbl)
        self.step_mic_gain = Stepper(3.5, 0.5, 12.0, 0.5, "x")
        self.step_mic_gain.valueChanged.connect(self._on_mic_gain)
        m_row.addWidget(self.step_mic_gain)

        self.chk_mic = QtWidgets.QCheckBox("启用")
        self.chk_mic.setChecked(True)
        self.chk_mic.toggled.connect(self._on_mic_toggle)
        m_row.addWidget(self.chk_mic)
        dv.addLayout(m_row)

        # 扬声器行
        s_row = QtWidgets.QHBoxLayout()
        s_row.setSpacing(12)
        s_lbl = QtWidgets.QLabel("扬声器")
        s_lbl.setFixedWidth(56)
        s_lbl.setStyleSheet("color:#6cb6ff; font-size:13px; font-weight:600;")
        s_row.addWidget(s_lbl)

        self.combo_spk = QtWidgets.QComboBox()
        self.combo_spk.addItem("系统默认输出", None)
        for d in SystemAudioListener.get_available_loopback_devices():
            self.combo_spk.addItem(d["name"], d["index"])
        self.combo_spk.currentIndexChanged.connect(self._on_spk_device)
        s_row.addWidget(self.combo_spk, 5)

        self.bar_speaker = QtWidgets.QProgressBar()
        self.bar_speaker.setRange(0, 100)
        self.bar_speaker.setTextVisible(False)
        self.bar_speaker.setFixedHeight(6)
        self.bar_speaker.setStyleSheet("QProgressBar{background:#191d26;border-radius:3px;} QProgressBar::chunk{background:#4a8fdb;border-radius:3px;}")
        s_row.addWidget(self.bar_speaker, 4)

        g2 = QtWidgets.QLabel("增益")
        g2.setStyleSheet("color:#7b8496; font-size:13px;")
        s_row.addWidget(g2)
        self.step_spk_gain = Stepper(1.5, 0.5, 8.0, 0.5, "x")
        self.step_spk_gain.valueChanged.connect(self._on_spk_gain)
        s_row.addWidget(self.step_spk_gain)

        self.chk_spk = QtWidgets.QCheckBox("启用")
        self.chk_spk.setChecked(True)
        self.chk_spk.toggled.connect(self._on_spk_toggle)
        s_row.addWidget(self.chk_spk)
        dv.addLayout(s_row)

        lay.addWidget(dev)

        # 日志
        log_card = self._card()
        lv = QtWidgets.QVBoxLayout(log_card)
        lv.setContentsMargins(18, 15, 18, 15)
        lv.setSpacing(10)
        lv.addWidget(self._section("实时事件流"))
        self.txt_log = QtWidgets.QTextBrowser()
        lv.addWidget(self.txt_log, 1)
        lay.addWidget(log_card, 1)

        # 测试区
        test = self._card()
        tv = QtWidgets.QVBoxLayout(test)
        tv.setContentsMargins(18, 13, 18, 13)
        tv.setSpacing(10)

        chips = QtWidgets.QHBoxLayout()
        chips.setSpacing(7)
        presets = [
            ("单杀带飞", "看我单杀对面，纯带飞！"),
            ("胡说八道", "你纯粹是在胡说八道！"),
            ("被集火", "对面全冲我来了！救救我！"),
            ("玩家阵亡", "@游戏画面出现阵亡大字"),
            ("破防了", "为什么不救我！我心态崩了！"),
            ("翻盘了", "大龙团赢了！翻盘了！"),
        ]
        for label, text in presets:
            b = QtWidgets.QPushButton(label)
            b.setObjectName("chip_btn")
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.clicked.connect(lambda _, p=text: self._quick_test(p))
            chips.addWidget(b)
        chips.addStretch()
        tv.addLayout(chips)

        inp = QtWidgets.QHBoxLayout()
        inp.setSpacing(8)
        self.edit_test = QtWidgets.QLineEdit()
        self.edit_test.setPlaceholderText("输入一句话进行单次测试（不会写入上下文记忆）")
        self.edit_test.returnPressed.connect(self._send_test)
        inp.addWidget(self.edit_test, 1)
        b1 = QtWidgets.QPushButton("测试")
        b1.setObjectName("btn_primary")
        b1.clicked.connect(self._send_test)
        inp.addWidget(b1)
        b2 = QtWidgets.QPushButton("清屏")
        b2.setObjectName("btn_ghost")
        b2.clicked.connect(self.txt_log.clear)
        inp.addWidget(b2)
        tv.addLayout(inp)

        lay.addWidget(test)
        return page

    # ---------- 音效库 ----------
    def _page_library(self):
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(16)

        lay.addLayout(self._page_header("音效库", "管理所有可播放的音效资产与触发场景描述"))

        bar = self._card()
        br = QtWidgets.QHBoxLayout(bar)
        br.setContentsMargins(18, 13, 18, 13)
        br.setSpacing(10)

        self.edit_search = QtWidgets.QLineEdit()
        self.edit_search.setPlaceholderText("搜索名称、触发场景或文件路径")
        self.edit_search.textChanged.connect(self._refresh_lib)
        br.addWidget(self.edit_search, 1)

        b_add = QtWidgets.QPushButton("添加音效")
        b_add.setObjectName("btn_primary")
        b_add.clicked.connect(self._add_meme)
        br.addWidget(b_add)
        lay.addWidget(bar)

        card = self._card()
        cv = QtWidgets.QVBoxLayout(card)
        cv.setContentsMargins(18, 15, 18, 15)
        cv.setSpacing(11)

        self.lbl_count = self._section("")
        cv.addWidget(self.lbl_count)

        self.list_memes = QtWidgets.QListWidget()
        self.list_memes.itemDoubleClicked.connect(self._preview_meme)
        cv.addWidget(self.list_memes, 1)

        acts = QtWidgets.QHBoxLayout()
        acts.setSpacing(8)
        for text, fn in [
            ("试听", lambda: self._preview_meme(self.list_memes.currentItem())),
            ("打开文件位置", self._reveal_file),
            ("编辑", self._edit_meme),
            ("删除", self._delete_meme),
        ]:
            b = QtWidgets.QPushButton(text)
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.clicked.connect(fn)
            acts.addWidget(b)
        acts.addStretch()
        b_reset = QtWidgets.QPushButton("恢复默认库")
        b_reset.setObjectName("btn_ghost")
        b_reset.clicked.connect(self._reset_memes)
        acts.addWidget(b_reset)
        cv.addLayout(acts)

        lay.addWidget(card, 1)
        self._refresh_lib()
        return page

    # ---------- 决策审视 ----------
    def _page_inspect(self):
        page = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(page)
        lay.setContentsMargins(26, 22, 26, 22)
        lay.setSpacing(16)

        lay.addLayout(self._page_header("决策审视", "查看每次决策的完整推理链路与 Token 消耗明细"))

        top = self._card()
        tv = QtWidgets.QVBoxLayout(top)
        tv.setContentsMargins(18, 15, 18, 15)
        tv.setSpacing(10)

        self.lbl_engine = QtWidgets.QLabel("尚未产生决策")
        self.lbl_engine.setStyleSheet("color:#6cb6ff; font-size:14px; font-weight:600;")
        tv.addWidget(self.lbl_engine)

        self.lbl_tokens = QtWidgets.QLabel("Token 用量：尚无记录")
        self.lbl_tokens.setWordWrap(True)
        self.lbl_tokens.setStyleSheet(
            "color:#b18cf0; font-size:12px; background:#101218;"
            "padding:9px 12px; border-radius:6px; border:1px solid #22262f;"
        )
        tv.addWidget(self.lbl_tokens)
        lay.addWidget(top)

        detail = self._card()
        dv = QtWidgets.QVBoxLayout(detail)
        dv.setContentsMargins(18, 15, 18, 15)
        dv.setSpacing(10)

        dv.addWidget(self._section("推理思考链"))
        self.txt_thinking = QtWidgets.QTextBrowser()
        dv.addWidget(self.txt_thinking, 3)

        dv.addWidget(self._section("输入上下文切片"))
        self.txt_prompt = QtWidgets.QTextBrowser()
        dv.addWidget(self.txt_prompt, 2)

        b = QtWidgets.QPushButton("复制完整日志")
        b.setObjectName("btn_ghost")
        b.clicked.connect(self._copy_log)
        dv.addWidget(b)

        lay.addWidget(detail, 1)
        return page

    # ---------- 设置 ----------
    def _page_settings(self):
        page = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(page)
        outer.setContentsMargins(26, 22, 26, 22)
        outer.setSpacing(16)

        outer.addLayout(self._page_header("设置", "配置决策模型、视觉识别与玩梗节奏"))

        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 10, 0)
        lay.setSpacing(16)

        # ===== 1. 决策大脑 =====
        brain = self._card()
        bv = QtWidgets.QVBoxLayout(brain)
        bv.setContentsMargins(18, 16, 18, 16)
        bv.setSpacing(13)

        bv.addWidget(self._section("决策大脑（负责理解对话并选择音效）"))

        form1 = QtWidgets.QFormLayout()
        form1.setSpacing(11)

        self.combo_mode = QtWidgets.QComboBox()
        self.combo_mode.addItem("自动（有 Key 用大模型，无 Key 用内置规则）", "auto")
        self.combo_mode.addItem("强制使用大模型", "llm")
        self.combo_mode.addItem("仅用内置规则（离线，零延迟）", "heuristic")
        self.combo_mode.currentIndexChanged.connect(self._on_mode_change)
        form1.addRow("决策模式", self.combo_mode)

        self.combo_billing = QtWidgets.QComboBox()
        self.combo_billing.addItem("免费 / 自建渠道（不计费）", "free")
        self.combo_billing.addItem("按官方价格估算（¥2 / 百万 Tokens）", "deepseek")
        self.combo_billing.currentIndexChanged.connect(self._on_billing_change)
        form1.addRow("计费方式", self.combo_billing)

        self.edit_base_url = QtWidgets.QLineEdit()
        form1.addRow("接口地址", self.edit_base_url)

        self.edit_model = QtWidgets.QLineEdit()
        form1.addRow("模型名称", self.edit_model)

        self.edit_key = QtWidgets.QLineEdit()
        self.edit_key.setEchoMode(QtWidgets.QLineEdit.Password)
        self.edit_key.setPlaceholderText("填入 API Key")
        form1.addRow("API Key", self.edit_key)

        bv.addLayout(form1)

        brow = QtWidgets.QHBoxLayout()
        brow.setSpacing(8)
        for text, fn in [
            ("填入 DeepSeek 官方", lambda: self._fill_template("https://api.deepseek.com/v1", "deepseek-chat", "deepseek")),
            ("填入本地 Ollama", lambda: self._fill_template("http://localhost:11434/v1", "qwen2.5:3b", "free")),
            ("还原已保存配置", self._restore_inputs),
        ]:
            b = QtWidgets.QPushButton(text)
            b.setObjectName("btn_ghost")
            b.clicked.connect(fn)
            brow.addWidget(b)
        brow.addStretch()
        bv.addLayout(brow)

        b_save = QtWidgets.QPushButton("保存决策大脑配置")
        b_save.setObjectName("btn_primary")
        b_save.clicked.connect(self._save_brain)
        bv.addWidget(b_save)
        lay.addWidget(brain)

        # ===== 2. Token 优化（紧跟决策大脑，属于同一组输出设置）=====
        optimize = self._card()
        ov = QtWidgets.QVBoxLayout(optimize)
        ov.setContentsMargins(18, 16, 18, 16)
        ov.setSpacing(13)

        ov.addWidget(self._section("候选召回方式"))

        # 详细说明两种引擎的工作方式
        engine_doc = QtWidgets.QLabel(
            "🔍 <b>漏斗如何工作</b>：先由本地从全部音效中筛出几个候选，再只把候选交给大模型裁决，"
            "这样每次请求只需几百字而非几千字。<br><br>"
            "<b>关键词匹配</b>（默认）：靠预先写好的词表做字面比对。<br>"
            "　✓ 零延迟、不依赖任何外部服务<br>"
            "　✗ 词表覆盖不到的说法会漏（说“打得漂亮”匹配不到“打赢”）<br><br>"
            "<b>语义向量</b>：把说的话和音效描述都转成向量，按意思相近度召回。<br>"
            "　✓ 能识别同义不同词，覆盖更广<br>"
            "　✗ 需要额外配置 embedding 服务（如 Ollama 或官方 API）<br>"
            "　✗ 每次召回多一次网络请求"
        )
        engine_doc.setWordWrap(True)
        engine_doc.setTextFormat(QtCore.Qt.RichText)
        engine_doc.setStyleSheet("color:#8892a2; font-size:12px; line-height:150%;")
        ov.addWidget(engine_doc)

        eng_row = QtWidgets.QHBoxLayout()
        eng_row.setSpacing(10)
        eng_row.addWidget(QtWidgets.QLabel("召回引擎"))
        self.combo_funnel_engine = QtWidgets.QComboBox()
        self.combo_funnel_engine.addItem("关键词匹配（默认，无需额外配置）", "keyword")
        self.combo_funnel_engine.addItem("语义向量（需配置 embedding 服务）", "semantic")
        self.combo_funnel_engine.currentIndexChanged.connect(self._on_funnel_engine)
        eng_row.addWidget(self.combo_funnel_engine, 1)

        self.btn_test_embed = QtWidgets.QPushButton("测试 embedding")
        self.btn_test_embed.setObjectName("btn_ghost")
        self.btn_test_embed.setCursor(QtCore.Qt.PointingHandCursor)
        self.btn_test_embed.clicked.connect(self._test_embedding)
        eng_row.addWidget(self.btn_test_embed)
        ov.addLayout(eng_row)

        # embedding 配置（仅在语义模式下显示）
        self.frame_embed = QtWidgets.QFrame()
        self.frame_embed.setObjectName("card_flat")
        ef = QtWidgets.QFormLayout(self.frame_embed)
        ef.setContentsMargins(14, 12, 14, 12)
        ef.setSpacing(10)

        self.edit_embed_url = QtWidgets.QLineEdit("http://localhost:11434/v1")
        ef.addRow("embedding 地址", self.edit_embed_url)

        self.edit_embed_model = QtWidgets.QLineEdit("nomic-embed-text")
        ef.addRow("embedding 模型", self.edit_embed_model)

        self.edit_embed_key = QtWidgets.QLineEdit("ollama")
        self.edit_embed_key.setEchoMode(QtWidgets.QLineEdit.Password)
        ef.addRow("embedding Key", self.edit_embed_key)

        embed_hint = QtWidgets.QLabel(
            "本地 Ollama 示例：先执行 <code>ollama pull nomic-embed-text</code>，"
            "地址填 <code>http://localhost:11434/v1</code>"
        )
        embed_hint.setWordWrap(True)
        embed_hint.setTextFormat(QtCore.Qt.RichText)
        embed_hint.setStyleSheet("color:#6b7484; font-size:12px;")
        ef.addRow("", embed_hint)

        self.btn_save_embed = QtWidgets.QPushButton("保存 embedding 配置")
        self.btn_save_embed.setObjectName("btn_primary")
        self.btn_save_embed.clicked.connect(self._save_embedding)
        ef.addRow(self.btn_save_embed)

        ov.addWidget(self.frame_embed)

        self.chk_funnel = QtWidgets.QCheckBox("启用两段式漏斗（本地粗筛候选，大幅减少每次请求的 Token）")
        self.chk_funnel.toggled.connect(self._on_funnel)
        ov.addWidget(self.chk_funnel)

        # 触发灵敏度：三段式选择，控制"话痨程度"
        sens_row = QtWidgets.QHBoxLayout()
        sens_row.setSpacing(10)
        sens_row.addWidget(QtWidgets.QLabel("触发灵敏度"))
        self.combo_sensitivity = QtWidgets.QComboBox()
        self.combo_sensitivity.addItem("保守（宁缺毋滥，省 Token）", 25)
        self.combo_sensitivity.addItem("均衡（该出手就出手）", 50)
        self.combo_sensitivity.addItem("激进（话痨，气氛拉满）", 80)
        self.combo_sensitivity.currentIndexChanged.connect(self._on_sensitivity_combo)
        sens_row.addWidget(self.combo_sensitivity, 1)
        ov.addLayout(sens_row)

        fk_row = QtWidgets.QHBoxLayout()
        fk_row.setSpacing(10)
        fk_row.addWidget(QtWidgets.QLabel("候选数量上限"))
        self.step_topk = Stepper(5, 2, 12, 1, " 个", is_int=True)
        self.step_topk.valueChanged.connect(self._on_topk)
        fk_row.addWidget(self.step_topk)
        fk_hint = QtWidgets.QLabel("候选越多判断越准，Token 消耗越高")
        fk_hint.setStyleSheet("color:#6b7484; font-size:12px;")
        fk_row.addWidget(fk_hint)
        fk_row.addStretch()
        ov.addLayout(fk_row)

        self.chk_thinking = QtWidgets.QCheckBox("让大模型返回详细推理过程（便于调试，但输出 Token 会显著增加）")
        self.chk_thinking.toggled.connect(self._on_thinking)
        ov.addWidget(self.chk_thinking)

        # 截图分辨率：图片 Token 是带图决策的主要开销
        img_row = QtWidgets.QHBoxLayout()
        img_row.setSpacing(10)
        img_row.addWidget(QtWidgets.QLabel("截图分辨率"))
        self.combo_img_size = QtWidgets.QComboBox()
        self.combo_img_size.addItem("256（最省，约 85 tokens）", 256)
        self.combo_img_size.addItem("384（推荐，约 258 tokens）", 384)
        self.combo_img_size.addItem("512（更清晰，约 450 tokens）", 512)
        self.combo_img_size.addItem("768（最清晰，约 900 tokens）", 768)
        self.combo_img_size.currentIndexChanged.connect(self._on_img_size)
        img_row.addWidget(self.combo_img_size)
        img_hint = QtWidgets.QLabel("直接决定带图决策的图片开销")
        img_hint.setStyleSheet("color:#6b7484; font-size:12px;")
        img_row.addWidget(img_hint)
        img_row.addStretch()
        ov.addLayout(img_row)

        lay.addWidget(optimize)

        # ===== 3. 视觉识别 =====
        vision = self._card()
        vv = QtWidgets.QVBoxLayout(vision)
        vv.setContentsMargins(18, 16, 18, 16)
        vv.setSpacing(13)

        vv.addWidget(self._section("视觉识别（负责观察游戏画面，判断战局）"))

        hint = QtWidgets.QLabel("选择由谁来看图：可直接复用上面的决策大脑渠道，也可以单独配置一个更便宜的视觉模型。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#7b8496; font-size:12px;")
        vv.addWidget(hint)

        self.radio_vision_main = QtWidgets.QRadioButton("复用决策大脑渠道（零额外配置）")
        self.radio_vision_custom = QtWidgets.QRadioButton("使用独立视觉模型（可节省主模型用量）")
        self.radio_vision_main.setChecked(True)
        self.radio_vision_main.toggled.connect(self._on_vision_source)
        vv.addWidget(self.radio_vision_main)
        vv.addWidget(self.radio_vision_custom)

        # 独立视觉配置容器
        self.vision_custom_box = QtWidgets.QFrame()
        self.vision_custom_box.setObjectName("card_flat")
        cvf = QtWidgets.QFormLayout(self.vision_custom_box)
        cvf.setContentsMargins(14, 14, 14, 14)
        cvf.setSpacing(11)

        self.edit_v_url = QtWidgets.QLineEdit("http://localhost:11434/v1")
        cvf.addRow("视觉接口地址", self.edit_v_url)

        self.edit_v_model = QtWidgets.QLineEdit("moondream")
        cvf.addRow("视觉模型名称", self.edit_v_model)

        self.edit_v_key = QtWidgets.QLineEdit("ollama")
        self.edit_v_key.setEchoMode(QtWidgets.QLineEdit.Password)
        cvf.addRow("视觉 API Key", self.edit_v_key)

        # 免费视觉渠道快速填入
        vfill_row = QtWidgets.QHBoxLayout()
        vfill_row.setSpacing(6)
        lbl_vfill = QtWidgets.QLabel("快速填入:")
        lbl_vfill.setStyleSheet("font-size:11px; color:#94a3b8;")
        vfill_row.addWidget(lbl_vfill)

        b_glm = QtWidgets.QPushButton("GLM-4V-Flash (免费)")
        b_glm.setObjectName("chip_btn")
        b_glm.setCursor(QtCore.Qt.PointingHandCursor)
        b_glm.setToolTip("智谱免费视觉模型，注册即送 API Key")
        b_glm.clicked.connect(lambda: (
            self.edit_v_url.setText("https://open.bigmodel.cn/api/paas/v4"),
            self.edit_v_model.setText("glm-4v-flash"),
            self.edit_v_key.setText(""),
        ))
        vfill_row.addWidget(b_glm)

        b_ollama_v = QtWidgets.QPushButton("本地 Ollama")
        b_ollama_v.setObjectName("chip_btn")
        b_ollama_v.setCursor(QtCore.Qt.PointingHandCursor)
        b_ollama_v.clicked.connect(lambda: (
            self.edit_v_url.setText("http://localhost:11434/v1"),
            self.edit_v_model.setText("moondream"),
            self.edit_v_key.setText("ollama"),
        ))
        vfill_row.addWidget(b_ollama_v)
        vfill_row.addStretch()
        cvf.addRow("", vfill_row)

        vv.addWidget(self.vision_custom_box)

        vrow = QtWidgets.QHBoxLayout()
        vrow.setSpacing(8)
        b_test_v = QtWidgets.QPushButton("测试视觉连通性")
        b_test_v.setObjectName("btn_ghost")
        b_test_v.clicked.connect(self._test_vision)
        vrow.addWidget(b_test_v)
        b_save_v = QtWidgets.QPushButton("保存视觉配置")
        b_save_v.setObjectName("btn_primary")
        b_save_v.clicked.connect(self._save_vision)
        vrow.addWidget(b_save_v)
        vrow.addStretch()
        vv.addLayout(vrow)

        divider = QtWidgets.QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet("background:#22262f;")
        vv.addWidget(divider)

        self.chk_auto_vlm = QtWidgets.QCheckBox("开启后台自动巡帧（画面有变化时才发送，静止时不消耗）")
        self.chk_auto_vlm.toggled.connect(self._on_auto_vlm)
        vv.addWidget(self.chk_auto_vlm)

        iv = QtWidgets.QHBoxLayout()
        iv.setSpacing(10)
        iv.addWidget(QtWidgets.QLabel("巡帧间隔"))
        self.step_interval = Stepper(6.0, 0.1, 30.0, 0.1, " 秒")
        self.step_interval.valueChanged.connect(self._on_interval)
        iv.addWidget(self.step_interval)

        # 巡帧频次快捷档位
        for label, secs in [("0.1s", 0.1), ("0.5s", 0.5), ("1s", 1.0), ("3s", 3.0), ("6s", 6.0), ("10s", 10.0)]:
            b = QtWidgets.QPushButton(label)
            b.setObjectName("chip_btn")
            b.setCursor(QtCore.Qt.PointingHandCursor)
            b.clicked.connect(lambda _, s=secs: self._apply_interval(s))
            iv.addWidget(b)
        iv.addStretch()
        vv.addLayout(iv)

        hint2 = QtWidgets.QLabel("提示：间隔越短越灵敏，但请求量成倍增加。若使用免费/自建渠道可不心疼，官方计费渠道建议 ≥3 秒。")
        hint2.setWordWrap(True)
        hint2.setStyleSheet("color:#6b7484; font-size:12px;")
        vv.addWidget(hint2)

        self.chk_ocr = QtWidgets.QCheckBox("开启屏幕文字识别（本地离线，识别「阵亡 / 失败 / 胜利」等大字）")
        self.chk_ocr.toggled.connect(self._on_ocr)
        vv.addWidget(self.chk_ocr)

        lay.addWidget(vision)

        # ===== 4. 节奏与音频 =====
        rhythm = self._card()
        rv = QtWidgets.QVBoxLayout(rhythm)
        rv.setContentsMargins(18, 16, 18, 16)
        rv.setSpacing(13)

        rv.addWidget(self._section("节奏与音频"))

        rf = QtWidgets.QFormLayout()
        rf.setSpacing(11)

        self.step_cd = Stepper(8, 3, 60, 1, " 秒", is_int=True)
        self.step_cd.valueChanged.connect(self._on_cd)
        rf.addRow("防连播冷却", self.step_cd)

        self.slider_vol = SliderRow(50, 0, 100, "%", min_width=260)
        self.slider_vol.valueChanged.connect(self._on_vol)
        rf.addRow("音效音量", self.slider_vol)
        rv.addLayout(rf)

        self.chk_settlement = QtWidgets.QCheckBox("停止检测时弹出会话结算报告")
        self.chk_settlement.toggled.connect(self._on_settlement)
        rv.addWidget(self.chk_settlement)

        self.chk_emotion = QtWidgets.QCheckBox("启用声音情绪识别（实验性，可能不稳定）")
        self.chk_emotion.toggled.connect(self._on_emotion)
        rv.addWidget(self.chk_emotion)

        lay.addWidget(rhythm)
        lay.addStretch()

        scroll.setWidget(inner)
        outer.addWidget(scroll, 1)
        return page

    # ---------------- 设置加载与应用 ----------------
    def _load_settings(self):
        # 加载期间加锁，防止控件 setValue/setChecked 触发的回调把默认值写回配置文件
        self._loading_settings = True
        try:
            self._do_load_settings()
        finally:
            self._loading_settings = False

    def _do_load_settings(self):
        api = self.config.get("api", {})
        self.edit_base_url.setText(api.get("base_url", ""))
        self.edit_model.setText(api.get("model", ""))
        self.edit_key.setText(api.get("api_key", ""))

        i = self.combo_mode.findData(api.get("mode", "auto"))
        if i >= 0:
            self.combo_mode.setCurrentIndex(i)
        i = self.combo_billing.findData(api.get("billing_mode", "free"))
        if i >= 0:
            self.combo_billing.setCurrentIndex(i)

        v = self.config.get("vision_api", {})
        src = v.get("vision_source", "main")
        if src == "custom":
            self.radio_vision_custom.setChecked(True)
        else:
            self.radio_vision_main.setChecked(True)
        self.edit_v_url.setText(v.get("base_url", "http://localhost:11434/v1"))
        self.edit_v_model.setText(v.get("model", "moondream"))
        self.edit_v_key.setText(v.get("api_key", "ollama"))
        self._on_vision_source()

        self.chk_auto_vlm.setChecked(v.get("enable_auto_sampling", False))
        self.screen_watcher.is_vlm_auto_enabled = v.get("enable_auto_sampling", False)
        self.step_interval.setValue(v.get("sampling_interval", 6))
        self.screen_watcher.vlm_sample_interval = float(v.get("sampling_interval", 6))

        ocr = self.config.get("screen", {}).get("enable_screen_ocr", False)
        self.chk_ocr.setChecked(ocr)
        self.screen_watcher.is_ocr_enabled = ocr

        a = self.config.get("audio", {})
        self.slider_vol.setValue(int(a.get("volume", 0.5) * 100))
        self.player.set_volume(a.get("volume", 0.5))
        self.step_mic_gain.setValue(a.get("mic_gain", 3.5))
        self.listener.set_gain(a.get("mic_gain", 3.5))
        self.step_spk_gain.setValue(a.get("speaker_gain", 1.5))
        self.system_listener.set_gain(a.get("speaker_gain", 1.5))

        mic_on = a.get("enable_mic", True)
        self.chk_mic.setChecked(mic_on)
        self.listener.is_enabled = mic_on
        spk_on = a.get("enable_speaker", True)
        self.chk_spk.setChecked(spk_on)
        self.system_listener.is_enabled = spk_on
        emo = a.get("enable_emotion", False)
        self.chk_emotion.setChecked(emo)
        self.listener.enable_emotion = emo
        self.system_listener.enable_emotion = emo

        name = a.get("mic_device_name", "")
        if name:
            i = self._find_combo_index(self.combo_mic, name)
            if i >= 0:
                self.combo_mic.setCurrentIndex(i)
                self.listener.device_index = self.combo_mic.itemData(i)
        name = a.get("speaker_device_name", "")
        if name:
            i = self._find_combo_index(self.combo_spk, name)
            if i >= 0:
                self.combo_spk.setCurrentIndex(i)
                self.system_listener.device_index = self.combo_spk.itemData(i)

        b = self.config.get("behavior", {})
        self.step_cd.setValue(b.get("cooldown_seconds", 8))
        self.buffer.cooldown_seconds = b.get("cooldown_seconds", 8)
        self.chk_settlement.setChecked(b.get("show_session_settlement", True))

        # Token 优化项
        self.chk_funnel.setChecked(b.get("use_funnel", True))
        self.step_topk.setValue(b.get("funnel_top_k", 5))
        self.chk_thinking.setChecked(b.get("enable_thinking", False))
        sens = int(b.get("trigger_sensitivity", 50))
        # 根据保存的数值映射到最近的三档
        idx_s = 0 if sens <= 33 else (2 if sens >= 67 else 1)
        self.combo_sensitivity.setCurrentIndex(idx_s)

        # 召回引擎
        engine = b.get("funnel_engine", "keyword")
        idx_e = self.combo_funnel_engine.findData(engine)
        if idx_e >= 0:
            self.combo_funnel_engine.setCurrentIndex(idx_e)
        self.frame_embed.setVisible(engine == "semantic")

        # embedding 配置
        emb = self.config.get("embedding", {})
        self.edit_embed_url.setText(emb.get("base_url", "http://localhost:11434/v1"))
        self.edit_embed_model.setText(emb.get("model", "nomic-embed-text"))
        self.edit_embed_key.setText(emb.get("api_key", "ollama"))

        # 截图分辨率
        img_size = v.get("image_size", 512)
        idx_is = self.combo_img_size.findData(img_size)
        if idx_is >= 0:
            self.combo_img_size.setCurrentIndex(idx_is)
        self.screen_watcher.image_size = int(img_size)
        self.screen_watcher.image_quality = int(v.get("image_quality", 65))

        # 把缺省键补齐到内存配置（不触发回写），后续保存时自然带上
        b.setdefault("use_funnel", True)
        b.setdefault("funnel_top_k", 5)
        b.setdefault("enable_thinking", False)
        b.setdefault("trigger_sensitivity", 50)
        b.setdefault("funnel_engine", "keyword")

        self._log("sys", "配置已加载")

    def _sync_config(self):
        # 加载配置期间禁止回写，避免程序初始化时把默认值覆盖掉用户已保存的设置
        if getattr(self, "_loading_settings", False):
            return
        save_json(CONFIG_PATH, self.config)

    @staticmethod
    def _find_combo_index(combo, saved_name: str) -> int:
        """宽容匹配已保存的设备名（兼容旧版带 [Loopback] 后缀或名称不全的情况）"""
        if not saved_name:
            return -1
        i = combo.findText(saved_name)
        if i >= 0:
            return i

        norm = saved_name.replace("[Loopback]", "").strip()
        for idx in range(combo.count()):
            text = combo.itemText(idx)
            if text == norm or norm in text or text in norm:
                return idx
        return -1

    def _fill_template(self, url, model, billing):
        self.edit_base_url.setText(url)
        self.edit_model.setText(model)
        i = self.combo_billing.findData(billing)
        if i >= 0:
            self.combo_billing.setCurrentIndex(i)
        self._log("sys", f"已填入模板：{model}。确认后请点击保存。")

    def _restore_inputs(self):
        api = self.config.get("api", {})
        self.edit_base_url.setText(api.get("base_url", ""))
        self.edit_model.setText(api.get("model", ""))
        self.edit_key.setText(api.get("api_key", ""))
        self._log("sys", "已还原为当前保存的配置")

    def _save_brain(self):
        self.config.setdefault("api", {}).update({
            "base_url": self.edit_base_url.text().strip(),
            "model": self.edit_model.text().strip(),
            "api_key": self.edit_key.text().strip(),
            "billing_mode": self.combo_billing.currentData(),
            "mode": self.combo_mode.currentData(),
        })
        self._sync_config()
        self.arbiter = MemeArbiter(self.config, self.memes)
        self._log("sys", "决策大脑配置已保存")

    def _on_vision_source(self):
        use_custom = self.radio_vision_custom.isChecked()
        self.vision_custom_box.setVisible(use_custom)
        self.config.setdefault("vision_api", {})["vision_source"] = "custom" if use_custom else "main"
        self._sync_config()
        self.arbiter = MemeArbiter(self.config, self.memes)

    def _save_vision(self):
        v = self.config.setdefault("vision_api", {})
        v.update({
            "vision_source": "custom" if self.radio_vision_custom.isChecked() else "main",
            "base_url": self.edit_v_url.text().strip(),
            "model": self.edit_v_model.text().strip(),
            "api_key": self.edit_v_key.text().strip(),
        })
        self._sync_config()
        self.arbiter = MemeArbiter(self.config, self.memes)
        self._log("sys", f"视觉配置已保存：{v['model']}")

    def _test_vision(self):
        self._log("sys", "正在测试视觉模型连通性…")
        threading.Thread(target=self._do_test_vision, daemon=True).start()

    def _do_test_vision(self):
        b64 = capture_screen_for_vision(self.config.get("vision_api", {}))
        if not b64:
            self.sig.log.emit("warn", "截图失败，无法测试")
            return
        summary, tokens, model = self.arbiter.analyze_screen_vision(b64)
        if summary.startswith("["):
            self.sig.log.emit("warn", f"视觉模型测试失败：{summary}")
        else:
            self.sig.log.emit("screen", f"视觉模型 [{model}] 返回：\"{summary}\"（{tokens} Tokens）")

    def _on_auto_vlm(self, checked):
        self.screen_watcher.is_vlm_auto_enabled = checked
        self.config.setdefault("vision_api", {})["enable_auto_sampling"] = checked
        self._sync_config()
        self._log("sys", f"后台自动巡帧已{'开启' if checked else '关闭'}")

    def _apply_interval(self, secs):
        """快捷档位：同步步进器显示并立即生效"""
        self.step_interval.setValue(secs)
        self._on_interval(secs)

    def _on_interval(self, v):
        v = float(v)
        self.screen_watcher.vlm_sample_interval = v
        self.config.setdefault("vision_api", {})["sampling_interval"] = round(v, 2)
        self._sync_config()

    def _on_ocr(self, checked):
        self.screen_watcher.is_ocr_enabled = checked
        self.config.setdefault("screen", {})["enable_screen_ocr"] = checked
        self._sync_config()
        self._log("sys", f"屏幕文字识别已{'开启' if checked else '关闭'}")

    def _on_funnel(self, checked):
        self.config.setdefault("behavior", {})["use_funnel"] = checked
        self._sync_config()
        self._log("sys", f"两段式漏斗已{'开启（本地粗筛候选）' if checked else '关闭（每次发送全部音效）'}")

    def _on_topk(self, v):
        self.config.setdefault("behavior", {})["funnel_top_k"] = int(v)
        self._sync_config()

    def _on_funnel_engine(self, _idx):
        engine = self.combo_funnel_engine.currentData()
        self.config.setdefault("behavior", {})["funnel_engine"] = engine
        self.frame_embed.setVisible(engine == "semantic")
        self._sync_config()
        # 切换引擎后重建 arbiter，让新引擎立即生效
        try:
            self.arbiter = MemeArbiter(self.config, self.memes)
        except Exception as e:
            self._log("warn", f"切换召回引擎失败：{e}")
            return
        if engine == "semantic":
            self._log("sys", "召回引擎已切换为【语义向量】——需确保 embedding 服务可用，否则会自动回退关键词匹配")
        else:
            self._log("sys", "召回引擎已切换为【关键词匹配】（零延迟、无需外部服务）")

    def _save_embedding(self):
        e = self.config.setdefault("embedding", {})
        e["enabled"] = True
        e["base_url"] = self.edit_embed_url.text().strip()
        e["model"] = self.edit_embed_model.text().strip()
        e["api_key"] = self.edit_embed_key.text().strip()
        # 默认关闭本地哈希兜底：它无法识别同义词，效果不如关键词匹配
        e.setdefault("local_fallback", False)
        e.setdefault("min_score", 0.15)
        self._sync_config()
        self.arbiter = MemeArbiter(self.config, self.memes)
        self._log("sys", "embedding 配置已保存并重建索引")

    def _test_embedding(self):
        """测试 embedding 服务连通性"""
        self._log("sys", "正在测试 embedding 服务…")
        threading.Thread(target=self._do_test_embedding, daemon=True).start()

    def _do_test_embedding(self):
        try:
            from core.semantic_funnel import EmbeddingClient
            cfg = dict(self.config)
            cfg["embedding"] = {
                "enabled": True,
                "base_url": self.edit_embed_url.text().strip(),
                "model": self.edit_embed_model.text().strip(),
                "api_key": self.edit_embed_key.text().strip(),
                "timeout": 8.0,
            }
            client = EmbeddingClient(cfg)
            ok, msg = client.probe()
            if ok:
                self.sig.log.emit("hit", f"embedding 服务{msg}")
            else:
                self.sig.log.emit("warn", f"embedding 不可用：{msg}")
        except Exception as e:
            self.sig.log.emit("warn", f"embedding 测试异常：{e}")

    def _on_sensitivity_combo(self, _idx):
        val = int(self.combo_sensitivity.currentData())
        self.config.setdefault("behavior", {})["trigger_sensitivity"] = val
        self._sync_config()
        self._log("sys", f"触发灵敏度已设为：{self.combo_sensitivity.currentText()}")

    def _on_thinking(self, checked):
        self.config.setdefault("behavior", {})["enable_thinking"] = checked
        self._sync_config()
        self._log("sys", f"推理过程输出已{'开启（Token 消耗增加）' if checked else '关闭'}")

    def _on_img_size(self, _idx):
        size = self.combo_img_size.currentData()
        self.config.setdefault("vision_api", {})["image_size"] = int(size)
        self.screen_watcher.image_size = int(size)
        self._sync_config()
        self._log("sys", f"截图分辨率已设为 {size}px")

    def _on_cd(self, v):
        self.buffer.cooldown_seconds = int(v)
        self.config.setdefault("behavior", {})["cooldown_seconds"] = int(v)
        self._sync_config()

    def _on_vol(self, v):
        self.player.set_volume(v / 100.0)
        self.config.setdefault("audio", {})["volume"] = v / 100.0
        self._sync_config()

    def _on_mic_gain(self, v):
        self.listener.set_gain(v)
        self.config.setdefault("audio", {})["mic_gain"] = float(v)
        self._sync_config()

    def _on_spk_gain(self, v):
        self.system_listener.set_gain(v)
        self.config.setdefault("audio", {})["speaker_gain"] = float(v)
        self._sync_config()

    def _on_mic_toggle(self, checked):
        self.listener.is_enabled = checked
        self.config.setdefault("audio", {})["enable_mic"] = checked
        self._sync_config()

    def _on_spk_toggle(self, checked):
        self.system_listener.is_enabled = checked
        self.config.setdefault("audio", {})["enable_speaker"] = checked
        self._sync_config()

    def _on_emotion(self, checked):
        self.listener.enable_emotion = checked
        self.system_listener.enable_emotion = checked
        self.config.setdefault("audio", {})["enable_emotion"] = checked
        self._sync_config()

    def _on_settlement(self, checked):
        self.config.setdefault("behavior", {})["show_session_settlement"] = checked
        self._sync_config()

    def _on_mode_change(self, _):
        mode = self.combo_mode.currentData()
        self.arbiter.mode = mode
        self.config.setdefault("api", {})["mode"] = mode
        self._sync_config()

    def _on_billing_change(self, _):
        self.config.setdefault("api", {})["billing_mode"] = self.combo_billing.currentData()
        self._sync_config()

    def _on_mic_device(self, _):
        idx = self.combo_mic.currentData()
        self.listener.switch_device(idx)
        self.config.setdefault("audio", {})["mic_device_name"] = self.combo_mic.currentText()
        self._sync_config()
        self._log("sys", f"麦克风已切换：{self.combo_mic.currentText()}")

    def _on_spk_device(self, _):
        idx = self.combo_spk.currentData()
        self.system_listener.switch_device(idx)
        self.config.setdefault("audio", {})["speaker_device_name"] = self.combo_spk.currentText()
        self._sync_config()
        self._log("sys", f"扬声器已切换：{self.combo_spk.currentText()}")

    # ---------------- 音效库 ----------------
    def _refresh_lib(self, query=""):
        self.list_memes.clear()
        q = query.strip().lower()
        for m in self.memes:
            if q and q not in m.get("title", "").lower() \
                    and q not in m.get("triggers", "").lower() \
                    and q not in m.get("filename", "").lower():
                continue
            item = QtWidgets.QListWidgetItem()
            item.setText(
                f"[{m['id']}]  {m['title']}   ·   {m.get('vibe', '')}\n"
                f"      {m.get('filename', '')}\n"
                f"      {m.get('triggers', '')}"
            )
            item.setToolTip(f"{m.get('filename', '')}\n\n{m.get('triggers', '')}")
            item.setData(QtCore.Qt.UserRole, m)
            self.list_memes.addItem(item)
        self.lbl_count.setText(f"共 {len(self.memes)} 个音效 · 双击可试听")

    def _preview_meme(self, item):
        if not item:
            return
        m = self._real_meme_from_item(item)
        self._log("play", f"试听：{m['title']}")
        self.player.play(m["filename"])

    def _real_meme_from_item(self, item):
        """从列表项拿到 self.memes 里的真实 dict（item.data 返回的是深拷贝，需按 id 回查）"""
        copied = item.data(QtCore.Qt.UserRole)
        if copied is None:
            return None
        return next((x for x in self.memes if x["id"] == copied["id"]), None)

    def _reveal_file(self):
        item = self.list_memes.currentItem()
        if not item:
            QtWidgets.QMessageBox.information(self, "提示", "请先选中一个音效")
            return
        m = self._real_meme_from_item(item)
        if m is None:
            return
        p = resolve_audio_file_path(m.get("filename", ""))
        if p.exists():
            subprocess.Popen(["explorer", "/select,", str(p)])
        else:
            QtWidgets.QMessageBox.warning(self, "未找到文件", str(p))

    def _add_meme(self):
        dlg = MemeEditorDialog(self)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            self.memes.append(dlg.result_data)
            self._reindex_memes()
            self._log("sys", f"已添加音效：{dlg.result_data['title']}")

    def _edit_meme(self):
        item = self.list_memes.currentItem()
        if not item:
            QtWidgets.QMessageBox.information(self, "提示", "请先选中一个音效")
            return
        # 注意：item.data(UserRole) 返回的是 dict 的深拷贝，不是 self.memes 里的原对象。
        # 必须通过 id 在 self.memes 中找到真正的 dict，否则 m.update() 只改了副本，保存无效。
        m = self._real_meme_from_item(item)
        if m is None:
            QtWidgets.QMessageBox.warning(self, "错误", "未找到该音效，可能已被删除")
            return
        dlg = MemeEditorDialog(self, meme_data=m)
        if dlg.exec_() == QtWidgets.QDialog.Accepted:
            m.update(dlg.result_data)
            self._reindex_memes()
            self._log("sys", f"已更新音效：{m['title']}")

    def _delete_meme(self):
        item = self.list_memes.currentItem()
        if not item:
            return
        m = self._real_meme_from_item(item)
        if m is None:
            return
        r = QtWidgets.QMessageBox.question(
            self, "确认删除", f"确定要删除《{m['title']}》吗？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if r == QtWidgets.QMessageBox.Yes:
            self.memes = [x for x in self.memes if x["id"] != m["id"]]
            self._reindex_memes()
            self._log("sys", f"已删除音效：{m['title']}")

    def _reset_memes(self):
        r = QtWidgets.QMessageBox.question(
            self, "确认恢复", "确定要恢复为默认音效库吗？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        if r == QtWidgets.QMessageBox.Yes:
            self.memes = load_json(MEME_LIB_PATH)
            self._reindex_memes()
            self._log("sys", "已恢复默认音效库")

    def _reindex_memes(self):
        for i, m in enumerate(self.memes, 1):
            m["id"] = i
        save_json(MEME_LIB_PATH, self.memes)
        self.arbiter.reload_memes(self.memes)
        self._refresh_lib(self.edit_search.text())

    # ---------------- 运行控制 ----------------
    def _toggle_running(self):
        if not self.is_running:
            self.is_running = True
            self.session_active = True
            self.session_start = time.time()
            self.session_calls = 0
            self.session_total = 0
            self.session_prompt = 0
            self.session_output = 0
            self.session_cached = 0
            self.session_played.clear()

            self.btn_toggle.setText("停止检测")
            self.btn_toggle.setObjectName("btn_stop")
            self.btn_toggle.setStyle(self.btn_toggle.style())
            self.lbl_status.setText("运行中 · 正在监听")
            self.lbl_status.setStyleSheet("color:#3fb87d; font-size:13px; font-weight:600;")
            self.badge_tok.setText("0 Tokens")

            if self.chk_mic.isChecked():
                self.listener.start()
            if self.chk_spk.isChecked():
                self.system_listener.is_enabled = True
                self.system_listener.start()
            self.screen_watcher.start()
            self._log("sys", "已开始检测")
        else:
            self.is_running = False
            self.btn_toggle.setText("开始检测")
            self.btn_toggle.setObjectName("btn_start")
            self.btn_toggle.setStyle(self.btn_toggle.style())
            self.lbl_status.setText("待机中")
            self.lbl_status.setStyleSheet("color:#7b8496; font-size:13px;")

            self.listener.stop()
            self.system_listener.stop()
            self.screen_watcher.stop()
            self._log("sys", "已停止检测")

            if self.session_active and self.chk_settlement.isChecked():
                self._show_settlement()
            self.session_active = False

    def _show_settlement(self):
        secs = int(time.time() - self.session_start)
        m, s = divmod(secs, 60)
        duration = f"{m} 分 {s} 秒" if m else f"{s} 秒"
        hit = (self.session_cached / self.session_prompt * 100.0) if self.session_prompt > 0 else 0.0
        top = ""
        if self.session_played:
            top = collections.Counter(self.session_played).most_common(1)[0][0]
        dlg = SessionReportDialog(
            self, duration, self.session_calls, self.session_total,
            self.session_prompt, self.session_output, self.session_cached,
            hit, top, self.config.get("api", {}).get("billing_mode", "free"))
        dlg.exec_()

    def _panic_mute(self):
        self.player.stop()
        self._log("sys", "已紧急静音")

    def _toggle_top(self, checked):
        flags = self.windowFlags()
        if checked:
            self.setWindowFlags(flags | QtCore.Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(flags & ~QtCore.Qt.WindowStaysOnTopHint)
        self.show()

    def _start_cd_timer(self):
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._tick_cd)
        self.timer.start(500)

    def _tick_cd(self):
        in_cd, remain = self.buffer.is_in_cooldown()
        if in_cd:
            self.badge_cd.setText(f"冷却 {remain:.0f}s")
            self.badge_cd.setStyleSheet(
                "background:#2a1a1c; border:1px solid #6b3335; border-radius:6px;"
                "padding:5px 11px; color:#e08a86; font-size:12px; font-weight:600;")
        else:
            self.badge_cd.setText("冷却就绪")
            self.badge_cd.setStyleSheet(
                "background:#101218; border:1px solid #2a2f3a; border-radius:6px;"
                "padding:5px 11px; color:#6cb6ff; font-size:12px; font-weight:600;")

    # ---------------- 事件处理 ----------------
    def _on_mic_speech(self, text, emotion=None):
        tag = f"  [{emotion}]" if (emotion and self.listener.enable_emotion) else ""
        self.sig.log.emit("mic", f"麦克风  \"{text}\"{tag}")
        self.buffer.add("麦克风语音", text, emotion=emotion if self.listener.enable_emotion else None)
        self._trigger_eval()

    def _on_sys_speech(self, text, emotion=None):
        tag = f"  [{emotion}]" if (emotion and self.system_listener.enable_emotion) else ""
        self.sig.log.emit("speaker", f"队友语音  \"{text}\"{tag}")
        self.buffer.add("队友开黑语音", text, emotion=emotion if self.system_listener.enable_emotion else None)
        self._trigger_eval()

    def _on_screen_event(self, desc):
        self.sig.log.emit("screen", f"画面事件  {desc}")
        self.buffer.add("屏幕视觉", desc)
        self._trigger_eval()

    def _trigger_eval(self):
        threading.Thread(target=self._evaluate, daemon=True).start()

    def _evaluate(self):
        if not self._eval_lock.acquire(blocking=False):
            return
        try:
            in_cd, remain = self.buffer.is_in_cooldown()
            if in_cd:
                self.sig.log.emit("cd", f"冷却保护，剩余 {remain:.1f}s")
                return

            ctx = self.buffer.get_prompt_context()
            self.sig.log.emit("ai", "正在研判接梗时机…")
            decision = self.arbiter.decide(ctx)
            self._accumulate(decision)
            self.sig.audit.emit(decision)

            if decision.should_play:
                title = decision.meme.title if decision.meme else "?"
                self.sig.log.emit("hit", f"命中《{title}》 ({decision.latency_ms:.0f}ms) · {decision.reason}")
                self.player.play(decision.meme.filename)
                self.buffer.mark_played(title)
            else:
                self.sig.log.emit("silent", f"保持沉默 ({decision.latency_ms:.0f}ms) · {decision.reason}")
        finally:
            self._eval_lock.release()

    def _accumulate(self, decision, extra_tokens: int = 0):
        """累计本次会话用量。extra_tokens 用于把独立视觉模型的消耗一并计入"""
        if not self.session_active:
            return
        self.session_calls += 1
        self.session_total += decision.total_tokens + extra_tokens
        self.session_prompt += decision.prompt_tokens + extra_tokens
        self.session_output += decision.completion_tokens
        self.session_cached += decision.cached_tokens
        if decision.should_play and decision.meme:
            self.session_played.append(decision.meme.title)

    @QtCore.pyqtSlot()
    def snap_and_analyze(self):
        self.sig.log.emit("screen", "正在截取画面并送入视觉模型…")
        threading.Thread(target=self._do_snap, daemon=True).start()

    def _do_snap(self):
        b64 = capture_screen_for_vision(self.config.get("vision_api", {}))
        if not b64:
            self.sig.log.emit("warn", "截图失败")
            return

        v = self.config.get("vision_api", {})
        if v.get("vision_source", "main") == "custom":
            summary, vt, vmodel = self.arbiter.analyze_screen_vision(b64)
            self.sig.log.emit("screen", f"视觉模型 [{vmodel}]：\"{summary}\"")
            self.buffer.add("画面视觉", summary)
            # 把视觉摘要一并交给漏斗粗筛，让画面信息参与候选选择
            ctx = self.buffer.get_prompt_context()
            decision = self.arbiter.decide(ctx, visual_summary=summary)
            # 视觉模型自身消耗也计入会话（否则账单对不上）
            self._accumulate(decision, extra_tokens=vt)
            self.sig.audit.emit(decision)
            if decision.should_play:
                title = decision.meme.title if decision.meme else "?"
                self.sig.log.emit("hit", f"截图命中《{title}》 · {decision.reason}")
                self.player.play(decision.meme.filename)
                self.buffer.mark_played(title)
            else:
                self.sig.log.emit("silent", f"截图未接梗 · {decision.reason}")
        else:
            ctx = self.buffer.get_prompt_context()
            prompt = f"{ctx}\n[画面截图] 玩家按下了截图研判，请结合截图判断当前战局并决定是否接梗。"
            decision = self.arbiter.decide(prompt, image_base64=b64)
            self._accumulate(decision)
            self.sig.audit.emit(decision)
            if decision.should_play:
                title = decision.meme.title if decision.meme else "?"
                self.sig.log.emit("hit", f"截图命中《{title}》 · {decision.reason}")
                self.player.play(decision.meme.filename)
                self.buffer.mark_played(title)
            else:
                self.sig.log.emit("silent", f"截图未接梗 · {decision.reason}")

    def _on_auto_frame(self, b64):
        """
        采样线程回调：只把最新画面塞进容积为 1 的队列。
        若上一帧还在分析中，直接丢弃旧帧（保证始终分析最新战局，且不会堆积请求）。
        """
        if not self.session_active:
            return
        with self._frame_lock:
            self._pending_frame = b64

    # ---------------- 测试 ----------------
    def _quick_test(self, text):
        self.edit_test.setText(text)
        self._send_test()

    def _send_test(self):
        raw = self.edit_test.text().strip()
        if not raw:
            return
        self.edit_test.clear()

        emotion = None
        content = raw
        if "#" in raw:
            content, emotion = [p.strip() for p in raw.split("#", 1)]

        if content.startswith("@"):
            src, content = "游戏事件", content[1:].strip()
            self.sig.log.emit("screen", f"测试事件  {content}")
            line = f"[{src}] {content}"
        else:
            tag = f"  [{emotion}]" if emotion else ""
            self.sig.log.emit("mic", f"测试输入  \"{content}\"{tag}  （单次，不入记忆）")
            line = f"[开黑对话] \"{content}\"" + (f" [情绪: {emotion}]" if emotion else "")

        threading.Thread(target=self._test_eval, args=(line,), daemon=True).start()

    def _test_eval(self, ctx):
        decision = self.arbiter.decide(ctx)
        self.sig.audit.emit(decision)
        if decision.should_play:
            title = decision.meme.title if decision.meme else "?"
            self.sig.log.emit("hit", f"命中《{title}》 · {decision.reason}")
            self.player.play(decision.meme.filename)
        else:
            self.sig.log.emit("silent", f"保持沉默 · {decision.reason}")

    # ---------------- 展示 ----------------
    def _render_audit(self, decision: DecisionResult):
        result = f"播放《{decision.meme.title}》" if decision.should_play else "保持沉默"
        self.lbl_engine.setText(f"{decision.engine_used}   ·   {decision.latency_ms:.0f}ms   ·   {result}")

        # 本次请求的明细
        if decision.total_tokens > 0:
            cache = f"缓存命中 {decision.cached_tokens} ({decision.cache_hit_rate:.1f}%)" if decision.cached_tokens > 0 else "无缓存命中"
            billing = self.config.get("api", {}).get("billing_mode", "free")
            cost = "免费渠道" if billing == "free" else f"约 ¥{decision.total_tokens/1000*0.002:.4f}"
            self.lbl_tokens.setText(
                f"本次 {decision.total_tokens} Tokens（输入 {decision.prompt_tokens} / 输出 {decision.completion_tokens}）   ·   {cache}   ·   {cost}\n"
                f"本次会话累计：{self.session_calls} 次决策 · {self.session_total} Tokens"
            )
        else:
            self.lbl_tokens.setText(
                f"本次 0 Tokens（本地漏斗直接裁决，未发起远程请求）\n"
                f"本次会话累计：{self.session_calls} 次决策 · {self.session_total} Tokens"
            )

        # 徽章始终显示会话累计，不随单次 0 消耗而归零
        self.badge_tok.setText(f"{self.session_calls} 次 · {self.session_total} Tok")

        self.txt_thinking.setPlainText(decision.thinking or "（无详细推理内容）")
        self.txt_prompt.setPlainText(decision.prompt_sent or "（未构造远程请求）")

    def _copy_log(self):
        text = (
            f"{self.lbl_engine.text()}\n{self.lbl_tokens.text()}\n\n"
            f"=== 推理过程 ===\n{self.txt_thinking.toPlainText()}\n\n"
            f"=== 输入上下文 ===\n{self.txt_prompt.toPlainText()}"
        )
        QtWidgets.QApplication.clipboard().setText(text)
        self._log("sys", "已复制到剪贴板")

    def _log(self, kind, text):
        ts = time.strftime("%H:%M:%S")
        colors = {
            "sys": "#6cb6ff",
            "mic": "#d8b25a",
            "speaker": "#7ec8e3",
            "screen": "#d98cc0",
            "ai": "#b18cf0",
            "hit": "#3fb87d",
            "silent": "#6b7484",
            "cd": "#5a6272",
            "play": "#e0a04a",
            "warn": "#db5852",
        }
        color = colors.get(kind, "#c4cbd6")
        html = (
            f"<div style='margin-bottom:5px;'>"
            f"<span style='color:#4a5261;'>[{ts}]</span> "
            f"<span style='color:{color};'>{text}</span></div>"
        )
        self.txt_log.append(html)
        sb = self.txt_log.verticalScrollBar()
        sb.setValue(sb.maximum())

    def closeEvent(self, event):
        self._frame_worker_stop = True
        if _KEYBOARD_AVAILABLE:
            # keyboard 库的 listen/process 是守护线程，且没有公开的停止接口，
            # 只能解绑热键；进程退出时守护线程会自动结束，不影响正常关闭。
            try:
                keyboard.unhook_all()
            except Exception:
                pass
        self.listener.stop()
        self.system_listener.stop()
        self.screen_watcher.stop()
        self.player.stop()
        event.accept()


def apply_dark_palette(app):
    """为 Fusion 风格注入完整深色调色板，杜绝系统浅色默认值从缝隙透出"""
    from PyQt5 import QtGui as _QtGui
    c = QtGui.QColor
    pal = _QtGui.QPalette()
    pal.setColor(pal.Window, c("#14161c"))
    pal.setColor(pal.WindowText, c("#e8ecf3"))
    pal.setColor(pal.Base, c("#0e1015"))
    pal.setColor(pal.AlternateBase, c("#191c24"))
    pal.setColor(pal.ToolTipBase, c("#232832"))
    pal.setColor(pal.ToolTipText, c("#e8ecf3"))
    pal.setColor(pal.Text, c("#e8ecf3"))
    pal.setColor(pal.Button, c("#232832"))
    pal.setColor(pal.ButtonText, c("#d5dbe5"))
    pal.setColor(pal.BrightText, c("#ffffff"))
    pal.setColor(pal.Link, c("#6cb6ff"))
    pal.setColor(pal.Highlight, c("#2a3444"))
    pal.setColor(pal.HighlightedText, c("#ffffff"))
    pal.setColor(pal.PlaceholderText, c("#5a6272"))

    # 禁用态
    dis = _QtGui.QPalette.Disabled
    pal.setColor(dis, pal.WindowText, c("#5a6272"))
    pal.setColor(dis, pal.Text, c("#5a6272"))
    pal.setColor(dis, pal.ButtonText, c("#5a6272"))
    pal.setColor(dis, pal.Base, c("#14161c"))
    app.setPalette(pal)


def main():
    # 日志初始化：GUI 模式写入文件，控制台只保留 WARNING 以上
    import logging
    from core.http_client import setup_logging
    setup_logging(
        level=logging.INFO,
        log_file=str(CURRENT_DIR / "logs" / "automeme.log"),
        quiet_console=False,
    )
    logging.getLogger("automeme").setLevel(logging.INFO)

    # Windows 高分屏：按物理像素渲染，避免系统二次拉伸导致文字发虚
    if sys.platform == "win32":
        try:
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseHighDpiPixmaps, True)

    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    apply_dark_palette(app)

    win = MainWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
