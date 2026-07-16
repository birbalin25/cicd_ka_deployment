# Databricks notebook source
"""Copy example questions to deployed Knowledge Assistants.

Reads ka_deployment_status to find successfully deployed KAs with
examples, then copies examples from source to target. Tracks progress
in its own ka_examples_status table.

Run this job ~1 hour after the deploy job, once files sync completes
and KA state transitions to ACTIVE.

Usage (Databricks notebook — invoked by the DAB job):
    Widgets: catalog, schema, status_table_name,
             source_host, secret_scope, run_id, since_timestamp

    - run_id (optional): deploy run to process (default: latest run).
    - since_timestamp (optional): if set, process pending rows across all
      runs whose completed_at is newer than this timestamp; overrides run_id.

Usage (local):
    export DATABRICKS_HOST=https://target-workspace.cloud.databricks.com
    export DATABRICKS_TOKEN=<target-token>
    python src/copy_examples.py \
        --catalog prod_catalog --schema prod_schema \
        --source-host https://source-workspace.cloud.databricks.com \
        --source-token <source-token>
"""

import argparse
import time

from databricks.sdk import WorkspaceClient

from common import (
    build_source_client,
    check_ka_active,
    get_dbutils,
    get_job_context,
    get_spark,
    init_examples_table,
    insert_examples_row,
    ka_api_call,
    update_examples_copy,
    update_row_copied_examples,
)


# ---------------------------------------------------------------------------
# Parameter resolution
# ---------------------------------------------------------------------------

def _resolve_params() -> dict:
    dbutils = get_dbutils()
    if dbutils is not None:
        return {
            "catalog": dbutils.widgets.get("catalog"),
            "schema": dbutils.widgets.get("schema"),
            "status_table_name": dbutils.widgets.get("status_table_name"),
            "source_host": dbutils.widgets.get("source_host"),
            "secret_scope": dbutils.widgets.get("secret_scope"),
            "run_id": dbutils.widgets.get("run_id"),
            "since_timestamp": dbutils.widgets.get("since_timestamp"),
        }

    parser = argparse.ArgumentParser(description="Copy KA examples")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--status-table-name", default="")
    parser.add_argument("--source-host", default=None)
    parser.add_argument("--secret-scope", default="")
    parser.add_argument("--source-token", default=None,
                        help="Source workspace PAT (local CLI only)")
    parser.add_argument("--source-client-id", default=None,
                        help="Source workspace SP client ID (local CLI only)")
    parser.add_argument("--source-client-secret", default=None,
                        help="Source workspace SP client secret (local CLI only)")
    parser.add_argument("--run-id", default="",
                        help="Deploy run_id to process (default: latest)")
    parser.add_argument("--since-timestamp", default="",
                        help="Only process rows with completed_at newer than this "
                             "timestamp (e.g. '2026-07-15 00:00:00'). Overrides --run-id.")
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
        "run_id": args.run_id,
        "since_timestamp": args.since_timestamp,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    params = _resolve_params()
    catalog = params["catalog"]
    schema = params["schema"]

    deploy_table = (params.get("status_table_name") or "").strip()
    if not deploy_table:
        deploy_table = f"{catalog}.{schema}.ka_deployment_status"
    examples_table = f"{catalog}.{schema}.ka_examples_status"

    spark = get_spark()
    if spark is None:
        raise RuntimeError("SparkSession not available.")

    # Build workspace clients
    source_client = build_source_client(
        source_host=params.get("source_host"),
        secret_scope=params.get("secret_scope", ""),
        source_token=params.get("source_token"),
        source_client_id=params.get("source_client_id"),
        source_client_secret=params.get("source_client_secret"),
    )
    target_client = WorkspaceClient()

    source_host = (source_client.config.host or "").rstrip("/")
    target_host = (target_client.config.host or "").rstrip("/")
    job_id, job_run_id = get_job_context()

    # Init examples table
    init_examples_table(spark, examples_table)

    # Determine selection filter. A since_timestamp (if provided) takes
    # precedence over run_id: it selects pending rows across all runs whose
    # completed_at is strictly newer than the given timestamp.
    since_timestamp = (params.get("since_timestamp") or "").strip()

    if since_timestamp:
        safe_ts = since_timestamp.replace("'", "")
        print(f"Processing rows with completed_at > '{safe_ts}'")
        candidates = spark.sql(
            f"""
            SELECT run_id, agent_id, target_ka_name,
                   display_name_override, source_example_count,
                   source_host, target_host
            FROM {deploy_table}
            WHERE completed_at > TIMESTAMP '{safe_ts}'
              AND copied_examples = 'Pending'
            """
        ).collect()
    else:
        # Determine run_id filter
        run_id_filter = (params.get("run_id") or "").strip()
        if not run_id_filter:
            latest = spark.sql(
                f"SELECT run_id FROM {deploy_table} ORDER BY completed_at DESC LIMIT 1"
            ).collect()
            if not latest:
                print("No rows found in deploy status table.")
                return
            run_id_filter = latest[0]["run_id"]

        print(f"Processing run_id: {run_id_filter}")
        candidates = spark.sql(
            f"""
            SELECT run_id, agent_id, target_ka_name,
                   display_name_override, source_example_count,
                   source_host, target_host
            FROM {deploy_table}
            WHERE run_id = '{run_id_filter}'
              AND copied_examples = 'Pending'
            """
        ).collect()

    if not candidates:
        print("No KAs pending examples copy.")
        return

    print(f"Found {len(candidates)} KA(s) to process")
    print(f"{'='*60}")

    copied_count = 0
    skipped_count = 0
    failed_count = 0

    for row in candidates:
        row_run_id = row["run_id"]
        agent_id = row["agent_id"]
        target_ka_name = row["target_ka_name"]
        display_name = row["display_name_override"] or ""
        source_example_count = row["source_example_count"]
        ka_id = target_ka_name.split("/")[-1] if "/" in target_ka_name else target_ka_name

        print(f"\nProcessing KA {agent_id} (run_id={row_run_id}, target: {ka_id}) ...")

        # Insert Pending row into examples table
        insert_examples_row(
            spark, examples_table,
            run_id=row_run_id,
            job_id=job_id,
            job_run_id=job_run_id,
            agent_id=agent_id,
            target_ka_name=target_ka_name,
            display_name=display_name,
            source_host=source_host,
            target_host=target_host,
            source_example_count=source_example_count,
        )

        try:
            # Sanity check: is KA ACTIVE?
            is_active, ka_state, wait_secs, checks = check_ka_active(
                target_client, ka_id, max_wait=180, poll_interval=30
            )

            if not is_active:
                msg = (
                    f"KA not ready: state={ka_state} after {wait_secs}s ({checks} checks). "
                    f"Re-run this job after files sync completes."
                )
                print(f"  {msg}")
                update_examples_copy(
                    spark, examples_table, row_run_id, agent_id,
                    "Pending", msg,
                )
                skipped_count += 1
                continue

            # Fetch examples from source KA
            resp = ka_api_call(
                source_client, "GET",
                f"knowledge-assistants/{agent_id}/examples",
            )
            examples = resp.get("examples", [])
            print(f"  Fetched {len(examples)} example(s) from source")

            # POST each example to target KA
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

            # Validate: count examples on target
            try:
                val_resp = ka_api_call(
                    target_client, "GET",
                    f"{target_ka_name}/examples",
                )
                target_count = len(val_resp.get("examples", []))
            except Exception:
                target_count = added

            # Determine status
            if target_count >= source_example_count:
                status = "Copied"
                msg = (
                    f"Copied {added}/{len(examples)} examples in {elapsed}s. "
                    f"Validated: {target_count} on target. "
                    f"KA ACTIVE on check {checks}/{6}."
                )
            else:
                status = "Partial"
                msg = (
                    f"Copied {added}/{len(examples)} examples in {elapsed}s. "
                    f"Validation: {target_count} on target vs {source_example_count} in source — mismatch."
                )
                if failed_examples:
                    msg += f" Errors: {'; '.join(failed_examples[:3])}"

            print(f"  {msg}")
            update_examples_copy(
                spark, examples_table, row_run_id, agent_id,
                status, msg,
                target_example_count=target_count,
            )
            if status == "Copied":
                update_row_copied_examples(
                    spark, deploy_table, row_run_id, agent_id,
                    f"Examples copied successfully. Job_id={job_id}, Job_run_id={job_run_id}",
                )
                copied_count += 1
            else:
                update_row_copied_examples(
                    spark, deploy_table, row_run_id, agent_id,
                    f"Partial copy ({target_count}/{source_example_count}). Job_id={job_id}, Job_run_id={job_run_id}",
                )

        except Exception as ex:
            msg = f"Error: {ex}"
            print(f"  {msg}")
            update_examples_copy(
                spark, examples_table, row_run_id, agent_id,
                "Failed", msg,
            )
            update_row_copied_examples(
                spark, deploy_table, row_run_id, agent_id,
                f"Failed: {ex}. Job_id={job_id}, Job_run_id={job_run_id}",
            )
            failed_count += 1

    print(f"\n{'='*60}")
    print(f"Examples copy complete")
    print(f"  Copied: {copied_count}  Skipped: {skipped_count}  Failed: {failed_count}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
