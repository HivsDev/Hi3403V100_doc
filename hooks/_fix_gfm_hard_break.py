"""GFM 行尾反斜杠硬换行，构建期转换为 Python-Markdown 支持的行尾两空格。

背景：CommonMark/GitCode 把「行尾单个 `` \\ ``」渲染为硬换行（``<br>``）；Python-Markdown
不支持该语法，会把 `` \\ `` 原样输出成字面文本，页面上直接显示出反斜杠。Python-Markdown
支持的等价写法是「行尾两个及以上空格」→ ``<br />``。

本 hook 在 mkdocs 解析之前（``on_page_markdown``）做纯文本预处理，源文件保持
GitCode 兼容写法不动：

- 行尾单个 `` \\ `` 且下一行是接续内容 → 替换为行尾两个空格（转成 ``<br />``）；
- 行尾单个 `` \\ `` 且下一行是空行/块级起点/文件末尾 → 硬换行无意义，直接删除 `` \\ ``，
  避免字面显示；
- `` \\\\ ``（转义反斜杠）与行首缩进 ≥4 空格的行（缩进代码块保护，如 shell 续行符）不动；
- 代码围栏内部绝不修改。
"""

import re

_FENCE_RE = re.compile(r'^\s{0,3}(?:`{3,}|~{3,})')
# 块级起点：标题/列表/围栏/引用/表格行——其后接的行尾 \ 属段末，删除而非换行
_BLOCK_START_RE = re.compile(r'^\s{0,3}(?:#{1,6}\s|[-*+]\s|\d{1,9}[.)]\s|>|`{3}|~{3}|\|)')


def on_page_markdown(markdown, page, config, files):
    """在 markdown 解析前，把 GFM 行尾反斜杠硬换行转换为两空格硬换行。"""
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

        stripped = line.rstrip()
        # 行尾单个 \（转义 \\ 不算）；缩进 ≥4 的行可能是缩进代码块，不动
        if (stripped.endswith('\\') and not stripped.endswith('\\\\')
                and len(line) - len(line.lstrip()) < 4):
            nxt = lines[i + 1] if i + 1 < len(lines) else ''
            if nxt.strip() and not _BLOCK_START_RE.match(nxt):
                # 下一行接续本段 → 两空格硬换行
                result.append(stripped[:-1].rstrip() + '  ')
            else:
                # 段末/块级前 → 硬换行无意义，删除 \
                result.append(stripped[:-1].rstrip())
            continue
        result.append(line)

    return '\n'.join(result)
