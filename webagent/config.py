from __future__ import annotations
import json, os
from dataclasses import dataclass, asdict
from pathlib import Path

APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".webagent")) / "WebAgent"
CONFIG_PATH = APP_DIR / "config.json"
DB_PATH = APP_DIR / "webagent.db"
BROWSER_DIR = APP_DIR / "browser-profile"
DOWNLOAD_DIR = APP_DIR / "downloads"
DIAGNOSTIC_DIR = APP_DIR / "diagnostics"
SHARED_DIR = APP_DIR / "shared"
OPENROUTER_LEDGER_PATH = SHARED_DIR / "openrouter_quota.sqlite3"
CREDENTIALS_PATH = APP_DIR / "credentials.json"  # metadata only; raw keys live in the OS credential store
DEFAULT_WORKSPACE = Path.home() / "Documents" / "WebAgent Workspace"

@dataclass
class AppConfig:
    mode: str = "hybrid"  # automatic | manual | hybrid
    primary_provider: str = "codex"
    primary_model: str = ""
    browser_provider: str = "groq"
    browser_model: str = ""
    vision_provider: str = "gemini"
    vision_model: str = ""
    final_provider: str = "codex"
    final_model: str = ""
    max_steps: int = 24
    research_depth: str = "standard"
    max_workers: int = 3
    start_url: str = "https://www.google.com/"
    workspace_root: str = str(DEFAULT_WORKSPACE)
    dark_theme: bool = True
    rate_limit_max_wait_seconds: int = 60
    # Provider/model management. Key fingerprints are used so raw API keys are never written here.
    disabled_key_fingerprints: dict = None
    provider_priority: list = None
    openrouter_daily_allocation: int = 30
    provider_max_parallel: dict = None

    def __post_init__(self):
        if self.disabled_key_fingerprints is None: self.disabled_key_fingerprints = {}
        if self.provider_priority is None: self.provider_priority = ["groq","gemini","openrouter","codex"]
        if self.provider_max_parallel is None: self.provider_max_parallel = {}

    @classmethod
    def load(cls) -> "AppConfig":
        APP_DIR.mkdir(parents=True, exist_ok=True)
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)
        SHARED_DIR.mkdir(parents=True, exist_ok=True)
        if not CONFIG_PATH.exists():
            obj = cls(); obj.ensure_dirs(); return obj
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            allowed = cls.__dataclass_fields__
            obj = cls(**{k: v for k, v in raw.items() if k in allowed})
            obj.ensure_dirs(); return obj
        except Exception:
            obj = cls(); obj.ensure_dirs(); return obj

    def ensure_dirs(self) -> None:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        DIAGNOSTIC_DIR.mkdir(parents=True, exist_ok=True)
        SHARED_DIR.mkdir(parents=True, exist_ok=True)
        Path(self.workspace_root).expanduser().mkdir(parents=True, exist_ok=True)

    def save(self) -> None:
        self.ensure_dirs()
        CONFIG_PATH.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
