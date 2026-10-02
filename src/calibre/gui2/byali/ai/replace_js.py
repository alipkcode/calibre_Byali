#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   - Injected into main frame + iframes (profile script, RunsOnSubFrames)
#   - Actual replace runs IN the frame that owns the selection (usually the book iframe)
#   - Parent cannot read iframe.contentDocument → use postMessage to ask iframes
#   - Parent exposes byaliStartReplace / byaliGetReplaceResult for Python polling
#   - JS log buffer for Byali Debug Log

"""JavaScript helpers for in-viewer Byali text rewrite + original flip."""

BYALI_VIEWER_HELPER_JS = r'''
(function () {
  window.__byaliJsLog = window.__byaliJsLog || [];

  function byaliLog(level, msg, data) {
    try {
      var entry = {
        t: Date.now(),
        level: level || 'DEBUG',
        msg: String(msg || ''),
        data: data === undefined ? null : data
      };
      window.__byaliJsLog.push(entry);
      if (window.__byaliJsLog.length > 800) {
        window.__byaliJsLog.splice(0, window.__byaliJsLog.length - 800);
      }
      // Sandboxed book iframe cannot share the parent's log buffer — forward entries.
      if (!isTop) {
        try {
          window.parent.postMessage({ __byali: 'byali-js-log', entry: entry }, '*');
        } catch (e) {}
      }
    } catch (e) {}
  }
  window.byaliLog = byaliLog;
  window.byaliDrainJsLog = function () {
    var out = window.__byaliJsLog || [];
    window.__byaliJsLog = [];
    return out;
  };

  var STYLE_ID = 'byali-rewrite-style';
  var MSG_REPLACE = 'byali-replace-do';
  var MSG_RESULT = 'byali-replace-result';
  var isTop = (window === window.top);

  if (!document.getElementById(STYLE_ID)) {
    var st = document.createElement('style');
    st.id = STYLE_ID;
    st.textContent = [
      '.byali-rewrite{display:inline;background:rgba(64,158,255,.12);border-bottom:1px dotted rgba(64,158,255,.85);padding:0 .05em;border-radius:.15em;}',
      '.byali-rewrite[data-showing="original"]{background:rgba(255,193,7,.14);border-bottom-color:rgba(255,152,0,.9);}',
      '.byali-rewrite-info{display:inline-flex;align-items:center;justify-content:center;margin-left:.25em;width:1.05em;height:1.05em;border-radius:50%;border:1px solid currentColor;font:600 .72em/1 sans-serif;cursor:pointer;opacity:.85;vertical-align:super;padding:0;background:transparent;color:inherit;}',
      '.byali-rewrite-info:hover{opacity:1;}'
    ].join('');
    (document.head || document.documentElement).appendChild(st);
  }

  function ensureStyleInDoc(doc) {
    if (doc.getElementById(STYLE_ID)) return;
    var host = document.getElementById(STYLE_ID);
    var st = doc.createElement('style');
    st.id = STYLE_ID;
    st.textContent = host ? host.textContent : '';
    (doc.head || doc.documentElement).appendChild(st);
  }

  function wireInfoButton(btn, wrap) {
    btn.addEventListener('click', function (ev) {
      ev.preventDefault();
      ev.stopPropagation();
      var showing = wrap.getAttribute('data-showing') || 'rewritten';
      var next = showing === 'rewritten' ? 'original' : 'rewritten';
      wrap.setAttribute('data-showing', next);
      var textNode = wrap.querySelector('.byali-rewrite-text');
      if (!textNode) return;
      textNode.textContent = next === 'original' ? (wrap.getAttribute('data-original') || '') : (wrap.getAttribute('data-rewritten') || '');
      btn.title = next === 'original' ? 'Show rewritten text' : 'Show original text';
      btn.setAttribute('aria-label', btn.title);
    });
  }

  function buildWrapper(doc, original, rewritten, rid) {
    ensureStyleInDoc(doc);
    var wrap = doc.createElement('span');
    wrap.className = 'byali-rewrite';
    wrap.setAttribute('data-byali-id', rid || '');
    wrap.setAttribute('data-original', original);
    wrap.setAttribute('data-rewritten', rewritten);
    wrap.setAttribute('data-showing', 'rewritten');
    var text = doc.createElement('span');
    text.className = 'byali-rewrite-text';
    text.textContent = rewritten;
    var btn = doc.createElement('button');
    btn.type = 'button';
    btn.className = 'byali-rewrite-info';
    btn.textContent = 'i';
    btn.title = 'Show original text';
    btn.setAttribute('aria-label', btn.title);
    wireInfoButton(btn, wrap);
    wrap.appendChild(text);
    wrap.appendChild(btn);
    return wrap;
  }

  // Nearest block-level ancestor — only this element gets dir forced (not siblings / body).
  function findBlockAncestor(node) {
    var el = node && node.nodeType === 1 ? node : (node && node.parentElement);
    var BLOCK = /^(P|DIV|LI|BLOCKQUOTE|H[1-6]|TD|TH|DT|DD|FIGCAPTION|SECTION|ARTICLE|ASIDE|HEADER|FOOTER|MAIN)$/i;
    while (el && el !== document.body && el !== document.documentElement) {
      if (el.tagName && BLOCK.test(el.tagName)) return el;
      el = el.parentElement;
    }
    return null;
  }

  function applyParagraphDirection(anchorNode, direction) {
    var d = String(direction || '').toLowerCase();
    if (d !== 'ltr' && d !== 'rtl') return false;
    var block = findBlockAncestor(anchorNode);
    if (!block) {
      // Fallback: at least force the rewrite span itself
      if (anchorNode && anchorNode.nodeType === 1) {
        anchorNode.setAttribute('dir', d);
        anchorNode.style.direction = d;
        anchorNode.style.unicodeBidi = 'isolate';
        return true;
      }
      return false;
    }
    block.setAttribute('dir', d);
    block.style.direction = d;
    block.style.textAlign = d === 'rtl' ? 'right' : 'left';
    block.setAttribute('data-byali-dir', d);
    byaliLog('INFO', 'forced paragraph direction', {
      dir: d,
      tag: block.tagName || '',
      preview: String(block.textContent || '').slice(0, 60)
    });
    return true;
  }

  function localSelectionInfo(original) {
    var info = {
      is_top: isTop,
      rangeCount: 0,
      selected_len: 0,
      selected_preview: '',
      isCollapsed: true,
      matches_original: null
    };
    try {
      var sel = window.getSelection ? window.getSelection() : null;
      if (!sel) return info;
      info.rangeCount = sel.rangeCount || 0;
      info.isCollapsed = !!sel.isCollapsed;
      var t = String(sel.toString() || '');
      info.selected_len = t.length;
      info.selected_preview = t.slice(0, 80);
      if (original) {
        info.matches_original = (t === original) || (t.trim() === String(original).trim());
      }
    } catch (e) {
      info.error = String(e && e.message ? e.message : e);
    }
    return info;
  }

  function findTextRange(doc, needle) {
    var want = String(needle || '');
    if (!want) return null;
    var root = doc.body || doc.documentElement;
    if (!root) return null;
    // Exact match inside one text node
    var walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    var node;
    while ((node = walker.nextNode())) {
      if (!node.nodeValue) continue;
      if (node.parentElement && node.parentElement.closest('.byali-rewrite')) continue;
      var idx = node.nodeValue.indexOf(want);
      if (idx < 0) continue;
      try {
        var range = doc.createRange();
        range.setStart(node, idx);
        range.setEnd(node, idx + want.length);
        return range;
      } catch (e) {}
    }
    // Cross-node exact concatenation
    var nodes = [];
    var full = '';
    walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    while ((node = walker.nextNode())) {
      if (!node.nodeValue) continue;
      if (node.parentElement && node.parentElement.closest('.byali-rewrite')) continue;
      nodes.push({ node: node, start: full.length });
      full += node.nodeValue;
    }
    var at = full.indexOf(want);
    if (at < 0) {
      var trimmed = want.trim();
      if (trimmed && trimmed !== want) at = full.indexOf(trimmed);
      if (at >= 0) want = trimmed;
    }
    if (at < 0) return null;
    var endAt = at + want.length;
    var startNode = null, startOff = 0, endNode = null, endOff = 0;
    for (var i = 0; i < nodes.length; i++) {
      var a = nodes[i].start;
      var b = a + nodes[i].node.nodeValue.length;
      if (!startNode && at >= a && at < b) {
        startNode = nodes[i].node;
        startOff = at - a;
      }
      if (endAt > a && endAt <= b) {
        endNode = nodes[i].node;
        endOff = endAt - a;
        break;
      }
    }
    if (!startNode || !endNode) return null;
    try {
      var r2 = doc.createRange();
      r2.setStart(startNode, startOff);
      r2.setEnd(endNode, endOff);
      return r2;
    } catch (e) {
      return null;
    }
  }

  // Replace inside THIS document only (works when called inside the book iframe).
  function replaceInThisDocument(original, rewritten, rid, textDirection) {
    byaliLog('INFO', 'replaceInThisDocument', {
      is_top: isTop,
      original_len: original ? String(original).length : 0,
      rewritten_len: rewritten ? String(rewritten).length : 0,
      text_direction: textDirection || 'auto',
      sel: localSelectionInfo(original)
    });
    var doc = document;
    var sel = window.getSelection ? window.getSelection() : null;

    function finishOk(wrap) {
      try {
        applyParagraphDirection(wrap, textDirection);
      } catch (eDir) {
        byaliLog('WARN', 'applyParagraphDirection failed', { error: String(eDir) });
      }
      return true;
    }

    // 1) Live selection in this frame
    if (sel && sel.rangeCount) {
      var range = sel.getRangeAt(0);
      var selected = range.toString();
      if (selected) {
        try {
          var wrap = buildWrapper(doc, original || selected, rewritten, rid);
          range.deleteContents();
          range.insertNode(wrap);
          sel.removeAllRanges();
          byaliLog('INFO', 'replace via live selection SUCCESS', { is_top: isTop, rid: rid || '' });
          return finishOk(wrap);
        } catch (e) {
          byaliLog('ERROR', 'live selection insert failed', {
            is_top: isTop,
            error: String(e && e.message ? e.message : e)
          });
        }
      }
    }

    // 2) Text search for the original passage in this document
    if (original) {
      var fr = findTextRange(doc, original) || findTextRange(doc, String(original).trim());
      if (fr) {
        try {
          var wrap2 = buildWrapper(doc, original, rewritten, rid);
          fr.deleteContents();
          fr.insertNode(wrap2);
          byaliLog('INFO', 'replace via text search SUCCESS', { is_top: isTop, rid: rid || '' });
          return finishOk(wrap2);
        } catch (e2) {
          byaliLog('ERROR', 'text search insert failed', {
            is_top: isTop,
            error: String(e2 && e2.message ? e2.message : e2)
          });
        }
      } else {
        byaliLog('DEBUG', 'text search found no match in this frame', {
          is_top: isTop,
          original_preview: String(original).slice(0, 80)
        });
      }
    }
    return false;
  }

  window.byaliReplaceInThisDocument = replaceInThisDocument;

  function reportResult(rid, ok, via) {
    var payload = { __byali: MSG_RESULT, rid: rid || '', ok: !!ok, via: via || '', is_top: isTop };
    byaliLog(ok ? 'INFO' : 'WARN', 'replace result', payload);
    if (isTop) {
      window.__byaliReplaceResult = { done: true, ok: !!ok, rid: rid || '', via: via || '' };
    } else {
      try {
        window.parent.postMessage(payload, '*');
      } catch (e) {
        byaliLog('ERROR', 'postMessage result failed', { error: String(e) });
      }
    }
  }

  // Listen for replace requests (iframe) / results (parent).
  // Re-assign handler on every inject so hot-reloads pick up new logic without stacking listeners.
  window.__byaliOnReplaceMessage = function (ev) {
    var data = ev && ev.data;
    if (!data || typeof data !== 'object') return;

    if (isTop && data.__byali === 'byali-js-log' && data.entry) {
      try {
        window.__byaliJsLog = window.__byaliJsLog || [];
        window.__byaliJsLog.push(data.entry);
        if (window.__byaliJsLog.length > 800) {
          window.__byaliJsLog.splice(0, window.__byaliJsLog.length - 800);
        }
      } catch (e) {}
      return;
    }

    if (!isTop && data.__byali === MSG_REPLACE) {
      byaliLog('INFO', 'iframe received replace request', {
        rid: data.rid || '',
        original_len: data.original ? String(data.original).length : 0,
        text_direction: data.text_direction || 'auto'
      });
      var ok = false;
      try {
        ok = replaceInThisDocument(
          data.original || '',
          data.rewritten || '',
          data.rid || '',
          data.text_direction || 'auto'
        );
      } catch (e) {
        byaliLog('ERROR', 'iframe replace threw', { error: String(e) });
      }
      reportResult(data.rid, ok, ok ? 'iframe' : 'iframe-fail');
      return;
    }

    if (isTop && data.__byali === MSG_RESULT) {
      byaliLog('INFO', 'parent received iframe replace result', data);
      // Prefer success if any iframe reports ok (multi-iframe books).
      var prev = window.__byaliReplaceResult;
      if (prev && prev.done && prev.ok) return;
      window.__byaliReplaceResult = {
        done: true,
        ok: !!data.ok,
        rid: data.rid || '',
        via: data.via || 'iframe'
      };
    }
  };
  if (!window.__byali_replace_msg_hook__) {
    window.__byali_replace_msg_hook__ = true;
    window.addEventListener('message', function (ev) {
      if (typeof window.__byaliOnReplaceMessage === 'function') {
        window.__byaliOnReplaceMessage(ev);
      }
    }, false);
  }

  function postReplaceToIframes(original, rewritten, rid, textDirection) {
    var frames = document.querySelectorAll('iframe');
    var sent = 0;
    var errors = [];
    for (var i = 0; i < frames.length; i++) {
      try {
        var win = frames[i].contentWindow;
        if (!win) {
          errors.push({ index: i, error: 'contentWindow null' });
          continue;
        }
        win.postMessage({
          __byali: MSG_REPLACE,
          original: original || '',
          rewritten: rewritten || '',
          rid: rid || '',
          text_direction: textDirection || 'auto'
        }, '*');
        sent++;
      } catch (e) {
        errors.push({ index: i, error: String(e && e.message ? e.message : e) });
      }
    }
    byaliLog('INFO', 'posted replace to iframes', { sent: sent, iframe_count: frames.length, errors: errors });
    return sent;
  }

  // Called from Python on the TOP frame. Kicks off async iframe replace.
  window.byaliStartReplace = function (original, rewritten, rid, textDirection) {
    byaliLog('INFO', 'byaliStartReplace', {
      is_top: isTop,
      original_len: original ? String(original).length : 0,
      rewritten_len: rewritten ? String(rewritten).length : 0,
      rid: rid || '',
      text_direction: textDirection || 'auto',
      local_sel: localSelectionInfo(original)
    });
    window.__byaliReplaceResult = { done: false, ok: false, rid: rid || '', via: '' };

    // Try this frame first (rare for book content, but cheap)
    if (replaceInThisDocument(original, rewritten, rid, textDirection)) {
      window.__byaliReplaceResult = { done: true, ok: true, rid: rid || '', via: 'top' };
      return true;
    }

    if (isTop) {
      var sent = postReplaceToIframes(original, rewritten, rid, textDirection);
      if (!sent) {
        // Last resort: text search already tried on top; mark failed soon
        window.__byaliReplaceResult = { done: true, ok: false, rid: rid || '', via: 'no-iframe' };
        byaliLog('ERROR', 'no iframes reachable for postMessage');
        return false;
      }
      // Async — Python must poll byaliGetReplaceResult
      return null;
    }
    return false;
  };

  window.byaliGetReplaceResult = function () {
    return window.__byaliReplaceResult || { done: false, ok: false, rid: '', via: '' };
  };

  // Back-compat name used by older callers: prefer StartReplace semantics on top.
  window.byaliReplaceSelection = function (original, rewritten, rid, textDirection) {
    if (isTop) {
      return window.byaliStartReplace(original, rewritten, rid, textDirection);
    }
    return replaceInThisDocument(original, rewritten, rid, textDirection);
  };

  window.byaliGetSelectedText = function () {
    var sel = window.getSelection ? window.getSelection() : null;
    var t = sel ? String(sel.toString() || '') : '';
    if (t) {
      byaliLog('DEBUG', 'byaliGetSelectedText local hit', { is_top: isTop, len: t.length });
      return t;
    }
    return '';
  };

  window.byaliDiagnoseSelection = function (original) {
    var probe = localSelectionInfo(original || '');
    var iframe_meta = [];
    if (isTop) {
      var frames = document.querySelectorAll('iframe');
      for (var i = 0; i < frames.length; i++) {
        var m = { index: i, has_contentWindow: false, has_contentDocument: false };
        try { m.has_contentWindow = !!frames[i].contentWindow; } catch (e) { m.cw_error = String(e); }
        try { m.has_contentDocument = !!frames[i].contentDocument; } catch (e2) { m.cd_error = String(e2); }
        iframe_meta.push(m);
      }
    }
    byaliLog('INFO', 'diagnoseSelection', { probe: probe, iframes: iframe_meta });
    return { probe: probe, iframes: iframe_meta };
  };

  // Same postMessage pattern for notes (iframe does the wrap)
  var MSG_NOTE = 'byali-note-do';
  var MSG_NOTE_RESULT = 'byali-note-result';

  window.byaliStartAttachNote = function (anchorText, note, meta) {
    meta = meta || {};
    window.__byaliNoteResult = { done: false, ok: false, id: meta.id || '' };
    byaliLog('INFO', 'byaliStartAttachNote', {
      is_top: isTop,
      anchor_len: anchorText ? String(anchorText).length : 0,
      note_len: note ? String(note).length : 0
    });
    // Local attempt if selection/text is here
    if (typeof window.byaliAttachNoteInThisDocument === 'function') {
      try {
        if (window.byaliAttachNoteInThisDocument(anchorText, note, meta)) {
          window.__byaliNoteResult = { done: true, ok: true, id: meta.id || '', via: isTop ? 'top' : 'iframe-local' };
          return true;
        }
      } catch (e) {}
    }
    if (!isTop) return false;
    var frames = document.querySelectorAll('iframe');
    var sent = 0;
    for (var i = 0; i < frames.length; i++) {
      try {
        frames[i].contentWindow.postMessage({
          __byali: MSG_NOTE,
          anchorText: anchorText || '',
          note: note || '',
          meta: meta || {}
        }, '*');
        sent++;
      } catch (e) {}
    }
    byaliLog('INFO', 'posted note attach to iframes', { sent: sent });
    if (!sent) {
      window.__byaliNoteResult = { done: true, ok: false, id: meta.id || '', via: 'no-iframe' };
      return false;
    }
    return null;
  };

  window.byaliGetNoteResult = function () {
    return window.__byaliNoteResult || { done: false, ok: false, id: '', via: '' };
  };

  window.__byaliOnNoteMessage = function (ev) {
    var data = ev && ev.data;
    if (!data || typeof data !== 'object') return;
    if (!isTop && data.__byali === MSG_NOTE) {
      var ok = false;
      try {
        if (typeof window.byaliAttachNoteInThisDocument === 'function') {
          ok = !!window.byaliAttachNoteInThisDocument(data.anchorText || '', data.note || '', data.meta || {});
        }
      } catch (e) {}
      try {
        window.parent.postMessage({
          __byali: MSG_NOTE_RESULT,
          ok: ok,
          id: (data.meta && data.meta.id) || ''
        }, '*');
      } catch (e2) {}
    }
    if (isTop && data.__byali === MSG_NOTE_RESULT) {
      var prevN = window.__byaliNoteResult;
      if (prevN && prevN.done && prevN.ok) return;
      window.__byaliNoteResult = { done: true, ok: !!data.ok, id: data.id || '', via: 'iframe' };
    }
  };
  if (!window.__byali_note_msg_hook__) {
    window.__byali_note_msg_hook__ = true;
    window.addEventListener('message', function (ev) {
      if (typeof window.__byaliOnNoteMessage === 'function') {
        window.__byaliOnNoteMessage(ev);
      }
    }, false);
  }

  window.__byali_ai_helper__ = true;
  window.__byali_ai_helper_ver__ = 3;
  byaliLog('INFO', 'Byali rewrite helper installed/updated', { is_top: isTop, ver: 3 });
})();
'''
