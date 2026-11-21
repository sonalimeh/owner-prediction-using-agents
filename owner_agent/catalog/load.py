import json
import os
import sqlite3

from owner_agent.storage import db

# record_type -> (table, columns in insert order)
RECORDS = {
    "user": ("users", ("id", "name", "team", "user_type", "active")),
    "asset": (
        "assets",
        ("id", "qualified_name", "type", "description", "owner", "owner_set_by", "version"),
    ),
    "edge": ("edges", ("edge_id", "from_id", "to_id", "edge_type")),
    "signal": ("signals", ("signal_id", "asset_id", "user_id", "signal_type", "ts", "note")),
}


class LoadError(Exception):
    pass


def _row(record, lineno):
    kind = record.get("record_type")
    if kind not in RECORDS:
        raise LoadError(f"line {lineno}: unknown record_type {kind!r}")
    table, columns = RECORDS[kind]
    missing = [c for c in columns if c not in record]
    if missing:
        raise LoadError(f"line {lineno}: {kind} record missing {', '.join(missing)}")
    if kind == "user" and not isinstance(record["active"], bool):
        raise LoadError(f"line {lineno}: user.active must be true or false")
    if kind == "asset" and isinstance(record["version"], bool):
        raise LoadError(f"line {lineno}: asset.version must be an integer")
    return table, columns, tuple(record[c] for c in columns)


def _check_foreign_keys(conn):
    problems = conn.execute("PRAGMA foreign_key_check").fetchall()
    if not problems:
        return
    lines = []
    for table, rowid, parent, _ in problems[:10]:
        key = "edge_id" if table == "edges" else "signal_id" if table == "signals" else "id"
        record_id = conn.execute(f"SELECT {key} FROM {table} WHERE rowid = ?", (rowid,)).fetchone()[0]
        lines.append(f"{table} {record_id} references a missing {parent} row")
    more = f" (and {len(problems) - 10} more)" if len(problems) > 10 else ""
    raise LoadError("dangling references: " + "; ".join(lines) + more)


def _populate(conn, seed_path):
    counts = {kind: 0 for kind in RECORDS}
    conn.execute("BEGIN")
    # Seed files list assets before the users they reference, so check references at the end.
    conn.execute("PRAGMA defer_foreign_keys = ON")
    with open(seed_path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                raise LoadError(f"line {lineno}: invalid JSON: {e}") from None
            if not isinstance(record, dict):
                raise LoadError(f"line {lineno}: expected a JSON object")
            table, columns, values = _row(record, lineno)
            try:
                conn.execute(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                    values,
                )
            except sqlite3.Error as e:
                raise LoadError(f"line {lineno}: {record['record_type']} rejected: {e}") from None
            counts[record["record_type"]] += 1
    _check_foreign_keys(conn)
    conn.execute("COMMIT")
    counts["orphans"] = conn.execute("SELECT count(*) FROM assets WHERE owner IS NULL").fetchone()[0]
    return counts


def load(seed_path, db_path):
    """Build the catalog from a seed file, replacing any database at db_path.

    The database is built in a temporary file and moved into place only on success,
    so a failed load never leaves a partial catalog behind and keeps the old one.
    """
    if not os.path.isfile(seed_path):
        raise LoadError(f"seed file not found: {seed_path}")
    tmp_path = db_path + ".loading"
    if os.path.exists(tmp_path):
        os.remove(tmp_path)
    conn = db.connect(tmp_path)
    try:
        db.create_schema(conn)
        counts = _populate(conn, seed_path)
    except BaseException:
        conn.close()
        os.remove(tmp_path)
        raise
    conn.close()
    for suffix in ("-wal", "-shm", "-journal"):
        if os.path.exists(db_path + suffix):
            os.remove(db_path + suffix)
    os.replace(tmp_path, db_path)
    return counts
