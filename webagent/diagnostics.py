from __future__ import annotations
import json, re, threading, time, uuid, zipfile, shutil
from datetime import datetime, timezone
from pathlib import Path

_SECRET_RE = re.compile(r'(?i)(api[_-]?key|token|secret|password)(["\'=:\s]+)([^\s,"\']+)')
_BEARER_RE = re.compile(r'(?i)bearer\s+[^\s,"\']+')

def _now(): return datetime.now(timezone.utc).isoformat()

def redact(value):
    if value is None: return None
    if isinstance(value, dict):
        out={}
        for k,v in value.items():
            kl=k.lower()
            # Provider logs use labels like 'key 2 [12-char-fingerprint]'; these contain no secret material.
            if kl=='api_key' and isinstance(v,str) and re.fullmatch(r'key \d+ \[[0-9a-f]{12}\]',v,re.I): out[k]=v
            elif any(x in kl for x in ('key','token','secret','password','authorization')): out[k]='<redacted>'
            else: out[k]=redact(v)
        return out
    if isinstance(value, list): return [redact(v) for v in value]
    s=str(value)
    s=_BEARER_RE.sub('Bearer <redacted>',s)
    return _SECRET_RE.sub(lambda m:f"{m.group(1)}{m.group(2)}<redacted>",s)

class DiagnosticManager:
    def __init__(self, root:Path):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True); self.lock=threading.RLock(); self.run_id=None; self.jsonl=None; self.text=None
    def start_run(self, goal:str, chat_id:int|None=None, settings:dict|None=None):
        rid=datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
        with self.lock:
            self.run_id=rid; self.jsonl=self.root/f'{rid}.jsonl'; self.text=self.root/f'{rid}.log'
            self.log('run_start',goal=goal,chat_id=chat_id,settings=settings or {})
        return rid
    def log(self,event:str,**data):
        with self.lock:
            if not self.run_id:
                self.start_run('application diagnostic session')
            rec={'at':_now(),'run_id':self.run_id,'event':event,**redact(data)}
            with self.jsonl.open('a',encoding='utf-8') as f: f.write(json.dumps(rec,ensure_ascii=False,default=str)+'\n')
            msg=data.get('message') or data.get('error') or ''
            line=f"[{rec['at']}] {event.upper()} {redact(msg)}"
            extras={k:v for k,v in rec.items() if k not in ('at','run_id','event') and k not in ('message','error')}
            if extras: line+=' '+json.dumps(extras,ensure_ascii=False,default=str)
            with self.text.open('a',encoding='utf-8') as f: f.write(line+'\n')
    def current_paths(self):
        return {'run_id':self.run_id,'jsonl':str(self.jsonl) if self.jsonl else '', 'log':str(self.text) if self.text else ''}
    def bundle(self, db_path:Path|None=None, config_path:Path|None=None, extra_files:list[Path]|None=None) -> Path:
        rid=self.run_id or 'latest'; target=self.root/f'{rid}-diagnostic-bundle.zip'
        manifest={'created_at':_now(),'run_id':rid,'files':[]}
        with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as z:
            for p in [self.jsonl,self.text]:
                if p and Path(p).exists(): z.write(p,arcname=Path(p).name); manifest['files'].append(Path(p).name)
            if db_path and Path(db_path).exists():
                tmp=self.root/f'{rid}-webagent.db'; shutil.copy2(db_path,tmp); z.write(tmp,arcname='webagent.db'); tmp.unlink(missing_ok=True); manifest['files'].append('webagent.db')
            if config_path and Path(config_path).exists():
                try:
                    cfg=redact(json.loads(Path(config_path).read_text(encoding='utf-8')))
                    z.writestr('config-redacted.json',json.dumps(cfg,indent=2,ensure_ascii=False)); manifest['files'].append('config-redacted.json')
                except Exception: pass
            for p in extra_files or []:
                if Path(p).exists(): z.write(p,arcname='extra/'+Path(p).name); manifest['files'].append('extra/'+Path(p).name)
            z.writestr('manifest.json',json.dumps(manifest,indent=2))
        return target
