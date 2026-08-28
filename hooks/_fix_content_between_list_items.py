"""编号列表项之间/末项后的内容归项规则（构建期重缩进为 4 空格）。

背景：Python-Markdown 与 CommonMark/GitCode 的两处差异叠加：
1. 列表项之间/之后的内容会中断列表，且 Python-Markdown 忽略有序列表起始编号
   （后续 `N.` 显示为 1，GitCode 因保留 ``<ol start=N>`` 看起来正常）；
2. 嵌套内容需 4 空格缩进（GitCode 按内容列识别 1~3 空格即项内容）。

处理规则（位置区分，源码缩进作为末项内容的意图标记）：

1. **夹心规则**（序号 N 与 N+1 之间的内容，序号连续性攸关）：
   顶格与 1~3 空格缩进的普通文本/图片**全部归入项 N**——不归项则序号必然错乱；
2. **尾项规则**（末项之后、直到块级终点的，无后续项）：
   **只处理 1~3 空格缩进**的内容（作者意图标记），**顶格内容不动**
   （视为有意放在列表外，如章节级注意事项）；
   两种场景下，已被前序 hook 缩到 4 空格的行（如 _fix_image_in_list 的产物）
   均作为运行延续收录（重写为恒等，幂等）。尾项的终止符为列表行时守卫不触发
   ——防止 Python-Markdown 把后续顶层无序列表并入有序列表。

其余刻意不处理：中间含标题/表格/引用/围栏等结构行（围栏另有 _fix_fence_in_list
的夹心规则）、紧贴项行的惰性续行、代码围栏内部。源文件永不修改。
"""

import re

_FENCE_RE = re.compile(r'^\s{0,3}(?:`{3,}|~{3,})')
# 0~4 空格缩进的围栏行（4 空格 = 已归入列表项的代码块，收集时整块穿越）
_FENCE_ANY_RE = re.compile(r'^(\s{0,4})(`{3,}|~{3,})(.*)$')
_ITEM_RE = re.compile(r'^(\d{1,9})([.)])[ \t]+')
_ANY_MARKER_RE = re.compile(r'^(?:[-*+]|\d{1,9}[.)])[ \t]+')
_HEADING_RE = re.compile(r'^#{1,6}\s')


def _safe_content(line):
    """可安全缩进为列表项内容的行：顶格或 1~4 空格缩进的普通文本/图片
    （排除结构行与列表行）。返回 (安全, 缩进空格数)。"""
    core = line.lstrip(' ')
    n = len(line) - len(core)
    if n > 4:
        return False, n
    if _ANY_MARKER_RE.match(core):
        return False, n
    if core.startswith('![') or core.startswith('<img'):
        return True, n
    if not core or core[0] in '#>|<\t':
        return False, n
    return True, n


def on_page_markdown(markdown, page, config, files):
    """在 markdown 解析前，按位置规则把编号项间/末项后的内容归项。"""
    lines = markdown.split('\n')
    result = []
    in_fence = False
    recent_handled = False   # 自上一个块级边界起，本列表是否有项的夹心已被处理
    i = 0
    n_lines = len(lines)

    while i < n_lines:
        line = lines[i]
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            result.append(line)
            i += 1
            continue
        if in_fence:
            result.append(line)
            i += 1
            continue
        # 块级边界（标题）重置链条信号
        if _HEADING_RE.match(line):
            recent_handled = False

        m = _ITEM_RE.match(line)
        handled = False
        if m:
            num, marker = int(m.group(1)), m.group(2)
            # 向后收集：(安全, 缩进) 内容行，直到编号项或不可缩进行
            j = i + 1
            content = []           # [(行号, 缩进空格数)]
            passthrough = set()    # 围栏块行号（原样保留，归 _fix_fence_in_list 域）
            saw_blank = False
            next_is_seq = False
            while j < n_lines:
                cur = lines[j]
                if not cur.strip():
                    saw_blank = True
                    j += 1
                    continue
                if not saw_blank:
                    break  # 紧贴项行的内容=惰性续行，本就归入项内
                m2 = _ITEM_RE.match(cur)
                if m2:
                    next_is_seq = (int(m2.group(1)) == num + 1 and m2.group(2) == marker)
                    break
                fm = _FENCE_ANY_RE.match(cur)
                if fm:
                    # 围栏块（含已归项的 4 空格缩进代码块）：整块穿越后继续收集
                    k2 = j + 1
                    closed = False
                    while k2 < n_lines:
                        cm = _FENCE_ANY_RE.match(lines[k2])
                        if cm and cm.group(2)[0] == fm.group(2)[0] and not cm.group(3).strip():
                            k2 += 1
                            closed = True
                            break
                        k2 += 1
                    if not closed:
                        break  # 未闭合围栏：保守终止，不处理
                    passthrough.update(range(j, k2))
                    j = k2
                    continue
                ok, ind = _safe_content(cur)
                if not ok:
                    break
                content.append((j, ind))
                j += 1

            if next_is_seq and content:
                # 夹心规则：N → 内容(顶格或缩进) → N+1，全部归入项 N
                result.append(line)
                fixed = {k for k, _ in content}
                for k in range(i + 1, j):
                    result.append('    ' + lines[k].lstrip(' ') if k in fixed else lines[k])
                i = j
                recent_handled = True
                handled = True
            elif content and not next_is_seq:
                # 尾项规则：缩进即意图标记，处理首个顶格行之前的 1~3 空格内容
                # （无需冒号/链条信号——那是为顶格内容设计的猜测机制，已废弃）；
                # 终止符为列表行时守卫不触发（防止后续顶层无序列表被并入有序列表）
                term = lines[j] if j < n_lines else ''
                if not _ANY_MARKER_RE.match(term):
                    fixed = set()
                    for k, ind in content:
                        if ind == 0:
                            break  # 首个顶格行起不再归项
                        fixed.add(k)
                    if fixed:
                        result.append(line)
                        for k in range(i + 1, j):
                            result.append('    ' + lines[k].lstrip(' ') if k in fixed else lines[k])
                        i = j
                        handled = True
        if not handled:
            result.append(line)
            i += 1

    return '\n'.join(result)
