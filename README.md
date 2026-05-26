# Healthcare AI Agent

A multi-automation AI agent platform built with **Azure AI Foundry** and **o4-mini** that eliminates manual administrative work across HR onboarding, patient intake, billing triage, and executive reporting. Built as a portfolio project mirroring a real healthcare organization's tech stack.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Azure AI Foundry                            │
│                  o4-mini (GPT-4o family)                        │
│              Chat Completions API — function calling            │
└────────────────────────┬────────────────────────────────────────┘
                         │ orchestrates
          ┌──────────────┼──────────────┬──────────────┐
          ▼              ▼              ▼              ▼
   Automation 1   Automation 2   Automation 3   Automation 4
   Onboarding      Intake         Billing        Reporting
   (PrimeHR)    (DocVault /       Triage         Weekly
                 Doc Intel)    (ClaimBridge)    Insights

          │              │              │              │
          ▼              ▼              ▼              ▼
    Mock FastAPI   Azure Doc      Mock FastAPI   pandas +
    PrimeHR API  Intelligence   ClaimBridge API  matplotlib
    (port 8001)  + Blob Storage  (port 8002)    + o4-mini
          │              │              │              │
          ▼              ▼              ▼              ▼
    Azure Blob    NLM ICD-10    Azure Blob     Azure Comms
    Storage       Public API    Storage        Email
          │              │              │              │
          └──────────────┴──────────────┴──────────────┘
                                  │
                         Azure Table Storage
                           (audit log — all automations)
```

---

## The Four Automations

### Automation 1 — New Employee Onboarding (PrimeHR)
Agent receives a new hire request → fetches employee data from mock PrimeHR API → generates a personalized onboarding PDF → uploads to Azure Blob Storage → notifies manager and employee via Azure Communication Services.

Supports **single employee** runs and **bulk Excel upload** with parallel batch processing (up to 3 employees simultaneously). A live status board tracks each employee in real time with a View PDF button per record.

**Tools:** `get_employee_details` · `fill_onboarding_form` · `store_document` · `notify_manager` · `notify_employee`

---

### Automation 2 — Patient Intake & Document Capture (DocVault)
PDF uploaded to Blob Storage → agent extracts structured fields via Azure Document Intelligence → validates insurance eligibility against mock payer API → stores indexed JSON record back to Blob.

Supports **single patient** runs and **bulk Excel upload** (5 patients processed in parallel). Insurance eligibility is pre-checked and displayed on each patient's status card. View PDF is available per patient.

**Tools:** `extract_document_fields` · `validate_insurance` · `store_indexed_record`

---

### Automation 3 — Billing Workflow Triage (ClaimBridge)
Agent polls exception queue → for each claim: validates ICD-10 code against the NLM public API → classifies rejection reason → auto-resubmits resolvable claims or routes complex ones to billing staff with a priority flag → generates an HTML triage report with dollars recovered vs. at risk → emails the billing manager a summary.

**Tools:** `get_billing_exception_queue` · `get_claim_details` · `validate_icd10_code` · `classify_claim` · `resubmit_claim` · `route_to_staff` · `record_claim_outcome` · `notify_staff_claim_routed` · `generate_billing_report` · `send_billing_summary_email`

---

### Automation 4 — Weekly Report & Admin Email
Agent fetches metrics CSV → o4-mini analyzes trends → matplotlib generates charts → HTML report assembled with inline images → delivered to admin distribution list via Azure Communication Services. Supports financial, clinical, and billing report modes, plus a combined report.

**Tools:** `fetch_data_csv` · `analyze_trends` · `generate_charts` · `build_report` · `send_report_email`

---

## Project Structure

```
healthcare-ai/
├── README.md
├── requirements.txt
├── .env.example                      # Environment variable template (copy to .env)
├── dashboard.py                      # FastAPI web dashboard — main UI for all automations
├── Start Dashboard.bat               # Windows one-click launcher
│
├── agent/
│   ├── agent_runner.py               # Core agent loop — model + tool orchestration
│   ├── bulk_onboarding_runner.py     # Parallel batch processor for bulk onboarding
│   ├── bulk_intake_runner.py         # Parallel batch processor for bulk intake
│   ├── tools/
│   │   ├── onboarding_tools.py       # Automation 1 tool functions
│   │   ├── intake_tools.py           # Automation 2 tool functions
│   │   ├── billing_tools.py          # Automation 3 tool functions
│   │   └── reporting_tools.py        # Automation 4 tool functions
│   └── prompts/
│       ├── onboarding_prompt.txt
│       ├── intake_prompt.txt
│       ├── billing_classifier_prompt.txt
│       └── report_analysis_prompt.txt
│
├── mock_apis/
│   ├── primehr_api.py                # FastAPI stub — HR employee data (port 8001)
│   ├── claimbridge_api.py            # FastAPI stub — billing claims queue (port 8002)
│   ├── generate_data.py              # Faker data generator
│   └── data/
│       ├── employees.json            # 75 synthetic employee records
│       └── claims.csv                # 300 synthetic billing claims
│
├── data/
│   ├── bulk_onboarding_template.xlsx # Excel template for bulk employee onboarding
│   ├── bulk_intake_template.xlsx     # Excel template for bulk patient intake
│   └── sample_intake.pdf             # Synthetic patient intake PDF
│
├── scripts/
│   └── generate_intake_pdf.py        # Generates + uploads synthetic intake PDF to Blob
│
├── reporting/
│   ├── chart_generator.py            # matplotlib bar/line/pie chart rendering
│   ├── report_builder.py             # HTML report assembly with embedded charts
│   ├── report_combiner.py            # Combined multi-section report builder
│   ├── email_sender.py               # Azure Communication Services delivery
│   └── templates/                    # HTML report templates
│
└── audit/
    └── audit_logger.py               # Azure Table Storage + local JSONL fallback
```

---

## Setup

### 1. Clone and install dependencies

```bash
git clone <repo-url>
cd healthcare-ai
pip install -r requirements.txt
```

### 2. Configure environment variables

Copy `.env.example` to `.env` and fill in your Azure credentials:

```bash
cp .env.example .env
```

| Variable | Description |
|---|---|
| `AZURE_OPENAI_ENDPOINT` | Azure OpenAI endpoint URL |
| `AZURE_API_KEY` | Azure AI / OpenAI API key |
| `MODEL_DEPLOYMENT_NAME` | Model deployment name (e.g. `o4-mini`) |
| `DOC_INTELLIGENCE_ENDPOINT` | Azure Document Intelligence endpoint |
| `DOC_INTELLIGENCE_KEY` | Azure Document Intelligence key |
| `AZURE_STORAGE_CONNECTION_STRING` | Azure Blob + Table Storage connection string |
| `AZURE_COMMS_CONNECTION_STRING` | Azure Communication Services connection string |
| `ADMIN_EMAIL_SENDER` | Verified sender address for ACS |
| `ADMIN_EMAIL_RECIPIENTS` | Admin recipient email address |

### 3. Generate mock data

```bash
python mock_apis/generate_data.py
```

Outputs: `mock_apis/data/employees.json`, `mock_apis/data/claims.csv`, `data/weekly_metrics.csv`

### 4. Start the mock APIs

Open two terminals:

```bash
# Terminal 1 — PrimeHR HR API
uvicorn mock_apis.primehr_api:app --port 8001 --reload

# Terminal 2 — ClaimBridge Billing API
uvicorn mock_apis.claimbridge_api:app --port 8002 --reload
```

Interactive docs available at `http://localhost:8001/docs` and `http://localhost:8002/docs`.

### 5. Launch the dashboard

```bash
python dashboard.py
```

Then open `http://localhost:8000` in your browser.

**Windows shortcut:** double-click `Start Dashboard.bat` — opens the browser and starts the dashboard (which launches the mock APIs automatically).

---

## Running the Automations

The primary interface is the **web dashboard** at `http://localhost:8000`. Each automation has a card with:
- A **Run** button for single runs
- A **Bulk Upload** section for Excel-based batch processing
- A **live status board** showing real-time progress per record
- **View PDF** / **View Report** buttons for generated outputs

### CLI (advanced / headless)

```bash
# Automation 1 — Onboard a single employee
python -m agent.agent_runner --automation onboarding --employee-id EMP-0023

# Automation 2 — Process a patient intake PDF
python scripts/generate_intake_pdf.py        # generates PDF + uploads to Blob, prints SAS URL
python -m agent.agent_runner --automation intake --blob-url <sas-url>

# Automation 3 — Triage billing exception queue
python -m agent.agent_runner --automation billing

# Automation 4 — Generate and email weekly report
python -m agent.agent_runner --automation report --mode financial
python -m agent.agent_runner --automation report --mode clinical
python -m agent.agent_runner --automation report --mode billing
```

---

## Mock API Reference

### PrimeHR HR API — `http://localhost:8001`

| Method | Endpoint | Description |
|---|---|---|
| GET | `/employees` | List employees — filter by `department`, `status`, `onboarding_status` |
| GET | `/employees/{id}` | Full employee record |
| GET | `/employees/{id}/onboarding` | Onboarding checklist (completed vs. pending docs) |
| POST | `/employees/{id}/onboarding/complete` | Mark a document step as completed |
| GET | `/health` | Health check |

### ClaimBridge Billing API — `http://localhost:8002`

| Method | Endpoint | Description |
|---|---|---|
| GET | `/claims` | List claims — filter by `status`, `department`, `payer`, `priority` |
| GET | `/claims/exceptions` | Active exception/denial queue sorted by priority |
| GET | `/claims/stats` | Aggregate billing statistics |
| GET | `/claims/{id}` | Full claim record |
| POST | `/claims/{id}/resubmit` | Auto-resubmit with corrected data |
| POST | `/claims/{id}/route` | Route to staff member with priority flag |
| PATCH | `/claims/{id}/status` | Update claim status |
| GET | `/health` | Health check |

---

## Synthetic Data

All data is 100% synthetic — no real patient or employee information is used anywhere in this project.

| Dataset | Records | Generator |
|---|---|---|
| Employee records | 75 | Python `Faker` — realistic healthcare staff across 10 departments |
| Billing claims | 300 | Python `Faker` + real ICD-10/CPT codes — 101 in exception queue |
| Weekly metrics | 120 rows | Calculated — 12 weeks × 10 departments with realistic variance |
| Bulk onboarding template | 5 employees | Matches full employee schema — ready to upload |
| Bulk intake template | 5 patients | Matches full patient intake schema — includes one ineligible patient |

---

## HIPAA / Compliance Notes

- All data is synthetic — no real patient or employee data anywhere in this project
- All Azure services remain within the Azure compliance boundary
- Every agent tool call writes an audit log entry to Azure Table Storage (timestamp, action, entity ID, input hash, output summary)
- In a real deployment, Azure's HIPAA Business Associate Agreement (BAA) would be signed before processing any PHI
- Document Intelligence processes PDFs entirely within Azure — no data sent to third-party APIs

---

## Key Technologies

| Technology | Role |
|---|---|
| Azure AI Foundry | Agent orchestration, model hosting |
| Azure OpenAI / o4-mini | Reasoning, classification, report analysis |
| Azure Document Intelligence | OCR and structured field extraction from intake PDFs |
| Azure Blob Storage | Onboarding docs, intake PDFs, report files |
| Azure Table Storage | Audit log for all four automations |
| Azure Communication Services | Email delivery — manager alerts, billing summaries, weekly reports |
| FastAPI | Web dashboard, mock PrimeHR HR API, mock ClaimBridge billing API |
| pandas | Metrics ingestion, cleaning, and statistical summarization |
| matplotlib | Bar, line, and pie chart generation for weekly reports |
| openpyxl | Bulk Excel upload parsing for onboarding and intake |
| Python Faker | Synthetic employee and billing claims data generation |
| fpdf2 | Synthetic patient intake PDF generation |
