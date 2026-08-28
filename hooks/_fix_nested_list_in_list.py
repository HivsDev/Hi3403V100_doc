"""顶层列表项下 1~3 空格缩进的子列表行，构建期重缩进为 4 空格。

背景：Python-Markdown 要求嵌套列表按「4 空格一级」缩进；CommonMark/GitCode 按
「父项标记内容列」识别（`1. `/`4. ` 为 3、`- ` 为 2）。源文档按 GitCode 习惯在
列表项下写 3 空格缩进的子列表时，mkdocs 不将其识别为子列表，而是吞并为父列表的
**平级条目**——无序子列表整体消失（变成父有序列表的编号项），并顺延污染父列表
后续编号。

本 hook 在 mkdocs 解析之前（``on_page_markdown``）做纯文本预处理：1~3 空格缩进的
列表行，若其**所属上下文是某个顶层列表项**（从当前行向上跳过空行与全部缩进行，
遇到的第一个顶格行是列表项），则重缩进为 4 空格。该判定同时覆盖：

- 上一行就是顶层列表项（子列表起点）；
- 上一行是已缩进的兄弟子项 / 已是 ≥4 空格的嵌套子项（同组延续）；
- 上一行是项内的缩进正文（如 `   **说明**` 后跟 `   - 子项`——旧链条规则会漏掉
  导致子列表仍被压平，本规则修复）。

刻意保留、不处理的情形：
- 子列表缩进 ≥4 空格（已符合规则，幂等跳过）；
- 顶格（0 缩进）列表行——是平级新列表，不动；
- 向上回溯遇到的顶格行不是列表项（段落/标题等）——不在任何列表项内，不动；
- 代码围栏内部——状态跟踪，绝不修改。
"""

import re

_FENCE_RE = re.compile(r'^\s{0,3}(?:`{3,}|~{3,})')
# 顶层列表项行（行首无缩进）：无序 [-*+] 或有序 \d+[.)]，后跟空白
_TOP_ITEM_RE = re.compile(r'^(?:[-*+]|\d{1,9}[.)])[ \t]+')
# 1~3 空格缩进的列表行
_NESTED_LIST_RE = re.compile(r'^(\s{1,3})((?:[-*+]|\d{1,9}[.)])[ \t]+.*)$')


def _indent_len(line):
    n = 0
    for ch in line:
        if ch == ' ':
            n += 1
        elif ch == '\t':
            n += 4
        else:
            break
    return n


def _in_top_item(lines, idx):
    """idx 行是否处于某个顶层列表项的内容区：向上跳过空行与全部缩进行，
    首个顶格行是列表项则为真。"""
    j = idx - 1
    while j >= 0:
        cur = lines[j]
        if not cur.strip():
            j -= 1
            continue
        if _indent_len(cur) == 0:
            return bool(_TOP_ITEM_RE.match(cur))
        j -= 1
    return False


def on_page_markdown(markdown, page, config, files):
    """在 markdown 解析前，把顶层列表项下的 1~3 空格缩进子列表重缩进为 4 空格。"""
    lines = markdown.split('\n')
    result = []
    in_fence = False

    for i, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            result.append(line)
            continue
        if in_fence:
            result.append(line)
            continue
        m = _NESTED_LIST_RE.match(line)
        if m and _in_top_item(lines, i):
            result.append(' ' * (4 - len(m.group(1))) + line)
            continue
        result.append(line)

    return '\n'.join(result)
