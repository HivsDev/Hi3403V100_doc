"""下载按钮：链接拼接 + 构建产物打包 zip

on_config 计算压缩包文件名并注入模板上下文（供 header 下载按钮使用），
on_post_build 将构建产物打包为 zip 放到构建目录根，保证链接与压缩包名称一致。

文件名优先级（与 build.sh 的 version 取值逻辑一致，tag 优先）：
1. 环境变量 SOURCE_NAME + GIT_TAG   → $SOURCE_NAME-$GIT_TAG.zip
2. 环境变量 SOURCE_NAME + codeBranch → $SOURCE_NAME-$codeBranch.zip
3. extra.download.filename（本地构建默认值）
4. extra.version_selector.repo_path + version 推导（如 hs-fbb-master.zip）

链接规则：配置了 url → url/filename 绝对外链；未配置 url → filename（站点根
相对路径，经模板 | url 过滤器按页面深度自动换算，指向本站根目录下的 zip）。


示例配置（在 mkdocs.yml 的 extra 部分中）：
```yaml
extra:
  download:
    filename: hs-fbb-master.zip   # 必需，本地构建默认压缩包名（CI 中被环境变量覆盖）
    tooltip: 下载HTML              # 可选，按钮悬浮提示
    # url: https://docs.hisilicon.com/projects/downloads  # 可选，设置则链接指向 url/filename
```
"""

import os
import sys
import zipfile
from pathlib import Path


def _sanitize(name):
    """仅去掉路径分隔符，防止文件名逃出构建目录。"""
    return name.replace('\\', '/').rsplit('/', 1)[-1].strip()


def _resolve_filename(config):
    """按环境变量 → extra.download.filename → version_selector 推导的顺序取压缩包名。"""
    source = os.environ.get('SOURCE_NAME', '').strip()
    tag = os.environ.get('GIT_TAG', '').strip()
    branch = os.environ.get('codeBranch', '').strip()

    if source and tag:
        return f"{source}-{tag}.zip", 'env GIT_TAG'
    if source and branch:
        return f"{source}-{branch}.zip", 'env codeBranch'

    extra = config.get('extra', {})
    dl = extra.get('download') or {}
    configured = str(dl.get('filename', '')).strip()
    if configured:
        return _sanitize(configured), 'extra.download.filename'

    selector = extra.get('version_selector') or {}
    repo = _sanitize(str(selector.get('repo_path', '')))
    version = _sanitize(str(selector.get('version', '')))
    if repo and version:
        return f"{repo}-{version}.zip", 'extra.version_selector'

    return '', None


def on_config(config, **kwargs):
    extra = config.get('extra', {})
    dl = extra.get('download')
    if not dl:
        return config

    filename, source = _resolve_filename(config)
    if not filename:
        print("[download] 无法确定压缩包文件名（无环境变量/extra.download.filename/version_selector），跳过")
        return config

    url = str(dl.get('url', '')).strip().rstrip('/')
    dl['filename'] = filename
    dl['href'] = f"{url}/{filename}" if url else filename

    print(f"[download] 压缩包名: {filename} (来源: {source})，下载链接: {dl['href']}")
    return config


def on_post_build(config, **kwargs):
    dl = config.get('extra', {}).get('download')
    if not dl or not dl.get('filename'):
        return

    # mkdocs serve 每次热重建都会触发 on_post_build，跳过以免反复重新压缩
    if 'serve' in sys.argv[1:]:
        return

    site_dir = Path(config['site_dir'])
    filename = dl['filename']
    zip_path = site_dir / filename

    # 先枚举文件快照再打包，并在临时文件写完后原子替换，确保 zip 不会把自己打进去
    files = [
        p for p in site_dir.rglob('*')
        if p.is_file()
        and p != zip_path
        and p.suffix != '.zip'
        and '__pycache__' not in p.parts
    ]

    tmp_path = site_dir / f".{filename}.tmp"
    with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.relative_to(site_dir).as_posix())
    os.replace(tmp_path, zip_path)

    size_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"[download] 构建产物已打包: {zip_path} ({len(files)} 个文件, {size_mb:.1f} MB)")
