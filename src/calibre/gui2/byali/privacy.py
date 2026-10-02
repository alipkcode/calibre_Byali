#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   1) byali_privacy JSONConfig (offline-first defaults)
#   2) Hard-offline switches (Calibre update check + donate URL)
#   3) User gates for metadata-source updates + online metadata/cover download
#   4) Shared helpers for GUI / CLI entry points

"""Byali privacy / offline gates for Calibre network contact."""

from __future__ import annotations

from functools import lru_cache

from calibre.utils.config import JSONConfig
from calibre.utils.localization import _

# Keys in byali_privacy.json
KEY_ALLOW_METADATA_SOURCE_UPDATES = 'allow_metadata_source_updates'
KEY_ALLOW_ONLINE_METADATA_DOWNLOAD = 'allow_online_metadata_download'


@lru_cache(2)
def prefs() -> JSONConfig:
    ans = JSONConfig('byali_privacy')
    # Offline-first: user must opt in to re-enable these contacts.
    ans.defaults[KEY_ALLOW_METADATA_SOURCE_UPDATES] = False
    ans.defaults[KEY_ALLOW_ONLINE_METADATA_DOWNLOAD] = False
    return ans


def calibre_update_check_allowed() -> bool:
    """Hard offline in Byali — never phone home for version/plugin update checks."""
    return False


def calibre_donate_url_allowed() -> bool:
    """Hard offline in Byali — Donate never opens calibre-ebook.com."""
    return False


def allow_metadata_source_updates() -> bool:
    return bool(prefs()[KEY_ALLOW_METADATA_SOURCE_UPDATES])


def allow_online_metadata_download() -> bool:
    return bool(prefs()[KEY_ALLOW_ONLINE_METADATA_DOWNLOAD])


def set_allow_metadata_source_updates(enabled: bool) -> None:
    prefs().set(KEY_ALLOW_METADATA_SOURCE_UPDATES, bool(enabled))


def set_allow_online_metadata_download(enabled: bool) -> None:
    prefs().set(KEY_ALLOW_ONLINE_METADATA_DOWNLOAD, bool(enabled))


def notify_metadata_download_blocked(parent=None) -> None:
    """Show why an online metadata/cover action was blocked."""
    from calibre.gui2 import error_dialog

    error_dialog(
        parent,
        _('Online metadata blocked'),
        _(
            'Downloading metadata or covers from the internet is blocked by Byali Privacy.\n\n'
            'Open Byali Privacy in the toolbar to allow it, or fill metadata manually.'
        ),
        show=True,
    )


def ensure_online_metadata_download_allowed(parent=None) -> bool:
    """
    Return True if online metadata/cover download may proceed.
    When blocked and parent is given, show a dialog.
    """
    if allow_online_metadata_download():
        return True
    if parent is not None:
        notify_metadata_download_blocked(parent)
    return False
