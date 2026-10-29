"""Persistent job state and lease fencing for the API and worker."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


def _new_id() -> str:
    return uuid.uuid4().hex


def _password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1)
    return f"{salt.hex()}:{digest.hex()}"


from .workflow import WorkflowStore


class Store(WorkflowStore):
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users(
                    id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
                CREATE TABLE IF NOT EXISTS sessions(
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, url TEXT NOT NULL, repo_ids TEXT NOT NULL,
                    created_by TEXT NOT NULL, status TEXT NOT NULL,
                    snapshot_json TEXT, error TEXT, candidate_id TEXT,
                    lease_epoch INTEGER NOT NULL DEFAULT 0, lease_owner TEXT,
                    lease_expires REAL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts(
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, number INTEGER NOT NULL,
                    epoch INTEGER NOT NULL, status TEXT NOT NULL, started_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    kind TEXT NOT NULL, payload_json TEXT NOT NULL, epoch INTEGER,
                    created_at REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS events_job_idx ON events(job_id,id);
                CREATE TABLE IF NOT EXISTS candidates(
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, attempt_id TEXT,
                    repos_json TEXT NOT NULL, reproduced INTEGER NOT NULL,
                    verified INTEGER NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS approvals(
                    candidate_id TEXT PRIMARY KEY, reviewer_id TEXT NOT NULL,
                    binding TEXT NOT NULL, created_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS deliveries(
                    candidate_id TEXT PRIMARY KEY, status TEXT NOT NULL,
                    prs_json TEXT NOT NULL, updated_at REAL NOT NULL);
            """)

            self._workflow_schema(db)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def create_user(self, username: str, password: str) -> str:
        username = username.strip().lower()
        if not username or len(password) < 6:
            raise ValueError("username and password of at least 6 characters required")
        user_id = _new_id()
        with self._db() as db:
            db.execute("INSERT INTO users VALUES(?,?,?,1)",
                       (user_id, username, _password_hash(password)))
        return user_id

    def authenticate(self, username: str, password: str) -> str | None:
        with self._db() as db:
            row = db.execute("SELECT id,password_hash FROM users WHERE username=? AND active=1",
                             (username.strip().lower(),)).fetchone()
        if row is None:
            return None
        try:
            salt, digest = row["password_hash"].split(":")
            actual = _password_hash(password, bytes.fromhex(salt)).split(":")[1]
            return row["id"] if hmac.compare_digest(actual, digest) else None
        except (ValueError, TypeError):
            return None

    def create_session(self, user_id: str, ttl_seconds: int = 604800) -> str:
        token = secrets.token_urlsafe(32)
        with self._db() as db:
            db.execute("INSERT INTO sessions VALUES(?,?,?)",
                       (hashlib.sha256(token.encode()).hexdigest(), user_id,
                        time.time() + ttl_seconds))
        return token

    def get_user_by_session(self, token: str | None) -> dict[str, str] | None:
        if not token:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._db() as db:
            row = db.execute("""SELECT u.id,u.username FROM users u JOIN sessions s
                ON s.user_id=u.id WHERE s.token_hash=? AND s.expires>? AND u.active=1""",
                (digest, time.time())).fetchone()
        return dict(row) if row else None

    def delete_session(self, token: str | None) -> None:
        if token:
            with self._db() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?",
                           (hashlib.sha256(token.encode()).hexdigest(),))

    def create_job(self, url: str, repo_ids: list[str], creator_id: str,
                   snapshot: dict[str, Any] | None = None) -> str:
        if not repo_ids or len(set(repo_ids)) != len(repo_ids):
            raise ValueError("unique repositories required")
        job_id, now = _new_id(), time.time()
        with self._db() as db:
            db.execute("""INSERT INTO jobs(id,url,repo_ids,created_by,status,snapshot_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (job_id, url, json.dumps(repo_ids), creator_id, "queued",
                 json.dumps(snapshot) if snapshot is not None else None, now, now))
            db.execute("INSERT INTO events(job_id,kind,payload_json,created_at) VALUES(?,?,?,?)",
                       (job_id, "created", "{}", now))
        self.ensure_workflow(job_id)
        return job_id

    def _decode_job(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        job = dict(row)
        job["repo_ids"] = json.loads(job["repo_ids"])
        job["snapshot"] = json.loads(job.pop("snapshot_json")) if job["snapshot_json"] else None
        job["workflow"] = self.get_workflow(job["id"])
        return job

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            return self._decode_job(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def list_jobs(self, viewer_id: str) -> list[dict[str, Any]]:
        with self._db() as db:
            if not db.execute("SELECT 1 FROM users WHERE id=? AND active=1", (viewer_id,)).fetchone():
                return []
            return [self._decode_job(row) for row in db.execute("SELECT * FROM jobs ORDER BY created_at DESC")]

    def retry_job(self, job_id: str, user_id: str) -> bool:
        """Atomically requeue a terminal attempt and fence every previous worker."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["created_by"] != user_id:
                raise PermissionError("only the creator may retry")
            if row["status"] not in ("failed", "cancelled", "unreproduced", "diagnosis_only"):
                return False
            workflow = self._load_workflow(db, job_id)
            if workflow:
                from .workflow import initial
                fresh = initial(json.loads(row['snapshot_json'] or '{}'), workflow['snapshot_version'])
                fresh['revision'] = workflow['revision'] + 1
                fresh['input'] = workflow['input']
                fresh['source_snapshot_version'] = workflow.get('source_snapshot_version', 1)
                saved = db.execute('SELECT snapshot_json FROM job_snapshots WHERE job_id=? AND version=?', (job_id, workflow['snapshot_version'])).fetchone()
                if saved:
                    from .workflow import digest
                    fresh['snapshot_hash'] = digest(json.loads(saved[0]))
                self._write_workflow(db, job_id, fresh)
            now = time.time()
            db.execute("""UPDATE jobs SET status='queued',error=NULL,candidate_id=NULL,
                lease_epoch=lease_epoch+1,lease_owner=NULL,lease_expires=NULL,updated_at=?
                WHERE id=?""", (now, job_id))
            db.execute("UPDATE attempts SET status=? WHERE job_id=? AND status='running'",
                       (row["status"], job_id))
            db.execute("INSERT INTO events(job_id,kind,payload_json,created_at) VALUES(?,?,?,?)",
                       (job_id, "retry", json.dumps({"message": "任务已重新排队", "requested_by": user_id}), now))
            return True

    def claim_next_job(self, owner: str, *, now: float | None = None,
                       lease_seconds: int = 60) -> dict[str, Any] | None:
        now = time.time() if now is None else now
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT id,lease_epoch FROM jobs WHERE status='queued'
                OR (status='running' AND lease_expires<?) ORDER BY created_at LIMIT 1""",
                (now,)).fetchone()
            if row is None:
                return None
            epoch = row["lease_epoch"] + 1
            db.execute("""UPDATE jobs SET status='running',lease_epoch=?,lease_owner=?,
                lease_expires=?,updated_at=? WHERE id=?""",
                (epoch, owner, now + lease_seconds, now, row["id"]))
            number = db.execute("SELECT COUNT(*) FROM attempts WHERE job_id=?",
                                (row["id"],)).fetchone()[0] + 1
            attempt_id = _new_id()
            db.execute("INSERT INTO attempts VALUES(?,?,?,?,?,?)",
                       (attempt_id, row["id"], number, epoch, "running", now))
            return {"job_id": row["id"], "attempt_id": attempt_id, "epoch": epoch}

    def renew_lease(self, job_id: str, epoch: int, owner: str, *, now: float | None = None,
                    lease_seconds: int = 60) -> bool:
        now = time.time() if now is None else now
        with self._db() as db:
            cursor = db.execute("""UPDATE jobs SET lease_expires=?,updated_at=?
                WHERE id=? AND lease_epoch=? AND lease_owner=? AND status='running'""",
                (now + lease_seconds, now, job_id, epoch, owner))
            return cursor.rowcount == 1

    def append_event(self, job_id: str, kind: str, payload: dict[str, Any],
                     *, epoch: int | None = None) -> bool:
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT lease_epoch,status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or (epoch is not None and (row["lease_epoch"] != epoch
                                                      or row["status"] not in ("running", "review_ready"))):
                return False
            db.execute("INSERT INTO events(job_id,kind,payload_json,epoch,created_at) VALUES(?,?,?,?,?)",
                       (job_id, kind, json.dumps(payload), epoch, time.time()))
            return True

    def get_events(self, job_id: str, after: int = 0) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM events WHERE job_id=? AND id>? ORDER BY id",
                              (job_id, after)).fetchall()
        return [{"id": row["id"], "job_id": job_id, "kind": row["kind"],
                 "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]}
                for row in rows]

    def set_job_status(self, job_id: str, status: str, *, epoch: int | None = None,
                       error: str | None = None) -> bool:
        with self._db() as db:
            sql = "UPDATE jobs SET status=?,error=?,updated_at=? WHERE id=?"
            args: tuple[Any, ...] = (status, error, time.time(), job_id)
            if epoch is not None:
                sql += " AND lease_epoch=? AND status='running'"
                args += (epoch,)
            return db.execute(sql, args).rowcount == 1

    def save_snapshot(self, job_id: str, snapshot: dict[str, Any], *, epoch: int) -> bool:
        with self._db() as db:
            return db.execute("""UPDATE jobs SET snapshot_json=?,updated_at=?
                WHERE id=? AND lease_epoch=?""",
                (json.dumps(snapshot), time.time(), job_id, epoch)).rowcount == 1

    def save_candidate(self, job_id: str, repos: dict[str, Any], *, reproduced: bool = True,
                       verified: bool = True, attempt_id: str | None = None,
                       epoch: int | None = None) -> str:
        workflow=self.get_workflow(job_id)
        if workflow and not workflow["ready_for_implementation"]:
            raise ValueError("PRD 未通过双重收敛审查")
        if workflow:
            job = self.get_job(job_id)
            if not repos or not set(repos) <= set(job["repo_ids"]):
                raise ValueError("候选仓库不在分析范围内")
        if not reproduced or not verified:
            raise ValueError("reproduction and verification required")
        candidate_id = _new_id()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT lease_epoch,status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or (epoch is not None and (row["lease_epoch"] != epoch
                                                      or row["status"] != "running")):
                raise ValueError("stale lease")
            db.execute("INSERT INTO candidates VALUES(?,?,?,?,?,?,?)",
                       (candidate_id, job_id, attempt_id, json.dumps(repos), 1, 1, time.time()))
            db.execute("UPDATE jobs SET candidate_id=?,status='review_ready',updated_at=? WHERE id=?",
                       (candidate_id, time.time(), job_id))
        return candidate_id

    def mark_review_ready(self, job_id: str, repos: dict[str, Any], *, reproduced: bool,
                          epoch: int) -> str:
        if not reproduced:
            raise ValueError("reproduction required")
        return self.save_candidate(job_id, repos, epoch=epoch)

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["repos"] = json.loads(item.pop("repos_json"))
        item["reproduced"], item["verified"] = bool(item["reproduced"]), bool(item["verified"])
        return item

    def approve_candidate(self, job_id: str, candidate_id: str, reviewer_id: str) -> bool:
        from .review import approve_candidate

        if not self.workflow_implementation_ready(job_id):
            return False

        try:
            approve_candidate(self, job_id, candidate_id, reviewer_id)
        except ValueError:
            return False
        return True

    def save_approval(self, candidate_id: str, reviewer_id: str, binding: str) -> None:
        with self._db() as db:
            db.execute("""INSERT INTO approvals VALUES(?,?,?,?) ON CONFLICT(candidate_id)
                DO UPDATE SET reviewer_id=excluded.reviewer_id,binding=excluded.binding,
                created_at=excluded.created_at""",
                (candidate_id, reviewer_id, binding, time.time()))

    def get_approval(self, candidate_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM approvals WHERE candidate_id=?", (candidate_id,)).fetchone()
        return dict(row) if row else None

    def get_delivery(self, candidate_id: str) -> dict[str, Any] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM deliveries WHERE candidate_id=?", (candidate_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["prs"] = json.loads(result.pop("prs_json"))
        return result

    def save_delivery(self, candidate_id: str, status: str, prs: dict[str, Any]) -> None:
        with self._db() as db:
            db.execute("""INSERT INTO deliveries VALUES(?,?,?,?) ON CONFLICT(candidate_id)
                DO UPDATE SET status=excluded.status,prs_json=excluded.prs_json,
                updated_at=excluded.updated_at""",
                (candidate_id, status, json.dumps(prs), time.time()))

    def next_approved_candidate(self) -> str | None:
        with self._db() as db:
            rows = db.execute("""SELECT a.candidate_id,c.job_id FROM approvals a
                JOIN candidates c ON c.id=a.candidate_id
                JOIN jobs j ON j.candidate_id=c.id
                LEFT JOIN deliveries d ON d.candidate_id=c.id
                WHERE j.status IN ('review_ready','delivery_wip')
                    AND (d.status IS NULL OR d.status='delivery_wip')
                ORDER BY a.created_at""").fetchall()
            return next((row["candidate_id"] for row in rows if self.workflow_implementation_ready(row["job_id"])), None)
