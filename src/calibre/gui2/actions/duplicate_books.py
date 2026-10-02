#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Toolbar / menu InterfaceAction for Byali duplication.
# Blueprint:
#   1) genesis: wire main click + dropdown menu
#   2) gui_layout_complete: ensure Byali section on toolbar
#   3) duplicate_selected: call byali.duplication + refresh GUI
#   4) show_duplicated: search for Duplicated tag

"""GUI action: Duplicate selected books into editable labeled copies."""

from qt.core import QToolButton

from calibre.gui2 import error_dialog
from calibre.gui2.actions import InterfaceAction
from calibre.gui2.byali.constants import DUPLICATE_ACTION_NAME, DUPLICATE_TAG
from calibre.gui2.byali.duplication import duplicate_books
from calibre.gui2.byali.toolbar import ensure_byali_toolbar_section
from calibre.utils.localization import _, ngettext


class DuplicateBooksAction(InterfaceAction):
    name = DUPLICATE_ACTION_NAME
    action_spec = (
        _('Duplication'),
        'edit-copy.png',
        _('Duplicate selected books for editing / future AI rewrite'),
        None,
    )
    action_type = 'current'
    action_add_menu = True
    action_menu_clone_qaction = _('Duplicate selected books')
    popup_type = QToolButton.ToolButtonPopupMode.MenuButtonPopup
    dont_add_to = frozenset(('context-menu-device', 'toolbar-device', 'menubar-device'))

    def genesis(self):
        self.qaction.triggered.connect(self.duplicate_selected)
        m = self.qaction.menu()
        assert m is not None
        self.menu = m
        m.addSeparator()
        self.show_duplicated_action = self.create_menu_action(
            m,
            'byali-show-duplicated',
            _('Show duplicated books'),
            icon='search.png',
            description=_('Search the library for books tagged as Duplicated'),
            triggered=self.show_duplicated,
            shortcut=False,
        )

    def gui_layout_complete(self):
        ensure_byali_toolbar_section(self.gui)

    def location_selected(self, loc):
        enabled = loc == 'library'
        self.qaction.setEnabled(enabled)
        self.menuless_qaction.setEnabled(enabled)
        for action in self.menu.actions():
            action.setEnabled(enabled)

    def _selected_book_ids(self):
        rows = self.gui.library_view.selectionModel().selectedRows()
        if not rows:
            error_dialog(
                self.gui,
                _('Cannot duplicate'),
                _('No books selected'),
                show=True,
            )
            return []
        model = self.gui.library_view.model()
        return [model.id(r) for r in rows]

    def duplicate_selected(self, *args):
        book_ids = self._selected_book_ids()
        if not book_ids:
            return

        db = self.gui.current_db
        new_ids = duplicate_books(db, book_ids)
        if not new_ids:
            error_dialog(
                self.gui,
                _('Duplication failed'),
                _('Could not create duplicate copies of the selected books.'),
                show=True,
            )
            return

        self._refresh_after_add(len(new_ids))
        self.gui.library_view.select_rows(new_ids)
        self.gui.status_bar.show_message(
            ngettext(
                'Created 1 duplicated book (tag: {tag})',
                'Created {n} duplicated books (tag: {tag})',
                len(new_ids),
            ).format(n=len(new_ids), tag=DUPLICATE_TAG),
            4000,
        )

    def _refresh_after_add(self, num):
        self.gui.library_view.model().books_added(num)
        self.gui.library_view.set_current_row(0)
        self.gui.refresh_cover_browser()
        self.gui.tags_view.recount()

    def show_duplicated(self, *args):
        self.gui.search.set_search_string(f'tags:"={DUPLICATE_TAG}"')
