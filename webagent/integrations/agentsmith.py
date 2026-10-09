from __future__ import annotations
import json, os, re, subprocess
from dataclasses import dataclass, field
from typing import Any

@dataclass
class AgentSmithEnvironment:
    distro: str | None = None
    config: dict[str, Any] = field(default_factory=dict)
    secrets: dict[str, str] = field(default_factory=dict)
    codex_path: str | None = None
    error: str | None = None

    def keys(self, stem: str) -> list[str]:
        values: list[tuple[int, str]] = []
        legacy = self.secrets.get(stem, "").strip()
        for k, v in self.secrets.items():
            m = re.fullmatch(re.escape(stem) + r"_(\d+)", k)
            if m and v.strip():
                values.append((int(m.group(1)), v.strip()))
        values.sort()
        result = [v for _, v in values]
        if legacy and legacy not in result:
            result.insert(0, legacy)
        return result

    @property
    def groq_keys(self): return self.keys("GROQ_API_KEY")
    @property
    def gemini_keys(self): return self.keys("GEMINI_API_KEY")
    @property
    def openrouter_keys(self): return self.keys("OPENROUTER_API_KEY")

class AgentSmithBridge:
    """Read-only compatibility bridge. No AgentSmith Python modules are imported."""
    def __init__(self, preferred_distro: str | None = None):
        self.preferred_distro = preferred_distro or os.environ.get("WEBAGENT_WSL_DISTRO")

    @staticmethod
    def _wsl(args: list[str], distro: str | None = None, timeout: int = 20) -> subprocess.CompletedProcess:
        cmd = ["wsl.exe"]
        if distro:
            cmd += ["-d", distro]
        cmd += args
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)

    def _distros(self) -> list[str]:
        try:
            p = self._wsl(["-l", "-q"])
            text = (p.stdout or "").replace("\x00", "")
            return [x.strip() for x in text.splitlines() if x.strip()]
        except Exception:
            return []

    def discover(self, include_secrets: bool = True) -> AgentSmithEnvironment:
        env = AgentSmithEnvironment()
        candidates = []
        if self.preferred_distro:
            candidates.append(self.preferred_distro)
        distros = self._distros()
        for name in ["GroqVM", "AgentSmith"] + distros:
            if name and name not in candidates:
                candidates.append(name)
        if not candidates:
            env.error = "No WSL distributions found."
            return env
        for distro in candidates:
            try:
                p = self._wsl(["--", "sh", "-lc", "test -f /etc/groqvm/config.json && echo YES || true"], distro)
                if "YES" in (p.stdout or ""):
                    env.distro = distro
                    break
            except Exception:
                continue
        if not env.distro:
            env.error = "No WSL distro containing /etc/groqvm/config.json was found."
            return env
        script = r'''python3 - <<'PY'
import json, os, shutil
from pathlib import Path
cfg={}
try: cfg=json.loads(Path('/etc/groqvm/config.json').read_text())
except Exception: pass
sec={}
if __INCLUDE_SECRETS__:
    try:
        for raw in Path('/etc/groqvm/secrets.env').read_text().splitlines():
            line=raw.strip()
            if line and not line.startswith('#') and '=' in line:
                k,v=line.split('=',1); sec[k.strip()]=v.strip()
    except Exception: pass
candidates=[os.environ.get('CODEX_BIN',''), os.environ.get('CODEX_PATH',''), shutil.which('codex') or '', str(Path.home()/'.local/bin/codex'), '/usr/local/bin/codex', '/usr/bin/codex']
codex=next((p for p in candidates if p and Path(p).is_file()), None)
print(json.dumps({'config':cfg,'secrets':sec,'codex_path':codex}))
PY'''
        script = script.replace('__INCLUDE_SECRETS__', 'True' if include_secrets else 'False')
        try:
            p = self._wsl(["--", "sh", "-lc", script], env.distro)
            if p.returncode != 0:
                raise RuntimeError((p.stderr or p.stdout).strip())
            data = json.loads((p.stdout or "{}").strip())
            env.config = data.get("config") or {}
            env.secrets = data.get("secrets") or {}
            env.codex_path = data.get("codex_path")
            return env
        except Exception as exc:
            env.error = f"AgentSmith discovery failed: {exc}"
            return env

def read_agentsmith_openrouter_usage(distro: str | None) -> dict[str, int]:
    """Read AgentSmith's OpenRouter per-key usage for the current UTC day."""
    if not distro:
        raise RuntimeError('AgentSmith distro is unknown')
    script = """python3 - <<'PY'
import json
from datetime import datetime, timezone
from pathlib import Path
p=Path('/var/lib/groqvm/openrouter_usage.json')
day=datetime.now(timezone.utc).date().isoformat()
out={}
if p.exists():
    data=json.loads(p.read_text())
    if isinstance(data,dict):
        for fp,row in data.items():
            if isinstance(row,dict) and str(row.get('date') or row.get('day') or '')==day:
                out[str(fp)]=max(0,int(row.get('used',0) or 0))
print(json.dumps(out))
PY"""
    p=subprocess.run(['wsl.exe','-d',distro,'--','sh','-lc',script],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=8)
    if p.returncode!=0:
        raise RuntimeError((p.stderr or p.stdout or 'could not read AgentSmith OpenRouter usage').strip())
    data=json.loads((p.stdout or '{}').strip())
    return {str(k):max(0,int(v)) for k,v in data.items()}
