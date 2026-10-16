"""P3 隔离集成测试；保留 D 盘全部测试产物，不删除文件。"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine import branches, sim
from play import create, load_ids, open_world


def dump(path):
    with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as con:
        return list(con.iterdump())


def refused(call):
    try:
        call()
    except (ValueError, sqlite3.DatabaseError, FileExistsError):
        return
    raise AssertionError("应拒绝的操作却宣称成功")


def main():
    directory = Path(tempfile.mkdtemp(prefix="lingtu-p3-", dir="D:/Projects"))
    # 仅测试时替换存储根，生产 API 始终固定在项目 db 目录。
    branches._DB_DIR = directory / "db"
    source = directory / "source.db"
    other = directory / "other.db"
    create(str(source), 20261015, "lishi", "快照测试")
    create(str(other), 20261015, "lishi", "另一个世界")
    baseline = dump(source)
    checks = 0

    def passed(message):
        nonlocal checks
        checks += 1
        print("PASS:", message)

    snap = branches.create_snapshot(source, "同名")
    assert dump(source) == baseline and dump(snap["path"]) == baseline
    assert Path(snap["path"]).parent == directory / "db" / "snapshots"
    assert snap["sha256"] == branches._hash(snap["path"])
    passed("sqlite backup 快照与源库 iterdump 一致，源库不变")

    listed = branches.list_snapshots(source)
    assert len(listed) == 1 and listed[0]["id"] == snap["id"]
    assert branches.list_snapshots(other) == []
    refused(lambda: branches.create_branch(other, snap["id"], "跨源"))
    passed("manifest 条目绑定源绝对路径，跨源使用拒绝")

    for invalid in ("../source", "D:/outside", "", "A" * 32, "0" * 31, None, "0" * 32):
        refused(lambda: branches.create_branch(source, invalid, "非法"))
    passed("路径穿越、绝对路径、非法及未知 id 拒绝")

    before_files = {p.name for p in (directory / "db" / "snapshots").iterdir()}
    second = branches.create_snapshot(source, "同名")
    assert second["id"] != snap["id"] and branches._hash(snap["path"]) == snap["sha256"]
    assert before_files <= {p.name for p in (directory / "db" / "snapshots").iterdir()}
    passed("重名快照生成独立 id，不覆盖已有文件")

    branch = branches.create_branch(source, snap["id"], "同名")
    branch2 = branches.create_branch(source, snap["id"], "同名")
    registered = branches.list_branches(source)
    assert len(registered) == 2 and {branches.branch_path(source, row["id"]) for row in registered} == {branch, branch2}
    refused(lambda: branches.branch_path(other, registered[0]["id"]))
    refused(lambda: branches.branch_path(source, "../bad"))
    assert branch != branch2 and dump(branch) == baseline and dump(branch2) == baseline
    assert Path(branch).parent == directory / "db" / "branches"
    store, guard, cx = open_world(branch)
    try:
        sim.run(store, cx, guard, load_ids(store), 1, 20261015, verbose=False)
        assert store.scalar("SELECT tick FROM world WHERE id=1") > 0
        assert cx.verify_chain()[0] and cx.verify_anchor()["ok"]
    finally:
        store.close()
    assert dump(branch) != baseline and dump(branch2) == baseline and dump(source) == baseline
    assert branches._hash(snap["path"]) == snap["sha256"]
    passed("分支可独立推进 1 日，链和 anchor 通过；源库及兄弟分支不变")

    with ThreadPoolExecutor(max_workers=4) as pool:
        concurrent = list(pool.map(lambda _: branches.create_snapshot(source, "并发同名"), range(4)))
    assert len({x["id"] for x in concurrent}) == 4
    assert len(branches.list_snapshots(source)) == 6
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda _: branches.create_branch(source, snap["id"], "并发分支"), range(4)))
    assert len(set(paths)) == 4 and all(dump(p) == baseline for p in paths)
    passed("4 路并发同名快照及分支均独立，manifest 无丢条")

    class FixedID:
        hex = snap["id"]

    with patch.object(branches.uuid, "uuid4", return_value=FixedID()):
        refused(lambda: branches.create_snapshot(source, "碰撞"))
    assert branches._hash(snap["path"]) == snap["sha256"]
    passed("强制 id 碰撞由 O_EXCL 拒绝，已有存档未覆盖")

    with closing(sqlite3.connect(second["path"])) as con:
        con.execute("UPDATE world SET name='篡改'")
        con.commit()
    count = len(list((directory / "db" / "branches").glob("*.db")))
    refused(lambda: branches.create_branch(source, second["id"], "篡改"))
    assert len(list((directory / "db" / "branches").glob("*.db"))) == count
    passed("篡改快照 SHA256 不匹配，拒绝分支且不生成成功条目")

    # 即使 manifest 中的 hash 被同步改写，事件链和 anchor 仍须独立校验。
    for field, value in (("chain_head", "错误锚点"), ("event_count", 999)):
        bad = branches.create_snapshot(source, "坏历史")
        with closing(sqlite3.connect(bad["path"])) as con:
            con.execute(f"UPDATE world SET {field}=? WHERE id=1", (value,))
            con.commit()
        with closing(branches._manifest()) as con:
            con.execute("UPDATE snapshots SET sha256=? WHERE id=?", (branches._hash(bad["path"]), bad["id"]))
            con.commit()
        refused(lambda: branches.create_branch(source, bad["id"], "坏历史"))
    passed("SHA256 可匹配时，错误 anchor 仍独立拒绝")

    bad_chain = branches.create_snapshot(source, "断链")
    with closing(sqlite3.connect(bad_chain["path"])) as con:
        triggers = con.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='event'").fetchall()
        for (trigger,) in triggers:
            con.execute('DROP TRIGGER "' + trigger.replace('"', '""') + '"')
        con.execute("UPDATE event SET summary='被篡改的事件'")
        con.commit()
    with closing(branches._manifest()) as con:
        con.execute("UPDATE snapshots SET sha256=? WHERE id=?", (branches._hash(bad_chain["path"]), bad_chain["id"]))
        con.commit()
    refused(lambda: branches.create_branch(source, bad_chain["id"], "断链"))
    passed("快照内事件被篡改且 hash 同步改写时，事件链仍独立拒绝")

    with patch.object(branches, "_verify", side_effect=[None, ValueError("分支校验失败")]):
        refused(lambda: branches.create_branch(source, snap["id"], "失败分支"))
    with closing(branches._manifest()) as con:
        assert con.execute("SELECT COUNT(*) FROM branches WHERE name='失败分支'").fetchone()[0] == 0
    passed("复制后校验失败不返回成功、不登记；失败产物保留不删除")

    assert dump(source) == baseline
    passed("全部操作结束后源库 iterdump 逐行完全不变")
    print(f"RESULT: {checks} 项检查通过")
    print("测试产物保留目录:", directory)
    print("生产存储根:", ROOT / "db")


if __name__ == "__main__":
    main()
