"""列表项内 1~3 空格缩进的图片行，构建期重缩进为 4 空格。

背景：Python-Markdown 要求列表项内的块内容按「4 空格一级」缩进才归入列表项；
而 CommonMark/GitCode 按「标记内容列」识别（`1. `/`2. ` 为 3、`- ` 为 2）。源
文档按 GitCode 习惯在列表项下写 3 空格缩进的图片行时，mkdocs 构建会把图片段
落"踢出"列表项，有序列表被截断成多个 `<ol>`（起始编号还被忽略），后续 `2.`、
`3.` 项全部显示为 1。

与 ``_fix_fence_in_list.py`` 同根同法（源文件保持 GitCode 兼容写法不动，构建期
重缩进）：把「上一非空行是顶层列表项 + 缩进 1~3 空格的图片行」重缩进为 4 空格。
图片行含 Markdown 图片 ``![alt](url)`` 与原生 HTML ``<img ...>`` 两种形式；
由列表项引出的连续图片链（图片行之间允许空行）一并处理。

刻意保留、不处理的情形：
- 图片缩进 ≥4 空格（已符合规则，幂等跳过）；
- 顶格（0 缩进）图片行——可能有意放在列表外，不动；
- 无列表上下文的缩进图片行（如普通段落后的缩进图片）——不动，防止误伤；
- 上一非空行是嵌套列表项（行首有缩进）——嵌套缩进语义复杂，转人工；
- 代码围栏内部——状态跟踪，绝不修改。
"""

import re

# 围栏行（0~3 空格 + ``` 或 ~~~），用于跟踪围栏状态
_FENCE_RE = re.compile(r'^\s{0,3}(?:`{3,}|~{3,})')
# 顶层列表项行（行首无缩进）
_LIST_ITEM_RE = re.compile(r'^(?:[-*+]|\d{1,9}[.)])[ \t]+')
# 图片行：Markdown 图片或原生 <img>，行首 1~3 空格缩进
_IMG_RE = re.compile(r'^(\s{1,3})(!\[[^\]]*\]\([^)]+\)|<img\b.*)$')


def on_page_markdown(markdown, page, config, files):
    """在 markdown 解析前，把列表项内的 1~3 空格缩进图片行重缩进为 4 空格。"""
    lines = markdown.split('\n')
    result = []
    fixed_idx = set()   # 已被本 hook 重缩进的图片行（图片链传递用）
    in_fence = False

    def prev_nonblank_idx(idx):
        j = idx - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        return j

    for i, line in enumerate(lines):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            result.append(line)
            continue
        if in_fence:
            result.append(line)
            continue
        m = _IMG_RE.match(line)
        if m:
            j = prev_nonblank_idx(i)
            prev = lines[j] if j >= 0 else ''
            # 触发：上一非空行是顶层列表项，或上一非空行是本 hook 已重缩进的图片（图片链）
            if _LIST_ITEM_RE.match(prev) or j in fixed_idx:
                result.append(' ' * (4 - len(m.group(1))) + line)
                fixed_idx.add(i)
                continue
        result.append(line)

    return '\n'.join(result)
