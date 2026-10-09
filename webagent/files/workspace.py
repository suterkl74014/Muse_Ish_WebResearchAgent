from __future__ import annotations
import csv, json, mimetypes, os, shutil
from pathlib import Path

class WorkspaceManager:
    """Agent file access constrained to one configurable working root.

    Each chat gets a default subfolder, but callers may also address shared files
    under the root with paths beginning `shared/`. Absolute paths and traversal
    outside the root are rejected.
    """
    TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json", ".html", ".htm", ".xml", ".yaml", ".yml", ".log", ".py", ".js", ".css"}

    def __init__(self, root: Path, db):
        self.db = db
        self.set_root(root)

    def set_root(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "shared").mkdir(exist_ok=True)

    def chat_dir(self, chat_id: int) -> Path:
        p = self.root / f"chat-{int(chat_id):06d}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _safe(self, chat_id: int, requested: str) -> Path:
        requested = str(requested or "").strip().replace("\\", "/")
        if not requested:
            raise ValueError("A file path is required")
        p = Path(requested)
        if p.is_absolute():
            candidate = p.resolve()
        elif requested.startswith("shared/"):
            candidate = (self.root / requested).resolve()
        else:
            candidate = (self.chat_dir(chat_id) / requested).resolve()
        root = self.root.resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError(f"Path is outside the Web Agent working folder: {self.root}")
        return candidate

    def write_text(self, chat_id: int, task_id: int | None, path_name: str, content: str) -> dict:
        path = self._safe(chat_id, path_name); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(content), encoding="utf-8")
        return self._register(chat_id, task_id, path)

    def append_text(self, chat_id: int, task_id: int | None, path_name: str, content: str) -> dict:
        path = self._safe(chat_id, path_name); path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f: f.write(str(content))
        return self._register(chat_id, task_id, path)


    def write_html(self, chat_id: int, task_id: int | None, path_name: str, html: str) -> dict:
        if not path_name.lower().endswith((".html", ".htm")): path_name += ".html"
        return self.write_text(chat_id, task_id, path_name, str(html))

    def write_json(self, chat_id: int, task_id: int | None, path_name: str, data) -> dict:
        if not path_name.lower().endswith(".json"): path_name += ".json"
        return self.write_text(chat_id, task_id, path_name, json.dumps(data, indent=2, ensure_ascii=False))

    def write_csv(self, chat_id: int, task_id: int | None, path_name: str, rows: list) -> dict:
        if not path_name.lower().endswith(".csv"): path_name += ".csv"
        path = self._safe(chat_id, path_name); path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            if rows and isinstance(rows[0], dict):
                fields=[]
                for row in rows:
                    for key in row.keys():
                        if key not in fields: fields.append(key)
                w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
            else:
                w=csv.writer(f)
                for row in rows: w.writerow(row if isinstance(row,(list,tuple)) else [row])
        return self._register(chat_id, task_id, path, "text/csv")

    def read_text(self, chat_id: int, path_name: str, max_chars: int = 30000) -> str:
        path=self._safe(chat_id,path_name)
        if not path.exists() or not path.is_file(): raise FileNotFoundError(path_name)
        if path.suffix.lower() not in self.TEXT_EXTENSIONS: raise ValueError("This version can read only text-like files directly")
        return path.read_text(encoding="utf-8",errors="replace")[:max_chars]

    def list_files(self, chat_id: int, include_shared: bool = True) -> list[dict]:
        roots=[self.chat_dir(chat_id)]
        if include_shared: roots.append(self.root/"shared")
        out=[]
        for base in roots:
            for p in sorted(base.rglob("*")):
                if p.is_file(): out.append({"path":str(p.relative_to(self.root)),"size":p.stat().st_size})
        return out

    def delete(self, chat_id:int, path_name:str) -> None:
        path=self._safe(chat_id,path_name)
        if path.is_file(): path.unlink()
        elif path.is_dir(): shutil.rmtree(path)

    def _register(self, chat_id:int, task_id:int|None, path:Path, mime:str|None=None) -> dict:
        mime=mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.db.add_artifact(chat_id,task_id,path.name,str(path),mime,path.stat().st_size)
        return {"name":path.name,"path":str(path),"relative_path":str(path.relative_to(self.root)),"size":path.stat().st_size,"mime":mime}


    def temp_dir(self, run_id: str) -> Path:
        safe=''.join(c for c in str(run_id or 'run') if c.isalnum() or c in ('-','_'))[:96] or 'run'
        p=(self.root/'.temp'/safe).resolve(); p.mkdir(parents=True,exist_ok=True); return p

    def _safe_temp(self, run_id:str, requested:str) -> Path:
        requested=str(requested or '').strip().replace('\\','/')
        if not requested: raise ValueError('A temp file path is required')
        base=self.temp_dir(run_id); candidate=(base/requested).resolve()
        if candidate != base and base not in candidate.parents: raise ValueError('Temp path escapes run temp folder')
        return candidate

    def write_temp(self, run_id:str, path_name:str, content:str) -> dict:
        path=self._safe_temp(run_id,path_name); path.parent.mkdir(parents=True,exist_ok=True); path.write_text(str(content),encoding='utf-8')
        return {'name':path.name,'path':str(path),'relative_path':str(path.relative_to(self.root)),'size':path.stat().st_size}

    def read_temp(self, run_id:str, path_name:str, max_chars:int=60000) -> str:
        path=self._safe_temp(run_id,path_name)
        if not path.exists() or not path.is_file(): raise FileNotFoundError(path_name)
        return path.read_text(encoding='utf-8',errors='replace')[:max_chars]

    def list_temp(self, run_id:str) -> list[dict]:
        base=self.temp_dir(run_id); out=[]
        for p in sorted(base.rglob('*')):
            if p.is_file(): out.append({'path':str(p.relative_to(base)),'size':p.stat().st_size})
        return out

    def cleanup_temp(self, run_id:str) -> None:
        p=self.temp_dir(run_id)
        if p.exists(): shutil.rmtree(p,ignore_errors=True)

    def open_root(self) -> None: self._open(self.root)
    def open_chat_folder(self, chat_id:int) -> None: self._open(self.chat_dir(chat_id))
    @staticmethod
    def _open(path:Path) -> None:
        if os.name=="nt": os.startfile(path)  # type: ignore[attr-defined]
        else:
            import subprocess; subprocess.Popen(["xdg-open",str(path)])

# Optional richer artifact helpers are attached here to keep the file tool surface small.
def _write_docx(self, chat_id:int, task_id:int|None, path_name:str, title:str, content:str) -> dict:
    from docx import Document
    if not path_name.lower().endswith('.docx'): path_name += '.docx'
    path=self._safe(chat_id,path_name); path.parent.mkdir(parents=True,exist_ok=True)
    doc=Document()
    if title: doc.add_heading(title,0)
    for block in str(content).split('\n\n'):
        block=block.strip()
        if not block: continue
        if block.startswith('# '): doc.add_heading(block[2:].strip(),1)
        elif block.startswith('## '): doc.add_heading(block[3:].strip(),2)
        else: doc.add_paragraph(block)
    doc.save(path)
    return self._register(chat_id,task_id,path,'application/vnd.openxmlformats-officedocument.wordprocessingml.document')

def _write_pdf(self, chat_id:int, task_id:int|None, path_name:str, title:str, content:str) -> dict:
    from reportlab.lib.pagesizes import LETTER
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import inch
    if not path_name.lower().endswith('.pdf'): path_name += '.pdf'
    path=self._safe(chat_id,path_name); path.parent.mkdir(parents=True,exist_ok=True)
    styles=getSampleStyleSheet(); story=[]
    if title: story += [Paragraph(str(title),styles['Title']),Spacer(1,0.18*inch)]
    for block in str(content).split('\n\n'):
        block=block.strip()
        if not block: continue
        style=styles['BodyText']
        if block.startswith('# '): style=styles['Heading1']; block=block[2:].strip()
        elif block.startswith('## '): style=styles['Heading2']; block=block[3:].strip()
        story += [Paragraph(block.replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('\n','<br/>'),style),Spacer(1,0.08*inch)]
    SimpleDocTemplate(str(path),pagesize=LETTER,rightMargin=.65*inch,leftMargin=.65*inch,topMargin=.65*inch,bottomMargin=.65*inch).build(story)
    return self._register(chat_id,task_id,path,'application/pdf')

def _write_xlsx(self, chat_id:int, task_id:int|None, path_name:str, sheets:list) -> dict:
    from openpyxl import Workbook
    if not path_name.lower().endswith('.xlsx'): path_name += '.xlsx'
    path=self._safe(chat_id,path_name); path.parent.mkdir(parents=True,exist_ok=True)
    wb=Workbook(); default=wb.active
    if sheets:
        first=True
        for spec in sheets:
            name=str(spec.get('name') or 'Sheet')[:31]
            ws=default if first else wb.create_sheet(); first=False; ws.title=name
            rows=spec.get('rows') or []
            if rows and isinstance(rows[0],dict):
                fields=[]
                for row in rows:
                    for key in row.keys():
                        if key not in fields: fields.append(key)
                ws.append(fields)
                for row in rows: ws.append([row.get(k,'') for k in fields])
            else:
                for row in rows: ws.append(row if isinstance(row,(list,tuple)) else [row])
    wb.save(path)
    return self._register(chat_id,task_id,path,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')

WorkspaceManager.write_docx=_write_docx
WorkspaceManager.write_pdf=_write_pdf
WorkspaceManager.write_xlsx=_write_xlsx
