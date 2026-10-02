#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   - JS helpers injected into the ebook viewer ApplicationWorld
#   - Shared JS log buffer (byaliLog / byaliDrainJsLog) for diagnostics
#   - Replace current iframe selection with rewrite wrapper + i flip button
#   - Toggle original <-> rewritten on i click
#   - Detailed per-doc selection probes when replace fails (no behavior change)

"""JavaScript helpers for in-viewer Byali text rewrite + original flip."""

BYALI_VIEWER_HELPER_JS = r'''
(function () {
  // Always (re)install functions so logging updates apply on reinject.
  window.__byaliJsLog = window.__byaliJsLog || [];

  function byaliLog(level, msg, data) {
    try {
      window.__byaliJsLog.push({
        t: Date.now(),
        level: level || 'DEBUG',
        msg: String(msg || ''),
        data: data === undefined ? null : data
      });
      if (window.__byaliJsLog.length > 800) {
        window.__byaliJsLog.splice(0, window.__byaliJsLog.length - 800);
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
  if (!document.getElementById(STYLE_ID)) {
    var st = document.createElement('style');
    st.id = STYLE_ID;
    st.textContent = [
      '.byali-rewrite{display:inline;background:rgba(64,158,255,.12);border-bottom:1px dotted rgba(64,158,255,.85);padding:0 .05em;border-radius:.15em;}',
      '.byali-rewrite[data-showing="original"]{background:rgba(255,193,7,.14);border-bottom-color:rgba(255,152,0,.9);}',
      '.byali-rewrite-info{display:inline-flex;align-items:center;justify-content:center;margin-left:.25em;width:1.05em;height:1.05em;border-radius:50%;border:1px solid currentColor;font:600 .72em/1 sans-serif;cursor:pointer;opacity:.85;vertical-align:super;padding:0;background:transparent;color:inherit;}',
      '.byali-rewrite-info:hover{opacity:1;}'
    ].join('');
    document.documentElement.appendChild(st);
  }

  function bookDocs() {
    var docs = [];
    var iframeInfo = [];
    try {
      var iframes = document.querySelectorAll('iframe');
      for (var i = 0; i < iframes.length; i++) {
        var info = { index: i, accessible: false, error: null };
        try {
          var d = iframes[i].contentDocument;
          if (d) {
            docs.push(d);
            info.accessible = true;
            info.readyState = d.readyState;
          } else {
            info.error = 'contentDocument null';
          }
        } catch (e) {
          info.error = String(e && e.message ? e.message : e);
        }
        iframeInfo.push(info);
      }
    } catch (e) {
      byaliLog('WARN', 'iframe scan failed', { error: String(e) });
    }
    docs.push(document);
    byaliLog('DEBUG', 'bookDocs', {
      iframe_count: iframeInfo.length,
      docs: docs.length,
      iframes: iframeInfo
    });
    return docs;
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

  function probeDoc(doc, index, original) {
    var probe = {
      index: index,
      is_top: doc === document,
      has_getSelection: !!(doc.getSelection),
      rangeCount: 0,
      selected_len: 0,
      selected_preview: '',
      matches_original: null,
      activeElement: null
    };
    try {
      probe.activeElement = doc.activeElement ? (doc.activeElement.tagName || '') : null;
    } catch (e) {}
    try {
      var sel = doc.getSelection ? doc.getSelection() : null;
      if (!sel) {
        probe.error = 'getSelection returned null';
        return probe;
      }
      probe.rangeCount = sel.rangeCount || 0;
      probe.isCollapsed = !!sel.isCollapsed;
      if (sel.rangeCount) {
        var t = String(sel.toString() || '');
        probe.selected_len = t.length;
        probe.selected_preview = t.slice(0, 80);
        if (original) {
          probe.matches_original = (t === original) || (t.trim() === String(original).trim());
        }
        try {
          var r = sel.getRangeAt(0);
          probe.start_node = r.startContainer && r.startContainer.nodeName;
          probe.end_node = r.endContainer && r.endContainer.nodeName;
        } catch (e2) {
          probe.range_error = String(e2 && e2.message ? e2.message : e2);
        }
      }
    } catch (e) {
      probe.error = String(e && e.message ? e.message : e);
    }
    return probe;
  }

  window.byaliReplaceSelection = function (original, rewritten, rid) {
    byaliLog('INFO', 'byaliReplaceSelection called', {
      original_len: original ? String(original).length : 0,
      rewritten_len: rewritten ? String(rewritten).length : 0,
      rid: rid || '',
      original_preview: original ? String(original).slice(0, 80) : '',
      rewritten_preview: rewritten ? String(rewritten).slice(0, 80) : ''
    });
    var docs = bookDocs();
    var probes = [];
    for (var i = 0; i < docs.length; i++) {
      var doc = docs[i];
      var probe = probeDoc(doc, i, original);
      probes.push(probe);
      var sel = doc.getSelection ? doc.getSelection() : null;
      if (!sel || !sel.rangeCount) continue;
      var range = sel.getRangeAt(0);
      var selected = range.toString();
      if (!selected) {
        byaliLog('DEBUG', 'doc has rangeCount but empty toString', { index: i });
        continue;
      }
      byaliLog('INFO', 'attempting insert on doc', {
        index: i,
        selected_len: selected.length,
        selected_preview: selected.slice(0, 80)
      });
      try {
        var wrap = buildWrapper(doc, original || selected, rewritten, rid);
        range.deleteContents();
        range.insertNode(wrap);
        sel.removeAllRanges();
        byaliLog('INFO', 'replace SUCCESS', { index: i, rid: rid || '' });
        return true;
      } catch (e) {
        byaliLog('ERROR', 'insert failed', {
          index: i,
          error: String(e && e.message ? e.message : e),
          name: e && e.name ? e.name : ''
        });
      }
    }
    byaliLog('ERROR', 'replace FAILED — no usable selection', {
      probes: probes,
      helper_flag: !!window.__byali_ai_helper__
    });
    return false;
  };

  window.byaliGetSelectedText = function () {
    var docs = bookDocs();
    for (var i = 0; i < docs.length; i++) {
      var doc = docs[i];
      var sel = doc.getSelection ? doc.getSelection() : null;
      if (!sel) continue;
      var t = String(sel.toString() || '');
      if (t) {
        byaliLog('DEBUG', 'byaliGetSelectedText hit', { index: i, len: t.length });
        return t;
      }
    }
    byaliLog('DEBUG', 'byaliGetSelectedText empty');
    return '';
  };

  window.byaliDiagnoseSelection = function (original) {
    var docs = bookDocs();
    var probes = [];
    for (var i = 0; i < docs.length; i++) {
      probes.push(probeDoc(docs[i], i, original || ''));
    }
    byaliLog('INFO', 'diagnoseSelection', { probes: probes });
    return probes;
  };

  window.__byali_ai_helper__ = true;
  byaliLog('INFO', 'Byali rewrite helper installed/updated');
})();
'''
