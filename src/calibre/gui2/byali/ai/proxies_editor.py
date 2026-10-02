#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint (premium Proxies tab — PBE5 network library UX):
#   1) Shelf Active/Archived/All + search/result/rate/source filters
#   2) Paste-list dialog (clipboard extract + scheme + skip dupes)
#   3) Test-library dialog (scope cards + min success rate)
#   4) Batch test worker with Stop; archive matching / failed
#   5) Approve green proxies as named presets for LLM connections

"""Proxies tab: PBE5-parity filter, paste-batch, and scoped batch testing."""

from __future__ import annotations

from qt.core import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QObject,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QThread,
    QVBoxLayout,
    QWidget,
    Qt,
    pyqtSignal,
)

from calibre.gui2 import error_dialog, info_dialog, question_dialog, warning_dialog
from calibre.gui2.byali.ai.connections import get_connection, load_connections
from calibre.gui2.byali.ai.proxy_paste import (
    extract_proxies_from_text,
    formatted_ready_urls,
    pick_paste_scheme,
    schemes_in_text,
)
from calibre.gui2.byali.ai.proxy_presets import (
    SHELF_ACTIVE,
    SHELF_ALL,
    SHELF_ARCHIVED,
    ProxyPreset,
    add_proxy_candidate,
    apply_test_result,
    approve_proxy_preset,
    batch_add_proxy_urls,
    bulk_set_archived,
    delete_proxy_preset,
    entries_for_test,
    filter_presets,
    is_failed_entry,
    list_by_shelf,
    set_archived,
    shelf_counts,
)
from calibre.gui2.byali.ai.proxy_probe import models_probe_url, test_proxy
from calibre.gui2.byali.ai.transport import TransportError
from calibre.gui2.widgets2 import Dialog
from calibre.utils.localization import _


# ---------------------------------------------------------------------------
# Batch test worker (keeps UI responsive)
# ---------------------------------------------------------------------------
class _BatchTestWorker(QObject):
    progress = pyqtSignal(int, int, str)  # i, total, status
    one_done = pyqtSignal(object, object)  # preset_id, result dict
    finished = pyqtSignal(int, int, bool)  # ok_count, ran, aborted

    def __init__(self, preset_ids, probe_url, headers):
        super().__init__()
        self.preset_ids = list(preset_ids)
        self.probe_url = probe_url
        self.headers = headers
        self.abort = False

    def run(self):
        ok_count = ran = 0
        total = len(self.preset_ids)
        for i, pid in enumerate(self.preset_ids):
            if self.abort:
                break
            from calibre.gui2.byali.ai.proxy_presets import get_proxy_preset

            preset = get_proxy_preset(pid)
            if preset is None:
                continue
            self.progress.emit(i + 1, total, preset.proxy_url)
            try:
                result = test_proxy(
                    preset.proxy_url,
                    probe_api_url=self.probe_url,
                    probe_headers=self.headers,
                )
                apply_test_result(pid, result)
                ran += 1
                if result.get('ok'):
                    ok_count += 1
                self.one_done.emit(pid, result)
            except Exception as e:  # noqa: BLE001
                self.one_done.emit(pid, {'ok': False, 'message': str(e)})
        self.finished.emit(ok_count, ran, bool(self.abort))


# ---------------------------------------------------------------------------
# Paste list dialog
# ---------------------------------------------------------------------------
class ProxyPasteDialog(Dialog):
    def __init__(self, parent=None):
        self.added_count = 0
        self._ready_urls: list[str] = []
        super().__init__(_('Paste proxy list'), 'byali-proxy-paste', parent)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(
            _('Paste any text or table. IP, port, and protocol are detected even when the page wraps.')
        ))
        self.text = QPlainTextEdit(self)
        self.text.setPlaceholderText(
            'http://1.2.3.4:8080\n'
            '5.6.7.8 3128\n'
            'socks5://user:pass@host:1080'
        )
        self.text.textChanged.connect(self._refresh_preview)
        layout.addWidget(self.text, 1)

        self.stats = QLabel('', self)
        layout.addWidget(self.stats)

        scheme_row = QHBoxLayout()
        layout.addLayout(scheme_row)
        scheme_row.addWidget(QLabel(_('Default protocol (for host:port lines):')))
        self.scheme = QComboBox(self)
        for s in ('http', 'https', 'socks4', 'socks5', 'socks5h'):
            self.scheme.addItem(s.upper(), s)
        self.scheme.currentIndexChanged.connect(self._refresh_preview)
        scheme_row.addWidget(self.scheme)
        scheme_row.addStretch()

        self.skip_dupes = QCheckBox(_('Skip duplicates already in the library (same host:port)'), self)
        self.skip_dupes.setChecked(True)
        self.skip_dupes.toggled.connect(self._refresh_preview)
        layout.addWidget(self.skip_dupes)

        self.preview = QPlainTextEdit(self)
        self.preview.setReadOnly(True)
        self.preview.setMaximumHeight(120)
        layout.addWidget(self.preview)

        self.add_btn = QPushButton(_('Add to library'), self)
        self.add_btn.setEnabled(False)
        self.add_btn.clicked.connect(self._add)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, self)
        bb.rejected.connect(self.reject)
        bb.addButton(self.add_btn, QDialogButtonBox.ButtonRole.AcceptRole)
        layout.addWidget(bb)
        self._refresh_preview()

    def _items_and_urls(self):
        items, truncated = extract_proxies_from_text(self.text.toPlainText())
        # Prefer scheme detected in text, else combo
        detected = pick_paste_scheme(schemes_in_text(self.text.toPlainText()))
        scheme = detected or (self.scheme.currentData() or 'http')
        need_scheme = sum(1 for i in items if not i.scheme)
        urls, dupes = formatted_ready_urls(items, scheme, skip_dupes=self.skip_dupes.isChecked())
        return items, urls, dupes, truncated, need_scheme, scheme

    def _refresh_preview(self):
        items, urls, dupes, truncated, need_scheme, scheme = self._items_and_urls()
        parts = [
            _('Found: {0}').format(len(items)),
            _('Ready: {0}').format(len(urls)),
        ]
        if need_scheme:
            parts.append(_('Need scheme: {0}').format(need_scheme))
        if dupes:
            parts.append(_('Duplicates skipped: {0}').format(dupes))
        if truncated:
            parts.append(_('Truncated at {0}').format(200))
        self.stats.setText(' · '.join(parts) + f' · scheme={scheme}')
        self.preview.setPlainText('\n'.join(urls[:40]) + ('\n…' if len(urls) > 40 else ''))
        self.add_btn.setEnabled(bool(urls))
        self._ready_urls = urls

    def _add(self):
        urls = getattr(self, '_ready_urls', [])
        if not urls:
            return
        added, skipped = batch_add_proxy_urls(urls, source='paste')
        self.added_count = added
        info_dialog(
            self,
            _('Library updated'),
            _('Added {0} prox{1}; skipped {2}.').format(added, _('y') if added == 1 else _('ies'), skipped),
            show=True,
            show_copy_button=False,
        )
        self.accept()


# ---------------------------------------------------------------------------
# Test library (scoped) dialog
# ---------------------------------------------------------------------------
class ProxyTestScopeDialog(Dialog):
    def __init__(self, parent, shelf_entries: list[ProxyPreset], visible: list[ProxyPreset]):
        self.shelf_entries = shelf_entries
        self.visible = visible
        self.selected_ids: list[str] = []
        super().__init__(_('Test library'), 'byali-proxy-test-scope', parent)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_('Choose who to probe. Each run adds one history dot.')))

        layout.addWidget(QLabel(_('Which entries')))
        self.scope_group = QButtonGroup(self)
        self.scope_radios = {}
        scopes = [
            ('all', _('All entries'), _('Every item on this shelf')),
            ('green', _('Latest green'), _('Last test passed locally and through the API')),
            ('orange', _('Latest orange'), _('Only one of the two tests passed')),
            ('red', _('Latest red'), _('Both tests failed last time')),
            ('local-fail', _('Local failed'), _('Could not reach the proxy')),
            ('api-fail', _('API failed'), _('Reached locally, but the API path failed')),
            ('untested', _('Untested'), _('Never tested yet')),
            ('visible', _('Currently visible'), _('Only the rows shown by the current list filter')),
        ]
        for key, title, desc in scopes:
            rb = QRadioButton(f'{title}', self)
            rb.setToolTip(desc)
            self.scope_group.addButton(rb)
            self.scope_radios[key] = rb
            layout.addWidget(rb)
        self.scope_radios['all'].setChecked(True)

        rate_row = QHBoxLayout()
        layout.addLayout(rate_row)
        rate_row.addWidget(QLabel(_('Minimum success rate:')))
        self.rate = QComboBox(self)
        self.rate.addItem(_('Any'), '')
        for v in ('25', '50', '75', '100'):
            self.rate.addItem(f'≥ {v}%', v)
        self.rate.addItem(_('Custom…'), 'custom')
        rate_row.addWidget(self.rate)
        self.custom_pct = QSpinBox(self)
        self.custom_pct.setRange(0, 100)
        self.custom_pct.setSuffix('%')
        self.custom_pct.setEnabled(False)
        self.rate.currentIndexChanged.connect(
            lambda: self.custom_pct.setEnabled(self.rate.currentData() == 'custom')
        )
        rate_row.addWidget(self.custom_pct)
        rate_row.addStretch()

        self.count_label = QLabel('', self)
        layout.addWidget(self.count_label)
        for rb in self.scope_radios.values():
            rb.toggled.connect(self._update_count)
        self.rate.currentIndexChanged.connect(self._update_count)
        self.custom_pct.valueChanged.connect(self._update_count)

        bb = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        self.start_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        self.start_btn.setText(_('Start test'))
        layout.addWidget(bb)
        self._update_count()

    def _scope_key(self) -> str:
        for key, rb in self.scope_radios.items():
            if rb.isChecked():
                return key
        return 'all'

    def _matched(self) -> list[ProxyPreset]:
        return entries_for_test(
            self.shelf_entries,
            self._scope_key(),
            visible=self.visible,
            min_pct=self.rate.currentData() or '',
            min_pct_custom=str(self.custom_pct.value()),
        )

    def _update_count(self):
        n = len(self._matched())
        self.count_label.setText(_('{0} proxies will be tested').format(n))
        self.start_btn.setEnabled(n > 0)

    def _accept(self):
        matched = self._matched()
        if not matched:
            warning_dialog(self, _('Nothing to test'), _('No entries match these conditions.'), show=True)
            return
        self.selected_ids = [p.id for p in matched]
        self.accept()


# ---------------------------------------------------------------------------
# Main Proxies editor
# ---------------------------------------------------------------------------
class ProxiesEditor(QWidget):
    """Manage proxy candidates with PBE5-parity filters, paste, and batch test."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._shelf = SHELF_ACTIVE
        self._testing = False
        self._worker = None
        self._thread = None
        self._items: list[ProxyPreset] = []

        root = QVBoxLayout(self)

        tip = QLabel(
            _(
                'Add or paste proxies, filter and batch-test them (local + API), '
                'then approve the green ones with a preset name for LLM connections.'
            )
        )
        tip.setWordWrap(True)
        root.addWidget(tip)

        # Shelf
        shelf_row = QHBoxLayout()
        root.addLayout(shelf_row)
        self.shelf_active = QPushButton(_('Active'), self)
        self.shelf_archived = QPushButton(_('Archived'), self)
        self.shelf_all = QPushButton(_('All'), self)
        for btn, key in (
            (self.shelf_active, SHELF_ACTIVE),
            (self.shelf_archived, SHELF_ARCHIVED),
            (self.shelf_all, SHELF_ALL),
        ):
            btn.setCheckable(True)
            btn.clicked.connect(lambda *_a, k=key: self._set_shelf(k))
            shelf_row.addWidget(btn)
        self.shelf_counts_label = QLabel('', self)
        shelf_row.addWidget(self.shelf_counts_label)
        shelf_row.addStretch()
        self.shelf_active.setChecked(True)

        # Filters
        filt = QHBoxLayout()
        root.addLayout(filt)
        self.search = QLineEdit(self)
        self.search.setPlaceholderText(_('Filter by name or URL…'))
        self.search.textChanged.connect(self._reload_list)
        filt.addWidget(self.search, 2)

        self.result_filter = QComboBox(self)
        for key, label in (
            ('all', _('All results')),
            ('green', _('Green')),
            ('orange', _('Orange')),
            ('red', _('Red')),
            ('untested', _('Untested')),
            ('local-fail', _('Local failed')),
            ('api-fail', _('API failed')),
        ):
            self.result_filter.addItem(label, key)
        self.result_filter.currentIndexChanged.connect(self._reload_list)
        filt.addWidget(self.result_filter)

        self.rate_filter = QComboBox(self)
        self.rate_filter.addItem(_('Any rate'), '')
        for v in ('25', '50', '75', '100'):
            self.rate_filter.addItem(f'≥ {v}%', v)
        self.rate_filter.currentIndexChanged.connect(self._reload_list)
        filt.addWidget(self.rate_filter)

        self.source_filter = QComboBox(self)
        for key, label in (
            ('all', _('All sources')),
            ('user', _('Manual')),
            ('paste', _('Pasted')),
            ('scan', _('Scan')),
        ):
            self.source_filter.addItem(label, key)
        self.source_filter.currentIndexChanged.connect(self._reload_list)
        filt.addWidget(self.source_filter)

        self.count_label = QLabel('', self)
        filt.addWidget(self.count_label)

        body = QHBoxLayout()
        root.addLayout(body, 1)

        left = QVBoxLayout()
        body.addLayout(left, 2)
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
        self.add_btn.clicked.connect(self._add_one)
        add_row.addWidget(self.add_btn)
        self.paste_btn = QPushButton(_('Paste list'), self)
        self.paste_btn.clicked.connect(self._paste_list)
        add_row.addWidget(self.paste_btn)

        bulk = QHBoxLayout()
        left.addLayout(bulk)
        self.archive_match_btn = QPushButton(_('Archive matching'), self)
        self.archive_match_btn.clicked.connect(lambda: self._bulk_archive(True))
        self.restore_match_btn = QPushButton(_('Restore matching'), self)
        self.restore_match_btn.clicked.connect(lambda: self._bulk_archive(False))
        self.archive_failed_btn = QPushButton(_('Archive failed'), self)
        self.archive_failed_btn.clicked.connect(self._archive_failed)
        bulk.addWidget(self.archive_match_btn)
        bulk.addWidget(self.restore_match_btn)
        bulk.addWidget(self.archive_failed_btn)

        right = QVBoxLayout()
        body.addLayout(right, 2)
        form = QFormLayout()
        right.addLayout(form)
        self.detail_url = QLabel('', self)
        self.detail_url.setWordWrap(True)
        self.detail_url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(_('Proxy URL:'), self.detail_url)
        self.status_label = QLabel(_('Not tested'), self)
        self.status_label.setWordWrap(True)
        form.addRow(_('Last test:'), self.status_label)
        self.history_label = QLabel('', self)
        form.addRow(_('History:'), self.history_label)
        self.message_label = QLabel('', self)
        self.message_label.setWordWrap(True)
        self.message_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        form.addRow(_('Details:'), self.message_label)

        self.probe_conn = QComboBox(self)
        form.addRow(_('Probe via LLM connection:'), self.probe_conn)
        self.name_edit = QLineEdit(self)
        self.name_edit.setPlaceholderText(_('Preset name, e.g. Home VPN'))
        self.name_edit.setMaxLength(120)
        form.addRow(_('Preset name:'), self.name_edit)

        actions = QHBoxLayout()
        right.addLayout(actions)
        self.test_btn = QPushButton(_('Test'), self)
        self.test_btn.clicked.connect(self._test_one)
        self.test_all_btn = QPushButton(_('Test library…'), self)
        self.test_all_btn.clicked.connect(self._test_library)
        self.stop_btn = QPushButton(_('Stop'), self)
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop_batch)
        self.approve_btn = QPushButton(_('Approve as preset…'), self)
        self.approve_btn.clicked.connect(self._approve)
        self.remove_btn = QPushButton(_('Remove'), self)
        self.remove_btn.clicked.connect(self._remove)
        self.archive_btn = QPushButton(_('Archive'), self)
        self.archive_btn.clicked.connect(self._toggle_archive_current)
        for b in (self.test_btn, self.test_all_btn, self.stop_btn, self.approve_btn, self.remove_btn, self.archive_btn):
            actions.addWidget(b)
        actions.addStretch()

        self.batch_status = QLabel('', self)
        self.batch_status.setWordWrap(True)
        right.addWidget(self.batch_status)
        right.addStretch()

        self.refresh_probe_connections()
        self._reload_list()

    # ----- probe context -----
    def refresh_probe_connections(self):
        current = self.probe_conn.currentData()
        self.probe_conn.blockSignals(True)
        self.probe_conn.clear()
        for conn in load_connections():
            self.probe_conn.addItem(f'{conn.name} — {conn.api_url}', conn.id)
        self.probe_conn.blockSignals(False)
        if current is not None:
            idx = self.probe_conn.findData(current)
            if idx >= 0:
                self.probe_conn.setCurrentIndex(idx)

    def _probe_headers_and_url(self) -> tuple[str, list[tuple[str, str]]]:
        conn = get_connection(self.probe_conn.currentData() or '')
        if conn is None:
            return 'https://api.openai.com/v1/models', []
        headers = list(conn.headers_tuples())
        if conn.api_key and not any(k.lower() == 'authorization' for k, _ in headers):
            headers.insert(0, ('Authorization', f'Bearer {conn.api_key}'))
        return models_probe_url(conn.api_url), headers

    # ----- list / filters -----
    def _set_shelf(self, shelf: str):
        self._shelf = shelf
        self.shelf_active.setChecked(shelf == SHELF_ACTIVE)
        self.shelf_archived.setChecked(shelf == SHELF_ARCHIVED)
        self.shelf_all.setChecked(shelf == SHELF_ALL)
        self._reload_list()

    def _visible_filtered(self) -> list[ProxyPreset]:
        shelf_items = list_by_shelf(self._shelf)
        return filter_presets(
            shelf_items,
            search=self.search.text(),
            result_filter=self.result_filter.currentData() or 'all',
            rate_filter=self.rate_filter.currentData() or '',
            source_filter=self.source_filter.currentData() or 'all',
        )

    def _reload_list(self, select_id: str | None = None):
        if select_id is None:
            cur = self._current()
            select_id = cur.id if cur else None
        counts = shelf_counts()
        self.shelf_counts_label.setText(
            _('({0} active · {1} archived · {2} total)').format(
                counts['active'], counts['archived'], counts['total']
            )
        )
        self._items = self._visible_filtered()
        shelf_total = len(list_by_shelf(self._shelf))
        self.count_label.setText(_('showing {0} / {1}').format(len(self._items), shelf_total))
        self.list.clear()
        select_row = 0
        for i, preset in enumerate(self._items):
            color = preset.latest_color() or '—'
            star = '★ ' if preset.approved and preset.name else ''
            arch = ' [A]' if preset.is_archived else ''
            title = f'{star}{preset.display_label()}{arch}  [{color}]  {preset.history_dots_text()}'
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, preset.id)
            tip = preset.last_test_message or preset.proxy_url
            item.setToolTip(f'{preset.proxy_url}\n{tip}')
            # color hint
            if color == 'green':
                item.setForeground(Qt.GlobalColor.darkGreen)
            elif color == 'orange':
                item.setForeground(Qt.GlobalColor.darkYellow)
            elif color == 'red':
                item.setForeground(Qt.GlobalColor.darkRed)
            self.list.addItem(item)
            if select_id and preset.id == select_id:
                select_row = i
        if self._items:
            self.list.setCurrentRow(select_row)
        else:
            self._clear_detail()
        self._update_bulk_buttons()

    def _update_bulk_buttons(self):
        matching = self._items
        active_match = [p for p in matching if not p.is_archived]
        archived_match = [p for p in matching if p.is_archived]
        failed = [p for p in list_by_shelf(SHELF_ACTIVE) if is_failed_entry(p)]
        self.archive_match_btn.setEnabled(bool(active_match) and self._shelf != SHELF_ARCHIVED)
        self.restore_match_btn.setEnabled(bool(archived_match) and self._shelf != SHELF_ACTIVE)
        self.archive_failed_btn.setEnabled(bool(failed) and self._shelf != SHELF_ARCHIVED)

    def _current(self) -> ProxyPreset | None:
        row = self.list.currentRow()
        if row < 0 or row >= len(self._items):
            return None
        return self._items[row]

    def _clear_detail(self):
        self.detail_url.setText('')
        self.status_label.setText(_('Not tested'))
        self.status_label.setStyleSheet('')
        self.history_label.setText('')
        self.message_label.setText('')
        self.name_edit.clear()

    def _load_current(self, row: int):
        preset = self._current()
        if preset is None:
            self._clear_detail()
            return
        self.detail_url.setText(preset.proxy_url)
        color = preset.latest_color()
        local = _('Local OK') if preset.last_local_ok else (_('Local fail') if preset.last_local_ok is False else _('—'))
        api = _('API OK') if preset.last_api_ok else (_('API fail') if preset.last_api_ok is False else _('—'))
        lat = f' · {preset.last_test_latency_ms} ms' if preset.last_test_latency_ms is not None else ''
        approved = _(' · approved as “{0}”').format(preset.name) if preset.approved and preset.name else ''
        self.status_label.setText(f'{color or _("untested")} · {local} / {api}{lat}{approved}')
        style = {
            'green': 'color: #1e8449;',
            'orange': 'color: #d68910;',
            'red': 'color: #c0392b;',
        }.get(color or '', '')
        self.status_label.setStyleSheet(style)
        self.history_label.setText(preset.history_dots_text() or '—')
        self.message_label.setText(preset.last_test_message or '')
        self.name_edit.setText(preset.name if preset.approved else (preset.name or ''))
        self.archive_btn.setText(_('Restore') if preset.is_archived else _('Archive'))

    # ----- mutate -----
    def _add_one(self):
        raw = self.url_edit.text().strip()
        if not raw:
            return
        try:
            preset = add_proxy_candidate(raw, source='user')
        except TransportError as e:
            error_dialog(self, _('Invalid proxy'), str(e), show=True)
            return
        self.url_edit.clear()
        self._reload_list(select_id=preset.id)

    def _paste_list(self):
        d = ProxyPasteDialog(self)
        if d.exec():
            self._reload_list()

    def _remove(self):
        preset = self._current()
        if preset is None:
            return
        if not question_dialog(self, _('Remove'), _('Remove “{0}”?').format(preset.display_label())):
            return
        delete_proxy_preset(preset.id)
        self._reload_list()

    def _toggle_archive_current(self):
        preset = self._current()
        if preset is None:
            return
        set_archived(preset.id, not preset.is_archived)
        self._reload_list()

    def _bulk_archive(self, archived: bool):
        ids = [p.id for p in self._items if bool(p.is_archived) != archived]
        if not ids:
            return
        verb = _('Archive') if archived else _('Restore')
        if not question_dialog(self, verb, _('{0} {1} matching proxies?').format(verb, len(ids))):
            return
        bulk_set_archived(ids, archived)
        self._reload_list()

    def _archive_failed(self):
        failed = [p for p in list_by_shelf(SHELF_ACTIVE) if is_failed_entry(p)]
        if not failed:
            return
        if not question_dialog(
            self, _('Archive failed'), _('Archive {0} failed (red/orange) active proxies?').format(len(failed))
        ):
            return
        bulk_set_archived([p.id for p in failed], True)
        self._reload_list()

    def _approve(self):
        preset = self._current()
        if preset is None:
            return
        name = self.name_edit.text().strip()
        if not name:
            error_dialog(self, _('Preset name required'), _('Enter a short preset name before approving.'), show=True)
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
            _('“{0}” is available in LLM connections when Use proxy is checked.').format(name),
            show=True,
            show_copy_button=False,
        )

    # ----- testing -----
    def _test_one(self):
        if self._testing:
            return
        preset = self._current()
        if preset is None:
            return
        probe_url, headers = self._probe_headers_and_url()
        self.batch_status.setText(_('Testing…'))
        result = test_proxy(preset.proxy_url, probe_api_url=probe_url, probe_headers=headers)
        apply_test_result(preset.id, result)
        self._reload_list(select_id=preset.id)
        title = _('Proxy test passed') if result.get('ok') else _('Proxy test finished')
        info_dialog(self, title, result.get('message') or '', show=True, show_copy_button=False)
        self.batch_status.setText('')

    def _test_library(self):
        if self._testing:
            return
        shelf_entries = list_by_shelf(self._shelf)
        visible = self._visible_filtered()
        d = ProxyTestScopeDialog(self, shelf_entries, visible)
        if not d.exec() or not d.selected_ids:
            return
        self._start_batch(d.selected_ids)

    def _start_batch(self, preset_ids: list[str]):
        probe_url, headers = self._probe_headers_and_url()
        self._testing = True
        self.stop_btn.setEnabled(True)
        self.test_all_btn.setEnabled(False)
        self.test_btn.setEnabled(False)
        self.batch_status.setText(_('Testing library… 0/{0}').format(len(preset_ids)))

        self._thread = QThread(self)
        self._worker = _BatchTestWorker(preset_ids, probe_url, headers)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_batch_progress)
        self._worker.one_done.connect(lambda *_: self._reload_list())
        self._worker.finished.connect(self._on_batch_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.start()

    def _on_batch_progress(self, i: int, total: int, url: str):
        self.batch_status.setText(_('Testing library… {0}/{1}: {2}').format(i, total, url))

    def _stop_batch(self):
        if self._worker is not None:
            self._worker.abort = True
        self.batch_status.setText(_('Stopping…'))

    def _on_batch_finished(self, ok_count: int, ran: int, aborted: bool):
        self._testing = False
        self.stop_btn.setEnabled(False)
        self.test_all_btn.setEnabled(True)
        self.test_btn.setEnabled(True)
        prefix = _('Stopped') + ' · ' if aborted else ''
        self.batch_status.setText(
            prefix + _('OK — local and API: {0}/{1}').format(ok_count, ran)
        )
        self._reload_list()
        self._worker = None
        self._thread = None
