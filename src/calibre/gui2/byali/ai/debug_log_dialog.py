#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   1) Dialog shell with live-updating log view
#   2) Copy all / Clear / Open log file
#   3) Subscribe to debug_log listeners for refresh

"""UI dialog to inspect Byali AI debug logs while reproducing bugs."""

from __future__ import annotations

import os

from qt.core import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QTimer

from calibre.gui2 import info_dialog
from calibre.gui2.byali.ai import debug_log
from calibre.gui2.widgets2 import Dialog
from calibre.utils.localization import _


class ByaliDebugLogDialog(Dialog):
    def __init__(self, parent=None):
        self._closed = False
        super().__init__(_('Byali Debug Log'), 'byali-ai-debug-log', parent=parent)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        tip = QLabel(
            _(
                'Detailed timeline for AI Agents rewrite / note attach. '
                'Reproduce the bug with this window open, then Copy all and send the log.'
            ),
            self,
        )
        tip.setWordWrap(True)
        layout.addWidget(tip)

        path = debug_log.log_file_path()
        self.path_label = QLabel(_('Log file: {}').format(path), self)
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(self.path_label.textInteractionFlags())
        from qt.core import Qt

        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.path_label)

        self.view = QPlainTextEdit(self)
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = self.view.font()
        font.setFamily('Consolas')
        font.setStyleHint(font.StyleHint.Monospace)
        self.view.setFont(font)
        layout.addWidget(self.view, 1)

        row = QHBoxLayout()
        layout.addLayout(row)
        self.copy_btn = QPushButton(_('Copy all'), self)
        self.clear_btn = QPushButton(_('Clear'), self)
        self.refresh_btn = QPushButton(_('Refresh'), self)
        self.open_btn = QPushButton(_('Open log folder'), self)
        self.copy_btn.clicked.connect(self._copy)
        self.clear_btn.clicked.connect(self._clear)
        self.refresh_btn.clicked.connect(self._reload)
        self.open_btn.clicked.connect(self._open_folder)
        row.addWidget(self.copy_btn)
        row.addWidget(self.clear_btn)
        row.addWidget(self.refresh_btn)
        row.addWidget(self.open_btn)
        row.addStretch()
        layout.addWidget(self.bb)

        self._reload()
        debug_log.add_listener(self._on_log)
        # Safety net refresh
        self._timer = QTimer(self)
        self._timer.setInterval(800)
        self._timer.timeout.connect(self._reload)
        self._timer.start()

    def _on_log(self):
        if self._closed:
            return
        QTimer.singleShot(0, self._reload)

    def _reload(self):
        if self._closed:
            return
        text = debug_log.get_text()
        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 4
        self.view.setPlainText(text)
        if at_bottom:
            bar.setValue(bar.maximum())

    def _copy(self):
        self.view.selectAll()
        self.view.copy()
        info_dialog(self, _('Copied'), _('Log copied to clipboard.'), show=True, show_copy_button=False)

    def _clear(self):
        debug_log.clear()
        self._reload()

    def _open_folder(self):
        path = debug_log.log_file_path()
        folder = os.path.dirname(path)
        os.makedirs(folder, exist_ok=True)
        from calibre.gui2 import open_local_file

        open_local_file(folder)

    def reject(self):
        self._cleanup()
        super().reject()

    def accept(self):
        self._cleanup()
        super().accept()

    def _cleanup(self):
        self._closed = True
        try:
            self._timer.stop()
        except Exception:
            pass
        debug_log.remove_listener(self._on_log)


def open_byali_debug_log(parent=None) -> None:
    d = ByaliDebugLogDialog(parent)
    d.exec()
