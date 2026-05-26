"""
Audit logger — writes structured entries to Azure Table Storage.
Falls back to a local JSONL file if Azure is unavailable (dev mode).
"""

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def write_audit_entry(
    automation: str,
    action: str,
    entity_id: str,
    input_summary: str,
    output_summary: str,
    status: str = "success",
    extra: dict | None = None,
) -> dict:
    """
    Write one audit record.  Tries Azure Table Storage first; falls back
    to a local audit/audit_log.jsonl file so the agent can run without Azure.

    Returns the completed entry dict.
    """
    entry = {
        "PartitionKey": automation,
        "RowKey": str(uuid.uuid4()),
        "timestamp": _utc_now(),
        "automation": automation,
        "action": action,
        "entity_id": entity_id,
        "input_hash": _hash(input_summary),
        "input_summary": input_summary[:500],
        "output_summary": output_summary[:500],
        "status": status,
        **(extra or {}),
    }

    _try_azure(entry)
    _write_local(entry)
    return entry


def _try_azure(entry: dict) -> None:
    conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
    table_name = os.getenv("AZURE_TABLE_NAME", "auditlog")
    if not conn_str:
        return
    try:
        from azure.data.tables import TableServiceClient
        svc = TableServiceClient.from_connection_string(conn_str)
        svc.create_table_if_not_exists(table_name)
        tbl = svc.get_table_client(table_name)
        safe = {k: str(v) if not isinstance(v, (str, int, float, bool)) else v
                for k, v in entry.items()}
        tbl.upsert_entity(safe)
    except Exception as e:
        print(f"[audit] Azure Table write failed: {e}", flush=True)


def _write_local(entry: dict) -> None:
    log_path = os.path.join(os.path.dirname(__file__), "audit_log.jsonl")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
