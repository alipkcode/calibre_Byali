#!/usr/bin/env python
# License: GPLv3 Copyright: 2026, Ali / calibre_Byali
#
# Blueprint:
#   - JS helpers injected into the ebook viewer ApplicationWorld
#   - Replace current iframe selection with rewrite wrapper + i flip button
#   - Toggle original <-> rewritten on i click

"""JavaScript helpers for in-viewer Byali text rewrite + original flip."""

BYALI_VIEWER_HELPER_JS = r'''
(function () {
  if (window.__byali_ai_helper__) return;
  window.__byali_ai_helper__ = true;

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
    st.textContent = document.getElementById(STYLE_ID).textContent;
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

  window.byaliReplaceSelection = function (original, rewritten, rid) {
    var docs = bookDocs();
    for (var i = 0; i < docs.length; i++) {
      var doc = docs[i];
      var sel = doc.getSelection ? doc.getSelection() : null;
      if (!sel || !sel.rangeCount) continue;
      var range = sel.getRangeAt(0);
      var selected = range.toString();
      if (!selected) continue;
      if (original && selected !== original && selected.trim() !== String(original).trim()) {
        // still allow if selection is non-empty; user may have whitespace diffs
      }
      try {
        var wrap = buildWrapper(doc, original || selected, rewritten, rid);
        range.deleteContents();
        range.insertNode(wrap);
        sel.removeAllRanges();
        return true;
      } catch (e) {}
    }
    return false;
  };

  window.byaliGetSelectedText = function () {
    var docs = bookDocs();
    for (var i = 0; i < docs.length; i++) {
      var doc = docs[i];
      var sel = doc.getSelection ? doc.getSelection() : null;
      if (!sel) continue;
      var t = String(sel.toString() || '');
      if (t) return t;
    }
    return '';
  };
})();
'''
