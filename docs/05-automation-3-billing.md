# Automation 3 — Billing Workflow Triage

This document explains every file, function, and design decision in Automation 3. You will understand how rejected insurance claims are fetched, validated, classified, resolved or escalated, and how the final report is generated — including why the report always captures every claim even when the agent processes them out of strict order.

---

## What Problem Does This Solve?

When an insurance company rejects a billing claim, a billing specialist must:

1. Find the rejected claim in the billing system
2. Read the rejection reason
3. Check whether the ICD-10 diagnosis code is valid
4. Decide: can this be fixed and resubmitted automatically? Or does it need specialist review?
5. If auto-resolvable: correct the error and resubmit the claim
6. If needs review: assign it to the right billing staff member with a priority flag, and email them
7. Track the outcome for reporting
8. Write a summary report showing dollars recovered vs. still at risk
9. Email the billing manager

An experienced billing specialist handles perhaps 3–4 claims per hour. The agent handles 5 in under 2 minutes.

---

## Files Involved

| File | Role |
|---|---|
| `agent/tools/billing_tools.py` | All 10 tool functions the agent calls |
| `agent/prompts/billing_classifier_prompt.txt` | Step-by-step workflow instructions for the agent |
| `agent/agent_runner.py` | Core agent loop |
| `mock_apis/claimbridge_api.py` | Fake billing system REST API (port 8002) |
| `mock_apis/data/claims.csv` | 300 synthetic billing claim records |

---

## The Prompt — `agent/prompts/billing_classifier_prompt.txt`

The billing prompt is the most detailed and prescriptive of the four. It specifies a four-step workflow with substeps for each claim.

**Why so specific?**

The billing workflow has strict ordering requirements. For each claim, you must:
- Get details before you can validate the ICD-10 code (you need the code first)
- Validate the ICD-10 code before classifying (the classification uses the validity result)
- Classify before resubmitting or routing (classification determines which path to take)
- Record the outcome after (not before) resubmission or routing

If the model is not given explicit ordering instructions, it may try to classify before validating, or generate the report before all outcomes are recorded. The detailed prompt prevents this.

---

## Module-Level State — The Key to Reliable Reporting

Billing tools use three module-level (global) state variables:

```python
_claim_outcomes: list = []   # accumulates every processed claim's final outcome
_fetched_queue: dict = {}    # saves queue data at fetch time (claim_id → queue entry)
_classify_cache: dict = {}   # saves classify results (claim_id → {resolution_action, icd10_valid})
```

**Why are these needed?**

The agent processes each claim through many tool calls. By the time `generate_billing_report` is called, it needs to know the outcome for every claim. The `_claim_outcomes` list accumulates these as `record_claim_outcome` is called. But — and this is important — AI models are not perfectly sequential. The model might call `generate_billing_report` just before calling `record_claim_outcome` for the last claim. If the report only reads `_claim_outcomes`, it would miss the last claim.

The `_fetched_queue` and `_classify_cache` are safety nets that capture data at earlier points in time, so the report can reconstruct any missing claim by querying the ClaimBridge API directly. See the section on `generate_billing_report` for the full explanation.

---

## Tool 1 — `get_billing_exception_queue`

### What It Does

Fetches the active billing exception queue from the ClaimBridge API — the list of claims currently in `exception` or `denied` status that need attention.

```python
global _claim_outcomes, _fetched_queue, _classify_cache
_claim_outcomes = []     # reset at the start of every new run
_fetched_queue = {}
_classify_cache = {}

resp = requests.get(f"{CLAIMBRIDGE_URL}/claims/exceptions", params={"limit": min(limit, 100)})
data = resp.json()
for exc in data.get("exceptions", []):
    _fetched_queue[exc["claim_id"]] = exc  # save queue data before the agent processes anything
```

The three state variables are reset here at the start of each run. This is important: if you run billing twice in a row without restarting, you don't want the previous run's outcomes mixed into the new run's report.

The `_fetched_queue` save happens here — before the agent calls `resubmit_claim` or `route_to_staff`. Those API calls can modify the claim record, potentially clearing fields like `rejection_reason` and `priority`. By saving the original queue data now, the report can reconstruct those fields later even if the claim has been modified.

### What ClaimBridge Returns

```json
{
  "success": true,
  "queue_depth": 5,
  "exceptions": [
    {
      "claim_id": "CLM-12345678",
      "patient_name": "Michael Bradley",
      "rejection_reason": "Missing prior authorization",
      "priority": "medium",
      "billed_amount": 2847.50,
      "days_in_queue": 12
    },
    ...
  ]
}
```

---

## Tool 2 — `get_claim_details`

### What It Does

Fetches the full record for a specific claim, including the ICD-10 code, procedure code, payer, provider, and complete financial details.

```python
resp = requests.get(f"{CLAIMBRIDGE_URL}/claims/{claim_id}", timeout=5)
return json.dumps({"success": True, "claim": resp.json()})
```

The agent needs this detailed record because the queue endpoint only returns summary data. The ICD-10 code is in the full record, not the queue summary. The agent extracts `icd10_code` from this response to pass to `validate_icd10_code`.

### What It Returns (key fields)

```json
{
  "claim_id": "CLM-12345678",
  "patient_name": "Michael Bradley",
  "icd10_code": "M54.5",
  "icd10_description": "Low back pain",
  "procedure_code": "97110",
  "billed_amount": 2847.50,
  "allowed_amount": 0.0,
  "payer": "Blue Cross Blue Shield",
  "rejection_reason": "Missing prior authorization",
  "priority": "medium",
  "claim_status": "exception"
}
```

---

## Tool 3 — `validate_icd10_code`

### What It Does

Validates a single ICD-10-CM diagnosis code against the NLM (National Library of Medicine) public API.

```python
resp = requests.get(
    "https://clinicaltables.nlm.nih.gov/api/icd10cm/v3/search",
    params={"sf": "code,name", "terms": code, "maxList": 5},
    timeout=8,
)
data  = resp.json()
total = data[0]      # number of matches found
matches = data[3]    # list of [code, description] pairs

exact = next(
    (m for m in matches if m[0].upper() == code.strip().upper()), None
)
return json.dumps({
    "is_valid":    exact is not None,
    "description": exact[1] if exact else None,
    "suggestions": [{"code": m[0], "description": m[1]} for m in matches[:5]],
})
```

The function checks whether any of the returned matches is an exact match for the submitted code. If yes: the code is valid. If no: the code is invalid, and the suggestions contain related valid codes the agent can use as corrections.

### Example: Invalid Code

If a claim has ICD-10 code `M54.99` (not a real code), the NLM API might return:
```json
{
  "is_valid": false,
  "suggestions": [
    {"code": "M54.5",  "description": "Low back pain"},
    {"code": "M54.89", "description": "Other dorsalgia"}
  ]
}
```

The agent can then resubmit the claim using `M54.5` as the corrected code.

### Timeout Handling

The NLM API is a free public service. It can be slow under load. The 8-second timeout is generous to accommodate this. If it times out, the function returns `{"success": False, "error": "NLM API timed out."}`. The agent will treat this as unable to validate, and the claim will be classified conservatively (not auto-resolvable).

---

## Tool 4 — `classify_claim`

### What It Does

Takes the rejection reason and ICD-10 validation result and classifies the claim into one of two paths:
- `"auto_resolvable"` — the agent can fix and resubmit without human involvement
- `"needs_human_review"` — a billing specialist must review this

```python
def classify_claim(claim_id: str, rejection_reason: str, icd10_valid: bool) -> str:
    reason_lower = rejection_reason.lower()
    auto_resolvable = any(pat in reason_lower for pat in AUTO_RESOLVABLE_PATTERNS)

    if not icd10_valid and "invalid icd-10" not in reason_lower:
        # ICD-10 is invalid but the rejection was for a different reason
        # Still needs human to figure out the right code in context
        auto_resolvable = False
        resolution_action = "validate_and_correct_icd10_code"
        priority = "high"
    elif "missing prior authorization" in reason_lower:
        resolution_action = "obtain_and_resubmit_with_prior_auth"
        priority = "medium"
    elif "invalid icd-10" in reason_lower:
        resolution_action = "correct_icd10_code_and_resubmit"
        priority = "medium"
    elif "duplicate claim" in reason_lower:
        resolution_action = "verify_and_close_duplicate"
        priority = "low"
    elif "not eligible" in reason_lower:
        auto_resolvable = False
        resolution_action = "verify_eligibility_with_payer"
        priority = "high"
    elif "timely filing" in reason_lower:
        auto_resolvable = False
        resolution_action = "submit_timely_filing_appeal"
        priority = "critical"
    elif "not in network" in reason_lower:
        auto_resolvable = False
        resolution_action = "review_network_status_with_payer_relations"
        priority = "high"
    ...

    # Save to classify cache for report reconstruction
    global _classify_cache
    _classify_cache[claim_id] = {"resolution_action": resolution_action, "icd10_valid": icd10_valid}
```

### Auto-Resolvable Categories

```python
AUTO_RESOLVABLE_PATTERNS = [
    "missing prior authorization",
    "invalid icd-10 code",
    "incorrect billing modifier",
    "claim submitted with incomplete patient demographics",
    "duplicate claim submission",
]
```

These are administrative errors that have clear, mechanical fixes:
- Missing prior auth → obtain the authorization number and resubmit
- Invalid ICD-10 → correct the code using the NLM suggestion and resubmit
- Wrong modifier → fix the modifier code and resubmit
- Incomplete demographics → complete the patient info and resubmit
- Duplicate submission → verify it's a duplicate and close the extra

### Non-Resolvable Categories

These require human judgment:
- Patient not eligible → must verify with payer, possibly check date of service eligibility
- Provider not in network → complex payer relations issue
- Maximum benefit reached → involves patient notification and financial counseling
- Timely filing exceeded → legal deadline missed, requires formal appeal
- Unknown reason → specialist needs to assess

### The Classify Cache

```python
_classify_cache[claim_id] = {"resolution_action": resolution_action, "icd10_valid": icd10_valid}
```

This saves the classification result at classify time. The report generator reads from this cache when reconstructing any claim that wasn't recorded in `_claim_outcomes` before `generate_billing_report` was called.

---

## Tool 5 — `resubmit_claim`

### What It Does

For auto-resolvable claims, this tool calls the ClaimBridge API to resubmit the claim with corrected data.

```python
payload = {
    "resubmitted_by": "healthcare-ai-billing-agent",
    "resolution_notes": resolution_notes or "Auto-resolved by agent.",
}
if corrected_icd10_code:
    payload["corrected_icd10_code"] = corrected_icd10_code
if prior_auth_number:
    payload["prior_auth_number"] = prior_auth_number

resp = requests.post(f"{CLAIMBRIDGE_URL}/claims/{claim_id}/resubmit", json=payload)
```

The ClaimBridge mock API handles this by setting `claim_status = "resubmitted"` and recording the resolution notes. In a real system, this would submit the corrected claim to the insurance company's clearinghouse.

### Arguments the Agent Passes

- **For invalid ICD-10:** `corrected_icd10_code = "{suggestion from NLM}"` 
- **For missing prior auth:** `prior_auth_number = "OBTAINED-{claim_id}"` (mock authorization number)
- **For others:** just `resolution_notes` explaining what was corrected

---

## Tool 6 — `route_to_staff`

### What It Does

For claims that need human review, this tool assigns the claim to a specific billing staff member with a priority flag.

```python
payload = {
    "assigned_to":    assigned_to,   # e.g., "billing.supervisor@HealthcareAI.com"
    "priority":       priority,      # low | medium | high | critical
    "routing_reason": routing_reason,
    "routed_by":      "healthcare-ai-billing-agent",
}
resp = requests.post(f"{CLAIMBRIDGE_URL}/claims/{claim_id}/route", json=payload)
```

The prompt specifies the assignment rules:
- `critical` priority → `billing.supervisor@HealthcareAI.com`
- `high` priority → `billing.senior@HealthcareAI.com`
- `medium` / `low` → `billing.staff@HealthcareAI.com`

The ClaimBridge mock API records the `assigned_to` field and updates the claim status to `"routed"`.

---

## Tool 7 — `record_claim_outcome`

### What It Does

Records the final outcome of a processed claim into `_claim_outcomes` and emits a dashboard card.

```python
global _claim_outcomes
record = {
    "claim_id":          claim_id,
    "patient_name":      patient_name,
    "rejection_reason":  rejection_reason,
    "icd10_valid":       icd10_valid,
    "outcome":           outcome,       # "auto_resolved" or "escalated"
    "priority":          priority,
    "billed_amount":     float(billed_amount),
    "resolution_action": resolution_action,
}
_claim_outcomes.append(record)
print(f"CLAIM_CARD:{json.dumps(record)}", flush=True)  # dashboard parses this
```

The `CLAIM_CARD:` print statement is the live dashboard protocol. The dashboard's JavaScript looks for lines starting with `CLAIM_CARD:`, parses the JSON, and renders a color-coded card for that claim in real time as the agent processes each one.

### Why This Tool Exists (Instead of Tracking Internally)

`record_claim_outcome` serves two purposes:
1. Provides structured data to the report generator (`_claim_outcomes`)
2. Emits dashboard cards in real time (the `CLAIM_CARD:` print)

If tracking were done internally (e.g., inside `resubmit_claim` and `route_to_staff`), there would be no clean mechanism to emit the formatted dashboard card with all the fields the UI needs. Having a dedicated "record outcome" tool gives the model a clear instruction: after every claim is resolved, call this tool.

---

## Tool 8 — `notify_staff_claim_routed`

### What It Does

Sends a formatted HTML email to the billing staff member who was just assigned the claim.

The email is color-coded by priority:
- `critical` → deep red header (`#C62828`)
- `high` → dark orange header (`#E65100`)
- `medium` → amber header (`#F9A825`)
- `low` → green header (`#2E7D32`)

The `_ACTION_LABELS` dictionary translates the machine-readable `resolution_action` codes into human-readable action descriptions:

```python
_ACTION_LABELS = {
    "verify_eligibility_with_payer": "Verify patient eligibility on date of service with the payer",
    "submit_timely_filing_appeal": "Submit a timely filing appeal immediately — deadline may be at risk",
    ...
}
```

This means the email body says "Action Required: Submit a timely filing appeal immediately" instead of the raw code `submit_timely_filing_appeal`.

---

## Tool 9 — `generate_billing_report`

### What It Does

Compiles the final HTML triage report from all claims processed in this run. This is the most sophisticated tool in the project because it handles the agent out-of-order execution problem.

### The Out-of-Order Execution Problem

AI models are not perfectly sequential. In a run with 5 claims, the expected order is:

```
process claim 1 → process claim 2 → ... → process claim 5 → generate_billing_report
```

But occasionally the model calls tools slightly out of order:

```
process claim 1 → process claim 2 → ... → generate_billing_report → record_claim_outcome for claim 5
```

If `generate_billing_report` simply reads `_claim_outcomes`, it would miss claim 5, producing a report that shows 4 claims when 5 were actually processed.

### The Fix — Gap Filling

```python
claims = list(_claim_outcomes)  # start with what was recorded

# Find any claims that were fetched but not yet recorded
recorded_ids = {c["claim_id"] for c in claims}
for cid, saved in _fetched_queue.items():
    if cid in recorded_ids:
        continue   # already recorded, skip

    # Query ClaimBridge for current status of this unrecorded claim
    try:
        resp = requests.get(f"{CLAIMBRIDGE_URL}/claims/{cid}", timeout=5)
        current = resp.json()
    except Exception:
        continue

    # Determine outcome from current claim status
    if current["claim_status"] == "resubmitted":
        outcome = "auto_resolved"
    elif current.get("assigned_to"):
        outcome = "escalated"
    else:
        continue   # not yet processed — skip

    # Reconstruct the full record using saved queue data + classify cache
    cached = _classify_cache.get(cid, {})
    claims.append({
        "claim_id":          cid,
        "patient_name":      saved.get("patient_name", ""),
        "rejection_reason":  saved.get("rejection_reason", ""),
        "icd10_valid":       cached.get("icd10_valid", True),
        "outcome":           outcome,
        "priority":          saved.get("priority", ""),
        "billed_amount":     float(saved.get("billed_amount", 0)),
        "resolution_action": cached.get("resolution_action", ""),
    })
```

This works because:
- `_fetched_queue` has the original queue data (patient name, rejection reason, priority, billed amount) saved at the very start of the run, before any claim was modified
- `_classify_cache` has the classification result (resolution action, ICD-10 validity) saved after `classify_claim` was called
- ClaimBridge's current `claim_status` tells us whether the claim was resubmitted or routed

Together, these three data sources can fully reconstruct any claim's record even if `record_claim_outcome` wasn't called before the report was generated.

### Report Content

The HTML report contains:

**Summary Metrics (4 cards):**
- Total claims processed
- Auto-resolved count + percentage
- Escalated count
- Estimated time saved (18 minutes × auto-resolved count)

**Financial Summary (2 cards):**
- Dollars auto-recovered (sum of billed_amount for auto_resolved claims)
- Dollars still at risk (sum of billed_amount for escalated claims)

**Claim Detail Cards:** One card per claim showing:
- Claim ID and patient name
- Auto-Resolved (green) or Escalated (red) badge
- Rejection reason
- ICD-10 validity (green checkmark or red X)
- Priority level (color-coded)
- Billed amount
- Resolution action (human-readable from `_ACTION_LABELS`)

### Upload and SAS URL

The report is saved locally to `data/reports/billing_triage_{timestamp}.html` and uploaded to Azure Blob Storage with a 7-day SAS URL. That URL is returned and passed to `send_billing_summary_email`.

---

## Tool 10 — `send_billing_summary_email`

### What It Does

Sends a summary email to the billing manager with:
- Total processed, auto-resolved count + percentage, escalated count
- Dollars auto-recovered (green) and still at risk (red)
- Estimated time saved
- A "View Full Billing Report →" button linking to the SAS URL

This email is sent to `MANAGER_NOTIFICATION_EMAIL` from `.env`.

---

## The Complete Flow — 5 Claims

```
User clicks "Run Billing" in dashboard
        │
        ▼
agent_runner sends to o4-mini:
"Fetch the current billing exception queue and triage each claim..."
        │
Model: call get_billing_exception_queue(limit=5)
        │
        ▼
get_billing_exception_queue
        ├── resets _claim_outcomes, _fetched_queue, _classify_cache
        ├── calls GET /claims/exceptions?limit=5 on ClaimBridge
        ├── saves each exception to _fetched_queue
        └── returns: 5 claims with summary data

For each of the 5 claims (repeat 5 times):
        │
        ├── get_claim_details("{claim_id}")
        │     └── GET /claims/{id} → full record including icd10_code
        │
        ├── validate_icd10_code("{icd10_code}")
        │     └── GET NLM API → is_valid, suggestions
        │
        ├── classify_claim("{claim_id}", "{rejection_reason}", {icd10_valid})
        │     ├── saves to _classify_cache
        │     └── returns: classification, resolution_action, priority
        │
        ├── IF auto_resolvable:
        │     └── resubmit_claim("{claim_id}", corrected_icd10_code, prior_auth_number, notes)
        │           └── POST /claims/{id}/resubmit → status becomes "resubmitted"
        │
        ├── IF needs_human_review:
        │     ├── route_to_staff("{claim_id}", "{assigned_to}", "{priority}", "{reason}")
        │     │     └── POST /claims/{id}/route → assigns to staff member
        │     └── notify_staff_claim_routed(...)
        │           └── Azure Communication Services → email to billing staff
        │
        └── record_claim_outcome("{claim_id}", "{name}", "{reason}", {icd10_valid}, "{outcome}", ...)
              ├── appends to _claim_outcomes
              └── prints CLAIM_CARD:{json} → dashboard card

Model: call generate_billing_report()
        │
        ▼
generate_billing_report
        ├── reads _claim_outcomes
        ├── gap-fills any missing claims via _fetched_queue + _classify_cache + ClaimBridge API
        ├── calculates totals: auto_resolved, escalated, dollars_recovered, dollars_at_risk
        ├── builds HTML report
        ├── saves to data/reports/billing_triage_{timestamp}.html
        ├── uploads to Azure Blob Storage
        └── returns: report_url, totals

Model: call send_billing_summary_email(total_processed, auto_resolved, escalated, ...)
        │
        ▼
send_billing_summary_email → Azure Communication Services
        └── sends manager summary with report link

Model: finish_reason="stop" → writes final text summary
        │
dashboard shows: billing triage board with 5 claim cards + View Report button
```

---

## The ClaimBridge Mock API — `mock_apis/claimbridge_api.py`

The ClaimBridge API simulates a real billing management system (like Athenahealth, eClinicalWorks, or Epic's billing module).

### Data Loading

```python
_claims: list[dict] = _load_claims()      # loads all 300 claims from claims.csv
_by_id: dict[str, dict] = {c["claim_id"]: c for c in _claims}
```

The CSV data is loaded into memory at startup and served from dictionaries for O(1) lookups.

### Key Endpoints

**`GET /claims/exceptions`** — returns claims with status `exception` or `denied`, sorted by priority (critical first), limited to the requested count:

```python
exceptions = [c for c in _claims if c["claim_status"] in ("exception", "denied")]
priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "": 4}
exceptions.sort(key=lambda c: priority_order.get(c["priority"], 4))
return {"exceptions": exceptions[:limit], "queue_depth": len(exceptions)}
```

**`POST /claims/{id}/resubmit`** — updates the claim in memory:
```python
claim["claim_status"] = "resubmitted"
claim["resolution_notes"] = body.resolution_notes
if body.corrected_icd10_code:
    claim["icd10_code"] = body.corrected_icd10_code
```

**`POST /claims/{id}/route`** — marks the claim as routed:
```python
claim["claim_status"] = "routed"
claim["assigned_to"]  = body.assigned_to
claim["priority"]     = body.priority
```

These in-memory modifications are why the gap-filling logic in `generate_billing_report` can check `current["claim_status"] == "resubmitted"` to determine if a claim was auto-resolved.

---

## Key Design Decisions Explained

### Why 5 Claims Per Run (Not All 101 in the Exception Queue)?

The billing prompt specifies `limit=5`. This is intentional. The goal is to demonstrate the full triage workflow in a reasonable demo time (~60–90 seconds). Processing all 101 exceptions would take 15–20 minutes and hundreds of model API calls, hitting rate limits and making the demo impractical. In a production system, you could set the limit to 20 or 50, or run the agent continuously with a queue processor.

### Why Is the NLM ICD-10 Validation a Real API Call (Not Mock)?

This is one of the showcase features of the project. Using the real NLM API demonstrates that the agent can call external public services during its reasoning process. Every ICD-10 code in the billing claims is checked against the actual NLM database — if the code is in the database, it passes; if not, real alternative suggestions are returned. This shows the system doing genuine medical coding validation, not just pretending.

### Why Save Data in `_fetched_queue` and `_classify_cache` at Call Time?

The ClaimBridge API is stateful — when you call `resubmit_claim`, it changes the claim's status and potentially clears its rejection reason. If `generate_billing_report` tried to reconstruct a resubmitted claim by re-reading it from ClaimBridge, `rejection_reason` might be empty or overwritten. By saving it at queue fetch time (before any modifications), we preserve the original data. This is a defensive programming pattern: capture state early and preserve it, because APIs can change their data.
