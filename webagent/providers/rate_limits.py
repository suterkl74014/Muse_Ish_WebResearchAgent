from __future__ import annotations
import hashlib, json, math, re, sqlite3, threading, time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_RATE_TEXT=re.compile(r"(?i)(rate.?limit|too many requests|quota|resource[_ ]?exhausted|tokens per minute|requests per minute|429)")

def estimate_tokens(text: str) -> int:
    if not text: return 0
    return max(1, math.ceil(len(text) / 3.3))

def retry_after_seconds(headers: Any = None, body: str = "", default: float = 15.0) -> float:
    try:
        if headers:
            value=headers.get("retry-after") or headers.get("Retry-After")
            if value:
                try: return max(.5, float(value))
                except Exception: pass
            for k in ("x-ratelimit-reset-tokens","x-ratelimit-reset-requests","x-ratelimit-reset"):
                value=headers.get(k)
                if value:
                    m=re.search(r"([0-9]+(?:\.[0-9]+)?)\s*(ms|s|sec|seconds|m|min|minutes)?",str(value),re.I)
                    if m:
                        n=float(m.group(1)); unit=(m.group(2) or 's').lower()
                        if unit=='ms': n/=1000
                        elif unit.startswith('m'): n*=60
                        return max(.5,n)
    except Exception: pass
    for pat in [r"retry.?after[^0-9]{0,20}([0-9]+(?:\.[0-9]+)?)\s*(seconds?|secs?|s|minutes?|mins?|m)?",
                r"try again in\s*([0-9]+(?:\.[0-9]+)?)\s*(seconds?|secs?|s|minutes?|mins?|m)?"]:
        m=re.search(pat,body or "",re.I)
        if m:
            n=float(m.group(1)); unit=(m.group(2) or 's').lower()
            if unit.startswith('m'): n*=60
            return max(.5,n)
    return max(.5,float(default))

def is_rate_limit(status: int | None, text: str = "") -> bool:
    return status == 429 or bool(_RATE_TEXT.search(text or ""))

class RollingTokenLimiter:
    def __init__(self, limit_tokens: int=7000, window_seconds: float=60.0):
        self.limit=max(1,int(limit_tokens)); self.window=max(1.0,float(window_seconds)); self.events=deque(); self.next_id=1; self.lock=threading.RLock()
    def set_limit(self,limit_tokens:int):
        with self.lock:self.limit=max(1,int(limit_tokens))
    def _prune(self,now):
        cutoff=now-self.window
        while self.events and self.events[0][1] <= cutoff: self.events.popleft()
    def reserve(self,tokens:int):
        with self.lock:
            now=time.monotonic(); self._prune(now); amount=max(0,int(tokens))
            if amount > self.limit: raise ValueError(f"single request reservation {amount} exceeds rolling limit {self.limit}")
            event=self.next_id; self.next_id+=1
            if amount:self.events.append([event,now,amount])
            return event
    def wait_seconds(self,tokens:int):
        with self.lock:
            now=time.monotonic(); self._prune(now); amount=max(0,int(tokens))
            if amount > self.limit: raise ValueError(f"single request reservation {amount} exceeds rolling limit {self.limit}")
            used=sum(int(x[2]) for x in self.events)
            if used+amount<=self.limit:return 0.0
            remaining=used
            for _,ts,n in self.events:
                remaining-=int(n)
                if remaining+amount<=self.limit:return max(0.0,float(ts)+self.window-now)
            return self.window
    def settle(self,event_id:int,tokens:int):
        with self.lock:
            now=time.monotonic(); self._prune(now)
            for e in self.events:
                if e[0]==event_id:e[2]=max(0,int(tokens)); return
    def release(self,event_id:int):
        with self.lock:
            for e in list(self.events):
                if e[0]==event_id:self.events.remove(e); return True
        return False
    def snapshot(self):
        with self.lock:
            now=time.monotonic(); self._prune(now); used=sum(int(x[2]) for x in self.events)
            return {'used_tokens':used,'limit_tokens':self.limit,'remaining_tokens':max(0,self.limit-used),'window_seconds':self.window}

class DailyAttemptLedger:
    """Cross-process, cross-session per-key UTC-day safety ledger.

    SQLite + BEGIN IMMEDIATE makes reservation atomic across multiple Web Agent
    processes/installs for the same Windows user. The key itself is never stored.
    A request is counted before dispatch, intentionally including failed/crashed calls.
    """
    def __init__(self,path:Path,limit:int=30,legacy_json_paths=None):
        self.path=Path(path); self.limit=max(1,min(40,int(limit))); self.lock=threading.RLock()
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self._init_db()
        self._migrate_legacy([Path(x) for x in (legacy_json_paths or []) if x])
    @staticmethod
    def _fp(key:str): return hashlib.sha256(key.encode('utf-8')).hexdigest()
    @staticmethod
    def _day(): return datetime.now(timezone.utc).strftime('%Y-%m-%d')
    def _connect(self):
        c=sqlite3.connect(self.path,timeout=15,isolation_level=None)
        c.execute('PRAGMA journal_mode=WAL'); c.execute('PRAGMA busy_timeout=15000')
        return c
    def _init_db(self):
        with self._connect() as c:
            c.execute('CREATE TABLE IF NOT EXISTS usage(key_fp TEXT NOT NULL, utc_day TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0, updated_utc TEXT NOT NULL, PRIMARY KEY(key_fp,utc_day))')
            c.execute('CREATE TABLE IF NOT EXISTS migrations(source TEXT PRIMARY KEY, migrated_utc TEXT NOT NULL)')
    def _migrate_legacy(self,paths):
        for path in paths:
            try:
                if not path.exists(): continue
                marker=str(path.resolve())
                with self._connect() as c:
                    if c.execute('SELECT 1 FROM migrations WHERE source=?',(marker,)).fetchone(): continue
                data=json.loads(path.read_text(encoding='utf-8'))
                day=self._day()
                with self._connect() as c:
                    c.execute('BEGIN IMMEDIATE')
                    for fp,row in (data.items() if isinstance(data,dict) else []):
                        if not isinstance(row,dict): continue
                        rday=str(row.get('day') or row.get('date') or '')
                        used=max(0,int(row.get('used',0) or 0))
                        if rday!=day or used<=0: continue
                        existing=c.execute('SELECT used FROM usage WHERE key_fp=? AND utc_day=?',(fp,day)).fetchone()
                        keep=max(used,int(existing[0]) if existing else 0)
                        c.execute('INSERT INTO usage(key_fp,utc_day,used,updated_utc) VALUES(?,?,?,?) ON CONFLICT(key_fp,utc_day) DO UPDATE SET used=MAX(used,excluded.used), updated_utc=excluded.updated_utc',(fp,day,keep,datetime.now(timezone.utc).isoformat()))
                    c.execute('INSERT OR REPLACE INTO migrations(source,migrated_utc) VALUES(?,?)',(marker,datetime.now(timezone.utc).isoformat()))
                    c.execute('COMMIT')
            except Exception:
                # Conservative behavior is enforced by reserve() if the SQLite ledger itself is unavailable.
                pass
    def used(self,key:str):
        fp=self._fp(key); day=self._day()
        try:
            with self._connect() as c:
                row=c.execute('SELECT used FROM usage WHERE key_fp=? AND utc_day=?',(fp,day)).fetchone()
                return max(0,int(row[0])) if row else 0
        except Exception as e:
            raise RuntimeError(f'OpenRouter quota history unavailable; refusing request for safety: {e}') from e
    def can_use(self,key:str,external_used:int=0): return self.used(key)+max(0,int(external_used))<self.limit
    def reserve(self,key:str,external_used:int=0):
        fp=self._fp(key); day=self._day(); now=datetime.now(timezone.utc).isoformat()
        try:
            with self._connect() as c:
                c.execute('BEGIN IMMEDIATE')
                row=c.execute('SELECT used FROM usage WHERE key_fp=? AND utc_day=?',(fp,day)).fetchone(); used=max(0,int(row[0])) if row else 0
                external=max(0,int(external_used)); combined=used+external
                if combined>=self.limit:
                    c.execute('COMMIT'); return {'used':used,'external_used':external,'combined_used':combined,'limit':self.limit,'remaining':0,'allowed':False,'status':'DAILY_LOCKED'}
                used+=1
                c.execute('INSERT INTO usage(key_fp,utc_day,used,updated_utc) VALUES(?,?,?,?) ON CONFLICT(key_fp,utc_day) DO UPDATE SET used=excluded.used,updated_utc=excluded.updated_utc',(fp,day,used,now))
                c.execute('COMMIT')
                combined=used+external
                return {'used':used,'external_used':external,'combined_used':combined,'limit':self.limit,'remaining':max(0,self.limit-combined),'allowed':True,'status':'AVAILABLE' if combined<self.limit else 'DAILY_LOCKED'}
        except sqlite3.Error as e:
            raise RuntimeError(f'OpenRouter quota ledger unavailable; refusing request for safety: {e}') from e
