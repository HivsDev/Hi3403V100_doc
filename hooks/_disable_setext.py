"""禁用 Python-Markdown 的 setext 标题解析。

背景：Python-Markdown（mkdocs-material 的底层引擎）默认开启 setext 标题规则。
当一行 ``---`` 或 ``===`` 紧贴在一段非空正文下方（中间没有空行）时，正文会被
解析成 ``<h2>`` / ``<h1>``，``---`` 被吞掉，TOC / 锚点 ID 全部跟着错乱。

示例（这是 bug，不是预期效果）::

    应通过运行时内存统计确认实际使用量。
    ---

渲染成::

    <h2 id="...">应通过运行时内存统计确认实际使用量。</h2>

本扩展直接从 block processor 注册表中移除 ``setextheader``，让 ``---`` / ``===``
只能渲染成水平线（``<hr/>``）或字面文本。站点所有标题改由 ATX 形式（``#`` ~ ``######``）
表达 —— 本仓库现有文档已全部使用 ATX（0 处 setext），禁用不会破坏任何既有页面。

注册方式：MkDocs 的 markdown_extensions 通过 Python 模块导入加载扩展，
而 hooks 目录不在 Python 路径上，无法直接用 ``hooks._disable_setext:_NoSetextExtension``
注册。因此改为通过 ``hooks:`` 列表注册（文件路径加载），在 ``on_config`` 事件中
将扩展实例动态追加到 ``markdown_extensions`` 配置中，绕过模块导入限制。
"""

from markdown.extensions import Extension


class _NoSetextExtension(Extension):
    """移除 setextheader block processor 的 Markdown 扩展。"""

    def extendMarkdown(self, md):
        # extendMarkdown 被调用时，所有内置 block processor 都已注册。
        # 直接按注册名 'setextheader' 移除（python-markdown >= 3.0）。
        try:
            md.parser.blockprocessors.deregister("setextheader")
        except ValueError:
            # 旧版本（< 3.0）使用 'setexth'，做一次兜底。
            try:
                md.parser.blockprocessors.deregister("setexth")
            except ValueError:
                pass


def on_config(config, **kwargs):
    """在 MkDocs 配置阶段，将 _NoSetextExtension 实例追加到 markdown_extensions。

    MkDocs 在 on_config 之后才创建 Markdown 转换器，因此此时追加的扩展会被加载。
    用实例而非字符串注册，避免了 "No module named 'hooks'" 的导入问题。
    """
    config["markdown_extensions"].append(_NoSetextExtension())
    return config
