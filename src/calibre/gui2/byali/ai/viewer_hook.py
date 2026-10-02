#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   1) Track selected text from viewer
#   2) Context-menu → AI Agents (rewrite) + Note agents submenus
#   3) Run agent in a worker thread
#   4) replace mode → existing JS rewrite wrapper
#   5) note mode → attach note icon + persist in notes_store
#   6) Restore notes on content load; flush JS deletes to store

"""Ebook-viewer integration for Byali AI Agents rewrite + passage notes."""

from __future__ import annotations

import json
from functools import partial
from threading import Thread
from uuid import uuid4

from qt.core import QCursor, QEvent, QMenu, QObject, QProgressDialog, Qt, QTimer, pyqtSignal

from calibre.gui2 import error_dialog, info_dialog
from calibre.gui2.byali.ai.agents import (
    OUTPUT_MODE_NOTE,
    ensure_defaults_seeded,
    load_agents,
    prefs as agent_prefs,
)
from calibre.gui2.byali.ai.notes_js import BYALI_NOTES_HELPER_JS
from calibre.gui2.byali.ai.notes_store import (
    add_note,
    delete_notes,
    notes_as_js_payload,
)
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
        web.view_created.connect(self._inject_helper)
        web.content_file_changed.connect(self._on_content_changed)
        self._busy = False
        self._delete_timer = QTimer(self)
        self._delete_timer.setInterval(900)
        self._delete_timer.timeout.connect(self._flush_deleted_notes)
        self._delete_timer.start()

    def _book_key(self) -> str:
        data = getattr(self.viewer, 'current_book_data', None) or {}
        # Prefer viewer annotations key when present
        key = data.get('annotations_path_key')
        if key:
            key = str(key)
            if key.endswith('.json'):
                key = key[:-5]
            return key
        path = data.get('pathtoebook') or ''
        if not path:
            return ''
        from calibre.gui2.viewer.ui import path_key

        return path_key(path)

    def _runjs(self, src, callback=None):
        page = self.viewer.web_view._page
        page.runjs(src, callback=callback)

    def _on_selection_changed(self, text, annot):
        self.selected_text = text or ''

    def _inject_helper(self, *args):
        try:
            self._runjs(BYALI_VIEWER_HELPER_JS)
            self._runjs(BYALI_NOTES_HELPER_JS)
        except Exception:
            pass
        self._restore_notes()

    def _on_content_changed(self, *args):
        self._flush_deleted_notes()
        self._inject_helper()

    def _restore_notes(self):
        key = self._book_key()
        if not key:
            return
        payload = notes_as_js_payload(key)
        if not payload:
            return
        js = 'window.byaliRestoreNotes && window.byaliRestoreNotes(' + json.dumps(payload) + ')'
        try:
            self._runjs(js)
        except Exception:
            pass

    def _flush_deleted_notes(self):
        key = self._book_key()
        if not key:
            return

        def _apply(raw):
            ids = []
            if isinstance(raw, list):
                ids = [str(x) for x in raw if x]
            elif isinstance(raw, str) and raw.strip():
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        ids = [str(x) for x in parsed if x]
                except Exception:
                    ids = []
            if ids:
                delete_notes(key, ids)

        try:
            self._runjs(
                'window.byaliDrainDeletedNotes ? JSON.stringify(window.byaliDrainDeletedNotes()) : "[]"',
                callback=_apply,
            )
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
        agents = load_agents(include_disabled=False)
        rewrite = [a for a in agents if not a.is_note_agent]
        notes = [a for a in agents if a.is_note_agent]

        rewrite_menu = menu.addMenu(_('AI Agents'))
        if not rewrite:
            rewrite_menu.addAction(_('No rewrite agents enabled')).setEnabled(False)
        else:
            for agent in rewrite:
                act = rewrite_menu.addAction(agent.name)
                act.triggered.connect(partial(self.run_agent, agent, selected_text))

        notes_menu = menu.addMenu(_('Note agents'))
        if not notes:
            notes_menu.addAction(_('No note agents enabled')).setEnabled(False)
        else:
            for agent in notes:
                act = notes_menu.addAction(agent.name)
                act.setToolTip(_('Keeps the text and attaches a note icon'))
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
        label = _('Creating note: {}') if agent.is_note_agent else _('Running AI agent: {}')
        self._progress = QProgressDialog(
            label.format(agent.name),
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

        if getattr(agent, 'output_mode', '') == OUTPUT_MODE_NOTE or agent.is_note_agent:
            self._apply_note(agent, original, result)
            return

        rid = uuid4().hex
        o = json.dumps(original)
        r = json.dumps(result)
        i = json.dumps(rid)
        js = f'window.byaliReplaceSelection && window.byaliReplaceSelection({o}, {r}, {i})'
        self._runjs(js, callback=partial(self._after_replace, agent.name, original, result))

    def _apply_note(self, agent, original, note_text):
        book_key = self._book_key()
        if not book_key:
            info_dialog(
                self.viewer,
                _('Note ready'),
                _('Could not determine the current book path, so the note was not saved. Output is shown below.'),
                det_msg=note_text,
                show=True,
            )
            return
        saved = add_note(
            book_key,
            anchor_text=original,
            note=note_text,
            agent_id=agent.id,
            agent_name=agent.name,
        )
        meta = {
            'id': saved.id,
            'agent_name': saved.agent_name,
            'created_at': saved.created_at,
        }
        js = (
            'window.byaliAttachNoteToSelection && window.byaliAttachNoteToSelection('
            + json.dumps(original)
            + ', '
            + json.dumps(note_text)
            + ', '
            + json.dumps(meta)
            + ')'
        )
        self._runjs(js, callback=partial(self._after_note_attach, agent.name, note_text))

    def _after_note_attach(self, agent_name, note_text, ok):
        if ok:
            self.viewer.web_view.generic_action(
                'show-status-message',
                {'text': _('Byali: note attached ({})').format(agent_name)},
            )
            return
        info_dialog(
            self.viewer,
            _('Note saved'),
            _(
                'The note was saved for this book, but the selection could not be marked in the page '
                '(it may have been cleared). Re-open the chapter or search the passage to see the note icon. '
                'Note text is shown below.'
            ),
            det_msg=note_text,
            show=True,
        )

    def _after_replace(self, agent_name, original, result, ok):
        if ok:
            self.viewer.web_view.generic_action(
                'show-status-message',
                {'text': _('AI Agents: replaced selection with “{}” output').format(agent_name)},
            )
            return
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
