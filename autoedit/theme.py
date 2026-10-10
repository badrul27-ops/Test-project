"""AutoEditor dark theme + input guards.

One place for colours/spacing so every panel looks consistent.
"""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QAbstractSpinBox, QComboBox, QSlider

# ---- tokens
BG = "#15171C"          # window
SURFACE = "#1C1F26"     # panels
SURFACE_2 = "#232730"   # inputs / raised
BORDER = "#323846"
TEXT = "#E8EAF0"
TEXT_2 = "#B4BACB"      # secondary (still AA on SURFACE)
MUTED = "#8C93A6"
ACCENT = "#8B7CF6"      # restrained violet
ACCENT_HI = "#A195FA"
ACCENT_LO = "#4B3FA8"
OK = "#34D399"
WARN = "#F5A524"
ERR = "#F26D6D"
TL_BG = "#12141A"


def apply_theme(app):
    app.setStyle("Fusion")
    f = QFont("Segoe UI", 9)
    f.setStyleHint(QFont.SansSerif)
    app.setFont(f)
    pal = QPalette()
    for role, col in ((QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, SURFACE_2),
                      (QPalette.AlternateBase, SURFACE), (QPalette.ToolTipBase, SURFACE_2),
                      (QPalette.ToolTipText, TEXT), (QPalette.Text, TEXT), (QPalette.Button, SURFACE_2),
                      (QPalette.ButtonText, TEXT), (QPalette.BrightText, "#FFFFFF"),
                      (QPalette.Highlight, ACCENT_LO), (QPalette.HighlightedText, "#FFFFFF"),
                      (QPalette.Link, ACCENT_HI), (QPalette.PlaceholderText, MUTED)):
        pal.setColor(role, QColor(col))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor("#6B7285"))
    app.setPalette(pal)
    app.setStyleSheet(QSS)


import os as _os
_ASSETS = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "assets").replace("\\", "/")
CHECK = _ASSETS + "/check.svg"
DOWN = _ASSETS + "/down.svg"

QSS = f"""
* {{ outline: 0; }}
QMainWindow, QDialog {{ background: {BG}; }}
QWidget {{ color: {TEXT}; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 5px; }}

QMenuBar {{ background: {BG}; border-bottom: 1px solid {BORDER}; padding: 2px 4px; }}
QMenuBar::item {{ padding: 4px 10px; border-radius: 4px; background: transparent; }}
QMenuBar::item:selected {{ background: {SURFACE_2}; }}
QMenu {{ background: {SURFACE}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 28px 6px 14px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT_LO}; }}
QMenu::item:disabled {{ color: #5E6577; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}

QToolBar#Header {{ background: {SURFACE}; border: none; border-bottom: 1px solid {BORDER};
                   padding: 6px 8px; spacing: 6px; }}
QToolBar#Header QToolButton {{ padding: 5px 10px; border-radius: 6px; color: {TEXT}; }}
QToolBar#Header QToolButton:hover {{ background: {SURFACE_2}; }}
QToolBar#Header QToolButton:disabled {{ color: #5E6577; }}
QLabel#Brand {{ font-size: 14px; font-weight: 700; padding: 0 10px 0 4px; }}
QLabel#ProjectName {{ color: {TEXT_2}; font-weight: 600; padding: 0 12px 0 2px; }}
QLabel#StatusPill {{ border-radius: 9px; padding: 2px 10px; background: {BG}; color: {TEXT_2};
                     border: 1px dashed {BORDER}; font-size: 11px; }}
QLabel#StatusPill[state="dirty"] {{ color: {WARN}; }}
QLabel#StatusPill[state="saved"] {{ color: {OK}; }}
QLabel#StatusPill[state="busy"] {{ color: {ACCENT_HI}; }}

QFrame#Panel, QWidget#Panel {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px; }}
QLabel#PanelTitle {{ font-size: 12px; font-weight: 700; color: {TEXT}; padding: 2px 0 6px 0; }}
QLabel#Section {{ font-weight: 700; color: {TEXT}; padding-top: 8px; }}
QLabel#Help {{ color: {TEXT_2}; }}
QLabel#Ok {{ color: {OK}; }}
QLabel#Warn {{ color: {WARN}; }}
QLabel#Err {{ color: {ERR}; }}
QLabel#Empty {{ color: {TEXT_2}; font-size: 12px; }}

QWidget#ToolTabs {{ background: {SURFACE}; }}
QScrollArea#ToolTabScroll {{ background: {SURFACE}; border: none; border-bottom: 1px solid {BORDER}; }}
QToolButton#ToolTab {{ color: {TEXT_2}; background: transparent; border: none; border-radius: 8px;
                       padding: 6px 4px 5px 4px; font-size: 11px; }}
QToolButton#ToolTab:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QToolButton#ToolTab:checked {{ background: {ACCENT_LO}; color: #FFFFFF; font-weight: 600; }}
QToolButton#ClearBtn {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px; }}
QToolButton#ClearBtn:hover {{ background: #4A2A30; border-color: {ERR}; }}
QWidget#Home {{ background: {BG}; }}
QScrollArea#HomeScroll {{ background: {BG}; border: none; }}
QToolButton#VolumeButton {{ padding: 5px 8px; border: 1px solid {BORDER}; border-radius: 6px; }}
QToolButton#VolumeButton[muted="true"] {{ color: {WARN}; border-color: {WARN}; }}
QToolButton#VolumeButton::menu-indicator {{ image: none; width: 0; }}
QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#HomeTitle {{ font-size: 22px; font-weight: 700; }}
QLabel#HomeSub {{ font-size: 12px; color: {TEXT_2}; }}
QListWidget#Rail {{ background: {SURFACE}; border: none; border-right: 1px solid {BORDER}; padding: 6px 4px; }}
QListWidget#Rail::item {{ color: {TEXT_2}; padding: 4px 2px; margin: 2px 0; border-radius: 8px; }}
QListWidget#Rail::item:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QListWidget#Rail::item:selected {{ background: {ACCENT_LO}; color: #FFFFFF; }}

QGroupBox {{ border: 1px solid {BORDER}; border-radius: 8px; margin-top: 14px; padding: 10px 8px 8px 8px;
             background: {SURFACE}; font-weight: 700; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {TEXT}; }}

QLineEdit, QComboBox, QAbstractSpinBox, QPlainTextEdit, QTextEdit {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 8px;
    min-height: 20px; selection-background-color: {ACCENT_LO}; }}
QLineEdit:hover, QComboBox:hover, QAbstractSpinBox:hover {{ border-color: #465066; }}
QLineEdit:focus, QComboBox:focus, QAbstractSpinBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QLineEdit:disabled, QComboBox:disabled, QAbstractSpinBox:disabled {{ color: #6B7285; background: {SURFACE}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url({DOWN}); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {BORDER};
                               selection-background-color: {ACCENT_LO}; padding: 2px; }}

QPushButton {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 12px;
               min-height: 18px; }}
QPushButton:hover {{ border-color: #4A5470; background: #2A2F3A; }}
QPushButton:pressed {{ background: #20242C; }}
QPushButton:disabled {{ color: #5E6577; border-color: #2A2F3A; }}
QPushButton[primary="true"] {{ background: {ACCENT}; border: 1px solid {ACCENT}; color: #0E0B20; font-weight: 700; }}
QPushButton[primary="true"]:hover {{ background: {ACCENT_HI}; }}
QPushButton[primary="true"]:disabled {{ background: #2A2840; border-color: #34324F; color: #7F7AA8; }}

QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px; border: 1px solid #56607A;
                        background: {SURFACE_2}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url({CHECK}); }}
QCheckBox::indicator:hover {{ border-color: {ACCENT_HI}; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: #FFFFFF; width: 14px; height: 14px; margin: -6px 0; border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: {ACCENT_HI}; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; top: -1px; background: {SURFACE}; }}
QTabBar::tab {{ background: transparent; color: {TEXT_2}; padding: 7px 14px; border: none;
                border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabWidget#PropTabs QTabBar::tab {{ padding: 6px 8px; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}

QTableWidget, QListWidget, QTreeView {{ background: {SURFACE}; alternate-background-color: #20232B;
    border: 1px solid {BORDER}; border-radius: 6px; gridline-color: #2A2F3A;
    selection-background-color: {ACCENT_LO}; selection-color: #FFFFFF; }}
QHeaderView::section {{ background: {SURFACE_2}; color: {TEXT_2}; border: none;
    border-right: 1px solid {BORDER}; border-bottom: 1px solid {BORDER}; padding: 5px 6px; font-weight: 600; }}
QTableCornerButton::section {{ background: {SURFACE_2}; border: none; }}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #3A4152; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #4B5468; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #3A4152; border-radius: 4px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: #4B5468; }}
QScrollBar:horizontal:disabled {{ background: transparent; }}
QScrollBar::handle:horizontal:disabled {{ background: #262A33; }}
QScrollBar#TimelineBar:horizontal {{ background: {SURFACE_2}; height: 16px; margin: 0; border: 1px solid {BORDER};
                                     border-radius: 8px; }}
QScrollBar#TimelineBar::handle:horizontal {{ background: #7A83A0; border-radius: 6px; min-width: 40px; margin: 2px; }}
QScrollBar#TimelineBar::handle:horizontal:hover {{ background: {ACCENT_HI}; }}
QScrollBar#TimelineBar::handle:horizontal:pressed {{ background: {ACCENT}; }}
QScrollBar#TimelineBar:horizontal:disabled {{ background: {SURFACE}; border: 1px dashed {BORDER}; }}
QScrollBar#TimelineBar::handle:horizontal:disabled {{ background: #3A4152; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSplitter::handle {{ background: #262B35; }}
QSplitter::handle:horizontal {{ width: 5px; }}
QSplitter::handle:vertical {{ height: 5px; }}
QSplitter::handle:hover {{ background: {ACCENT_LO}; }}

QProgressBar {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px; text-align: center;
                color: {TEXT}; min-height: 14px; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 5px; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {TEXT_2}; }}
QStatusBar::item {{ border: none; }}
"""


class WheelGuard(QObject):
    """Root-cause fix for 'settings change when I scroll/click around':
    combo boxes, spin boxes and sliders only react to the mouse wheel when the user
    has deliberately focused them (clicked into them). Otherwise the wheel scrolls
    the panel as expected instead of silently changing a value."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Wheel and isinstance(obj, (QComboBox, QAbstractSpinBox, QSlider)):
            if not obj.hasFocus():
                # hand the wheel to the surrounding scroll area so the panel still scrolls
                from PySide6.QtWidgets import QAbstractScrollArea, QApplication
                p = obj.parent()
                while p is not None and not isinstance(p, QAbstractScrollArea):
                    p = p.parent()
                if p is not None:
                    QApplication.sendEvent(p.verticalScrollBar(), ev)
                return True
        return False

    @staticmethod
    def prepare(widget):
        """Wheel-sensitive controls get focus only by click/tab, never by hovering + wheel."""
        for cls in (QComboBox, QAbstractSpinBox, QSlider):
            for w in widget.findChildren(cls):
                w.setFocusPolicy(Qt.StrongFocus)
        # let combos shrink with narrow panels instead of forcing clipped layouts
        for w in widget.findChildren(QComboBox):
            w.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            if w.minimumContentsLength() < 6:
                w.setMinimumContentsLength(6)
