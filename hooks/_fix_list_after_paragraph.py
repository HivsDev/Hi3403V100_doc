"""段落行后紧跟列表行（无空行）时自动补空行，避免列表被并入段落。

背景：Python-Markdown（mkdocs 的底层引擎）不支持列表"打断"段落：当一行普通
文字后面直接跟 ``-   列表项`` / ``1. 列表项``（中间没有空行）时，列表标记不会
被解析，列表行会被当作段落的软换行并入同一个 ``<p>``。HTML 折叠空白后，多个
列表项与段落文字全部挤在一行里，行首还残留字面的 ``-   ``。

IDP 导出的文档大量使用这种写法，引用块内尤其常见（如 ``>-   T<sub>N</sub>...``
紧跟 ``>超过扩展工作环境参数数值...``）。本 hook 在 markdown 被解析之前
（``on_page_markdown``）做纯文本预处理：仅在"文字行与列表行紧贴、中间无空行"
的位置插入一个空行，让列表独立成块。

处理的列表行（允许引用块 ``>`` 前缀，标记本身最多 3 空格缩进）：

- 无序列表：``-   项目`` / ``* 项目`` / ``+ 项目``
- 有序列表：``1. 项目`` / ``1) 项目``

刻意保留、不处理的情形：

- 前一行不是普通文字行（空行、标题、表格行、HTML 行、admonition、定义列表、
  列表项、代码围栏行等）—— 只有"普通文字行 + 列表行"紧贴才补空行，避免影响
  嵌套列表等已有结构。
- 前一行带缩进（可能是列表项的续行/嵌套内容）—— 保守跳过。
- 前一行与列表行的引用块深度不一致（如 ``>文字`` 后跟 ``>>- 项目``）—— 保守跳过。
- 代码围栏（`` ``` `` / ``~~~``）内部 —— 原样输出，绝不修改。

引用块内的"段落+列表"相邻，分隔行会保留引用前缀（如 ``>``），以免把一个
引用块拆成多个。
"""

import re

# 代码围栏起始标记（行首最多 3 空格缩进，``` 或 ~~~）。
_FENCE_RE = re.compile(r'^\s{0,3}(```|~~~)')

# 列表标记：无序 [-*+] 或有序 \d+[.)]，后跟空白或行尾；标记前最多 3 空格缩进。
_LIST_RE = re.compile(r'^\s{0,3}(?:[-*+]|\d{1,9}[.)])(?:\s|$)')

# 引用块前缀中的一个 ">" 记号（允许记号前后的空白）。
_QUOTE_TOKEN_RE = re.compile(r'^[ \t]*>[ \t]?')

# 前一行内容以这些字符开头时不视为普通文字行（结构性内容，保守跳过）。
_STRUCTURAL_FIRST_CHARS = set('#|<!=:`~')


def _strip_quote(line):
    """拆出行首的引用块前缀。

    返回 (前缀, 内容, 引用深度)。前缀为连续的 ">" 记号及其间空白，如
    ``"> "`` / ``">"`` / ``">> "``；非引用行前缀为空串、深度为 0。
    """
    prefix = ''
    depth = 0
    rest = line
    while True:
        m = _QUOTE_TOKEN_RE.match(rest)
        if not m:
            break
        prefix += m.group(0)
        depth += 1
        rest = rest[m.end():]
    return prefix, rest, depth


def _is_list_content(content):
    """去掉引用前缀后的内容是否为列表行。"""
    return bool(_LIST_RE.match(content))


def _is_plain_text_content(content):
    """去掉引用前缀后的内容是否为普通文字行。

    空行、纯引用分隔行（内容为空）、带缩进的行，以及标题/表格/HTML/
    admonition/定义列表/列表等结构行都不算。
    """
    if not content.strip():
        return False
    if content[0] in ' \t':
        return False
    if content[0] in _STRUCTURAL_FIRST_CHARS:
        return False
    if _is_list_content(content):
        return False
    return True


def on_page_markdown(markdown, page, config, files):
    """在 markdown 解析前，为紧贴的文字行与列表行之间补一个空行。"""
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

        _, cur_content, cur_depth = _strip_quote(line)
        if _is_list_content(cur_content) and result:
            prev = result[-1]
            prev_prefix, prev_content, prev_depth = _strip_quote(prev)
            # 相邻两行：普通文字行 + 列表行，且引用块深度一致 → 中间补空行。
            if prev_depth == cur_depth and _is_plain_text_content(prev_content):
                # 引用块内的段落+列表，分隔行保留引用前缀以免拆散引用块。
                result.append(prev_prefix.rstrip() if prev_prefix else '')

        result.append(line)

    return '\n'.join(result)
