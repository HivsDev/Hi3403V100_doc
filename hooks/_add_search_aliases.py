"""
搜索别名注入 hook
=================
为 API 标识符类标题（如 ``wifi_sta_connect``、``wifi_sta_config_stru``）追加可检索的
别名文本，提升片段级、简称级查询的命中率与排序权重。

背景
----
mkdocs-material 的 search 插件（lunr）默认 separator 为 ``[\\s\\-]+``，本项目已将其
扩展为 ``[\\s\\-_./]+``，使 ``wifi_sta_connect`` 被切分为 ``wifi`` / ``sta`` / ``connect``
三个 token。但用户常以**无前缀片段**（如 ``sta_connect``）查询，此时切分得到
``sta`` + ``connect``，虽可命中但 ranking 偏低，且无法体现"连续片段"语义。

本 hook 在 ``on_page_content`` 阶段（HTML 已渲染、索引尚未构建）扫描页面内的各级
标题（h1~h6），对命中的标识符类标题，在其**紧邻位置**注入一段对人不可见但对索引
可见的别名文本，从而：
1. 让 ``sta_connect`` 这类去前缀片段作为文本出现，获得更高权重；
2. 让别名紧贴对应标题段落，定位更精确。

实现采用视觉隐藏容器（sr-only 风格），确保页面渲染不受影响。
"""

import re

# 匹配 HTML 标题标签（h1~h6），捕获标题内部文本
_HEADING_RE = re.compile(r"<(h[1-6])[^>]*>(.*?)</\1>", re.IGNORECASE | re.DOTALL)

# 匹配标题纯文本中的代码标识符：小写字母/数字，含至少一个下划线段，长度 ≥ 5
# 例：wifi_sta_connect、wifi_sta_config_stru、uapi_wifi_set_tpc_mode
_IDENT_RE = re.compile(r"(?<![\w])([a-z][a-z0-9]*(?:_[a-z0-9]+)+)(?![\w])")

# 仅处理这些前缀的标识符，避免对普通英文句子过度切分
_IDENT_PREFIXES = (
    "wifi_", "sta_", "ap_", "sle_", "ble_", "osal_", "uapi_",
    "errcode_", "pinctrl_", "gpio_", "i2c_", "spi_", "uart_", "pwm_",
    "adc_", "dma_", "ipcm_", "lowio_", "clock_", "reg_", "sys_",
    "tcpip_", "net_", "http_", "mqtt_", "lwip_", "socket_",
    "hal_", "drv_", "sec_",
)


def _strip_tags(html_text):
    """剥离 HTML 标签，返回纯文本（用于标题文本分析）。"""
    return re.sub(r"<[^>]+>", "", html_text)


def _extract_aliases(text):
    """从纯文本中提取有意义的别名片段。

    对 ``wifi_sta_connect`` 产出：
      - ``wifi_sta_connect``  （整体）
      - ``sta_connect``        （去掉首段命名空间前缀）
      - ``wifi sta connect``   （空格分词形式，辅助 ranking）
    """
    aliases = set()
    for ident in _IDENT_RE.findall(text.lower()):
        if not ident.startswith(_IDENT_PREFIXES):
            continue
        aliases.add(ident)
        parts = ident.split("_")
        # 去掉首个命名空间段，得到"去前缀片段"：wifi_sta_connect -> sta_connect
        if len(parts) >= 3:
            aliases.add("_".join(parts[1:]))
        # 空格分词形式，辅助 lunr 对空格切分查询的命中
        aliases.add(" ".join(parts))
    return aliases


def _build_alias_block(aliases, original_title):
    """构造视觉隐藏的别名段落 HTML。"""
    alias_text = " ".join(sorted(aliases))
    return (
        '<span class="search-alias" aria-hidden="true" '
        'style="position:absolute;width:1px;height:1px;padding:0;margin:-1px;'
        'overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0">'
        f"<!-- search aliases: {original_title} -->"
        f"{alias_text}"
        "</span>"
    )


def on_page_content(html, page, config, files):
    """在页面 HTML 渲染后、搜索索引构建前，为标识符类标题注入别名。

    扫描所有 h1~h6 标题，对每个命中的标识符标题，在其**结尾标签后**插入别名块。
    这样别名文本会进入该标题对应的索引 section，获得与标题邻近的定位。
    """
    # 收集所有需要注入的位置，避免在迭代中修改字符串导致偏移
    injections = []  # [(end_pos, alias_block), ...]
    seen_aliases = set()

    for m in _HEADING_RE.finditer(html):
        heading_html = m.group(2)
        plain = _strip_tags(heading_html)
        aliases = _extract_aliases(plain)
        if not aliases:
            continue
        # 跨标题去重，避免同一别名重复注入多次
        new_aliases = aliases - seen_aliases
        if not new_aliases:
            continue
        seen_aliases |= new_aliases
        block = _build_alias_block(new_aliases, plain.strip())
        # 在标题结束标签（m.end()）之后插入
        injections.append((m.end(), block))

    if not injections:
        return html

    # 从后往前插入，避免位置偏移
    parts = []
    last = len(html)
    for pos, block in sorted(injections, key=lambda x: x[0], reverse=True):
        parts.append(html[pos:last])
        parts.append(block)
        last = pos
    parts.append(html[:last])
    return "".join(reversed(parts))
