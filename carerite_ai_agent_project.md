# Carerite Healthcare Admin AI Agent — Full Project Guide

> A portfolio project built to demonstrate AI agent automation skills for a healthcare administration role. Mirrors Carerite's actual tech stack: Azure, Azure AI Foundry, Viventium, Docstar, Megastar, and SVV.

---

## Project Overview

Build a multi-automation AI agent using **Azure AI Foundry** that eliminates manual administrative work across HR, document management, billing, and executive reporting. The agent orchestrates tool calls across mocked and real APIs, maintains an audit trail, and delivers weekly insight reports with charts to administrators via email.

**Core technologies:** Azure AI Foundry · Azure OpenAI (GPT-4o) · Azure Document Intelligence · Azure Blob Storage · Azure Communication Services · Azure Functions · Python · FastAPI · pandas · matplotlib

---

## Resume Bullet Points

Use these when adding the project to your resume:

- Built a multi-step AI agent using Azure AI Foundry that automated 4 healthcare admin workflows across HR, document management, billing, and reporting systems
- Integrated Azure Document Intelligence for OCR-based patient intake with structured data extraction and audit logging
- Designed mock REST APIs mirroring Viventium and Docstar schemas to demonstrate real-world integration readiness
- Extended the agent with an automated reporting module that ingests financial and operational CSVs, runs GPT-4o-powered trend analysis, and emails formatted PDF insight reports with week-over-week charts to administrators on a weekly schedule
- Used pandas for data normalization and anomaly detection preprocessing, with Azure OpenAI surfacing prioritized actionable insights per report run
- Delivered reports via Azure Communication Services with full audit logging in Azure Table Storage for HIPAA-aligned traceability
- Agent auto-resolved 68% of billing exceptions, reducing simulated manual triage time by approximately 6 hours per week

---

## The Four Automations

### Automation 1 — New Employee Onboarding (Viventium)

**What it does:**
The agent receives an HR onboarding request, pulls employee information from a mocked Viventium API, auto-fills onboarding forms, routes completed documents to Docstar-style storage, and notifies the manager via email or Microsoft Teams.

**Tech used:**
- Azure AI Foundry (agent orchestration + tool calling)
- Azure Logic Apps (workflow trigger)
- FastAPI stub (mocked Viventium HR API)
- Python `Faker` library (synthetic employee data)
- Azure Blob Storage (document storage)
- Azure Communication Services (email notification)

**Key agent steps:**
1. Trigger received (new hire request payload)
2. Agent calls `get_employee_details()` tool → hits mock Viventium API
3. Agent calls `fill_onboarding_form()` tool → generates completed form
4. Agent calls `store_document()` tool → uploads to Azure Blob Storage
5. Agent calls `notify_manager()` tool → sends email via Azure Communication Services
6. Audit entry written to Azure Table Storage

---

### Automation 2 — Patient Intake & Document Capture (Docstar)

**What it does:**
The agent reads an uploaded intake PDF, extracts structured patient data using Azure Document Intelligence, cross-checks insurance eligibility, and stores the indexed record in a Docstar-style document management system.

**Tech used:**
- Azure Document Intelligence (OCR + field extraction)
- Azure Blob Storage (PDF input and indexed record output)
- Azure AI Foundry (agent orchestration)
- Synthea (synthetic patient data source)
- CMS.gov forms (real PDF intake form templates)

**Key agent steps:**
1. PDF uploaded to Azure Blob Storage trigger
2. Agent calls `extract_document_fields()` tool → Azure Document Intelligence processes PDF
3. Agent calls `validate_insurance()` tool → cross-checks extracted insurance ID against mock eligibility API
4. Agent calls `store_indexed_record()` tool → saves structured JSON record
5. Audit entry written with timestamp, document ID, and extracted field count

---

### Automation 3 — Billing Workflow Triage (Megastar / SVV)

**What it does:**
The agent monitors a queue of billing claim exceptions, classifies each claim by rejection reason, routes to the appropriate staff member or auto-resolves common errors, and logs all actions with a full audit trail.

**Tech used:**
- Azure Service Bus (claims exception queue)
- Azure AI Foundry (agent classification + routing)
- NLM ICD-10 API (real-time code validation)
- FastAPI stub (mocked Megastar/SVV billing API)
- Azure Table Storage (audit log)
- Python `Faker` + CMS data (synthetic claims CSV)

**Key agent steps:**
1. Azure Service Bus message received (billing exception)
2. Agent calls `classify_claim()` tool → categorizes rejection reason
3. Agent calls `validate_icd10_code()` tool → hits NLM public API
4. Decision branch:
   - Auto-resolvable → agent calls `resubmit_claim()` tool
   - Needs human review → agent calls `route_to_staff()` tool with priority flag
5. Audit entry written with claim ID, classification, resolution, and timestamp

**ICD-10 validation endpoint (free, public):**
```
https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search?sf=code,name&terms={code}
```

---

### Automation 4 — Report Analysis & Admin Email with Charts

**What it does:**
On a weekly schedule, the agent ingests financial and clinical data CSVs from Azure Blob Storage, runs AI-powered trend analysis using GPT-4o, detects anomalies, generates charts comparing week-over-week metrics, assembles an HTML/PDF report, and emails it with chart attachments to the admin distribution list.

**Tech used:**
- Azure Functions (scheduled weekly trigger)
- Azure Blob Storage (CSV data source)
- Azure OpenAI / GPT-4o (trend analysis + insight generation)
- pandas (data normalization, anomaly detection preprocessing)
- matplotlib / Plotly (chart generation)
- WeasyPrint (HTML → PDF conversion)
- Azure Communication Services (email delivery with attachments)
- Azure Table Storage (report run audit log)

**Three report modes (separate email templates):**

| Mode | Audience | Key Metrics |
|------|----------|-------------|
| Financial | CFO / Finance admin | Revenue by dept, A/R aging, collections, payer mix |
| Clinical | Operations director | Visit volumes, no-show rate, wait times, staff ratios |
| Billing | Billing manager | Claims submitted, rejection rate, auto-resolve %, processing time |

**Charts generated per report:**
- Bar chart: this week vs. prior week by department
- Line chart: 4-week rolling trend with target benchmark line
- (Optional) Pie chart: claim rejection reason breakdown

**Key agent steps:**
1. Azure Functions timer trigger fires (weekly, e.g. Monday 6am)
2. Agent calls `fetch_data_csv()` tool → reads latest CSV from Blob Storage
3. pandas cleans and structures data into key metric dictionary
4. Agent calls `analyze_trends()` tool → passes structured metrics to GPT-4o with analysis prompt
5. GPT-4o returns top 3–5 prioritized insights with severity flags (warning / ok / info)
6. Agent calls `generate_charts()` tool → matplotlib renders PNGs for each chart
7. Agent calls `build_report()` tool → assembles HTML email + PDF with charts embedded
8. Agent calls `send_report_email()` tool → Azure Communication Services delivers to admin list
9. Audit entry written: timestamp, data source, insights count, recipients, report ID

**Sample AI-generated insight output (what gets emailed):**

```
── Weekly Admin Insight Report · Week of May 19, 2026 ──

⚠  Claim rejection rate up 14% vs. prior week (12.3% → 14.1%).
   Primary driver: missing prior-auth codes on orthopedic claims.
   Recommend auditing ortho billing queue.

✓  A/R collections improved — outstanding balance reduced by $42,800 (8.2%)
   since last month. Top performer: cardiology dept.

⚠  No-show rate elevated in pediatrics (18.4%, benchmark 10%).
   Suggest automated reminder cadence review for that department.

◈  Staff-to-patient ratio tightening on Thursdays — 1:7.2 vs. target 1:5.
   Consider shift redistribution or temp staffing for high-volume days.
```

---

## Data Sources

### Synthetic / Mock Data (no credentials needed)

| Source | What it provides | How to use |
|--------|-----------------|------------|
| Python `Faker` | Realistic employee records, claim IDs, patient names, addresses | `pip install faker` — generate JSON/CSV in ~20 lines |
| FastAPI stub | Mocked Viventium HR API + Megastar billing API | Build a local REST API serving your Faker-generated data |
| Claude-generated CSV | 12-week financial and operational dataset | Ask Claude to generate and drop directly into project |

### Free Public Healthcare Data

| Source | What it provides | URL |
|--------|-----------------|-----|
| Synthea (MITRE) | Synthetic but medically realistic patient records in FHIR/JSON | https://synthea.mitre.org |
| CMS.gov forms | Real government patient intake and prior-auth PDF forms | https://www.cms.gov/medicare/cms-forms/cms-forms/downloads |
| data.cms.gov | De-identified Medicare claims data with procedure codes and rejection reasons | https://data.cms.gov |
| NLM ICD-10 API | Free public API for ICD-10 diagnosis and procedure code lookup | https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search |
| HCUP / AHRQ | Real de-identified hospital financial and utilization data | https://hcupnet.ahrq.gov |

### Azure Services (all have free tiers)

| Service | Free tier | Used for |
|---------|-----------|----------|
| Azure AI Foundry | $200 credit on new accounts | Agent SDK, tool calling, GPT-4o |
| Azure OpenAI | Included in AI Foundry credit | GPT-4o analysis and insight generation |
| Azure Document Intelligence | 500 pages/month free | PDF OCR and field extraction |
| Azure Blob Storage | 5GB free | CSV, PDF, and report file storage |
| Azure Communication Services | Free tier email sending | Weekly report email delivery |
| Azure Functions | 1M executions/month free | Scheduled weekly report trigger |
| Azure Table Storage | 1GB free | Audit logging for all automations |
| Azure Service Bus | Free tier available | Billing claims exception queue |

---

## Recommended Build Order

### Step 1 — Azure account setup (Day 1)
- Create a free Azure account at portal.azure.com
- Claim your $200 free credit
- Spin up: Azure AI Foundry, Blob Storage, Table Storage, Communication Services
- Note all connection strings and API keys into a `.env` file

### Step 2 — Generate mock data (Day 1–2)
- Install `Faker`: `pip install faker fastapi uvicorn pandas openpyxl`
- Generate employee HR JSON (50–100 records)
- Generate billing claims CSV (200–500 records with ICD-10 codes, amounts, statuses)
- Run Synthea locally to generate 100 synthetic patient records
- Download 2–3 CMS intake PDF forms for Document Intelligence testing

### Step 3 — Build mock APIs (Day 2)
- Build FastAPI stub for Viventium HR API (serves employee JSON)
- Build FastAPI stub for Megastar billing API (serves claims queue)
- Test both with curl or Postman before connecting to agent

### Step 4 — Build the Azure AI Foundry agent (Day 3–4)
- Define tool schemas for each action (get_employee, fill_form, store_doc, etc.)
- Start with Automation 1 (onboarding) — simplest flow, no OCR needed
- Add Automation 3 (billing triage) — introduces classification logic
- Add Automation 2 (patient intake) — introduces Document Intelligence

### Step 5 — Build the report pipeline (Day 5)
- Write pandas data ingestion and cleaning logic
- Build GPT-4o analysis prompt with structured metric input
- Generate matplotlib charts (bar + line per report mode)
- Assemble HTML email template with inline charts
- Wire up Azure Communication Services email delivery

### Step 6 — Polish and document (Day 6)
- Add audit logging to Azure Table Storage across all 4 automations
- Write a clear README.md with architecture diagram
- Record a 2-minute Loom walkthrough of the agent running end-to-end
- Push to GitHub with a well-organized folder structure

---

## Suggested Project Folder Structure

```
carerite-admin-agent/
├── README.md
├── .env.example
├── requirements.txt
│
├── mock_apis/
│   ├── viventium_api.py        # FastAPI stub — HR employee data
│   ├── megastar_api.py         # FastAPI stub — billing claims queue
│   └── data/
│       ├── employees.json      # Faker-generated employee records
│       └── claims.csv          # Faker-generated billing claims
│
├── data/
│   ├── synthea_patients/       # Synthea FHIR output
│   ├── cms_forms/              # Downloaded CMS intake PDFs
│   └── weekly_metrics.csv      # Financial/operational report data
│
├── agent/
│   ├── tools/
│   │   ├── onboarding_tools.py
│   │   ├── intake_tools.py
│   │   ├── billing_tools.py
│   │   └── reporting_tools.py
│   ├── prompts/
│   │   ├── onboarding_prompt.txt
│   │   ├── billing_classifier_prompt.txt
│   │   └── report_analysis_prompt.txt
│   └── agent_runner.py         # Main Azure AI Foundry agent entry point
│
├── reporting/
│   ├── chart_generator.py      # matplotlib chart rendering
│   ├── report_builder.py       # HTML + PDF assembly
│   ├── email_sender.py         # Azure Communication Services delivery
│   └── templates/
│       ├── financial_report.html
│       ├── clinical_report.html
│       └── billing_report.html
│
└── audit/
    └── audit_logger.py         # Azure Table Storage logging
```

---

## Key Python Packages

```
# requirements.txt
azure-ai-foundry
azure-ai-documentintelligence
azure-communication-email
azure-storage-blob
azure-data-tables
azure-servicebus
openai
fastapi
uvicorn
pandas
matplotlib
plotly
weasyprint
faker
python-dotenv
requests
```

---

## HIPAA / Compliance Notes (mention in interview)

- All data in this project is **100% synthetic** — no real patient data is used
- All Azure services are kept within the Azure boundary — no data leaves to third-party AI APIs
- Every agent action writes an audit log entry (timestamp, action, input hash, output summary)
- Document Intelligence processes PDFs within Azure's compliance boundary
- In a real deployment, Azure's HIPAA Business Associate Agreement (BAA) would be signed

---

## What to Say in the Interview

- *"I built this specifically because I knew your stack included Azure AI Foundry and tools like Viventium and Docstar, so I wanted to demonstrate I already understand the environment."*
- *"The agent doesn't just chat — it orchestrates multi-step tool calls, handles errors, and creates an audit trail, which matters a lot in healthcare for compliance."*
- *"The reporting module emails week-over-week charts automatically so admins have insights in their inbox Monday morning without anyone having to pull a report manually."*
- *"All data is synthetic and processed within Azure, which is how you'd architect it for HIPAA compliance in production."*

---

*Generated with Claude — project tailored for a healthcare AI Agents administration role at Carerite.*
