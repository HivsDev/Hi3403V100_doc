"""修正图片/链接 URL 中未编码的特殊字符，避免 OBS 静态托管 404。

背景：本站构建产物（``mkdocs build`` 的 ``site/``）整体托管在华为云 OBS，并以
"静态网站托管"方式对外提供访问（域名如 ``docs.hisilicon.com``）。OBS 静态网站
托管的请求端点会按 application/x-www-form-urlencoded 规则解码 URL 路径，其中
**裸 ``+`` 会被解码成空格**。

当对象 key 形如 ``.../I2C主设备发送AT+STARTI2CMASTER.png``（文件名里含字面 ``+``）
时，mkdocs 生成的页面里的图片 URL 是：

    .../I2C%E4%B8%BB...%E9%80%81AT+STARTI2CMASTER.png

中文已被正确编码为 UTF-8 百分号序列，但 ``+`` 仍是裸字符。OBS 端点把 ``+``
解码成空格后，实际去查的对象 key 变成了 ``...AT STARTI2CMASTER.png``（带空格），
与 OBS 里真实存储的对象（带 ``+``）对不上，命中静态托管的错误文档，最终返回
站点的 404 页面（注意：这是前端 HTML 404，不是 OBS 的 XML ``NoSuchKey``）。

修复思路：在 markdown 被解析之前（``on_page_markdown``）做纯文本预处理，对页面
里的 markdown 链接/图片 ``![](url)``、``[text](url)`` 以及原生 HTML
``<img src="...">`` / ``<a href="...">`` 的 URL 做 percent-encoding，把 OBS 静态
托管会"误伤"的字符（路径段里的 ``+``、中文、空格等）转义掉。

处理规则（``_encode_url``）：

- 跳过 ``data:`` / ``mailto:`` / ``tel:`` 以及纯锚点（``#frag``）。
- 用 ``[^?#]`` 把 URL 拆成"路径段"与"query/fragment 段"，只对路径段编码；
  query 里的 ``+`` 在语义上合法（表示空格），保持原样。
- 路径段里：
  - 已是 ``%XX`` 形式的字节 → 原样保留（**绝不二次编码**，保证幂等）。
  - RFC 3986 保留/安全字符 ``-_.~!$&'()*+,;=:@/`` → 原样保留，**除了 ``+``**，
    它会被改写成 ``%2B``（这正是本次要修的坑）。
  - 其余字符（中文、空格等）→ 调 ``urllib.parse.quote`` 编码。
- 幂等：对已经编码过的 URL 再次调用，结果不变。

刻意保留、不处理的情形：

- 代码围栏（`` ``` `` / ``~~~``）内部 —— 原样输出，绝不修改。
- **行内代码**（`` `...` ``）内部 —— 原样，不编码（里面是字面量，非真实链接）。
- **反斜杠转义的** ``\![...]`` / ``\[...]`` —— 已被转义，不是真实图片/链接，跳过。
- **reference 式链接定义**（``[ref]: url``）—— 不是 ``(...)`` 形式，正则不匹配，自动跳过。
- HTML 端**只处理图片相关标签**（``<img>`` / ``<a>``）的 ``src``/``href``；``<script>``、
  ``<iframe>``、``<source>`` 等其它资源的 URL 不动，避免误伤静态资源引用。
- 外部 ``http(s)://`` 绝对 URL 同样编码（中文/空格/裸 ``+`` 一样会触发 OBS 端
  问题），不区别对待。
"""

import re
from urllib.parse import quote

# 代码围栏起始标记（行首最多 3 空格缩进，``` 或 ~~~）。
_FENCE_RE = re.compile(r'^\s{0,3}(```|~~~)')

# 行内代码 span：成对的反引号串（含反引号本身），编码前先占位保护，避免对其
# 内部的 ![](...)/src= 等字面量误编码。匹配 `` `...` `` 或 `` ``...`` `` 等。
# 用占位而非排除，可稳妥处理一行内多个 span、跨内容等情况。
_INLINE_CODE_RE = re.compile(r'(`+)(.+?)\1')

# 占位符：行内代码替换为该 token，转码完成后再还原。token 含 NUL 字节，
# 正常 markdown 不会出现，确保不与正文冲突。
_CODE_PLACEHOLDER = '\x00__CODE_{0}__\x00'

# Markdown 链接/图片：[text](url) / ![alt](url)
# 分组：(prefix 含左括号)(url)(可选 "title"/'title')(右括号)
# 借鉴 _homepage.py 的 md_pattern，url 段用 [^\s)]+ 避免吃掉括号。
# 反斜杠转义（\![...] / \[...]）的排除不在此正则处理：负向逆序断言在 '!' 可选
# 时会被引擎绕过（'!?匹配空'使断言位置落到 '[' 而非 '!'）。改由 on_page_markdown
# 在转码前用 _ESCAPE_SPAN_RE 把这类转义片段占位保护，与行内代码同策略。
_MD_LINK_RE = re.compile(
    r'(!?\[[^\]]*\]\()\s*([^\s)]+)(\s+(?:"[^"]*"|\'[^\']*\'))?\s*(\))'
)

# 反斜杠转义的图片/链接起始：\![ 或 \[，占位保护避免被 _MD_LINK_RE 误匹配。
_ESCAPE_SPAN_RE = re.compile(r'\\(!?\[)')

# HTML 属性：仅处理图片相关标签 <img>/<a> 的 src/href。
# 显式限定标签名，避免误伤 <script src>/<iframe src>/<source src> 等静态资源。
# 分组：(tag)(attr)(quote)(url)(quote)
_HTML_ATTR_RE = re.compile(
    r'(<(?:img|a)\b[^>]*?\b)(src|href)(\s*=\s*)(["\'])(.*?)\4',
    re.IGNORECASE,
)

# 跳过编码的特殊 URL（data URI、邮件、电话、纯锚点）。
_SKIP_SCHEMES = ('data:', 'mailto:', 'tel:')

# 把 URL 拆成"路径段"与"query/fragment 段"，仅对路径段编码。
_SPLIT_QUERY_RE = re.compile(r'^([^?#]*)([?#].*)?$', re.DOTALL)

# 已编码的 %XX 序列（用于幂等：跳过二次编码）。
_PCT_RE = re.compile(r'%[0-9A-Fa-f]{2}')

# 路径段里"安全保留"的原样字符（RFC 3986 unreserved + 部分保留），不含 '+'。
# '+' 单独处理（强制改 %2B）。
_SAFE_PATH_CHARS = "-_.~!$&'()*,;=:@/"

# query/fragment 段里连同 '+'、'?#' 分隔符一起保留（query 中 '+' 表示空格，
# 语义合法；'?#' 作为 query/fragment 的结构性分隔符必须保留）。
_SAFE_QUERY_CHARS = _SAFE_PATH_CHARS + '+?#'


def _encode_url(url):
    """对单个 URL 做 percent-encoding，返回修正后的 URL（幂等）。

    - 跳过 ``data:`` / ``mailto:`` / ``tel:`` 与纯锚点 ``#frag``。
    - 路径段里：保留 ``%XX``（不二次编码）与安全字符，强制把 ``+`` 改成 ``%2B``，
      其余字符（中文、空格等）编码。
    - query/fragment 段：保留 ``%XX`` 与安全字符（含 ``+`` 与 ``?`` ``#`` 分隔符），
      其余编码。
    """
    # 纯锚点（#frag）与 data:/mailto:/tel: 直接原样返回。
    if url.startswith('#') or url.lower().startswith(_SKIP_SCHEMES):
        return url

    split = _SPLIT_QUERY_RE.match(url)
    path_part = split.group(1)
    suffix = split.group(2) or ''

    return _encode_segment(path_part, _SAFE_PATH_CHARS, encode_plus=True) \
        + _encode_segment(suffix, _SAFE_QUERY_CHARS, encode_plus=False)


def _encode_segment(segment, safe_chars, encode_plus):
    """编码 URL 的某一段。

    已是 ``%XX`` 的字节原样保留（幂等）；``safe_chars`` 内的字符保留；其余用
    ``urllib.parse.quote`` 编码。当 ``encode_plus`` 为真时，``+`` 强制编码为
    ``%2B``（即便它在 safe_chars 之外也会被特殊处理）。
    """
    out = []
    i = 0
    n = len(segment)
    while i < n:
        ch = segment[i]
        # 已编码的 %XX 序列原样保留，避免二次编码。
        if ch == '%' and i + 2 < n + 1 and _PCT_RE.match(segment, i):
            out.append(segment[i:i + 3])
            i += 3
            continue
        if encode_plus and ch == '+':
            out.append('%2B')
            i += 1
            continue
        if ch in safe_chars:
            out.append(ch)
            i += 1
            continue
        # 其余字符（含中文、空格、其它多字节）按 UTF-8 编码。
        out.append(quote(ch, safe=''))
        i += 1
    return ''.join(out)


def _md_replacer(match):
    """替换 markdown 链接/图片里的 url 段。"""
    prefix, url, title, close = match.groups()
    rebuilt = prefix + _encode_url(url.strip())
    if title:
        rebuilt += title
    return rebuilt + close


def _html_replacer(match):
    """替换 <img>/<a> 标签 src/href 里的 url 段。"""
    tag, attr, eq, quote_char, url = match.groups()
    return f'{tag}{attr}{eq}{quote_char}{_encode_url(url)}{quote_char}'


def on_page_markdown(markdown, page, config, files):
    """在 markdown 解析前，对图片/链接 URL 做特殊字符 percent-encoding。

    - 跳过代码围栏（`` ``` `` / ``~~~``）整段内容。
    - 跳过行内代码 span（`` `...` ``）与反斜杠转义的 ``\\![`` / ``\\[``：
      先占位保护，转码完再还原，避免对其字面量误编码。
    - 对其余位置的 markdown 链接/图片与 ``<img>``/``<a>`` 的 ``src``/``href`` 转码。
    """
    lines = markdown.split('\n')
    result = []
    in_fence = False

    for line in lines:
        # 代码围栏：切换状态后原样输出，围栏内绝不修改。
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            result.append(line)
            continue
        if in_fence:
            result.append(line)
            continue

        # 围栏外：先把行内代码 span、反斜杠转义起始占位保护，避免误编码。
        protected = []

        def _stash(match):
            protected.append(match.group(0))
            return _CODE_PLACEHOLDER.format(len(protected) - 1)

        masked = _INLINE_CODE_RE.sub(_stash, line)
        masked = _ESCAPE_SPAN_RE.sub(_stash, masked)

        # 转码 markdown 链接/图片、HTML <img>/<a> 的 src/href。
        masked = _MD_LINK_RE.sub(_md_replacer, masked)
        masked = _HTML_ATTR_RE.sub(_html_replacer, masked)

        # 还原被保护的片段。
        for idx, span in enumerate(protected):
            masked = masked.replace(_CODE_PLACEHOLDER.format(idx), span)

        result.append(masked)

    return '\n'.join(result)
