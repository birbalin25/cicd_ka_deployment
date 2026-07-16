#!/usr/bin/env python3
"""Generate the KA Deployment Tool user manual as a Databricks-styled .docx.

A .docx is a ZIP of WordprocessingML parts. This builds one with REAL Word
heading styles (so Word's Navigation Pane shows each section as a "tab") and a
live Table of Contents field. Styling follows the Databricks brand: DM Sans,
Databricks red-orange (#FF3621) headings, slate (#1B3139) titles.

No third-party packages required (python-docx is not available offline).
"""

import zipfile
from xml.sax.saxutils import escape

OUT = "KA_Deployment_User_Manual.docx"

# Databricks brand palette
CLR_TITLE = "1B3139"      # Databricks slate/navy
CLR_HEADING = "FF3621"    # Databricks red-orange (Lava)
CLR_H2 = "1B3139"         # slate for sub-headings
CLR_SUBTLE = "5A6872"     # cool grey
CLR_CODE_BG = "F5F5F0"    # oat-light for code blocks
CLR_TABLE_HDR = "1B3139"  # header fill
CLR_BORDER = "C6CDD2"
FONT = "DM Sans"
MONO = "Consolas"


# ---------------------------------------------------------------------------
# Run / paragraph helpers
# ---------------------------------------------------------------------------

def run(text, *, bold=False, italic=False, color=None, size=None, mono=False):
    rpr = "<w:rPr>"
    rpr += f'<w:rFonts w:ascii="{MONO if mono else FONT}" w:hAnsi="{MONO if mono else FONT}" w:cs="{MONO if mono else FONT}"/>'
    if bold:
        rpr += "<w:b/>"
    if italic:
        rpr += "<w:i/>"
    if color:
        rpr += f'<w:color w:val="{color}"/>'
    if size:
        rpr += f'<w:sz w:val="{size*2}"/><w:szCs w:val="{size*2}"/>'
    rpr += "</w:rPr>"
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def p(runs="", *, style=None, align=None, after=120, before=0, shade=None, indent=None):
    if isinstance(runs, str) and not runs.startswith("<w:r"):
        runs = run(runs)
    ppr = "<w:pPr>"
    if style:
        ppr += f'<w:pStyle w:val="{style}"/>'
    if shade:
        ppr += f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>'
    if indent:
        ppr += f'<w:ind w:left="{indent}"/>'
    ppr += f'<w:spacing w:before="{before}" w:after="{after}"/>'
    if align:
        ppr += f'<w:jc w:val="{align}"/>'
    ppr += "</w:pPr>"
    return f"<w:p>{ppr}{runs}</w:p>"


def h1(text):
    return p(text, style="Heading1")


def h2(text):
    return p(text, style="Heading2")


def h3(text):
    return p(text, style="Heading3")


def title(text):
    return p(run(text, bold=True, color=CLR_TITLE, size=32), align="center", after=60)


def subtitle(text):
    return p(run(text, italic=True, color=CLR_SUBTLE, size=14), align="center", after=240)


def body(text):
    return p(text, after=120)


def bullet(runs):
    if isinstance(runs, str) and not runs.startswith("<w:r"):
        runs = run(runs)
    ppr = ('<w:pPr><w:ind w:left="360" w:hanging="180"/>'
           '<w:spacing w:after="60"/></w:pPr>')
    return f'<w:p>{ppr}{run("•  ", bold=True, color=CLR_HEADING)}{runs}</w:p>'


def code(lines):
    out = []
    for ln in lines:
        out.append(p(run(ln if ln else " ", mono=True, size=9, color=CLR_TITLE),
                     shade=CLR_CODE_BG, after=0, before=0, indent=120))
    return "".join(out) + p(" ", after=80)


def page_break():
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def note(text):
    return p(run("Note:  ", bold=True, color=CLR_HEADING, size=10)
             + run(text, italic=True, color=CLR_SUBTLE, size=10), after=120)


# ---------------------------------------------------------------------------
# Table
# ---------------------------------------------------------------------------

def table(headers, rows, widths):
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)

    def cell(text, w, header=False):
        shade = CLR_TABLE_HDR if header else "FFFFFF"
        tcpr = (f'<w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>'
                f'<w:shd w:val="clear" w:color="auto" w:fill="{shade}"/>'
                '<w:tcMar><w:top w:w="50" w:type="dxa"/><w:bottom w:w="50" w:type="dxa"/>'
                '<w:left w:w="90" w:type="dxa"/><w:right w:w="90" w:type="dxa"/></w:tcMar>'
                '<w:vAlign w:val="center"/></w:tcPr>')
        if header:
            r = run(text, bold=True, color="FFFFFF", size=9)
        elif text.startswith("`") and text.endswith("`") and len(text) > 1:
            r = run(text[1:-1], mono=True, size=9, color=CLR_TITLE)
        else:
            r = run(text, size=9)
        return f'<w:tc>{tcpr}<w:p><w:pPr><w:spacing w:after="20"/></w:pPr>{r}</w:p></w:tc>'

    hdr = "<w:tr>" + "".join(cell(h, widths[i], True) for i, h in enumerate(headers)) + "</w:tr>"
    rowxml = ""
    for row_cells in rows:
        rowxml += "<w:tr>" + "".join(cell(str(c), widths[i]) for i, c in enumerate(row_cells)) + "</w:tr>"
    borders = ("<w:tblBorders>"
               + "".join(f'<w:{e} w:val="single" w:sz="4" w:color="{CLR_BORDER}"/>'
                         for e in ("top", "left", "bottom", "right", "insideH", "insideV"))
               + "</w:tblBorders>")
    total = sum(widths)
    tblpr = (f'<w:tblPr><w:tblW w:w="{total}" w:type="dxa"/>'
             f'<w:tblLayout w:type="fixed"/>{borders}</w:tblPr>')
    return (f'<w:tbl>{tblpr}<w:tblGrid>{grid}</w:tblGrid>{hdr}{rowxml}</w:tbl>'
            + p(" ", after=80))


def toc_field(entries):
    """A live TOC field. Word offers to update it on open; we also render a
    static fallback listing so it is readable even before updating."""
    begin = ('<w:p><w:pPr><w:spacing w:after="40"/></w:pPr>'
             '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
             '<w:r><w:instrText xml:space="preserve"> TOC \\o "1-2" \\h \\z \\u </w:instrText></w:r>'
             '<w:r><w:fldChar w:fldCharType="separate"/></w:r></w:p>')
    static = ""
    for label, lvl in entries:
        indent = 0 if lvl == 1 else 360
        static += p(run(label, color=CLR_TITLE, size=11, bold=(lvl == 1)), indent=indent, after=40)
    end = '<w:p><w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
    return begin + static + end


# ===========================================================================
# Build document body
# ===========================================================================

D = []

# ---- Cover ----
D.append(p(" ", after=240))
D.append(title("Knowledge Assistant Deployment Tool"))
D.append(subtitle("User Manual"))
D.append(p(run("Migrate Databricks Knowledge Assistants across workspaces using "
               "Databricks Asset Bundles.", color=CLR_SUBTLE, size=12), align="center", after=240))
D.append(p(run("Version 1.1   •   Last updated 2026-07-16", italic=True, color=CLR_SUBTLE, size=10),
           align="center"))
D.append(page_break())

# ---- Contents ----
D.append(h1("Contents"))
D.append(toc_field([
    ("1.  Project Overview", 1),
    ("2.  Input File — agents_input.csv", 1),
    ("3.  Configuration — variables.yml", 1),
    ("4.  Authentication", 1),
    ("5.  Prerequisites & Permissions Checklist", 1),
    ("6.  GitHub Secrets & Variables by Environment", 1),
    ("7.  How to Deploy (Run the Tool)", 1),
    ("8.  Running the Tool Locally", 1),
    ("9.  Status Tracking Tables", 1),
    ("10.  Copying Examples", 1),
    ("11.  Troubleshooting", 1),
]))
D.append(page_break())

# ===========================================================================
# TAB 1 — Overview
# ===========================================================================
D.append(h1("1.  Project Overview"))
D.append(body("This tool automates copying (migrating) Databricks Knowledge Assistants (KAs) "
              "from a source workspace to one or more target workspaces. It is driven by a "
              "simple CSV list of KAs and runs as a Databricks Asset Bundle (DAB) job."))
D.append(h2("What it does"))
for b in [
    "Reads a list of KAs to migrate from a CSV file (agents_input.csv).",
    "Exports each KA's definition, knowledge sources, and example questions from the source workspace.",
    "Re-creates each KA on the target workspace, remapping catalog/schema paths.",
    "Runs post-deployment tests: KA exists, has knowledge sources, and has a serving endpoint.",
    "Records every step in Delta status tables so results are auditable.",
    "Copies example questions after each KA becomes ACTIVE.",
]:
    D.append(bullet(b))
D.append(h2("The two jobs"))
D.append(bullet(run("{env}_Batch_Deploy_Agents", bold=True) + run(" — migrates all KAs listed in the CSV.")))
D.append(bullet(run("{env}_Copy_KA_Examples", bold=True) + run(" — copies example questions once KAs are ACTIVE.")))
D.append(h2("Two-phase design (why)"))
D.append(body("A newly created KA stays in the CREATING state until its files finish syncing and its "
              "vector index is provisioned (often 15–25 minutes). Example questions can only be added "
              "once the KA is ACTIVE. To avoid blocking, the tool creates all KAs first, then copies "
              "examples afterward."))
D.append(page_break())

# ===========================================================================
# TAB 2 — agents_input.csv
# ===========================================================================
D.append(h1("2.  Input File — agents_input.csv"))
D.append(body("This CSV lists the KAs to migrate. It lives at configs/agents_input.csv and is synced to "
              "the workspace when the bundle is deployed. One row = one KA."))
D.append(h2("Columns"))
D.append(table(
    ["Column", "Req.", "Purpose", "Example"],
    [
        ["`agent_type`", "Yes", "Agent type. Use KA for Knowledge Assistant; other rows are ignored.", "`KA`"],
        ["`agent_id`", "Yes", "Source KA unique ID (UUID) to export.", "`b4b67d24-d217-4312-9f2b-5e1ccc022920`"],
        ["`target_catalog`", "No", "Override target catalog. Blank = job default.", "`prod_catalog`"],
        ["`target_schema`", "No", "Override target schema. Blank = job default.", "`ka_schema`"],
        ["`display_name_override`", "No", "Rename the KA on target. Blank = keep source name.", "`bir-first-ka`"],
        ["`skip_tests`", "No", "true skips post-deploy tests. Default false.", "`false`"],
        ["`copy_volumes`", "No", "true copies file-based source volumes to target. Default false.", "`true`"],
        ["`replace_KA`", "No", "true replaces an existing same-named KA; false skips if it exists.", "`true`"],
    ],
    [1900, 700, 4100, 2300],
))
D.append(h3("replace_KA behavior"))
D.append(bullet("true  → if a KA with the target display name exists, it is deleted and re-created."))
D.append(bullet("false → if it exists, it is skipped (left untouched). If it does NOT exist, it is migrated."))
D.append(h2("Example file"))
D.append(code([
    "agent_type,agent_id,target_catalog,target_schema,display_name_override,skip_tests,copy_volumes,replace_KA",
    "KA,b4b67d24-d217-4312-9f2b-5e1ccc022920,,,bir-first-ka,false,true,true",
    "KA,e2756793-fb6c-4bb5-9820-77a3a0103147,,,bir-second-ka,false,true,true",
    "KA,268aec86-75bf-4f0a-9fa8-473b4f670811,,,bir-third-ka,false,true,true",
]))
D.append(note("Leave target_catalog / target_schema blank to inherit the environment defaults from variables.yml."))
D.append(page_break())

# ===========================================================================
# TAB 3 — variables.yml
# ===========================================================================
D.append(h1("3.  Configuration — variables.yml"))
D.append(body("variables.yml defines the settings the jobs use. Each variable has a default and can be "
              "overridden per environment (dev / staging / prod) in the targets section. The table below "
              "mirrors the inline comments in variables.yml."))
D.append(h2("Variables"))
D.append(table(
    ["Variable", "What it controls", "Default", "Example"],
    [
        ["`env_prefix`",
         "Prefix added to every job name. Include the trailing underscore yourself (no space is added). Set per environment.",
         "`Dev_`", "`Prod_`"],
        ["`target_catalog`",
         "Default catalog KAs deploy into. Overridden per row by the target_catalog column in agents_input.csv when non-empty.",
         "`main`", "`prod_catalog`"],
        ["`target_schema`",
         "Default schema KAs deploy into. Overridden per row by the target_schema column in agents_input.csv when non-empty.",
         "`default`", "`ka_schema`"],
        ["`status_table_name`",
         "Fully-qualified Delta status table. Blank uses the convention {catalog}.{schema}.ka_deployment_status.",
         "(blank)", "`main.ka.ka_deployment_status`"],
        ["`source_host`",
         "Source workspace to export KAs FROM (full https URL). Blank = export from the same workspace.",
         "(set)", "`https://adb-xxxx.7.azuredatabricks.net`"],
        ["`secret_scope`",
         "Secret scope holding SOURCE credentials. Keys: source-token, source-client-id, source-client-secret.",
         "`ka-deployment`", "`ka-deployment`"],
        ["`budget_policy_id`",
         "Serverless usage policy ID attached to the jobs. Blank = workspace default. List IDs: databricks budget-policies list.",
         "(set)", "`511dfd24-7efe-312e-8213-04f11f170b29`"],
        ["`wait_and_copy_examples`",
         "true = deploy job waits for ACTIVE and copies examples inline. false = defer to the Copy KA Examples job.",
         "`true`", "`false`"],
        ["`deploy_wait_minutes`",
         "Used when wait_and_copy_examples=true. Max MINUTES to poll each KA for ACTIVE before copying. A per-KA ceiling, not an added delay for every KA (KAs provision in parallel).",
         "`40`", "`40`"],
        ["`copy_wait_minutes`",
         "Max MINUTES the Copy KA Examples job polls each KA for ACTIVE before skipping it. A skipped KA stays Pending and is retried next run.",
         "`3`", "`3`"],
        ["`since_timestamp`",
         "Copy job filter. Set = only rows with completed_at newer than this (all runs). Blank = ALL rows still pending copy. Format YYYY-MM-DDTHH:MM:SS.",
         "(blank)", "`2026-07-16T06:13:58`"],
        ["`cluster_spark_version`",
         "Spark version for CLASSIC compute only (ignored on serverless).",
         "`15.4.x-scala2.12`", "`15.4.x-scala2.12`"],
        ["`cluster_node_type`",
         "Node type for CLASSIC compute only (ignored on serverless).",
         "`Standard_DS3_v2`", "`Standard_DS3_v2`"],
    ],
    [1950, 4350, 1450, 1250],
))
D.append(h2("Per-environment overrides"))
D.append(body("Any variable can be overridden per environment in the targets section. Values not listed "
              "there fall back to the defaults above."))
D.append(code([
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
    "      since_timestamp: \"2026-07-16T06:13:58\"",
]))
D.append(note("Timestamp format is YYYY-MM-DDTHH:MM:SS, e.g. 2026-07-16T06:13:58. "
              "Wait times (deploy_wait_minutes, copy_wait_minutes) are in MINUTES."))
D.append(page_break())

# ===========================================================================
# TAB 4 — Authentication
# ===========================================================================
D.append(h1("4.  Authentication"))
D.append(body("The tool authenticates to two workspaces: the TARGET (where KAs are created) and the "
              "SOURCE (where KAs are exported from). Both follow the same rule — try Service Principal "
              "(SP) first, then fall back to a Personal Access Token (PAT)."))

D.append(h2("Target workspace — SP first, then PAT"))
D.append(body("In GitHub Actions, each deploy/run step tries SP credentials first. If SP is not set OR "
              "the SP command fails, it falls back to the PAT. The logic is:"))
D.append(bullet(run("If ", ) + run("DATABRICKS_CLIENT_ID", mono=True, size=9)
                + run(" and ") + run("DATABRICKS_CLIENT_SECRET", mono=True, size=9)
                + run(" are set → authenticate as the Service Principal (OAuth M2M).")))
D.append(bullet(run("If the SP attempt fails (or is not set) → fall back to the PAT ")
                + run("(STAGING_TOKEN / PROD_TOKEN)", mono=True, size=9) + run(".")))
D.append(bullet("If neither is available → the job errors with a clear message."))
D.append(body("Simplified from .github/workflows/deploy.yml:"))
D.append(code([
    "# Try Service Principal first",
    "if [ -n \"$_CLIENT_ID\" ] && [ -n \"$_CLIENT_SECRET\" ]; then",
    "  export DATABRICKS_CLIENT_ID=\"$_CLIENT_ID\"",
    "  export DATABRICKS_CLIENT_SECRET=\"$_CLIENT_SECRET\"",
    "  if databricks bundle deploy -t staging; then exit 0; fi",
    "  echo \"SP auth failed, falling back to PAT...\"",
    "  unset DATABRICKS_CLIENT_ID DATABRICKS_CLIENT_SECRET",
    "fi",
    "# Fall back to PAT",
    "if [ -n \"$_TOKEN\" ]; then",
    "  export DATABRICKS_TOKEN=\"$_TOKEN\"",
    "  databricks bundle deploy -t staging",
    "fi",
]))

D.append(h2("Source workspace — same pattern, from a secret scope"))
D.append(body("Source credentials are never passed as job parameters (which would be visible in the job "
              "UI). Instead the workflow writes them into a Databricks secret scope, and the notebook "
              "reads them at runtime via build_source_client() in src/common.py. Resolution order:"))
D.append(bullet("Secret scope (keys below) — used inside Databricks jobs."))
D.append(bullet("Explicit CLI args — used for local runs."))
D.append(bullet("Environment variables — final fallback."))
D.append(body("build_source_client() then tries the SP (client-id + client-secret) first; if the SP "
              "connectivity check fails, it falls back to the PAT (source-token)."))
D.append(table(
    ["Secret scope key", "Purpose"],
    [
        ["`source-client-id`", "Source workspace SP client ID (tried first)."],
        ["`source-client-secret`", "Source workspace SP client secret."],
        ["`source-token`", "Source workspace PAT (fallback if SP fails)."],
    ],
    [3200, 5800],
))
D.append(note("The secret scope name comes from the secret_scope variable (default: ka-deployment). "
              "The workflow creates/updates this scope before deploying."))
D.append(page_break())

# ===========================================================================
# TAB 5 — Prerequisites & Permissions Checklist
# ===========================================================================
D.append(h1("5.  Prerequisites & Permissions Checklist"))
D.append(body("Grant these BEFORE running the tool. Requirements are derived from the actual API and SDK "
              "calls the jobs make. TARGET is where KAs are created; SOURCE is where they are exported from."))
D.append(h2("Who needs these permissions — SP or PAT?"))
D.append(body("Each workspace authenticates Service Principal FIRST, then falls back to a PAT if the SP is "
              "not set or fails. So the permissions below must be held by whichever identity actually "
              "authenticates to that workspace:"))
D.append(bullet(run("If you configure the SP", bold=True)
                + run(" → the Service Principal must hold the permissions.")))
D.append(bullet(run("If you rely on the PAT", bold=True)
                + run(" → the PAT owner (user) must hold the permissions.")))
D.append(bullet(run("If you want the fallback to work", bold=True)
                + run(" → grant BOTH the SP and the PAT owner, on each workspace independently "
                      "(target and source are separate identities).")))
D.append(note("This applies to every item below: target items 1–6 and 9 → the TARGET identity; "
              "source items 7–8 → the SOURCE identity."))

D.append(h2("Workspace & platform"))
for b in [
    "TARGET workspace: Unity Catalog enabled, serverless compute enabled, Premium/Enterprise tier.",
    "TARGET workspace: NOT enrolled in the Serverless Access Controls Preview (it blocks KA creation).",
    "Mosaic AI Agent Bricks preview enabled (Admin Console → Workspace Settings → Previews).",
    "Serverless usage policy exists and the running identity has the User role on it (needed for KA / serverless AI).",
    "Both workspaces are in a region where Knowledge Assistant is supported.",
]:
    D.append(bullet(b))

D.append(h2("TARGET identity — Knowledge Assistant (target SP, or target PAT owner on fallback)"))
D.append(body("Held by whichever identity authenticates to the TARGET workspace — the target Service "
              "Principal (DATABRICKS_CLIENT_ID / DATABRICKS_CLIENT_SECRET) if set, otherwise the target "
              "PAT (STAGING_TOKEN / PROD_TOKEN) owner. The job creates KAs via POST/DELETE on the "
              "knowledge-assistants REST API and reads examples/sources."))
for b in [
    "Create, read, update, delete Knowledge Assistants (KA admin / can-manage on the target).",
    "Permission to create the KA's backing serving endpoint (serverless model serving).",
]:
    D.append(bullet(b))

D.append(h2("TARGET identity — Unity Catalog"))
D.append(body("KAs and their knowledge sources live under a catalog/schema, and file-based sources use "
              "UC Volumes. Grants needed on the TARGET catalog and schema:"))
D.append(table(
    ["UC privilege", "On", "Why (code path)"],
    [
        ["`USE CATALOG`", "target catalog", "Resolve/deploy KA and sources under the catalog."],
        ["`USE SCHEMA`", "target schema", "Resolve/deploy KA and sources under the schema."],
        ["`CREATE VOLUME`", "target schema", "volumes.create() when copy_volumes=true (create missing volume)."],
        ["`READ VOLUME`", "target volume(s)", "volumes.read() to verify a volume exists; files.upload()."],
        ["`WRITE VOLUME`", "target volume(s)", "files.upload() copies source files into the target volume."],
        ["USE CATALOG + USE SCHEMA", "index catalog/schema", "vector_search_indexes.get_index() pre-flight check for index sources."],
        ["Vector Search index access", "target index", "Index-based sources must already exist on target and be readable."],
    ],
    [2600, 2400, 4000],
))
D.append(note("If copy_volumes=false, the target volume must already exist and be readable — the job only "
              "verifies it (volumes.read) and will fail if missing."))

D.append(h2("TARGET identity — status tables"))
D.append(body("The jobs create and write two Delta tables (ka_deployment_status, ka_examples_status) under "
              "the target catalog/schema."))
for b in [
    "CREATE TABLE on the target schema (tables are auto-created on first run; ALTER TABLE for migrations).",
    "MODIFY / SELECT on those tables (INSERT and UPDATE status rows).",
]:
    D.append(bullet(b))

D.append(h2("SOURCE identity (source SP, or source PAT owner on fallback)"))
D.append(body("Held by whichever identity authenticates to the SOURCE workspace — the source Service "
              "Principal (source-client-id / source-client-secret) if set, otherwise the source PAT "
              "(source-token) owner. Used to export KA definitions and (optionally) read volume files."))
for b in [
    "Read Knowledge Assistants: GET knowledge-assistants, its knowledge-sources, and examples (can-view on the KAs to migrate).",
    "READ VOLUME on any file-based source volumes (files.list_directory_contents + files.download) when copy_volumes=true.",
    "USE CATALOG / USE SCHEMA on the source catalog/schema backing those volumes.",
]:
    D.append(bullet(b))

D.append(h2("Credentials / secret scope"))
for b in [
    "The CI identity (or you, locally) can create/write the Databricks secret scope named by secret_scope.",
    "Source SP credentials (client id + secret) OR a source PAT are stored under the scope keys "
    "source-client-id, source-client-secret, source-token.",
    "Target auth: SP (DATABRICKS_CLIENT_ID + SECRET) is tried first, then the PAT falls back — provide at least one.",
]:
    D.append(bullet(b))

D.append(h2("Quick pre-flight checklist"))
D.append(table(
    ["#", "Check", "Where"],
    [
        ["1", "SP/PAT can create & manage KAs", "Target workspace"],
        ["2", "User role on a serverless usage policy", "Target workspace"],
        ["3", "USE CATALOG + USE SCHEMA granted", "Target catalog/schema"],
        ["4", "CREATE/READ/WRITE VOLUME (if copy_volumes=true)", "Target schema/volumes"],
        ["5", "Vector index exists & readable (index sources)", "Target"],
        ["6", "CREATE TABLE + MODIFY on status tables", "Target schema"],
        ["7", "Can view/read KAs to migrate + their examples", "Source workspace"],
        ["8", "READ VOLUME on source volumes (if copy_volumes=true)", "Source"],
        ["9", "Secret scope created with source creds", "Target workspace"],
        ["10", "Not enrolled in Serverless Access Controls Preview", "Target workspace"],
    ],
    [500, 6100, 2400],
))
D.append(page_break())

# ===========================================================================
# TAB 6 — GitHub secrets & variables by environment
# ===========================================================================
D.append(h1("6.  GitHub Secrets & Variables by Environment"))
D.append(body("The GitHub Actions workflow uses two environments: staging and production. Create the "
              "secrets under each GitHub Environment (Settings → Environments), except the shared ones "
              "noted below. Names must match exactly."))

D.append(h2("Staging environment (environment: staging)"))
D.append(table(
    ["Name", "Type", "Purpose", "Example"],
    [
        ["`STAGING_HOST`", "Secret", "Target (staging) workspace URL.", "`https://adb-111....azuredatabricks.net`"],
        ["`STAGING_TOKEN`", "Secret", "Target PAT (fallback if SP fails).", "`dapi...`"],
        ["`SOURCE_HOST`", "Secret", "Source workspace URL to export from.", "`https://adb-740....azuredatabricks.net`"],
        ["`SOURCE_TOKEN`", "Secret", "Source PAT (fallback if source SP fails).", "`dapi...`"],
        ["`SOURCE_CLIENT_ID`", "Secret", "Source workspace SP client ID.", "`4b0...c9`"],
        ["`SOURCE_CLIENT_SECRET`", "Secret", "Source workspace SP client secret.", "`dose...`"],
    ],
    [2600, 900, 3500, 2000],
))

D.append(h2("Production environment (environment: production)"))
D.append(table(
    ["Name", "Type", "Purpose", "Example"],
    [
        ["`PROD_HOST`", "Secret", "Target (prod) workspace URL.", "`https://adb-222....azuredatabricks.net`"],
        ["`PROD_TOKEN`", "Secret", "Target PAT (fallback if SP fails).", "`dapi...`"],
        ["`SOURCE_HOST`", "Secret", "Source workspace URL to export from.", "`https://adb-740....azuredatabricks.net`"],
        ["`SOURCE_TOKEN`", "Secret", "Source PAT (fallback if source SP fails).", "`dapi...`"],
        ["`SOURCE_CLIENT_ID`", "Secret", "Source workspace SP client ID.", "`4b0...c9`"],
        ["`SOURCE_CLIENT_SECRET`", "Secret", "Source workspace SP client secret.", "`dose...`"],
    ],
    [2600, 900, 3500, 2000],
))

D.append(h2("Shared across both environments"))
D.append(body("These names are the same in both environments (the workflow reads them from whichever "
              "environment the job runs in). Define them once per environment:"))
D.append(table(
    ["Name", "Type", "Purpose", "Example"],
    [
        ["`DATABRICKS_CLIENT_ID`", "Secret", "Target workspace SP client ID (tried first).", "`4b0...c9`"],
        ["`DATABRICKS_CLIENT_SECRET`", "Secret", "Target workspace SP client secret.", "`dose...`"],
        ["`SECRET_SCOPE`", "Variable", "Secret-scope name to create on target (matches variables.yml).", "`ka-deployment`"],
    ],
    [3000, 900, 3900, 1200],
))
D.append(note("SECRET_SCOPE is a GitHub *Variable* (vars.SECRET_SCOPE), not a Secret. Everything else "
              "above is a Secret. Prod deployment requires manual approval on the production environment."))

D.append(h2("Which name goes where — quick map"))
D.append(table(
    ["Purpose", "Staging name", "Production name"],
    [
        ["Target workspace URL", "`STAGING_HOST`", "`PROD_HOST`"],
        ["Target PAT (fallback)", "`STAGING_TOKEN`", "`PROD_TOKEN`"],
        ["Target SP client ID", "`DATABRICKS_CLIENT_ID`", "`DATABRICKS_CLIENT_ID`"],
        ["Target SP client secret", "`DATABRICKS_CLIENT_SECRET`", "`DATABRICKS_CLIENT_SECRET`"],
        ["Source workspace URL", "`SOURCE_HOST`", "`SOURCE_HOST`"],
        ["Source SP client ID", "`SOURCE_CLIENT_ID`", "`SOURCE_CLIENT_ID`"],
        ["Source SP client secret", "`SOURCE_CLIENT_SECRET`", "`SOURCE_CLIENT_SECRET`"],
        ["Source PAT (fallback)", "`SOURCE_TOKEN`", "`SOURCE_TOKEN`"],
        ["Secret scope name (Variable)", "`SECRET_SCOPE`", "`SECRET_SCOPE`"],
    ],
    [3400, 2800, 2800],
))
D.append(page_break())

# ===========================================================================
# TAB 6 — How to deploy
# ===========================================================================
D.append(h1("7.  How to Deploy (Run the Tool)"))
D.append(h2("Option A — Automatic (CI/CD)"))
D.append(body("Pushing to the main branch triggers the GitHub Actions workflow, which deploys the bundle "
              "and runs the job for staging (prod requires manual approval). Credentials come from the "
              "GitHub environment secrets described in section 6."))
D.append(h2("Option B — Manual (Databricks CLI)"))
D.append(body("1) Deploy the bundle:"))
D.append(code(["databricks bundle deploy -t staging"]))
D.append(body("2) Run the batch deploy job:"))
D.append(code(["databricks bundle run batch_deploy_agents -t staging"]))
D.append(body("3) (Later) run the examples copy job:"))
D.append(code(["databricks bundle run copy_examples_job -t staging"]))
D.append(h2("Choosing compute"))
D.append(body("databricks.yml includes either job_serverless.yml (default) or job_classic.yml. Switch by "
              "changing the include line. Serverless has faster cold starts; classic avoids serverless "
              "networking restrictions for cross-workspace calls."))
D.append(page_break())

# ===========================================================================
# TAB 8 — Running locally
# ===========================================================================
D.append(h1("8.  Running the Tool Locally"))
D.append(body("You can run from your laptop in two ways. Both need the Databricks CLI installed and "
              "authenticated to the TARGET workspace. The key difference is WHERE the code executes, which "
              "changes whether the source secret scope is used."))

D.append(h2("Prerequisites"))
for b in [
    "Databricks CLI installed (databricks -v).",
    "A profile in ~/.databrickscfg for the target workspace, OR the DATABRICKS_HOST / auth env vars set.",
    "The same permissions from section 5 — held by the identity you authenticate as (SP or your user/PAT).",
]:
    D.append(bullet(b))

D.append(h2("Authenticate to the TARGET workspace"))
D.append(body("Pick ONE. A ~/.databrickscfg profile is simplest; env vars also work."))
D.append(body("Profile (recommended):"))
D.append(code([
    "# ~/.databrickscfg",
    "[myprofile]",
    "host  = https://adb-xxxx.7.azuredatabricks.net",
    "token = dapiXXXXXXXXXXXXXXXX          # PAT",
    "# --- or Service Principal (OAuth M2M) instead of token ---",
    "# client_id     = <sp-client-id>",
    "# client_secret = <sp-client-secret>",
]))
D.append(body("Environment variables (alternative):"))
D.append(code([
    "export DATABRICKS_HOST=https://adb-xxxx.7.azuredatabricks.net",
    "export DATABRICKS_TOKEN=dapiXXXXXXXXXXXXXXXX          # PAT",
    "# --- or SP instead of token ---",
    "# export DATABRICKS_CLIENT_ID=<sp-client-id>",
    "# export DATABRICKS_CLIENT_SECRET=<sp-client-secret>",
]))

D.append(h2("Option A — Bundle CLI from local (runs ON Databricks compute)"))
D.append(body("This is the normal local workflow: you trigger the DAB from your laptop, but the job "
              "actually executes on Databricks. Use a target that maps to your workspace profile."))
D.append(code([
    "databricks bundle deploy -t dev -p myprofile",
    "databricks bundle run batch_deploy_agents -t dev -p myprofile",
    "databricks bundle run copy_examples_job  -t dev -p myprofile",
]))
D.append(note("Because the job runs on Databricks (dbutils is available), it reads SOURCE credentials from "
              "the secret scope. So the secret scope AND its keys (source-client-id, source-client-secret, "
              "source-token) MUST already exist on the target workspace before you run. See 'Set up the "
              "secret scope' below."))

D.append(h2("Option B — Direct Python script (runs ON your laptop)"))
D.append(body("Runs the notebook code as a plain script. Requires a Spark session available locally "
              "(e.g. Databricks Connect). Here SOURCE credentials are passed as CLI args or env vars — the "
              "secret scope is NOT used at all (dbutils is absent locally)."))
D.append(code([
    "# target auth from profile/env vars above; pass source creds explicitly",
    "python src/orchestrator.py \\",
    "  --catalog prod_catalog --schema ka_schema \\",
    "  --source-host https://adb-source.7.azuredatabricks.net \\",
    "  --source-client-id <sp-id> --source-client-secret <sp-secret>",
    "",
    "# or a source PAT instead of the SP:",
    "#   --source-token dapiSOURCEXXted",
]))
D.append(body("Equivalent source env vars (used if the args are omitted):"))
D.append(table(
    ["Env var", "Purpose"],
    [
        ["`SOURCE_CLIENT_ID`", "Source SP client ID (tried first)."],
        ["`SOURCE_CLIENT_SECRET`", "Source SP client secret."],
        ["`SOURCE_DATABRICKS_TOKEN`", "Source PAT (fallback if SP not set/failing)."],
    ],
    [3200, 5800],
))

D.append(h2("Do I need the secret scope locally?"))
D.append(table(
    ["How you run", "Where it executes", "Secret scope needed?"],
    [
        ["Option A — bundle run", "Databricks compute", "YES — scope + source keys must exist on target first."],
        ["Option B — python script", "Your laptop", "NO — pass source creds via --source-* args or SOURCE_* env vars."],
    ],
    [2600, 2600, 3800],
))

D.append(h2("Set up the secret scope (only for Option A)"))
D.append(body("Create the scope once on the target workspace and add the source credential keys. The scope "
              "name must match the secret_scope variable (default: ka-deployment)."))
D.append(code([
    "databricks secrets create-scope ka-deployment -p myprofile",
    "databricks secrets put-secret ka-deployment source-client-id \\",
    "  --string-value <sp-id>        -p myprofile",
    "databricks secrets put-secret ka-deployment source-client-secret \\",
    "  --string-value <sp-secret>    -p myprofile",
    "# optional PAT fallback:",
    "databricks secrets put-secret ka-deployment source-token \\",
    "  --string-value dapiSOURCEXXX  -p myprofile",
]))
D.append(note("Credential resolution order in code: secret scope (Databricks runs only) → explicit CLI args "
              "→ SOURCE_* env vars. Locally via Option B, the scope step returns nothing, so args/env vars win."))
D.append(page_break())

# ===========================================================================
# TAB 9 — Status tables
# ===========================================================================
D.append(h1("9.  Status Tracking Tables"))
D.append(h2("ka_deployment_status (deploy job)"))
D.append(table(
    ["Column", "Meaning"],
    [
        ["`run_id`", "Unique ID for the batch run."],
        ["`agent_id`", "Source KA ID processed."],
        ["`target_ka_name`", "Deployed KA resource name on target."],
        ["`status`", "Pending / Deploying / Success / Failed / Skipped."],
        ["`status_desc`", "Human-readable detail of what happened."],
        ["`test_status`", "Pass / Fail / Skipped / N/A."],
        ["`copied_examples`", "Pending / copied message / N/A."],
        ["`completed_at`", "When the deploy finished (2026-07-16T06:13:58)."],
    ],
    [2600, 6400],
))
D.append(h2("ka_examples_status (copy job)"))
D.append(table(
    ["Column", "Meaning"],
    [
        ["`run_id`", "Links back to the deploy run."],
        ["`agent_id`", "Source KA ID."],
        ["`source_example_count`", "Examples in the source KA."],
        ["`target_example_count`", "Verified count on target after copy."],
        ["`copy_status`", "Pending / Copied / Partial / Failed."],
        ["`copy_details`", "Timing, counts, validation notes."],
        ["`updated_at`", "Last update (2026-07-16T06:13:58)."],
    ],
    [2600, 6400],
))
D.append(page_break())

# ===========================================================================
# TAB 8 — Copying examples
# ===========================================================================
D.append(h1("10.  Copying Examples"))
D.append(body("Example questions are copied only after a KA reaches ACTIVE. How this happens is controlled "
              "by the wait_and_copy_examples variable."))
D.append(h2("Inline (wait_and_copy_examples = true)"))
D.append(body("The deploy job creates all KAs, then polls each (every 30s) for ACTIVE up to "
              "deploy_wait_minutes (default 40) and copies its examples. This ceiling is a max wait per "
              "KA, not an added delay for every KA — KAs provision in parallel, so later ones are usually "
              "already ACTIVE."))
D.append(h2("Deferred (wait_and_copy_examples = false, recommended)"))
D.append(body("The deploy job finishes without waiting. Run the Copy KA Examples job later; it polls each "
              "KA up to copy_wait_minutes (default 3) and copies examples for those now ACTIVE."))
D.append(h2("Processing only recent runs (since_timestamp)"))
D.append(body("The copy job accepts an optional since_timestamp. When set, it processes only deploy rows "
              "whose completed_at is newer than that timestamp (across all runs). When blank, it processes "
              "ALL rows still pending copy (copied_examples = 'Pending') across every run."))
D.append(code([
    "databricks bundle run copy_examples_job -t staging \\",
    "  -- --since-timestamp \"2026-07-16T06:13:58\"",
]))
D.append(note("Timestamp format: YYYY-MM-DDTHH:MM:SS (a bare date YYYY-MM-DD also works)."))
D.append(page_break())

# ===========================================================================
# TAB 9 — Troubleshooting
# ===========================================================================
D.append(h1("11.  Troubleshooting"))
D.append(table(
    ["Symptom", "Likely cause & fix"],
    [
        ["\"Knowledge Assistant is not supported ... Serverless Access Controls Preview\"",
         "The workspace is enrolled in the Serverless Access Controls Preview, which blocks KA creation. Disable that preview in the Account Console."],
        ["\"Library installation failed\" on serverless",
         "Do not add databricks-sdk to serverless dependencies — it is pre-installed and the preview blocks PyPI downloads."],
        ["job_id / job_run_id show \"local\"",
         "Running outside a Databricks job. Expected for local CLI runs."],
        ["source_display_name / source_example_count blank",
         "Source KA could not be read. Check source_host and the secret-scope credentials."],
        ["KA skipped unexpectedly",
         "replace_KA is false and a same-named KA already exists. Set replace_KA=true to replace it."],
        ["Examples not copied",
         "KA may not be ACTIVE yet. Re-run the Copy KA Examples job later, optionally with --since-timestamp."],
        ["SP auth fails in CI",
         "Verify DATABRICKS_CLIENT_ID/SECRET are set in the environment; the workflow falls back to PAT (STAGING_TOKEN / PROD_TOKEN)."],
    ],
    [3200, 5800],
))
D.append(note("For deployment status, check the ka_deployment_status table first — status_desc usually "
              "explains exactly what happened."))

document_xml = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>" + "".join(D)
    + '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
      '<w:pgMar w:top="1200" w:right="1440" w:bottom="1200" w:left="1440" '
      'w:header="720" w:footer="720" w:gutter="0"/></w:sectPr>'
    "</w:body></w:document>"
)

# ---------------------------------------------------------------------------
# styles.xml — real Word heading styles (drives the Navigation Pane "tabs")
# ---------------------------------------------------------------------------

def _heading_style(sid, name, level, size, color, bold=True):
    b = "<w:b/>" if bold else ""
    return (
        f'<w:style w:type="paragraph" w:styleId="{sid}">'
        f'<w:name w:val="{name}"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/>'
        f'<w:qFormat/>'
        f'<w:pPr><w:keepNext/><w:keepLines/>'
        f'<w:spacing w:before="{320 if level==1 else 240}" w:after="80"/>'
        f'<w:outlineLvl w:val="{level-1}"/></w:pPr>'
        f'<w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/>{b}'
        f'<w:color w:val="{color}"/><w:sz w:val="{size*2}"/><w:szCs w:val="{size*2}"/></w:rPr>'
        f'</w:style>'
    )

styles_xml = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:docDefaults><w:rPrDefault><w:rPr>'
    f'<w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/>'
    '<w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr></w:rPrDefault></w:docDefaults>'
    # Normal
    '<w:style w:type="paragraph" w:default="1" w:styleId="Normal">'
    '<w:name w:val="Normal"/>'
    f'<w:rPr><w:rFonts w:ascii="{FONT}" w:hAnsi="{FONT}" w:cs="{FONT}"/>'
    f'<w:color w:val="272727"/><w:sz w:val="22"/></w:rPr></w:style>'
    # Headings
    + _heading_style("Heading1", "heading 1", 1, 17, CLR_HEADING)
    + _heading_style("Heading2", "heading 2", 2, 13, CLR_H2)
    + _heading_style("Heading3", "heading 3", 3, 11, CLR_SUBTLE)
    # Title placeholder (title text uses direct formatting; kept for completeness)
    + '<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>'
    '<w:basedOn w:val="Normal"/><w:qFormat/></w:style>'
    '</w:styles>'
)

# ---------------------------------------------------------------------------
# Package parts (with settings.xml so Word refreshes the TOC field on open)
# ---------------------------------------------------------------------------

CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/>'
    "</Types>"
)

RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>"
)

DOC_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'
    "</Relationships>"
)

SETTINGS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    '<w:updateFields w:val="true"/>'
    "</w:settings>"
)

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("[Content_Types].xml", CONTENT_TYPES)
    z.writestr("_rels/.rels", RELS)
    z.writestr("word/_rels/document.xml.rels", DOC_RELS)
    z.writestr("word/document.xml", document_xml)
    z.writestr("word/styles.xml", styles_xml)
    z.writestr("word/settings.xml", SETTINGS)

print(f"Wrote {OUT}")
