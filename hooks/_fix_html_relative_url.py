"""为原生 HTML ``<img>``/``<a>`` 的相对 URL 补 ``../`` 前缀（use_directory_urls 场景）。

背景：华为导出文档大量使用原生 HTML 图片（``<img src="figures/x.png">``）与链接。
MkDocs 只会重写 **Markdown 语法** 的相对链接/图片（``![](x.png)``、``[文字](x)``），
原生 HTML 标签会原样透传到产物。当 ``use_directory_urls: true`` 时，非 index 页面
（如 ``foo/bar.md``）的页面 URL 是目录 ``foo/bar/``，浏览器把相对 src 解析到
``foo/bar/figures/x.png``，而图片实际被拷贝到 ``foo/figures/`` —— 引用 404。
"目录名=文件名"的文档（``tools/X/X.md``）表现为路径多出一层同名目录。

MkDocs 原生对 markdown 图片的处理，是把目标 URL 换算成"相对页面 URL"（非 index
页等效于统一加 ``../`` 前缀，见 ``mkdocs.structure.pages`` 的
``_RelativePathTreeprocessor``）。本 hook 在 ``on_page_markdown`` 阶段对原生
HTML 做同样的事，让两类写法行为一致。

处理规则：

- 仅当 ``use_directory_urls`` 为真 **且** 当前页面不是 index 页时生效。未启用
  ``use_directory_urls`` 的项目（页面 URL 形如 ``foo/bar.html``，相对引用本就
  正确）与 index 页面（URL 目录就是源文件所在目录）原样返回，因此本 hook 可
  安全复用于各项目的基础配置。
- 仅处理 ``<img>`` / ``<a>`` 的 ``src`` / ``href``（与 ``_encode_image_url.py``
  的标签范围保持一致），``<script>``、``<iframe>`` 等其它标签的 URL 不动。
- 仅处理"相对 URL"：外部绝对地址（http:// 等）、协议相对（``//``）、站内根路径
  （``/static/...``）、纯锚点/纯查询（``#frag``、``?q=1``）、``mailto:``/
  ``tel:``/``data:`` 一律跳过。
- 直接字符串拼接 ``'../' + url``：``figures/x.png → ../figures/x.png``、
  ``../a.png → ../../a.png``。页面 URL 目录恒比源文件所在目录深恰好一层，
  前缀拼接即精确正确。**不能**用 ``urljoin('../', url)`` —— 相对基址里的
  ``..`` 会被 urljoin 按"出栈到根即丢弃"规则吞掉，结果反而丢层级；mkdocs
  原生处理 markdown 链接时也用的是自实现的 ``get_relative_url`` 而非 urljoin。
- 与 ``_encode_image_url.py`` 的先后顺序不敏感（路径拼接与百分号编码互不干扰），
  本 hook 注册在其之前，让"编码"作为 URL 的最后一道处理。

刻意保留、不处理的情形：

- 代码围栏（`` ``` `` / ``~~~``）内部 —— 原样输出，绝不修改。
- 行内代码（`` `...` ``）内部 —— 占位保护后还原，避免改到示例代码。
- 原生 HTML 里的 ``<a href="xxx.md">`` 文档间链接：路径前缀会被修正，但
  ``.md → 页面 URL`` 的改写是另一回事（MkDocs 对 markdown 语法链接也才做），
  不在本 hook 范围内。
"""

import re
from urllib.parse import urlsplit

# 代码围栏起始标记（行首最多 3 空格缩进，``` 或 ~~~）。
_FENCE_RE = re.compile(r'^\s{0,3}(```|~~~)')

# 行内代码 span：成对的反引号串（含反引号本身），处理前先占位保护。
_INLINE_CODE_RE = re.compile(r'(`+)(.+?)\1')

# 占位符：行内代码替换为该 token，处理完再还原。token 含 NUL 字节，
# 正常 markdown 不会出现，确保不与正文冲突。
_CODE_PLACEHOLDER = '\x00__CODE_{0}__\x00'

# HTML 属性：仅处理图片相关标签 <img>/<a> 的 src/href（与 _encode_image_url.py
# 一致）。显式限定标签名，避免误伤 <script src>/<iframe src> 等静态资源。
# 分组：(tag)(attr)(eq)(quote)(url)(quote)
_HTML_ATTR_RE = re.compile(
    r'(<(?:img|a)\b[^>]*?\b)(src|href)(\s*=\s*)(["\'])(.*?)\4',
    re.IGNORECASE,
)


def _fix_url(url):
    """相对 URL 补 ../ 前缀；绝对地址、根路径、纯锚点等原样返回。"""
    url = url.strip()
    scheme, netloc, path, query, fragment = urlsplit(url)
    if scheme or netloc or not path or path.startswith(('/', '\\')):
        return url
    return '../' + url


def _html_replacer(match):
    """替换 <img>/<a> 标签 src/href 里的 url 段。"""
    tag, attr, eq, quote_char, url = match.groups()
    return f'{tag}{attr}{eq}{quote_char}{_fix_url(url)}{quote_char}'


def on_page_markdown(markdown, page, config, files):
    """在 markdown 解析前，为原生 HTML 相对 URL 补 ../ 前缀（按需启用）。"""
    if not config.get('use_directory_urls') or page.is_index:
        return markdown

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

        # 围栏外：先把行内代码 span 占位保护，避免改到示例代码。
        protected = []

        def _stash(match):
            protected.append(match.group(0))
            return _CODE_PLACEHOLDER.format(len(protected) - 1)

        masked = _INLINE_CODE_RE.sub(_stash, line)
        masked = _HTML_ATTR_RE.sub(_html_replacer, masked)

        # 还原被保护的片段。
        for idx, span in enumerate(protected):
            masked = masked.replace(_CODE_PLACEHOLDER.format(idx), span)

        result.append(masked)

    return '\n'.join(result)
