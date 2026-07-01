"""Post-deployment verification tests for Knowledge Assistants.

Runs lightweight checks after deployment to verify the agent exists,
has the expected knowledge sources, and has a serving endpoint.
Results are returned as dicts suitable for writing to test_results.csv.

Usage (imported by orchestrator.py):
    from test_runner import run_tests

Usage (standalone local):
    python src/test_runner.py --agent-id <id>
"""

import argparse

from databricks.sdk import WorkspaceClient

from common import ka_api_call, timestamp_now


# ---------------------------------------------------------------------------
# KA tests
# ---------------------------------------------------------------------------

def _test_ka_exists(w: WorkspaceClient, agent_id: str) -> tuple[bool, str]:
    """Verify the KA exists via REST API."""
    try:
        ka_api_call(w, "GET", f"knowledge_assistants/{agent_id}")
        return True, ""
    except Exception as ex:
        return False, f"verify_exists failed: {ex}"


def _test_ka_sources(w: WorkspaceClient, agent_id: str) -> tuple[bool, str]:
    """Verify at least one knowledge source is present."""
    try:
        resp = ka_api_call(w, "GET", f"knowledge_assistants/{agent_id}/knowledge-sources")
        sources = resp.get("knowledge_sources", [])
        if len(sources) == 0:
            return False, "verify_sources: no knowledge sources found"
        return True, ""
    except Exception as ex:
        return False, f"verify_sources failed: {ex}"


def _test_ka_endpoint(w: WorkspaceClient, agent_id: str) -> tuple[bool, str]:
    """Verify the KA's serving endpoint exists."""
    try:
        short_id = agent_id.split("-")[0]
        endpoint_name = f"ka-{short_id}-endpoint"
        ep = w.serving_endpoints.get(endpoint_name)
        if ep is None:
            return False, f"verify_endpoint: endpoint '{endpoint_name}' not found"
        return True, ""
    except Exception as ex:
        return False, f"verify_endpoint failed: {ex}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_tests(w: WorkspaceClient, agent_type: str, agent_id: str) -> dict:
    """Run all verification tests for a deployed KA.

    Returns a dict with keys: agent_type, agent_id, test_status,
    timestamp, error_details.
    """
    if agent_type.upper() != "KA":
        return {
            "agent_type": agent_type,
            "agent_id": agent_id,
            "test_status": "Skipped",
            "timestamp": timestamp_now(),
            "error_details": f"Unsupported agent_type: {agent_type}",
        }

    errors = []
    checks = [
        _test_ka_exists,
        _test_ka_sources,
        _test_ka_endpoint,
    ]

    for check_fn in checks:
        passed, err = check_fn(w, agent_id)
        if not passed:
            errors.append(err)

    return {
        "agent_type": agent_type,
        "agent_id": agent_id,
        "test_status": "Pass" if not errors else "Fail",
        "timestamp": timestamp_now(),
        "error_details": "; ".join(errors),
    }


# ---------------------------------------------------------------------------
# Standalone CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Run post-deploy tests")
    parser.add_argument("--agent-id", required=True)
    args = parser.parse_args()

    w = WorkspaceClient()
    result = run_tests(w, "KA", args.agent_id)

    print(f"Test result: {result['test_status']}")
    if result["error_details"]:
        print(f"  Errors: {result['error_details']}")


if __name__ == "__main__":
    main()
