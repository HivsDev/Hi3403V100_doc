/*!
 * inpage-find.js — 页内查找条（Ctrl+F 风格）
 * ==================================================================
 * 作用：在命中页（URL 带 ?h=，由 Material 生成 <mark data-md-highlight>）时，
 *       顶部显示一个查找条，支持上/下切换命中、计数、清除。
 *
 * 注意：Ctrl+F / Cmd+F 不拦截——交由浏览器原生查找处理。本查找条仅在
 *       「点击全文搜索结果、带 ?h= 进入页面」时自动唤起。
 *
 * 设计要点：
 *   1. 高亮沿用 Material 已有的 <mark data-md-highlight>（不改配色）。
 *      ?h= 进入时 Material 已包裹好 mark，直接导航即可。
 *   2. 用户在查找条里改了查询词 → 先 unwrap 旧 mark，再用 Material 同款
 *      逻辑在 .md-content 内重新包裹（大小写不敏感子串匹配）。
 *   3. 当前命中项加 .inpage-find-current 类（红色描边，不动底色）。
 *   4. 清除/关闭 → unwrap 全部 mark + 清 URL ?h= + 隐藏查找条。
 *
 * 暴露：window.InpageFind.open(query?) / window.InpageFind.close()
 */
(function (window, document) {
  "use strict";

  var InpageFind = {
    _bar: null,
    _input: null,
    _countEl: null,
    _currentIndex: -1,
    _marks: [],            // 当前命中的 mark 元素列表（按文档顺序）
    _lastQuery: "",
    _bound: false,
    _keyBound: false,      // 全局 keydown 是否已绑定（避免 SPA 重复绑定）
    _lastPathname: "",     // 上次处理的 pathname，用于识别「同页导航」
  };

  // 与全文检索的分词一致：仅按空白切分。
  var SEPARATOR_RE = /\s+/;

  // ---------- DOM 构建 ----------
  // SVG 图标（单色，currentColor 跟随按钮颜色）
  var ICON_SEARCH =
    '<svg viewBox="0 0 24 24"><path d="M15.5 14h-.79l-.28-.27a6.5 6.5 0 1 0-.7.7l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0A4.5 4.5 0 1 1 14 9.5 4.5 4.5 0 0 1 9.5 14z"/></svg>';
  var ICON_UP =
    '<svg viewBox="0 0 24 24"><path d="M7.41 15.41L12 10.83l4.59 4.58L18 14l-6-6-6 6z"/></svg>';
  var ICON_DOWN =
    '<svg viewBox="0 0 24 24"><path d="M7.41 8.59L12 13.17l4.59-4.58L18 10l-6 6-6-6z"/></svg>';
  var ICON_CLOSE =
    '<svg viewBox="0 0 24 24"><path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/></svg>';

  function buildBar() {
    if (InpageFind._bar) return InpageFind._bar;

    var bar = document.createElement("div");
    bar.className = "inpage-find";
    bar.setAttribute("role", "search");
    bar.style.display = "none";
    bar.innerHTML =
      '<span class="inpage-find__icon" aria-hidden="true">' + ICON_SEARCH + "</span>" +
      '<input type="text" class="inpage-find__input" placeholder="页内查找…" ' +
      'aria-label="页内查找" autocomplete="off" autocapitalize="off" ' +
      'autocorrect="off" spellcheck="false">' +
      '<span class="inpage-find__count" aria-live="polite"></span>' +
      '<span class="inpage-find__sep"></span>' +
      '<button type="button" class="inpage-find__btn inpage-find__btn--up" ' +
      'title="上一个 (↑ / Shift+Enter)" aria-label="上一个" disabled>' + ICON_UP + "</button>" +
      '<button type="button" class="inpage-find__btn inpage-find__btn--down" ' +
      'title="下一个 (↓ / Enter)" aria-label="下一个" disabled>' + ICON_DOWN + "</button>" +
      '<span class="inpage-find__sep"></span>' +
      '<button type="button" class="inpage-find__btn inpage-find__btn--close" ' +
      'title="关闭 (Esc)" aria-label="关闭查找">' + ICON_CLOSE + "</button>";

    document.body.appendChild(bar);
    InpageFind._bar = bar;
    InpageFind._input = bar.querySelector(".inpage-find__input");
    InpageFind._countEl = bar.querySelector(".inpage-find__count");

    bindBarEvents();
    return bar;
  }

  function bindBarEvents() {
    if (InpageFind._bound) return;
    InpageFind._bound = true;

    var input = InpageFind._input;
    // 回车 = 下一个，Shift+回车 = 上一个
    // ↑/↓ = 上一个/下一个（与浏览器 Ctrl+F 查找条一致）
    input.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        if (e.shiftKey) gotoPrev(); else gotoNext();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        gotoNext();
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        gotoPrev();
      } else if (e.key === "Escape") {
        e.preventDefault();
        close();
      }
    });
    // 输入变化 → 重新高亮
    input.addEventListener("input", function () {
      applyQuery(input.value);
    });

    InpageFind._bar.querySelector(".inpage-find__btn--up").addEventListener("click", gotoPrev);
    InpageFind._bar.querySelector(".inpage-find__btn--down").addEventListener("click", gotoNext);
    InpageFind._bar.querySelector(".inpage-find__btn--close").addEventListener("click", close);
  }

  // ---------- 高亮管理 ----------
  // 移除 .md-content 内所有 Material 高亮 mark（保留文字）
  function clearMarks() {
    var content = getContentRoot();
    if (!content) return;
    var marks = content.querySelectorAll("mark[data-md-highlight]");
    for (var i = 0; i < marks.length; i++) {
      unwrapMark(marks[i]);
    }
    // 清除当前项标记类（已随 unwrap 消失，保险）
    InpageFind._marks = [];
    InpageFind._currentIndex = -1;
  }

  function unwrapMark(mark) {
    var parent = mark.parentNode;
    while (mark.firstChild) {
      parent.insertBefore(mark.firstChild, mark);
    }
    parent.removeChild(mark);
    parent.normalize();
  }

  function getContentRoot() {
    return document.querySelector(".md-content") || document.querySelector(".md-typeset") || document.body;
  }

  // 在 .md-content 内按 query 重新高亮（大小写不敏感子串）
  // query 与现有 mark 的查询一致时直接复用（由 Material 生成），避免重复包裹。
  function applyQuery(query) {
    query = (query || "").trim();
    var lowerQ = query.toLowerCase();

    // 与上次查询相同：不重做高亮，仅刷新 mark 列表与定位
    if (lowerQ && lowerQ === InpageFind._lastQuery) {
      refreshMarks();
      return;
    }

    // 查询为空：清除高亮与计数
    if (!lowerQ) {
      clearMarks();
      InpageFind._lastQuery = "";
      updateCount(0, -1);
      return;
    }

    // 查询变化：清除旧高亮，重新包裹
    clearMarks();
    InpageFind._lastQuery = lowerQ;
    highlightInContent(query);
    refreshMarks();
  }

  // 在 .md-content 的文本节点中匹配 query，包裹 <mark data-md-highlight>
  // 大小写不敏感；排除 script/style/已经高亮的区域。
  function highlightInContent(query) {
    var root = getContentRoot();
    if (!root) return;

    var lowerQ = query.toLowerCase();
    var tokens = query.split(SEPARATOR_RE).filter(Boolean);
    var hasSeparator = tokens.length && query.split(SEPARATOR_RE).join("") !== query;

    // 高亮词选择策略：
    //   1) 优先用整句作为 needle——整句在某个文本节点内完整出现时，只高亮整句，
    //      避免把 "WS63 是一款...设计" 拆成 ws63/soc 等分别高亮导致计数虚高。
    //   2) 整句在任意单个文本节点内无完整命中时，回退到分词高亮（与全文检索分词一致）。
    //      注意：整句若跨 DOM 节点（如被 <strong> 包裹导致断裂），单节点内匹配不到，
    //      此时回退分词——这是可接受的边界情况。
    var needles;
    if (!hasSeparator) {
      needles = [query];
    } else {
      // 探测整句是否在任意单个文本节点内完整命中
      var wholeHit = false;
      var walker0 = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
        acceptNode: function (node) {
          var p = node.parentNode;
          if (!p) return NodeFilter.FILTER_REJECT;
          var tag = p.nodeName.toLowerCase();
          if (tag === "script" || tag === "style" || tag === "noscript") return NodeFilter.FILTER_REJECT;
          if (tag === "mark" && p.getAttribute("data-md-highlight") !== null) return NodeFilter.FILTER_REJECT;
          if (p.closest && p.closest(".inpage-find")) return NodeFilter.FILTER_REJECT;
          if (p.closest && p.closest('[aria-hidden="true"], .search-alias')) return NodeFilter.FILTER_REJECT;
          if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
          return lowerQ && node.nodeValue.toLowerCase().indexOf(lowerQ) !== -1
            ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT;
        }
      });
      if (walker0.nextNode()) wholeHit = true;
      needles = wholeHit ? [query] : tokens;
    }
    if (!needles.length) return;

    var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: function (node) {
        var p = node.parentNode;
        if (!p) return NodeFilter.FILTER_REJECT;
        var tag = p.nodeName.toLowerCase();
        // 跳过脚本/样式/已高亮 mark/查找条自身
        if (tag === "script" || tag === "style" || tag === "noscript") return NodeFilter.FILTER_REJECT;
        if (tag === "mark" && p.getAttribute("data-md-highlight") !== null) return NodeFilter.FILTER_REJECT;
        if (p.closest && p.closest(".inpage-find")) return NodeFilter.FILTER_REJECT;
        // 对辅助技术隐藏的元素，导致页内查找计数虚高
        if (p.closest && p.closest('[aria-hidden="true"], .search-alias')) return NodeFilter.FILTER_REJECT;
        if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });

    var toWrap = [];
    var n;
    while ((n = walker.nextNode())) {
      var text = n.nodeValue;
      var lower = text.toLowerCase();
      // 该文本节点是否命中任一 needle
      var hit = false;
      for (var i = 0; i < needles.length; i++) {
        if (lower.indexOf(needles[i].toLowerCase()) !== -1) { hit = true; break; }
      }
      if (!hit) continue;
      toWrap.push({ node: n, text: text, lower: lower });
    }

    for (var j = 0; j < toWrap.length; j++) {
      wrapTextWithMarks(toWrap[j].node, toWrap[j].text, toWrap[j].lower, needles);
    }
  }

  // 把一个文本节点按命中位置切成多段，命中部分包 mark
  function wrapTextWithMarks(textNode, text, lower, needles) {
    // 找出所有命中区间 [start,end)
    var ranges = [];
    for (var i = 0; i < needles.length; i++) {
      var nl = needles[i].toLowerCase();
      var idx = 0;
      while ((idx = lower.indexOf(nl, idx)) !== -1) {
        ranges.push([idx, idx + nl.length]);
        idx += nl.length;
      }
    }
    if (!ranges.length) return;
    ranges.sort(function (a, b) { return a[0] - b[0]; });
    // 合并重叠
    var merged = [ranges[0]];
    for (var k = 1; k < ranges.length; k++) {
      var last = merged[merged.length - 1];
      if (ranges[k][0] <= last[1]) last[1] = Math.max(last[1], ranges[k][1]);
      else merged.push(ranges[k]);
    }

    var parent = textNode.parentNode;
    var frag = document.createDocumentFragment();
    var cursor = 0;
    for (var m = 0; m < merged.length; m++) {
      var s = merged[m][0], e = merged[m][1];
      // 命中前的普通文本
      if (s > cursor) frag.appendChild(document.createTextNode(text.slice(cursor, s)));
      // 命中片段包 mark
      var mark = document.createElement("mark");
      mark.setAttribute("data-md-highlight", "");
      mark.textContent = text.slice(s, e);
      frag.appendChild(mark);
      cursor = e;
    }
    // 尾部普通文本
    if (cursor < text.length) frag.appendChild(document.createTextNode(text.slice(cursor)));
    parent.replaceChild(frag, textNode);
  }

  // 刷新 mark 列表（按文档顺序），并定位到第 1 个命中。
  // _skipFirstFocus 为 true 时（如从搜索结果进入页面，锚点已负责定位）：
  // 只生成高亮与 current 标记，不滚动，避免与锚点定位打架造成页面横跳。
  function refreshMarks() {
    var root = getContentRoot();
    InpageFind._marks = root ? Array.prototype.slice.call(root.querySelectorAll("mark[data-md-highlight]")) : [];
    var skipScroll = InpageFind._skipFirstFocus === true;
    InpageFind._skipFirstFocus = false; // 一次性标志，消费即清
    if (InpageFind._marks.length) {
      InpageFind._currentIndex = 0;
      focusCurrent(skipScroll ? false : true);
    } else {
      InpageFind._currentIndex = -1;
    }
    updateCount(InpageFind._marks.length, InpageFind._currentIndex);
  }

  // ---------- 导航 ----------
  function focusCurrent(scroll) {
    var idx = InpageFind._currentIndex;
    var marks = InpageFind._marks;
    // 清除其他项的当前标记
    for (var i = 0; i < marks.length; i++) {
      marks[i].classList.remove("inpage-find-current");
    }
    if (idx < 0 || idx >= marks.length) return;
    var cur = marks[idx];
    cur.classList.add("inpage-find-current");
    if (scroll) {
      // 滚动到可视区中部，留出顶部查找条/header 空间
      try {
        cur.scrollIntoView({ block: "center", behavior: "smooth" });
      } catch (e) {
        cur.scrollIntoView();
      }
    }
    updateCount(marks.length, idx);
  }

  function gotoNext() {
    var marks = InpageFind._marks;
    if (!marks.length) return;
    InpageFind._currentIndex = (InpageFind._currentIndex + 1) % marks.length;
    focusCurrent(true);
  }

  function gotoPrev() {
    var marks = InpageFind._marks;
    if (!marks.length) return;
    InpageFind._currentIndex = (InpageFind._currentIndex - 1 + marks.length) % marks.length;
    focusCurrent(true);
  }

  function updateCount(total, current) {
    var el = InpageFind._countEl;
    var upBtn = InpageFind._bar && InpageFind._bar.querySelector(".inpage-find__btn--up");
    var downBtn = InpageFind._bar && InpageFind._bar.querySelector(".inpage-find__btn--down");
    if (!el) return;
    if (total === 0) {
      el.textContent = InpageFind._lastQuery ? "无结果" : "";
    } else {
      el.textContent = (current + 1) + " / " + total;
    }
    if (upBtn) upBtn.disabled = total === 0;
    if (downBtn) downBtn.disabled = total === 0;
  }

  // ---------- 对齐到内容区域 ----------
  // 查找条比搜索弹框窄（内容区宽度的 60%），在内容区内水平居中。
  // 用 left + width 定位（不用 right），避免与 Material 规则冲突。
  function alignToContent() {
    var bar = InpageFind._bar;
    if (!bar) return;
    var content = document.querySelector(".md-content");
    if (!content) return;
    var rect = content.getBoundingClientRect();
    var viewportWidth = document.documentElement.clientWidth;
    var margin = 6;
    var contentLeft = rect.left + margin;
    var contentWidth = rect.width - margin * 2;
    // 查找条宽度 = 内容区宽度的 60%，但不超过 560px、不小于 320px
    var barWidth = Math.max(320, Math.min(560, Math.round(contentWidth * 0.6)));
    // 在内容区内居中
    var left = contentLeft + Math.round((contentWidth - barWidth) / 2);
    bar.style.setProperty("left", left + "px", "important");
    bar.style.setProperty("right", "auto", "important");
    bar.style.setProperty("width", barWidth + "px", "important");
    bar.style.setProperty("transform", "none", "important");
  }

  // ---------- URL ?h= 处理 ----------
  function getHParam() {
    try {
      return new URLSearchParams(window.location.search).get("h") || "";
    } catch (e) { return ""; }
  }

  function clearHParam() {
    try {
      var url = new URL(window.location.href);
      if (url.searchParams.has("h")) {
        url.searchParams.delete("h");
        window.history.replaceState(null, "", url.toString());
      }
    } catch (e) {}
  }

  // ---------- 开放 API ----------
  // open(query?)：显示查找条；query 省略时取 URL ?h=，再省略则空
  InpageFind.open = function (query, opts) {
    var bar = buildBar();
    bar.style.display = "flex";
    document.body.classList.add("inpage-find-active");
    // 左右居中对齐到内容区域（与搜索弹框用同一套对齐逻辑）
    alignToContent();

    // opts.focusFirst === false：打开后不定位到第一个匹配（由调用方控制滚动，
    // 如从搜索结果进入页面时定位职责在锚点，查找条只负责高亮，避免滚动打架）
    InpageFind._skipFirstFocus = !!(opts && opts.focusFirst === false);

    var q = (typeof query === "string" ? query : getHParam()) || "";
    // 仅当输入框当前为空或与新值不同时才赋值（避免覆盖用户正在输入）
    if (InpageFind._input.value !== q) {
      InpageFind._input.value = q;
    }
    applyQuery(q);
    InpageFind._input.focus();
    // 选中末尾便于继续输入
    var len = InpageFind._input.value.length;
    try { InpageFind._input.setSelectionRange(len, len); } catch (e) {}
  };

  InpageFind.close = function () { close(); };

  function close() {
    // 取消尚未触发的自动唤起定时器
    if (InpageFind._autoOpenTimer) {
      clearTimeout(InpageFind._autoOpenTimer);
      InpageFind._autoOpenTimer = null;
    }
    if (!InpageFind._bar) return;
    InpageFind._bar.style.display = "none";
    document.body.classList.remove("inpage-find-active");
    // 关闭即清除高亮 + 清 URL ?h=
    clearMarks();
    InpageFind._lastQuery = "";
    clearHParam();
    if (InpageFind._input) InpageFind._input.value = "";
    updateCount(0, -1);
  }

  // ---------- 快捷键 ----------
  // 注意：Ctrl+F / Cmd+F 不拦截——交由浏览器原生查找处理。
  // 本查找条仅在「点击全文搜索结果、带 ?h= 进入页面」时自动唤起。
  function onkeydown(e) {
    // 查找条可见时 Esc 关闭（即便焦点不在输入框）
    if (e.key === "Escape" && InpageFind._bar && InpageFind._bar.style.display !== "none") {
      e.preventDefault();
      close();
    }
  }

  // ---------- 自动唤起：带 ?h= 进入页面 ----------
  // hOverride：SPA 导航时 Material instant 会丢弃 URL 查询参数（location.search
  // 里根本没有 ?h=），此时由调用方把点击锚点 href 中截获的查询词传入，
  // 不能依赖 getHParam() 读当前 URL。
  function autoOpenFromHash(hOverride) {
    // 按「导航来源」决定是否唤起：
    //  - link（点击普通超链接/目录锚点触发的整页加载）：不弹，用户只想跳转到链接位置
    //  - result（点击搜索结果）：正常弹，帮助用户在结果页内逐个定位关键词
    //  - 无标记（刷新/直接访问带 ?h= 的 URL）：正常弹
    var src = null;
    try { src = sessionStorage.getItem('ss-nav-src'); } catch (e) {}
    try { sessionStorage.removeItem('ss-nav-src'); } catch (e) {}
    if (src === 'link') return;

    var h = (typeof hOverride === "string" && hOverride) || getHParam();
    if (h) {
      // 取消上一次尚未触发的定时器（SPA 换页可能连续触发）
      if (InpageFind._autoOpenTimer) clearTimeout(InpageFind._autoOpenTimer);
      // 等 Material 完成 ?h= 高亮包裹（它订阅 location$，有延迟）
      InpageFind._autoOpenTimer = setTimeout(function () {
        InpageFind._autoOpenTimer = null;
        // focusFirst:false —— 定位职责在 URL 锚点（搜索结果链接带 #hash），
        // 查找条只负责高亮，不滚动到第一个匹配，避免与锚点定位打架
        InpageFind.open(h, { focusFirst: false });
      }, 350);
    }
  }

  // ---------- 点击截获：从锚点 href 抓取 ?h=（SPA 导航的唯一查询词来源）----------
  // Material instant 导航会丢弃 URL 查询参数，落地后 location.search 无 ?h=。
  // 在点击瞬间（capture 阶段）从被点锚点的 href 中截获 h 存入模块变量，
  // 供 onSpaNavigate 在 SPA 换页完成后使用。整页加载场景 JS 状态重置，无副作用。
  //
  // 同时剥离 href 中的 ?h= 参数：Material instant 导航会用完整 URL（含 ?h=）
  // 发 XHR 请求抓取目标页面。静态服务器（OBS）可能对带 query 参数的 .html
  // 返回 404。在 capture 阶段把 ?h= 从 href 移除，Material 就会用干净 URL 发
  // XHR；随后由 location$ 订阅把 ?h= 通过 replaceState 补回地址栏。
  function onClickCapture(e) {
    if (e.defaultPrevented || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) {
      InpageFind._pendingH = null;
      return;
    }
    var el = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!el) return;
    var h = null;
    try { h = new URL(el.href, window.location.href).searchParams.get("h"); } catch (err) {}
    InpageFind._pendingH = h || null;

    // 剥离 ?h=：让 Material instant 用干净 URL 发 XHR，避免静态服务器 404
    if (h) {
      try {
        var url = new URL(el.href, window.location.href);
        url.searchParams.delete("h");
        var origHref = el.getAttribute("href");
        el.setAttribute("href", url.href);
        // 微任务中恢复原始 href（Material 已在 bubble 阶段同步读取 el.href）
        Promise.resolve().then(function () {
          el.setAttribute("href", origHref);
        });
      } catch (err2) {}
    }
  }

  // ---------- 初始化（幂等，支持 SPA）----------
  function init() {
    // 首次：绑定全局快捷键（capture 阶段，先于浏览器原生查找拦截）
    if (!InpageFind._keyBound) {
      document.addEventListener("keydown", onkeydown, true);
      InpageFind._keyBound = true;
    }
    // 记录初始 pathname，供 onLocationChange 判断「同页导航」用
    if (!InpageFind._lastPathname) {
      try { InpageFind._lastPathname = new URL(window.location.href).pathname; } catch (e) {}
    }
    autoOpenFromHash();
  }

  // SPA 导航后处理：跨页导航时按导航来源决定开关查找条
  function onSpaNavigate() {
    // SPA 导航不消费「导航来源」标记，清除避免残留影响下次刷新的自动唤起
    try { sessionStorage.removeItem('ss-nav-src'); } catch (e) {}
    var curPath = "";
    try { curPath = new URL(window.location.href).pathname; } catch (e) {}
    var samePath = InpageFind._lastPathname && curPath === InpageFind._lastPathname;
    InpageFind._lastPathname = curPath;
    if (samePath) {
      InpageFind._pendingH = null;
      return; // 同页导航：由 onLocationChange 处理关闭
    }

    // 跨页导航（新内容就绪）
    var pendingH = InpageFind._pendingH;
    InpageFind._pendingH = null;

    if (pendingH) {
      // 导航源自搜索结果（onClickCapture 截获了 ?h=）：打开/保持查找条
      if (InpageFind._bar && InpageFind._bar.style.display !== "none") {
        var q = InpageFind._input.value;
        InpageFind._lastQuery = "";
        applyQuery(q);
      } else {
        autoOpenFromHash(pendingH);
      }
    } else {
      // 导航非源自搜索结果（如点击左侧目录）：关闭查找条
      // 与本地无 instant 导航、整页刷新后查找条消失的行为对齐
      if (InpageFind._bar && InpageFind._bar.style.display !== "none") {
        close();
      }
    }
  }

  // location$ 兜底：同 pathname 导航（如点击右侧目录锚点）时 material 的 document$
  // 不 emit，由此处兜底。同页导航时按 _pendingH 决定：
  //  - _pendingH 有值 = 搜索结果指向当前页（同 pathname 不同 hash/query）→ 唤起查找条
  //  - _pendingH 无值 = 用户点了 TOC 锚点 → 关闭查找条（脱离搜索上下文）
  // 注意：跨页导航时【不能】在此更新 _lastPathname——SPA 下 location$ 先于
  // document$ 触发，提前写入新路径会让 onSpaNavigate 误判为"同页导航"而静默
  // 返回，导致 autoOpenFromHash（搜索结果唤起查找条）永远不执行。
  function onLocationChange() {
    var curPath = "";
    try { curPath = new URL(window.location.href).pathname; } catch (e) {}
    var samePath = InpageFind._lastPathname && curPath === InpageFind._lastPathname;
    // 跨页导航：不介入，也不更新 _lastPathname（留给 onSpaNavigate 判断与更新）
    if (!samePath) return;
    // 同页导航
    var pendingH = InpageFind._pendingH;
    InpageFind._pendingH = null;
    if (pendingH) {
      // 搜索结果指向当前页（如 version.html → version.html?h=x#x）：唤起查找条
      autoOpenFromHash(pendingH);
    } else {
      // 点击右侧 TOC 锚点等：关闭查找条
      if (InpageFind._bar && InpageFind._bar.style.display !== "none") {
        close();
      }
    }
  }

  function boot() {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", init);
    } else {
      init();
    }
    // 点击截获（capture，一次注册）：从被点锚点 href 抓取 ?h=，供 SPA 换页后唤起
    if (!InpageFind._clickBound) {
      document.addEventListener("click", onClickCapture, true);
      InpageFind._clickBound = true;
    }
    // Material 的 document$ 在 SPA 导航后 emit
    try {
      if (window.document$ && typeof window.document$.subscribe === "function") {
        window.document$.subscribe(function () {
          setTimeout(onSpaNavigate, 150);
        });
      }
    } catch (e) {}
    // location$ 兜底：同 pathname 导航（如点击右侧目录锚点）时 material 的 document$
    // 不 emit，由此处兜底关闭查找条。
    try {
      if (window.location$ && typeof window.location$.subscribe === "function") {
        window.location$.subscribe(function () {
          setTimeout(onLocationChange, 150);
        });
      }
    } catch (e) {}
    // location$ 同步订阅：在 Material pushState（干净 URL）后立即把 ?h= 补回地址栏。
    // onClickCapture 已从 href 剥离 ?h= 使 Material 用干净 URL 发 XHR，
    // 此处把 ?h= 写回 URL 供 Material search.highlight 读取及用户分享/收藏。
    // 不消费 _pendingH（留给 onSpaNavigate 使用）。
    try {
      if (window.location$ && typeof window.location$.subscribe === "function" && !InpageFind._urlRestoreBound) {
        window.location$.subscribe(function () {
          if (!InpageFind._pendingH) return;
          try {
            var url = new URL(window.location.href);
            if (!url.searchParams.has("h")) {
              url.searchParams.set("h", InpageFind._pendingH);
              history.replaceState(null, "", url.toString());
            }
          } catch (e) {}
        });
        InpageFind._urlRestoreBound = true;
      }
    } catch (e) {}
  }

  window.InpageFind = InpageFind;
  boot();
})(window, document);
