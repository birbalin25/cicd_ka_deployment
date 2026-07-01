"""Export a Knowledge Assistant configuration from a source workspace.

Serializes the assistant definition, knowledge sources, and examples
to a JSON file that can be version-controlled and deployed to other
workspaces via deploy_ka.py.

Uses the REST API directly for export because the source workspace
may run an older API version (2.0) that the SDK's built-in methods
(which target API 2.1) don't support.

Usage (local):
    export DATABRICKS_HOST=https://source-workspace.cloud.databricks.com
    export DATABRICKS_TOKEN=<token>
    python src/export_ka.py --ka-id <knowledge-assistant-id>

Usage (notebook):
    Set widget "ka_id" and run all cells.
"""

import argparse
import json
import os

from databricks.sdk import WorkspaceClient

from common import get_dbutils, ka_api_call


def get_params() -> tuple:
    """Resolve (ka_id, export_config_volume_path) from widget or CLI args.

    export_config_volume_path is the UC Volume path where ka_config.json
    is saved.  It is None when running locally (falls back to local
    configs/ dir).
    """
    dbutils = get_dbutils()
    if dbutils is not None:
        return (
            dbutils.widgets.get("ka_id"),
            dbutils.widgets.get("export_config_volume_path"),
        )

    # CLI fallback
    parser = argparse.ArgumentParser(description="Export Knowledge Assistant config")
    parser.add_argument("--ka-id", required=True, help="Knowledge Assistant ID")
    parser.add_argument("--export-config-volume-path", default=None,
                        help="UC Volume path (e.g. /Volumes/catalog/schema/vol)")
    args = parser.parse_args()
    return args.ka_id, args.export_config_volume_path


def export_knowledge_assistant(w: WorkspaceClient, ka_id: str) -> dict:
    """Fetch and serialize a Knowledge Assistant.

    Uses the REST API with version fallback (2.1 -> 2.0) to support
    both newer and older workspace versions.
    """
    data = ka_api_call(w, "GET", f"knowledge_assistants/{ka_id}")
    ka = data.get("knowledge_assistant", data)

    # Serialize knowledge sources
    serialized_sources = []

    # Sources may come from the KA response or via a separate list call
    raw_sources = ka.get("knowledge_sources", [])
    if not raw_sources:
        try:
            src_resp = ka_api_call(
                w, "GET", f"knowledge_assistants/{ka_id}/knowledge-sources"
            )
            raw_sources = src_resp.get("knowledge_sources", [])
        except Exception:
            pass

    for s in raw_sources:
        source_entry = {}
        # API 2.1 flat format: source_type at top level, no wrapper keys
        if "source_type" in s and not any(
            k in s for k in ("index_source", "files_source", "file_source", "file_table_source")
        ):
            st = s["source_type"]
            if st == "index":
                source_entry = {
                    "display_name": s.get("display_name", ""),
                    "description": s.get("description", ""),
                    "source_type": "index",
                    "index_name": s.get("index", {}).get("index_name", s.get("index", {}).get("name", "")),
                    "text_col": s.get("index", {}).get("text_col", ""),
                    "doc_uri_col": s.get("index", {}).get("doc_uri_col", ""),
                }
            elif st == "files":
                source_entry = {
                    "display_name": s.get("display_name", ""),
                    "description": s.get("description", ""),
                    "source_type": "files",
                    "files_path": s.get("files", {}).get("path", ""),
                }
        # API 2.0 wrapped format
        elif "index_source" in s:
            idx = s["index_source"]
            source_entry = {
                "display_name": idx.get("name", ""),
                "description": idx.get("description", ""),
                "source_type": "index",
                "index_name": idx.get("index", {}).get("name", ""),
                "text_col": idx.get("index", {}).get("text_col", ""),
                "doc_uri_col": idx.get("index", {}).get("doc_uri_col", ""),
            }
        elif "files_source" in s:
            fs = s["files_source"]
            source_entry = {
                "display_name": fs.get("name", ""),
                "description": fs.get("description", ""),
                "source_type": "files",
                "files_path": fs.get("files", {}).get("path", ""),
            }
        elif "file_source" in s:
            fs = s["file_source"]
            source_entry = {
                "display_name": fs.get("name", ""),
                "description": fs.get("description", ""),
                "source_type": "files",
                "files_path": fs.get("path", ""),
            }
        elif "file_table_source" in s:
            fts = s["file_table_source"]
            source_entry = {
                "display_name": fts.get("name", ""),
                "description": fts.get("description", ""),
                "source_type": "file_table",
                "table_name": fts.get("table_name", ""),
            }
        if source_entry:
            serialized_sources.append(source_entry)

    # Retrieve examples
    serialized_examples = []
    try:
        resp = ka_api_call(w, "GET", f"knowledge_assistants/{ka_id}/examples")
        for ex in resp.get("examples", []):
            serialized_examples.append({
                "question": ex.get("question", ""),
                "guidelines": ex.get("guidelines", []),
            })
    except Exception as ex:
        print(f"  Warning: could not list examples: {ex}")

    config = {
        "display_name": ka.get("display_name", ka.get("name", "")),
        "description": ka.get("description", ""),
        "instructions": ka.get("instructions", ""),
        "knowledge_sources": serialized_sources,
        "examples": serialized_examples,
    }
    return config


def main() -> None:
    ka_id, export_config_volume_path = get_params()
    w = WorkspaceClient()

    print(f"Exporting Knowledge Assistant {ka_id} ...")
    config = export_knowledge_assistant(w, ka_id)

    if export_config_volume_path:
        # Running as notebook — write to UC Volume so deploy job can read it
        output_path = f"{export_config_volume_path}/ka_config.json"
        os.makedirs(export_config_volume_path, exist_ok=True)
    else:
        # Running locally — write to local configs/ directory
        output_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "configs",
            "ka_config.json",
        )
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(config, f, indent=2)

    print(f"Configuration written to {output_path}")
    print(f"  Display name : {config['display_name']}")
    print(f"  Sources      : {len(config['knowledge_sources'])}")
    print(f"  Examples     : {len(config['examples'])}")


if __name__ == "__main__":
    main()
