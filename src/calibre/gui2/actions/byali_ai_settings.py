#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Main-library toolbar action for Byali AI Settings.

"""Open Byali AI Settings (LLM connection + rewrite agents)."""

from qt.core import QToolButton

from calibre.gui2.actions import InterfaceAction
from calibre.gui2.byali.ai.settings_dialog import ByaliAISettingsDialog
from calibre.gui2.byali.constants import AI_SETTINGS_ACTION_NAME
from calibre.utils.localization import _


class ByaliAISettingsAction(InterfaceAction):
    name = AI_SETTINGS_ACTION_NAME
    action_spec = (
        _('Byali AI'),
        'ai.png',
        _('Configure LLM providers and Byali rewrite agents'),
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
            'byali-ai-settings',
            _('AI Settings…'),
            icon='ai.png',
            description=_('Connect LLM APIs and manage rewrite agents'),
            triggered=self.open_settings,
            shortcut=False,
        )
        self.qaction.triggered.connect(self.open_settings)

    def open_settings(self, *args):
        d = ByaliAISettingsDialog(self.gui)
        d.exec()
