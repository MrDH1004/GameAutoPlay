"""제어판 공통 색상과 스타일. 작은 글씨와 충분한 대비를 유지한다."""
DARK_C = dict(bg="#10151e", panel="#182130", panel2="#202d40", line="#2d3d54",
              text="#edf3fc", sub="#a3b2c8", accent="#89adff", ok="#65dbb2",
              warn="#f1c778", bad="#fa8e9b", idle="#71839d",
              hover="#293b55", selected="#36547d", nav_selected="#283b58",
              nav_border="#3a557a", editor_bg="#111a28", scroll_handle="#3d506a",
              text_on_accent="#0c0e13")
LIGHT_C = dict(bg="#f3f6fb", panel="#ffffff", panel2="#e8eef7", line="#b8c6d9",
               text="#172235", sub="#53647b", accent="#2f6fca", ok="#16845f",
               warn="#9a6800", bad="#b83245", idle="#718096",
               hover="#dce8f8", selected="#b9d4f5", nav_selected="#d8e7fa",
               nav_border="#8db3e3", editor_bg="#ffffff", scroll_handle="#9aadc5",
               text_on_accent="#ffffff")
C = dict(DARK_C)


def theme_colors(name):
    return dict(LIGHT_C if name == "light" else DARK_C)

_QSS_TEMPLATE = """
* { font-family: 'Malgun Gothic', 'Segoe UI'; font-size: 14px; color: %(text)s; }
QMainWindow, QWidget#root { background: %(bg)s; }
QWidget { background: transparent; }
QFrame#card, QFrame#sidebar, QFrame#toolbar { background: %(panel)s; border: 1px solid %(line)s; border-radius: 12px; }
QLabel#cardTitle, QLabel#sub { color: %(sub)s; font-size: 13px; }
QLabel#big { font-size: 15px; font-weight: 700; }
QLabel#pageTitle { font-size: 16px; font-weight: 700; }
QTabWidget::pane { border: none; }
QTabBar::tab { color: %(sub)s; padding: 9px 14px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: %(accent)s; border-bottom-color: %(accent)s; }
QTabWidget#recommendTabs QTabBar { background: %(panel)s; }
QTabWidget#recommendTabs QTabBar::tab { background: %(panel2)s; color: %(sub)s; border: 1px solid %(line)s; border-radius: 6px; padding: 8px 13px; margin-right: 4px; }
QTabWidget#recommendTabs QTabBar::tab:selected { background: %(selected)s; color: %(text)s; border: 1px solid %(accent)s; font-weight: 700; }
QTabWidget#recommendTabs QTabBar::tab:hover { background: %(hover)s; color: %(text)s; }
QPushButton { background: %(panel2)s; border: 1px solid %(line)s; border-radius: 8px; padding: 8px 12px; font-weight: 600; }
QPushButton:hover { border-color: %(accent)s; background: %(hover)s; }
QPushButton:focus { border: 1px solid %(accent)s; }
QPushButton:disabled { color: %(idle)s; background: %(panel)s; }
QPushButton#nav { text-align: left; background: transparent; border: 1px solid transparent; padding: 11px 10px; }
QPushButton#nav:checked { background: %(nav_selected)s; color: %(text)s; border-color: %(nav_border)s; }
QPushButton#nav:hover { background: %(panel2)s; }
QPushButton#start { background: %(ok)s; color: #102c24; border: none; }
QPushButton#start:hover { background: %(ok)s; }
QPushButton#start:disabled { background: %(panel2)s; color: %(idle)s; }
QPushButton#stop { color: %(bad)s; border-color: #65414e; }
QPushButton#stop:disabled { color: %(idle)s; border-color: %(line)s; }
QGroupBox { background: %(panel)s; border: 1px solid %(line)s; border-radius: 10px; margin-top: 14px; padding: 18px 12px 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: %(sub)s; }
QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit, QComboBox { background: %(panel2)s; border: 1px solid %(line)s; border-radius: 7px; padding: 6px 8px; min-height: 20px; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: %(accent)s; }
QComboBox QAbstractItemView { background: %(panel2)s; selection-background-color: %(selected)s; }
QPlainTextEdit, QTextBrowser { background: %(editor_bg)s; border: 1px solid %(line)s; border-radius: 10px; padding: 8px; }
QPlainTextEdit { font-family: Consolas, 'Malgun Gothic'; font-size: 13px; }
QTableWidget { background: %(panel)s; alternate-background-color: %(panel2)s; gridline-color: %(line)s; border: 1px solid %(line)s; selection-background-color: %(selected)s; }
QHeaderView::section { background: %(panel2)s; border: none; padding: 8px; }
QToolTip, QMenu { background: %(panel2)s; color: %(text)s; border: 1px solid %(line)s; padding: 6px; }
QMenu::item:selected { background: %(selected)s; }
QScrollArea { border: none; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: %(scroll_handle)s; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QProgressBar { background: %(panel2)s; border: none; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: %(ok)s; border-radius: 3px; }
"""


def qss_for(colors):
    return _QSS_TEMPLATE % colors


QSS = qss_for(C)
