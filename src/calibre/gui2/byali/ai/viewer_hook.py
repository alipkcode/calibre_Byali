#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   1) Track selected text from viewer
#   2) Context-menu / shortcut → AI Agents submenu
#   3) Run agent in a worker thread
#   4) Inject JS to replace selection + attach i-flip control

"""Ebook-viewer integration for Byali AI Agents rewrite flow."""

from __future__ import annotations

import json
from functools import partial
from threading import Thread
from uuid import uuid4

from qt.core import QCursor, QEvent, QMenu, QObject, QProgressDialog, Qt, pyqtSignal

from calibre.gui2 import error_dialog, info_dialog
from calibre.gui2.byali.ai.agents import ensure_defaults_seeded, load_agents, prefs as agent_prefs
from calibre.gui2.byali.ai.replace_js import BYALI_VIEWER_HELPER_JS
from calibre.gui2.byali.ai.runner import AgentRunError, run_agent_on_text
from calibre.gui2.byali.ai.settings_dialog import ByaliAISettingsDialog
from calibre.utils.localization import _


class _AgentWorker(QObject):
    finished = pyqtSignal(object, object, object)  # agent, original, result_or_exc

    def __init__(self, agent, selected_text):
        super().__init__()
        self.agent = agent
        self.selected_text = selected_text

    def run(self):
        try:
            result = run_agent_on_text(self.agent, self.selected_text)
            self.finished.emit(self.agent, self.selected_text, result)
        except Exception as e:
            self.finished.emit(self.agent, self.selected_text, e)


class ByaliAIViewerController(QObject):
    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.selected_text = ''
        ensure_defaults_seeded()
        web = viewer.web_view
        web.selection_changed.connect(self._on_selection_changed)
        web.installEventFilter(self)
        focus_proxy = web.focusProxy()
        if focus_proxy is not None:
            focus_proxy.installEventFilter(self)
        # Inject helper once view is usable
        web.view_created.connect(self._inject_helper)
        web.content_file_changed.connect(lambda *_: self._inject_helper())
        self._busy = False

    def _runjs(self, src, callback=None):
        page = self.viewer.web_view._page
        page.runjs(src, callback=callback)

    def _on_selection_changed(self, text, annot):
        self.selected_text = text or ''

    def _inject_helper(self, *args):
        try:
            self._runjs(BYALI_VIEWER_HELPER_JS)
        except Exception:
            pass

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.ContextMenu and not self._busy:
            text = (self.selected_text or '').strip()
            if text:
                self.show_agents_menu(global_pos=event.globalPos() if hasattr(event, 'globalPos') else QCursor.pos())
                return True
        return super().eventFilter(obj, event)

    def show_agents_menu(self, global_pos=None):
        text = (self.selected_text or '').strip()
        if not text:
            # Ask JS for live selection (more accurate than last signal)
            self._runjs(
                'window.byaliGetSelectedText ? window.byaliGetSelectedText() : ""',
                callback=self._show_menu_with_js_selection,
            )
            self._pending_menu_pos = global_pos or QCursor.pos()
            return
        self._popup_menu(text, global_pos or QCursor.pos())

    def _show_menu_with_js_selection(self, text):
        text = (text or self.selected_text or '').strip()
        pos = getattr(self, '_pending_menu_pos', QCursor.pos())
        if not text:
            error_dialog(self.viewer, _('AI Agents'), _('Select some text in the book first.'), show=True)
            return
        self.selected_text = text
        self._popup_menu(text, pos)

    def _popup_menu(self, selected_text, global_pos):
        menu = QMenu(self.viewer)
        agents_menu = menu.addMenu(_('AI Agents'))
        agents = load_agents(include_disabled=False)
        if not agents:
            agents_menu.addAction(_('No agents enabled')).setEnabled(False)
        else:
            for agent in agents:
                act = agents_menu.addAction(agent.name)
                act.triggered.connect(partial(self.run_agent, agent, selected_text))
        menu.addSeparator()
        menu.addAction(_('Byali AI Settings…'), self.open_settings)
        menu.exec(global_pos)

    def open_settings(self):
        d = ByaliAISettingsDialog(self.viewer)
        d.exec()

    def run_agent(self, agent, selected_text):
        if self._busy:
            return
        self._busy = True
        self._inject_helper()
        self._progress = QProgressDialog(
            _('Running AI agent: {}').format(agent.name),
            _('Cancel'),
            0,
            0,
            self.viewer,
        )
        self._progress.setWindowTitle(_('Byali AI'))
        self._progress.setWindowModality(Qt.WindowModality.WindowModal)
        self._progress.setMinimumDuration(400)
        self._progress.show()

        worker = _AgentWorker(agent, selected_text)
        worker.finished.connect(self._on_agent_finished, Qt.ConnectionType.QueuedConnection)
        # Keep worker referenced
        self._worker = worker
        Thread(target=worker.run, daemon=True, name='ByaliAIAgent').start()
        agent_prefs().set('last_agent_id', agent.id)

    def _on_agent_finished(self, agent, original, result):
        self._busy = False
        try:
            self._progress.close()
        except Exception:
            pass
        if isinstance(result, Exception):
            msg = str(result) if isinstance(result, AgentRunError) else _('AI request failed: {}').format(result)
            error_dialog(self.viewer, _('AI Agents'), msg, show=True)
            return
        rid = uuid4().hex
        # JSON-encode for safe JS string literals
        o = json.dumps(original)
        r = json.dumps(result)
        i = json.dumps(rid)
        js = f'window.byaliReplaceSelection && window.byaliReplaceSelection({o}, {r}, {i})'
        self._runjs(js, callback=partial(self._after_replace, agent.name, original, result))

    def _after_replace(self, agent_name, original, result, ok):
        if ok:
            self.viewer.web_view.generic_action(
                'show-status-message',
                {'text': _('AI Agents: replaced selection with “{}” output').format(agent_name)},
            )
            return
        # Fallback: show result so the user still gets value
        info_dialog(
            self.viewer,
            _('AI rewrite ready'),
            _(
                'Could not replace the selection in the page (selection may have been cleared). '
                'The AI output is shown below — you can copy it manually.'
            ),
            det_msg=result,
            show=True,
        )


def install_byali_ai_viewer_hook(viewer):
    """Attach Byali AI controller to an EbookViewer instance (idempotent)."""
    if getattr(viewer, '_byali_ai_controller', None) is not None:
        return viewer._byali_ai_controller
    controller = ByaliAIViewerController(viewer)
    viewer._byali_ai_controller = controller
    return controller
