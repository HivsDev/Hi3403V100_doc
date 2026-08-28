import html as _html
import importlib.util
import logging
import os
import posixpath
import re
import unicodedata
from urllib.parse import unquote

log = logging.getLogger('mkdocs.plugins.slugify_links')

# 复用 _slugify.py 的 slug 规则，保证「fragment 规整」与历史行为一致。
# MkDocs 用 importlib 单独加载每个 hook 文件，hooks 目录不在 sys.path 中，
# 因此不能用 `from hooks._slugify import _slug`，需按文件路径动态加载同目录模块。
_slugify_util = importlib.util.spec_from_file_location(
    '_slugify_util',
    os.path.join(os.path.dirname(__file__), '_slugify.py'),
)
_slugify_mod = importlib.util.module_from_spec(_slugify_util)
_slugify_util.loader.exec_module(_slugify_mod)
_slug = _slugify_mod._slug


# ---------------------------------------------------------------------------
# 锚点 id 计算：优先直接取配置中实际生效的 toc slugify 函数（_get_slugify），
# 保证与真实渲染永远同源、规则再变也不漂移；配置取不到时退回 _real_slug 复刻。
# 另复刻 python-markdown toc 扩展的去重逻辑（重复标题追加 _1/_2 后缀）。
# 两者合并后可在 markdown 阶段预算出每页构建后的全部锚点 id，用于把正文里
# 手写的不精确 fragment（GitHub 风格小写、缺标点、重复标题误写 -1 后缀等）
# 自动改写为真实存在的 id；匹配不到的输出构建警告，暴露真死链。
# ---------------------------------------------------------------------------

_RE_TAGS = re.compile(r'</?[^>]*>', re.UNICODE)
_RE_INVALID_SLUG_CHAR = re.compile(r'[^\w\- ]', re.UNICODE)
_RE_SPACE = re.compile(r' ', re.UNICODE)
_IDCOUNT_RE = re.compile(r'^(.*)_([0-9]+)$')

_ATX_RE = re.compile(r'^(#{1,6})\s+(.*?)\s*#*\s*$')
_ATTR_ID_RE = re.compile(r'\{#([^}\s]+)\}')
_ATTR_ID_TRAILING_RE = re.compile(r'\{#[^}]*\}\s*$')
_HTML_ID_RE = re.compile(r'\b(?:id|name)\s*=\s*["\']([^"\']+)["\']')
_MD_IMG_RE = re.compile(r'!\[[^\]]*\]\([^)]*\)')
_MD_LINK_RE = re.compile(r'(?<!!)\[([^\]]*)\]\([^)]*\)')
_FOOTNOTE_RE = re.compile(r'\[\^[^\]]+\]')
_EXTERNAL_RE = re.compile(r'^[a-zA-Z][a-zA-Z0-9+.\-]*:', re.IGNORECASE)
# 模糊键剥离规则：非单词字符 + 下划线都要去掉，否则 _1 后缀与 -1 写法对不上
_RE_TOLERANT_STRIP = re.compile(r'[\W_]', re.UNICODE)

# 跨页链接目标文件的 id 集合缓存：(src_uri, mtime_ns, slugify_id) -> ids，mtime 变化自动失效
_FILE_ID_CACHE = {}


def _real_slug(text, separator='-'):
    """pymdownx.slugs.slugify 默认参数的复刻，仅在配置里取不到 slugify 时兜底：
    NFC → 去 HTML 标签 → strip → 删 [^\w\- ] → 每个空格单独替换为分隔符。"""
    slug = _RE_TAGS.sub('', unicodedata.normalize('NFC', text)).strip()
    slug = _RE_INVALID_SLUG_CHAR.sub('', slug)
    return _RE_SPACE.sub(separator, slug)


def _get_slugify(config):
    """优先取配置中实际生效的 toc slugify（mkdocs_base.yml 或 _slugify.py on_config
    写入的），保证 id 计算与真实渲染永远同源；取不到才退回复刻规则。"""
    try:
        slugify = config.get('mdx_configs', {}).get('toc', {}).get('slugify')
        if callable(slugify):
            return slugify
    except Exception:
        pass
    return _real_slug


def _unique(idvalue, used):
    """与 markdown.extensions.toc.unique 一致：与已有 id 冲突时追加 _1、_2 …后缀。"""
    while idvalue in used or not idvalue:
        m = _IDCOUNT_RE.match(idvalue)
        if m:
            idvalue = '%s_%d' % (m.group(1), int(m.group(2)) + 1)
        else:
            idvalue = '%s_%d' % (idvalue, 1)
    used.add(idvalue)
    return idvalue


def _heading_name(text):
    """原始 ATX 标题文本 → 近似渲染后的纯文本：去图片/链接语法、行内 HTML 标签、
    加粗记号、脚注引用，还原 HTML 实体，连续空白折叠为单个空格
    （与渲染后 HTML 的空白语义一致，如 `X <a id=..></a> [Y]` 不会产生双连字符）。"""
    text = _MD_IMG_RE.sub('', text)
    text = _MD_LINK_RE.sub(r'\1', text)
    text = _RE_TAGS.sub('', text)
    text = text.replace('**', '').replace('__', '')
    text = _FOOTNOTE_RE.sub('', text)
    text = _html.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()


def _page_ids(markdown, slugify=None):
    """计算一页 markdown 构建后的全部可跳转锚点 id：
    标题 id（含 _1/_2 去重后缀）+ 显式 <a id>/<a name>/{#id} 锚点。"""
    slugify = slugify or _real_slug
    ids = set()
    used = set()
    in_code = False
    for line in markdown.split('\n'):
        stripped = line.lstrip()
        if stripped.startswith('```') or stripped.startswith('~~~'):
            in_code = not in_code
            continue
        if in_code:
            continue
        # 显式 id 先全部收集：toc 会用它们预置去重集合，且它们本身就是链接目标
        for m in _HTML_ID_RE.finditer(line):
            ids.add(m.group(1))
            used.add(m.group(1))
        for m in _ATTR_ID_RE.finditer(line):
            ids.add(m.group(1))
            used.add(m.group(1))
        hm = _ATX_RE.match(line)
        if not hm:
            continue
        text = _ATTR_ID_TRAILING_RE.sub('', hm.group(2))
        ids.add(_unique(slugify(_heading_name(text), '-'), used))
    return ids


def _tolerant(value):
    """模糊匹配键：删除所有非单词字符与下划线（空格、-、_、标点）并 casefold。
    用于兜底匹配「同一标题、不同写法」的 fragment（大小写/标点/`-1` vs `_1` 后缀差异）。"""
    return _RE_TOLERANT_STRIP.sub('', value).casefold()


def _ids_for_target(path_part, page, files, slugify):
    """解析跨页链接 path.md 的锚点 id 集合；目标文件不存在返回 None。"""
    src_uri = getattr(page.file, 'src_uri', None) or page.file.src_path
    base = posixpath.dirname(src_uri)
    target = posixpath.normpath(posixpath.join(base, path_part))
    f = files.get_file_from_path(target)
    if f is None and target.lower().endswith('.html'):
        f = files.get_file_from_path(target[:-5] + '.md')
    if f is None:
        return None
    abs_path = getattr(f, 'abs_src_path', None)
    if not abs_path or not os.path.isfile(abs_path):
        return None
    mtime = os.stat(abs_path).st_mtime_ns
    cache_key = (f.src_uri, mtime, id(slugify))
    cached = _FILE_ID_CACHE.get(f.src_uri)
    if cached is not None and cached[0] == cache_key:
        return cached[1]
    with open(abs_path, encoding='utf-8', errors='replace') as fh:
        id_set = _page_ids(fh.read(), slugify)
    _FILE_ID_CACHE[f.src_uri] = (cache_key, id_set)
    return id_set


def _resolve_target(target, page_ids, page, files, slugify):
    """规整并解析链接目标：精确命中不动；未命中按模糊键找唯一真实 id 改写。

    兼容 markdown 链接与原始 HTML <a href> 两种形态的目标值；fragment 若是
    percent-encode 写法（如 %28args...%29）会同时按解码后再匹配。"""
    target = target.strip()
    if '#' not in target or _EXTERNAL_RE.match(target):
        return target
    path_part, frag = target.split('#', 1)
    # 去掉可选的 <title> 附注（path#frag "title" 形式，极少见）
    frag = re.split(r'\s+["<]', frag)[0].strip()
    if not frag:
        return target
    if path_part:
        ids = _ids_for_target(path_part, page, files, slugify)
        prefix = path_part + '#'
        where = path_part
    else:
        ids = page_ids
        prefix = '#'
        where = '本页'
    if ids is None:
        return target  # 目标文件缺失：维持原有规整行为，不猜测
    # 变体：原文与 percent-decode 后各算一份（源码里 %28 等编码写法很常见）
    variants = []
    for v in (frag, unquote(frag)):
        if v and v not in variants:
            variants.append(v)
    candidates = set()
    matched = None
    for v in variants:
        frag1 = _slug(v)
        if not frag1:
            continue
        if matched is None and (frag1 in ids or v in ids):
            matched = frag1
        candidates |= {i for i in ids if _tolerant(i) == _tolerant(frag1)}
    if matched is not None:
        return prefix + matched
    if len(candidates) == 1:
        return prefix + candidates.pop()
    # 歧义兜底：候选中恰有一个与 fragment 仅大小写不同（casefold 相等）时选它，
    # 典型场景：#uisphereview 同时命中标题 UISphereView 与页首 H1 的 ui_sphere_view
    frag_cf = {_slug(v).casefold() for v in variants}
    case_insensitive = {c for c in candidates if c.casefold() in frag_cf}
    if len(case_insensitive) == 1:
        return prefix + case_insensitive.pop()
    src = getattr(page.file, 'src_uri', None) or page.file.src_path
    if not candidates:
        log.warning('锚点可能失效：%s 中的链接 #%s 在%s找不到对应标题/锚点',
                    src, frag, where)
    else:
        log.warning('锚点歧义：%s 中的链接 #%s 在%s匹配到多个目标 %s，保持原样',
                    src, frag, where, sorted(candidates))
    return prefix + _slug(variants[0])


# 匹配 markdown 链接 [text](target)，排除图片 ![...]
# target 形如：#frag / path.md#frag / path.md / http://...
# 文本与目标均允许反斜杠转义字符（IDP 表格链接常见 value\[\] 写法，
# 否则 [^\]]* 会在文本中间的 \] 处提前截断导致整条链接漏处理）
_LINK_RE = re.compile(r'(?<!!)\[((?:\\.|[^\]\\])*)\]\(((?:\\.|[^)\\])+)\)')

# 匹配原始 HTML 锚点链接 <a href="target">（IDP 导出文档大量使用，同样需要修复）
# 只捕获 href 值本身，替换时保留引号风格；<a id/name> 锚点无 href，不会命中
_HTML_HREF_RE = re.compile(r'(<a\s[^>]*?href\s*=\s*)(["\'])([^"\']+)(\2)')


def on_page_markdown(markdown, page, config, files):
    """自动规整并解析正文里 [text](#xxx) / [text](file.md#xxx) 的锚点 fragment。

    解析顺序：
    1. 按空格转「-」规整（历史行为，保持不变）；
    2. 与目标页构建后的真实标题/锚点 id 精确比对，命中即用；
    3. 未命中则按「忽略大小写+去标点/分隔符」模糊键匹配，唯一命中才改写为
       真实 id（如 #列出可用命令clac -> #列出可用命令CLAC、#xxx-1 -> #xxx_1）；
    4. 无命中或多义：保持规整结果并输出构建警告，便于暴露真死链。

    同时处理 markdown 链接 [t](#x) 与原始 HTML 链接 <a href="#x">；
    链接文本、文件路径、外部链接均不受影响。
    """
    slugify = _get_slugify(config)
    page_ids = _page_ids(markdown, slugify)

    def _md_sub(m):
        text, target = m.group(1), m.group(2)
        return '[%s](%s)' % (text, _resolve_target(target, page_ids, page, files, slugify))

    def _html_sub(m):
        head, quote, target, quote2 = m.groups()
        return '%s%s%s%s' % (head, quote, _resolve_target(target, page_ids, page, files, slugify), quote2)

    lines = markdown.split('\n')
    in_code = False
    out = []
    for line in lines:
        # 跳过围栏代码块内容
        stripped = line.lstrip()
        if stripped.startswith('```') or stripped.startswith('~~~'):
            in_code = not in_code
            out.append(line)
            continue
        if in_code:
            out.append(line)
            continue
        line = _LINK_RE.sub(_md_sub, line)
        out.append(_HTML_HREF_RE.sub(_html_sub, line))
    return '\n'.join(out)
