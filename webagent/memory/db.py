from __future__ import annotations
import json, sqlite3, threading
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """SQLite persistence for chats, messages, tasks, evidence, events and artifacts.

    The schema intentionally keeps v0.1 tables compatible: existing databases are
    migrated in place with additive columns/tables only.
    """
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self._init()

    def _conn(self):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        return c

    def _columns(self, c, table: str) -> set[str]:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}

    def _init(self):
        with self._conn() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS chats(
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                settings_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                at TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                FOREIGN KEY(chat_id) REFERENCES chats(id)
            );
            CREATE TABLE IF NOT EXISTS tasks(
                id INTEGER PRIMARY KEY,
                goal TEXT,
                status TEXT,
                created_at TEXT,
                result TEXT
            );
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY,
                task_id INTEGER,
                at TEXT,
                kind TEXT,
                message TEXT
            );
            CREATE TABLE IF NOT EXISTS evidence(
                id INTEGER PRIMARY KEY,
                task_id INTEGER,
                at TEXT,
                url TEXT,
                title TEXT,
                text TEXT
            );
            CREATE TABLE IF NOT EXISTS task_plans(
                task_id INTEGER PRIMARY KEY,
                plan_json TEXT NOT NULL,
                state_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS artifacts(
                id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                task_id INTEGER,
                at TEXT NOT NULL,
                name TEXT NOT NULL,
                path TEXT NOT NULL,
                mime TEXT DEFAULT '',
                size INTEGER DEFAULT 0,
                FOREIGN KEY(chat_id) REFERENCES chats(id)
            );
            ''')
            # Additive migration for v0.1 databases.
            task_cols = self._columns(c, "tasks")
            if "chat_id" not in task_cols:
                c.execute("ALTER TABLE tasks ADD COLUMN chat_id INTEGER")
            if "settings_json" not in task_cols:
                c.execute("ALTER TABLE tasks ADD COLUMN settings_json TEXT NOT NULL DEFAULT '{}'")
            if "parent_task_id" not in task_cols:
                c.execute("ALTER TABLE tasks ADD COLUMN parent_task_id INTEGER")
            if "run_id" not in task_cols:
                c.execute("ALTER TABLE tasks ADD COLUMN run_id TEXT")

    # ---------- Chats ----------
    def create_chat(self, title: str = "New chat", settings: dict | None = None) -> int:
        now = _now()
        with self.lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO chats(title,created_at,updated_at,settings_json) VALUES(?,?,?,?)",
                (title.strip() or "New chat", now, now, json.dumps(settings or {})),
            )
            return int(cur.lastrowid)

    def list_chats(self, query: str = "") -> list[dict]:
        with self.lock, self._conn() as c:
            if query.strip():
                rows = c.execute(
                    "SELECT * FROM chats WHERE title LIKE ? ORDER BY updated_at DESC",
                    (f"%{query.strip()}%",),
                ).fetchall()
            else:
                rows = c.execute("SELECT * FROM chats ORDER BY updated_at DESC").fetchall()
            return [dict(r) for r in rows]

    def get_chat(self, chat_id: int) -> dict | None:
        with self.lock, self._conn() as c:
            row = c.execute("SELECT * FROM chats WHERE id=?", (chat_id,)).fetchone()
            return dict(row) if row else None

    def rename_chat(self, chat_id: int, title: str) -> None:
        with self.lock, self._conn() as c:
            c.execute("UPDATE chats SET title=?,updated_at=? WHERE id=?", (title.strip() or "New chat", _now(), chat_id))

    def update_chat_settings(self, chat_id: int, settings: dict) -> None:
        with self.lock, self._conn() as c:
            c.execute("UPDATE chats SET settings_json=?,updated_at=? WHERE id=?", (json.dumps(settings), _now(), chat_id))

    def delete_chat(self, chat_id: int) -> None:
        with self.lock, self._conn() as c:
            task_ids = [r[0] for r in c.execute("SELECT id FROM tasks WHERE chat_id=?", (chat_id,)).fetchall()]
            if task_ids:
                marks = ",".join("?" for _ in task_ids)
                c.execute(f"DELETE FROM events WHERE task_id IN ({marks})", task_ids)
                c.execute(f"DELETE FROM evidence WHERE task_id IN ({marks})", task_ids)
                c.execute(f"DELETE FROM task_plans WHERE task_id IN ({marks})", task_ids)
            c.execute("DELETE FROM artifacts WHERE chat_id=?", (chat_id,))
            c.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
            c.execute("DELETE FROM tasks WHERE chat_id=?", (chat_id,))
            c.execute("DELETE FROM chats WHERE id=?", (chat_id,))

    # ---------- Messages ----------
    def add_message(self, chat_id: int, role: str, content: str) -> int:
        with self.lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO messages(chat_id,at,role,content) VALUES(?,?,?,?)",
                (chat_id, _now(), role, content),
            )
            c.execute("UPDATE chats SET updated_at=? WHERE id=?", (_now(), chat_id))
            return int(cur.lastrowid)

    def messages(self, chat_id: int, limit: int = 500) -> list[dict]:
        with self.lock, self._conn() as c:
            rows = c.execute(
                "SELECT * FROM messages WHERE chat_id=? ORDER BY id ASC LIMIT ?",
                (chat_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # ---------- Tasks / events / evidence ----------
    def create_task(self, goal: str, chat_id: int | None = None, settings: dict | None = None, parent_task_id: int | None = None, run_id: str | None = None):
        now = _now()
        with self.lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO tasks(goal,status,created_at,chat_id,settings_json,parent_task_id,run_id) VALUES(?,?,?,?,?,?,?)",
                (goal, "running", now, chat_id, json.dumps(settings or {}), parent_task_id, run_id),
            )
            if chat_id:
                c.execute("UPDATE chats SET updated_at=? WHERE id=?", (now, chat_id))
            return int(cur.lastrowid)

    def event(self, task_id, kind, message):
        with self.lock, self._conn() as c:
            c.execute("INSERT INTO events(task_id,at,kind,message) VALUES(?,?,?,?)", (task_id, _now(), kind, message))

    def events_for_chat(self, chat_id: int, limit: int = 300) -> list[dict]:
        with self.lock, self._conn() as c:
            rows = c.execute(
                """SELECT e.* FROM events e JOIN tasks t ON t.id=e.task_id
                   WHERE t.chat_id=? ORDER BY e.id DESC LIMIT ?""",
                (chat_id, limit),
            ).fetchall()
            return [dict(r) for r in reversed(rows)]

    def evidence(self, task_id, url, title, text):
        with self.lock, self._conn() as c:
            cur=c.execute(
                "INSERT INTO evidence(task_id,at,url,title,text) VALUES(?,?,?,?,?)",
                (task_id, _now(), url, title, text[:20000]),
            )
            return int(cur.lastrowid)

    def evidence_by_ids(self, ids:list[int]) -> list[dict]:
        if not ids:return []
        marks=','.join('?' for _ in ids)
        with self.lock, self._conn() as c:
            rows=c.execute(f"SELECT * FROM evidence WHERE id IN ({marks}) ORDER BY id",ids).fetchall()
            return [dict(r) for r in rows]

    def evidence_for_chat(self, chat_id: int, limit: int = 300) -> list[dict]:
        with self.lock, self._conn() as c:
            rows = c.execute(
                """SELECT e.* FROM evidence e JOIN tasks t ON t.id=e.task_id
                   WHERE t.chat_id=? ORDER BY e.id DESC LIMIT ?""",
                (chat_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def finish(self, task_id, status, result=""):
        with self.lock, self._conn() as c:
            c.execute("UPDATE tasks SET status=?,result=? WHERE id=?", (status, result, task_id))


    def save_plan(self, task_id:int, plan:dict) -> None:
        state={t.get("key"):"pending" for t in plan.get("tasks",[])}
        with self.lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO task_plans(task_id,plan_json,state_json,updated_at) VALUES(?,?,?,?)",(task_id,json.dumps(plan),json.dumps(state),_now()))

    def update_plan_task(self, task_id:int, key:str, status:str) -> None:
        with self.lock, self._conn() as c:
            row=c.execute("SELECT state_json FROM task_plans WHERE task_id=?",(task_id,)).fetchone()
            if not row:return
            try: state=json.loads(row[0] or '{}')
            except Exception: state={}
            state[key]=status
            c.execute("UPDATE task_plans SET state_json=?,updated_at=? WHERE task_id=?",(json.dumps(state),_now(),task_id))

    def plan_for_task(self, task_id:int) -> dict|None:
        with self.lock, self._conn() as c:
            row=c.execute("SELECT * FROM task_plans WHERE task_id=?",(task_id,)).fetchone()
            if not row:return None
            d=dict(row); d['plan']=json.loads(d.pop('plan_json')); d['state']=json.loads(d.pop('state_json')); return d

    def latest_task_for_chat(self, chat_id:int) -> dict|None:
        with self.lock, self._conn() as c:
            row=c.execute("SELECT * FROM tasks WHERE chat_id=? ORDER BY id DESC LIMIT 1",(chat_id,)).fetchone()
            return dict(row) if row else None

    def latest_root_task_for_chat(self, chat_id:int) -> dict|None:
        with self.lock, self._conn() as c:
            row=c.execute("SELECT * FROM tasks WHERE chat_id=? AND parent_task_id IS NULL ORDER BY id DESC LIMIT 1",(chat_id,)).fetchone()
            return dict(row) if row else None

    def tasks_for_tree(self, root_task_id:int) -> list[dict]:
        ids=self.task_tree_ids(root_task_id)
        if not ids:return []
        marks=','.join('?' for _ in ids)
        with self.lock, self._conn() as c:
            rows=c.execute(f"SELECT * FROM tasks WHERE id IN ({marks}) ORDER BY id",ids).fetchall()
            return [dict(r) for r in rows]

    def evidence_for_task(self, task_id:int, limit:int=300) -> list[dict]:
        with self.lock, self._conn() as c:
            rows=c.execute("SELECT * FROM evidence WHERE task_id=? ORDER BY id DESC LIMIT ?",(task_id,limit)).fetchall()
            return [dict(r) for r in rows]


    def get_task(self, task_id:int) -> dict|None:
        with self.lock, self._conn() as c:
            row=c.execute("SELECT * FROM tasks WHERE id=?",(task_id,)).fetchone()
            return dict(row) if row else None

    def task_children(self, task_id:int) -> list[dict]:
        with self.lock, self._conn() as c:
            rows=c.execute("SELECT * FROM tasks WHERE parent_task_id=? ORDER BY id",(task_id,)).fetchall()
            return [dict(r) for r in rows]

    def task_tree_ids(self, root_task_id:int) -> list[int]:
        with self.lock, self._conn() as c:
            seen=[]; queue=[int(root_task_id)]
            while queue:
                cur=queue.pop(0)
                if cur in seen: continue
                seen.append(cur)
                rows=c.execute("SELECT id FROM tasks WHERE parent_task_id=? ORDER BY id",(cur,)).fetchall()
                queue.extend(int(r[0]) for r in rows)
            return seen

    def evidence_for_task_tree(self, root_task_id:int, limit:int=500) -> list[dict]:
        ids=self.task_tree_ids(root_task_id)
        if not ids:return []
        marks=','.join('?' for _ in ids)
        with self.lock, self._conn() as c:
            rows=c.execute(f"SELECT * FROM evidence WHERE task_id IN ({marks}) ORDER BY id DESC LIMIT ?",(*ids,limit)).fetchall()
            return [dict(r) for r in rows]

    def artifacts_for_task_tree(self, root_task_id:int) -> list[dict]:
        ids=self.task_tree_ids(root_task_id)
        if not ids:return []
        marks=','.join('?' for _ in ids)
        with self.lock, self._conn() as c:
            rows=c.execute(f"SELECT * FROM artifacts WHERE task_id IN ({marks}) ORDER BY id DESC",ids).fetchall()
            return [dict(r) for r in rows]

    # ---------- Artifacts ----------
    def add_artifact(self, chat_id: int, task_id: int | None, name: str, path: str, mime: str = "", size: int = 0) -> int:
        with self.lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO artifacts(chat_id,task_id,at,name,path,mime,size) VALUES(?,?,?,?,?,?,?)",
                (chat_id, task_id, _now(), name, path, mime, int(size or 0)),
            )
            c.execute("UPDATE chats SET updated_at=? WHERE id=?", (_now(), chat_id))
            return int(cur.lastrowid)

    def artifacts(self, chat_id: int) -> list[dict]:
        with self.lock, self._conn() as c:
            rows = c.execute("SELECT * FROM artifacts WHERE chat_id=? ORDER BY id DESC", (chat_id,)).fetchall()
            return [dict(r) for r in rows]
