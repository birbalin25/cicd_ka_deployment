"""Shared utilities for agent export/deploy scripts.

Provides common functions for dbutils detection, UC path remapping,
CSV I/O, Delta table status tracking, and timestamp generation used
across KA and Supervisor agent deployment workflows.
"""

import csv
import io
import os
import time
from datetime import datetime, timezone

from databricks.sdk import WorkspaceClient
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

    # Some sub-endpoints only exist on one version, so always try both
    # with fallback. The cache just decides which to try first.
    first, second = ("2.0", "2.1") if cached == "2.0" else ("2.1", "2.0")

    try:
        result = w.api_client.do(method, f"/api/{first}/{path}", body=body)
        _ka_api_versions[host] = first
        return result
    except Exception as e_first:
        try:
            result = w.api_client.do(method, f"/api/{second}/{path}", body=body)
            return result
        except Exception:
            raise e_first


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
# Job context
# ---------------------------------------------------------------------------

def get_job_context() -> tuple[str, str]:
    """Return (job_id, job_run_id) from the Databricks job context.

    Tries dbutils notebook context first (works on serverless and classic),
    then falls back to Spark conf. Returns ("local", "local") when running
    outside a Databricks job.
    """
    dbutils = get_dbutils()
    if dbutils is not None:
        try:
            ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
            job_id = ctx.jobId().get()
            job_run_id = ctx.idInJob().get()
            if job_id and job_run_id:
                return str(job_id), str(job_run_id)
        except Exception:
            pass

    spark = get_spark()
    if spark is not None:
        try:
            job_id = spark.conf.get("spark.databricks.job.id", "")
            job_run_id = spark.conf.get("spark.databricks.job.runId", "")
            if job_id and job_run_id:
                return job_id, job_run_id
        except Exception:
            pass
    return "local", "local"


# ---------------------------------------------------------------------------
# Source workspace client builder
# ---------------------------------------------------------------------------

def build_source_client(
    source_host: str | None,
    source_token: str | None,
    source_client_id: str | None,
    source_client_secret: str | None,
) -> WorkspaceClient:
    """Build a WorkspaceClient for the source workspace.

    If source_host is provided, creates a separate client.
    Auth priority: service principal (client_id + client_secret) first;
    if SP fails connectivity check, falls back to PAT (source_token).
    If source_host is blank, returns default client (same workspace).
    """
    if not source_host:
        return WorkspaceClient()

    if source_client_id and source_client_secret:
        try:
            sp_client = WorkspaceClient(
                host=source_host,
                client_id=source_client_id,
                client_secret=source_client_secret,
            )
            sp_client.current_user.me()
            print(f"  Source auth: service principal OK")
            return sp_client
        except Exception as ex:
            print(f"  Source auth: SP failed ({ex}), falling back to PAT...")

    token = source_token or os.environ.get("SOURCE_DATABRICKS_TOKEN", "")
    if not token:
        raise RuntimeError(
            "Source workspace authentication failed. "
            "Set SOURCE_TOKEN (PAT) or SOURCE_CLIENT_ID + SOURCE_CLIENT_SECRET (SP)."
        )
    pat_client = WorkspaceClient(host=source_host, token=token)
    print(f"  Source auth: PAT OK")
    return pat_client


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

    Returns dict with file_count, elapsed_seconds, target_path.
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
    file_count = 0
    t0 = time.time()
    for file_info in source_client.files.list_directory_contents(source_path):
        if file_info.is_directory:
            continue
        src_file_path = file_info.path
        relative = src_file_path[len(source_path):]
        tgt_file_path = target_path + relative

        resp = source_client.files.download(src_file_path)
        content = resp.contents.read()
        target_client.files.upload(tgt_file_path, io.BytesIO(content), overwrite=True)
        file_count += 1
        print(f"    Copied: {src_file_path} → {tgt_file_path}")

    elapsed = round(time.time() - t0, 1)
    return {"file_count": file_count, "elapsed_seconds": elapsed, "target_path": target_path}


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


def _escape_sql(value: str) -> str:
    """Escape single quotes for safe SQL string interpolation."""
    return value.replace("'", "\\'")


# ---------------------------------------------------------------------------
# Delta table: ka_deployment_status (owned by deploy job)
# ---------------------------------------------------------------------------

_CREATE_DEPLOY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table_name} (
    run_id STRING,
    job_id STRING,
    job_run_id STRING,
    agent_type STRING,
    agent_id STRING,
    target_ka_name STRING,
    source_display_name STRING,
    source_example_count INT,
    source_host STRING,
    target_host STRING,
    target_catalog STRING,
    target_schema STRING,
    display_name_override STRING,
    skip_tests STRING,
    status STRING,
    status_desc STRING,
    test_status STRING,
    test_status_desc STRING,
    completed_at TIMESTAMP
)
"""

_DEPLOY_NEW_COLUMNS = [
    ("job_id", "STRING"),
    ("job_run_id", "STRING"),
    ("target_ka_name", "STRING"),
    ("source_display_name", "STRING"),
    ("source_example_count", "INT"),
    ("source_host", "STRING"),
    ("target_host", "STRING"),
    ("completed_at", "TIMESTAMP"),
]


def init_deployment_table(
    spark,
    table_name: str,
    rows: list[dict],
    run_id: str,
    default_catalog: str,
    default_schema: str,
    job_id: str = "",
    job_run_id: str = "",
    source_host: str = "",
    target_host: str = "",
) -> None:
    """Create the status table (if needed) and insert Pending rows."""
    spark.sql(_CREATE_DEPLOY_TABLE_SQL.format(table_name=table_name))

    # Migrate old column names if table existed before the rename
    try:
        cols = [c.name for c in spark.table(table_name).schema]
        if "error_details" in cols and "status_desc" not in cols:
            spark.sql(f"ALTER TABLE {table_name} RENAME COLUMN error_details TO status_desc")
        if "test_error_details" in cols and "test_status_desc" not in cols:
            spark.sql(f"ALTER TABLE {table_name} RENAME COLUMN test_error_details TO test_status_desc")
    except Exception:
        pass

    # Add new columns to existing tables
    try:
        cols = [c.name for c in spark.table(table_name).schema]
        for col_name, col_type in _DEPLOY_NEW_COLUMNS:
            if col_name not in cols:
                try:
                    spark.sql(f"ALTER TABLE {table_name} ADD COLUMN {col_name} {col_type}")
                except Exception:
                    pass
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
            (run_id, job_id, job_run_id, agent_type, agent_id,
             target_ka_name, source_display_name, source_example_count, source_host, target_host,
             target_catalog, target_schema, display_name_override, skip_tests,
             status, status_desc, test_status, test_status_desc,
             completed_at)
            VALUES (
                '{run_id}',
                '{job_id}',
                '{job_run_id}',
                '{row.get("agent_type", "KA")}',
                '{row["agent_id"]}',
                '',
                '',
                0,
                '{_escape_sql(source_host)}',
                '{_escape_sql(target_host)}',
                '{target_catalog}',
                '{target_schema}',
                '{_escape_sql(display_name)}',
                '{skip_tests}',
                'Pending',
                '',
                'Pending',
                '',
                NULL
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
    spark.sql(
        f"""
        UPDATE {table_name}
        SET status = '{status}',
            status_desc = '{_escape_sql(status_desc)}'
        WHERE run_id = '{run_id}' AND agent_id = '{agent_id}'
        """
    )


def update_row_deploy_started(
    spark, table_name: str, run_id: str, agent_id: str
) -> None:
    """Mark a row as Deploying."""
    spark.sql(
        f"""
        UPDATE {table_name}
        SET status = 'Deploying'
        WHERE run_id = '{run_id}' AND agent_id = '{agent_id}'
        """
    )


def update_row_deploy_result(
    spark,
    table_name: str,
    run_id: str,
    agent_id: str,
    status: str,
    status_desc: str,
    target_ka_name: str = "",
    source_display_name: str = "",
    source_example_count: int = 0,
) -> None:
    """Update a row with final deploy result including target KA info."""
    spark.sql(
        f"""
        UPDATE {table_name}
        SET status = '{status}',
            status_desc = '{_escape_sql(status_desc)}',
            target_ka_name = '{_escape_sql(target_ka_name)}',
            source_display_name = '{_escape_sql(source_display_name)}',
            source_example_count = {source_example_count},
            completed_at = current_timestamp()
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
    spark.sql(
        f"""
        UPDATE {table_name}
        SET test_status = '{test_status}',
            test_status_desc = '{_escape_sql(test_status_desc)}'
        WHERE run_id = '{run_id}' AND agent_id = '{agent_id}'
        """
    )


# ---------------------------------------------------------------------------
# Delta table: ka_examples_status (owned by copier job)
# ---------------------------------------------------------------------------

_CREATE_EXAMPLES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS {table_name} (
    run_id STRING,
    job_id STRING,
    job_run_id STRING,
    agent_id STRING,
    target_ka_name STRING,
    display_name STRING,
    source_host STRING,
    target_host STRING,
    source_example_count INT,
    target_example_count INT,
    copy_status STRING,
    copy_details STRING,
    created_at TIMESTAMP,
    copied_at TIMESTAMP,
    updated_at TIMESTAMP
)
"""


def init_examples_table(spark, table_name: str) -> None:
    """Create the examples status table if it doesn't exist."""
    spark.sql(_CREATE_EXAMPLES_TABLE_SQL.format(table_name=table_name))


def insert_examples_row(
    spark,
    table_name: str,
    run_id: str,
    job_id: str,
    job_run_id: str,
    agent_id: str,
    target_ka_name: str,
    display_name: str,
    source_host: str,
    target_host: str,
    source_example_count: int,
) -> None:
    """Insert a Pending row into the examples status table."""
    spark.sql(
        f"""
        INSERT INTO {table_name}
        (run_id, job_id, job_run_id, agent_id, target_ka_name,
         display_name, source_host, target_host,
         source_example_count, target_example_count,
         copy_status, copy_details,
         created_at, copied_at, updated_at)
        VALUES (
            '{run_id}',
            '{job_id}',
            '{job_run_id}',
            '{agent_id}',
            '{_escape_sql(target_ka_name)}',
            '{_escape_sql(display_name)}',
            '{_escape_sql(source_host)}',
            '{_escape_sql(target_host)}',
            {source_example_count},
            0,
            'Pending',
            '',
            current_timestamp(),
            NULL,
            current_timestamp()
        )
        """
    )


def update_examples_copy(
    spark,
    table_name: str,
    run_id: str,
    agent_id: str,
    copy_status: str,
    copy_details: str,
    target_example_count: int = 0,
    set_copied_at: bool = False,
) -> None:
    """Update the copy result for an examples row."""
    copied_at_clause = "copied_at = current_timestamp()," if set_copied_at else ""
    spark.sql(
        f"""
        UPDATE {table_name}
        SET copy_status = '{copy_status}',
            copy_details = '{_escape_sql(copy_details)}',
            target_example_count = {target_example_count},
            {copied_at_clause}
            updated_at = current_timestamp()
        WHERE run_id = '{run_id}' AND agent_id = '{agent_id}'
        """
    )
