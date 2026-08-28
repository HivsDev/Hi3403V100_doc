"""图文相邻无空行时自动补空行，避免文字与图片渲染进同一个段落。

背景：Python-Markdown（mkdocs-material 的底层引擎）按"空行分段"。当一段文字
（含 ``**图 N**`` 图注）与一行图片相邻、中间没有空行时，二者会被合并进同一个
``<p>``，渲染成 ``<p>文字 <img ...></p>`` 或 ``<p><img ...> 文字</p>`` —— 文字和
大图挤在同一行。

历史上曾启用 ``nl2br`` 扩展来规避（单换行也转 ``<br>``），但它会把正文里所有
软换行都变成强制换行，引入副作用，现已移除。本 hook 在 Markdown 被解析之前
（``on_page_markdown``）做纯文本预处理：仅在"文字行与图片行紧贴、中间无空行"
的位置插入一个空行，让二者各自独立成块。范围精确，不影响其它内容。

两个方向都处理：

- 文字 → 图片：``**图 N** ...`` 紧跟 ``![](x.png)`` / ``<img ...>``
- 图片 → 文字：``<img ...>`` 紧跟 ``2. 后续步骤...``（华为导出文档常见）

图片行（行首允许缩进、允许引用块 ``>`` 标记）：

- Markdown 图片：``    ![](figures/x.png)``
- 原生 HTML 图片：``    <img src="..." width="700">``

刻意保留、不处理的情形：

- 句中内联小图标（如 ``单击 <img width="20"> 按钮``）—— 不在行首，不匹配，
  自动保持内联。
- ``说明：/注意：`` 提示块里"小图标 + 标签 + 正文"同段 —— 这些行的相邻行是
  空行或 ``>`` 行，本就不触发补行。
- 代码围栏（`` ``` `` / ``~~~``）内部 —— 原样输出，绝不修改。

引用块（``>``）内的图文相邻，分隔行会保留引用前缀（如 ``>``），以免把一个
引用块拆成多个。
"""

import re

# 代码围栏起始标记（行首最多 3 空格缩进，``` 或 ~~~）。
_FENCE_RE = re.compile(r'^\s{0,3}(```|~~~)')

# 图片行：行首允许缩进和引用块 ">" 标记，之后是 markdown 图片或 HTML <img>。
_IMG_RE = re.compile(r'^\s*(>\s*)*(?:!\[[^\]]*\]\(|<img\b)')

# 引用块前缀（连续的 ">" 及其后空白），用于在引用块内补行时保持结构。
_QUOTE_PREFIX_RE = re.compile(r'^(\s*(>\s*)+)')


def _quote_prefix(line):
    """返回行的引用块前缀（如 ``"> "``），无引用标记返回 ``''``。"""
    m = _QUOTE_PREFIX_RE.match(line)
    return m.group(1) if m else ''


def _has_text(line):
    """是否为含实际文字内容的行（去掉引用前缀与首尾空白后仍有字符）。

    纯引用块分隔行（如 ``>``、``> ``）不含文字内容，不算文字行。
    """
    return bool(_QUOTE_PREFIX_RE.sub('', line).strip())


def _is_img_line(line):
    """是否为图片行（代码围栏已由调用方排除）。"""
    return bool(_IMG_RE.match(line))


def on_page_markdown(markdown, page, config, files):
    """在 markdown 解析前，为紧贴的文字行与图片行之间补一个空行。"""
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

        cur_img = _is_img_line(line)

        if result:
            prev = result[-1]
            prev_img = _is_img_line(prev)
            # 相邻两行：一个图片行、一个非空文字行 → 中间补空行。
            # 注意用 _has_text 判文字行，避免把纯引用分隔行（"> "）误算成文字。
            if (cur_img and _has_text(prev) and not prev_img) or \
               (not cur_img and _has_text(line) and prev_img):
                prefix = _quote_prefix(prev)
                # 引用块内的图文，分隔行保留引用前缀以免拆散引用块。
                result.append(prefix.rstrip() if prefix else '')

        result.append(line)

    return '\n'.join(result)
