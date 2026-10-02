#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Shared constants for Byali features.
# Blueprint:
#   1) Visible labels (tag / title suffix)
#   2) Lineage identifier for future AI features
#   3) Toolbar / plugin action names for the Byali section

"""Constants for Byali duplication and later AI rewrite features."""

# --- Visible labels ---
DUPLICATE_TAG = 'Duplicated'
DUPLICATE_TITLE_SUFFIX = ' [Duplicated]'

# --- Lineage for future AI / rewrite tooling ---
# Stored as identifiers['byali_source'] = '<original book_id>'
SOURCE_IDENTIFIER = 'byali_source'

# --- Interface action names (must match InterfaceActionBase.name / action.name) ---
DUPLICATE_ACTION_NAME = 'Duplicate Books'

# Ordered list of Byali toolbar actions. Future Byali actions append here so
# ensure_byali_toolbar_section() places them in one separated header section.
BYALI_TOOLBAR_ACTIONS = (DUPLICATE_ACTION_NAME,)
