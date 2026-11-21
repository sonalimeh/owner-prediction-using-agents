import json

ASSET_QUERY = """
SELECT 'asset' AS record_type, id, qualified_name, type, description, owner, owner_set_by, version, deleted
FROM assets
ORDER BY id
"""

AUDIT_QUERY = """
SELECT 'audit' AS record_type, audit_id, proposal_id, actor, asset_id,
       old_owner, new_owner, version_before, version_after, ts
FROM audit_log
ORDER BY asset_id, version_before, audit_id
"""


def asset_lines(conn):
    for row in conn.execute(ASSET_QUERY):
        record = dict(row)
        record["deleted"] = bool(record["deleted"])
        yield json.dumps(record, sort_keys=True)


def audit_lines(conn):
    for row in conn.execute(AUDIT_QUERY):
        yield json.dumps(dict(row), sort_keys=True)
