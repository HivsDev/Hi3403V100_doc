"""列表项内 1~3 空格缩进的围栏代码块，构建期重缩进为 4 空格；顶格围栏按意图判定。

背景：Python-Markdown + pymdownx.superfences 要求列表内嵌围栏按「4 空格一级」
缩进才归入列表项；而 CommonMark/GitCode 按「标记内容列」识别（`1. `/`2. ` 为 3、
`- ` 为 2）。源文档按 GitCode 习惯写 3 空格缩进的围栏时，mkdocs 构建会把代码块
"踢出"列表项，有序列表被截断成多个 `<ol>`（起始编号还被忽略），后续 `3.` 项显示
为 1。

本 hook 在 mkdocs 解析之前（``on_page_markdown``）做纯文本预处理，源文件保持
GitCode 兼容写法不动。两类触发：

1. 缩进 1~3 空格的围栏 + 上一非空行是顶层列表项 → 整块重缩进为 4 空格；
2. **顶格（0 缩进）围栏** + 上一非空行是顶层列表项，且满足以下任一意图信号：
   - 夹在连续列表项之间（闭栏后首个非空行仍是列表项）——序号必然受损，必须归入前项；
   - 前置列表项以「：」/「:」结尾（如 `1. 创建工程：` 后跟命令）——代码明显是该项内容；
   满足则整块（开栏/体/闭栏）加 4 空格缩进。其余顶格围栏可能是有意放在列表外，不动。

刻意保留、不处理的情形：
- 围栏缩进 ≥4 空格（已符合 superfences 规则，存量 110 处正确写法，幂等跳过）；
- 顶格围栏但无上述意图信号——不动；
- 上一非空行是嵌套列表项（行首有缩进）——嵌套层级缩进语义复杂，转人工；
- 围栏未正确闭合——整块保持原样，避免半改损坏文档；
- 围栏内部（含嵌套围栏）——外层状态跟踪，绝不修改。
"""

import re

# 任意围栏行（行首 0~3 空格 + ``` 或 ~~~；superfences 嵌套即 4 空格一级）
_FENCE_RE = re.compile(r'^(\s{0,3})(`{3,}|~{3,})(.*)$')
# 顶层列表项行（行首无缩进）：无序 [-*+] 或有序 \d+[.)]，后跟空白
_LIST_ITEM_RE = re.compile(r'^(?:[-*+]|\d{1,9}[.)])[ \t]+')


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
    首个顶格行是列表项则为真（覆盖「围栏跟在项内缩进正文之后」的链条场景）。
    途中遇到围栏标记行立即返回 False——围栏内部/边界处上下文不可靠，不跨围栏回溯。"""
    j = idx - 1
    while j >= 0:
        cur = lines[j]
        if not cur.strip():
            j -= 1
            continue
        if _FENCE_RE.match(cur):
            return False
        if _indent_len(cur) == 0:
            return bool(_LIST_ITEM_RE.match(cur))
        j -= 1
    return False


def on_page_markdown(markdown, page, config, files):
    """在 markdown 解析前，把列表项内的 1~3 空格缩进围栏重缩进为 4 空格。"""
    lines = markdown.split('\n')
    result = []
    i = 0
    n = len(lines)
    in_fence = False  # 全局围栏状态：围栏内容（含外层代码块内部）绝不修改

    def prev_nonblank(idx):
        j = idx - 1
        while j >= 0 and not lines[j].strip():
            j -= 1
        return lines[j] if j >= 0 else ''

    while i < n:
        line = lines[i]
        m = _FENCE_RE.match(line)
        is_fence_line = bool(m)
        if in_fence:
            result.append(line)
            if is_fence_line:
                in_fence = False
            i += 1
            continue
        if not is_fence_line:
            result.append(line)
            i += 1
            continue
        prev = prev_nonblank(i)
        indent = len(m.group(1))
        # 触发条件一：缩进 1~3 + 上一非空行是顶层列表项
        # 触发条件二：顶格 + 上一非空行是顶层列表项 + 意图信号（夹在连续项之间 / 项以冒号结尾）
        top_level = indent == 0 and _LIST_ITEM_RE.match(prev)
        indent_1_3 = 1 <= indent <= 3 and _in_top_item(lines, i)
        if indent_1_3 or top_level:
            fence_char = m.group(2)[0]
            # 收集到闭栏行（同种围栏字符、无语言标注、缩进 0~3）；未闭合则整块保持原样
            block = [line]
            k = i + 1
            closed = False
            while k < n:
                cur = lines[k]
                cm = _FENCE_RE.match(cur)
                if cm and cm.group(2)[0] == fence_char and not cm.group(3).strip():
                    block.append(cur)
                    closed = True
                    k += 1
                    break
                block.append(cur)
                k += 1
            # 顶格围栏的意图信号判定（1~3 缩进的老规则不需要）
            if top_level and closed:
                nk = k
                while nk < n and not lines[nk].strip():
                    nk += 1
                next_is_item = nk < n and _LIST_ITEM_RE.match(lines[nk])
                ends_colon = prev.rstrip().endswith(("：", ":"))
                if not (next_is_item or ends_colon):
                    closed = False  # 无意图信号：顶格围栏保持原样
            if closed:
                delta = 4 - indent
                for b in block:
                    if b.strip() and _indent_len(b) >= indent:
                        result.append(' ' * delta + b)
                    else:
                        result.append(b)
                i = k
                continue
        # 不满足重缩进条件：原样保留，并按围栏开/闭切换状态
        result.append(line)
        in_fence = not in_fence
        i += 1

    return '\n'.join(result)
