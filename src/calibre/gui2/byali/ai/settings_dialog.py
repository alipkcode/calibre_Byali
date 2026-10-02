#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   Tab 1 — ConnectionsEditor: OpenAI-compatible endpoints + Use-proxy checkbox
#   Tab 2 — ProxiesEditor: test proxies (PBE5 two-step) + approve named presets
#   Tab 3 — AgentsEditor: prompts + per-agent connection/model/temp/reasoning/search

"""Settings dialog for Byali multi-connection LLM setup and rewrite agents."""

from __future__ import annotations

from qt.core import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPoint,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
    Qt,
)

from calibre.gui2 import error_dialog, info_dialog
from calibre.gui2.byali.ai.agents import (
    OUTPUT_MODE_CHOICES,
    OUTPUT_MODE_NOTE,
    OUTPUT_MODE_REPLACE,
    REASONING_CHOICES,
    Agent,
    delete_agent,
    ensure_defaults_seeded,
    load_agents,
    new_agent_template,
    upsert_agent,
)
from calibre.gui2.byali.ai.connection_presets import (
    ConnectionPreset,
    connection_from_preset,
    connection_presets,
)
from calibre.gui2.byali.ai.connections import (
    Connection,
    delete_connection,
    fetch_models,
    load_connections,
    new_connection_template,
    test_connection,
    upsert_connection,
)
from calibre.gui2.byali.ai.network_library import KIND_DNS, ensure_network_library_seeded
from calibre.gui2.byali.ai.network_library_dialog import NetworkLibraryDialog
from calibre.gui2.byali.ai.proxies_editor import ProxiesEditor
from calibre.gui2.byali.ai.proxy_presets import (
    ensure_legacy_proxy_migrated,
    list_approved_proxy_presets,
)
from calibre.gui2.byali.ai.transport import TransportError
from calibre.gui2.widgets import BusyCursor
from calibre.gui2.widgets2 import Dialog
from calibre.utils.localization import _


class ConnectionsEditor(QWidget):
    """Manage multiple named LLM API connections."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)

        left = QVBoxLayout()
        layout.addLayout(left, 1)
        self.list = QListWidget(self)
        self.list.currentRowChanged.connect(self._load_current)
        left.addWidget(self.list)
        row = QHBoxLayout()
        left.addLayout(row)
        self.add_btn = QPushButton(_('Add'), self)
        self.del_btn = QPushButton(_('Remove'), self)
        self.add_btn.setToolTip(_('Add a connection — choose a major provider or Custom'))
        self.add_btn.clicked.connect(self._show_add_menu)
        self.del_btn.clicked.connect(self._delete_current)
        row.addWidget(self.add_btn)
        row.addWidget(self.del_btn)

        right = QVBoxLayout()
        layout.addLayout(right, 2)
        form = QFormLayout()
        right.addLayout(form)

        self.name_edit = QLineEdit(self)
        self.url_edit = QLineEdit(self)
        self.url_edit.setPlaceholderText(_('e.g. https://api.openai.com/v1 or http://localhost:11434/v1'))
        self.key_edit = QLineEdit(self)
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText(_('Optional API key (Bearer)'))
        self.headers_edit = QPlainTextEdit(self)
        self.headers_edit.setPlaceholderText(_('Optional headers, one per line: Header-Name: Value'))
        self.headers_edit.setMaximumHeight(80)
        self.timeout_sb = QSpinBox(self)
        self.timeout_sb.setRange(15, 600)
        self.timeout_sb.setSuffix(_(' seconds'))

        # DNS gate (optional) — still per-connection
        self.dns_edit = QLineEdit(self)
        self.dns_edit.setPlaceholderText(_('e.g. 8.8.8.8'))
        self.dns_edit.setMaxLength(120)
        self.dns_edit.setClearButtonEnabled(True)
        dns_row = QHBoxLayout()
        dns_row.addWidget(self.dns_edit, 1)
        self.dns_lib_btn = QPushButton(_('Library'), self)
        self.dns_lib_btn.setToolTip(_('Open DNS library'))
        self.dns_lib_btn.clicked.connect(lambda: self._open_network_library(KIND_DNS))
        dns_row.addWidget(self.dns_lib_btn)
        dns_wrap = QWidget(self)
        dns_wrap.setLayout(dns_row)

        # Proxy: checkbox + approved presets from the Proxies tab
        self.use_proxy_cb = QCheckBox(_('Use a proxy for this LLM connection'), self)
        self.use_proxy_cb.toggled.connect(self._on_use_proxy_toggled)
        self.proxy_preset_combo = QComboBox(self)
        self.proxy_preset_combo.setEnabled(False)
        self.proxy_preset_combo.setToolTip(
            _('Only proxies that were tested green and approved with a preset name on the Proxies tab.')
        )

        form.addRow(_('Name:'), self.name_edit)
        form.addRow(_('API &URL:'), self.url_edit)
        form.addRow(_('API &key:'), self.key_edit)
        form.addRow(_('HTTP &headers:'), self.headers_edit)
        form.addRow(_('&Timeout:'), self.timeout_sb)
        form.addRow(_('DNS gate (optional):'), dns_wrap)
        form.addRow(self.use_proxy_cb)
        form.addRow(_('Proxy preset:'), self.proxy_preset_combo)

        tip = QLabel(
            _(
                'Each connection is an OpenAI-compatible endpoint. '
                'Works with OpenAI, OpenRouter, Ollama (/v1), LM Studio, and similar gateways.\n'
                'DNS gate: resolve the provider hostname via this DNS server (leave empty for system DNS).\n'
                'Proxy: manage and test proxies on the Proxies tab, then enable the checkbox and pick an approved preset.'
            )
        )
        tip.setWordWrap(True)
        right.addWidget(tip)

        actions = QHBoxLayout()
        right.addLayout(actions)
        self.save_btn = QPushButton(_('Save connection'), self)
        self.refresh_btn = QPushButton(_('Refresh models'), self)
        self.test_btn = QPushButton(_('Test connection'), self)
        self.save_btn.clicked.connect(self._save_current)
        self.refresh_btn.clicked.connect(self._refresh_models)
        self.test_btn.clicked.connect(self._test_connection)
        actions.addWidget(self.save_btn)
        actions.addWidget(self.refresh_btn)
        actions.addWidget(self.test_btn)
        actions.addStretch()

        self.status = QLabel('', self)
        self.status.setWordWrap(True)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        right.addWidget(self.status)

        self.models_label = QLabel(_('Models: (none loaded yet)'), self)
        self.models_label.setWordWrap(True)
        right.addWidget(self.models_label)
        right.addStretch()

        self._items: list[Connection] = []
        ensure_network_library_seeded()
        self.refresh_proxy_presets()
        self._reload_list()

    def _open_network_library(self, kind: str):
        d = NetworkLibraryDialog(kind, self)
        if d.exec() and d.selected_value:
            if kind == KIND_DNS:
                self.dns_edit.setText(d.selected_value)

    def refresh_proxy_presets(self, keep_id: str | None = None):
        """Reload approved proxy presets into the connection picker."""
        current = keep_id if keep_id is not None else self.proxy_preset_combo.currentData()
        self.proxy_preset_combo.blockSignals(True)
        self.proxy_preset_combo.clear()
        self.proxy_preset_combo.addItem(_('(select an approved proxy)'), '')
        for preset in list_approved_proxy_presets():
            self.proxy_preset_combo.addItem(f'{preset.name} — {preset.proxy_url}', preset.id)
        self.proxy_preset_combo.blockSignals(False)
        if current:
            idx = self.proxy_preset_combo.findData(current)
            if idx >= 0:
                self.proxy_preset_combo.setCurrentIndex(idx)

    def _on_use_proxy_toggled(self, checked: bool):
        self.proxy_preset_combo.setEnabled(bool(checked))
        if checked:
            self.refresh_proxy_presets()

    def _reload_list(self, select_id: str | None = None):
        self._items = load_connections()
        self.list.clear()
        select_row = 0
        for i, conn in enumerate(self._items):
            item = QListWidgetItem(conn.name)
            item.setData(Qt.ItemDataRole.UserRole, conn.id)
            self.list.addItem(item)
            if select_id and conn.id == select_id:
                select_row = i
        if self._items:
            self.list.setCurrentRow(select_row)

    def _current(self) -> Connection | None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self._items):
            return None
        return self._items[row]

    def _parse_headers(self) -> list[list[str]]:
        ans = []
        for line in self.headers_edit.toPlainText().splitlines():
            line = line.strip()
            if not line:
                continue
            key, sep, val = line.partition(':')
            if key.strip() and sep and val.strip():
                ans.append([key.strip(), val.strip()])
        return ans

    def _load_current(self, row: int):
        conn = self._current()
        if conn is None:
            return
        self.name_edit.setText(conn.name)
        self.url_edit.setText(conn.api_url)
        self.key_edit.setText(conn.api_key)
        self.headers_edit.setPlainText('\n'.join(f'{a}: {b}' for a, b in conn.headers))
        self.timeout_sb.setValue(int(conn.timeout or 120))
        self.dns_edit.setText(conn.dns_server or '')
        # Migrate legacy raw proxy_url into an approved preset once
        preset_id = conn.proxy_preset_id or ''
        if conn.use_proxy and not preset_id and conn.proxy_url.strip():
            migrated = ensure_legacy_proxy_migrated(conn.proxy_url, suggested_name=conn.name or _('Migrated proxy'))
            if migrated is not None:
                preset_id = migrated.id
                conn.proxy_preset_id = migrated.id
                conn.use_proxy = True
        self.refresh_proxy_presets(keep_id=preset_id)
        self.use_proxy_cb.blockSignals(True)
        self.use_proxy_cb.setChecked(bool(conn.use_proxy))
        self.use_proxy_cb.blockSignals(False)
        self.proxy_preset_combo.setEnabled(bool(conn.use_proxy))
        self._update_models_label(conn.cached_models)
        self.status.setText('')

    def _update_models_label(self, models: list[str]):
        if not models:
            self.models_label.setText(_('Models: (none loaded yet)'))
            self.models_label.setToolTip('')
            return
        sample = ', '.join(models[:8])
        more = _(' … (+{} more)').format(len(models) - 8) if len(models) > 8 else ''
        self.models_label.setText(_('Models ({0}): {1}{2}').format(len(models), sample, more))
        self.models_label.setToolTip('\n'.join(models))

    def _collect_into(self, conn: Connection) -> Connection:
        conn.name = self.name_edit.text().strip() or conn.name
        conn.api_url = self.url_edit.text().strip()
        conn.api_key = self.key_edit.text().strip()
        conn.headers = self._parse_headers()
        conn.timeout = self.timeout_sb.value()
        conn.dns_server = self.dns_edit.text().strip()
        conn.use_proxy = self.use_proxy_cb.isChecked()
        conn.proxy_preset_id = self.proxy_preset_combo.currentData() or ''
        if not conn.use_proxy:
            conn.proxy_preset_id = ''
        return conn

    def _show_add_menu(self):
        """Drop-up menu of major providers + Custom (same blank template as before)."""
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        presets = connection_presets()
        # Cloud / gateway presets first; Custom last (already ordered that way)
        for preset in presets[:-1]:
            act = menu.addAction(preset.label)
            if preset.tip:
                act.setToolTip(preset.tip)
                act.setStatusTip(preset.tip)
            act.triggered.connect(lambda *_a, p=preset: self._add_from_preset(p))
        menu.addSeparator()
        custom = presets[-1]
        custom_act = menu.addAction(custom.label)
        if custom.tip:
            custom_act.setToolTip(custom.tip)
        custom_act.triggered.connect(lambda *_a, p=custom: self._add_from_preset(p))

        # Drop-up: open above the Add button
        menu.adjustSize()
        btn_top_left = self.add_btn.mapToGlobal(QPoint(0, 0))
        pos = QPoint(btn_top_left.x(), btn_top_left.y() - menu.sizeHint().height())
        menu.exec(pos)

    def _apply_preset_hints(self, preset: ConnectionPreset):
        self.key_edit.setPlaceholderText(preset.key_placeholder or _('Optional API key (Bearer)'))
        if preset.tip:
            self.status.setStyleSheet('')
            self.status.setText(preset.tip)

    def _add_from_preset(self, preset: ConnectionPreset):
        if preset.is_custom:
            conn = new_connection_template()
        else:
            conn = connection_from_preset(preset)
        upsert_connection(conn)
        self._reload_list(select_id=conn.id)
        self._apply_preset_hints(preset)

    def _add(self):
        # Kept for compatibility; Add button now uses the preset drop-up menu.
        self._add_from_preset(connection_presets()[-1])

    def _delete_current(self):
        conn = self._current()
        if conn is None:
            return
        delete_connection(conn.id)
        self._reload_list()

    def _save_current(self):
        conn = self._current()
        if conn is None:
            return
        self._collect_into(conn)
        if not conn.api_url:
            error_dialog(self, _('Missing API URL'), _('Enter an API URL for this connection.'), show=True)
            return
        try:
            upsert_connection(conn)
        except TransportError as e:
            error_dialog(self, _('Invalid networking'), str(e), show=True)
            return
        self._reload_list(select_id=conn.id)
        self.status.setText(_('Connection saved.'))

    def _refresh_models(self):
        conn = self._current()
        if conn is None:
            return
        self._collect_into(conn)
        try:
            upsert_connection(conn)
        except TransportError as e:
            error_dialog(self, _('Invalid networking'), str(e), show=True)
            return
        with BusyCursor():
            try:
                models = fetch_models(conn)
            except Exception as e:
                self.status.setText(_('Failed to load models: {}').format(e))
                self.status.setStyleSheet('color: #c0392b;')
                error_dialog(self, _('Model list failed'), _('Could not load models from the API.'), det_msg=str(e), show=True)
                return
        self.status.setStyleSheet('')
        self.status.setText(_('Loaded {} models from the API.').format(len(models)))
        self._update_models_label(models)
        self._reload_list(select_id=conn.id)

    def _test_connection(self):
        conn = self._current()
        if conn is None:
            return
        self._collect_into(conn)
        try:
            upsert_connection(conn)
        except TransportError as e:
            error_dialog(self, _('Invalid networking'), str(e), show=True)
            return
        with BusyCursor():
            try:
                msg = test_connection(conn)
            except Exception as e:
                self.status.setStyleSheet('color: #c0392b;')
                self.status.setText(_('Connection test failed: {}').format(e))
                error_dialog(
                    self,
                    _('Connection test failed'),
                    _('The API connection could not be verified. Check URL, key, proxy, DNS, and network.'),
                    det_msg=str(e),
                    show=True,
                )
                return
        self.status.setStyleSheet('color: #1e8449;')
        self.status.setText(msg)
        self._update_models_label(conn.cached_models)
        info_dialog(self, _('Connection OK'), msg, show=True, show_copy_button=False)
        self._reload_list(select_id=conn.id)


class AgentsEditor(QWidget):
    """Edit rewrite agents, including per-agent LLM options."""

    def __init__(self, parent=None):
        super().__init__(parent)
        ensure_defaults_seeded()
        layout = QHBoxLayout(self)
        self.list = QListWidget(self)
        self.list.currentRowChanged.connect(self._load_current)
        layout.addWidget(self.list, 1)

        right = QVBoxLayout()
        layout.addLayout(right, 2)
        form = QFormLayout()
        right.addLayout(form)

        self.name_edit = QLineEdit(self)
        self.system_edit = QPlainTextEdit(self)
        self.system_edit.setPlaceholderText(_('System instructions for this agent'))
        self.user_edit = QPlainTextEdit(self)
        self.user_edit.setPlaceholderText(_('User prompt template. Use {selected} for the selected text.'))

        self.connection_combo = QComboBox(self)
        self.model_combo = QComboBox(self)
        self.model_combo.setEditable(True)
        self.model_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.refresh_models_btn = QPushButton(_('Refresh'), self)
        self.refresh_models_btn.clicked.connect(self._refresh_models_for_connection)
        model_row = QWidget(self)
        mh = QHBoxLayout(model_row)
        mh.setContentsMargins(0, 0, 0, 0)
        mh.addWidget(self.model_combo, 1)
        mh.addWidget(self.refresh_models_btn)

        self.temp_sb = QDoubleSpinBox(self)
        self.temp_sb.setRange(0.0, 2.0)
        self.temp_sb.setSingleStep(0.1)
        self.temp_sb.setToolTip(_('Lower = more deterministic; higher = more creative.'))

        self.reasoning_combo = QComboBox(self)
        labels = {
            'auto': _('Automatic'),
            'none': _('None'),
            'low': _('Low'),
            'medium': _('Medium'),
            'high': _('High'),
        }
        for key in REASONING_CHOICES:
            self.reasoning_combo.addItem(labels[key], key)

        self.web_search_cb = QCheckBox(_('Allow web search / up-to-date retrieval when available'), self)

        self.output_mode_combo = QComboBox(self)
        self.output_mode_combo.addItem(_('Replace selection (rewrite)'), OUTPUT_MODE_REPLACE)
        self.output_mode_combo.addItem(_('Attach as note (keep text + icon)'), OUTPUT_MODE_NOTE)
        self.output_mode_combo.setToolTip(
            _(
                'Replace: swaps the selected text with the agent output (with an undo “i” control).\n'
                'Note: keeps the original passage and adds a note icon you can open while reading.'
            )
        )

        form.addRow(_('Name:'), self.name_edit)
        form.addRow(_('&Output mode:'), self.output_mode_combo)
        form.addRow(_('System prompt:'), self.system_edit)
        form.addRow(_('User template:'), self.user_edit)
        form.addRow(_('LLM &connection:'), self.connection_combo)
        form.addRow(_('&Model:'), model_row)
        form.addRow(_('&Temperature:'), self.temp_sb)
        form.addRow(_('&Reasoning effort:'), self.reasoning_combo)
        form.addRow('', self.web_search_cb)

        tip = QLabel(
            _(
                'Model / temperature / reasoning / search are configured per agent. Connections are shared.\n'
                'Note agents never rewrite the page — they save a note and show an “N” icon on the passage.'
            )
        )
        tip.setWordWrap(True)
        right.addWidget(tip)

        buttons = QHBoxLayout()
        right.addLayout(buttons)
        self.add_btn = QPushButton(_('Add agent'), self)
        self.save_btn = QPushButton(_('Save agent'), self)
        self.del_btn = QPushButton(_('Delete / disable'), self)
        self.add_btn.setToolTip(_('Add a rewrite agent or a note agent'))
        self.add_btn.clicked.connect(self._show_add_menu)
        self.save_btn.clicked.connect(self._save_current)
        self.del_btn.clicked.connect(self._delete_current)
        buttons.addWidget(self.add_btn)
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.del_btn)
        buttons.addStretch()

        self.connection_combo.currentIndexChanged.connect(self._on_connection_changed)
        self._agents: list[Agent] = []
        self._fill_connections()
        self._reload_list()

    def _fill_connections(self, select_id: str | None = None):
        self.connection_combo.blockSignals(True)
        self.connection_combo.clear()
        for conn in load_connections():
            self.connection_combo.addItem(conn.name, conn.id)
        if select_id:
            idx = self.connection_combo.findData(select_id)
            if idx >= 0:
                self.connection_combo.setCurrentIndex(idx)
        self.connection_combo.blockSignals(False)

    def refresh_connection_list(self):
        current = self.connection_combo.currentData()
        self._fill_connections(select_id=current)
        self._populate_models_from_connection()

    def _reload_list(self, select_id: str | None = None):
        self._agents = load_agents(include_disabled=True)
        self.list.clear()
        select_row = 0
        for i, agent in enumerate(self._agents):
            kind = _('note') if agent.is_note_agent else _('rewrite')
            label = f'{agent.name} [{kind}]'
            if not agent.enabled:
                label += f' ({_("disabled")})'
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, agent.id)
            self.list.addItem(item)
            if select_id and agent.id == select_id:
                select_row = i
        if self._agents:
            self.list.setCurrentRow(select_row)

    def _current_agent(self) -> Agent | None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self._agents):
            return None
        return self._agents[row]

    def _populate_models_from_connection(self, preferred_model: str = ''):
        conn_id = self.connection_combo.currentData() or ''
        models = []
        for conn in load_connections():
            if conn.id == conn_id:
                models = list(conn.cached_models or [])
                break
        current = preferred_model or self.model_combo.currentText().strip()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for mid in models:
            self.model_combo.addItem(mid)
        if current:
            self.model_combo.setCurrentText(current)
        elif models:
            self.model_combo.setCurrentText(models[0])
        self.model_combo.blockSignals(False)

    def _on_connection_changed(self, *_args):
        self._populate_models_from_connection()

    def _refresh_models_for_connection(self):
        conn_id = self.connection_combo.currentData()
        conn = None
        for c in load_connections():
            if c.id == conn_id:
                conn = c
                break
        if conn is None:
            error_dialog(self, _('No connection'), _('Select an LLM connection first.'), show=True)
            return
        with BusyCursor():
            try:
                models = fetch_models(conn)
            except Exception as e:
                error_dialog(self, _('Model list failed'), _('Could not load models from the API.'), det_msg=str(e), show=True)
                return
        self._populate_models_from_connection(preferred_model=self.model_combo.currentText().strip())
        info_dialog(self, _('Models loaded'), _('Loaded {} models.').format(len(models)), show=True, show_copy_button=False)

    def _load_current(self, row: int):
        agent = self._current_agent()
        if agent is None:
            return
        self.name_edit.setText(agent.name)
        self.system_edit.setPlainText(agent.system_prompt)
        self.user_edit.setPlainText(agent.user_template)
        mode_idx = self.output_mode_combo.findData(agent.output_mode)
        self.output_mode_combo.setCurrentIndex(max(0, mode_idx))
        self._fill_connections(select_id=agent.connection_id or None)
        self._populate_models_from_connection(preferred_model=agent.model)
        self.temp_sb.setValue(float(agent.temperature))
        idx = self.reasoning_combo.findData(agent.reasoning_strategy)
        self.reasoning_combo.setCurrentIndex(max(0, idx))
        self.web_search_cb.setChecked(bool(agent.allow_web_searches))

    def _show_add_menu(self):
        menu = QMenu(self)
        menu.addAction(_('Rewrite agent…'), lambda: self._add(OUTPUT_MODE_REPLACE))
        menu.addAction(_('Note agent…'), lambda: self._add(OUTPUT_MODE_NOTE))
        menu.exec(self.add_btn.mapToGlobal(QPoint(0, self.add_btn.height())))

    def _add(self, output_mode: str = OUTPUT_MODE_REPLACE):
        agent = new_agent_template(output_mode=output_mode)
        upsert_agent(agent)
        self._reload_list(select_id=agent.id)

    def _save_current(self):
        agent = self._current_agent()
        if agent is None:
            return
        agent.name = self.name_edit.text().strip() or agent.name
        agent.system_prompt = self.system_edit.toPlainText()
        agent.user_template = self.user_edit.toPlainText() or '{selected}'
        mode = self.output_mode_combo.currentData() or OUTPUT_MODE_REPLACE
        if mode not in OUTPUT_MODE_CHOICES:
            mode = OUTPUT_MODE_REPLACE
        agent.output_mode = mode
        agent.enabled = True
        agent.connection_id = self.connection_combo.currentData() or ''
        agent.model = self.model_combo.currentText().strip()
        agent.temperature = float(self.temp_sb.value())
        agent.reasoning_strategy = self.reasoning_combo.currentData() or 'auto'
        agent.allow_web_searches = self.web_search_cb.isChecked()
        upsert_agent(agent)
        self._reload_list(select_id=agent.id)

    def _delete_current(self):
        agent = self._current_agent()
        if agent is None:
            return
        if agent.builtin:
            agent.enabled = False
            upsert_agent(agent)
        else:
            delete_agent(agent.id)
        self._reload_list()


class ByaliAISettingsDialog(Dialog):
    def __init__(self, parent=None):
        super().__init__(_('Byali AI Settings'), 'byali-ai-settings', parent=parent)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs)

        self.connections_editor = ConnectionsEditor(self)
        self.tabs.addTab(self.connections_editor, _('LLM connections'))

        self.proxies_editor = ProxiesEditor(self)
        self.tabs.addTab(self.proxies_editor, _('Proxies'))

        self.agents_editor = AgentsEditor(self)
        self.tabs.addTab(self.agents_editor, _('AI Agents'))
        self.tabs.currentChanged.connect(self._tab_changed)

        tools = QHBoxLayout()
        layout.addLayout(tools)
        self.debug_log_btn = QPushButton(_('Byali Debug Log…'), self)
        self.debug_log_btn.setToolTip(_('View detailed AI rewrite / note attach diagnostics'))
        self.debug_log_btn.clicked.connect(self._open_debug_log)
        tools.addWidget(self.debug_log_btn)
        tools.addStretch()

        layout.addWidget(self.bb)

    def _open_debug_log(self):
        from calibre.gui2.byali.ai.debug_log_dialog import ByaliDebugLogDialog

        ByaliDebugLogDialog(self).exec()

    def _tab_changed(self, index: int):
        # Refresh cross-tab pickers when switching
        if self.tabs.widget(index) is self.agents_editor:
            self.agents_editor.refresh_connection_list()
        elif self.tabs.widget(index) is self.connections_editor:
            self.connections_editor.refresh_proxy_presets()
        elif self.tabs.widget(index) is self.proxies_editor:
            self.proxies_editor.refresh_probe_connections()

    def accept(self):
        self.connections_editor._save_current()
        self.agents_editor._save_current()
        super().accept()
