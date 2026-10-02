#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint (Qt port of PBE5 DNS/proxy library popup):
#   1) List library entries for dns | proxy
#   2) Add / remove / paste-batch
#   3) Use selection → fill the connection field

"""DNS / proxy library dialog for Byali AI connections."""

from __future__ import annotations

from qt.core import (
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    Qt,
)

from calibre.gui2 import error_dialog, info_dialog, question_dialog
from calibre.gui2.byali.ai.network_library import (
    BATCH_PROXY_LIMIT,
    KIND_DNS,
    KIND_PROXY,
    delete_library_entry,
    ensure_network_library_seeded,
    list_library,
    upsert_library_value,
)
from calibre.gui2.byali.ai.transport import TransportError
from calibre.gui2.widgets2 import Dialog
from calibre.utils.localization import _


class NetworkLibraryDialog(Dialog):
    """Pick or manage reusable DNS gates / proxy URLs."""

    def __init__(self, kind: str, parent=None):
        self.kind = kind if kind in (KIND_DNS, KIND_PROXY) else KIND_PROXY
        self.selected_value = ''
        title = _('Proxy library') if self.kind == KIND_PROXY else _('DNS library')
        super().__init__(title, 'byali-ai-network-library', parent)

    def setup_ui(self):
        ensure_network_library_seeded()
        layout = QVBoxLayout(self)

        subtitle = QLabel(
            _(
                'Each entry can be one-click filled into the connection form. '
                'Seeded public DNS resolvers appear automatically.'
            )
            if self.kind == KIND_DNS
            else _(
                'Save reusable HTTP/HTTPS/SOCKS proxy URLs here. '
                'Select one and click Use to fill the connection’s Proxy URL field.'
            )
        )
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.list = QListWidget(self)
        self.list.itemDoubleClicked.connect(lambda *_: self.accept())
        layout.addWidget(self.list, 1)

        add_row = QHBoxLayout()
        layout.addLayout(add_row)
        placeholder = _('e.g. 8.8.8.8') if self.kind == KIND_DNS else _('e.g. http://host:8080')
        self.add_edit = QLineEdit(self)
        self.add_edit.setPlaceholderText(placeholder)
        self.add_edit.setMaxLength(255 if self.kind == KIND_PROXY else 120)
        add_row.addWidget(self.add_edit, 1)
        self.add_btn = QPushButton(_('Add'), self)
        self.add_btn.clicked.connect(self._add_one)
        add_row.addWidget(self.add_btn)

        if self.kind == KIND_PROXY:
            self.paste_edit = QPlainTextEdit(self)
            self.paste_edit.setPlaceholderText(
                _(
                    'Optional: paste many proxies (one per line).\n'
                    'Examples:\n'
                    'http://1.2.3.4:8080\n'
                    'https://user:pass@host:443\n'
                    'socks5://127.0.0.1:1080'
                )
            )
            self.paste_edit.setMaximumHeight(100)
            layout.addWidget(self.paste_edit)
            paste_btn = QPushButton(_('Add pasted proxies to library'), self)
            paste_btn.clicked.connect(self._add_pasted)
            layout.addWidget(paste_btn)

        actions = QHBoxLayout()
        layout.addLayout(actions)
        self.remove_btn = QPushButton(_('Remove'), self)
        self.remove_btn.clicked.connect(self._remove)
        actions.addWidget(self.remove_btn)
        actions.addStretch()

        bb = QDialogButtonBox(self)
        self.use_btn = bb.addButton(_('Use selected'), QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

        self._reload()

    def _reload(self):
        self.list.clear()
        for entry in list_library(self.kind, include_archived=False):
            label = entry.label.strip()
            text = f'{label} — {entry.value}' if label else entry.value
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, entry.id)
            item.setData(Qt.ItemDataRole.UserRole + 1, entry.value)
            item.setToolTip(entry.value)
            self.list.addItem(item)

    def _add_one(self):
        raw = self.add_edit.text().strip()
        if not raw:
            return
        try:
            upsert_library_value(self.kind, raw, source='user', restore_if_archived=True)
        except TransportError as e:
            error_dialog(self, _('Invalid value'), str(e), show=True)
            return
        self.add_edit.clear()
        self._reload()

    def _add_pasted(self):
        if self.kind != KIND_PROXY:
            return
        lines = [ln.strip() for ln in self.paste_edit.toPlainText().splitlines() if ln.strip()]
        if not lines:
            return
        added = skipped = 0
        for raw in lines[:BATCH_PROXY_LIMIT]:
            try:
                before = {e.value for e in list_library(KIND_PROXY, include_archived=True)}
                entry = upsert_library_value(KIND_PROXY, raw, source='user', restore_if_archived=True)
                if entry.value in before:
                    skipped += 1
                else:
                    added += 1
            except TransportError:
                skipped += 1
        self.paste_edit.clear()
        self._reload()
        info_dialog(
            self,
            _('Library updated'),
            _('Added {0} prox{1}; skipped {2}.').format(
                added, _('y') if added == 1 else _('ies'), skipped
            ),
            show=True,
        )

    def _remove(self):
        item = self.list.currentItem()
        if item is None:
            return
        entry_id = item.data(Qt.ItemDataRole.UserRole)
        if not question_dialog(self, _('Remove'), _('Remove this entry from the library?')):
            return
        delete_library_entry(entry_id)
        self._reload()

    def accept(self):
        item = self.list.currentItem()
        if item is None:
            error_dialog(self, _('Nothing selected'), _('Select a library entry first.'), show=True)
            return
        self.selected_value = item.data(Qt.ItemDataRole.UserRole + 1) or ''
        if not self.selected_value:
            error_dialog(self, _('Nothing selected'), _('Select a library entry first.'), show=True)
            return
        super().accept()
