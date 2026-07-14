"""Deploy a Knowledge Assistant to a target workspace.

Reads the serialized configuration produced by export_ka.py and
creates (or updates) the assistant, its knowledge sources, and
examples in the target workspace.  Catalog/schema references in
knowledge-source paths are remapped to the target environment values.

Usage (Databricks notebook — invoked by the DAB job):
    Widgets "catalog", "schema", and "deploy_config_volume_path" are set
    via base_parameters in databricks.yml.

Usage (local):
    export DATABRICKS_HOST=https://target-workspace.cloud.databricks.com
    export DATABRICKS_TOKEN=<token>
    python src/deploy_ka.py --catalog prod_catalog --schema prod_schema
"""

import argparse
import json
import os
import sys
import time

from databricks.sdk import WorkspaceClient

from common import get_dbutils, ka_api_call, remap_path


# ---------------------------------------------------------------------------
# Parameter resolution
# ---------------------------------------------------------------------------

def _resolve_params() -> tuple:
    """Return (catalog, schema, deploy_config_volume_path) from notebook widgets or CLI args.

    deploy_config_volume_path is the UC Volume path where ka_config.json
    is stored.  It is None when running locally (falls back to local
    configs/ dir).
    """
    dbutils = get_dbutils()
    if dbutils is not None:
        return (
            dbutils.widgets.get("catalog"),
            dbutils.widgets.get("schema"),
            dbutils.widgets.get("deploy_config_volume_path"),
        )

    parser = argparse.ArgumentParser(description="Deploy Knowledge Assistant")
    parser.add_argument("--catalog", required=True)
    parser.add_argument("--schema", required=True)
    parser.add_argument("--deploy-config-volume-path", default=None,
                        help="UC Volume path (e.g. /Volumes/catalog/schema/vol)")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to ka_config.json (overrides deploy-config-volume-path)",
    )
    args = parser.parse_args()
    return args.catalog, args.schema, args.deploy_config_volume_path


# ---------------------------------------------------------------------------
# Core deployment logic
# ---------------------------------------------------------------------------

def find_existing_assistant(w: WorkspaceClient, display_name: str):
    """Return an existing assistant with the given display_name, or None."""
    resp = ka_api_call(w, "GET", "knowledge_assistants")
    for assistant in resp.get("knowledge_assistants", []):
        if assistant.get("display_name") == display_name:
            return assistant
    return None


def deploy_assistant(w: WorkspaceClient, config: dict):
    """Create or update the Knowledge Assistant definition."""
    existing = find_existing_assistant(w, config["display_name"])

    if existing:
        print(f"Deleting existing assistant '{config['display_name']}' ...")
        ka_api_call(w, "DELETE", existing["name"])

    print(f"Creating assistant '{config['display_name']}' ...")
    result = ka_api_call(w, "POST", "knowledge_assistants", body={
        "display_name": config["display_name"],
        "description": config["description"],
        "instructions": config["instructions"],
    })

    return result["name"]


def deploy_knowledge_sources(
    w: WorkspaceClient, ka_name: str, sources: list, catalog: str, schema: str
):
    """Create knowledge sources for the assistant, remapping paths."""
    parent = ka_name

    # Remove existing sources to ensure idempotency
    resp = ka_api_call(w, "GET", f"{parent}/knowledge-sources")
    existing_sources = resp.get("knowledge_sources", [])
    for es in existing_sources:
        print(f"  Removing old source '{es.get('display_name', '')}' ...")
        ka_api_call(w, "DELETE", es["name"])

    for source_cfg in sources:
        # Detect source type from config keys rather than source_type field
        if "index_name" in source_cfg:
            remapped = remap_path(source_cfg["index_name"], catalog, schema)
            body = {
                "display_name": source_cfg["display_name"],
                "description": source_cfg.get("description", ""),
                "source_type": "index",
                "index": {
                    "index_name": remapped,
                    "text_col": source_cfg.get("text_col", ""),
                    "doc_uri_col": source_cfg.get("doc_uri_col", ""),
                },
            }
            print(f"  Adding index source: {remapped}")
        elif "files_path" in source_cfg:
            remapped = remap_path(source_cfg["files_path"], catalog, schema)
            body = {
                "display_name": source_cfg["display_name"],
                "description": source_cfg.get("description", ""),
                "source_type": "files",
                "files": {"path": remapped},
            }
            print(f"  Adding files source: {remapped}")
        else:
            print(f"  Skipping source '{source_cfg.get('display_name', '?')}' (unknown type)")
            continue

        ka_api_call(w, "POST", f"{parent}/knowledge-sources", body=body)


def deploy_examples(w: WorkspaceClient, ka_name: str, examples: list, max_retries: int = 6, retry_delay: int = 30):
    """Create examples for the assistant.

    Retries if the endpoint isn't ready yet (waits up to max_retries *
    retry_delay seconds for the endpoint to provision).
    """
    if not examples:
        return

    parent = ka_name

    for attempt in range(max_retries):
        added = 0
        failed = False

        for ex in examples:
            try:
                ka_api_call(w, "POST", f"{parent}/examples", body={
                    "question": ex.get("question", ""),
                    "guidelines": ex.get("guidelines", []),
                })
                added += 1
            except Exception as e:
                failed = True
                if attempt < max_retries - 1:
                    print(f"  Endpoint not ready, retrying in {retry_delay}s (attempt {attempt + 1}/{max_retries})...")
                    time.sleep(retry_delay)
                    break
                else:
                    print(f"  Warning: could not add examples after {max_retries} attempts: {e}")
                    print("  Add examples manually once the endpoint is online.")
                    break

        if not failed:
            print(f"  Added {added}/{len(examples)} example(s)")
            return

    print(f"  Added {added}/{len(examples)} example(s)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    catalog, schema, deploy_config_volume_path = _resolve_params()

    # Determine config file location
    if "--config" in sys.argv:
        # Explicit CLI override
        idx = sys.argv.index("--config")
        config_path = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
    elif deploy_config_volume_path:
        # Running as notebook — read from UC Volume
        config_path = f"{deploy_config_volume_path}/ka_config.json"
    else:
        # Running locally — read from local configs/ directory
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "configs",
            "ka_config.json",
        )

    with open(config_path) as f:
        config = json.load(f)

    w = WorkspaceClient()

    # 1. Deploy assistant
    ka_name = deploy_assistant(w, config)
    print(f"  Assistant: {ka_name}")

    # 2. Deploy knowledge sources
    print("Deploying knowledge sources ...")
    deploy_knowledge_sources(
        w, ka_name, config.get("knowledge_sources", []), catalog, schema
    )

    # 3. Sync file-based knowledge sources (fire and forget)
    #    Index-based sources don't need sync.
    has_file_sources = any("files_path" in s for s in config.get("knowledge_sources", []))
    if has_file_sources:
        print("Triggering knowledge source sync (running in background) ...")
        ka_api_call(w, "POST", f"{ka_name}/knowledge-sources:sync")
    else:
        print("Skipping sync (index-based sources don't require it)")

    # 4. Deploy examples (best-effort — may fail if endpoint not ready yet)
    print("Deploying examples ...")
    deploy_examples(w, ka_name, config.get("examples", []))

    ka_id = ka_name.split("/")[-1] if "/" in ka_name else ka_name
    host = w.config.host.rstrip("/")
    ui_link = f"{host}/ml/bricks/ka/configure/{ka_id}"
    if has_file_sources:
        print(f"KA created. File source sync in progress — check status: {ui_link}")
    else:
        print(f"KA created. Index sources attached. View: {ui_link}")


if __name__ == "__main__":
    main()
