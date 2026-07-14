"""Shared utilities for agent export/deploy scripts.

Provides common functions for dbutils detection, UC path remapping,
CSV I/O, Delta table status tracking, and timestamp generation used
across KA and Supervisor agent deployment workflows.
"""

import csv
import io
import os
from datetime import datetime, timezone

from databricks.sdk.service.catalog import VolumeType


# ---------------------------------------------------------------------------
# KA API version-agnostic helper
# ---------------------------------------------------------------------------

_ka_api_versions = {}  # cache: host → "2.1" or "2.0"


def ka_api_call(w, method, path, body=None):
    """KA API call with version fallback (2.1 → 2.0).

    Callers may pass paths with underscores or hyphens — both are
    normalized to hyphens (knowledge-assistants) since the REST API
    uses hyphens in both API versions.
    Caches detected version per workspace host to avoid repeated probes.

    Always sends at least an empty JSON body ({}) because some workspaces
    reject requests without one (even GET).
    """
    if body is None:
        body = {}
    # Normalize: the REST API uses hyphens in both 2.1 and 2.0
    path = path.replace("knowledge_assistants", "knowledge-assistants")

    host = w.config.host
    cached = _ka_api_versions.get(host)

    if cached == "2.0":
        return w.api_client.do(method, f"/api/2.0/{path}", body=body)

    try:
        result = w.api_client.do(method, f"/api/2.1/{path}", body=body)
        _ka_api_versions[host] = "2.1"
        return result
    except Exception as e21:
        try:
            result = w.api_client.do(method, f"/api/2.0/{path}", body=body)
            _ka_api_versions[host] = "2.0"
            return result
        except Exception:
            # Both versions failed — raise the 2.1 error (more likely
            # to contain the real problem, e.g. missing volume)
            raise e21


# ---------------------------------------------------------------------------
# Databricks notebook detection
# ---------------------------------------------------------------------------

def get_dbutils():
    """Return dbutils if running inside a Databricks notebook, else None."""
    try:
        import IPython

        return IPython.get_ipython().user_ns.get("dbutils")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# UC path remapping (extracted from deploy_ka.py)
# ---------------------------------------------------------------------------

def remap_volume_path(original_path: str, catalog: str, schema: str) -> str:
    """Remap a UC volume path to the target catalog and schema.

    /Volumes/<src_catalog>/<src_schema>/<volume>/...
    becomes
    /Volumes/<catalog>/<schema>/<volume>/...
    """
    parts = original_path.split("/")
    for i, part in enumerate(parts):
        if part == "Volumes" and i + 2 < len(parts):
            parts[i + 1] = catalog
            parts[i + 2] = schema
            break
    return "/".join(parts)


def remap_table_name(original_name: str, catalog: str, schema: str) -> str:
    """Remap a three-level UC table name to the target catalog and schema.

    <src_catalog>.<src_schema>.<table>  ->  <catalog>.<schema>.<table>
    """
    dot_parts = original_name.split(".")
    if len(dot_parts) >= 3:
        dot_parts[0] = catalog
        dot_parts[1] = schema
    return ".".join(dot_parts)


def remap_path(original_path: str, catalog: str, schema: str) -> str:
    """Dispatch to the correct remapper based on path format."""
    if "/Volumes/" in original_path:
        return remap_volume_path(original_path, catalog, schema)
    if "." in original_path:
        return remap_table_name(original_path, catalog, schema)
    return original_path


# ---------------------------------------------------------------------------
# Volume file copying (cross-workspace)
# ---------------------------------------------------------------------------

def copy_volume_files(source_client, target_client, source_path, target_catalog, target_schema):
    """Create target volume (if needed) and copy files from source volume path.

    Parameters
    ----------
    source_client : WorkspaceClient
        Client for the source workspace.
    target_client : WorkspaceClient
        Client for the target workspace.
    source_path : str
        UC Volume path in source, e.g. /Volumes/cat/schema/vol_name/subpath.
    target_catalog : str
        Target catalog name.
    target_schema : str
        Target schema name.
    """
    # Parse source path: /Volumes/<cat>/<schema>/<volume_name>/...
    parts = source_path.strip("/").split("/")
    # parts = ["Volumes", cat, schema, volume_name, ...]
    volume_name = parts[3]

    # Create volume in target (idempotent — ignore if exists)
    try:
        target_client.volumes.create(
            catalog_name=target_catalog,
            schema_name=target_schema,
            name=volume_name,
            volume_type=VolumeType.MANAGED,
        )
    except Exception:
        pass  # Already exists

    # Build target path by remapping catalog/schema
    target_path = remap_volume_path(source_path, target_catalog, target_schema)

    # List files in source path, download and upload each
    for file_info in source_client.files.list_directory_contents(source_path):
        if file_info.is_directory:
            continue
        src_file_path = file_info.path
        # Compute corresponding target file path
        relative = src_file_path[len(source_path):]
        tgt_file_path = target_path + relative

        resp = source_client.files.download(src_file_path)
        content = resp.contents.read()
        target_client.files.upload(tgt_file_path, io.BytesIO(content), overwrite=True)
        print(f"    Copied: {src_file_path} → {tgt_file_path}")


# ---------------------------------------------------------------------------
# CSV I/O (works on both local filesystem and UC Volumes)
# ---------------------------------------------------------------------------

def read_csv(path: str) -> list[dict]:
    """Read a CSV file and return a list of row dicts."""
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

def timestamp_now() -> str:
    """Return the current UTC time in ISO 8601 format."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# SparkSession helper
# ---------------------------------------------------------------------------

def get_spark():
    """Return the active SparkSession, or None if running locally."""
    try:
        from pyspark.sql import SparkSession

        return SparkSession.builder.getOrCreate()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Delta table status tracking
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table_name} (
    run_id STRING,
    agent_type STRING,
    agent_id STRING,
    target_catalog STRING,
    target_schema STRING,
    display_name_override STRING,
    skip_tests STRING,
    status STRING,
    status_desc STRING,
    test_status STRING,
    test_status_desc STRING,
    updated_at TIMESTAMP
)
"""


def init_deployment_table(
    spark,
    table_name: str,
    rows: list[dict],
    run_id: str,
    default_catalog: str,
    default_schema: str,
) -> None:
    """Create the status table (if needed) and insert Pending rows."""
    spark.sql(_CREATE_TABLE_SQL.format(table_name=table_name))

    # Migrate old column names if table existed before the rename
    try:
        cols = [c.name for c in spark.table(table_name).schema]
        if "error_details" in cols and "status_desc" not in cols:
            spark.sql(f"ALTER TABLE {table_name} RENAME COLUMN error_details TO status_desc")
        if "test_error_details" in cols and "test_status_desc" not in cols:
            spark.sql(f"ALTER TABLE {table_name} RENAME COLUMN test_error_details TO test_status_desc")
    except Exception:
        pass

    for row in rows:
        target_catalog = row.get("target_catalog", "").strip() or default_catalog
        target_schema = row.get("target_schema", "").strip() or default_schema
        display_name = row.get("display_name_override", "").strip() or ""
        skip_tests = row.get("skip_tests", "").strip()

        spark.sql(
            f"""
            INSERT INTO {table_name}
            VALUES (
                '{run_id}',
                '{row.get("agent_type", "KA")}',
                '{row["agent_id"]}',
                '{target_catalog}',
                '{target_schema}',
                '{display_name}',
                '{skip_tests}',
                'Pending',
                '',
                'Pending',
                '',
                current_timestamp()
            )
            """
        )


def update_row_status(
    spark,
    table_name: str,
    run_id: str,
    agent_id: str,
    status: str,
    status_desc: str = "",
) -> None:
    """Update the deployment status for a single row."""
    escaped_error = status_desc.replace("'", "\\'")
    spark.sql(
        f"""
        UPDATE {table_name}
        SET status = '{status}',
            status_desc = '{escaped_error}',
            updated_at = current_timestamp()
        WHERE run_id = '{run_id}' AND agent_id = '{agent_id}'
        """
    )


def update_row_test_status(
    spark,
    table_name: str,
    run_id: str,
    agent_id: str,
    test_status: str,
    test_status_desc: str = "",
) -> None:
    """Update the test status for a single row."""
    escaped_error = test_status_desc.replace("'", "\\'")
    spark.sql(
        f"""
        UPDATE {table_name}
        SET test_status = '{test_status}',
            test_status_desc = '{escaped_error}',
            updated_at = current_timestamp()
        WHERE run_id = '{run_id}' AND agent_id = '{agent_id}'
        """
    )
