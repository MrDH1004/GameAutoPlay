"""제어판 공통 색상과 스타일. 작은 글씨와 충분한 대비를 유지한다."""
C = dict(bg="#10151e", panel="#182130", panel2="#202d40", line="#2d3d54",
         text="#edf3fc", sub="#a3b2c8", accent="#89adff", ok="#65dbb2",
         warn="#f1c778", bad="#fa8e9b", idle="#71839d")

QSS = """
* { font-family: 'Malgun Gothic', 'Segoe UI'; font-size: 12px; color: %(text)s; }
QMainWindow, QWidget#root { background: %(bg)s; }
QWidget { background: transparent; }
QFrame#card, QFrame#sidebar, QFrame#toolbar { background: %(panel)s; border: 1px solid %(line)s; border-radius: 12px; }
QLabel#cardTitle, QLabel#sub { color: %(sub)s; font-size: 11px; }
QLabel#big { font-size: 13px; font-weight: 700; }
QLabel#pageTitle { font-size: 13px; font-weight: 700; }
QTabWidget::pane { border: none; }
QTabBar::tab { color: %(sub)s; padding: 9px 14px; border-bottom: 2px solid transparent; }
QTabBar::tab:selected { color: %(accent)s; border-bottom-color: %(accent)s; }
QPushButton { background: %(panel2)s; border: 1px solid %(line)s; border-radius: 8px; padding: 8px 12px; font-weight: 600; }
QPushButton:hover { border-color: %(accent)s; background: #293b55; }
QPushButton:focus { border: 1px solid %(accent)s; }
QPushButton:disabled { color: %(idle)s; background: %(panel)s; }
QPushButton#nav { text-align: left; background: transparent; border: 1px solid transparent; padding: 11px 10px; }
QPushButton#nav:checked { background: #283b58; color: #c9dbff; border-color: #3a557a; }
QPushButton#nav:hover { background: %(panel2)s; }
QPushButton#start { background: %(ok)s; color: #102c24; border: none; }
QPushButton#start:hover { background: #86e6c4; }
QPushButton#start:disabled { background: %(panel2)s; color: %(idle)s; }
QPushButton#stop { color: %(bad)s; border-color: #65414e; }
QPushButton#stop:disabled { color: %(idle)s; border-color: %(line)s; }
QGroupBox { background: %(panel)s; border: 1px solid %(line)s; border-radius: 10px; margin-top: 14px; padding: 18px 12px 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: %(sub)s; }
QLineEdit, QSpinBox, QDoubleSpinBox, QTimeEdit, QComboBox { background: %(panel2)s; border: 1px solid %(line)s; border-radius: 7px; padding: 6px 8px; min-height: 20px; }
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: %(accent)s; }
QComboBox QAbstractItemView { background: %(panel2)s; selection-background-color: #36547d; }
QPlainTextEdit, QTextBrowser { background: #111a28; border: 1px solid %(line)s; border-radius: 10px; padding: 8px; }
QPlainTextEdit { font-family: Consolas, 'Malgun Gothic'; font-size: 11px; }
QTableWidget { background: %(panel)s; alternate-background-color: %(panel2)s; gridline-color: %(line)s; border: 1px solid %(line)s; selection-background-color: #36547d; }
QHeaderView::section { background: %(panel2)s; border: none; padding: 8px; }
QToolTip, QMenu { background: %(panel2)s; color: %(text)s; border: 1px solid %(line)s; padding: 6px; }
QMenu::item:selected { background: #36547d; }
QScrollArea { border: none; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: #3d506a; border-radius: 4px; min-height: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; }
QProgressBar { background: %(panel2)s; border: none; border-radius: 3px; max-height: 6px; }
QProgressBar::chunk { background: %(ok)s; border-radius: 3px; }
""" % C
