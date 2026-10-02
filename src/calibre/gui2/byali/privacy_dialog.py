#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   1) Dialog shell (Byali Privacy)
#   2) Always-offline status (update check + donate) — informational
#   3) User checkboxes for metadata source updates + online metadata download
#   4) Persist on accept

"""Settings window for Byali offline / privacy gates."""

from __future__ import annotations

from qt.core import QCheckBox, QDialog, QGroupBox, QHBoxLayout, QLabel, QVBoxLayout, Qt

from calibre.gui2.byali.privacy import (
    KEY_ALLOW_METADATA_SOURCE_UPDATES,
    KEY_ALLOW_ONLINE_METADATA_DOWNLOAD,
    prefs,
    set_allow_metadata_source_updates,
    set_allow_online_metadata_download,
)
from calibre.gui2.widgets2 import Dialog
from calibre.utils.localization import _


class ByaliPrivacyDialog(Dialog):
    def __init__(self, parent=None):
        super().__init__(_('Byali Privacy'), 'byali-privacy-settings', parent=parent)

    def setup_ui(self):
        layout = QVBoxLayout(self)

        intro = QLabel(
            _(
                'Byali keeps Calibre offline-first. Some contacts are always blocked; '
                'metadata online access is optional and off by default.'
            ),
            self,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        # --- Always offline ---
        always = QGroupBox(_('Always offline (cannot be enabled)'), self)
        always_l = QVBoxLayout(always)
        for text in (
            _('Calibre version / plugin update check → code.calibre-ebook.com'),
            _('Donate button → calibre-ebook.com/donate'),
        ):
            row = QHBoxLayout()
            lock = QLabel('🔒', self)
            lock.setToolTip(_('Hard-blocked in Byali'))
            label = QLabel(text, self)
            label.setWordWrap(True)
            row.addWidget(lock, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(label, 1)
            always_l.addLayout(row)
        note = QLabel(
            _('Stock Calibre AI (Preferences → AI / ai.json) is unchanged. Byali AI uses its own settings.'),
            self,
        )
        note.setWordWrap(True)
        note.setStyleSheet('color: palette(mid);')
        always_l.addWidget(note)
        layout.addWidget(always)

        # --- User gates ---
        gates = QGroupBox(_('Metadata online access'), self)
        gates_l = QVBoxLayout(gates)

        self.cb_source_updates = QCheckBox(
            _('Allow updating metadata source plugins from Calibre servers'),
            self,
        )
        self.cb_source_updates.setToolTip(
            _(
                'When enabled, Calibre may fetch refreshed Amazon/Google/etc. scraper '
                'definitions from code.calibre-ebook.com/metadata-sources/. '
                'When disabled, only the built-in / already-cached sources are used.'
            )
        )
        gates_l.addWidget(self.cb_source_updates)

        self.cb_download = QCheckBox(
            _('Allow downloading metadata and covers from the internet'),
            self,
        )
        self.cb_download.setToolTip(
            _(
                'When enabled, Edit Metadata → Download metadata/cover and related '
                'actions may contact Amazon, Google Books, and other configured sources. '
                'When disabled, those actions stay local and show a clear block message.'
            )
        )
        gates_l.addWidget(self.cb_download)

        hint = QLabel(
            _(
                'Tip: leave both unchecked for a fully local library. '
                'Turn them on only when you need online metadata, then turn them off again.'
            ),
            self,
        )
        hint.setWordWrap(True)
        gates_l.addWidget(hint)
        layout.addWidget(gates)

        layout.addStretch(1)
        layout.addWidget(self.bb)

        p = prefs()
        self.cb_source_updates.setChecked(bool(p[KEY_ALLOW_METADATA_SOURCE_UPDATES]))
        self.cb_download.setChecked(bool(p[KEY_ALLOW_ONLINE_METADATA_DOWNLOAD]))

    def accept(self):
        set_allow_metadata_source_updates(self.cb_source_updates.isChecked())
        set_allow_online_metadata_download(self.cb_download.isChecked())
        # Remember geometry via Dialog base
        super().accept()


def open_byali_privacy_dialog(parent=None) -> bool:
    """Open the privacy dialog; return True if the user accepted."""
    d = ByaliPrivacyDialog(parent)
    return d.exec() == QDialog.DialogCode.Accepted
