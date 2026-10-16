# -*- coding: utf-8 -*-
"""Windows desktop window around the local world platform."""
import argparse
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sys
import threading
from urllib.request import urlopen
from http.server import ThreadingHTTPServer

import play
from engine import branches

_platform_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "platform.py"
_spec = importlib.util.spec_from_file_location("lingtu_platform", _platform_path)
world_platform = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(world_platform)


def paths():
    resource_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    app_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else resource_root
    return resource_root, app_root


def launch(db_path=None, check=False):
    resources, app_root = paths()
    world_dir = app_root / "worlds"
    world_dir.mkdir(exist_ok=True)
    db = Path(db_path).resolve() if db_path else world_dir / "desktop.db"
    world_platform.WEB_ROOT = str(resources / "web")
    branches._DB_DIR = world_dir
    if not db.is_file():
        if db_path is not None:
            raise ValueError("指定的存档不存在，不会自动重建")
        # play.create resolves schema.sql against play.ROOT.
        play.ROOT = str(resources)
        play.create(str(db), 20261015, "lishi", None)
    store, _, _ = play.open_world(str(db))
    store.close()
    server = ThreadingHTTPServer(("127.0.0.1", 0), world_platform.Handler)
    server.db_path = str(db)
    server.root_db_path = str(db)
    worker = threading.Thread(target=server.serve_forever, name="lingtu-http", daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{server.server_port}/"
    try:
        if check:
            with closing(urlopen(url + "api/status", timeout=5)) as response:
                status = json.load(response)
            if not status["history_ok"]:
                raise RuntimeError("历史链校验未通过")
            for route, marker in (("", "成为世界中的一个人"),
                                  ("write", "小说写作工作台"),
                                  ("simulation", "世界观察台")):
                with closing(urlopen(url + route, timeout=5)) as response:
                    if marker not in response.read().decode("utf-8"):
                        raise RuntimeError(f"页面校验失败：/{route}")
            with closing(urlopen(url + "api/snapshots", timeout=5)) as response:
                archive = json.load(response)
            if archive["current_db"] != str(db):
                raise RuntimeError("快照接口使用了错误存档")
            print(json.dumps({"url": url, "db": str(db), "world": status["世界"],
                              "history_ok": True, "pages_ok": True}, ensure_ascii=False))
            return 0
        import webview
        webview.create_window("灵途自生", url, width=1280, height=850, min_size=(860, 620))
        webview.start(gui="edgechromium", debug=False, private_mode=True)
        return 0
    finally:
        server.shutdown()
        worker.join(timeout=5)
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="灵途自生 Windows 桌面版")
    parser.add_argument("--db", help="指定已存在的世界存档；默认使用 worlds/desktop.db")
    parser.add_argument("--check", action="store_true", help="只进行本机服务自检，不打开窗口")
    args = parser.parse_args()
    try:
        return launch(args.db, args.check)
    except Exception as exc:
        print(f"桌面版启动失败：{exc}", file=sys.stderr)
        if not args.check:
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(0, str(exc), "灵途自生启动失败", 0x10)
            except Exception:
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
