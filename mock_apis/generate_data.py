"""
Generate synthetic employee and billing claims data for Healthcare AI mock APIs.
Outputs:
  mock_apis/data/employees.json  — 75 healthcare staff records
  mock_apis/data/claims.csv      — 300 billing claim records
"""

import json
import csv
import random
import os
from datetime import datetime, timedelta
from faker import Faker

fake = Faker()
random.seed(42)
Faker.seed(42)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "data")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# ── Reference data ────────────────────────────────────────────────────────────

DEPARTMENTS = [
    "Cardiology", "Orthopedics", "Pediatrics", "Neurology", "Oncology",
    "Emergency Medicine", "Radiology", "Physical Therapy", "Mental Health",
    "Primary Care",
]

POSITIONS_BY_DEPT = {
    "Cardiology":         ["Cardiologist", "Cardiac Nurse", "Cardiac Tech", "Medical Assistant"],
    "Orthopedics":        ["Orthopedic Surgeon", "Orthopedic Nurse", "Physical Therapist", "Medical Assistant"],
    "Pediatrics":         ["Pediatrician", "Pediatric Nurse", "Medical Assistant", "Child Life Specialist"],
    "Neurology":          ["Neurologist", "Neurology Nurse", "EEG Technician", "Medical Assistant"],
    "Oncology":           ["Oncologist", "Oncology Nurse", "Radiation Therapist", "Medical Assistant"],
    "Emergency Medicine": ["Emergency Physician", "ER Nurse", "Paramedic", "Medical Assistant"],
    "Radiology":          ["Radiologist", "Radiology Technician", "Sonographer", "Medical Assistant"],
    "Physical Therapy":   ["Physical Therapist", "PT Assistant", "Occupational Therapist"],
    "Mental Health":      ["Psychiatrist", "Licensed Therapist", "Social Worker", "Mental Health Counselor"],
    "Primary Care":       ["Family Physician", "Internist", "Nurse Practitioner", "Medical Assistant"],
}

EMPLOYMENT_TYPES = ["Full-Time", "Part-Time", "PRN", "Contract"]
EMPLOYMENT_TYPE_WEIGHTS = [0.65, 0.20, 0.10, 0.05]

PAY_RANGES = {
    "Cardiologist": (280000, 420000), "Cardiac Nurse": (70000, 95000),
    "Cardiac Tech": (52000, 70000), "Orthopedic Surgeon": (400000, 600000),
    "Orthopedic Nurse": (68000, 92000), "Physical Therapist": (72000, 95000),
    "Pediatrician": (190000, 280000), "Pediatric Nurse": (65000, 88000),
    "Child Life Specialist": (45000, 62000), "Neurologist": (280000, 400000),
    "Neurology Nurse": (68000, 92000), "EEG Technician": (50000, 68000),
    "Oncologist": (350000, 500000), "Oncology Nurse": (70000, 96000),
    "Radiation Therapist": (80000, 110000), "Emergency Physician": (280000, 400000),
    "ER Nurse": (72000, 98000), "Paramedic": (48000, 68000),
    "Radiologist": (380000, 520000), "Radiology Technician": (55000, 78000),
    "Sonographer": (68000, 92000), "PT Assistant": (48000, 65000),
    "Occupational Therapist": (70000, 95000), "Psychiatrist": (250000, 380000),
    "Licensed Therapist": (60000, 85000), "Social Worker": (48000, 68000),
    "Mental Health Counselor": (45000, 65000), "Family Physician": (220000, 320000),
    "Internist": (210000, 310000), "Nurse Practitioner": (105000, 145000),
    "Medical Assistant": (35000, 50000),
}

ONBOARDING_DOCS = [
    "W-4 Federal Withholding", "AZ State Tax Form", "I-9 Employment Eligibility",
    "Direct Deposit Authorization", "Benefits Enrollment Form",
    "HIPAA Confidentiality Agreement", "Employee Handbook Acknowledgment",
    "Background Check Consent", "Drug Screen Authorization",
    "Emergency Contact Form", "IT Access Request",
]

# ── ICD-10 codes (code, short description) ────────────────────────────────────

ICD10_CODES = [
    ("Z00.00", "General adult medical exam, no abnormal findings"),
    ("J06.9",  "Acute upper respiratory infection, unspecified"),
    ("M54.5",  "Low back pain"),
    ("E11.9",  "Type 2 diabetes mellitus without complications"),
    ("I10",    "Essential (primary) hypertension"),
    ("J18.9",  "Pneumonia, unspecified organism"),
    ("K21.0",  "GERD with esophagitis"),
    ("F32.9",  "Major depressive disorder, single episode, unspecified"),
    ("G43.909","Migraine, unspecified, not intractable, without status migrainosus"),
    ("M17.11", "Primary osteoarthritis, right knee"),
    ("M17.12", "Primary osteoarthritis, left knee"),
    ("S72.001A","Fracture of unspecified part of neck of right femur, init"),
    ("Z23",    "Encounter for immunization"),
    ("J45.909","Unspecified asthma, uncomplicated"),
    ("N18.3",  "Chronic kidney disease, stage 3"),
    ("I25.10", "Atherosclerotic heart disease of native coronary artery"),
    ("C18.9",  "Malignant neoplasm of colon, unspecified"),
    ("F41.1",  "Generalized anxiety disorder"),
    ("E78.5",  "Hyperlipidemia, unspecified"),
    ("M79.3",  "Panniculitis, unspecified"),
]

PROCEDURE_CODES = [
    ("99213", "Office visit, established patient, level 3"),
    ("99214", "Office visit, established patient, level 4"),
    ("99203", "Office visit, new patient, level 3"),
    ("99232", "Subsequent hospital care, level 2"),
    ("93000", "Electrocardiogram, routine with interpretation"),
    ("27447", "Total knee arthroplasty"),
    ("71046", "Chest X-ray, 2 views"),
    ("80053", "Comprehensive metabolic panel"),
    ("85025", "Complete blood count with differential"),
    ("90837", "Psychotherapy, 60 min"),
    ("97110", "Therapeutic exercises"),
    ("43239", "Upper GI endoscopy with biopsy"),
    ("45378", "Colonoscopy, diagnostic"),
    ("76700", "Abdominal ultrasound, complete"),
    ("99291", "Critical care, first 30-74 min"),
]

PAYERS = [
    ("Blue Cross Blue Shield", "BCBS-AZ-001"),
    ("UnitedHealthcare",       "UHC-SW-002"),
    ("Aetna",                  "AET-NAT-003"),
    ("Cigna",                  "CIG-NAT-004"),
    ("Humana",                 "HUM-NAT-005"),
    ("Medicare",               "CMS-MCR-001"),
    ("Medicaid AHCCCS",        "AZ-MCD-001"),
    ("Tricare",                "TRI-DOD-001"),
]

REJECTION_REASONS = [
    "Missing prior authorization",
    "Invalid ICD-10 code — not covered under plan",
    "Duplicate claim submission",
    "Patient not eligible on date of service",
    "Procedure not covered under plan",
    "Timely filing limit exceeded",
    "Missing required documentation",
    "Incorrect billing modifier",
    "Provider not in network",
    "Maximum annual benefit reached",
    "Coordination of benefits — primary payer not billed first",
    "Claim submitted with incomplete patient demographics",
]

STATUSES   = ["approved", "approved", "approved", "pending", "pending",
              "exception", "exception", "denied", "resubmitted", "closed"]
PRIORITIES = ["low", "low", "medium", "medium", "high", "critical"]


# ── Helpers ───────────────────────────────────────────────────────────────────

def rand_date(start_days_ago: int, end_days_ago: int = 0) -> str:
    delta = random.randint(end_days_ago, start_days_ago)
    return (datetime.now() - timedelta(days=delta)).strftime("%Y-%m-%d")

def annual_to_hourly(annual: float) -> float:
    return round(annual / 2080, 2)

def employee_email(first: str, last: str) -> str:
    return f"{first.lower()}.{last.lower()}@healthcare-ai.com"


# ── Generate employees ────────────────────────────────────────────────────────

def generate_employees(n: int = 75) -> list[dict]:
    managers: list[dict] = []
    employees: list[dict] = []

    # First pass — create one manager per department
    for i, dept in enumerate(DEPARTMENTS):
        mgr_first = fake.first_name()
        mgr_last  = fake.last_name()
        mgr_id    = f"EMP-{(i + 1):04d}"
        managers.append({
            "employee_id":    mgr_id,
            "department":     dept,
            "first_name":     mgr_first,
            "last_name":      mgr_last,
            "email":          employee_email(mgr_first, mgr_last),
            "position":       f"{dept} Department Manager",
        })

    # Second pass — fill remaining slots
    remaining = n - len(managers)
    for i in range(remaining):
        dept   = random.choice(DEPARTMENTS)
        pos    = random.choice(POSITIONS_BY_DEPT[dept])
        first  = fake.first_name()
        last   = fake.last_name()
        emp_id = f"EMP-{(len(managers) + i + 1):04d}"
        mgr    = next(m for m in managers if m["department"] == dept)

        lo, hi = PAY_RANGES.get(pos, (40000, 70000))
        annual = random.randint(lo, hi)
        emp_type = random.choices(EMPLOYMENT_TYPES, EMPLOYMENT_TYPE_WEIGHTS)[0]

        hire_date  = rand_date(1825, 30)    # 5 years ago → 30 days ago
        start_date = (datetime.strptime(hire_date, "%Y-%m-%d")
                      + timedelta(days=random.randint(7, 21))).strftime("%Y-%m-%d")

        onboard_status = random.choices(
            ["Completed", "In Progress", "Pending"],
            weights=[0.70, 0.20, 0.10],
        )[0]

        # Which docs are still outstanding
        if onboard_status == "Completed":
            docs_pending = []
        elif onboard_status == "In Progress":
            docs_pending = random.sample(ONBOARDING_DOCS, k=random.randint(1, 4))
        else:
            docs_pending = list(ONBOARDING_DOCS)

        employees.append({
            "employee_id":        emp_id,
            "first_name":         first,
            "last_name":          last,
            "email":              employee_email(first, last),
            "personal_email":     fake.email(),
            "phone":              fake.phone_number(),
            "department":         dept,
            "position":           pos,
            "employment_type":    emp_type,
            "hire_date":          hire_date,
            "start_date":         start_date,
            "annual_salary":      annual,
            "hourly_rate":        annual_to_hourly(annual),
            "pay_frequency":      "Bi-Weekly",
            "address": {
                "street": fake.street_address(),
                "city":   fake.city(),
                "state":  "AZ",
                "zip":    fake.zipcode(),
            },
            "date_of_birth":      rand_date(23725, 12775),   # age 35–65
            "ssn_last4":          f"{random.randint(1000, 9999)}",
            "emergency_contact": {
                "name":         fake.name(),
                "relationship": random.choice(["Spouse", "Parent", "Sibling", "Partner", "Friend"]),
                "phone":        fake.phone_number(),
            },
            "manager_id":         mgr["employee_id"],
            "manager_name":       f"{mgr['first_name']} {mgr['last_name']}",
            "manager_email":      mgr["email"],
            "status":             random.choices(["Active", "Inactive"], weights=[0.93, 0.07])[0],
            "onboarding_status":  onboard_status,
            "documents_pending":  docs_pending,
            "notes":              "",
        })

    # Add the manager records themselves with full employee fields
    full_managers = []
    for i, mgr in enumerate(managers):
        dept = mgr["department"]
        lo, hi = 95000, 155000
        annual = random.randint(lo, hi)
        hire_date  = rand_date(2555, 365)
        start_date = (datetime.strptime(hire_date, "%Y-%m-%d")
                      + timedelta(days=random.randint(7, 21))).strftime("%Y-%m-%d")
        full_managers.append({
            "employee_id":        mgr["employee_id"],
            "first_name":         mgr["first_name"],
            "last_name":          mgr["last_name"],
            "email":              mgr["email"],
            "personal_email":     fake.email(),
            "phone":              fake.phone_number(),
            "department":         dept,
            "position":           mgr["position"],
            "employment_type":    "Full-Time",
            "hire_date":          hire_date,
            "start_date":         start_date,
            "annual_salary":      annual,
            "hourly_rate":        annual_to_hourly(annual),
            "pay_frequency":      "Bi-Weekly",
            "address": {
                "street": fake.street_address(),
                "city":   fake.city(),
                "state":  "AZ",
                "zip":    fake.zipcode(),
            },
            "date_of_birth":      rand_date(23725, 12775),
            "ssn_last4":          f"{random.randint(1000, 9999)}",
            "emergency_contact": {
                "name":         fake.name(),
                "relationship": random.choice(["Spouse", "Parent", "Sibling", "Partner"]),
                "phone":        fake.phone_number(),
            },
            "manager_id":         None,
            "manager_name":       None,
            "manager_email":      None,
            "status":             "Active",
            "onboarding_status":  "Completed",
            "documents_pending":  [],
            "notes":              "Department lead",
        })

    return full_managers + employees


# ── Generate claims ───────────────────────────────────────────────────────────

def generate_claims(n: int = 300) -> list[dict]:
    claims = []
    for i in range(n):
        claim_id   = f"CLM-{fake.bothify('########').upper()}"
        icd_code, icd_desc   = random.choice(ICD10_CODES)
        proc_code, proc_desc = random.choice(PROCEDURE_CODES)
        payer_name, payer_id = random.choice(PAYERS)
        dept       = random.choice(DEPARTMENTS)
        status     = random.choice(STATUSES)

        billed    = round(random.uniform(150, 12000), 2)
        allowed   = round(billed * random.uniform(0.40, 0.85), 2) if status == "approved" else 0.0
        dos       = rand_date(180, 1)
        sub_date  = (datetime.strptime(dos, "%Y-%m-%d")
                     + timedelta(days=random.randint(1, 14))).strftime("%Y-%m-%d")
        days_in_q = (datetime.now() - datetime.strptime(sub_date, "%Y-%m-%d")).days

        rejection = ""
        priority  = ""
        if status in ("exception", "denied"):
            rejection = random.choice(REJECTION_REASONS)
            priority  = random.choices(PRIORITIES, weights=[5, 10, 30, 30, 20, 5])[0]

        provider_first = fake.first_name()
        provider_last  = fake.last_name()

        claims.append({
            "claim_id":            claim_id,
            "patient_name":        fake.name(),
            "patient_id":          f"PAT-{fake.bothify('######').upper()}",
            "date_of_service":     dos,
            "provider_name":       f"Dr. {provider_first} {provider_last}",
            "provider_npi":        fake.numerify("##########"),
            "department":          dept,
            "icd10_code":          icd_code,
            "icd10_description":   icd_desc,
            "procedure_code":      proc_code,
            "procedure_description": proc_desc,
            "billed_amount":       billed,
            "allowed_amount":      allowed,
            "payer":               payer_name,
            "payer_id":            payer_id,
            "claim_status":        status,
            "rejection_reason":    rejection,
            "priority":            priority,
            "submission_date":     sub_date,
            "days_in_queue":       days_in_q,
            "assigned_to":         "",
            "resolution_notes":    "",
        })
    return claims


# ── Write output ──────────────────────────────────────────────────────────────

def main():
    print("Generating employee records...")
    employees = generate_employees(75)
    emp_path = os.path.join(OUTPUT_DIR, "employees.json")
    with open(emp_path, "w", encoding="utf-8") as f:
        json.dump(employees, f, indent=2)
    print(f"  OK: {len(employees)} employees -> {emp_path}")

    print("Generating billing claims...")
    claims = generate_claims(300)
    clm_path = os.path.join(OUTPUT_DIR, "claims.csv")
    fieldnames = list(claims[0].keys())
    with open(clm_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(claims)
    status_counts = {}
    for c in claims:
        status_counts[c["claim_status"]] = status_counts.get(c["claim_status"], 0) + 1
    print(f"  OK: {len(claims)} claims -> {clm_path}")
    print(f"  Status breakdown: {status_counts}")
    print("Done.")


def generate_weekly_metrics(weeks: int = 12) -> list[dict]:
    """
    Generate a 12-week x 10-department operational metrics dataset
    for the Automation 4 reporting pipeline.
    """
    from datetime import timedelta
    base_date = datetime.now() - timedelta(weeks=weeks)
    # Round to Monday
    base_date -= timedelta(days=base_date.weekday())

    rows = []
    for w in range(weeks):
        week_start = (base_date + timedelta(weeks=w)).strftime("%Y-%m-%d")
        for dept in DEPARTMENTS:
            # Add week-over-week variance with a slight upward trend
            trend = 1 + (w * 0.005)
            noise = random.uniform(0.88, 1.12)

            visits       = int(random.randint(80, 220) * trend * noise)
            no_shows     = int(visits * random.uniform(0.07, 0.22))
            no_show_rate = round(no_shows / visits * 100, 2) if visits else 0
            new_patients = int(visits * random.uniform(0.15, 0.35))
            staff_count  = random.randint(8, 25)
            wait_min     = round(random.uniform(10, 32), 1)

            claims_sub    = int(visits * random.uniform(0.80, 1.10))
            denial_rate   = round(random.uniform(0.06, 0.18), 4)
            claims_denied = int(claims_sub * denial_rate)
            claims_approv = claims_sub - claims_denied
            rejection_pct = round(denial_rate * 100, 2)
            auto_resolve  = round(random.uniform(0.55, 0.78) * 100, 2)
            avg_ar_days   = round(random.uniform(18, 45), 1)

            billed_per_visit = random.uniform(280, 950)
            total_revenue    = round(visits * billed_per_visit * trend * noise, 2)
            collection_rate  = random.uniform(0.72, 0.91)
            collections      = round(total_revenue * collection_rate, 2)

            rows.append({
                "week_start_date":    week_start,
                "department":         dept,
                "total_visits":       visits,
                "no_show_count":      no_shows,
                "no_show_rate":       no_show_rate,
                "new_patients":       new_patients,
                "staff_count":        staff_count,
                "avg_wait_minutes":   wait_min,
                "claims_submitted":   claims_sub,
                "claims_approved":    claims_approv,
                "claims_denied":      claims_denied,
                "rejection_rate":     rejection_pct,
                "auto_resolve_pct":   auto_resolve,
                "avg_ar_days":        avg_ar_days,
                "total_revenue":      total_revenue,
                "collections_amount": collections,
            })
    return rows


if __name__ == "__main__":
    main()
    print("Generating weekly metrics...")
    metrics = generate_weekly_metrics(12)
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    os.makedirs(data_dir, exist_ok=True)
    metrics_path = os.path.join(data_dir, "weekly_metrics.csv")
    fieldnames = list(metrics[0].keys())
    with open(metrics_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(metrics)
    print(f"  OK: {len(metrics)} rows ({12} weeks x {len(DEPARTMENTS)} depts) -> {metrics_path}")

