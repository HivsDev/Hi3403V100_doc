/*!
 * substring-search.js — 任意子串模糊检索（纯前端，零后端）
 * ==================================================================
 * 作用：补充 mkdocs-material 内置 lunr 搜索（仅支持 token 前缀匹配）的能力，
 *       支持「任意子串」检索。例如输入 sta_connect / connect / set_fixed
 *       均可命中 wifi_sta_connect / wifi_set_fixed_tx_rate 等条目。
 *
 * 工作原理：
 *   1. 首次打开搜索时懒加载 fetch search/search_index.json，缓存扁平文档数组；
 *   2. 用户输入 → 转小写 → 多 token（空格切分）AND 子串匹配；
 *   3. 评分：标题命中权重远高于正文；完整词 > 前缀 > 子串；多 token 连续命中加权；
 *   4. 按得分排序后分页渲染（首屏 pageSize 条，滚动/点击加载更多），
 *      复用 material 结果项视觉风格。
 *
 * 与原生搜索的关系：
 *   - 输入 ≥ 1 字符时显示子串结果、隐藏原生结果（单字符也走子串检索，
 *     保证任意输入长度下的结果展示风格一致）；
 *   - 输入为空时回退到原生结果；
 *   - 原生 worker 与本模块互不干扰，可独立回退。
 *
 * 暴露：window.SubstringSearch.init()，幂等。
 */
(function (window, document) {
  "use strict";

  var SubstringSearch = {
    _inited: false,
    _docs: null,          // 扁平化后的文档数组 [{loc, title, text}]
    _loading: null,       // 加载 Promise
    _lastQuery: "",
    _debounceTimer: null,
    _allResults: [],      // 当前查询的全部匹配结果（已排序）
    _renderedCount: 0,    // 已渲染的条目数（分页游标）
    _scrollListener: null,// 滚动监听器引用（便于卸载）
  };

  // ---- 配置 ----
  var CONFIG = {
    minQueryLength: 1,     // 至少 1 字符即启用子串检索（单字符也走子串路径，保持展示风格一致）
    pageSize: 20,          // 每页渲染条目数（无限滚动分步加载）
    debounceMs: 150,
    historyKey: "ws63_search_history", // localStorage key
    historyMax: 10,        // 历史词条最大保留条数
  };

  // ---- 历史搜索词条存储（localStorage，最近 N 条，去重置顶）----
  var HistoryStore = {
    get: function () {
      try {
        var raw = localStorage.getItem(CONFIG.historyKey);
        if (!raw) return [];
        var arr = JSON.parse(raw);
        return Array.isArray(arr) ? arr : [];
      } catch (e) { return []; }
    },
    save: function (list) {
      try { localStorage.setItem(CONFIG.historyKey, JSON.stringify(list.slice(0, CONFIG.historyMax))); }
      catch (e) {}
    },
    add: function (term) {
      term = (term || "").trim();
      if (!term) return;
      var list = HistoryStore.get();
      // 去重：已存在则移到队首
      var idx = list.indexOf(term);
      if (idx !== -1) list.splice(idx, 1);
      list.unshift(term);
      HistoryStore.save(list);
    },
    remove: function (term) {
      var list = HistoryStore.get();
      var idx = list.indexOf(term);
      if (idx !== -1) { list.splice(idx, 1); HistoryStore.save(list); }
    },
    clear: function () { HistoryStore.save([]); },
  };
  SubstringSearch.HistoryStore = HistoryStore;

  // ---- 站点根 URL 缓存 ----
  // __config.base 是相对当前页面的相对路径（如 "." / "../.."），仅在首次整页加载时
  // 与当前页面路径匹配。SPA（navigation.instant）导航后，Material 把内存里的
  // config.base 改写成绝对 URL，但【不会】更新 DOM 中 #__config 元素的 textContent
  // —— 它仍保留首屏页面的旧 base 值。若每次都从 #__config 读 base，SPA 导航后再
  // 搜索会拿到过期值：旧 base（"."）+ 新的深页 URL → resolveUrl 会多拼当前页的
  // 目录层级 → 404、"重复点击搜索会 404"、"会多拼一级"。
  // 因此在脚本首次执行（首屏整页加载，__config 新鲜）时，把相对 base 解析为绝对
  // 站点根 URL 并缓存，后续 SPA 导航不再重读 #__config，直接复用缓存值。
  var _siteRoot = null;
  function getSiteRoot() {
    if (_siteRoot) return _siteRoot;
    var base = ".";
    var cfgEl = document.getElementById("__config");
    if (cfgEl) {
      try {
        var cfg = JSON.parse(cfgEl.textContent);
        if (cfg && cfg.base) base = cfg.base;
      } catch (e) {}
    }
    // new URL(base + "/", window.location.href) 把相对 base 解析为绝对站点根 URL
    // （如 "http://site.com/"）；base + "/" 是纯路径相对 URL，不会继承当前页的
    // query/hash。结果在整站 SPA 导航中保持稳定，无需重算。
    _siteRoot = new URL(base + "/", window.location.href).href;
    return _siteRoot;
  }

  // 计算索引文件的绝对 URL（相对站点根），兼容子页面路径。
  // 复用 getSiteRoot() 缓存：SPA 导航后 __config.base 已过期，不能每次重读。
  function getIndexUrl() {
    return getSiteRoot() + "search/search_index.json";
  }

  // ---- 索引加载与扁平化 ----
  function loadIndex() {
    if (SubstringSearch._docs) {
      return Promise.resolve(SubstringSearch._docs);
    }
    if (SubstringSearch._loading) {
      return SubstringSearch._loading;
    }
    SubstringSearch._loading = fetch(getIndexUrl())
      .then(function (r) {
        if (!r.ok) throw new Error("search index HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        var raw = (data && data.docs) || [];
        // 预处理：剥离 HTML 标签、归一化空白、小写化
        var docs = [];
        for (var i = 0; i < raw.length; i++) {
          var d = raw[i];
          var title = stripHtml(d.title || "").trim();
          var text = stripHtml(d.text || "").replace(/\s+/g, " ").trim();
          docs.push({
            loc: d.location,
            title: title,
            titleLower: title.toLowerCase(),
            text: text,
            textLower: text.toLowerCase(),
          });
        }
        SubstringSearch._docs = docs;
        SubstringSearch._loading = null;
        return docs;
      })
      .catch(function (err) {
        SubstringSearch._loading = null;
        console.warn("[substring-search] 索引加载失败，回退原生搜索:", err);
        return null;
      });
    return SubstringSearch._loading;
  }

  function stripHtml(html) {
    if (!html) return "";
    // 移除标签、解码常见实体
    return String(html)
      .replace(/<[^>]+>/g, " ")
      .replace(/&lt;/g, "<")
      .replace(/&gt;/g, ">")
      .replace(/&amp;/g, "&")
      .replace(/&quot;/g, '"')
      .replace(/&#39;/g, "'")
      .replace(/&nbsp;/g, " ");
  }

  // ---- 查询与评分 ----
  // 把查询按空白切成 token；每个 token 在标题/正文中做子串匹配。
  function search(query) {
    var docs = SubstringSearch._docs;
    if (!docs) return [];
    var q = (query || "").trim().toLowerCase();
    if (!q) return [];
    var tokens = q.split(/\s+/).filter(Boolean);
    if (!tokens.length) return [];

    var results = [];
    for (var i = 0; i < docs.length; i++) {
      var d = docs[i];
      var score = 0;
      var allHit = true;
      var titleHitCount = 0;
      var firstTextHitIdx = -1; // 记录首个 token 在正文中的命中位置，用于生成摘要

      for (var t = 0; t < tokens.length; t++) {
        var tok = tokens[t];
        var titleIdx = d.titleLower.indexOf(tok);
        var textIdx = d.textLower.indexOf(tok);

        if (titleIdx === -1 && textIdx === -1) {
          allHit = false;
          break;
        }

        // 标题命中：高权重
        if (titleIdx !== -1) {
          titleHitCount++;
          score += 1000;
          // 标题越短越精确（归一化到 40 字符），短标题加分
          score += (1.0 - Math.min(d.titleLower.length / 40.0, 1.0)) * 300;
          // 标题开头（前缀）命中加分
          if (titleIdx === 0) score += 100;
          // 查询落在标题的 token 边界（完整词/段命中）加分
          if (isTokenBoundary(d.titleLower, titleIdx, tok.length)) score += 120;
          // API 标识符标题（如 wifi_sta_connect）：查询正好是其某个下划线段 -> 强加分
          if (isIdentifier(d.titleLower)) {
            var segs = d.titleLower.split("_");
            if (segs.indexOf(tok) !== -1) score += 250;
          }
        } else {
          // 仅正文命中：低权重，按出现次数适度累计（封顶 3 次，避免长文档灌水）
          // 单字符查询（如 "f"）几乎命中每篇文档，传入上限提前退出，避免全文扫描。
          var cnt = countOccurrences(d.textLower, tok, 3);
          score += cnt * 1;
        }

        // 记录首个正文命中位置（用于生成正文摘要）
        if (textIdx !== -1 && firstTextHitIdx === -1) {
          firstTextHitIdx = textIdx;
        }
      }

      if (allHit) {
        // 多 token 查询，且全部 token 都命中标题 -> 额外大奖（精确匹配）
        if (titleHitCount === tokens.length && tokens.length > 1) score += 200;

        // 连续短语匹配（如 "int sock" 作为连续子串出现）-> 最高优先级
        // 这是用户最期望的结果：包含完整搜索短语的文章应排在最前
        if (tokens.length > 1) {
          var phrase = tokens.join(" ");
          // 标题中包含完整短语：顶级优先
          if (d.titleLower.indexOf(phrase) !== -1) {
            score += 5000;
          }
          // 正文中包含完整短语：次级优先
          if (d.textLower.indexOf(phrase) !== -1) {
            score += 2000;
            // 摘要优先用短语命中位置
            var phraseIdx = d.textLower.indexOf(phrase);
            if (firstTextHitIdx === -1 || phraseIdx < firstTextHitIdx) {
              firstTextHitIdx = phraseIdx;
            }
          }
        }

        // 无正文的纯标题小节（如 "Structures" 分类目录条目）降权：
        // 它们没有可预览的内容，排在有正文的结果之后更符合用户预期
        if (!d.text) score -= 600;

        results.push({
          loc: d.loc,
          title: d.title,
          score: score,
          text: d.text,
          textHitIdx: firstTextHitIdx,
          docIndex: i, // 在扁平索引中的下标，供空正文条目取紧邻的下一条目做预览
        });
      }
    }

    // 按得分降序；得分相同按标题字典序（稳定排序，避免插入序干扰）
    results.sort(function (a, b) {
      if (b.score !== a.score) return b.score - a.score;
      return a.title < b.title ? -1 : a.title > b.title ? 1 : 0;
    });
    // 返回全部匹配结果，由渲染层分页加载（无限滚动），避免截断丢条目
    return results;
  }

  // 判断子串命中位置是否落在 token 边界（前后为非单词字符/下划线），用于区分"完整词命中"
  function isTokenBoundary(str, idx, len) {
    var before = idx === 0 || /[\W_]/.test(str.charAt(idx - 1));
    var after = idx + len === str.length || /[\W_]/.test(str.charAt(idx + len));
    return before && after;
  }

  // 判断是否为代码标识符类标题（小写字母/数字/下划线，且含下划线）
  function isIdentifier(s) {
    return /^[a-z][a-z0-9_]*$/.test(s) && s.indexOf("_") !== -1;
  }

  // 统计子串出现次数（可选 max：达到上限即提前返回，避免全文扫描）
  function countOccurrences(str, sub, max) {
    if (!sub) return 0;
    var count = 0, idx = 0;
    while ((idx = str.indexOf(sub, idx)) !== -1) {
      count++;
      idx += sub.length;
      if (max && count >= max) return count;
    }
    return count;
  }

  // ---- 结果渲染（分页 / 无限滚动）----
  // 首次渲染：清空容器，创建列表，渲染首页 pageSize 条，并绑定滚动加载
  function renderResults(results, query, container) {
    if (!container) return;
    // 卸载旧的滚动监听（查询变化时）
    detachScrollListener();
    cleanupTooltips(); // 切换到结果区前

    container.innerHTML = "";
    // 注意：历史词条不在渲染时记录——用户每输入一个字符都会触发渲染，历史仅在回车提交
    if (!results.length) {
      SubstringSearch._allResults = [];
      SubstringSearch._renderedCount = 0;
      container.innerHTML =
        '<div class="md-search-result__meta">未找到包含「' +
        escapeHtml(query) + '」的结果</div>';
      return;
    }

    SubstringSearch._allResults = results;
    SubstringSearch._renderedCount = 0;

    var ol = document.createElement("ol");
    ol.className = "md-search-result__list substring-search__list";
    container.appendChild(ol);

    // 渲染首页
    appendPage(container, query);

    // 绑定滚动监听（触底加载更多）
    attachScrollListener(container, ol, query);
  }

  // 追加渲染一页（pageSize 条）到已有列表
  // 注意：每次都从 DOM 重新获取 ol，避免 navigation.instant 替换 DOM 后
  // 闭包里的 ol 引用变成脱离文档的旧元素导致 appendChild 静默失效。
  function appendPage(container, query) {
    var all = SubstringSearch._allResults;
    var start = SubstringSearch._renderedCount;
    var end = Math.min(start + CONFIG.pageSize, all.length);
    // 从 DOM 重新获取当前的 ol（可能已被 SPA 替换）
    var ol = container.querySelector(".substring-search__list");
    if (!ol) return;

    for (var i = start; i < end; i++) {
      var r = all[i];
      var li = document.createElement("li");
      li.className = "md-search-result__item";

      var a = document.createElement("a");
      a.href = resolveUrl(r.loc);
      a.className = "md-search-result__link substring-search__link";
      a.tabIndex = 0;

      // 附加 ?h= 参数：material 的高亮功能读取此参数，在目标页面高亮命中词。
      // 注意：loc 可能含 #anchor，需把查询参数插到 hash 之前。
      var hUrl = resolveUrl(r.loc);
      var encodedQ = encodeURIComponent(query.trim());
      if (hUrl.indexOf("#") !== -1) {
        hUrl = hUrl.replace("#", (hUrl.indexOf("?") !== -1 ? "&" : "?") + "h=" + encodedQ + "#");
      } else {
        hUrl += (hUrl.indexOf("?") !== -1 ? "&" : "?") + "h=" + encodedQ;
      }
      a.href = hUrl;

      var titleEl = document.createElement("div");
      titleEl.className = "md-search-result__title";
      titleEl.innerHTML = highlight(r.title, query);

      // 正文摘要：截取命中词附近的文本片段（类似 material 原生搜索的正文预览）。
      // 无正文的纯标题条目（如 "Structures" 分类目录）：索引按页面小节顺序排列，
      // 紧邻下一条目即该条目在页面上的下一行内容——直接显示其标题（如第一个
      // 子小节名 i2s_config_t），与页面上用户实际看到的临近行一致。
      // 不拼接下一条目正文：其开头是别名 hook 注入的分词文本，拼接会造成重复。
      // 下一条目无标题或跨页/不存在时回退显示路径。
      var previewEl = document.createElement("div");
      previewEl.className = "md-search-result__summary substring-search__summary";
      var snippet = "";
      if (r.text) {
        snippet = makeSnippet(r.text, r.textHitIdx, query);
      } else {
        var nextDoc = SubstringSearch._docs ? SubstringSearch._docs[r.docIndex + 1] : null;
        var samePage = nextDoc &&
          nextDoc.loc.split("#")[0] === r.loc.split("#")[0];
        if (samePage) {
          snippet = (nextDoc.title || nextDoc.text.slice(0, 60)).trim().slice(0, 80);
        }
        snippet = snippet ? highlight(snippet, query) : escapeHtml(prettyPath(r.loc));
      }
      previewEl.innerHTML = snippet;

      a.appendChild(titleEl);
      a.appendChild(previewEl);
      li.appendChild(a);
      ol.appendChild(li);
    }
    SubstringSearch._renderedCount = end;

    // 更新/移除加载状态提示
    updateLoadHint(container, query);

    // 把第一条结果同步注入 material 原生结果容器（.md-search-result），
    // 让 material 的 Enter 回车逻辑自然选中子串检索的第一条结果（而非原生分词结果）。
    // 不拦截 material 的任何行为，保留 ?h= 存入、高亮、closeSearch 等全部副作用。
    syncFirstResultToNative(query);
  }

  // 将子串检索的第一条结果注入 material 原生搜索结果容器。
  // material 回车时从 .md-search-result 里按 data-md-score 选最高分跳转。
  // 注入一条 score 极高的结果，使 material 的 Enter 选中它。
  function syncFirstResultToNative(query) {
    var all = SubstringSearch._allResults;
    if (!all.length) return;
    var native = document.querySelector(".md-search-result");
    if (!native) return;

    var first = all[0];
    var href = resolveUrl(first.loc);
    // 正确拼接 ?h= 参数：必须插在 #hash 之前（查询参数不能在 hash 后面）
    if (href.indexOf("h=") === -1) {
      var hashIdx = href.indexOf("#");
      var hashPart = hashIdx !== -1 ? href.slice(hashIdx) : "";
      var basePart = hashIdx !== -1 ? href.slice(0, hashIdx) : href;
      href = basePart + (basePart.indexOf("?") !== -1 ? "&" : "?") + "h=" + encodeURIComponent(query.trim()) + hashPart;
    }

    // 清空原生结果容器，注入一条最高分结果
    native.innerHTML = "";
    native.style.display = "none"; // 保持隐藏（用户看到的是子串结果）
    var ol = document.createElement("ol");
    ol.className = "md-search-result__list";
    ol.setAttribute("role", "presentation");
    var li = document.createElement("li");
    li.className = "md-search-result__item";
    var a = document.createElement("a");
    a.href = href;
    a.className = "md-search-result__link";
    a.setAttribute("data-md-score", "999999"); // 极高分，确保 material Enter 选中
    a.innerHTML = '<div class="md-search-result__title">' + escapeHtml(first.title) + '</div>';
    li.appendChild(a);
    ol.appendChild(li);
    native.appendChild(ol);
  }

  // 底部提示：「加载更多」哨兵（可点击） 或 「已全部加载」计数
  function updateLoadHint(container, query) {
    var existing = container.querySelector(".substring-search__hint");
    var total = SubstringSearch._allResults.length;
    var rendered = SubstringSearch._renderedCount;

    // 确保 hint 元素存在（无论是否还有更多，都要给用户一个总数反馈）
    if (!existing) {
      existing = document.createElement("div");
      existing.className = "md-search-result__meta substring-search__hint";
      existing.setAttribute("role", "button");
      existing.setAttribute("tabindex", "0");
      existing.addEventListener("click", function () {
        if (SubstringSearch._renderedCount < SubstringSearch._allResults.length) {
          appendPage(container, query);
        }
      });
      existing.addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          existing.click();
        }
      });
      container.appendChild(existing);
    }

    if (rendered < total) {
      // 还有更多：显示可点击的加载提示
      existing.textContent = "已显示 " + rendered + " / " + total + " 条 · 点击或向下滚动加载更多";
      existing.setAttribute("data-more", "1");
      existing.setAttribute("role", "button");
    } else {
      // 已全部加载：显示总数（移除可交互属性）
      existing.removeAttribute("data-more");
      existing.removeAttribute("role");
      existing.textContent = "共 " + total + " 条结果，已全部加载";
    }
  }

  // 绑定滚动监听：结果列表触底时追加下一页
  function attachScrollListener(container, ol, query) {
    detachScrollListener();
    // material 的滚动容器是 .md-search__scrollwrap（结果区的可滚动区）
    var scroller = document.querySelector(".md-search__scrollwrap");
    if (!scroller) return;
    var loading = false; // 防重入锁：避免一次触底连续触发多次 appendPage
    var handler = function () {
      if (loading) return;
      // 触底判定：距底部 < 80px 时加载下一页
      var remain = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
      if (remain < 80 && SubstringSearch._renderedCount < SubstringSearch._allResults.length) {
        loading = true;
        appendPage(container, query);
        // 下一轮滚动事件再允许加载
        setTimeout(function () { loading = false; }, 200);
      }
    };
    scroller.addEventListener("scroll", handler, { passive: true });
    SubstringSearch._scrollListener = { el: scroller, fn: handler };
  }

  function detachScrollListener() {
    if (SubstringSearch._scrollListener) {
      try {
        SubstringSearch._scrollListener.el.removeEventListener(
          "scroll", SubstringSearch._scrollListener.fn
        );
      } catch (e) {}
      SubstringSearch._scrollListener = null;
    }
  }

  function highlight(text, query) {
    // 对查询的每个 token 在标题中高亮
    var tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    var lower = text.toLowerCase();
    // 用占位避免重叠替换：先标记位置，再统一替换
    var marks = [];
    for (var i = 0; i < tokens.length; i++) {
      var tok = tokens[i];
      var idx = 0;
      while ((idx = lower.indexOf(tok, idx)) !== -1) {
        marks.push([idx, idx + tok.length]);
        idx += tok.length;
      }
    }
    if (!marks.length) return escapeHtml(text);
    marks.sort(function (a, b) { return a[0] - b[0]; });
    // 合并重叠区间
    var merged = [marks[0]];
    for (var j = 1; j < marks.length; j++) {
      var last = merged[merged.length - 1];
      if (marks[j][0] <= last[1]) {
        last[1] = Math.max(last[1], marks[j][1]);
      } else {
        merged.push(marks[j]);
      }
    }

    // 对每段分别 escapeHtml 后再拼接，标记边界与文本边界严格对齐。
    var out = "";
    var cursor = 0;
    for (var k = 0; k < merged.length; k++) {
      var s = merged[k][0], e = merged[k][1];
      out += escapeHtml(text.slice(cursor, s));
      out += "<mark>" + escapeHtml(text.slice(s, e)) + "</mark>";
      cursor = e;
    }
    out += escapeHtml(text.slice(cursor));
    return out;
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function resolveUrl(loc) {
    // use_directory_urls: false，loc 形如 "xxx.html#anchor" 或 "path/to.html"
    // loc 相对于站点根。用缓存的绝对站点根 URL 拼接，避免 SPA 导航后 __config.base
    // 过期导致"多拼一级"和 404（详见 getSiteRoot 注释）。
    // 返回 pathname 形式（去掉 origin），与原行为一致，便于 a.href 使用。
    var root = getSiteRoot();
    var origin = window.location.origin;
    var rootPath = root.indexOf(origin) === 0 ? root.slice(origin.length) : root;
    return rootPath + loc;
  }

  function prettyPath(loc) {
    // 去掉 .html 后缀与锚点，展示可读路径
    return loc.replace(/\.html.*$/i, "").replace(/#/g, " › ");
  }

  // 生成正文摘要：以命中位置为中心，截取前后若干字符，高亮命中词。
  // text: 正文全文；hitIdx: 首个命中位置（-1 表示仅标题命中）；query: 查询词
  function makeSnippet(text, hitIdx, query) {
    var SNIPPET_LEN = 80; // 摘要总长度（字符）
    var text_ = text || "";
    if (!text_) return "";

    var center;
    if (hitIdx >= 0) {
      center = hitIdx;
    } else {
      // 仅标题命中，取正文开头
      center = 0;
    }
    var start = Math.max(0, center - Math.floor(SNIPPET_LEN / 3));
    var end = Math.min(text_.length, start + SNIPPET_LEN);
    var snippet = text_.slice(start, end).trim();
    if (start > 0) snippet = "…" + snippet;
    if (end < text_.length) snippet = snippet + "…";
    // 转义后再高亮命中词
    return highlight(snippet, query);
  }

  // tooltip：鼠标悬停/键盘聚焦时显示完整词条。
  function cleanupTooltips() {
    var tips = document.querySelectorAll(".substring-search__history-chip-tooltip");
    for (var i = 0; i < tips.length; i++) {
      if (tips[i].parentNode) tips[i].parentNode.removeChild(tips[i]);
    }
  }
  function attachChipTooltip(chip, text) {
    var tip = null;
    function show() {
      if (tip) return;
      cleanupTooltips(); // 显示新 tooltip 前，先清掉其他 chip 残留的 tooltip
      tip = document.createElement("div");
      tip.className = "substring-search__history-chip-tooltip";
      tip.textContent = text;
      tip.setAttribute("role", "tooltip");
      document.body.appendChild(tip);
      position();
    }
    function position() {
      if (!tip) return;
      var rect = chip.getBoundingClientRect();
      var top = rect.bottom + 6;
      var left = rect.left + rect.width / 2;
      tip.style.top = top + "px";
      tip.style.left = left + "px";
      var tw = tip.offsetWidth;
      var th = tip.offsetHeight;
      tip.style.left = (left - tw / 2) + "px";
      if (top + th > window.innerHeight && rect.top - th - 6 > 0) {
        tip.style.top = (rect.top - th - 6) + "px";
      }
    }
    function hide() {
      if (!tip) return;
      tip.parentNode.removeChild(tip);
      tip = null;
    }
    chip.addEventListener("mouseenter", show);
    chip.addEventListener("mouseleave", hide);
    chip.addEventListener("focusin", show);
    chip.addEventListener("focusout", hide);
    chip.addEventListener("click", hide);
    window.addEventListener("scroll", position, true);
  }

  // ---- 历史搜索区渲染 ----
  function renderHistory(container) {
    if (!container) return;
    detachScrollListener();
    cleanupTooltips(); // 重建历史区前，清理可能残留的 tooltip
    SubstringSearch._allResults = [];
    SubstringSearch._renderedCount = 0;
    container.innerHTML = "";
    container.hidden = false; // 历史区与子串结果共用此容器，需显示

    var history = HistoryStore.get();
    if (!history.length) {
      // 无历史：保持容器可见但空（避免原生结果闪现），给一句占位提示
      container.innerHTML =
        '<div class="substring-search__history-empty">输入关键词搜索文档</div>';
      return;
    }

    var wrap = document.createElement("div");
    wrap.className = "substring-search__history";

    var head = document.createElement("div");
    head.className = "substring-search__history-head";
    head.innerHTML = '<span class="substring-search__history-title">搜索历史</span>';
    var clearBtn = document.createElement("button");
    clearBtn.type = "button";
    clearBtn.className = "substring-search__history-clear";
    clearBtn.textContent = "清空";
    clearBtn.addEventListener("click", function () {
      HistoryStore.clear();
      renderHistory(container);
    });
    head.appendChild(clearBtn);
    wrap.appendChild(head);

    var list = document.createElement("div");
    list.className = "substring-search__history-list";
    for (var i = 0; i < history.length; i++) {
      (function (term) {
        var chip = document.createElement("button");
        chip.type = "button";
        chip.className = "substring-search__history-chip";
        var label = document.createElement("span");
        label.className = "substring-search__history-chip-label";
        label.textContent = term;
        chip.appendChild(label);
        // 挂载自定义 tooltip。不用原生 title：延迟大、宽度受限、且被裁剪显示不全。
        attachChipTooltip(chip, term);
        var del = document.createElement("span");
        del.className = "substring-search__history-chip-del";
        del.setAttribute("aria-hidden", "true");
        del.innerHTML = "&times;";
        chip.appendChild(del);

        // 点击 chip 主体 → 填入输入框并触发搜索
        chip.addEventListener("click", function (ev) {
          if (ev.target.closest(".substring-search__history-chip-del")) {
            // 点 ✕ 删除单条
            ev.stopPropagation();
            HistoryStore.remove(term);
            renderHistory(container);
            return;
          }
          var input = document.querySelector(".md-search__input");
          if (input) {
            input.value = term;
            input.focus();
            // 派发 input 事件，走既有防抖查询链路
            input.dispatchEvent(new Event("input", { bubbles: true }));
          }
        });
        list.appendChild(chip);
      })(history[i]);
    }
    wrap.appendChild(list);

    container.appendChild(wrap);
  }
  // 暴露供 custom.js 在打开搜索弹窗时调用
  SubstringSearch.showHistory = function () {
    var sub = document.querySelector(".substring-search-result");
    var native = document.querySelector(".md-search-result");
    if (native) native.style.display = "none"; // 原生结果也隐藏，避免「初始化中」文案
    renderHistory(sub);
  };

  // ---- 与原生搜索的显示切换 ----
  function showSubstringMode(active) {
    var sub = document.querySelector(".substring-search-result");
    var native = document.querySelector(".md-search-result");
    if (!sub) return;
    if (active) {
      sub.hidden = false;
      if (native) native.style.display = "none";
      startEmptyWatch();
    } else {
      // 退出子串模式：清理分页状态与滚动监听
      detachScrollListener();
      SubstringSearch._allResults = [];
      SubstringSearch._renderedCount = 0;
      var input = document.querySelector(".md-search__input");
      var val = input ? input.value : "";
      if (!val || val.trim().length < CONFIG.minQueryLength) {
        // 输入为空/过短：展示历史搜索区（而非回退到原生「初始化中」）
        if (native) native.style.display = "none";
        renderHistory(sub);
      } else {
        // 有内容但不达阈值（理论上不会到这，minQueryLength=1）：回退原生
        sub.hidden = true;
        sub.innerHTML = "";
        if (native) native.style.display = "";
      }
      stopEmptyWatch();
    }
  }

  // ---- 空值轮询守卫 ----
  // 部分「清空输入」操作（如 Playwright fill("")、拖拽删除、IME 合成结束）可能不触发
  // input 事件，导致子串模式进入后无法回退。进入子串模式后启动轻量轮询，一旦输入框
  // 变空或低于最小长度，立即回退到原生搜索。
  var _emptyWatchTimer = null;
  function startEmptyWatch() {
    stopEmptyWatch();
    _emptyWatchTimer = setInterval(function () {
      var input = document.querySelector(".md-search__input");
      if (!input) { stopEmptyWatch(); return; }
      var val = input.value;
      if (!val || val.trim().length < CONFIG.minQueryLength) {
        SubstringSearch._lastQuery = val;
        showSubstringMode(false);
      }
    }, 300);
  }
  function stopEmptyWatch() {
    if (_emptyWatchTimer) {
      clearInterval(_emptyWatchTimer);
      _emptyWatchTimer = null;
    }
  }

  // ---- 输入事件绑定 ----
  function onInput(query) {
    SubstringSearch._lastQuery = query;
    if (!query || query.trim().length < CONFIG.minQueryLength) {
      showSubstringMode(false);
      return;
    }
    loadIndex().then(function (docs) {
      if (!docs) {
        showSubstringMode(false);
        return;
      }
      // 异步期间若查询已变，丢弃过期结果
      if (query !== SubstringSearch._lastQuery) return;
      var results = search(query);
      var container = document.querySelector(".substring-search-result");
      if (query !== SubstringSearch._lastQuery) return;
      renderResults(results, query, container);
      showSubstringMode(true);
    });
  }

  function debounce(fn, ms) {
    return function () {
      var ctx = this, args = arguments;
      clearTimeout(SubstringSearch._debounceTimer);
      SubstringSearch._debounceTimer = setTimeout(function () {
        fn.apply(ctx, args);
      }, ms);
    };
  }

  // ---- 初始化（幂等，支持 SPA 重复调用）----
  // 用 input 元素上的 dataset 标记是否已绑定，避免对同一元素重复绑定；
  // 当 navigation.instant 替换 DOM 后，新 input 元素无标记，会重新绑定。
  SubstringSearch.init = function () {
    var input = document.querySelector(".md-search__input");
    if (!input) {
      // 模板未就绪，稍后重试
      setTimeout(SubstringSearch.init, 300);
      return;
    }
    if (input.getAttribute("data-ss-bound") === "1") {
      SubstringSearch._inited = true;
      return; // 已绑定过
    }
    input.setAttribute("data-ss-bound", "1");
    SubstringSearch._inited = true;

    // 智能分发：空/短查询立即处理（确保回退及时），长查询走 debounce
    var handler = function () {
      var val = input.value;
      if (!val || val.trim().length < CONFIG.minQueryLength) {
        // 短/空查询：立即回退，不走防抖
        onInput(val);
      } else {
        debounce(function () { onInput(input.value); }, CONFIG.debounceMs)();
      }
    };
    input.addEventListener("input", handler);
    // keyup 保险：部分清空场景（如按住删除）input 事件可能遗漏
    input.addEventListener("keyup", handler);
    // 回车提交：记录一次最终查询词到历史（不在每次输入时记录，避免逐字符记录）
    // 直接监听 keydown Enter，不依赖 form submit（Material 可能拦截 submit 事件）
    input.addEventListener("keydown", function (ev) {
      if (ev.key === "Enter") {
        var v = input.value.trim();
        if (v) HistoryStore.add(v);
      }
    });

    // material reset 按钮（清空）也触发回退
    var resetBtn = document.querySelector('.md-search__options button[type="reset"]');
    if (resetBtn) {
      resetBtn.addEventListener("click", function () {
        setTimeout(function () { onInput(""); }, 0);
      });
    }

    // 兼容 URL ?h= 预填：custom.js 已处理 input.value 赋值，这里补一次触发
    setTimeout(function () {
      if (input.value && input.value.trim().length >= CONFIG.minQueryLength) {
        onInput(input.value);
      }
    }, 100);
  };

  // 供回车跳转调用：确保索引加载后，返回第一条结果的链接（含 ?h= 参数）
  // 返回 Promise<string|null>，string 为 href，null 表示无结果
  SubstringSearch.getFirstResultHref = function (query) {
    if (!query || query.trim().length < CONFIG.minQueryLength) {
      return Promise.resolve(null);
    }
    return loadIndex().then(function (docs) {
      if (!docs) return null;
      var results = search(query);
      if (!results.length) return null;
      var href = resolveUrl(results[0].loc);
      if (href.indexOf("h=") === -1) {
        href += (href.indexOf("?") !== -1 ? "&" : "?") + "h=" + encodeURIComponent(query.trim());
      }
      return href;
    });
  };

  // 暴露并自动初始化
  window.SubstringSearch = SubstringSearch;
  function boot() {
    // 首次加载
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", SubstringSearch.init);
    } else {
      SubstringSearch.init();
    }
    // navigation.instant（SPA）下页面内容会被替换，DOM 里的 .md-search__input
    // 变成新元素，需要重新绑定。material 的 document$ 是 RxJS Subject，
    // 导航完成后会 emit 事件；用 subscribe 监听（addEventListener 在此对象上不存在）。
    try {
      if (window.document$ && typeof window.document$.subscribe === "function") {
        window.document$.subscribe(function () {
          // SPA 导航完成后稍延迟，等新 DOM 渲染好再重新绑定
          setTimeout(SubstringSearch.init, 100);
        });
      }
    } catch (e) {
      // document$ 不可用不影响首次初始化，忽略
    }
    // 兜底：检测搜索弹窗被重新插入 DOM 时重新初始化
    if (typeof MutationObserver !== "undefined" && !SubstringSearch._observer) {
      SubstringSearch._observer = new MutationObserver(function () {
        var input = document.querySelector(".md-search__input");
        if (input && input.getAttribute("data-ss-bound") !== "1") {
          SubstringSearch.init();
        }
      });
      SubstringSearch._observer.observe(document.body, { childList: true, subtree: true });
    }
  }
  boot();
})(window, document);
