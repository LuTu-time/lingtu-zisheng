# -*- coding: utf-8 -*-
"""Build a versioned Windows desktop bundle without modifying older releases."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def build(name, conda_bin):
    if not name.isascii() or not name.replace("-", "").isalnum():
        raise ValueError("构建名称只允许英文字母、数字和连字符")
    out = ROOT / "out"
    target = out / name
    archive = out / (name + ".zip")
    if target.exists() or archive.exists():
        raise ValueError("构建产物已存在，请使用新的 --name；不会覆盖旧版本")
    missing = [f for f in ("sqlite3.dll", "ffi.dll", "libexpat.dll", "libbz2.dll",
                           "liblzma.dll", "libmpdec-4.dll") if not (conda_bin / f).is_file()]
    if missing:
        raise ValueError("缺少运行库：" + ", ".join(missing))
    stage = out / ("." + name + "-source")
    if stage.exists():
        raise ValueError("构建源码暂存目录已存在，不会覆盖")
    stage.mkdir()
    for filename in ("desktop.py", "play.py", "run_p0.py"):
        shutil.copy2(ROOT / filename, stage / filename)
    shutil.copytree(ROOT / "engine", stage / "engine", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    command = [sys.executable, "-m", "PyInstaller", "--onedir", "--windowed", "--name", name,
               "--distpath", str(out), "--workpath", str(out / ("." + name + "-build")),
               "--specpath", str(out), "--collect-submodules", "engine",
               "--hidden-import", "webview.platforms.edgechromium"]
    for directory in ("web", "data", "schema"):
        command += ["--add-data", f"{ROOT / directory};{directory}"]
    command += ["--add-data", f"{ROOT / 'platform.py'};.",
                "--add-data", f"{ROOT / 'engine'};engine"]
    for filename in ("sqlite3.dll", "ffi.dll", "libexpat.dll", "libbz2.dll",
                     "liblzma.dll", "libmpdec-4.dll"):
        command += ["--add-binary", f"{conda_bin / filename};."]
    command.append(str(stage / "desktop.py"))
    env = os.environ.copy()
    env["TMP"] = env["TEMP"] = str(out)
    env["PYINSTALLER_CONFIG_DIR"] = str(out / ("." + name + "-config"))
    subprocess.run(command, cwd=stage, env=env, check=True)
    exe = target / (name + ".exe")
    subprocess.run([str(exe), "--check"], cwd=target, check=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for file in target.rglob("*"):
            if file.is_file() and "worlds" not in file.relative_to(target).parts:
                zipped.write(file, file.relative_to(out))
    with zipfile.ZipFile(archive) as zipped:
        if zipped.testzip() is not None:
            raise RuntimeError("交付包完整性校验失败")
    print(f"构建与自检通过：{archive}")


def main():
    parser = argparse.ArgumentParser(description="在 D 盘构建灵途自生 Windows 桌面版")
    parser.add_argument("--name", required=True, help="全新且不重复的英文版本名称")
    parser.add_argument("--conda-bin", default="D:/Anaconda/Library/bin", type=Path)
    args = parser.parse_args()
    build(args.name, args.conda_bin)


if __name__ == "__main__":
    main()
