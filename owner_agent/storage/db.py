import os
import sqlite3
import urllib.parse


class DatabaseError(Exception):
    pass

SCHEMA_VERSION = 6

CRASH_EXIT_CODE = 75
_commits = 0

SCHEMA = """
CREATE TABLE users (
    id        TEXT PRIMARY KEY,
    name      TEXT NOT NULL,
    team      TEXT NOT NULL,
    user_type TEXT NOT NULL CHECK (user_type IN ('person', 'service')),
    active    INTEGER NOT NULL CHECK (active IN (0, 1))
) STRICT;

CREATE TABLE assets (
    id             TEXT PRIMARY KEY,
    qualified_name TEXT NOT NULL UNIQUE,
    type           TEXT NOT NULL CHECK (type IN ('table', 'view', 'dashboard', 'pipeline')),
    description    TEXT NOT NULL,
    owner          TEXT REFERENCES users (id),
    owner_set_by   TEXT CHECK (owner_set_by IN ('human', 'agent')),
    version        INTEGER NOT NULL CHECK (version >= 0),
    -- delete_asset marks the asset deleted rather than removing it, so history keeps pointing at it.
    deleted        INTEGER NOT NULL DEFAULT 0 CHECK (deleted IN (0, 1)),
    CHECK (owner IS NOT NULL OR owner_set_by IS NULL)
) STRICT;

CREATE TABLE edges (
    edge_id   TEXT PRIMARY KEY,
    from_id   TEXT NOT NULL REFERENCES assets (id),
    to_id     TEXT NOT NULL REFERENCES assets (id),
    edge_type TEXT NOT NULL CHECK (edge_type IN ('upstream_of', 'contains'))
) STRICT;

CREATE TABLE signals (
    signal_id   TEXT PRIMARY KEY,
    asset_id    TEXT NOT NULL REFERENCES assets (id),
    user_id     TEXT NOT NULL REFERENCES users (id),
    signal_type TEXT NOT NULL CHECK (signal_type IN ('query', 'edit', 'incident_response')),
    ts          TEXT NOT NULL,
    note        TEXT NOT NULL
) STRICT;

CREATE TABLE runs (
    run_id         TEXT PRIMARY KEY,
    mode           TEXT NOT NULL CHECK (mode IN ('baseline', 'model')),
    model          TEXT,
    prompt_version TEXT NOT NULL,
    started_at     TEXT NOT NULL
) STRICT;

-- signal_ids and edge_ids are JSON arrays of the evidence the proposal relied on.
-- proposed_owner NULL means the agent declined. model NULL means no model was called.
-- confidence_group is the rules' group whose measured accuracy is the confidence (rules.GROUPS).
CREATE TABLE proposals (
    proposal_id      TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES runs (run_id),
    asset_id         TEXT NOT NULL REFERENCES assets (id),
    asset_version    INTEGER NOT NULL,
    proposed_owner   TEXT REFERENCES users (id),
    confidence       REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    confidence_group TEXT NOT NULL CHECK (length(confidence_group) > 0),
    signal_ids     TEXT NOT NULL CHECK (json_valid(signal_ids) AND json_type(signal_ids) = 'array'),
    edge_ids       TEXT NOT NULL CHECK (json_valid(edge_ids) AND json_type(edge_ids) = 'array'),
    reason         TEXT NOT NULL,
    model          TEXT,
    prompt_version TEXT NOT NULL,
    input_tokens   INTEGER NOT NULL CHECK (input_tokens >= 0),
    output_tokens  INTEGER NOT NULL CHECK (output_tokens >= 0),
    cost_usd       REAL NOT NULL CHECK (cost_usd >= 0),
    latency_ms     REAL NOT NULL CHECK (latency_ms >= 0),
    created_at     TEXT NOT NULL,
    UNIQUE (run_id, asset_id)
) STRICT;

CREATE TRIGGER runs_no_update BEFORE UPDATE ON runs
BEGIN
    SELECT RAISE(ABORT, 'runs are immutable');
END;

CREATE TRIGGER runs_no_delete BEFORE DELETE ON runs
BEGIN
    SELECT RAISE(ABORT, 'runs are immutable');
END;

CREATE TRIGGER proposals_no_update BEFORE UPDATE ON proposals
BEGIN
    SELECT RAISE(ABORT, 'proposals are immutable');
END;

CREATE TRIGGER proposals_no_delete BEFORE DELETE ON proposals
BEGIN
    SELECT RAISE(ABORT, 'proposals are immutable');
END;

-- A person's final decision on a proposal; at most one per proposal, never changed.
CREATE TABLE decisions (
    proposal_id TEXT PRIMARY KEY REFERENCES proposals (proposal_id),
    decision    TEXT NOT NULL CHECK (decision IN ('approved', 'rejected')),
    actor       TEXT NOT NULL CHECK (length(trim(actor)) > 0),
    reason      TEXT,
    decided_at  TEXT NOT NULL,
    CHECK (decision = 'approved' OR length(trim(coalesce(reason, ''))) > 0)
) STRICT;

CREATE TRIGGER decisions_only_owner_proposals_approved BEFORE INSERT ON decisions
WHEN NEW.decision = 'approved'
    AND (SELECT proposed_owner FROM proposals WHERE proposal_id = NEW.proposal_id) IS NULL
BEGIN
    SELECT RAISE(ABORT, 'only a proposal that names an owner can be approved');
END;

CREATE TRIGGER decisions_no_update BEFORE UPDATE ON decisions
BEGIN
    SELECT RAISE(ABORT, 'decisions are final');
END;

CREATE TRIGGER decisions_no_delete BEFORE DELETE ON decisions
BEGIN
    SELECT RAISE(ABORT, 'decisions are final');
END;

-- Edits people made, from changes files. One row per change_id, never changed.
CREATE TABLE changes (
    change_id  TEXT PRIMARY KEY,
    op         TEXT NOT NULL CHECK (op IN ('set_owner', 'delete_asset', 'deactivate_user')),
    asset_id   TEXT REFERENCES assets (id),
    user_id    TEXT REFERENCES users (id),
    owner      TEXT REFERENCES users (id),
    actor      TEXT NOT NULL,
    outcome    TEXT NOT NULL CHECK (outcome IN ('applied', 'skipped')),
    reason     TEXT,
    applied_at TEXT NOT NULL
) STRICT;

CREATE TRIGGER changes_no_update BEFORE UPDATE ON changes
BEGIN
    SELECT RAISE(ABORT, 'changes are append-only');
END;

CREATE TRIGGER changes_no_delete BEFORE DELETE ON changes
BEGIN
    SELECT RAISE(ABORT, 'changes are append-only');
END;

-- One entry per owner change made by apply.
CREATE TABLE audit_log (
    audit_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id    TEXT NOT NULL UNIQUE REFERENCES proposals (proposal_id),
    actor          TEXT NOT NULL,
    asset_id       TEXT NOT NULL REFERENCES assets (id),
    old_owner      TEXT REFERENCES users (id),
    new_owner      TEXT NOT NULL REFERENCES users (id),
    version_before INTEGER NOT NULL,
    version_after  INTEGER NOT NULL CHECK (version_after = version_before + 1),
    ts             TEXT NOT NULL
) STRICT;

CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only');
END;

CREATE INDEX assets_orphans ON assets (id) WHERE owner IS NULL;
CREATE INDEX assets_owner ON assets (owner);
CREATE INDEX edges_from ON edges (from_id);
CREATE INDEX edges_to ON edges (to_id);
CREATE INDEX signals_asset ON signals (asset_id, user_id);
CREATE INDEX signals_user ON signals (user_id);
CREATE INDEX proposals_run ON proposals (run_id);
"""


def connect(path):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def open_existing(path, readonly=False):
    if not os.path.isfile(path):
        raise DatabaseError(f"database not found: {path}; run load first")
    uri = "file:" + urllib.parse.quote(os.path.abspath(path)) + ("?mode=ro" if readonly else "")
    conn = sqlite3.connect(uri, uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version != SCHEMA_VERSION:
        conn.close()
        raise DatabaseError(
            f"{path} has schema version {version}, expected {SCHEMA_VERSION}; run load again"
        )
    return conn


def _crash_after():
    value = os.environ.get("CRASH_AFTER")
    if value is None or value == "":
        return None
    try:
        limit = int(value)
    except ValueError:
        raise DatabaseError(f"CRASH_AFTER must be a whole number, got {value!r}") from None
    if limit < 0:
        raise DatabaseError("CRASH_AFTER must not be negative")
    return limit


def crash_if_due():
    """Simulate a crash for resumability tests: exit once CRASH_AFTER transactions have committed."""
    limit = _crash_after()
    if limit is not None and _commits >= limit:
        os._exit(CRASH_EXIT_CODE)


def commit(conn):
    global _commits
    conn.execute("COMMIT")
    _commits += 1
    crash_if_due()


def create_schema(conn):
    conn.executescript(SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
