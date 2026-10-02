#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   1) Track selected text from viewer
#   2) Context-menu → AI Agents / Note agents + Debug Log
#   3) Run agent in a worker thread (logged session)
#   4) replace mode → JS rewrite wrapper + persist + drain JS logs
#   5) note mode → attach note icon + persist in notes_store
#   6) Restore notes + rewrites on content load; flush JS deletes to store
#   7) On replace/note failure → open Byali Debug Log automatically

"""Ebook-viewer integration for Byali AI Agents rewrite + passage notes."""

from __future__ import annotations

import json
from functools import partial
from threading import Thread
from uuid import uuid4

from qt.core import QCursor, QEvent, QMenu, QObject, QProgressDialog, Qt, QTimer, pyqtSignal

from calibre.gui2 import error_dialog, info_dialog
from calibre.gui2.byali.ai import debug_log
from calibre.gui2.byali.ai.agents import (
    OUTPUT_MODE_NOTE,
    ensure_defaults_seeded,
    load_agents,
    prefs as agent_prefs,
)
from calibre.gui2.byali.ai.debug_log_dialog import ByaliDebugLogDialog
from calibre.gui2.byali.ai.notes_js import BYALI_NOTES_HELPER_JS
from calibre.gui2.byali.ai.notes_store import (
    add_note,
    delete_notes,
    notes_as_js_payload,
)
from calibre.gui2.byali.ai.replace_js import BYALI_VIEWER_HELPER_JS
from calibre.gui2.byali.ai.rewrite_store import add_rewrite, rewrites_as_js_payload
from calibre.gui2.byali.ai.runner import AgentRunError, run_agent_on_text
from calibre.gui2.byali.ai.settings_dialog import ByaliAISettingsDialog
from calibre.utils.localization import _


class _AgentWorker(QObject):
    finished = pyqtSignal(object, object, object)  # agent, original, result_or_exc

    def __init__(self, agent, selected_text, session: str = ''):
        super().__init__()
        self.agent = agent
        self.selected_text = selected_text
        self.session = session

    def run(self):
        try:
            debug_log.info('worker: calling LLM', session=self.session, agent=self.agent.name)
            result = run_agent_on_text(self.agent, self.selected_text)
            debug_log.info(
                'worker: LLM ok',
                session=self.session,
                result_len=len(result or ''),
                result_preview=(result or '')[:120],
            )
            self.finished.emit(self.agent, self.selected_text, result)
        except Exception as e:
            debug_log.exception('worker: LLM failed', e, session=self.session, agent=self.agent.name)
            self.finished.emit(self.agent, self.selected_text, e)


class ByaliAIViewerController(QObject):
    def __init__(self, viewer):
        super().__init__(viewer)
        self.viewer = viewer
        self.selected_text = ''
        self._session = ''
        self._log_dialog = None
        ensure_defaults_seeded()
        debug_log.info('ByaliAIViewerController installed')
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

    def _drain_js_log(self, then=None):
        session = self._session

        def _got(raw):
            try:
                n = debug_log.ingest_js_entries(raw, session=session)
                debug_log.debug('drained JS log entries', session=session, count=n)
            except Exception as e:
                debug_log.exception('JS log drain failed', e, session=session)
            if then:
                then()

        try:
            self._runjs(
                'window.byaliDrainJsLog ? JSON.stringify(window.byaliDrainJsLog()) : "[]"',
                callback=_got,
            )
        except Exception as e:
            debug_log.exception('runjs drain failed', e, session=session)
            if then:
                then()

    def _open_debug_log(self, *args):
        if self._log_dialog is not None:
            try:
                if self._log_dialog.isVisible():
                    self._log_dialog.raise_()
                    self._log_dialog.activateWindow()
                    return
            except Exception:
                self._log_dialog = None
        self._log_dialog = ByaliDebugLogDialog(self.viewer)
        self._log_dialog.show()

    def _on_selection_changed(self, text, annot):
        self.selected_text = text or ''
        preview = (text or '')[:60].replace('\n', ' ')
        debug_log.debug(
            'selection_changed',
            len=len(text or ''),
            preview=preview,
            has_annot=annot is not None,
        )

    def _ensure_profile_scripts(self):
        """Refresh QWebEngineProfile scripts so book iframes get the latest helpers."""
        try:
            from calibre.gui2.viewer.web_view import create_profile
            from calibre.utils.webengine import create_script, insert_scripts

            profile = create_profile()
            insert_scripts(
                profile,
                create_script(name='byali-viewer-helper.js', src=BYALI_VIEWER_HELPER_JS),
                create_script(name='byali-notes-helper.js', src=BYALI_NOTES_HELPER_JS),
            )
            debug_log.debug('profile Byali scripts re-inserted for subframes')
        except Exception as e:
            debug_log.exception('profile script re-insert failed', e)

    def _inject_helper(self, *args):
        debug_log.debug('inject helpers')
        self._ensure_profile_scripts()
        try:
            self._runjs(BYALI_VIEWER_HELPER_JS)
            self._runjs(BYALI_NOTES_HELPER_JS)
        except Exception as e:
            debug_log.exception('inject helpers failed', e)
        self._restore_notes()
        self._restore_rewrites()
        self._drain_js_log()

    def _on_content_changed(self, *args):
        debug_log.debug('content_file_changed')
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
        except Exception as e:
            debug_log.exception('restore notes failed', e)

    def _restore_rewrites(self):
        key = self._book_key()
        if not key:
            return
        payload = rewrites_as_js_payload(key)
        if not payload:
            return
        debug_log.debug('restore rewrites', count=len(payload), book_key=key[:16])
        js = 'window.byaliRestoreRewrites && window.byaliRestoreRewrites(' + json.dumps(payload) + ')'

        def _run():
            try:
                self._runjs(js)
            except Exception as e:
                debug_log.exception('restore rewrites failed', e)

        _run()
        # Book iframe may still be booting after content_file_changed — one delayed retry.
        QTimer.singleShot(250, _run)

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
                debug_log.info('flushed deleted note ids', count=len(ids))

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
        menu.addAction(_('Byali Debug Log…'), self._open_debug_log)
        menu.exec(global_pos)

    def open_settings(self):
        d = ByaliAISettingsDialog(self.viewer)
        d.exec()

    def run_agent(self, agent, selected_text):
        if self._busy:
            debug_log.warn('run_agent ignored — already busy', agent=agent.name)
            return
        self._busy = True
        self._session = debug_log.new_session(f'agent:{agent.name}')
        debug_log.info(
            'run_agent start',
            session=self._session,
            agent=agent.name,
            agent_id=agent.id,
            output_mode=getattr(agent, 'output_mode', ''),
            selected_len=len(selected_text or ''),
            selected_preview=(selected_text or '')[:120],
            book_key=self._book_key()[:16] if self._book_key() else '',
        )
        self._inject_helper()
        # Diagnose selection state right before progress dialog (focus steal risk)
        try:
            self._runjs(
                'window.byaliDiagnoseSelection && JSON.stringify(window.byaliDiagnoseSelection('
                + json.dumps(selected_text)
                + '))',
                callback=lambda raw: debug_log.info(
                    'pre-progress selection diagnose',
                    session=self._session,
                    diagnose=raw if not isinstance(raw, str) else raw[:2000],
                ),
            )
        except Exception as e:
            debug_log.exception('pre-progress diagnose failed', e, session=self._session)

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
        debug_log.info('progress dialog shown (may clear DOM selection)', session=self._session)

        worker = _AgentWorker(agent, selected_text, session=self._session)
        worker.finished.connect(self._on_agent_finished, Qt.ConnectionType.QueuedConnection)
        self._worker = worker
        Thread(target=worker.run, daemon=True, name='ByaliAIAgent').start()
        agent_prefs().set('last_agent_id', agent.id)

    def _on_agent_finished(self, agent, original, result):
        self._busy = False
        try:
            self._progress.close()
            debug_log.info('progress dialog closed', session=self._session)
        except Exception as e:
            debug_log.exception('progress close failed', e, session=self._session)

        # Diagnose selection AFTER progress closes (this is the critical moment)
        try:
            self._runjs(
                'window.byaliDiagnoseSelection && JSON.stringify(window.byaliDiagnoseSelection('
                + json.dumps(original)
                + '))',
                callback=lambda raw: debug_log.info(
                    'post-progress selection diagnose',
                    session=self._session,
                    diagnose=raw if not isinstance(raw, str) else raw[:2000],
                ),
            )
        except Exception as e:
            debug_log.exception('post-progress diagnose failed', e, session=self._session)

        if isinstance(result, Exception):
            msg = str(result) if isinstance(result, AgentRunError) else _('AI request failed: {}').format(result)
            debug_log.error('agent finished with exception', session=self._session, msg=msg)
            self._drain_js_log(then=self._open_debug_log)
            error_dialog(self.viewer, _('AI Agents'), msg, show=True)
            return

        if getattr(agent, 'output_mode', '') == OUTPUT_MODE_NOTE or agent.is_note_agent:
            self._apply_note(agent, original, result)
            return

        rid = uuid4().hex
        text_direction = getattr(agent, 'text_direction', 'auto') or 'auto'
        debug_log.info(
            'calling byaliStartReplace (iframe postMessage)',
            session=self._session,
            rid=rid,
            original_len=len(original or ''),
            result_len=len(result or ''),
            text_direction=text_direction,
        )
        o = json.dumps(original)
        r = json.dumps(result)
        i = json.dumps(rid)
        td = json.dumps(text_direction)
        js = (
            '(function(){ try { '
            'if (!window.byaliStartReplace) { '
            'window.byaliLog && window.byaliLog("ERROR","byaliStartReplace missing"); '
            'return {done:true,ok:false,via:"missing"}; } '
            f'var v = window.byaliStartReplace({o}, {r}, {i}, {td}); '
            'if (v === true) return {done:true,ok:true,via:"sync-top"}; '
            'if (v === false) return {done:true,ok:false,via:"sync-fail"}; '
            'return {done:false,ok:false,via:"pending"}; '
            '} catch (e) { '
            'window.byaliLog && window.byaliLog("ERROR","startReplace threw",{error:String(e)}); '
            'return {done:true,ok:false,via:"throw"}; } '
            '})()'
        )
        self._replace_ctx = {
            'agent_id': agent.id,
            'agent_name': agent.name,
            'original': original,
            'result': result,
            'rid': rid,
            'text_direction': text_direction,
            'attempts': 0,
        }
        self._runjs(js, callback=self._on_replace_started)

    def _on_replace_started(self, status):
        if isinstance(status, str):
            try:
                status = json.loads(status)
            except Exception:
                status = None
        debug_log.info('replace started status', session=self._session, status=status)
        if isinstance(status, dict) and status.get('done'):
            self._finish_replace(bool(status.get('ok')), via=str(status.get('via') or ''))
            return
        # Pending iframe work — poll (also if status is None / unexpected)
        QTimer.singleShot(40, self._poll_replace_result)

    def _poll_replace_result(self):
        ctx = getattr(self, '_replace_ctx', None)
        if not ctx:
            return
        ctx['attempts'] = int(ctx.get('attempts') or 0) + 1
        if ctx['attempts'] > 50:  # ~2s
            debug_log.error('replace poll timeout', session=self._session, attempts=ctx['attempts'])
            self._finish_replace(False, via='timeout')
            return

        def _got(res):
            if isinstance(res, str):
                try:
                    res = json.loads(res)
                except Exception:
                    res = None
            if isinstance(res, dict) and res.get('done'):
                self._finish_replace(bool(res.get('ok')), via=str(res.get('via') or 'poll'))
                return
            QTimer.singleShot(40, self._poll_replace_result)

        try:
            self._runjs(
                'window.byaliGetReplaceResult ? JSON.stringify(window.byaliGetReplaceResult()) : "{\\"done\\":false}"',
                callback=_got,
            )
        except Exception as e:
            debug_log.exception('replace poll runjs failed', e, session=self._session)
            self._finish_replace(False, via='poll-error')

    def _finish_replace(self, ok: bool, via: str = ''):
        ctx = getattr(self, '_replace_ctx', None) or {}
        self._replace_ctx = None
        agent_name = ctx.get('agent_name') or ''
        agent_id = ctx.get('agent_id') or ''
        original = ctx.get('original') or ''
        result = ctx.get('result') or ''
        rid = ctx.get('rid') or ''
        text_direction = ctx.get('text_direction') or 'auto'
        debug_log.info(
            'finish replace',
            session=self._session,
            ok=ok,
            via=via,
            agent=agent_name,
        )

        def _done():
            if ok:
                book_key = self._book_key()
                if book_key and original and result:
                    try:
                        add_rewrite(
                            book_key,
                            original_text=original,
                            rewritten_text=result,
                            agent_id=agent_id,
                            agent_name=agent_name,
                            rewrite_id=rid or None,
                            text_direction=text_direction,
                        )
                        debug_log.info(
                            'rewrite persisted',
                            session=self._session,
                            book_key=book_key[:16],
                            rid=rid,
                        )
                    except Exception as e:
                        debug_log.exception('rewrite persist failed', e, session=self._session)
                self.viewer.web_view.generic_action(
                    'show-status-message',
                    {'text': _('AI Agents: replaced selection with “{}” output').format(agent_name)},
                )
                return
            debug_log.error('replace FAILED — opening debug log', session=self._session, via=via)
            self._open_debug_log()
            info_dialog(
                self.viewer,
                _('AI rewrite ready'),
                _(
                    'Could not replace the selection in the page (selection may have been cleared). '
                    'The AI output is shown below — you can copy it manually.\n\n'
                    'Byali Debug Log is open with the detailed timeline — please Copy all and send it.'
                ),
                det_msg=result,
                show=True,
            )

        self._drain_js_log(then=_done)

    def _apply_note(self, agent, original, note_text):
        book_key = self._book_key()
        debug_log.info('apply note', session=self._session, book_key=book_key[:16] if book_key else '')
        if not book_key:
            debug_log.error('note: no book key', session=self._session)
            self._drain_js_log(then=self._open_debug_log)
            info_dialog(
                self.viewer,
                _('Note ready'),
                _('Could not determine the current book path, so the note was not saved. Output is shown below.'),
                det_msg=note_text,
                show=True,
            )
            return
        text_direction = getattr(agent, 'text_direction', 'auto') or 'auto'
        saved = add_note(
            book_key,
            anchor_text=original,
            note=note_text,
            agent_id=agent.id,
            agent_name=agent.name,
            text_direction=text_direction,
        )
        meta = {
            'id': saved.id,
            'agent_name': saved.agent_name,
            'created_at': saved.created_at,
            'text_direction': saved.text_direction or text_direction,
        }
        debug_log.info(
            'calling byaliStartAttachNote (iframe postMessage)',
            session=self._session,
            note_id=saved.id,
            original_len=len(original or ''),
            text_direction=text_direction,
        )
        a = json.dumps(original)
        n = json.dumps(note_text)
        m = json.dumps(meta)
        js = (
            '(function(){ try { '
            'if (!window.byaliStartAttachNote) { '
            'if (window.byaliAttachNoteToSelection) '
            f'return {{done:true,ok:!!window.byaliAttachNoteToSelection({a},{n},{m}),via:"legacy"}}; '
            'return {done:true,ok:false,via:"missing"}; } '
            f'var v = window.byaliStartAttachNote({a}, {n}, {m}); '
            'if (v === true) return {done:true,ok:true,via:"sync-top"}; '
            'if (v === false) return {done:true,ok:false,via:"sync-fail"}; '
            'return {done:false,ok:false,via:"pending"}; '
            '} catch (e) { return {done:true,ok:false,via:"throw"}; } '
            '})()'
        )
        self._note_ctx = {
            'agent_name': agent.name,
            'note_text': note_text,
            'attempts': 0,
        }
        self._runjs(js, callback=self._on_note_started)

    def _on_note_started(self, status):
        if isinstance(status, str):
            try:
                status = json.loads(status)
            except Exception:
                status = None
        debug_log.info('note started status', session=self._session, status=status)
        if isinstance(status, dict) and status.get('done'):
            self._finish_note(bool(status.get('ok')), via=str(status.get('via') or ''))
            return
        QTimer.singleShot(40, self._poll_note_result)

    def _poll_note_result(self):
        ctx = getattr(self, '_note_ctx', None)
        if not ctx:
            return
        ctx['attempts'] = int(ctx.get('attempts') or 0) + 1
        if ctx['attempts'] > 50:
            debug_log.error('note poll timeout', session=self._session, attempts=ctx['attempts'])
            self._finish_note(False, via='timeout')
            return

        def _got(res):
            if isinstance(res, str):
                try:
                    res = json.loads(res)
                except Exception:
                    res = None
            if isinstance(res, dict) and res.get('done'):
                self._finish_note(bool(res.get('ok')), via=str(res.get('via') or 'poll'))
                return
            QTimer.singleShot(40, self._poll_note_result)

        try:
            self._runjs(
                'window.byaliGetNoteResult ? JSON.stringify(window.byaliGetNoteResult()) : "{\\"done\\":false}"',
                callback=_got,
            )
        except Exception as e:
            debug_log.exception('note poll runjs failed', e, session=self._session)
            self._finish_note(False, via='poll-error')

    def _finish_note(self, ok: bool, via: str = ''):
        ctx = getattr(self, '_note_ctx', None) or {}
        self._note_ctx = None
        agent_name = ctx.get('agent_name') or ''
        note_text = ctx.get('note_text') or ''
        debug_log.info('finish note attach', session=self._session, ok=ok, via=via, agent=agent_name)

        def _done():
            if ok:
                self.viewer.web_view.generic_action(
                    'show-status-message',
                    {'text': _('Byali: note attached ({})').format(agent_name)},
                )
                return
            debug_log.error('note attach failed in page', session=self._session, via=via)
            self._open_debug_log()
            info_dialog(
                self.viewer,
                _('Note saved'),
                _(
                    'The note was saved for this book, but the selection could not be marked in the page '
                    '(it may have been cleared). See Byali Debug Log for details. '
                    'Note text is shown below.'
                ),
                det_msg=note_text,
                show=True,
            )

        self._drain_js_log(then=_done)


def install_byali_ai_viewer_hook(viewer):
    """Attach Byali AI controller to an EbookViewer instance (idempotent)."""
    if getattr(viewer, '_byali_ai_controller', None) is not None:
        return viewer._byali_ai_controller
    controller = ByaliAIViewerController(viewer)
    viewer._byali_ai_controller = controller
    return controller
