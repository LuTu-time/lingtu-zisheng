# -*- coding: utf-8 -*-
"""叙事模型后端的验收测试（离线；用本机 mock 服务模拟两种后端）。

核验：
  1. 默认本机后端、模型不可用时优雅失败（ModelUnavailable → 平台 503）。
  2. 本机后端只接受 127.0.0.1；把地址指向外网会被拒（资料不出本机）。
  3. 本机后端（Ollama 形状）可用时能生成文本。
  4. 云端后端未配置时报「未配置」且不可用；generate 抛 ModelUnavailable。
  5. 云端后端（OpenAI 兼容形状）配置后能生成文本，且带上 Bearer Key。
  6. 未知后端名一律拒绝。
"""
import importlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import local_llm      # noqa: E402

PASS = []
FAIL = []


def check(cond, label):
    (PASS if cond else FAIL).append(label)
    print(("PASS: " if cond else "FAIL: ") + label, flush=True)


class _Handler(BaseHTTPRequestHandler):
    kind = "ollama"

    def log_message(self, *args):
        pass

    def _json(self, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.kind == "ollama" and self.path == "/api/tags":
            return self._json({"models": [{"name": "qwen2.5:7b"}]})
        if self.kind == "cloud" and self.path == "/models":
            return self._json({"data": [{"id": "test-model"}]})
        self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        if self.kind == "ollama" and self.path == "/api/chat":
            return self._json({"message": {"content": "本机模型写的草稿。"}})
        if self.kind == "cloud" and self.path == "/chat/completions":
            auth = self.headers.get("Authorization", "")
            return self._json({"choices": [{"message": {"content": "云端草稿 " + auth}}]})
        self.send_error(404)


def _serve(kind):
    handler = type("H", (_Handler,), {"kind": kind})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def reload_llm(**env):
    for key in ("LINGTU_LLM_BACKEND", "LINGTU_LLM_URL", "LINGTU_LLM_MODEL",
                "LINGTU_CLOUD_URL", "LINGTU_CLOUD_KEY", "LINGTU_CLOUD_MODEL"):
        if key in env:
            if env[key] is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = env[key]
    importlib.reload(local_llm)


def expect_unavailable(label, **env):
    reload_llm(**env)
    try:
        local_llm.generate("系统", "资料", "请求")
        check(False, label + "（本应失败）")
    except local_llm.ModelUnavailable:
        check(True, label)


def main():
    print("=" * 64)
    print("叙事模型后端验收测试（离线 mock）")
    print("=" * 64)

    # 1 默认本机后端、离线
    reload_llm(LINGTU_LLM_BACKEND="local", LINGTU_LLM_URL="http://127.0.0.1:9", LINGTU_LLM_MODEL="qwen2.5:7b")
    st = local_llm.status()
    check(st["backend"] == "local" and st["available"] is False and "不出本机" in st.get("note", ""),
          "默认后端=本机，离线时 status 报不可用")
    expect_unavailable("本机后端离线：generate 抛 ModelUnavailable（平台 503）")

    # 2 本机后端拒绝外网地址（隐私）
    expect_unavailable("本机后端指向外网被拒（资料不出本机）",
                       LINGTU_LLM_BACKEND="local", LINGTU_LLM_URL="https://api.example.com")

    # 3 本机后端可用
    server, port = _serve("ollama")
    try:
        reload_llm(LINGTU_LLM_BACKEND="local", LINGTU_LLM_URL=f"http://127.0.0.1:{port}",
                   LINGTU_LLM_MODEL="qwen2.5:7b")
        check(local_llm.status()["available"] is True, "本机后端可用时 status 报可用")
        text = local_llm.generate("系统", "资料", "请求")
        check("本机模型写的草稿" in text, "本机后端（Ollama 形状）可生成文本")
    finally:
        server.shutdown()

    # 4 云端未配置
    reload_llm(LINGTU_LLM_BACKEND="cloud", LINGTU_CLOUD_URL=None, LINGTU_CLOUD_KEY=None, LINGTU_CLOUD_MODEL=None)
    st = local_llm.status()
    check(st["backend"] == "cloud" and st["available"] is False and "未配置" in st.get("reason", ""),
          "云端未配置：status 明确报未配置")
    expect_unavailable("云端未配置：generate 抛 ModelUnavailable",
                       LINGTU_LLM_BACKEND="cloud", LINGTU_CLOUD_URL=None,
                       LINGTU_CLOUD_KEY=None, LINGTU_CLOUD_MODEL=None)

    # 5 云端可用（OpenAI 兼容形状）
    server, port = _serve("cloud")
    try:
        reload_llm(LINGTU_LLM_BACKEND="cloud", LINGTU_CLOUD_URL=f"http://127.0.0.1:{port}",
                   LINGTU_CLOUD_KEY="test-key", LINGTU_CLOUD_MODEL="test-model")
        st = local_llm.status()
        check(st["backend"] == "cloud" and st["available"] is True and st["model"] == "test-model",
              "云端后端配置后 status 报可用")
        text = local_llm.generate("系统", "资料", "请求")
        check("云端草稿" in text and "test-key" in text, "云端后端（OpenAI 形状）生成文本并携带 Bearer Key")
        check(local_llm.active_model() == "test-model", "active_model 随后端切换")
    finally:
        server.shutdown()

    # 6 未知后端
    expect_unavailable("未知后端名被拒", LINGTU_LLM_BACKEND="banana")

    print("-" * 64)
    print(f"通过 {len(PASS)}，失败 {len(FAIL)}")
    if FAIL:
        for f in FAIL:
            print("  ✗ " + f)
        return 1
    print("模型后端验收通过：本机默认、云端可选、隐私边界与优雅失败均成立。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
