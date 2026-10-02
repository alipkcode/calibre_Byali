#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Toolbar / menu InterfaceAction for Byali Privacy (offline gates).

"""Open Byali Privacy settings (offline / metadata network gates)."""

from qt.core import QToolButton

from calibre.gui2.actions import InterfaceAction
from calibre.gui2.byali.constants import PRIVACY_ACTION_NAME
from calibre.gui2.byali.privacy_dialog import ByaliPrivacyDialog
from calibre.gui2.byali.toolbar import ensure_byali_toolbar_section
from calibre.utils.localization import _


class ByaliPrivacyAction(InterfaceAction):
    name = PRIVACY_ACTION_NAME
    action_spec = (
        _('Byali Privacy'),
        'config.png',
        _('Offline gates: block Calibre servers and optional metadata downloads'),
        None,
    )
    action_type = 'global'
    popup_type = QToolButton.ToolButtonPopupMode.InstantPopup
    action_add_menu = True
    dont_add_to = frozenset(('toolbar-device', 'menubar-device', 'context-menu-device'))

    def genesis(self):
        m = self.qaction.menu()
        assert m is not None
        self.menu = m
        self.create_menu_action(
            m,
            'byali-privacy-settings',
            _('Privacy / Offline…'),
            icon='config.png',
            description=_('Control which online metadata contacts are allowed'),
            triggered=self.open_settings,
            shortcut=False,
        )
        self.qaction.triggered.connect(self.open_settings)

    def gui_layout_complete(self):
        ensure_byali_toolbar_section(self.gui)

    def open_settings(self, *args):
        d = ByaliPrivacyDialog(self.gui)
        d.exec()
