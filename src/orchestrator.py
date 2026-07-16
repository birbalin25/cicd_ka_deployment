# Databricks notebook source
"""CSV-driven batch orchestrator for Knowledge Assistant deployment.

Reads an input CSV (synced as a workspace file by `databricks bundle deploy`),
loads rows into a Delta status table, deploys each KA, and tracks all
status transitions in the Delta table.

Supports cross-workspace deployment: exports from source_host and
deploys to the current workspace (or target configured via env).

Usage (Databricks notebook — invoked by the DAB job):
    Widgets: catalog, schema, status_table_name,
             source_host, source_token

Usage (local):
    export DATABRICKS_HOST=https://target-workspace.cloud.databricks.com
    export DATABRICKS_TOKEN=<target-token>
    python src/orchestrator.py \
        --catalog prod_catalog --schema prod_schema \
        --source-host https://source-workspace.cloud.databricks.com \
        --source-token <source-token>
"""

import argparse
import os
import time
import traceback
import uuid

from databricks.sdk import WorkspaceClient

from common import (
    build_source_client,
    check_ka_active,
    copy_volume_files,
    get_dbutils,
    get_job_context,
    get_spark,
    init_deployment_table,
    ka_api_call,
    read_csv,
    remap_path,
    remap_volume_path,
    update_row_copied_examples,
    update_row_deploy_result,
    update_row_deploy_started,
    update_row_test_status,
)
from export_ka import export_knowledge_assistant
from deploy_ka import (
    deploy_assistant as deploy_ka_assistant,
    deploy_knowledge_sources,
    find_existing_assistant,
)
from test_runner import run_tests


# ---------------------------------------------------------------------------
# Parameter resolution
# ---------------------------------------------------------------------------

def _resolve_params() -> dict:
    """Return deployment parameters from notebook widgets or CLI args.

    Source workspace credentials are read from a Databricks secret scope
    (configured via secret_scope parameter). For local CLI usage,
    credentials can be passed directly via args or env vars.
    """
    dbutils = get_dbutils()
    if dbutils is not None:
        return {
            "catalog": dbutils.widgets.get("catalog"),
            "schema": dbutils.widgets.get("schema"),
            "status_table_name": dbutils.widgets.get("status_table_name"),
            "source_host": dbutils.widgets.get("source_host"),
            "secret_scope": dbutils.widgets.get("secret_scope"),
            "wait_and_copy_examples": dbutils.widgets.get("wait_and_copy_examples"),
            "deploy_wait_minutes": dbutils.widgets.get("deploy_wait_minutes"),
        }

    parser = argparse.ArgumentParser(description="Batch deploy KAs from CSV")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--status-table-name", default="",
                        help="Fully qualified Delta table for status tracking")
    parser.add_argument("--source-host", default=None,
                        help="Source workspace URL (default: same as target)")
    parser.add_argument("--secret-scope", default="",
                        help="Databricks secret scope for source credentials")
    parser.add_argument("--source-token", default=None,
                        help="Source workspace PAT (local CLI only)")
    parser.add_argument("--source-client-id", default=None,
                        help="Source workspace SP client ID (local CLI only)")
    parser.add_argument("--source-client-secret", default=None,
                        help="Source workspace SP client secret (local CLI only)")
    parser.add_argument("--wait-and-copy-examples", default="false",
                        help="Wait for KA ACTIVE and copy examples inline (default: false)")
    parser.add_argument("--deploy-wait-minutes", default="40",
                        help="Minutes to wait per KA for ACTIVE state when "
                             "wait-and-copy-examples is true (default: 40)")
    args = parser.parse_args()
    return {
        "catalog": args.catalog,
        "schema": args.schema,
        "status_table_name": args.status_table_name,
        "source_host": args.source_host,
        "secret_scope": args.secret_scope,
        "source_token": args.source_token,
        "source_client_id": args.source_client_id,
        "source_client_secret": args.source_client_secret,
        "wait_and_copy_examples": args.wait_and_copy_examples,
        "deploy_wait_minutes": args.deploy_wait_minutes,
    }


# ---------------------------------------------------------------------------
# Single-agent deployment wrapper
# ---------------------------------------------------------------------------

def _is_same_volume_path(source_client, target_client, source_path, catalog, schema):
    """Check if source and target volume paths resolve to the same location."""
    source_host = source_client.config.host.rstrip("/")
    target_host = target_client.config.host.rstrip("/")
    if source_host != target_host:
        return False
    target_path = remap_volume_path(source_path, catalog, schema)
    return source_path.rstrip("/") == target_path.rstrip("/")


def _deploy_single_ka(
    source_client: WorkspaceClient,
    target_client: WorkspaceClient,
    agent_id: str,
    catalog: str,
    schema: str,
    display_name_override: str | None,
    copy_volumes: bool = False,
) -> tuple[str, str, int, str]:
    """Export and deploy a single KA.

    Returns (ka_name, status_description, source_example_count, source_display_name).
    """
    status_parts = []

    # Export from source
    config = export_knowledge_assistant(source_client, agent_id)
    source_display_name = config.get("display_name", "")

    if display_name_override:
        config["display_name"] = display_name_override

    # Count examples in source
    source_example_count = len(config.get("examples", []))

    # Pre-flight: verify all knowledge source dependencies exist on target
    file_sources = [
        s for s in config.get("knowledge_sources", [])
        if "files_path" in s
    ]
    index_sources = [
        s for s in config.get("knowledge_sources", [])
        if "index_name" in s
    ]

    # Check index sources exist on target
    for src in index_sources:
        remapped = remap_path(src["index_name"], catalog, schema)
        try:
            target_client.vector_search_indexes.get_index(index_name=remapped)
        except Exception:
            raise RuntimeError(
                f"Vector search index '{remapped}' does not exist on target workspace. "
                f"Create the index before deploying this KA."
            )

    # Check file-based sources: copy or verify volumes exist
    if copy_volumes and file_sources:
        for src in file_sources:
            if _is_same_volume_path(source_client, target_client, src["files_path"], catalog, schema):
                print(f"  Volume copy skipped (same workspace and path): {src['files_path']}")
                status_parts.append(f"Volume copy skipped (same workspace and path): {src['files_path']}")
            else:
                print(f"  Copying volume files for: {src['files_path']}")
                result = copy_volume_files(
                    source_client, target_client,
                    src["files_path"], catalog, schema,
                )
                status_parts.append(
                    f"Volume copied: {result['file_count']} file(s) in {result['elapsed_seconds']}s, "
                    f"{src['files_path']} -> {result['target_path']}"
                )
    elif file_sources:
        for src in file_sources:
            target_path = remap_volume_path(src["files_path"], catalog, schema)
            parts = target_path.strip("/").split("/")
            vol_catalog, vol_schema, vol_name = parts[1], parts[2], parts[3]
            try:
                target_client.volumes.read(
                    catalog_name=vol_catalog,
                    schema_name=vol_schema,
                    name=vol_name,
                )
            except Exception:
                raise RuntimeError(
                    f"Volume '{vol_catalog}.{vol_schema}.{vol_name}' does not exist "
                    f"on target workspace and copy_volumes is false. "
                    f"Set copy_volumes=true in agents_input.csv or create the volume manually."
                )

    # Deploy to target
    ka_name = deploy_ka_assistant(target_client, config)
    ka_id = ka_name.split("/")[-1] if "/" in ka_name else ka_name
    print(f"  KA deployed: {ka_name}")
    status_parts.append(f"KA created: {config['display_name']}")

    deploy_knowledge_sources(
        target_client, ka_name,
        config.get("knowledge_sources", []),
        catalog, schema,
    )
    status_parts.append(f"Knowledge sources: {len(config.get('knowledge_sources', []))}")

    # Sync file-based sources (fire and forget — don't wait)
    has_file_sources = any(
        "files_path" in s for s in config.get("knowledge_sources", [])
    )
    if has_file_sources:
        ka_api_call(target_client, "POST", f"{ka_name}/knowledge-sources:sync")
        print("  File source sync triggered (running in background).")
        status_parts.append("File sync: triggered (background)")

    # Record example info (examples are copied by the separate copier job)
    if source_example_count > 0:
        status_parts.append(f"Examples: {source_example_count} in source (copy deferred to examples job)")
    else:
        status_parts.append("Examples: none in source")

    # Build UI link
    host = target_client.config.host.rstrip("/")
    ui_link = f"{host}/ml/bricks/ka/configure/{ka_id}"
    status_parts.append(f"UI: {ui_link}")

    status_msg = " | ".join(status_parts)
    print(f"  {status_msg}")
    return ka_name, status_msg, source_example_count, source_display_name


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------

def main() -> None:
    params = _resolve_params()
    catalog = params["catalog"]
    schema = params["schema"]
    wait_and_copy = (params.get("wait_and_copy_examples") or "").strip().lower() == "true"

    # Minutes to wait per KA for ACTIVE state (inline copy). Default 40.
    try:
        deploy_wait_minutes = int(float((params.get("deploy_wait_minutes") or "40").strip()))
    except (ValueError, TypeError):
        deploy_wait_minutes = 40
    deploy_wait_secs = max(0, deploy_wait_minutes * 60)

    # Build status table name: use explicit param or default convention
    status_table_name = (params.get("status_table_name") or "").strip()
    if not status_table_name:
        status_table_name = f"{catalog}.{schema}.ka_deployment_status"

    # Build workspace clients
    source_client = build_source_client(
        source_host=params.get("source_host"),
        secret_scope=params.get("secret_scope", ""),
        source_token=params.get("source_token"),
        source_client_id=params.get("source_client_id"),
        source_client_secret=params.get("source_client_secret"),
    )
    target_client = WorkspaceClient()

    # Capture workspace hosts
    source_host = (source_client.config.host or "").rstrip("/")
    target_host = (target_client.config.host or "").rstrip("/")

    # Read input CSV from bundle workspace files (relative to this notebook)
    try:
        notebook_dir = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        # __file__ is not defined inside Databricks notebooks.
        # Derive path from the notebook context instead.
        dbutils = get_dbutils()
        nb_path = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
        notebook_dir = "/Workspace" + os.path.dirname(nb_path)
    csv_path = os.path.join(notebook_dir, "..", "configs", "agents_input.csv")
    rows = read_csv(csv_path)
    ka_rows = [r for r in rows if r.get("agent_type", "").upper() == "KA"]
    print(f"Loaded {len(ka_rows)} Knowledge Assistant(s) from {csv_path}")

    # Initialize Delta status tracking
    run_id = str(uuid.uuid4())
    job_id, job_run_id = get_job_context()
    spark = get_spark()
    if spark is None:
        raise RuntimeError(
            "SparkSession not available. This notebook must run on Databricks."
        )
    init_deployment_table(
        spark, status_table_name, ka_rows, run_id,
        catalog, schema,
        job_id=job_id, job_run_id=job_run_id,
        source_host=source_host, target_host=target_host,
    )
    print(f"Status tracking: {status_table_name}  (run_id={run_id})")

    print(f"\n{'='*60}")
    print(f"Deploying {len(ka_rows)} Knowledge Assistant(s)")
    print(f"{'='*60}")

    for row in ka_rows:
        agent_id = row["agent_id"]
        row_catalog = row.get("target_catalog", "").strip() or catalog
        row_schema = row.get("target_schema", "").strip() or schema
        display_override = row.get("display_name_override", "").strip() or None
        skip_tests = row.get("skip_tests", "").strip().lower() == "true"
        copy_volumes = row.get("copy_volumes", "").strip().lower() == "true"
        replace_ka = row.get("replace_KA", "").strip().lower() == "true"

        # Fetch source KA info early so it's available for all paths
        src_display_name = ""
        source_example_count = 0
        try:
            src_config = export_knowledge_assistant(source_client, agent_id)
            src_display_name = src_config.get("display_name", "")
            source_example_count = len(src_config.get("examples", []))
        except Exception:
            pass

        if not replace_ka:
            target_display_name = display_override or src_display_name
            existing = find_existing_assistant(target_client, target_display_name) if target_display_name else None
            if existing:
                skip_msg = (
                    f"Skipped — KA '{target_display_name}' already exists on target workspace "
                    f"and replace_KA is set to false in agents_input.csv."
                )
                print(f"\nSkipping KA {agent_id}: {skip_msg}")
                update_row_deploy_result(
                    spark, status_table_name, run_id, agent_id,
                    "Skipped", skip_msg,
                    source_display_name=src_display_name,
                    source_example_count=source_example_count,
                )
                update_row_test_status(
                    spark, status_table_name, run_id, agent_id,
                    "N/A", "Deploy skipped — tests not applicable",
                )
                continue
            else:
                print(f"\n  KA '{target_display_name}' not found on target — proceeding with migration (replace_KA=false).")

        print(f"\nDeploying KA {agent_id} ...")
        update_row_deploy_started(spark, status_table_name, run_id, agent_id)

        try:
            ka_name, status_msg, example_count, deploy_display_name = _deploy_single_ka(
                source_client, target_client,
                agent_id, row_catalog, row_schema,
                display_override,
                copy_volumes=copy_volumes,
            )
            deployed_id = ka_name.split("/")[-1] if "/" in ka_name else ka_name

            update_row_deploy_result(
                spark, status_table_name, run_id, agent_id,
                "Success", status_msg,
                target_ka_name=ka_name,
                source_display_name=deploy_display_name or src_display_name,
                source_example_count=example_count,
            )

            # Run tests unless skipped
            if not skip_tests:
                result = run_tests(target_client, "KA", deployed_id)
                update_row_test_status(
                    spark, status_table_name, run_id, agent_id,
                    result["test_status"],
                    result.get("status_desc", ""),
                )
            else:
                update_row_test_status(
                    spark, status_table_name, run_id, agent_id, "Skipped"
                )

        except Exception as ex:
            print(f"  ERROR deploying KA {agent_id}: {ex}")
            # traceback.print_exc()
            update_row_deploy_result(
                spark, status_table_name, run_id, agent_id,
                "Failed", str(ex),
                source_display_name=src_display_name,
                source_example_count=source_example_count,
            )
            update_row_test_status(
                spark, status_table_name, run_id, agent_id,
                "N/A", "Deploy failed — tests not applicable",
            )
            continue

    # Print Phase 1 summary
    summary_df = spark.sql(
        f"""
        SELECT status, count(*) as cnt
        FROM {status_table_name}
        WHERE run_id = '{run_id}'
        GROUP BY status
        """
    )
    print(f"\n{'='*60}")
    print(f"Phase 1 — Deployment complete  (run_id={run_id})")
    print(f"{'='*60}")
    for row in summary_df.collect():
        print(f"  {row['status']}: {row['cnt']}")

    # ------------------------------------------------------------------
    # Phase 2: Copy examples inline (only if wait_and_copy_examples=true)
    # ------------------------------------------------------------------
    if not wait_and_copy:
        print(f"\nwait_and_copy_examples=false — examples copy deferred to Copy KA Examples job.")
    else:
        pending_rows = spark.sql(
            f"""
            SELECT agent_id, target_ka_name, source_display_name,
                   source_example_count
            FROM {status_table_name}
            WHERE run_id = '{run_id}' AND copied_examples = 'Pending'
            """
        ).collect()

        if not pending_rows:
            print(f"\nNo KAs with pending examples to copy.")
        else:
            print(f"\n{'='*60}")
            print(f"Phase 2 — Copying examples for {len(pending_rows)} KA(s) (wait_and_copy_examples=true)")
            print(f"{'='*60}")

            for prow in pending_rows:
                agent_id = prow["agent_id"]
                target_ka_name = prow["target_ka_name"]
                src_example_count = prow["source_example_count"]
                ka_id = target_ka_name.split("/")[-1] if "/" in target_ka_name else target_ka_name

                print(f"\n  Waiting for KA {ka_id} to become ACTIVE (max {deploy_wait_minutes} min)...")

                try:
                    is_active, ka_state, wait_secs, checks = check_ka_active(
                        target_client, ka_id, max_wait=deploy_wait_secs, poll_interval=30
                    )

                    if not is_active:
                        msg = (
                            f"KA not ready: state={ka_state} after {wait_secs}s ({checks} checks). "
                            f"Use Copy KA Examples job to retry later."
                        )
                        print(f"  {msg}")
                        update_row_copied_examples(
                            spark, status_table_name, run_id, agent_id,
                            f"Failed: {msg} Job_id={job_id}, Job_run_id={job_run_id}",
                        )
                        continue

                    print(f"  KA ACTIVE after {wait_secs}s (check {checks}). Copying examples...")

                    resp = ka_api_call(
                        source_client, "GET",
                        f"knowledge-assistants/{agent_id}/examples",
                    )
                    examples = resp.get("examples", [])
                    print(f"  Fetched {len(examples)} example(s) from source")

                    added = 0
                    failed_examples = []
                    t0 = time.time()
                    for ex in examples:
                        try:
                            ka_api_call(target_client, "POST", f"{target_ka_name}/examples", body={
                                "question": ex.get("question", ""),
                                "guidelines": ex.get("guidelines", []),
                            })
                            added += 1
                        except Exception as e:
                            failed_examples.append(str(e))
                    elapsed = round(time.time() - t0, 1)

                    # Validate
                    try:
                        val_resp = ka_api_call(
                            target_client, "GET", f"{target_ka_name}/examples",
                        )
                        target_count = len(val_resp.get("examples", []))
                    except Exception:
                        target_count = added

                    if target_count >= src_example_count:
                        msg = (
                            f"Examples copied successfully. "
                            f"Copied {added}/{len(examples)} in {elapsed}s, "
                            f"validated {target_count} on target. "
                            f"Job_id={job_id}, Job_run_id={job_run_id}"
                        )
                    else:
                        msg = (
                            f"Partial copy ({target_count}/{src_example_count}). "
                            f"Copied {added}/{len(examples)} in {elapsed}s. "
                            f"Job_id={job_id}, Job_run_id={job_run_id}"
                        )
                        if failed_examples:
                            msg += f" Errors: {'; '.join(failed_examples[:3])}"

                    print(f"  {msg}")
                    update_row_copied_examples(
                        spark, status_table_name, run_id, agent_id, msg,
                    )

                except Exception as ex:
                    msg = f"Failed: {ex}. Job_id={job_id}, Job_run_id={job_run_id}"
                    print(f"  {msg}")
                    update_row_copied_examples(
                        spark, status_table_name, run_id, agent_id, msg,
                    )

            print(f"\n{'='*60}")
            print(f"Phase 2 — Examples copy complete")
            print(f"{'='*60}")


if __name__ == "__main__":
    main()
