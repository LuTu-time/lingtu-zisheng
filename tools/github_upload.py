# -*- coding: utf-8 -*-
"""把本地仓库快照通过 **GitHub REST API** 上传（不需要 git 能连上 github.com）。

为什么需要它：在受限网络里，`api.github.com` 可达，而 `github.com:443`（git 的
HTTPS 传输）可能被代理拒绝（CONNECT tunnel failed / Empty reply from server）。
此时常规的 `git push` 无法工作，本脚本改用 Git Data API 直接建 blob → tree →
commit → ref，达到同样的效果。

用法：
    GH_TOKEN=<你的token> python tools/github_upload.py --owner <账号> --repo <仓库名>

说明：
  · Token 只从环境变量读取，绝不写入文件或仓库。
  · 只上传 `.gitignore` 认可的文件（等价于 `git ls-files`），不会夹带存档或构建产物。
  · 建的是 **空树上的一个提交**，因此适合「仓库为空」的首次上传；仓库已有提交时会
    以 force 方式更新 main（请自行确认）。
"""
import argparse
import base64
import json
import os
import subprocess
import sys

API = "https://api.github.com"


def api(method, path, token, payload=None, timeout=120):
    """用 curl 调 API —— 复用系统里已验证可用的 TLS/代理路径（Python 直连不一定通）。"""
    cmd = ["curl", "-s", "--ssl-no-revoke", "--max-time", str(timeout), "-X", method,
           "-H", "Authorization: Bearer " + token,
           "-H", "Accept: application/vnd.github+json",
           "-H", "Content-Type: application/json",
           API + path]
    if payload is not None:
        cmd += ["--data-binary", "@-"]
        proc = subprocess.run(cmd, input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              capture_output=True)
    else:
        proc = subprocess.run(cmd, capture_output=True)
    raw = proc.stdout.decode("utf-8", "replace")
    try:
        return json.loads(raw or "{}")
    except ValueError:
        raise SystemExit(f"API 返回无法解析（{method} {path}）：{raw[:300]}")


def ensure_non_empty(owner, repo, token, branch):
    """空仓库（无任何提交）不能用 Git Data API，会返回 "Git Repository is empty."。
    先用 Contents API 写一个占位文件，制造首个提交，使 blob/tree/commit 可用。"""
    probe = api("GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}", token)
    if "object" in probe:
        return False
    placeholder = {"message": "chore: 初始化仓库", "branch": branch,
                   "content": base64.b64encode(b"placeholder\n").decode("ascii")}
    res = api("PUT", f"/repos/{owner}/{repo}/contents/.keep", token, placeholder)
    if "commit" not in res:
        raise SystemExit(f"激活空仓库失败：{res.get('message')}")
    print("（仓库原本为空，已写占位提交以启用 Git Data API）")
    return True


def current_head(owner, repo, branch, token):
    """取分支当前提交 sha；分支不存在返回 None。"""
    res = api("GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}", token)
    obj = res.get("object") if isinstance(res, dict) else None
    return obj.get("sha") if isinstance(obj, dict) else None


def tracked_files():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return [line for line in out.stdout.splitlines() if line.strip()]


def main():
    ap = argparse.ArgumentParser(description="通过 GitHub API 上传仓库快照")
    ap.add_argument("--owner", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--message", default="灵途自生 v1：自演化小说世界引擎 + 本地写作平台（P0–P3，107/107 模块，零第三方依赖）")
    a = ap.parse_args()

    token = os.environ.get("GH_TOKEN")
    if not token:
        raise SystemExit("请先设置环境变量 GH_TOKEN")

    files = tracked_files()
    if not files:
        raise SystemExit("没有可上传的文件（git ls-files 为空）")
    ensure_non_empty(a.owner, a.repo, token, a.branch)
    print(f"共 {len(files)} 个文件，开始上传 blob…")

    entries, total = [], 0
    for path in files:
        with open(path, "rb") as stream:
            data = stream.read()
        total += len(data)
        blob = api("POST", f"/repos/{a.owner}/{a.repo}/git/blobs", token,
                   {"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"})
        if "sha" not in blob:
            raise SystemExit(f"创建 blob 失败（{path}）：{blob.get('message')}")
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        print(f"  · {path}  ({len(data)} B)")

    print(f"共 {total/1024:.0f} KB，创建 tree…")
    tree = api("POST", f"/repos/{a.owner}/{a.repo}/git/trees", token, {"tree": entries})
    if "sha" not in tree:
        raise SystemExit(f"创建 tree 失败：{tree.get('message')}")

    head = current_head(a.owner, a.repo, a.branch, token)
    print("创建 commit…" + ("（接在现有提交之后）" if head else "（首个根提交）"))
    commit = api("POST", f"/repos/{a.owner}/{a.repo}/git/commits", token,
                 {"message": a.message, "tree": tree["sha"],
                  "parents": [head] if head else []})
    if "sha" not in commit:
        raise SystemExit(f"创建 commit 失败：{commit.get('message')}")

    print(f"指向分支 {a.branch}…")
    ref_path = f"/repos/{a.owner}/{a.repo}/git/refs"
    ref = api("POST", ref_path, token, {"ref": f"refs/heads/{a.branch}", "sha": commit["sha"]})
    if "ref" not in ref:
        # 分支已存在 → 常规快进更新（父提交就是当前 head）；万一失败再退让为 force
        ref = api("PATCH", f"{ref_path}/heads/{a.branch}", token,
                  {"sha": commit["sha"], "force": False})
        if "ref" not in ref:
            ref = api("PATCH", f"{ref_path}/heads/{a.branch}", token,
                      {"sha": commit["sha"], "force": True})
        if "ref" not in ref:
            raise SystemExit(f"创建/更新分支失败：{ref.get('message')}")

    api("PATCH", f"/repos/{a.owner}/{a.repo}", token, {"default_branch": a.branch})
    print("\n完成。仓库地址：https://github.com/{}/{}".format(a.owner, a.repo))
    print("提交：", commit["sha"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
