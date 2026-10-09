from __future__ import annotations
import sys
from pathlib import Path
from PySide6.QtWidgets import QApplication, QMessageBox
from .config import AppConfig, APP_DIR, DB_PATH, BROWSER_DIR, DOWNLOAD_DIR, DIAGNOSTIC_DIR, CONFIG_PATH
from .integrations.agentsmith import AgentSmithBridge
from .providers.broker import ModelBroker
from .browser.manager import BrowserManager
from .memory.db import Database
from .files.workspace import WorkspaceManager
from .ui.main_window import MainWindow
from .diagnostics import DiagnosticManager

DARK_QSS = r'''
QWidget { background: #15181d; color: #e7eaf0; font-size: 13px; }
QMainWindow { background: #111318; }
QFrame#sidebar { background: #101217; border-right: 1px solid #292e37; }
QLabel#chatTitle { font-size: 20px; font-weight: 700; padding: 4px 2px; }
QLabel#sectionTitle { font-size: 16px; font-weight: 700; }
QLabel#muted { color: #9aa3b2; }
QLabel#verificationBanner { background: #332a16; border: 1px solid #8d6b24; border-radius: 6px; padding: 8px 10px; font-weight: 700; }
QPlainTextEdit, QLineEdit, QListWidget, QTableWidget, QComboBox, QSpinBox { background: #1b1f26; color: #f2f4f8; border: 1px solid #303743; border-radius: 6px; padding: 5px; selection-background-color: #365a8a; }
QListWidget::item { padding: 9px 7px; border-radius: 5px; }
QListWidget::item:selected { background: #263a55; }
QPushButton, QToolButton { background: #242a33; border: 1px solid #39414d; border-radius: 6px; padding: 7px 10px; }
QPushButton:hover, QToolButton:hover { background: #2e3540; }
QPushButton#primaryButton { background: #2d63a7; border-color: #3978c8; font-weight: 700; }
QPushButton#primaryButton:hover { background: #3572bc; }
QTabWidget::pane { border: 1px solid #303743; background: #171a20; }
QTabBar::tab { background: #20252d; padding: 8px 13px; border: 1px solid #303743; }
QTabBar::tab:selected { background: #2b3340; }
QHeaderView::section { background: #232933; color: #dce2ea; border: 0; border-right: 1px solid #303743; padding: 6px; }
QScrollBar:vertical { background: #15181d; width: 12px; } QScrollBar::handle:vertical { background: #3a424e; border-radius: 5px; min-height: 25px; }
QToolTip { background: #242a33; color: #fff; border: 1px solid #4a5361; }
'''

def main():
    app=QApplication(sys.argv); app.setApplicationName("Web Agent"); cfg=AppConfig.load()
    if cfg.dark_theme: app.setStyleSheet(DARK_QSS)
    diagnostics=DiagnosticManager(DIAGNOSTIC_DIR)
    env=AgentSmithBridge().discover(); env.webagent_config=cfg; env.config.setdefault('rate_limit_max_wait_seconds',cfg.rate_limit_max_wait_seconds); broker=ModelBroker(env,diagnostics=diagnostics,data_dir=APP_DIR); db=Database(DB_PATH); browser=BrowserManager(BROWSER_DIR,DOWNLOAD_DIR,diagnostics=diagnostics); workspace=WorkspaceManager(Path(cfg.workspace_root),db)
    w=MainWindow(env,broker,browser,db,workspace,cfg,diagnostics,DB_PATH,CONFIG_PATH); w.show()
    if env.error: QMessageBox.warning(w,"AgentSmith discovery",env.error+"\n\nWeb Agent can still start, but provider access may be unavailable.")
    sys.exit(app.exec())

if __name__=="__main__": main()
