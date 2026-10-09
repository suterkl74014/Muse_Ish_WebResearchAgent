from __future__ import annotations

from PySide6.QtCore import Qt, QThread, Signal
from webagent.integrations.agentsmith import AgentSmithBridge

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTabWidget,
    QWidget,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QPushButton,
    QLabel,
    QComboBox,
    QFormLayout,
    QSpinBox,
    QCheckBox,
    QLineEdit,
    QPlainTextEdit,
    QDialogButtonBox,
    QMessageBox,
)


class ModelRefreshThread(QThread):
    completed = Signal(str, object, str)

    def __init__(self, broker, provider, parent=None):
        super().__init__(parent)
        self.broker = broker
        self.provider = provider

    def run(self):
        try:
            self.completed.emit(self.provider, self.broker.refresh_models(self.provider), '')
        except Exception as e:
            self.completed.emit(self.provider, [], str(e))


class AddKeysDialog(QDialog):
    """Paste one key or hundreds of keys without imposing an app-side count limit."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Add API keys')
        self.resize(650, 430)
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.provider = QComboBox()
        self.provider.addItems(['groq', 'gemini', 'openrouter'])
        self.label = QLineEdit()
        self.label.setPlaceholderText('Optional label, e.g. Personal or Research')
        form.addRow('Provider', self.provider)
        form.addRow('Label', self.label)
        root.addLayout(form)
        note = QLabel('Paste one API key per line. WebAgent does not impose a key-count limit. Blank lines and duplicate keys are ignored.')
        note.setWordWrap(True)
        root.addWidget(note)
        self.keys = QPlainTextEdit()
        self.keys.setPlaceholderText('key-1\nkey-2\nkey-3\n…')
        root.addWidget(self.keys, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _accept(self):
        if not any(line.strip() for line in self.keys.toPlainText().splitlines()):
            QMessageBox.warning(self, 'No keys', 'Paste at least one API key.')
            return
        self.accept()

    def values(self):
        return (
            self.provider.currentText(),
            [line.strip() for line in self.keys.toPlainText().splitlines() if line.strip()],
            self.label.text().strip(),
        )


class ProviderSettingsDialog(QDialog):
    """Provider/model/key manager. Raw keys are accepted once, then never displayed."""

    def __init__(self, parent, broker, env, config):
        super().__init__(parent)
        self.broker = broker
        self.env = env
        self.config = config
        self._refresh_thread = None
        self._refresh_generation = 0
        self.setWindowTitle('Providers & Models')
        self.resize(1120, 780)
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        self._build_keys()
        self._build_models()
        self._build_routing()
        self._build_safety()
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _build_keys(self):
        w = QWidget()
        l = QVBoxLayout(w)
        note = QLabel(
            'WebAgent owns these credentials. Add one key or paste as many as you want; raw keys are stored in the operating-system credential store and are never written to config.json. API-key add/remove operations apply immediately.'
        )
        note.setWordWrap(True)
        l.addWidget(note)
        self.key_table = QTableWidget(0, 7)
        self.key_table.setHorizontalHeaderLabels(['Provider', 'Label', 'Fingerprint', 'Source', 'Enabled', 'In use', 'State'])
        self.key_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.key_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.key_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.key_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        l.addWidget(self.key_table, 1)

        bar = QHBoxLayout()
        add = QPushButton('Add API keys…')
        add.clicked.connect(self.add_keys)
        remove = QPushButton('Remove selected')
        remove.clicked.connect(self.remove_selected_keys)
        self.import_button = QPushButton('Import AgentSmith keys…')
        self.import_button.clicked.connect(self.import_agentsmith_keys)
        refresh = QPushButton('Refresh status')
        refresh.clicked.connect(self.refresh_keys)
        bar.addWidget(add)
        bar.addWidget(remove)
        bar.addWidget(self.import_button)
        bar.addStretch(1)
        bar.addWidget(refresh)
        l.addLayout(bar)
        self.import_button.setToolTip('Optional migration helper. Legacy API keys are read only after you click this button and confirm the import.')
        self.tabs.addTab(w, 'API Keys')
        self.refresh_keys()

    def refresh_keys(self):
        rows = []
        for provider in ('groq', 'gemini', 'openrouter'):
            for r in self.broker.key_rows(provider):
                rows.append((provider, r))
        self.key_table.setRowCount(len(rows))
        for row, (provider, r) in enumerate(rows):
            pitem = QTableWidgetItem(provider)
            pitem.setData(Qt.UserRole, r.get('record_id'))
            self.key_table.setItem(row, 0, pitem)
            self.key_table.setItem(row, 1, QTableWidgetItem(r['label']))
            fp = QTableWidgetItem(r['fingerprint'])
            fp.setData(Qt.UserRole, r['fingerprint'])
            self.key_table.setItem(row, 2, fp)
            self.key_table.setItem(row, 3, QTableWidgetItem(r.get('source', 'local')))
            cb = QCheckBox()
            cb.setChecked(bool(r['enabled']))
            self.key_table.setCellWidget(row, 4, cb)
            self.key_table.setItem(row, 5, QTableWidgetItem('yes' if r.get('in_use') else 'no'))
            self.key_table.setItem(row, 6, QTableWidgetItem('enabled' if r['enabled'] else 'disabled'))

    def _refresh_provider_choices(self):
        available = self.broker.available()
        widgets = []
        if hasattr(self, 'model_provider'):
            widgets.append(self.model_provider)
        for name in ('primary_p', 'browser_p', 'final_p'):
            if hasattr(self, name):
                widgets.append(getattr(self, name))
        for box in widgets:
            keep = box.currentText()
            box.blockSignals(True)
            box.clear()
            box.addItems(available)
            if keep in available:
                box.setCurrentText(keep)
            elif available:
                box.setCurrentIndex(0)
            box.blockSignals(False)
            loader = getattr(box, '_webagent_model_loader', None)
            if callable(loader):
                loader()
        if hasattr(self, 'model_provider'):
            self.load_cached_models()

    def add_keys(self):
        if self.broker.credential_store is None:
            QMessageBox.critical(self, 'Credential storage unavailable', 'This WebAgent instance was not started with its standalone credential store.')
            return
        d = AddKeysDialog(self)
        if not d.exec():
            return
        provider, keys, label = d.values()
        try:
            result = self.broker.credential_store.add_many(provider, keys, label_prefix=label, source='user')
            self.broker.reload_provider_keys(provider)
            self.refresh_keys()
            self._refresh_provider_choices()
            QMessageBox.information(
                self,
                'API keys added',
                f"Added {result['added']} key(s)." + (f" {result['duplicates']} duplicate(s) were already stored." if result['duplicates'] else ''),
            )
        except Exception as e:
            QMessageBox.critical(self, 'Could not store API keys', str(e))

    def remove_selected_keys(self):
        if self.broker.credential_store is None:
            return
        rows = sorted({index.row() for index in self.key_table.selectionModel().selectedRows()})
        record_ids = [self.key_table.item(row, 0).data(Qt.UserRole) for row in rows]
        record_ids = [rid for rid in record_ids if rid]
        if not record_ids:
            QMessageBox.information(self, 'Remove API keys', 'Select one or more WebAgent-owned key rows first.')
            return
        if QMessageBox.question(self, 'Remove API keys', f'Remove {len(record_ids)} selected API key(s) from WebAgent and the OS credential store?') != QMessageBox.Yes:
            return
        affected = {self.key_table.item(row, 0).text() for row in rows if self.key_table.item(row, 0).data(Qt.UserRole)}
        try:
            removed = self.broker.credential_store.remove_many(record_ids)
            for provider in affected:
                self.broker.reload_provider_keys(provider)
            self.refresh_keys()
            self._refresh_provider_choices()
            QMessageBox.information(self, 'API keys removed', f'Removed {removed} key(s).')
        except Exception as e:
            QMessageBox.critical(self, 'Could not remove API keys', str(e))

    def import_agentsmith_keys(self):
        if self.broker.credential_store is None:
            return
        legacy_env = AgentSmithBridge(preferred_distro=getattr(self.env, 'distro', None)).discover(include_secrets=True)
        legacy = {
            'groq': list(getattr(legacy_env, 'groq_keys', []) or []),
            'gemini': list(getattr(legacy_env, 'gemini_keys', []) or []),
            'openrouter': list(getattr(legacy_env, 'openrouter_keys', []) or []),
        }
        total = sum(len(v) for v in legacy.values())
        if not total:
            QMessageBox.information(self, 'Import AgentSmith keys', 'No legacy AgentSmith/GroqVM API keys were discovered.')
            return
        if QMessageBox.question(
            self,
            'Import AgentSmith keys',
            f'Copy {total} discovered legacy API key(s) into WebAgent\'s OS credential store? AgentSmith remains optional after import.',
        ) != QMessageBox.Yes:
            return
        try:
            added = duplicates = 0
            for provider, keys in legacy.items():
                result = self.broker.credential_store.add_many(provider, keys, source='agentsmith-import')
                added += result['added']
                duplicates += result['duplicates']
            self.broker.reload_all_keys()
            self.refresh_keys()
            self._refresh_provider_choices()
            QMessageBox.information(self, 'Import complete', f'Imported {added} key(s); skipped {duplicates} duplicate(s).')
        except Exception as e:
            QMessageBox.critical(self, 'Import failed', str(e))

    def _build_models(self):
        w = QWidget()
        l = QVBoxLayout(w)
        bar = QHBoxLayout()
        self.model_provider = QComboBox()
        self.model_provider.addItems(self.broker.available())
        self.model_search = QLineEdit()
        self.model_search.setPlaceholderText('Filter free models…')
        self.refresh_button = QPushButton('Discover / Refresh')
        self.refresh_button.clicked.connect(self.refresh_models_async)
        self.refresh_status = QLabel('Free models only')
        self.refresh_status.setMinimumWidth(180)
        bar.addWidget(QLabel('Provider'))
        bar.addWidget(self.model_provider)
        bar.addWidget(self.model_search, 1)
        bar.addWidget(self.refresh_status)
        bar.addWidget(self.refresh_button)
        l.addLayout(bar)
        self.model_list = QTableWidget(0, 6)
        self.model_list.setHorizontalHeaderLabels(['Model ID', 'Context/Input', 'Max output', 'Free', 'Tools', 'Metadata source'])
        self.model_list.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, 6):
            self.model_list.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeToContents)
        l.addWidget(self.model_list, 1)
        self.tabs.addTab(w, 'Model Catalog')
        self.model_provider.currentTextChanged.connect(self.load_cached_models)
        self.model_search.textChanged.connect(self._filter_models)
        self._catalog = []
        self.load_cached_models()

    def load_cached_models(self, *_):
        p = self.model_provider.currentText()
        self._catalog = []
        if not p:
            self.refresh_status.setText('Add an API key to enable providers')
            self._render_models()
            return
        try:
            models = self.broker.cached_models(p)
            for mid in models:
                info = self.broker.model_info(p, mid)
                if p in ('groq', 'gemini', 'openrouter') and info.free is not True:
                    continue
                self._catalog.append((mid, info))
            self.refresh_status.setText(f'{len(self._catalog)} free models')
        except Exception as e:
            self.refresh_status.setText('cached catalog unavailable')
            self.refresh_status.setToolTip(str(e))
        self._render_models()

    def refresh_models_async(self):
        if self._refresh_thread and self._refresh_thread.isRunning():
            self._refresh_generation += 1
            self.refresh_status.setText('Cancel requested…')
            self.refresh_button.setEnabled(False)
            return
        p = self.model_provider.currentText()
        if not p:
            return
        self._refresh_generation += 1
        generation = self._refresh_generation
        self.refresh_status.setText('Refreshing…')
        self.refresh_button.setText('Cancel refresh')
        t = ModelRefreshThread(self.broker, p, self)
        self._refresh_thread = t

        def done(provider, models, error):
            if generation != self._refresh_generation:
                self.refresh_status.setText('Refresh cancelled; cached catalog kept')
            elif error:
                self.refresh_status.setText('Refresh failed; cached catalog kept')
                self.refresh_status.setToolTip(error)
            else:
                self._catalog = []
                for mid in models:
                    info = self.broker.model_info(provider, mid)
                    if provider in ('groq', 'gemini', 'openrouter') and info.free is not True:
                        continue
                    self._catalog.append((mid, info))
                self.refresh_status.setText(f'{len(self._catalog)} free models')
                self._render_models()
            self.refresh_button.setText('Discover / Refresh')
            self.refresh_button.setEnabled(True)
            self._refresh_thread = None

        t.completed.connect(done)
        t.start()

    def _filter_models(self):
        self._render_models()

    def _render_models(self):
        q = self.model_search.text().strip().lower()
        rows = [x for x in self._catalog if not q or q in x[0].lower()]
        self.model_list.setRowCount(len(rows))
        for r, (mid, info) in enumerate(rows):
            vals = [
                mid,
                f'{info.context_tokens:,}' if info.context_tokens else 'unknown',
                f'{info.max_output_tokens:,}' if info.max_output_tokens else 'unknown',
                'yes' if info.free is True else 'no',
                'yes' if info.supports_tools else 'no/unknown',
                info.source,
            ]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                item.setToolTip(v)
                self.model_list.setItem(r, c, item)

    def _route_row(self, form, label, pvalue, mvalue):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        pb = QComboBox()
        available = self.broker.available()
        pb.addItems(available)
        pb.setCurrentText(pvalue if pvalue in available else (available[0] if available else ''))
        mb = QComboBox()
        mb.setMinimumWidth(520)
        mb.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        mb.setMinimumContentsLength(42)

        def load(*_):
            provider = pb.currentText()
            wanted = mb.property('wanted') or ''
            mb.clear()
            try:
                vals = self.broker.cached_models(provider)
            except Exception:
                vals = []
            mb.addItems(vals)
            if wanted and mb.findText(wanted) >= 0:
                mb.setCurrentText(wanted)
            elif mvalue and mb.findText(mvalue) >= 0:
                mb.setCurrentText(mvalue)
            for i in range(mb.count()):
                mb.setItemData(i, mb.itemText(i), Qt.ToolTipRole)

        mb.setProperty('wanted', mvalue)
        pb._webagent_model_loader = load
        pb.currentTextChanged.connect(load)
        load()
        h.addWidget(pb)
        h.addWidget(mb, 1)
        form.addRow(label, row)
        return pb, mb

    def _build_routing(self):
        w = QWidget()
        f = QFormLayout(w)
        self.mode = QComboBox()
        self.mode.addItems(['automatic', 'manual', 'hybrid'])
        self.mode.setCurrentText(self.config.mode)
        f.addRow('Routing mode', self.mode)
        self.primary_p, self.primary_m = self._route_row(f, 'Primary / planner', self.config.primary_provider, self.config.primary_model)
        self.browser_p, self.browser_m = self._route_row(f, 'Browser navigator', self.config.browser_provider, self.config.browser_model)
        self.final_p, self.final_m = self._route_row(f, 'Final / synthesis', self.config.final_provider, self.config.final_model)
        self.priority = QLineEdit(', '.join(getattr(self.config, 'provider_priority', []) or ['groq', 'gemini', 'openrouter', 'codex']))
        f.addRow('Automatic provider order', self.priority)
        self.tabs.addTab(w, 'Routing')

    def _build_safety(self):
        w = QWidget()
        f = QFormLayout(w)
        self.or_alloc = QSpinBox()
        self.or_alloc.setRange(1, 40)
        self.or_alloc.setValue(min(40, int(getattr(self.config, 'openrouter_daily_allocation', 30) or 30)))
        self.or_alloc.setToolTip('Hard per-key UTC-day allocation. Shared across sessions and installations under this Windows user; reservations occur before dispatch.')
        f.addRow('OpenRouter hard requests/day/key', self.or_alloc)
        note = QLabel('Recommended: 30. The quota ledger is persistent, per API-key fingerprint, and atomic across concurrent WebAgent processes. AgentSmith quota synchronization is used only when that optional legacy environment is actually present.')
        note.setWordWrap(True)
        f.addRow(note)
        self.tabs.addTab(w, 'Safety')

    def _save(self):
        disabled = {}
        for row in range(self.key_table.rowCount()):
            provider = self.key_table.item(row, 0).text()
            fp = self.key_table.item(row, 2).data(Qt.UserRole)
            cb = self.key_table.cellWidget(row, 4)
            if not cb.isChecked():
                disabled.setdefault(provider, []).append(fp)
        self.config.disabled_key_fingerprints = disabled
        self.config.mode = self.mode.currentText()
        self.config.primary_provider = self.primary_p.currentText()
        self.config.primary_model = self.primary_m.currentText()
        self.config.browser_provider = self.browser_p.currentText()
        self.config.browser_model = self.browser_m.currentText()
        self.config.final_provider = self.final_p.currentText()
        self.config.final_model = self.final_m.currentText()
        self.config.openrouter_daily_allocation = self.or_alloc.value()
        order = []
        for x in self.priority.text().split(','):
            x = x.strip().lower()
            if x in self.broker.providers and x not in order:
                order.append(x)
        for x in ['groq', 'gemini', 'openrouter', 'codex']:
            if x not in order:
                order.append(x)
        self.config.provider_priority = order
        self.config.save()
        for provider in ('groq', 'gemini', 'openrouter'):
            p = self.broker.providers.get(provider)
            if p is not None and hasattr(p, 'pool'):
                p.pool.set_enabled_fingerprints(disabled.get(provider, []))
        self.broker.provider_priority = order
        op = self.broker.providers.get('openrouter')
        if op is not None and hasattr(op, 'ledger'):
            op.ledger.limit = min(40, self.or_alloc.value())
        self.accept()
