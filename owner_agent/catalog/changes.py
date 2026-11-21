"""Edits people made to the catalog, applied from a changes file.

set_owner     sets the owner (owner_set_by = human) and increases the asset's version.
delete_asset  marks the asset deleted and increases its version.
deactivate_user marks the user inactive; assets are untouched.

The whole file is validated before anything is written, then applied in one transaction.
Each change_id is applied once: repeating a file reports already_applied, and reusing a
change_id with different content is an error.
"""

import json
from datetime import datetime, timezone

from owner_agent.storage import db

FIELDS = {
    "set_owner": ("asset_id", "owner"),
    "delete_asset": ("asset_id",),
    "deactivate_user": ("user_id",),
}


class ChangesError(Exception):
    pass


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _read(path):
    try:
        f = open(path, encoding="utf-8")
    except OSError as e:
        raise ChangesError(f"cannot read {path}: {e.strerror}") from None
    records, seen = [], set()
    with f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError as e:
                raise ChangesError(f"line {lineno}: invalid JSON: {e}") from None
            if not isinstance(r, dict) or r.get("record_type") != "change":
                raise ChangesError(f"line {lineno}: expected a record with record_type \"change\"")
            op = r.get("op")
            if op not in FIELDS:
                raise ChangesError(f"line {lineno}: unknown op {op!r}")
            required = ("change_id", "actor") + FIELDS[op]
            missing = [k for k in required if k not in r]
            if missing:
                raise ChangesError(f"line {lineno}: {op} change missing {', '.join(missing)}")
            for k in ("change_id", "actor") + tuple(k for k in FIELDS[op] if k != "owner"):
                if not isinstance(r[k], str) or not r[k].strip():
                    raise ChangesError(f"line {lineno}: {k} must be a non-empty string")
            if op == "set_owner" and r["owner"] is not None and not isinstance(r["owner"], str):
                raise ChangesError(f"line {lineno}: owner must be a user id or null")
            if r["change_id"] in seen:
                raise ChangesError(f"line {lineno}: change_id {r['change_id']} appears twice in the file")
            seen.add(r["change_id"])
            records.append(
                {
                    "change_id": r["change_id"],
                    "op": op,
                    "asset_id": r.get("asset_id"),
                    "user_id": r.get("user_id"),
                    "owner": r.get("owner"),
                    "actor": r["actor"],
                }
            )
    return records


def _check_references(conn, records):
    assets = {r[0] for r in conn.execute("SELECT id FROM assets")}
    users = {r[0] for r in conn.execute("SELECT id FROM users")}
    for c in records:
        if c["asset_id"] is not None and c["asset_id"] not in assets:
            raise ChangesError(f"change {c['change_id']}: unknown asset {c['asset_id']}")
        for key in ("user_id", "owner"):
            if c[key] is not None and c[key] not in users:
                raise ChangesError(f"change {c['change_id']}: unknown user {c[key]}")


def _apply_one(conn, c):
    """Apply one change inside the open transaction. Returns (outcome, reason)."""
    if c["op"] == "deactivate_user":
        user = conn.execute("SELECT active FROM users WHERE id = ?", (c["user_id"],)).fetchone()
        if not user["active"]:
            return "skipped", f"user {c['user_id']} was already inactive"
        conn.execute("UPDATE users SET active = 0 WHERE id = ?", (c["user_id"],))
        return "applied", None

    asset = conn.execute("SELECT deleted FROM assets WHERE id = ?", (c["asset_id"],)).fetchone()
    if asset["deleted"]:
        return "skipped", f"asset {c['asset_id']} was already deleted"
    if c["op"] == "delete_asset":
        conn.execute("UPDATE assets SET deleted = 1, version = version + 1 WHERE id = ?", (c["asset_id"],))
    else:
        conn.execute(
            "UPDATE assets SET owner = ?, owner_set_by = ?, version = version + 1 WHERE id = ?",
            (c["owner"], "human" if c["owner"] else None, c["asset_id"]),
        )
    return "applied", None


KEYS = ("op", "asset_id", "user_id", "owner", "actor")


def apply_changes(conn, path):
    records = _read(path)
    db.crash_if_due()
    conn.execute("BEGIN IMMEDIATE")
    try:
        _check_references(conn, records)
        results = []
        for c in records:
            previous = conn.execute("SELECT * FROM changes WHERE change_id = ?", (c["change_id"],)).fetchone()
            if previous is not None:
                if any(previous[k] != c[k] for k in KEYS):
                    raise ChangesError(f"change {c['change_id']} was already applied with different content")
                results.append({"change_id": c["change_id"], "op": c["op"], "outcome": "already_applied"})
                continue
            outcome, reason = _apply_one(conn, c)
            conn.execute(
                "INSERT INTO changes (change_id, op, asset_id, user_id, owner, actor, outcome, reason, applied_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (c["change_id"], c["op"], c["asset_id"], c["user_id"], c["owner"], c["actor"], outcome, reason, _now()),
            )
            result = {"change_id": c["change_id"], "op": c["op"], "outcome": outcome}
            if reason:
                result["reason"] = reason
            results.append(result)
        db.commit(conn)
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    counts = {k: sum(r["outcome"] == k for r in results) for k in ("applied", "skipped", "already_applied")}
    return {**counts, "changes": results}
