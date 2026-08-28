import re


def _iter_lines(markdown):
    """逐行迭代并跟踪当前行是否位于围栏代码块内（``` 或 ~~~）。

    返回 (line, in_code_block)，用于只统计/改写代码块外的 ATX 标题，
    避免把代码块里的 `# 注释` 误判为一级标题。
    """
    in_code = False
    fence_char = None  # '`' 或 '~'，当前代码块使用的围栏字符
    for line in markdown.split('\n'):
        stripped = line.lstrip()
        if not in_code:
            m = re.match(r'^(`{3,}|~{3,})', stripped)
            if m:
                in_code = True
                fence_char = m.group(1)[0]
            yield line, False
        else:
            m = re.match(r'^(`{3,}|~{3,})\s*$', stripped)
            if m and m.group(1)[0] == fence_char:
                in_code = False
                fence_char = None
            yield line, True


def on_page_markdown(markdown, page, config, files):
    """修复标题级别问题，针对IDP导出的文档有多个一级标题的场景。

    仅当代码块之外存在多个一级标题时才触发：将所有标题降一级，
    再以页面标题（通常来自 nav 的 key）作为唯一的一级标题。
    """
    lines_with_state = list(_iter_lines(markdown))

    h1_count = sum(
        1 for line, in_code in lines_with_state
        if not in_code and line.startswith('# ')
    )
    if h1_count <= 1:
        return markdown

    result = []
    for line, in_code in lines_with_state:
        # 代码块内的内容（含 `# 注释`）原样保留
        if in_code or not line.startswith('#'):
            result.append(line)
            continue
        # 代码块外的 ATX 标题整体降一级
        if line.startswith('# '):
            result.append('##' + line[1:])
        elif line.startswith('## '):
            result.append('###' + line[2:])
        elif line.startswith('### '):
            result.append('####' + line[3:])
        elif line.startswith('#### '):
            result.append('#####' + line[4:])
        elif line.startswith('##### '):
            result.append('######' + line[5:])
        else:
            result.append(line)

    new_h1 = page.title if page and page.title else ''
    if not new_h1:
        for line, in_code in lines_with_state:
            if not in_code and line.startswith('# '):
                new_h1 = line[2:].strip()
                break

    if new_h1:
        result.insert(0, '# ' + new_h1)

    return '\n'.join(result)
