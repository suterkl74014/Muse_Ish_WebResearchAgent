from __future__ import annotations
import json, os, threading
from pathlib import Path
from PySide6.QtCore import Signal, QObject, Qt, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QMainWindow,QWidget,QVBoxLayout,QHBoxLayout,QPlainTextEdit,QPushButton,QComboBox,QLabel,QSpinBox,
    QFormLayout,QSplitter,QMessageBox,QListWidget,QListWidgetItem,QLineEdit,QInputDialog,QTabWidget,
    QTableWidget,QTableWidgetItem,QHeaderView,QFileDialog,QToolButton,QFrame
)
from ..agent.runner import RunSettings
from ..agent.v3runner import V3Runner, V3Settings
from ..research import ResearchEngine
from .provider_settings import ProviderSettingsDialog

class Signals(QObject):
    event=Signal(str,str); done=Signal(str); error=Signal(str); models=Signal(str,list)

class MainWindow(QMainWindow):
    def __init__(self,env,broker,browser,db,workspace,config,diagnostics=None,db_path=None,config_path=None):
        super().__init__(); self.env=env; self.broker=broker; self.browser=browser; self.db=db; self.workspace=workspace; self.config=config; self.diagnostics=diagnostics; self.db_path=db_path; self.config_path=config_path
        self.signals=Signals(); self.runner=V3Runner(broker,browser,db,workspace,self._thread_event,research=ResearchEngine(diagnostics=diagnostics),diagnostics=diagnostics); self.worker=None; self.current_chat_id=None; self.running_chat_id=None
        self.setWindowTitle("Web Agent v0.3.10"); self.resize(1480,900); self.setMinimumSize(1100,700)
        self._build(); self.signals.event.connect(self._event); self.signals.done.connect(self._done); self.signals.error.connect(self._error); self.signals.models.connect(self._apply_models)
        self.live_timer=QTimer(self); self.live_timer.setInterval(700); self.live_timer.timeout.connect(self._live_refresh); self.live_timer.start()
        self.refresh_providers(); self._routing_mode_changed(self.mode.currentText()); self.refresh_chats(select_first=True)

    def _build(self):
        root=QWidget(); outer=QVBoxLayout(root); outer.setContentsMargins(10,10,10,10); outer.setSpacing(8)
        # top status/routing bar
        top=QHBoxLayout(); self.env_label=QLabel(); self.env_label.setObjectName("muted"); top.addWidget(self.env_label,1)
        self.mode=QComboBox(); self.mode.addItems(["automatic","manual","hybrid"]); self.mode.setCurrentText(self.config.mode); top.addWidget(QLabel("Mode")); top.addWidget(self.mode); self.mode.currentTextChanged.connect(self._routing_mode_changed)
        self.primary_p=QComboBox(); self.primary_m=QComboBox(); self.browser_p=QComboBox(); self.browser_m=QComboBox(); self.final_p=QComboBox(); self.final_m=QComboBox()
        for box in (self.primary_m,self.browser_m,self.final_m): box.setMinimumWidth(260)
        top.addWidget(QLabel("Primary")); top.addWidget(self.primary_p); top.addWidget(self.primary_m)
        self.provider_btn=QToolButton(); self.provider_btn.setText("Providers & Models…"); self.provider_btn.clicked.connect(self.show_provider_settings); top.addWidget(self.provider_btn)
        self.route_btn=QToolButton(); self.route_btn.setText("Routing…"); self.route_btn.clicked.connect(self.show_routing); top.addWidget(self.route_btn)
        self.limits_btn=QToolButton(); self.limits_btn.setText("Limits…"); self.limits_btn.clicked.connect(self.show_limits); top.addWidget(self.limits_btn)
        self.launch_btn=QPushButton("Launch Browser"); self.launch_btn.clicked.connect(self.launch_browser); top.addWidget(self.launch_btn)
        self.diag_btn=QToolButton(); self.diag_btn.setText("Diagnostics"); self.diag_btn.clicked.connect(self.open_diagnostics); top.addWidget(self.diag_btn)
        self.bundle_btn=QToolButton(); self.bundle_btn.setText("Make Bundle"); self.bundle_btn.clicked.connect(self.make_diagnostic_bundle); top.addWidget(self.bundle_btn)
        outer.addLayout(top)

        splitter=QSplitter(Qt.Horizontal)
        # chats sidebar
        side=QFrame(); side.setObjectName("sidebar"); sl=QVBoxLayout(side); sl.setContentsMargins(8,8,8,8)
        row=QHBoxLayout(); title=QLabel("Chats"); title.setObjectName("sectionTitle"); row.addWidget(title,1); newb=QPushButton("+ New"); newb.clicked.connect(self.new_chat); row.addWidget(newb); sl.addLayout(row)
        self.chat_search=QLineEdit(); self.chat_search.setPlaceholderText("Search chats…"); self.chat_search.textChanged.connect(lambda:self.refresh_chats(False)); sl.addWidget(self.chat_search)
        self.chat_list=QListWidget(); self.chat_list.currentItemChanged.connect(self.chat_selected); sl.addWidget(self.chat_list,1)
        crow=QHBoxLayout(); rb=QPushButton("Rename"); rb.clicked.connect(self.rename_chat); db=QPushButton("Delete"); db.clicked.connect(self.delete_chat); crow.addWidget(rb); crow.addWidget(db); sl.addLayout(crow)
        splitter.addWidget(side)

        # center conversation
        center=QWidget(); cl=QVBoxLayout(center); cl.setContentsMargins(8,0,8,0)
        self.chat_title=QLabel("New chat"); self.chat_title.setObjectName("chatTitle"); cl.addWidget(self.chat_title)
        self.verification_banner=QLabel("Human verification required in the visible browser. Complete the challenge there; Web Agent will resume automatically.")
        self.verification_banner.setWordWrap(True); self.verification_banner.setObjectName("verificationBanner"); self.verification_banner.setVisible(False); cl.addWidget(self.verification_banner)
        self.transcript=QPlainTextEdit(); self.transcript.setReadOnly(True); self.transcript.setPlaceholderText("Start a new conversation with Web Agent."); cl.addWidget(self.transcript,1)
        self.prompt=QPlainTextEdit(); self.prompt.setPlaceholderText("Ask Web Agent to research, browse, create a file, or do something on the web…"); self.prompt.setMaximumHeight(115); cl.addWidget(self.prompt)
        controls=QHBoxLayout(); self.run_btn=QPushButton("Run"); self.run_btn.setObjectName("primaryButton"); self.run_btn.clicked.connect(self.run_task)
        self.pause_btn=QPushButton("Pause / Take Control"); self.pause_btn.clicked.connect(self.toggle_pause); self.stop_btn=QPushButton("Stop"); self.stop_btn.clicked.connect(self.stop_task)
        self.steps=QSpinBox(); self.steps.setRange(3,100); self.steps.setValue(self.config.max_steps)
        self.depth=QComboBox(); self.depth.addItems(["quick","standard","deep"]); self.depth.setCurrentText(getattr(self.config,"research_depth","standard"))
        self.workers=QSpinBox(); self.workers.setRange(1,32); self.workers.setValue(getattr(self.config,"max_workers",3))
        controls.addWidget(self.run_btn); controls.addWidget(self.pause_btn); controls.addWidget(self.stop_btn); controls.addStretch(1); controls.addWidget(QLabel("Research")); controls.addWidget(self.depth); controls.addWidget(QLabel("Workers")); controls.addWidget(self.workers); controls.addWidget(QLabel("Max steps")); controls.addWidget(self.steps); cl.addLayout(controls)
        splitter.addWidget(center)

        # right inspector
        right=QWidget(); rl=QVBoxLayout(right); rl.setContentsMargins(0,0,0,0)
        wr=QHBoxLayout(); self.workspace_label=QLabel(); self.workspace_label.setObjectName("muted"); wr.addWidget(self.workspace_label,1)
        ob=QPushButton("Open Workspace"); ob.clicked.connect(self.open_workspace); cb=QPushButton("Change…"); cb.clicked.connect(self.change_workspace); wr.addWidget(ob); wr.addWidget(cb); rl.addLayout(wr)
        self.tabs=QTabWidget();
        self.activity=QPlainTextEdit(); self.activity.setReadOnly(True); self.tabs.addTab(self.activity,"Activity")
        self.tasks=QTableWidget(0,3); self.tasks.setHorizontalHeaderLabels(["Task","Type","Status"]); self.tasks.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch); self.tasks.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeToContents); self.tasks.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeToContents); self.tabs.addTab(self.tasks,"Tasks")
        self.sources=QTableWidget(0,2); self.sources.setHorizontalHeaderLabels(["Source","URL"]); self.sources.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeToContents); self.sources.horizontalHeader().setSectionResizeMode(1,QHeaderView.Stretch); self.sources.cellDoubleClicked.connect(self.open_source); self.tabs.addTab(self.sources,"Sources")
        self.files=QTableWidget(0,3); self.files.setHorizontalHeaderLabels(["File","Size","Path"]); self.files.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeToContents); self.files.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeToContents); self.files.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch); self.files.cellDoubleClicked.connect(self.open_file); self.tabs.addTab(self.files,"Files")
        rl.addWidget(self.tabs,1); splitter.addWidget(right)
        splitter.setSizes([230,730,470]); outer.addWidget(splitter,1); self.setCentralWidget(root)

    def _routing_mode_changed(self, mode):
        manual = (mode == "manual")
        # In Manual mode the Primary dropdown is the single authoritative provider/model.
        # Browser/Final settings remain saved for Hybrid mode but are not executable.
        self.route_btn.setToolTip("Manual mode uses Primary for every model call; Browser/Final routing is ignored." if manual else "Configure role-specific routing.")

    def show_provider_settings(self):
        d=ProviderSettingsDialog(self,self.broker,self.env,self.config)
        accepted=d.exec()
        # API-key add/remove operations apply immediately even if routing edits
        # are later cancelled, so always refresh the main provider bar.
        self.refresh_providers()
        if accepted:
            self.mode.setCurrentText(self.config.mode)
            self.activity.appendPlainText("[SETTINGS] Provider/model settings updated.")

    def show_routing(self):
        d=QMessageBox(self); d.setWindowTitle("Provider routing")
        w=QWidget(); f=QFormLayout(w)
        manual=self.mode.currentText()=="manual"
        if manual:
            note=QLabel("Manual mode: Primary is the ONLY provider/model used for planning, browsing, verification, and synthesis. Browser/Final settings below are ignored until you switch to Hybrid.")
            note.setWordWrap(True); f.addRow(note)
        for label,p,m in [("Primary",self.primary_p,self.primary_m),("Browser",self.browser_p,self.browser_m),("Final",self.final_p,self.final_m)]:
            row=QWidget(); h=QHBoxLayout(row); h.setContentsMargins(0,0,0,0); h.addWidget(p); h.addWidget(m); f.addRow(label,row)
            if manual and label != "Primary":
                p.setEnabled(False); m.setEnabled(False)
        d.layout().addWidget(w,1,0,1,d.layout().columnCount()); d.exec()
        # Restore enabled state after this informational dialog closes.
        self.browser_p.setEnabled(True); self.browser_m.setEnabled(True); self.final_p.setEnabled(True); self.final_m.setEnabled(True)

    def show_limits(self):
        lines=[]
        for label,pb,mb in [("Primary",self.primary_p,self.primary_m),("Browser",self.browser_p,self.browser_m),("Final",self.final_p,self.final_m)]:
            provider=pb.currentText(); model=mb.currentText()
            if not provider or not model or model=="Loading…": continue
            try:
                info=self.broker.model_info(provider,model)
                ctx=f"{info.context_tokens:,}" if info.context_tokens else "unknown / CLI-managed"
                out=f"{info.max_output_tokens:,}" if info.max_output_tokens else "unknown / configured default"
                lines.append(f"{label}: {provider} / {model}\n  context/input limit: {ctx} tokens\n  max output: {out} tokens\n  metadata: {info.source}")
            except Exception as e: lines.append(f"{label}: {provider} / {model} — metadata error: {e}")
        try:
            health=self.broker.health()
            lines.append("\nProvider health / cooldowns:")
            for name,row in health.items():
                lines.append(f"{name}: {json.dumps(row,ensure_ascii=False)}")
        except Exception as e: lines.append(f"Health unavailable: {e}")
        QMessageBox.information(self,"Model limits & rate status","\n\n".join(lines) if lines else "No provider/model selected.")

    def refresh_providers(self):
        avail=self.broker.available(); self.env_label.setText(f"WebAgent keys: Groq {self.broker.key_count('groq')}  •  Gemini {self.broker.key_count('gemini')}  •  OpenRouter {self.broker.key_count('openrouter')}  •  Optional Codex CLI {'✓' if self.env.codex_path else '—'}")
        specs=[(self.primary_p,self.primary_m,self.config.primary_provider,self.config.primary_model),(self.browser_p,self.browser_m,self.config.browser_provider,self.config.browser_model),(self.final_p,self.final_m,self.config.final_provider,self.config.final_model)]
        for pb,mb,wanted_p,wanted_m in specs:
            pb.blockSignals(True); pb.clear(); pb.addItems(avail); pb.setCurrentText(wanted_p if wanted_p in avail else (avail[0] if avail else "")); pb.blockSignals(False)
            pb.currentTextChanged.connect(lambda _=None,p=pb,m=mb:self.refresh_models(p,m)); self.refresh_models(pb,mb,wanted_m)
        self.workspace_label.setText(str(self.workspace.root))

    def refresh_models(self,pb,mb,wanted=""):
        p=pb.currentText(); mb.clear(); mb.addItem("Loading…")
        def work():
            try: models=self.broker.models(p) if p else []
            except Exception: models=[]
            self.signals.models.emit(p+"|"+str(id(mb)),models)
        mb.setProperty("wanted",wanted); threading.Thread(target=work,daemon=True).start()
    def _apply_models(self,key,models):
        p,_,ident=key.partition("|")
        for pb,mb in [(self.primary_p,self.primary_m),(self.browser_p,self.browser_m),(self.final_p,self.final_m)]:
            if str(id(mb))==ident and pb.currentText()==p:
                wanted=mb.property("wanted") or ""; mb.clear(); mb.addItems(models);
                for i in range(mb.count()): mb.setItemData(i,mb.itemText(i),Qt.ToolTipRole)
                if wanted and mb.findText(wanted)>=0: mb.setCurrentText(wanted)
                mb.setToolTip(mb.currentText())

    # chats
    def refresh_chats(self,select_first=False):
        keep=self.current_chat_id; self.chat_list.blockSignals(True); self.chat_list.clear()
        for c in self.db.list_chats(self.chat_search.text()):
            item=QListWidgetItem(c['title']); item.setData(Qt.UserRole,c['id']); self.chat_list.addItem(item)
            if c['id']==keep: self.chat_list.setCurrentItem(item)
        self.chat_list.blockSignals(False)
        if self.chat_list.count()==0 and not self.chat_search.text().strip():
            cid=self.db.create_chat(); self.current_chat_id=cid; return self.refresh_chats(True)
        if select_first or self.chat_list.currentItem() is None:
            if self.chat_list.count(): self.chat_list.setCurrentRow(0); self.chat_selected(self.chat_list.currentItem(),None)

    def new_chat(self):
        cid=self.db.create_chat(); self.current_chat_id=cid; self.chat_search.clear(); self.refresh_chats(False)
        for i in range(self.chat_list.count()):
            if self.chat_list.item(i).data(Qt.UserRole)==cid: self.chat_list.setCurrentRow(i); break
    def chat_selected(self,current,previous):
        if not current:return
        self.current_chat_id=int(current.data(Qt.UserRole)); c=self.db.get_chat(self.current_chat_id); self.chat_title.setText(c['title'] if c else 'Chat'); self.refresh_chat_views()
    def rename_chat(self):
        if not self.current_chat_id:return
        c=self.db.get_chat(self.current_chat_id); name,ok=QInputDialog.getText(self,"Rename chat","Name:",text=(c or {}).get('title',''))
        if ok and name.strip(): self.db.rename_chat(self.current_chat_id,name.strip()); self.refresh_chats(False); self.chat_title.setText(name.strip())
    def delete_chat(self):
        if not self.current_chat_id:return
        if QMessageBox.question(self,"Delete chat","Delete this chat history? Files in the working folder will be kept.")!=QMessageBox.Yes:return
        self.db.delete_chat(self.current_chat_id); self.current_chat_id=None; self.refresh_chats(True)

    def refresh_chat_views(self):
        if not self.current_chat_id:return
        msgs=self.db.messages(self.current_chat_id); blocks=[]
        for m in msgs:
            who="YOU" if m['role']=='user' else "WEB AGENT" if m['role']=='assistant' else m['role'].upper(); blocks.append(f"{who}\n{m['content']}")
        self.transcript.setPlainText("\n\n".join(blocks)); self.transcript.verticalScrollBar().setValue(self.transcript.verticalScrollBar().maximum())
        activity_text="\n".join(f"[{e['kind'].upper()}] {e['message']}" for e in self.db.events_for_chat(self.current_chat_id))
        self._refresh_activity_text(activity_text)
        ev=self.db.evidence_for_chat(self.current_chat_id); self.sources.setRowCount(len(ev))
        for r,e in enumerate(ev):
            self.sources.setItem(r,0,QTableWidgetItem(e.get('title') or 'Source')); self.sources.setItem(r,1,QTableWidgetItem(e.get('url') or ''))
        latest=self.db.latest_root_task_for_chat(self.current_chat_id); plan=self.db.plan_for_task(latest['id']) if latest else None
        rows=(plan or {}).get('plan',{}).get('tasks',[]) if plan else []; state=(plan or {}).get('state',{}) if plan else {}
        child_rows=[]
        if latest:
            for child in self.db.tasks_for_tree(latest['id']):
                if child['id']==latest['id']: continue
                child_rows.append({'title':f"↳ {child.get('goal','')[:90]}",'type':'worker','status':child.get('status','')})
        self.tasks.setRowCount(len(rows)+len(child_rows))
        for r,t in enumerate(rows):
            self.tasks.setItem(r,0,QTableWidgetItem(t.get('title',''))); self.tasks.setItem(r,1,QTableWidgetItem(t.get('type',''))); self.tasks.setItem(r,2,QTableWidgetItem(state.get(t.get('key'),'pending')))
        base=len(rows)
        for j,t in enumerate(child_rows):
            r=base+j; self.tasks.setItem(r,0,QTableWidgetItem(t['title'])); self.tasks.setItem(r,1,QTableWidgetItem(t['type'])); self.tasks.setItem(r,2,QTableWidgetItem(t['status']))
        arts=self.db.artifacts(self.current_chat_id); self.files.setRowCount(len(arts))
        for r,a in enumerate(arts):
            self.files.setItem(r,0,QTableWidgetItem(a['name'])); self.files.setItem(r,1,QTableWidgetItem(self._fmt_size(a.get('size') or 0))); self.files.setItem(r,2,QTableWidgetItem(a['path']))
        self.workspace_label.setText(str(self.workspace.root))
    def _refresh_activity_text(self, text: str):
        """Refresh Activity without snapping the viewport to the top.

        If the user is already following the live tail, keep it pinned to the
        newest entry. If they deliberately scrolled upward, preserve that
        position while new events arrive. Avoid resetting the document when
        nothing changed.
        """
        if self.activity.toPlainText() == text:
            return
        bar=self.activity.verticalScrollBar()
        old_value=bar.value(); old_max=bar.maximum()
        follow_tail=(old_max-old_value)<=2
        self.activity.setPlainText(text)
        bar=self.activity.verticalScrollBar()
        if follow_tail:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(min(old_value,bar.maximum()))

    @staticmethod
    def _fmt_size(n):
        n=float(n)
        for unit in ['B','KB','MB','GB']:
            if n<1024:return f"{n:.0f} {unit}" if unit=='B' else f"{n:.1f} {unit}"
            n/=1024
        return f"{n:.1f} TB"

    # task execution
    def _settings(self): return RunSettings(self.mode.currentText(),self.primary_p.currentText(),self.primary_m.currentText(),self.browser_p.currentText(),self.browser_m.currentText(),self.final_p.currentText(),self.final_m.currentText(),self.steps.value())
    def _live_refresh(self):
        if self.worker and self.worker.is_alive() and self.current_chat_id==self.running_chat_id:
            self.refresh_chat_views()

    def run_task(self):
        goal=self.prompt.toPlainText().strip()
        if not goal:return
        if not self.current_chat_id:self.new_chat()
        if self.worker and self.worker.is_alive(): QMessageBox.information(self,"Web Agent","A task is already running."); return
        if (self.db.get_chat(self.current_chat_id) or {}).get('title')=='New chat': self.db.rename_chat(self.current_chat_id,goal[:52].strip())
        self.db.add_message(self.current_chat_id,'user',goal); self.prompt.clear(); self.refresh_chats(False); self.refresh_chat_views(); self.run_btn.setEnabled(False); s=self._settings(); self._save_config(s); cid=self.current_chat_id
        def work():
            try:self.signals.done.emit(self.runner.run_v3(goal,V3Settings(s,self.depth.currentText(),self.workers.value()),cid))
            except Exception as e:self.signals.error.emit(str(e))
        self.running_chat_id=cid; self.worker=threading.Thread(target=work,daemon=True); self.worker.start()
    def _save_config(self,s):
        self.config.mode=s.mode; self.config.primary_provider=s.primary_provider; self.config.primary_model=s.primary_model; self.config.browser_provider=s.browser_provider; self.config.browser_model=s.browser_model; self.config.final_provider=s.final_provider; self.config.final_model=s.final_model; self.config.max_steps=s.max_steps; self.config.research_depth=self.depth.currentText(); self.config.max_workers=self.workers.value(); self.config.save()
    def _thread_event(self,k,m): self.signals.event.emit(k,m)
    def _event(self,k,m):
        # Events are persisted by task/chat in SQLite. Refreshing avoids leaking a running chat's activity into another chat if the user switches tabs.
        if k=='human_verification':
            self.verification_banner.setText(m); self.verification_banner.setVisible(True)
        elif k in ('human_verification_done','done','stop'):
            self.verification_banner.setVisible(False)
        if self.current_chat_id: self.refresh_chat_views()
    def _done(self,text):
        target=self.running_chat_id
        if target:self.db.add_message(target,'assistant',text)
        self.verification_banner.setVisible(False); self.running_chat_id=None; self.run_btn.setEnabled(True); self.pause_btn.setText("Pause / Take Control"); self.refresh_chats(False); self.refresh_chat_views()
    def _error(self,text): self.verification_banner.setVisible(False); self.run_btn.setEnabled(True); self.activity.appendPlainText("[ERROR] "+text); QMessageBox.critical(self,"Task failed",text)
    def toggle_pause(self):
        paused=self.pause_btn.text().startswith("Pause"); self.runner.set_paused(paused); self.pause_btn.setText("Resume Agent" if paused else "Pause / Take Control"); self.activity.appendPlainText("[CONTROL] "+("Paused. You control the visible browser." if paused else "Agent resumed."))
    def stop_task(self): self.runner.stop()

    # browser/workspace actions
    def launch_browser(self):
        try:self.browser.start(); self.browser.call("_navigate",self.config.start_url); self.activity.appendPlainText("[BROWSER] Visible Chromium launched.")
        except Exception as e:QMessageBox.critical(self,"Browser error",str(e))
    def open_workspace(self):
        try:self.workspace.open_root()
        except Exception as e:QMessageBox.warning(self,"Workspace",str(e))
    def change_workspace(self):
        selected=QFileDialog.getExistingDirectory(self,"Choose Web Agent working folder",str(self.workspace.root))
        if not selected:return
        try:
            self.workspace.set_root(Path(selected)); self.config.workspace_root=str(self.workspace.root); self.config.save(); self.refresh_chat_views()
        except Exception as e:QMessageBox.critical(self,"Workspace error",str(e))
    def open_source(self,row,col):
        item=self.sources.item(row,1)
        if item and item.text():
            try:self.browser.start(); self.browser.call("_navigate",item.text())
            except Exception:QDesktopServices.openUrl(QUrl(item.text()))
    def open_file(self,row,col):
        item=self.files.item(row,2)
        if not item:return
        p=item.text()
        try:
            if os.name=='nt': os.startfile(p)  # type: ignore[attr-defined]
            else: QDesktopServices.openUrl(QUrl.fromLocalFile(p))
        except Exception as e: QMessageBox.warning(self,"Open file",str(e))
    def open_diagnostics(self):
        if not self.diagnostics:return
        try:self.workspace._open(self.diagnostics.root)
        except Exception as e:QMessageBox.warning(self,"Diagnostics",str(e))
    def make_diagnostic_bundle(self):
        if not self.diagnostics:return
        try:
            path=self.diagnostics.bundle(self.db_path,self.config_path)
            QMessageBox.information(self,"Diagnostic bundle",f"Created:\n{path}")
            if os.name=='nt': os.startfile(path.parent)  # type: ignore[attr-defined]
        except Exception as e:QMessageBox.critical(self,"Diagnostic bundle",str(e))
    def closeEvent(self,event):
        try:self.runner.stop(); self.browser.stop()
        except Exception:pass
        event.accept()
