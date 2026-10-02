#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Toolbar section helper for Byali features.
# Blueprint:
#   1) Read current action-layout-toolbar from gprefs
#   2) Append a separator + any missing Byali actions (additive only)
#   3) Rebuild bars when the layout actually changed

"""Ensure Byali actions appear as their own toolbar section."""

from __future__ import annotations

from calibre.gui2 import gprefs
from calibre.gui2.byali.constants import BYALI_TOOLBAR_ACTIONS


def ensure_byali_toolbar_section(gui) -> bool:
    """
    If any Byali toolbar action is missing from the main toolbar layout,
    append a separator and the missing actions, then rebuild the bars.

    Returns True if the layout was modified.
    """
    key = 'action-layout-toolbar'
    layout = list(gprefs[key])
    missing = [name for name in BYALI_TOOLBAR_ACTIONS if name not in layout]
    if not missing:
        return False

    if layout and layout[-1] is not None:
        layout.append(None)
    layout.extend(missing)
    gprefs.set(key, tuple(layout))

    bars = getattr(gui, 'bars_manager', None)
    if bars is not None:
        bars.init_bars()
        bars.update_bars()
    return True
