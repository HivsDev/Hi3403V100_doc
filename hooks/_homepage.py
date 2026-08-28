"""自动从源页面生成主页 index.md

读取配置的源页面并生成索引页面，同时调整相对路径，使源页面内容作为网站主页提供服务。


示例配置（在 mkdocs.yml 的 extra 部分中）：
```yaml
extra:
  homepage_generator:
    source: zh_CN/HiDiTingV100/overview/README.md   # 必需
    target: zh_CN/index.md                            # 必需
```

单一事实来源：仅编辑源页面；此钩子在每次构建时自动保持主页同步。
"""

import re
from pathlib import Path, PurePosixPath
from os.path import relpath


def _is_external(url):
    """True for absolute URLs, site-root paths, anchors, and data URIs."""
    return bool(re.match(r'^([a-z][a-z0-9+.\-]*://|/|#|mailto:|tel:|data:)', url, re.IGNORECASE))


def _rebase(url, src_dir, dst_dir):
    """Rebase a relative URL so it resolves from dst_dir instead of src_dir."""
    if _is_external(url):
        return url
    # Preserve any trailing query string or fragment across the rebase
    m = re.match(r'^([^?#]*)([?#].*)?$', url)
    path_part, suffix = m.group(1), m.group(2) or ''
    # Resolve url from src_dir, then compute relative path from dst_dir
    resolved = PurePosixPath((src_dir / path_part).as_posix())
    # Normalize (resolve .. and .)
    parts = []
    for p in resolved.parts:
        if p == '..':
            if parts and parts[-1] != '..':
                parts.pop()
            else:
                parts.append(p)
        elif p == '.':
            continue
        else:
            parts.append(p)
    resolved_str = '/'.join(parts) if parts else '.'
    new_path = relpath(resolved_str, str(dst_dir)).replace('\\', '/')
    return new_path + suffix


def _rewrite_relative_links(content, src_rel, dst_rel):
    """Rewrite relative Markdown links/image refs and HTML src/href from src to dst location."""
    src_dir = PurePosixPath(src_rel).parent   # e.g. zh-CN/HiDiTingV100/overview
    dst_dir = PurePosixPath(dst_rel).parent   # e.g. zh-CN

    # --- Markdown links and images: [text](url) / ![alt](url) ---
    # groups: (prefix incl. open paren)(url)(optional "title"/'title')(close paren)
    md_pattern = r'(!?\[[^\]]*\]\()\s*([^\s)]+)(\s+(?:"[^"]*"|\'[^\']*\'))?\s*(\))'

    def _md_replacer(match):
        prefix, url, title, close = match.groups()
        rebuilt = prefix + _rebase(url.strip(), src_dir, dst_dir)
        if title:
            rebuilt += title
        return rebuilt + close

    content = re.sub(md_pattern, _md_replacer, content)

    # --- HTML attributes: <img src="..."> / <a href="..."> ---
    # The Markdown pass above only handles ![](...)/[](...) syntax, so HTML tags
    # such as <img src="..."> need their own rebasing or images break on the homepage.
    html_pattern = r'\b(src|href)\s*=\s*(["\'])(.*?)\2'

    def _html_replacer(match):
        attr, quote, url = match.group(1), match.group(2), match.group(3)
        return f'{attr}={quote}{_rebase(url, src_dir, dst_dir)}{quote}'

    content = re.sub(html_pattern, _html_replacer, content, flags=re.IGNORECASE)

    return content


def on_pre_build(config, **kwargs):
    extra = config.get('extra', {})
    cfg = extra.get('homepage_generator')
    if not cfg:
        return

    src_rel = cfg.get('source')
    dst_rel = cfg.get('target')
    if not src_rel or not dst_rel:
        raise ValueError(
            "[homepage] extra.homepage_generator 配置不完整，source 与 target 均为必需项，"
            f"当前 source={src_rel!r}, target={dst_rel!r}"
        )

    docs_dir = Path(config['docs_dir'])
    src = docs_dir / src_rel
    dst = docs_dir / dst_rel

    if not src.exists():
        raise FileNotFoundError(
            f"[homepage] 主页源文件不存在: {src}\n"
            f"  请检查 mkdocs.yml 中 extra.homepage_generator.source 配置"
            f"（路径应相对 docs_dir，当前配置: {src_rel}）"
        )

    content = src.read_text(encoding='utf-8')
    content = _rewrite_relative_links(content, src_rel, dst_rel)

    # Avoid unnecessary writes that could trigger mkdocs serve rebuild loops
    if dst.exists() and dst.read_text(encoding='utf-8') == content:
        return

    dst.write_text(content, encoding='utf-8')


def on_page_context(context, page, config, **kwargs):
    """Override edit URL for the generated homepage to point to the source file."""
    extra = config.get('extra', {})
    cfg = extra.get('homepage_generator')
    if not cfg:
        return

    # 编辑按钮（content.action.edit）未开启时页面不渲染编辑链接，也无需依赖 repo_url/edit_uri
    features = (config.get('theme') or {}).get('features') or []
    if 'content.action.edit' not in features:
        return

    dst_rel = cfg.get('target')
    src_rel = cfg.get('source')
    if not dst_rel or not src_rel:
        return

    # i18n plugin may prefix locale to the file path; compare the src_path part
    src_path = page.file.src_path.replace('\\', '/')
    if src_path == dst_rel:
        # Rebuild edit URL pointing to the source file
        # 注意: 未配置时 mkdocs 校验后的值为 None 而非缺失键，get 的默认值不生效
        repo_url = (config.get('repo_url') or '').rstrip('/')
        edit_uri = (config.get('edit_uri') or '').rstrip('/')
        if repo_url and edit_uri:
            page.edit_url = f"{repo_url}/{edit_uri}/{src_rel}"
