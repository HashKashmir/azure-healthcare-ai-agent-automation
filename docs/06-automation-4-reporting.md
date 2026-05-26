# Automation 4 — Weekly Report & Admin Email

This document explains every file and function in Automation 4. You will understand how raw metrics data becomes AI-generated insights, matplotlib charts, a formatted HTML report, and an email in your inbox — and why each step is designed the way it is.

---

## What Problem Does This Solve?

Healthcare administrators typically receive a weekly operational report. Producing it manually involves:

1. Exporting data from multiple systems
2. Cleaning and aggregating it
3. Calculating week-over-week changes for each metric
4. Identifying which departments are above or below benchmarks
5. Writing narrative analysis of what the numbers mean
6. Building charts in Excel or a BI tool
7. Formatting everything into a readable document
8. Emailing it to the distribution list

This takes hours. Automation 4 does it in 60–90 seconds. The AI (o4-mini) handles the interpretation and recommendation writing — the parts that actually require analytical judgment. The Python code handles the data and chart work.

---

## Report Modes

Automation 4 supports three distinct report modes, each targeting a different audience:

| Mode | Audience | Key Metrics |
|---|---|---|
| `financial` | CFO / Finance Administration | Revenue, collections rate, A/R days, denial revenue impact, no-show revenue loss |
| `clinical` | Operations Director | No-show rate, patient volume, new patient rate, staff utilization, wait times |
| `billing` | Billing Manager | Rejection rate, auto-resolve rate, dollars at risk, approval trajectory |

A fourth mode (`all`) runs all three in sequence and combines them into a single tabbed HTML report using `reporting/report_combiner.py`.

---

## Files Involved

| File | Role |
|---|---|
| `agent/tools/reporting_tools.py` | 5 tool functions + all data analysis logic |
| `agent/prompts/report_analysis_prompt.txt` | System instructions for the reporting agent |
| `reporting/chart_generator.py` | matplotlib chart rendering (3 charts per mode) |
| `reporting/report_builder.py` | HTML report assembly with embedded charts |
| `reporting/report_combiner.py` | Combines all three reports into a tabbed view |
| `reporting/email_sender.py` | Azure Communication Services delivery |
| `reporting/templates/` | HTML template files |
| `data/weekly_metrics.csv` | The source data (12 weeks × 10 departments) |

---

## The Weekly Metrics Data — `data/weekly_metrics.csv`

This CSV is the input to the entire reporting pipeline. It has 120 rows (12 weeks × 10 departments) and 16 columns:

| Column | Description |
|---|---|
| `week_start_date` | Monday of the reporting week |
| `department` | One of 10 departments (Cardiology, Orthopedics, etc.) |
| `total_visits` | Total patient visits that week |
| `no_show_count` | Patients who didn't show up |
| `new_patients` | First-time patients |
| `staff_count` | Number of clinical staff |
| `avg_wait_minutes` | Average patient wait time |
| `claims_submitted` | Claims filed to insurance |
| `claims_approved` | Claims paid by insurance |
| `claims_denied` | Claims rejected |
| `rejection_rate` | Percentage of claims denied |
| `auto_resolve_pct` | Percentage of denials resolved automatically |
| `avg_ar_days` | Average accounts receivable days outstanding |
| `total_revenue` | Total billed revenue |
| `collections_amount` | Revenue actually collected |

The data has a slight upward trend (week-over-week `trend = 1 + (w * 0.005)`) plus random noise, making it look realistic — not perfectly linear, but with a general improving direction.

---

## Tool 1 — `fetch_data_csv`

### What It Does

Loads the weekly metrics CSV into memory and filters it to the most recent 4 weeks.

```python
def fetch_data_csv(report_mode: str) -> str:
    # Try Azure Blob Storage first
    if conn_str:
        svc = BlobServiceClient.from_connection_string(conn_str)
        client = svc.get_blob_client(container="data-csv", blob=f"weekly_metrics_{report_mode}.csv")
        csv_text = client.download_blob().readall().decode("utf-8")

    # Fall back to local file
    if csv_text is None:
        with open("data/weekly_metrics.csv") as f:
            csv_text = f.read()

    df = pd.read_csv(StringIO(csv_text))

    # Filter to 4 most recent weeks
    if "week_start_date" in df.columns:
        df["week_start_date"] = pd.to_datetime(df["week_start_date"])
        df = df.sort_values("week_start_date")
        latest_4 = df["week_start_date"].unique()[-4:]
        df = df[df["week_start_date"].isin(latest_4)]

    # Store in module-level session cache
    _SESSION[report_mode] = {"df": df}
```

### Why 4 Weeks?

The report needs:
- **This week** vs. **prior week** for week-over-week comparisons
- **First 2 weeks** vs. **last 2 weeks** for 4-week trend direction (improving / stable / worsening)

4 weeks gives exactly what's needed for both comparisons. Using all 12 weeks would dilute the trend signals.

### The Session Cache

```python
_SESSION: dict = {}    # module-level, keyed by report_mode

_SESSION[report_mode] = {"df": df}
```

The session cache passes large objects (DataFrames) between tools without sending them through the AI. The model only passes `report_mode` between steps — the actual data stays in Python memory. This is critical: you cannot put a full DataFrame in a model message (it would be enormous). Instead, the model is told "call analyze_trends with the same report_mode" and the tool picks up the data from the session.

---

## Tool 2 — `analyze_trends`

### What It Does

Computes all the analytical signals from the data, formats them into a structured text brief, sends the brief to o4-mini for interpretation, and returns the AI-generated insights.

This is the step where AI actually adds value — it reads pre-computed numbers and writes human-quality analysis with recommendations.

### Step 1: Compute Signals — `_compute_signals(df, report_mode)`

Before the AI sees anything, Python calculates every number the AI will need to comment on:

```python
def _compute_signals(df, report_mode: str) -> dict:
    weeks = sorted(df["week_start_date"].unique())
    this_week  = weeks[-1]
    prior_week = weeks[-2]
    early_df = df[df["week_start_date"].isin(weeks[:mid])]  # first 2 weeks
    late_df  = df[df["week_start_date"].isin(weeks[mid:])]  # last 2 weeks

    # Financial signals example:
    rev_t = tot(this_df, "total_revenue")
    rev_p = tot(prior_df, "total_revenue")
    s["revenue_delta_pct"] = pct(rev_t, rev_p)      # week-over-week % change
    s["revenue_trend"] = trend(early_df, late_df, "total_revenue")  # improving/stable/worsening

    dept_rev_t = this_df.groupby("department")["total_revenue"].sum()
    s["top3_revenue_depts"] = [(d, v) for d, v in dept_rev_t.nlargest(3).items()]
    s["biggest_revenue_drop"] = (min_dept, min_change)
```

The helper functions:
- `tot(d, col)` — sum a column across all departments
- `wtd_rate(d, num, den)` — weighted rate (e.g., denial rate = denied / submitted × 100)
- `wtd_avg(d, val, wt)` — weighted average (e.g., A/R days weighted by claims submitted)
- `pct(new, old)` — percentage change: `(new - old) / |old| × 100`
- `pp(new, old)` — percentage point change (for rates): `new - old`
- `trend(e, l, col)` — compares early vs. late weeks, returns "improving" / "stable" / "worsening"

**Why pre-compute instead of just giving the AI the raw data?**

Two reasons:
1. Sending 120 rows of CSV to the model would use enormous amounts of tokens and be expensive
2. The AI is not reliable for arithmetic. LLMs can make calculation errors, especially on multi-step math. By pre-computing all the numbers in Python (which never makes arithmetic errors), we guarantee the AI is interpreting correct numbers — its job is interpretation and writing, not calculation

### Step 2: Format the Brief — `_format_brief(signals, report_mode)`

The signals dictionary is formatted into a structured text summary — what you might give a smart analyst who needs to write a report:

```
FINANCIAL SIGNALS — Week of 2025-05-19 vs 2025-05-12

REVENUE:
  Facility total: $1,247,832 vs $1,218,440 (+2.4%). 4-week trend: IMPROVING.
  Top 3 departments: Cardiology ($187,432), Oncology ($165,221), Emergency Medicine ($143,009).
  Biggest drop: Mental Health (-$12,430). Biggest gain: Radiology (+$18,221).

REVENUE PER VISIT:
  Facility: $712.45 vs $698.32 (+2.0%).
  Lowest efficiency dept: Physical Therapy ($312.11/visit).

COLLECTIONS:
  Rate: 81.3% vs 84.2% (-2.9pp). 4-week trend: WORSENING.
  Uncollected this week: $233,840.
  Lowest collections rate dept: Mental Health (72.1%).

A/R DAYS (35-day threshold):
  Facility avg: 32.4d vs 30.1d (+2.3d). 4-week trend: WORSENING.
  Above threshold: Oncology (38.2d), Neurology (36.7d).

DENIAL REVENUE IMPACT:
  Revenue at risk from denials: $48,230 vs $41,870 (+15.2%).

NO-SHOW REVENUE LOSS:
  Estimated: $89,412. Worst dept: Emergency Medicine ($22,318 lost).
```

This is not raw data. It is pre-interpreted, formatted, and contextual. The AI reads this and writes the analysis.

### Step 3: Send to o4-mini for Interpretation

```python
response = client.chat.completions.create(
    model=model,
    messages=[
        {"role": "system", "content": _SYSTEM_PROMPTS[report_mode]},
        {"role": "user",   "content": brief},
    ],
    response_format={"type": "json_object"},
)
parsed = json.loads(response.choices[0].message.content)
insights = parsed.get("insights", parsed)
```

**`response_format={"type": "json_object"}`** forces the model to return valid JSON. This is a reliability feature — without it, the model might add explanatory text around the JSON or format it inconsistently.

### The System Prompts — Role-Specific Personas

Each mode uses a different prompt persona:

**Financial:**
> "You are a CFO-level healthcare financial analyst reviewing a pre-computed weekly brief. Interpret the signals, explain business impact, and prioritize. Return a JSON object with an 'insights' array of exactly 4-5 items ranked by business impact. Each item: severity (critical|warning|ok|info), title (≤10 words, cite a number), detail (2-3 sentences with specific dollar amounts and percentages from the brief), recommendation (one specific, actionable step). Critical = immediate cash-flow risk >$50k or deteriorating A/R."

**Clinical:**
> "You are a healthcare operations director reviewing a pre-computed weekly clinical brief..."

**Billing:**
> "You are a billing manager for a healthcare organization reviewing a pre-computed weekly brief..."

The personas ensure the AI frames its analysis appropriately. A CFO cares about cash flow and A/R. An operations director cares about no-show rates and staff overload. A billing manager cares about rejection rates and auto-resolve performance. The same data, different lens.

### What o4-mini Returns

```json
{
  "insights": [
    {
      "severity": "critical",
      "title": "Collections rate fell 2.9pp — $233k uncollected",
      "detail": "The facility-wide collections rate dropped from 84.2% to 81.3% this week, leaving $233,840 uncollected. A/R days are simultaneously trending upward at 32.4 days facility-wide, with Oncology (38.2d) and Neurology (36.7d) exceeding the 35-day threshold. This combination suggests a systemic collections workflow issue.",
      "recommendation": "Pull the A/R aging report immediately and prioritize follow-up calls on Oncology and Neurology accounts over 35 days. Review the collections workflow for any process changes made in the last 2 weeks."
    },
    {
      "severity": "warning",
      "title": "Denial revenue risk up 15.2% — $48k at risk",
      "detail": "Revenue at risk from claim denials increased $6,360 week-over-week to $48,230. If this trend continues for 3 more weeks, denial risk will breach $50k — a critical threshold. Mental Health has both the lowest collections rate (72.1%) and a meaningful revenue contribution, making it a priority audit target.",
      "recommendation": "Audit the top denial categories in Mental Health and Emergency Medicine. Check whether prior-auth workflows have changed recently."
    },
    ...
  ]
}
```

Each insight has:
- `severity` — determines the color and icon in the report (`critical` = red, `warning` = orange, `ok` = green, `info` = blue)
- `title` — a concise headline with a specific number (≤10 words)
- `detail` — 2-3 sentences with context and department-specific callouts
- `recommendation` — one specific, actionable step

### Fallback When API Is Unavailable

If the Azure OpenAI endpoint is not configured, a set of pre-written mock insights is returned:

```python
if insights is None:
    mock = {
        "financial": [
            {"severity": "warning", "title": "Collections rate fell 2.9pp this week", ...},
            ...
        ],
        ...
    }
    insights = mock.get(report_mode)
```

The mock insights are generic but structurally correct — the report still generates and emails correctly even without a live AI connection.

### Dashboard Protocol

```python
for insight in insights:
    print(f"REPORT_INSIGHT:{json.dumps({'_mode': report_mode, **insight})}", flush=True)
```

The dashboard parses lines starting with `REPORT_INSIGHT:` and renders insight cards in real time as the agent processes them, before the full report is even built.

---

## Tool 3 — `generate_charts`

### What It Does

Calls `reporting/chart_generator.py` to produce 3 matplotlib PNG charts for the report mode.

```python
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from reporting.chart_generator import generate_report_charts

chart_paths = generate_report_charts(session["df"], report_mode)
_SESSION[report_mode]["chart_paths"] = chart_paths
```

The chart file paths are stored in the session so `build_report` can find them.

### `reporting/chart_generator.py` — The Three Charts

Each report mode has three charts, each paired with a specific metric the AI will comment on:

**Financial:**
1. **Revenue Bar** — side-by-side bars, this week vs. prior week, by department
2. **Collections Rate Line** — 4-week trend with facility average dashed line
3. **A/R Days Bar** — by department, red bars exceed the 35-day threshold line

**Clinical:**
1. **No-Show Rate Bar** — by department, red bars exceed the 10% benchmark
2. **No-Show Rate Line** — 4-week trend with 10% benchmark line
3. **Staff Utilization Bar** — visits per staff member, red bars exceed 7:1 threshold

**Billing:**
1. **Rejection Rate Bar** — by department, with 10% warning and 15% critical threshold lines
2. **Rejection Rate Line** — 4-week trend with 10% benchmark
3. **Auto-Resolve Rate Bar** — by department, green bars at/above 68% target

### Color Coding

All charts use consistent color logic:

```python
COLOR_PRIMARY   = "#1565C0"   # blue — normal/default
COLOR_SECONDARY = "#90CAF9"   # light blue — prior week comparison
COLOR_WARNING   = "#E53935"   # red — threshold exceeded
COLOR_CRITICAL  = "#B71C1C"   # dark red — critical threshold
COLOR_OK        = "#43A047"   # green — at or above target
COLOR_NEUTRAL   = "#757575"   # grey — reference lines
```

A bar chart for A/R days:
```python
colors = [COLOR_WARNING if v > threshold else COLOR_PRIMARY for v in vals]
# Bars above the threshold are red; below are blue
```

### Chart Output

Charts are saved to `data/report_charts/` with timestamped filenames like `financial_revenue_bar_20250525_143200.png`. Multiple runs create multiple files; the charts for a specific report are identified by the timestamp embedded in the session data.

**`matplotlib.use("Agg")`** at the top of the file sets a non-interactive backend. Without this, matplotlib would try to open a GUI window, which would crash on a headless server or in a subprocess. The "Agg" backend renders to PNG files without displaying anything.

---

## Tool 4 — `build_report`

### What It Does

Assembles the final HTML report by:
1. Converting AI insights into styled HTML cards
2. Converting chart PNG files into base64-encoded inline images
3. Wrapping everything in a professional HTML template
4. Optionally converting to PDF with WeasyPrint
5. Uploading to Azure Blob Storage with a 7-day SAS URL

### Insight Cards

```python
for item in insights:
    sev  = item.get("severity", "info")
    icon, text_color, bg_color = SEVERITY_ICON.get(sev, SEVERITY_ICON["info"])
    # icon: ⚠ for critical/warning, ✓ for ok, ◆ for info
    # text_color: #B71C1C for critical, #E65100 for warning, etc.
    # bg_color:   #FFEBEE for critical, #FFF3E0 for warning, etc.
    insight_html += f"""
    <div style="background:{bg_color};border-left:4px solid {text_color};...">
      <div style="color:{text_color};font-weight:700;">{icon} {item['title']}</div>
      <div>{item['detail']}</div>
      <div><strong>Recommendation:</strong> {item['recommendation']}</div>
    </div>"""
```

Each insight becomes a color-coded card with a left border in the severity color, the icon, title, detail, and recommendation.

### Inline Image Embedding

```python
for path in chart_paths:
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    chart_html += f'<img src="data:image/png;base64,{b64}" style="max-width:100%;" />'
```

Chart images are embedded directly in the HTML as base64-encoded strings. This means the HTML file is self-contained — you can open it anywhere without needing the PNG files to be present. It also makes the email work without hosting the images on a server.

### PDF Generation (Optional)

```python
def _try_pdf(html: str, report_id: str) -> str | None:
    try:
        from weasyprint import HTML
        pdf_path = os.path.join(OUTPUT_DIR, f"{report_id}.pdf")
        HTML(string=html).write_pdf(pdf_path)
        return pdf_path
    except Exception:
        return None
```

WeasyPrint converts HTML to PDF. If WeasyPrint is not installed (it requires system fonts), this step is silently skipped. The HTML report is always generated; the PDF is a bonus.

### Report ID

Each report gets a unique ID: `RPT-20250525-143200-FINANCIAL`. This ID is the filename, the blob name, and is displayed in the report header for reference.

---

## Tool 5 — `send_report_email`

### What It Does

Calls `reporting/email_sender.py` to deliver the HTML report to all configured recipients.

```python
from reporting.email_sender import send_report

recipients_str = os.getenv("ADMIN_EMAIL_RECIPIENTS", "")
recipient_list = [r.strip() for r in recipients_str.split(",") if r.strip()]

result = send_report(session["report_path"], report_mode, recipient_list)
```

### `reporting/email_sender.py` — CID Image Handling

Sending an HTML email with base64-embedded images has a compatibility problem: some email clients (especially Outlook) don't render large base64 `src` attributes correctly. The email sender converts these to CID (Content-ID) references — the standard email inline attachment format.

```python
def _extract_cid_attachments(html_body: str) -> tuple:
    attachments = []
    counter = [0]

    def replacer(match):
        idx = counter[0]
        counter[0] += 1
        cid     = f"chart{idx}"
        b64data = match.group(1)
        attachments.append({
            "name":            f"chart{idx}.png",
            "contentType":     "image/png",
            "contentInBase64": b64data,
            "contentId":       cid,
        })
        return f'src="cid:{cid}"'

    pattern  = r'src="data:image/png;base64,([^"]+)"'
    new_html = re.sub(pattern, replacer, html_body)
    return new_html, attachments
```

This regex finds every base64-encoded PNG `<img>` tag, extracts the base64 data, replaces `src="data:..."` with `src="cid:chart0"`, and creates an attachment entry. When the email is sent, the images are attached separately and the email client reassembles them.

### Azure Communication Services Send

```python
client  = EmailClient.from_connection_string(conn_str)
message = {
    "senderAddress": sender,
    "recipients":    {"to": to_list},
    "content":       {"subject": subject, "html": email_html},
    "attachments":   inline_attachments,
}
poller = client.begin_send(message)
result = poller.result()
```

The `attachments` key with `contentId` values tells ACS that these are inline attachments (displayed inside the HTML) rather than file downloads.

---

## `reporting/report_combiner.py` — The Combined Report

When you click "Run All Reports" or use `--mode all`, all three reports are run sequentially, then `build_combined_report()` is called.

The combiner:
1. Finds the most recent Financial, Clinical, and Billing HTML reports in `data/reports/`
2. Extracts the insight cards and chart sections from each one by parsing the HTML with regex
3. Assembles a single HTML file with a tabbed interface (three tabs: Financial, Clinical, Billing)
4. The tab switching is handled by inline JavaScript

This produces one file that a healthcare administrator can open and navigate between all three report modes.

---

## The Complete Flow — Financial Report

```
User clicks "Run Report — Financial" in dashboard
        │
        ▼
agent_runner sends to o4-mini:
"Run the weekly financial report pipeline. Fetch the metrics data, analyze it,
generate charts, build the report, and email it..."
        │
Model: call fetch_data_csv("financial")
        │
        ▼
fetch_data_csv
        ├── downloads weekly_metrics_financial.csv from Azure Blob (or reads local file)
        ├── filters to most recent 4 weeks
        ├── stores DataFrame in _SESSION["financial"]["df"]
        └── returns: row_count, week_range, columns
        │
Model: call analyze_trends("financial")
        │
        ▼
analyze_trends
        ├── reads _SESSION["financial"]["df"]
        ├── _compute_signals(): calculates ~25 financial metrics
        ├── _format_brief(): writes structured text brief
        ├── sends brief to o4-mini with CFO-level analyst persona
        ├── parses JSON response → 4-5 insight objects
        ├── stores insights in _SESSION["financial"]["insights"]
        ├── prints REPORT_INSIGHT:{json} for each insight (dashboard cards)
        └── returns: insight_count, insights array
        │
Model: call generate_charts("financial")
        │
        ▼
generate_charts → chart_generator.py
        ├── generates Revenue Bar chart (PNG)
        ├── generates Collections Rate Line chart (PNG)
        ├── generates A/R Days Bar chart (PNG)
        ├── stores chart_paths in _SESSION["financial"]["chart_paths"]
        └── returns: chart_count, chart_paths
        │
Model: call build_report("financial")
        │
        ▼
build_report → report_builder.py
        ├── reads insights and chart_paths from _SESSION
        ├── converts insights → styled HTML cards
        ├── converts chart PNGs → base64-embedded <img> tags
        ├── wraps in professional HTML template
        ├── saves to data/reports/RPT-{timestamp}-FINANCIAL.html
        ├── (optionally) converts to PDF via WeasyPrint
        ├── uploads HTML to Azure Blob Storage (7-day SAS URL)
        ├── stores report_path and report_url in _SESSION
        └── returns: report_id, html_path, report_url
        │
Model: call send_report_email("financial")
        │
        ▼
send_report_email → email_sender.py
        ├── reads report HTML from disk
        ├── extracts base64 images → CID attachments
        ├── sends via Azure Communication Services
        └── returns: status, message_ids, recipients
        │
Model: finish_reason="stop" → writes final text summary
        │
dashboard shows: View Report button + insight cards displayed in real time
```

---

## Key Design Decisions Explained

### Why Does Python Compute the Signals Instead of Letting the AI Calculate From Raw Data?

Three reasons:
1. **Accuracy** — LLMs make arithmetic errors on large numbers and multi-step calculations. Python never does.
2. **Cost** — Sending 120 rows of data to the model costs many tokens. Sending a 40-line text brief costs very few.
3. **Control** — By computing the signals ourselves, we decide exactly what the AI sees. We can ensure it always has week-over-week comparisons, trend directions, and threshold breach flags — not whatever it might notice (or miss) in raw data.

The AI's job is to interpret, prioritize, and communicate — tasks it's excellent at. Arithmetic is Python's job.

### Why Are Charts Generated in Python (matplotlib) Rather Than by the AI?

The AI cannot generate images. It produces text. Charts must be generated programmatically. matplotlib is the standard Python charting library — it produces publication-quality PNG charts that work well in HTML emails and browser-rendered reports.

### Why Embed Charts as Base64 in the HTML Instead of Linking to Files?

A self-contained HTML file works everywhere: you can open it offline, attach it to an email, or host it on a server. If charts were linked as separate files (`<img src="chart.png">`), the viewer would need access to those files — which breaks when the report is emailed or copied to a different computer.

### Why Is the Weekly Metrics CSV a Single File (Not Mode-Specific CSVs)?

The single `weekly_metrics.csv` contains all the columns needed by all three report modes. All three modes read the same file and use different subsets of columns. In the Azure Blob download, mode-specific filenames are tried (`weekly_metrics_financial.csv`) to allow uploading separate pre-computed files, but both approaches work with the combined local file.
