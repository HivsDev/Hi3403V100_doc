# 文档构建流程

## 更新 tools 子模块

> 需要有tools仓库的权限，才能更新submodules

### 一键更新

```bash
sync_tool.bat
```

### 手动更新
```bash
git submodule update --init --recursive --remote
```

## 同步 tools 内的文件

```bash
python tools/sync_tool.py
```

## 环境搭建

创建python的虚拟环境，安装依赖：
```bash
python -m venv venv
# Linux/macOS
source venv/bin/activate
# Windows (PowerShell)
venv\Scripts\Activate.ps1
# Windows (CMD)
venv\Scripts\activate.bat
pip install -r requirements.txt
```

## 构建文档

```bash
mkdocs build
```
