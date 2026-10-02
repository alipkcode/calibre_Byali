#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   1) Styles for note mark + floating popover
#   2) Attach note icon to current selection (keep original text)
#   3) Restore saved notes by matching anchor text
#   4) Popover open / close / delete (queues ids for Python flush)

"""JavaScript helpers for Byali passage notes (icon + popover) in the ebook viewer."""

BYALI_NOTES_HELPER_JS = r'''
(function () {
  // Reinstall functions each time (profile + runjs reinject).
  window.__byaliDeletedNotes = window.__byaliDeletedNotes || [];

  var STYLE_ID = 'byali-notes-style';
  var POP_ID = 'byali-note-popover';

  var CSS = [
    '.byali-note-mark{display:inline;background:rgba(255,193,7,.10);border-bottom:1px dashed rgba(255,167,38,.95);border-radius:.12em;padding:0 .04em;}',
    '.byali-note-icon{display:inline-flex;align-items:center;justify-content:center;margin-left:.22em;width:1.2em;height:1.2em;border-radius:50%;border:1px solid rgba(255,152,0,.75);background:rgba(255,248,225,.95);color:#e65100;font:600 .78em/1 "Segoe UI Emoji","Apple Color Emoji",sans-serif;cursor:pointer;vertical-align:super;padding:0;box-shadow:0 1px 2px rgba(0,0,0,.08);}',
    '.byali-note-icon:hover{background:#fff3e0;transform:scale(1.06);}',
    '.byali-note-icon[data-open="1"]{background:#ffe0b2;border-color:#ef6c00;}',
    '#byali-note-popover{position:fixed;z-index:2147483000;max-width:min(340px,92vw);min-width:220px;background:#1e1e1e;color:#f5f5f5;border:1px solid rgba(255,255,255,.12);border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,.35);font:400 13px/1.45 system-ui,-apple-system,Segoe UI,sans-serif;overflow:hidden;}',
    '#byali-note-popover .bn-hd{display:flex;align-items:center;gap:.5rem;padding:.65rem .8rem;background:linear-gradient(180deg,#2a2a2a,#232323);border-bottom:1px solid rgba(255,255,255,.08);}',
    '#byali-note-popover .bn-title{flex:1;font-weight:600;font-size:12.5px;letter-spacing:.01em;opacity:.95;}',
    '#byali-note-popover .bn-meta{padding:0 .8rem .35rem;font-size:11px;opacity:.55;}',
    '#byali-note-popover .bn-body{padding:.55rem .85rem .85rem;max-height:42vh;overflow:auto;white-space:pre-wrap;word-break:break-word;}',
    '#byali-note-popover .bn-ft{display:flex;gap:.45rem;justify-content:flex-end;padding:.55rem .7rem .7rem;border-top:1px solid rgba(255,255,255,.08);background:#191919;}',
    '#byali-note-popover button{appearance:none;border:0;border-radius:8px;padding:.4rem .7rem;font:600 12px/1.2 inherit;cursor:pointer;}',
    '#byali-note-popover .bn-close{background:transparent;color:#eee;opacity:.7;padding:.15rem .35rem;font-size:16px;}',
    '#byali-note-popover .bn-close:hover{opacity:1;}',
    '#byali-note-popover .bn-del{background:#5d1f1f;color:#ffcdd2;}',
    '#byali-note-popover .bn-del:hover{background:#7f2929;}',
    '#byali-note-popover .bn-done{background:#37474f;color:#eceff1;}',
    '#byali-note-popover .bn-done:hover{background:#455a64;}'
  ].join('');

  function bookDocs() {
    var docs = [];
    try {
      var iframes = document.querySelectorAll('iframe');
      for (var i = 0; i < iframes.length; i++) {
        try {
          var d = iframes[i].contentDocument;
          if (d) docs.push(d);
        } catch (e) {}
      }
    } catch (e) {}
    docs.push(document);
    return docs;
  }

  function ensureStyleInDoc(doc) {
    if (doc.getElementById(STYLE_ID)) return;
    var st = doc.createElement('style');
    st.id = STYLE_ID;
    st.textContent = CSS;
    (doc.head || doc.documentElement).appendChild(st);
  }

  function ensureHostStyle() {
    ensureStyleInDoc(document);
  }

  function closePopover() {
    var pop = document.getElementById(POP_ID);
    if (pop && pop.parentNode) pop.parentNode.removeChild(pop);
    var docs = bookDocs();
    for (var i = 0; i < docs.length; i++) {
      var nodes = docs[i].querySelectorAll('.byali-note-icon[data-open="1"]');
      for (var j = 0; j < nodes.length; j++) nodes[j].removeAttribute('data-open');
    }
  }

  function positionPopover(pop, anchorBtn) {
    var rect = anchorBtn.getBoundingClientRect();
    var vw = window.innerWidth || document.documentElement.clientWidth || 800;
    var vh = window.innerHeight || document.documentElement.clientHeight || 600;
    pop.style.visibility = 'hidden';
    pop.style.display = 'block';
    pop.style.left = '0px';
    pop.style.top = '0px';
    var pw = pop.offsetWidth || 280;
    var ph = pop.offsetHeight || 160;
    var left = rect.left + rect.width / 2 - pw / 2;
    var top = rect.bottom + 8;
    if (left < 8) left = 8;
    if (left + pw > vw - 8) left = Math.max(8, vw - pw - 8);
    if (top + ph > vh - 8) top = Math.max(8, rect.top - ph - 8);
    pop.style.left = left + 'px';
    pop.style.top = top + 'px';
    pop.style.visibility = 'visible';
  }

  function openPopover(btn) {
    ensureHostStyle();
    closePopover();
    btn.setAttribute('data-open', '1');
    var note = btn.getAttribute('data-note') || '';
    var agent = btn.getAttribute('data-agent') || '';
    var created = btn.getAttribute('data-created') || '';
    var nid = btn.getAttribute('data-note-id') || '';

    var pop = document.createElement('div');
    pop.id = POP_ID;
    pop.setAttribute('role', 'dialog');
    pop.setAttribute('aria-label', 'Byali note');

    var hd = document.createElement('div');
    hd.className = 'bn-hd';
    var title = document.createElement('div');
    title.className = 'bn-title';
    title.textContent = agent ? ('Note · ' + agent) : 'Reading note';
    var x = document.createElement('button');
    x.type = 'button';
    x.className = 'bn-close';
    x.setAttribute('aria-label', 'Close');
    x.textContent = '×';
    x.addEventListener('click', function (ev) { ev.stopPropagation(); closePopover(); });
    hd.appendChild(title);
    hd.appendChild(x);
    pop.appendChild(hd);

    if (created) {
      var meta = document.createElement('div');
      meta.className = 'bn-meta';
      meta.textContent = created;
      pop.appendChild(meta);
    }

    var body = document.createElement('div');
    body.className = 'bn-body';
    body.textContent = note;
    var noteDir = (btn.getAttribute('data-dir') || 'auto').toLowerCase();
    if (noteDir === 'ltr' || noteDir === 'rtl') {
      body.setAttribute('dir', noteDir);
      body.style.direction = noteDir;
      body.style.textAlign = noteDir === 'rtl' ? 'right' : 'left';
      pop.setAttribute('dir', noteDir);
    }
    pop.appendChild(body);

    var ft = document.createElement('div');
    ft.className = 'bn-ft';
    var del = document.createElement('button');
    del.type = 'button';
    del.className = 'bn-del';
    del.textContent = 'Delete';
    del.addEventListener('click', function (ev) {
      ev.stopPropagation();
      if (nid) window.__byaliDeletedNotes.push(nid);
      var mark = btn.closest('.byali-note-mark');
      if (mark && mark.parentNode) {
        var text = mark.getAttribute('data-anchor') || '';
        var tn = document.createTextNode(text || mark.textContent.replace(/📝\s*$/, '').replace(/N\s*$/, ''));
        // Prefer stored anchor
        if (mark.getAttribute('data-anchor')) {
          var parent = mark.parentNode;
          parent.insertBefore(document.createTextNode(mark.getAttribute('data-anchor')), mark);
          parent.removeChild(mark);
          try { parent.normalize(); } catch (e) {}
        } else {
          mark.parentNode.replaceChild(tn, mark);
        }
      }
      closePopover();
    });
    var done = document.createElement('button');
    done.type = 'button';
    done.className = 'bn-done';
    done.textContent = 'Done';
    done.addEventListener('click', function (ev) { ev.stopPropagation(); closePopover(); });
    ft.appendChild(del);
    ft.appendChild(done);
    pop.appendChild(ft);

    document.documentElement.appendChild(pop);
    positionPopover(pop, btn);

    setTimeout(function () {
      function onDocClick(ev) {
        if (!pop.contains(ev.target) && ev.target !== btn) {
          closePopover();
          document.removeEventListener('mousedown', onDocClick, true);
        }
      }
      document.addEventListener('mousedown', onDocClick, true);
    }, 0);
  }

  function wireIcon(btn) {
    btn.addEventListener('click', function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      if (btn.getAttribute('data-open') === '1') {
        closePopover();
        return;
      }
      openPopover(btn);
    });
  }

  function buildMark(doc, anchorText, note, meta) {
    ensureStyleInDoc(doc);
    meta = meta || {};
    var wrap = doc.createElement('span');
    wrap.className = 'byali-note-mark';
    wrap.setAttribute('data-byali-note-id', meta.id || '');
    wrap.setAttribute('data-anchor', anchorText || '');
    var text = doc.createElement('span');
    text.className = 'byali-note-text';
    text.textContent = anchorText;
    var btn = doc.createElement('button');
    btn.type = 'button';
    btn.className = 'byali-note-icon';
    btn.textContent = '\uD83D\uDCDD';
    btn.title = 'Show note';
    btn.setAttribute('aria-label', 'Show Byali note');
    btn.setAttribute('data-note-id', meta.id || '');
    btn.setAttribute('data-note', note || '');
    btn.setAttribute('data-agent', meta.agent_name || '');
    btn.setAttribute('data-created', meta.created_at || '');
    btn.setAttribute('data-dir', meta.text_direction || 'auto');
    wireIcon(btn);
    wrap.appendChild(text);
    wrap.appendChild(btn);
    return wrap;
  }

  function alreadyMarked(doc, noteId) {
    if (!noteId) return false;
    return !!doc.querySelector('.byali-note-mark[data-byali-note-id="' + noteId.replace(/"/g, '') + '"]');
  }

  function findAndWrap(doc, noteObj) {
    var needle = String(noteObj.anchor_text || '');
    if (!needle) return false;
    if (alreadyMarked(doc, noteObj.id)) return true;
    var walker = doc.createTreeWalker(doc.body || doc.documentElement, NodeFilter.SHOW_TEXT, null);
    var node;
    while ((node = walker.nextNode())) {
      if (!node.nodeValue) continue;
      if (node.parentElement && (node.parentElement.closest('.byali-note-mark') || node.parentElement.closest('.byali-rewrite'))) continue;
      var idx = node.nodeValue.indexOf(needle);
      if (idx < 0) continue;
      try {
        var range = doc.createRange();
        range.setStart(node, idx);
        range.setEnd(node, idx + needle.length);
        var mark = buildMark(doc, needle, noteObj.note || '', noteObj);
        range.deleteContents();
        range.insertNode(mark);
        return true;
      } catch (e) {}
    }
    return false;
  }

  window.byaliAttachNoteInThisDocument = function (anchorText, note, meta) {
    meta = meta || {};
    var doc = document;
    ensureStyleInDoc(doc);
    var sel = window.getSelection ? window.getSelection() : null;
    if (sel && sel.rangeCount) {
      var range = sel.getRangeAt(0);
      var selected = range.toString();
      if (selected) {
        try {
          var mark = buildMark(doc, anchorText || selected, note, meta);
          range.deleteContents();
          range.insertNode(mark);
          sel.removeAllRanges();
          return true;
        } catch (e) {}
      }
    }
    return findAndWrap(doc, {
      id: meta.id,
      anchor_text: anchorText || '',
      note: note,
      agent_name: meta.agent_name,
      created_at: meta.created_at,
      text_direction: meta.text_direction || 'auto'
    });
  };

  window.byaliAttachNoteToSelection = function (anchorText, note, meta) {
    // Prefer the async top-frame starter when present (postMessage to iframes).
    if (window === window.top && window.byaliStartAttachNote) {
      return window.byaliStartAttachNote(anchorText, note, meta);
    }
    return window.byaliAttachNoteInThisDocument(anchorText, note, meta);
  };

  window.byaliRestoreNotes = function (notes) {
    notes = notes || [];
    var ok = 0;
    for (var n = 0; n < notes.length; n++) {
      if (findAndWrap(document, notes[n] || {})) ok++;
    }
    // Ask iframes to restore too (parent cannot read their DOM)
    if (window === window.top) {
      var frames = document.querySelectorAll('iframe');
      for (var i = 0; i < frames.length; i++) {
        try {
          frames[i].contentWindow.postMessage({ __byali: 'byali-notes-restore', notes: notes }, '*');
        } catch (e) {}
      }
    }
    return ok;
  };

  if (!window.__byali_notes_restore_hook__) {
    window.__byali_notes_restore_hook__ = true;
    window.addEventListener('message', function (ev) {
      var data = ev && ev.data;
      if (!data || data.__byali !== 'byali-notes-restore') return;
      if (window === window.top) return;
      var notes = data.notes || [];
      for (var n = 0; n < notes.length; n++) {
        try { findAndWrap(document, notes[n] || {}); } catch (e) {}
      }
    }, false);
  }

  window.byaliDrainDeletedNotes = function () {
    var d = window.__byaliDeletedNotes || [];
    window.__byaliDeletedNotes = [];
    return d;
  };

  ensureHostStyle();
  window.__byali_notes_helper__ = true;
})();
'''
