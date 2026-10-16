"""建立历史不可改写的安全快照与可独立推进的世界分支。"""

from contextlib import closing
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import uuid

from .causality import Causality
from .guardrails import Guardrail
from . import projection


_DB_DIR = Path(__file__).resolve().parent.parent / "db"
_ID = re.compile(r"[0-9a-f]{32}\Z")


def _source(db_path):
    path = Path(db_path).resolve(strict=True)
    if not path.is_file():
        raise ValueError("源存档不存在")
    return str(path)


def _name(name):
    if not isinstance(name, str) or not name.strip() or len(name) > 120 or "\x00" in name:
        raise ValueError("显示名称须为 1 到 120 个字符")
    return name.strip()


def _directory(kind):
    path = _DB_DIR / kind
    if path.is_symlink():
        raise ValueError("存档目录不能是符号链接")
    path.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or not path.is_dir():
        raise ValueError("存档目录不安全")
    return path


def _manifest():
    path = _directory("snapshots") / "manifest.sqlite"
    if path.is_symlink():
        raise ValueError("manifest 不能是符号链接")
    con = sqlite3.connect(path, timeout=30)
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS snapshots (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, name TEXT NOT NULL,
            sha256 TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            state_digest TEXT NOT NULL DEFAULT ''
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS branches (
            id TEXT PRIMARY KEY, source TEXT NOT NULL, snapshot_id TEXT NOT NULL,
            name TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        # 非破坏性升级：早期 manifest 的 snapshots 没有 state_digest 列。
        if "state_digest" not in {row[1] for row in con.execute("PRAGMA table_info(snapshots)")}:
            con.execute("ALTER TABLE snapshots ADD COLUMN state_digest TEXT NOT NULL DEFAULT ''")
        con.commit()
        return con
    except Exception:
        con.close()
        raise


def _hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify(path):
    # 不调用 open_world：校验连接必须只读，不触碰历史或状态。
    with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("数据库完整性校验失败")
        if con.execute("SELECT COUNT(*) FROM world").fetchone()[0] != 1:
            raise ValueError("存档没有唯一世界")

        class ReadOnlyStore:
            def one(self, sql, args=()):
                return con.execute(sql, args).fetchone()

            def all(self, sql, args=()):
                return con.execute(sql, args).fetchall()

            def scalar(self, sql, args=(), default=None):
                row = con.execute(sql, args).fetchone()
                return row[0] if row else default

        store = ReadOnlyStore()
        cx = Causality(store, Guardrail(store))
        if not cx.verify_chain()[0] or not cx.verify_anchor()["ok"]:
            raise ValueError("事件链或历史锚点校验失败")


def _state_digest_of(path):
    """只读地计算某存档的状态表指纹（不触碰连接）。"""
    with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row

        class _ReadOnly:
            def all(self, sql, args=()):
                return con.execute(sql, args).fetchall()

            def scalar(self, sql, args=(), default=None):
                row = con.execute(sql, args).fetchone()
                return row[0] if row else default

        return projection.state_digest(_ReadOnly())["digest"]


def _backup(source, destination):
    # 独占预留服务器生成的文件名，绝不覆盖已有存档。
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(Path(source).as_uri() + "?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(Path(destination).as_uri() + "?mode=rw", uri=True)) as dst:
            src.backup(dst)


def create_snapshot(db_path, name):
    """返回已验证快照的 {id, name, path, sha256}；名称仅用于显示。"""
    source = _source(db_path)
    name = _name(name)
    _verify(source)
    with closing(_manifest()) as manifest, manifest:
        manifest.execute("BEGIN IMMEDIATE")
        snapshot_id = uuid.uuid4().hex
        path = _directory("snapshots") / (snapshot_id + ".db")
        _backup(source, path)
        _verify(path)
        checksum = _hash(path)
        digest = _state_digest_of(path)
        manifest.execute(
            "INSERT INTO snapshots (id, source, name, sha256, state_digest) VALUES (?, ?, ?, ?, ?)",
            (snapshot_id, source, name, checksum, digest),
        )
    return {"id": snapshot_id, "name": name, "path": str(path), "sha256": checksum,
            "state_digest": digest}


def list_snapshots(db_path):
    """仅列出绑定此源存档绝对路径的快照，不代表重新验证文件。"""
    source = _source(db_path)
    with closing(_manifest()) as manifest:
        rows = manifest.execute(
            "SELECT id, name, sha256, created_at, state_digest FROM snapshots "
            "WHERE source=? ORDER BY created_at, id",
            (source,),
        ).fetchall()
    directory = _directory("snapshots")
    return [dict(id=row[0], name=row[1], path=str(directory / (row[0] + ".db")),
                 sha256=row[2], created_at=row[3], state_digest=row[4]) for row in rows]


def list_branches(db_path):
    """List branches created directly from snapshots of this world."""
    source = _source(db_path)
    with closing(_manifest()) as manifest:
        rows = manifest.execute(
            "SELECT id,name,snapshot_id,created_at FROM branches WHERE source=? ORDER BY created_at,id",
            (source,),
        ).fetchall()
    return [dict(id=row[0], name=row[1], snapshot_id=row[2], created_at=row[3]) for row in rows]


def branch_path(db_path, branch_id):
    """Resolve only a registered direct child and verify its current history."""
    source = _source(db_path)
    if not isinstance(branch_id, str) or not _ID.fullmatch(branch_id):
        raise ValueError("非法分支 id")
    with closing(_manifest()) as manifest:
        row = manifest.execute(
            "SELECT id FROM branches WHERE id=? AND source=?", (branch_id, source)
        ).fetchone()
    if row is None:
        raise ValueError("分支不存在或不属于此源存档")
    path = _directory("branches") / (branch_id + ".db")
    if path.is_symlink() or not path.is_file():
        raise ValueError("分支存档缺失或不安全")
    _verify(path)
    return str(path)


def create_branch(db_path, snapshot_id, name):
    """返回已验证的分支路径供主控重启选择，不自动切换当前世界。"""
    source = _source(db_path)
    name = _name(name)
    if not isinstance(snapshot_id, str) or not _ID.fullmatch(snapshot_id):
        raise ValueError("非法快照 id")
    with closing(_manifest()) as manifest, manifest:
        manifest.execute("BEGIN IMMEDIATE")
        row = manifest.execute(
            "SELECT sha256 FROM snapshots WHERE id=? AND source=?", (snapshot_id, source)
        ).fetchone()
        if row is None:
            raise ValueError("快照不存在或不属于此源存档")
        snapshot = _directory("snapshots") / (snapshot_id + ".db")
        if snapshot.is_symlink() or not snapshot.is_file() or _hash(snapshot) != row[0]:
            raise ValueError("快照缺失或 SHA256 校验失败")
        _verify(snapshot)
        branch_id = uuid.uuid4().hex
        branch = _directory("branches") / (branch_id + ".db")
        _backup(snapshot, branch)
        if _hash(snapshot) != row[0]:
            raise ValueError("复制期间快照已改变")
        _verify(branch)
        manifest.execute(
            "INSERT INTO branches (id, source, snapshot_id, name) VALUES (?, ?, ?, ?)",
            (branch_id, source, snapshot_id, name),
        )
    return str(branch)
