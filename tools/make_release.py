# -*- coding: utf-8 -*-
"""把《灵途自生》打成「别人下载即可运行」的分发包。

产出（dist/ 目录）：
  灵途自生-桌面版-Windows-<tag>/  + .zip   免安装桌面程序（Windows 10/11 x64，无需 Python）
  灵途自生-源码版-<tag>/          + .zip   纯源码（零第三方依赖，需 Python 3.10+）
  SHA256SUMS.txt                            两个 zip 的校验值

用法：
  python tools/make_release.py                      # 用 out/ 下最新的桌面构建
  python tools/make_release.py --desktop out/LingtuDesktop-Cast-20261016
  python tools/make_release.py --tag 20261016
"""
import argparse
import hashlib
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
SOURCE_ENTRIES = ["engine", "tools", "data", "schema", "web",
                  "play.py", "platform.py", "desktop.py", "run_p0.py", "README.md"]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".git", ".workbuddy", "worlds", "db")

USAGE_DESKTOP = """灵途自生 · 桌面版（Windows）使用说明
================================================

【这是什么】
一个「小说世界的推演与写作」桌面工具：一个由生态、经济、社会自发演化的
世外小村，历史逐条记录且不可改写；你可以作为作者进入这个世界、观察它、
干预它，并把发生过的事写成可反复修改的章节草稿。

【怎么运行】
1. 把整个压缩包解压到一个**可写目录**（例如「文档」「桌面」下自建一个文件夹）。
   —— 不要解压到 C:\\Program Files 等需要管理员权限的位置，程序要在旁边写数据。
2. 双击「灵途自生.exe」。
3. 第一次启动会在程序旁边新建一个世界（worlds\\desktop.db），随后弹出应用窗口。
   之后每次启动都会继续使用同一个世界。

【三个页面】
  入世        —— 你以真实角色进入世界（填姓名/身份/目标），可拜访、交谈、帮工。
  小说写作台  —— 选世界事件写成章节草稿（可反复改）、导出全书、导入 TXT 设定与人物。
  世界观察台  —— 看状态、推进时间、作者干预、时间线存档/快照分支、状态封印。

【数据和备份】
· 世界数据全在程序旁边的 worlds\\ 文件夹里。**整份复制 worlds\\ 就是完整备份**。
· 关闭应用窗口，本机的服务也随之停止；程序只监听 127.0.0.1，不上传任何数据。

【可选：让「AI 写草稿 / 村民对话」可用】
默认不需要任何模型也能用（写作台的「生成草稿」在无模型时会提示不可用，其他功能不受影响）。
· 本机模型：安装 Ollama（https://ollama.com）后执行 `ollama pull qwen2.5:7b`，
  保持 Ollama 运行即可，世界资料不会离开本机。
· 云端模型（可选）：设置环境变量后启动，改由云端生成（资料会发往该地址）：
    set LINGTU_LLM_BACKEND=cloud
    set LINGTU_CLOUD_URL=https://api.siliconflow.cn/v1
    set LINGTU_CLOUD_KEY=你的Key
    set LINGTU_CLOUD_MODEL=Qwen/Qwen2.5-7B-Instruct

【系统要求】
· Windows 10 / 11，64 位。
· 需要「Microsoft Edge WebView2 运行时」。Win11 与多数更新过的 Win10 已自带；
  若窗口打不开，请到微软官网搜索下载「WebView2 Runtime（Evergreen）」安装后重试。

【常见问题】
· 杀毒软件报毒：PyInstaller 打包的程序常被误报，可加入信任/白名单后再运行。
· 双击没反应：确认解压到了可写目录；或先在命令行执行
    「灵途自生.exe --check」查看自检输出。
· 想换一个新世界：关掉程序，把 worlds\\desktop.db 改名或移走，再启动即可新建。

【授权】
本项目采用 MIT 许可证（见包内 LICENSE 或仓库根目录 LICENSE）：可自由使用、修改、分发与商用，
只需保留版权与许可声明。软件按「原样」提供，不附带任何担保。
"""

USAGE_SOURCE = """灵途自生 · 源码版使用说明
================================================

【这是什么】
《灵途自生》的完整源码：一个自演化的「小说世界」引擎 + 本地写作平台。
纯标准库实现，**零第三方依赖**，因此体积小、跨平台、容易审计与二次开发。

【运行环境】
· Python 3.10 或更高（安装时请勾选 “Add Python to PATH”）。
· 不需要 pip 安装任何东西。

【怎么运行（推荐：浏览器版）】
· Windows：双击「启动-Windows.bat」
· macOS / Linux：在终端执行  sh 启动-macOS-Linux.sh
  脚本会自动创建世界（首次）并打开浏览器；关闭终端即停止服务。
· 手动等价命令：
    python platform.py --db db/play.db --new-world --open   # 首次
    python platform.py --db db/play.db --open               # 以后

【想要独立窗口（可选）】
    pip install pywebview
    python desktop.py

【一键自检 / 全部验收测试】
    python tools/run_all.py --quick

【可选：让「AI 写草稿 / 村民对话」可用】
· 本机模型：安装 Ollama，`ollama pull qwen2.5:7b`，保持运行。
· 云端模型（可选，资料会发往该地址）：
    LINGTU_LLM_BACKEND=cloud LINGTU_CLOUD_URL=... LINGTU_CLOUD_KEY=... LINGTU_CLOUD_MODEL=... python platform.py

【数据在哪】
世界存档在 db/ 下（默认 db/play.db）；写作台章节、设定、快照也都在同一份 SQLite 里。
整份复制 db/ 即完成备份。程序只监听 127.0.0.1，不联网上传。

【授权】
本项目采用 MIT 许可证：可自由使用、修改、分发与商用，只需保留版权与许可声明。
"""

LAUNCH_BAT = """@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo 未检测到 Python。请先安装 Python 3.10+ 并勾选 Add Python to PATH。
  pause
  exit /b 1
)
set EXTRA=
if not exist "db\\play.db" set EXTRA=--new-world
python platform.py --db db/play.db %EXTRA% --open
pause
"""

LAUNCH_SH = """#!/usr/bin/env bash
cd "$(dirname "$0")"
PY=python3
command -v $PY >/dev/null 2>&1 || PY=python
command -v $PY >/dev/null 2>&1 || { echo "未检测到 Python 3.10+，请先安装。"; exit 1; }
EXTRA=""
[ -f db/play.db ] || EXTRA="--new-world"
exec $PY platform.py --db db/play.db $EXTRA --open
"""


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_text(path, text):
    # 纯文本说明里不保留 Markdown 粗体标记，Notepad 里更干净。
    path.write_bytes(text.replace("**", "").replace("\n", "\r\n").encode("utf-8"))


def _zip_dir(src, zip_path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for item in sorted(src.rglob("*")):
            if item.is_file():
                archive.write(item, item.relative_to(src.parent))


def build_desktop(tag, desktop_src):
    exes = sorted(desktop_src.glob("*.exe"))
    if len(exes) != 1 or not (desktop_src / "_internal").is_dir():
        raise SystemExit(f"桌面构建不完整（需一个 .exe 与 _internal）：{desktop_src}")
    exe = exes[0]
    dest = DIST / f"灵途自生-桌面版-Windows-{tag}"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copytree(desktop_src / "_internal", dest / "_internal",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy2(exe, dest / "灵途自生.exe")
    _write_text(dest / "使用说明.txt", USAGE_DESKTOP)
    zip_path = DIST / f"灵途自生-桌面版-Windows-{tag}.zip"
    if zip_path.exists():
        zip_path.unlink()
    _zip_dir(dest, zip_path)
    return dest, zip_path


def build_source(tag):
    dest = DIST / f"灵途自生-源码版-{tag}"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    for entry in SOURCE_ENTRIES:
        src = ROOT / entry
        if not src.exists():
            continue
        target = dest / entry
        if src.is_dir():
            shutil.copytree(src, target, ignore=IGNORE)
        else:
            shutil.copy2(src, target)
    _write_text(dest / "使用说明.txt", USAGE_SOURCE)
    _write_text(dest / "启动-Windows.bat", LAUNCH_BAT)
    (dest / "启动-macOS-Linux.sh").write_bytes(LAUNCH_SH.encode("utf-8"))
    zip_path = DIST / f"灵途自生-源码版-{tag}.zip"
    if zip_path.exists():
        zip_path.unlink()
    _zip_dir(dest, zip_path)
    return dest, zip_path


def main():
    ap = argparse.ArgumentParser(description="打包《灵途自生》分发包")
    ap.add_argument("--tag", default="20261016")
    ap.add_argument("--desktop", default=None,
                    help="桌面构建目录；缺省自动取 out/ 下最新的一个")
    a = ap.parse_args()

    DIST.mkdir(exist_ok=True)
    if a.desktop:
        desktop_src = (ROOT / a.desktop).resolve()
    else:
        candidates = [d for d in (ROOT / "out").glob("LingtuDesktop-*")
                      if d.is_dir() and (d / "_internal").is_dir() and list(d.glob("*.exe"))]
        if not candidates:
            raise SystemExit("out/ 下没有可用的桌面构建；先运行 tools/build_desktop.py")
        desktop_src = max(candidates, key=lambda d: d.stat().st_mtime)
    d_dir, d_zip = build_desktop(a.tag, desktop_src)
    s_dir, s_zip = build_source(a.tag)

    sums = DIST / "SHA256SUMS.txt"
    lines = [f"{_sha256(p)}  {p.name}" for p in (d_zip, s_zip)]
    sums.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))

    print("分发包已生成：")
    print(f"  桌面版：{d_zip}  ({d_zip.stat().st_size / 1048576:.1f} MB)")
    print(f"  源码版：{s_zip}  ({s_zip.stat().st_size / 1024:.0f} KB)")
    print(f"  校验：  {sums}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
