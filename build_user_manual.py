#!/usr/bin/env python3
"""Generate the KA Deployment Tool user manual as a .docx (no external deps).

A .docx is a ZIP archive of WordprocessingML XML parts. This builds a
well-formed document with a title page, table of contents, and one
page-break-separated section ("tab") per topic.
"""

import zipfile
from xml.sax.saxutils import escape

OUT = "KA_Deployment_User_Manual.docx"

# Databricks brand-ish palette
CLR_TITLE = "1B3139"      # dark slate
CLR_HEADING = "FF3621"    # Databricks red-orange
CLR_SUBTLE = "5A6872"     # grey
CLR_CODE_BG = "F2F4F5"    # light grey
CLR_TABLE_HDR = "1B3139"  # header fill

# ---------------------------------------------------------------------------
# Low-level paragraph / run builders (WordprocessingML)
# ---------------------------------------------------------------------------


def _run(text, *, bold=False, italic=False, color=None, size=None, mono=False):
    rpr = "<w:rPr>"
    if mono:
        rpr += '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:cs="Consolas"/>'
    if bold:
        rpr += "<w:b/>"
    if italic:
        rpr += "<w:i/>"
    if color:
        rpr += f'<w:color w:val="{color}"/>'
    if size:
        rpr += f'<w:sz w:val="{size*2}"/><w:szCs w:val="{size*2}"/>'
    rpr += "</w:rPr>"
    # preserve spaces
    return (
        f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'
    )


def para(runs="", *, style=None, align=None, spacing_before=0, spacing_after=120,
         shade=None, indent=None):
    """Build a <w:p>. `runs` may be a string (single run) or raw run XML."""
    if isinstance(runs, str) and not runs.startswith("<w:r"):
        runs = _run(runs)
    ppr = "<w:pPr>"
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
    if shade:
        ppr += f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>'
    if indent:
        ppr += f'<w:ind w:left="{indent}"/>'
    ppr += f'<w:spacing w:before="{spacing_before}" w:after="{spacing_after}"/>'
    if align:
        ppr += f'<w:jc w:val="{align}"/>'
    ppr += "</w:pPr>"
    return f"<w:p>{ppr}{runs}</w:p>"


def heading(text, level=1):
    color = CLR_HEADING if level <= 2 else CLR_SUBTLE
    size = {1: 20, 2: 15, 3: 13}.get(level, 12)
    return para(
        _run(text, bold=True, color=color, size=size),
        spacing_before=240, spacing_after=120,
    )


def title(text):
    return para(_run(text, bold=True, color=CLR_TITLE, size=30),
                align="center", spacing_before=0, spacing_after=120)


def subtitle(text):
    return para(_run(text, italic=True, color=CLR_SUBTLE, size=13),
                align="center", spacing_after=240)


def bullet(text_runs, indent=360):
    if isinstance(text_runs, str) and not text_runs.startswith("<w:r"):
        text_runs = _run(text_runs)
    ppr = (
        "<w:pPr>"
        f'<w:ind w:left="{indent}" w:hanging="180"/>'
        '<w:spacing w:after="60"/>'
        "</w:pPr>"
    )
    dot = _run("•  ", bold=True, color=CLR_HEADING)
    return f"<w:p>{ppr}{dot}{text_runs}</w:p>"


def code_block(lines):
    """Render lines as shaded monospace paragraphs."""
    out = []
    for i, ln in enumerate(lines):
        out.append(para(
            _run(ln if ln else " ", mono=True, size=9, color="1B3139"),
            shade=CLR_CODE_BG, spacing_after=0, spacing_before=0, indent=120,
        ))
    return "".join(out)


def page_break():
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def spacer():
    return para(" ", spacing_after=80)


# ---------------------------------------------------------------------------
# Table builder
# ---------------------------------------------------------------------------


def table(headers, rows, widths=None):
    n = len(headers)
    if widths is None:
        widths = [str(int(9000 / n))] * n
    else:
        widths = [str(w) for w in widths]

    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)

    def cell(text, w, *, header=False):
        shade = CLR_TABLE_HDR if header else "FFFFFF"
        tcpr = (
            "<w:tcPr>"
            f'<w:tcW w:w="{w}" w:type="dxa"/>'
            f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>'
            '<w:tcMar>'
            '<w:top w:w="60" w:type="dxa"/><w:bottom w:w="60" w:type="dxa"/>'
            '<w:left w:w="90" w:type="dxa"/><w:right w:w="90" w:type="dxa"/>'
            '</w:tcMar>'
            "</w:tcPr>"
        )
        if header:
            run = _run(text, bold=True, color="FFFFFF", size=9)
        else:
            # allow monospace for first-column code-ish values via marker
            if text.startswith("`") and text.endswith("`"):
                run = _run(text[1:-1], mono=True, size=9)
            else:
                run = _run(text, size=9)
        p = f'<w:p><w:pPr><w:spacing w:after="20"/></w:pPr>{run}</w:p>'
        return f"<w:tc>{tcpr}{p}</w:tc>"

    hdr_row = "<w:tr>" + "".join(
        cell(h, widths[i], header=True) for i, h in enumerate(headers)
    ) + "</w:tr>"

    body = ""
    for r in rows:
        body += "<w:tr>" + "".join(
            cell(str(c), widths[i]) for i, c in enumerate(r)
        ) + "</w:tr>"

    borders = (
        "<w:tblBorders>"
        '<w:top w:val="single" w:sz="4" w:color="C6CDD2"/>'
        '<w:left w:val="single" w:sz="4" w:color="C6CDD2"/>'
        '<w:bottom w:val="single" w:sz="4" w:color="C6CDD2"/>'
        '<w:right w:val="single" w:sz="4" w:color="C6CDD2"/>'
        '<w:insideH w:val="single" w:sz="4" w:color="C6CDD2"/>'
        '<w:insideV w:val="single" w:sz="4" w:color="C6CDD2"/>'
        "</w:tblBorders>"
    )
    tblpr = (
        "<w:tblPr>"
        '<w:tblW w:w="9000" w:type="dxa"/>'
        '<w:tblLayout w:type="fixed"/>'
        f"{borders}"
        "</w:tblPr>"
    )
    tblgrid = f"<w:tblGrid>{grid}</w:tblGrid>"
    return (
        f"<w:tbl>{tblpr}{tblgrid}{hdr_row}{body}</w:tbl>"
        + para(" ", spacing_after=80)
    )


# ---------------------------------------------------------------------------
# Document content
# ---------------------------------------------------------------------------

body = []

# ---- Title page ----
body.append(title("Knowledge Assistant Deployment Tool"))
body.append(subtitle("User Manual"))
body.append(spacer())
body.append(para(_run("What this tool does, how to configure it, and how to run it.",
                      color=CLR_SUBTLE, size=12), align="center"))
body.append(spacer())
body.append(para(_run("Version 1.0   •   Last updated 2026-07-16", color=CLR_SUBTLE,
                      italic=True, size=10), align="center"))
body.append(page_break())

# ---- Table of contents ----
body.append(heading("Contents", 1))
toc = [
    "1.  Project Overview",
    "2.  Input File — agents_input.csv",
    "3.  Configuration — variables.yml",
    "4.  How to Deploy (Run the Tool)",
    "5.  Status Tracking Tables",
    "6.  Copying Examples",
    "7.  Troubleshooting",
]
for t in toc:
    body.append(para(_run(t, size=11)))
body.append(page_break())

# ============================================================
# TAB 1 — Project Overview
# ============================================================
body.append(heading("1.  Project Overview", 1))
body.append(para(
    "This tool automates copying (migrating) Databricks Knowledge Assistants (KAs) "
    "from a source workspace to one or more target workspaces. It is driven by a "
    "simple CSV list of KAs and runs as a Databricks Asset Bundle (DAB) job."
))

body.append(heading("What it does", 2))
for b in [
    "Reads a list of KAs to migrate from a CSV file (agents_input.csv).",
    "Exports each KA's definition, knowledge sources, and example questions from the source workspace.",
    "Re-creates each KA on the target workspace, remapping catalog/schema paths.",
    "Runs post-deployment tests to confirm each KA exists, has its sources, and has a serving endpoint.",
    "Records every step in Delta status tables so you can audit results.",
    "Copies example questions in a second phase, once each KA becomes ACTIVE.",
]:
    body.append(bullet(b))

body.append(heading("How it runs", 2))
body.append(para(
    "The tool is packaged as a Databricks Asset Bundle. A GitHub Actions workflow "
    "deploys the bundle and runs the job automatically for the staging and prod "
    "environments. You can also deploy and run it manually with the Databricks CLI."
))
body.append(para(_run("Two jobs are created:", bold=True)))
body.append(bullet(
    _run("Batch Deploy Agents", bold=True)
    + _run(" — migrates all KAs listed in the CSV.")))
body.append(bullet(
    _run("Copy KA Examples", bold=True)
    + _run(" — copies example questions to KAs after they reach the ACTIVE state.")))

body.append(heading("Two-phase design (why)", 2))
body.append(para(
    "A newly created KA stays in the CREATING state until its files finish syncing "
    "and its vector index is provisioned (often 15–25 minutes). Example questions "
    "can only be added once the KA is ACTIVE. To avoid blocking every KA on that wait, "
    "the tool deploys all KAs first (Phase 1), then copies examples afterward (Phase 2)."
))
body.append(page_break())

# ============================================================
# TAB 2 — agents_input.csv
# ============================================================
body.append(heading("2.  Input File — agents_input.csv", 1))
body.append(para(
    "This CSV lists the KAs to migrate. It lives at configs/agents_input.csv and is "
    "synced to the workspace when the bundle is deployed. One row = one KA."
))

body.append(heading("Columns", 2))
body.append(table(
    ["Column", "Required", "Purpose", "Example"],
    [
        ["`agent_type`", "Yes", "Type of agent. Use KA for Knowledge Assistant. Other rows are ignored.", "`KA`"],
        ["`agent_id`", "Yes", "The source KA's unique ID (UUID) to export from the source workspace.", "`b4b67d24-d217-4312-9f2b-5e1ccc022920`"],
        ["`target_catalog`", "No", "Override target catalog for this KA. Blank = use the job default.", "`prod_catalog`"],
        ["`target_schema`", "No", "Override target schema for this KA. Blank = use the job default.", "`ka_schema`"],
        ["`display_name_override`", "No", "Rename the KA on the target. Blank = keep the source display name.", "`bir-first-ka`"],
        ["`skip_tests`", "No", "true skips post-deploy tests for this KA. Default false.", "`false`"],
        ["`copy_volumes`", "No", "true copies file-based knowledge-source volumes from source to target. Default false.", "`true`"],
        ["`replace_KA`", "No", "true replaces an existing KA of the same name. false skips migration if the name already exists on target.", "`true`"],
    ],
    widths=[2100, 900, 4200, 1800],
))

body.append(heading("replace_KA — how it behaves", 3))
for b in [
    "true  → if a KA with the target display name already exists, it is deleted and re-created (replaced).",
    "false → if a KA with the target display name already exists, it is skipped (left untouched). If it does NOT exist, it is migrated normally.",
]:
    body.append(bullet(b))

body.append(heading("Example file", 2))
body.append(code_block([
    "agent_type,agent_id,target_catalog,target_schema,display_name_override,skip_tests,copy_volumes,replace_KA",
    "KA,b4b67d24-d217-4312-9f2b-5e1ccc022920,,,bir-first-ka,false,true,true",
    "KA,e2756793-fb6c-4bb5-9820-77a3a0103147,,,bir-second-ka,false,true,true",
    "KA,268aec86-75bf-4f0a-9fa8-473b4f670811,,,bir-third-ka,false,true,true",
]))
body.append(para(_run(
    "Tip: leave target_catalog and target_schema blank to inherit the environment "
    "defaults set in variables.yml (see next section).", italic=True, color=CLR_SUBTLE, size=10)))
body.append(page_break())

# ============================================================
# TAB 3 — variables.yml
# ============================================================
body.append(heading("3.  Configuration — variables.yml", 1))
body.append(para(
    "variables.yml defines the settings the jobs use. Each variable has a default, "
    "and can be overridden per environment (dev / staging / prod) in the targets section."
))

body.append(heading("Variables", 2))
body.append(table(
    ["Variable", "What it controls", "Example value"],
    [
        ["`env_prefix`", "Prefix added to job names (no space). Set per environment.", "`Prod_`"],
        ["`target_catalog`", "Default catalog KAs are deployed into.", "`prod_catalog`"],
        ["`target_schema`", "Default schema KAs are deployed into.", "`ka_schema`"],
        ["`status_table_name`", "Full name of the deploy status table. Blank = {catalog}.{schema}.ka_deployment_status.", "`main.ka.ka_deployment_status`"],
        ["`source_host`", "Source workspace URL to export KAs from.", "`https://adb-7405617211688462.2.azuredatabricks.net`"],
        ["`secret_scope`", "Databricks secret scope holding source credentials.", "`ka-deployment`"],
        ["`budget_policy_id`", "Serverless usage policy ID attached to the jobs.", "`511dfd24-7efe-312e-8213-04f11f170b29`"],
        ["`wait_and_copy_examples`", "true = copy examples inline in the deploy job. false = defer to the Copy KA Examples job.", "`false`"],
        ["`since_timestamp`", "Copy job: only process rows completed after this time. Blank = latest run.", "`2026-07-16T06:13:58`"],
        ["`cluster_spark_version`", "Spark version (classic compute only).", "`15.4.x-scala2.12`"],
        ["`cluster_node_type`", "Node type (classic compute only).", "`Standard_DS3_v2`"],
    ],
    widths=[2400, 4400, 2200],
))

body.append(heading("Per-environment overrides", 2))
body.append(para(
    "The targets section overrides variables for each environment. Example:"))
body.append(code_block([
    "targets:",
    "  staging:",
    "    variables:",
    "      env_prefix: \"Staging_\"",
    "      target_catalog: \"dev\"",
    "      target_schema: \"birschema\"",
    "",
    "  prod:",
    "    variables:",
    "      env_prefix: \"Prod_\"",
    "      target_catalog: \"prod_catalog\"",
    "      target_schema: \"ka_schema\"",
    "      since_timestamp: \"2026-07-16T06:13:58\"",
]))

body.append(heading("Source credentials (secret scope)", 2))
body.append(para(
    "Source workspace credentials are NOT stored in variables.yml. They live in the "
    "Databricks secret scope named by secret_scope. Expected keys:"))
body.append(table(
    ["Secret key", "Purpose"],
    [
        ["`source-token`", "Source workspace PAT (used if service principal is not set)."],
        ["`source-client-id`", "Source workspace service principal client ID."],
        ["`source-client-secret`", "Source workspace service principal client secret."],
    ],
    widths=[3000, 6000],
))
body.append(page_break())

# ============================================================
# TAB 4 — How to deploy
# ============================================================
body.append(heading("4.  How to Deploy (Run the Tool)", 1))

body.append(heading("Option A — Automatic (CI/CD)", 2))
body.append(para(
    "Pushing to the main branch triggers the GitHub Actions workflow, which deploys "
    "the bundle and runs the job for staging (prod requires manual approval). No local "
    "setup needed — credentials come from GitHub secrets."))

body.append(heading("Option B — Manual (Databricks CLI)", 2))
body.append(para(_run("1) Deploy the bundle to a target environment:", bold=True)))
body.append(code_block([
    "databricks bundle deploy -t staging",
]))
body.append(para(_run("2) Run the batch deploy job:", bold=True)))
body.append(code_block([
    "databricks bundle run batch_deploy_agents -t staging",
]))
body.append(para(_run("3) (Later) run the examples copy job:", bold=True)))
body.append(code_block([
    "databricks bundle run copy_examples_job -t staging",
]))

body.append(heading("Choosing compute", 2))
body.append(para(
    "databricks.yml includes either job_serverless.yml (default) or job_classic.yml. "
    "Switch by changing the include line. Serverless has faster cold starts; classic "
    "avoids serverless networking restrictions for cross-workspace calls."))
body.append(page_break())

# ============================================================
# TAB 5 — Status tables
# ============================================================
body.append(heading("5.  Status Tracking Tables", 1))
body.append(para(
    "Every run records its results in Delta tables so you can audit what happened."))

body.append(heading("ka_deployment_status (deploy job)", 2))
body.append(para("Key columns to check after a run:"))
body.append(table(
    ["Column", "Meaning"],
    [
        ["`run_id`", "Unique ID for the batch run."],
        ["`agent_id`", "Source KA ID that was processed."],
        ["`target_ka_name`", "The deployed KA resource name on the target."],
        ["`status`", "Pending / Deploying / Success / Failed / Skipped."],
        ["`status_desc`", "Human-readable detail of what happened."],
        ["`test_status`", "Pass / Fail / Skipped / N/A."],
        ["`test_status_desc`", "Test details (e.g. sources found, endpoint state)."],
        ["`copied_examples`", "Pending / copied message / N/A (tracks example-copy state)."],
        ["`completed_at`", "When the deploy finished (e.g. 2026-07-16T06:13:58)."],
    ],
    widths=[2600, 6400],
))

body.append(heading("ka_examples_status (copy job)", 2))
body.append(para("Tracks the example-copy phase, one row per KA processed:"))
body.append(table(
    ["Column", "Meaning"],
    [
        ["`run_id`", "Links back to the deploy run."],
        ["`agent_id`", "Source KA ID."],
        ["`source_example_count`", "Number of examples in the source KA."],
        ["`target_example_count`", "Number verified on the target after copy."],
        ["`copy_status`", "Pending / Copied / Partial / Failed."],
        ["`copy_details`", "Timing, counts, and validation notes."],
        ["`updated_at`", "Last update time (e.g. 2026-07-16T06:13:58)."],
    ],
    widths=[2600, 6400],
))
body.append(page_break())

# ============================================================
# TAB 6 — Copying examples
# ============================================================
body.append(heading("6.  Copying Examples", 1))
body.append(para(
    "Example questions are copied only after a KA reaches the ACTIVE state. There are "
    "two ways this happens, controlled by the wait_and_copy_examples variable."))

body.append(heading("Inline (wait_and_copy_examples = true)", 2))
body.append(para(
    "The deploy job waits (up to 30 minutes per KA) for each KA to become ACTIVE and "
    "copies examples in the same run. Simple, but slower for large batches."))

body.append(heading("Deferred (wait_and_copy_examples = false, recommended)", 2))
body.append(para(
    "The deploy job finishes without waiting. Run the Copy KA Examples job later "
    "(e.g. an hour afterward) to copy examples for all KAs that are now ACTIVE."))

body.append(heading("Processing only recent runs (since_timestamp)", 2))
body.append(para(
    "The Copy KA Examples job accepts an optional since_timestamp. When set, it "
    "processes only deploy rows whose completed_at is newer than that timestamp "
    "(across all runs). When blank, it processes the latest run."))
body.append(para(_run("Run with a timestamp filter:", bold=True)))
body.append(code_block([
    "databricks bundle run copy_examples_job -t staging \\",
    "  -- --since-timestamp \"2026-07-16T06:13:58\"",
]))
body.append(para(_run(
    "Timestamp format: YYYY-MM-DDTHH:MM:SS  (example: 2026-07-16T06:13:58). "
    "A plain date like 2026-07-16 is also accepted.",
    italic=True, color=CLR_SUBTLE, size=10)))
body.append(page_break())

# ============================================================
# TAB 7 — Troubleshooting
# ============================================================
body.append(heading("7.  Troubleshooting", 1))
body.append(table(
    ["Symptom", "Likely cause & fix"],
    [
        ["\"Knowledge Assistant is not supported ... Serverless Access Controls Preview\"",
         "The workspace is enrolled in the Serverless Access Controls Preview, which blocks KA creation. Disable that preview in the Account Console (affects the whole workspace)."],
        ["\"Library installation failed\" on serverless",
         "Do not add databricks-sdk to the serverless dependencies — it is pre-installed, and the preview blocks PyPI downloads. Leave dependencies out."],
        ["job_id / job_run_id show \"local\"",
         "Running outside a Databricks job, or context not available. Expected for local CLI runs."],
        ["source_display_name / source_example_count blank",
         "The source KA could not be read. Check source_host and the secret scope credentials."],
        ["KA skipped unexpectedly",
         "replace_KA is false and a KA with the same target name already exists. Set replace_KA=true to replace it."],
        ["Examples not copied",
         "The KA may not be ACTIVE yet. Re-run the Copy KA Examples job later, optionally with --since-timestamp."],
    ],
    widths=[3200, 5800],
))
body.append(spacer())
body.append(para(_run(
    "For deployment status, always check the ka_deployment_status table first — the "
    "status_desc column usually explains exactly what happened.",
    italic=True, color=CLR_SUBTLE, size=10)))

document_xml = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>"
    + "".join(body)
    + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
      '<w:pgMar w:top="1200" w:right="1440" w:bottom="1200" w:left="1440" '
      'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
    "</w:body></w:document>"
)

# ---------------------------------------------------------------------------
# Static package parts
# ---------------------------------------------------------------------------

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" '
    'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>"
)

RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
    'Target="word/document.xml"/>'
    "</Relationships>"
)

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("[Content_Types].xml", CONTENT_TYPES)
    z.writestr("_rels/.rels", RELS)
    z.writestr("word/document.xml", document_xml)

print(f"Wrote {OUT}")
