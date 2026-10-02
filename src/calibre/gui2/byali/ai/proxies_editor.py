#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint (PBE5 proxy library test UX → dedicated Proxies tab):
#   1) List candidates with Local / API status (green / orange / red)
#   2) Add URL → Test (two-step) against an LLM connection’s /models
#   3) Approve with a shortcut name when both probes pass
#   4) Approved named presets feed the LLM connections picker

"""Proxies tab: test proxies and approve named presets for LLM connections."""

from __future__ import annotations

from qt.core import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
    Qt,
)

from calibre.gui2 import error_dialog, info_dialog, question_dialog
from calibre.gui2.byali.ai.connections import load_connections
from calibre.gui2.byali.ai.proxy_presets import (
    ProxyPreset,
    add_proxy_candidate,
    apply_test_result,
    approve_proxy_preset,
    delete_proxy_preset,
    load_proxy_presets,
)
from calibre.gui2.byali.ai.proxy_probe import network_attempt_color, test_proxy
from calibre.gui2.byali.ai.transport import TransportError
from calibre.gui2.widgets import BusyCursor
from calibre.utils.localization import _


def _status_text(preset: ProxyPreset) -> str:
    if preset.last_test_ok is None and preset.last_local_ok is None:
        return _('Not tested')
    color = network_attempt_color(preset.last_local_ok, preset.last_api_ok)
    local = _('Local OK') if preset.last_local_ok else _('Local fail')
    api = _('API OK') if preset.last_api_ok else _('API fail')
    latency = ''
    if preset.last_test_latency_ms is not None:
        latency = f' · {preset.last_test_latency_ms} ms'
    badge = {'green': '●', 'orange': '●', 'red': '●'}.get(color, '·')
    approved = _(' · approved as “{0}”').format(preset.name) if preset.approved and preset.name else ''
    return f'{badge} {local} / {api}{latency}{approved}'


def _status_stylesheet(preset: ProxyPreset) -> str:
    if preset.last_test_ok is None and preset.last_local_ok is None:
        return ''
    color = network_attempt_color(preset.last_local_ok, preset.last_api_ok)
    return {
        'green': 'color: #1e8449;',
        'orange': 'color: #d68910;',
        'red': 'color: #c0392b;',
    }.get(color, '')


class ProxiesEditor(QWidget):
    """Manage proxy candidates, PBE5-style tests, and approved presets."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)

        left = QVBoxLayout()
        layout.addLayout(left, 1)
        tip = QLabel(
            _(
                'Add proxy URLs, test them (local reachability + API through the proxy), '
                'then approve the green ones with a short preset name. '
                'Approved presets appear in LLM connections when “Use proxy” is checked.'
            )
        )
        tip.setWordWrap(True)
        left.addWidget(tip)

        self.list = QListWidget(self)
        self.list.currentRowChanged.connect(self._load_current)
        left.addWidget(self.list, 1)

        add_row = QHBoxLayout()
        left.addLayout(add_row)
        self.url_edit = QLineEdit(self)
        self.url_edit.setPlaceholderText(_('e.g. http://host:8080'))
        self.url_edit.setMaxLength(255)
        add_row.addWidget(self.url_edit, 1)
        self.add_btn = QPushButton(_('Add'), self)
        self.add_btn.clicked.connect(self._add)
        add_row.addWidget(self.add_btn)

        right = QVBoxLayout()
        layout.addLayout(right, 2)
        form = QFormLayout()
        right.addLayout(form)

        self.detail_url = QLabel('', self)
        self.detail_url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.detail_url.setWordWrap(True)
        form.addRow(_('Proxy URL:'), self.detail_url)

        self.status_label = QLabel(_('Not tested'), self)
        self.status_label.setWordWrap(True)
        form.addRow(_('Last test:'), self.status_label)

        self.message_label = QLabel('', self)
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(_('Details:'), self.message_label)

        self.probe_conn = QComboBox(self)
        self.probe_conn.setToolTip(
            _('API probe uses this connection’s base URL and key (same idea as PBE5).')
        )
        form.addRow(_('Probe via LLM connection:'), self.probe_conn)

        actions = QHBoxLayout()
        right.addLayout(actions)
        self.test_btn = QPushButton(_('Test proxy'), self)
        self.test_btn.setToolTip(_('Step 1: TCP to proxy. Step 2: GET /models through the proxy.'))
        self.test_btn.clicked.connect(self._test_current)
        self.approve_btn = QPushButton(_('Approve as preset…'), self)
        self.approve_btn.setToolTip(_('Requires both Local and API green — then enter a short name.'))
        self.approve_btn.clicked.connect(self._approve_current)
        self.remove_btn = QPushButton(_('Remove'), self)
        self.remove_btn.clicked.connect(self._remove_current)
        actions.addWidget(self.test_btn)
        actions.addWidget(self.approve_btn)
        actions.addWidget(self.remove_btn)
        actions.addStretch()

        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText(_('Preset name, e.g. Home VPN'))
        self.name_edit.setMaxLength(120)
        form.addRow(_('Preset name:'), self.name_edit)

        note = QLabel(
            _(
                'PBE5-compatible test: green = local + API both OK; '
                'orange = local OK but API failed; red = local failed.'
            )
        )
        note.setWordWrap(True)
        right.addWidget(note)
        right.addStretch()

        self._items: list[ProxyPreset] = []
        self.refresh_probe_connections()
        self._reload_list()

    def refresh_probe_connections(self):
        current = self.probe_conn.currentData()
        self.probe_conn.blockSignals(True)
        self.probe_conn.clear()
        for conn in load_connections():
            label = conn.name
            if conn.api_url:
                label = f'{conn.name} — {conn.api_url}'
            self.probe_conn.addItem(label, conn.id)
        self.probe_conn.blockSignals(False)
        if current:
            idx = self.probe_conn.findData(current)
            if idx >= 0:
                self.probe_conn.setCurrentIndex(idx)

    def _reload_list(self, select_id: str | None = None):
        self._items = load_proxy_presets()
        self.list.clear()
        select_row = 0
        for i, preset in enumerate(self._items):
            title = preset.display_label()
            if preset.approved and preset.name:
                title = f'★ {preset.name}'
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, preset.id)
            item.setToolTip(preset.proxy_url)
            self.list.addItem(item)
            if select_id and preset.id == select_id:
                select_row = i
        if self._items:
            self.list.setCurrentRow(select_row)
        else:
            self._clear_detail()

    def _current(self) -> ProxyPreset | None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self._items):
            return None
        return self._items[row]

    def _clear_detail(self):
        self.detail_url.setText('')
        self.status_label.setText(_('Not tested'))
        self.status_label.setStyleSheet('')
        self.message_label.setText('')
        self.name_edit.clear()

    def _load_current(self, row: int):
        preset = self._current()
        if preset is None:
            self._clear_detail()
            return
        self.detail_url.setText(preset.proxy_url)
        self.status_label.setText(_status_text(preset))
        self.status_label.setStyleSheet(_status_stylesheet(preset))
        self.message_label.setText(preset.last_test_message or '')
        self.name_edit.setText(preset.name if preset.approved else '')

    def _add(self):
        raw = self.url_edit.text().strip()
        if not raw:
            return
        try:
            preset = add_proxy_candidate(raw)
        except TransportError as e:
            error_dialog(self, _('Invalid proxy'), str(e), show=True)
            return
        self.url_edit.clear()
        self._reload_list(select_id=preset.id)

    def _probe_headers_and_url(self) -> tuple[str, list[tuple[str, str]]]:
        from calibre.gui2.byali.ai.connections import get_connection
        from calibre.gui2.byali.ai.proxy_probe import models_probe_url

        conn_id = self.probe_conn.currentData()
        conn = get_connection(conn_id) if conn_id else None
        if conn is None:
            return 'https://api.openai.com/v1/models', []
        headers: list[tuple[str, str]] = list(conn.headers_tuples())
        if conn.api_key:
            if not any(k.lower() == 'authorization' for k, _ in headers):
                headers.insert(0, ('Authorization', f'Bearer {conn.api_key}'))
        return models_probe_url(conn.api_url), headers

    def _test_current(self):
        preset = self._current()
        if preset is None:
            return
        probe_url, headers = self._probe_headers_and_url()
        with BusyCursor():
            result = test_proxy(preset.proxy_url, probe_api_url=probe_url, probe_headers=headers)
            updated = apply_test_result(preset.id, result)
        self._reload_list(select_id=preset.id)
        if updated is None:
            return
        title = _('Proxy test passed') if result.get('ok') else _('Proxy test finished')
        info_dialog(self, title, result.get('message') or '', show=True, show_copy_button=False)

    def _approve_current(self):
        preset = self._current()
        if preset is None:
            return
        name = self.name_edit.text().strip()
        if not name:
            error_dialog(
                self,
                _('Preset name required'),
                _('Enter a short preset name (e.g. Home VPN) before approving.'),
                show=True,
            )
            return
        try:
            approve_proxy_preset(preset.id, name)
        except ValueError as e:
            error_dialog(self, _('Cannot approve'), str(e), show=True)
            return
        self._reload_list(select_id=preset.id)
        info_dialog(
            self,
            _('Proxy approved'),
            _('“{0}” is now available in LLM connections when Use proxy is checked.').format(name),
            show=True,
            show_copy_button=False,
        )

    def _remove_current(self):
        preset = self._current()
        if preset is None:
            return
        label = preset.display_label()
        if not question_dialog(self, _('Remove proxy'), _('Remove “{0}” from the Proxies list?').format(label)):
            return
        delete_proxy_preset(preset.id)
        self._reload_list()
