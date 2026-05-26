"""
Automation 4 — Report Analysis & Admin Email tools.

Uses a module-level session cache so each tool only receives the small
report_mode string from the model. Large DataFrames, insights, chart paths,
and report paths are stored internally and passed between tools via _SESSION.
"""

import json
import os
import sys
from datetime import datetime, timezone
from io import StringIO

from dotenv import load_dotenv

load_dotenv()

REPORT_MODES = ("financial", "clinical", "billing")

# Module-level session cache — keyed by report_mode
_SESSION: dict = {}


# ── Tool 1: Fetch the latest metrics CSV ─────────────────────────────────────

def fetch_data_csv(report_mode: str) -> str:
    """
    Load the latest weekly metrics CSV for the given report mode into memory.

    Args:
        report_mode: One of 'financial', 'clinical', or 'billing'.

    Returns:
        JSON string confirming the data was loaded with row count and week range.
    """
    if report_mode not in REPORT_MODES:
        return json.dumps({"success": False, "error": f"Invalid mode '{report_mode}'."})

    import pandas as pd

    conn_str  = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
    container = os.getenv("AZURE_STORAGE_CONTAINER_DATA", "data-csv")
    blob_name = f"weekly_metrics_{report_mode}.csv"
    csv_text  = None

    if conn_str:
        try:
            from azure.storage.blob import BlobServiceClient
            svc    = BlobServiceClient.from_connection_string(conn_str)
            client = svc.get_blob_client(container=container, blob=blob_name)
            csv_text = client.download_blob().readall().decode("utf-8")
        except Exception:
            pass

    if csv_text is None:
        local_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "data", "weekly_metrics.csv"
        )
        if not os.path.exists(local_path):
            return json.dumps({"success": False, "error": f"CSV not found at {local_path}."})
        with open(local_path, encoding="utf-8") as f:
            csv_text = f.read()

    df = pd.read_csv(StringIO(csv_text))

    if "week_start_date" in df.columns:
        df["week_start_date"] = pd.to_datetime(df["week_start_date"])
        df = df.sort_values("week_start_date")
        latest_4 = df["week_start_date"].unique()[-4:]
        df = df[df["week_start_date"].isin(latest_4)]

    week_range = ""
    if "week_start_date" in df.columns:
        week_range = (
            f"{df['week_start_date'].min().date()} to {df['week_start_date'].max().date()}"
        )

    _SESSION[report_mode] = {"df": df}

    return json.dumps({
        "success":     True,
        "report_mode": report_mode,
        "week_range":  week_range,
        "row_count":   len(df),
        "columns":     list(df.columns),
        "next_step":   "Call analyze_trends with the same report_mode.",
    })


# ── Signal computation helpers ────────────────────────────────────────────────

def _compute_signals(df, report_mode: str) -> dict:
    """Pre-compute all analytical signals; AI interprets, never calculates."""
    import pandas as pd

    df = df.copy()
    df["week_start_date"] = pd.to_datetime(df["week_start_date"])
    weeks = sorted(df["week_start_date"].unique())
    this_week  = weeks[-1]
    prior_week = weeks[-2] if len(weeks) >= 2 else weeks[0]
    mid = max(1, len(weeks) // 2)
    early_df = df[df["week_start_date"].isin(weeks[:mid])]
    late_df  = df[df["week_start_date"].isin(weeks[mid:])]
    this_df  = df[df["week_start_date"] == this_week]
    prior_df = df[df["week_start_date"] == prior_week]

    def tot(d, col):
        return float(d[col].sum()) if col in d.columns else 0.0

    def wtd_rate(d, num, den):
        n, dn = tot(d, num), tot(d, den)
        return round(n / dn * 100, 1) if dn > 0 else 0.0

    def wtd_avg(d, val, wt):
        if val not in d.columns or wt not in d.columns:
            return 0.0
        num = (d[val] * d[wt]).sum()
        dn  = d[wt].sum()
        return round(float(num / dn), 1) if dn > 0 else 0.0

    def pct(new, old):
        return round((new - old) / abs(old) * 100, 1) if old != 0 else 0.0

    def pp(new, old):
        return round(new - old, 1)

    def trend(e, l, col, higher_good=True, agg="sum"):
        fn = tot if agg == "sum" else (lambda d, c: float(d[c].mean()) if c in d.columns else 0.0)
        ev, lv = fn(e, col), fn(l, col)
        if ev == 0:
            return "stable"
        delta = (lv - ev) / abs(ev) * 100
        if abs(delta) < 5:
            return "stable"
        return "improving" if (delta > 0) == higher_good else "worsening"

    s = {"week_this": this_week.strftime("%Y-%m-%d"),
         "week_prior": prior_week.strftime("%Y-%m-%d")}

    if report_mode == "financial":
        rev_t = tot(this_df, "total_revenue");  rev_p = tot(prior_df, "total_revenue")
        vis_t = tot(this_df, "total_visits");   vis_p = tot(prior_df, "total_visits")
        col_t = tot(this_df, "collections_amount"); col_p = tot(prior_df, "collections_amount")
        den_t = tot(this_df, "claims_denied");  den_p = tot(prior_df, "claims_denied")
        sub_t = tot(this_df, "claims_submitted"); sub_p = tot(prior_df, "claims_submitted")
        nos_t = tot(this_df, "no_show_count")
        rpv_t = round(rev_t / vis_t, 2) if vis_t > 0 else 0
        rpv_p = round(rev_p / vis_p, 2) if vis_p > 0 else 0
        rpc_t = round(rev_t / sub_t, 2) if sub_t > 0 else 0
        rpc_p = round(rev_p / sub_p, 2) if sub_p > 0 else 0

        dept_rev_t = this_df.groupby("department")["total_revenue"].sum()
        dept_rev_p = prior_df.groupby("department")["total_revenue"].sum()
        movers = {d: dept_rev_t.get(d, 0) - dept_rev_p.get(d, 0) for d in dept_rev_t.index}

        dept_rpv = this_df.groupby("department").apply(
            lambda g: g["total_revenue"].sum() / g["total_visits"].sum()
            if g["total_visits"].sum() > 0 else 0)
        dept_coll = this_df.groupby("department").apply(
            lambda g: g["collections_amount"].sum() / g["total_revenue"].sum() * 100
            if g["total_revenue"].sum() > 0 else 0)
        dept_ar = this_df.groupby("department")["avg_ar_days"].mean()
        ar_breach = dept_ar[dept_ar > 35].sort_values(ascending=False)
        dept_ns_loss = this_df.groupby("department")["no_show_count"].sum() * rpv_t

        col_rate_t = col_t / rev_t * 100 if rev_t > 0 else 0
        col_rate_p = col_p / rev_p * 100 if rev_p > 0 else 0
        s.update({
            "revenue_this": round(rev_t, 0), "revenue_prior": round(rev_p, 0),
            "revenue_delta_pct": pct(rev_t, rev_p),
            "revenue_trend": trend(early_df, late_df, "total_revenue"),
            "top3_revenue_depts": [(d, round(v, 0)) for d, v in dept_rev_t.nlargest(3).items()],
            "biggest_revenue_drop": (min(movers, key=movers.get), round(min(movers.values()), 0)),
            "biggest_revenue_gain": (max(movers, key=movers.get), round(max(movers.values()), 0)),
            "revenue_per_visit_this": rpv_t, "revenue_per_visit_prior": rpv_p,
            "revenue_per_visit_delta_pct": pct(rpv_t, rpv_p),
            "lowest_rpv_dept": (dept_rpv.idxmin(), round(dept_rpv.min(), 2)),
            "collections_rate_this": round(col_rate_t, 1),
            "collections_rate_prior": round(col_rate_p, 1),
            "collections_rate_delta_pp": pp(col_rate_t, col_rate_p),
            "uncollected_this": round(rev_t - col_t, 0),
            "collections_trend": trend(early_df, late_df, "collections_amount"),
            "lowest_coll_rate_dept": (dept_coll.idxmin(), round(dept_coll.min(), 1)),
            "ar_days_this": wtd_avg(this_df, "avg_ar_days", "claims_submitted"),
            "ar_days_prior": wtd_avg(prior_df, "avg_ar_days", "claims_submitted"),
            "ar_days_delta": pp(wtd_avg(this_df, "avg_ar_days", "claims_submitted"),
                                wtd_avg(prior_df, "avg_ar_days", "claims_submitted")),
            "ar_trend": trend(early_df, late_df, "avg_ar_days", higher_good=False, agg="mean"),
            "ar_days_above_threshold": [(d, round(v, 1)) for d, v in ar_breach.items()],
            "denial_revenue_risk_this": round(den_t * rpc_t, 0),
            "denial_revenue_risk_prior": round(den_p * rpc_p, 0),
            "denial_revenue_risk_delta_pct": pct(den_t * rpc_t, den_p * rpc_p),
            "no_show_revenue_loss": round(nos_t * rpv_t, 0),
            "worst_no_show_dept": (dept_ns_loss.idxmax(), round(dept_ns_loss.max(), 0)),
        })

    elif report_mode == "clinical":
        vis_t = tot(this_df, "total_visits"); vis_p = tot(prior_df, "total_visits")
        nos_t = tot(this_df, "no_show_count"); nos_p = tot(prior_df, "no_show_count")
        np_t  = tot(this_df, "new_patients");  np_p  = tot(prior_df, "new_patients")
        rev_t = tot(this_df, "total_revenue")
        rpv_t = round(rev_t / vis_t, 2) if vis_t > 0 else 0
        nsr_t = wtd_rate(this_df, "no_show_count", "total_visits")
        nsr_p = wtd_rate(prior_df, "no_show_count", "total_visits")

        dept_nsr = this_df.groupby("department").apply(
            lambda g: g["no_show_count"].sum() / g["total_visits"].sum() * 100
            if g["total_visits"].sum() > 0 else 0)
        dept_vps = this_df.groupby("department").apply(
            lambda g: g["total_visits"].sum() / g["staff_count"].sum()
            if g["staff_count"].sum() > 0 else 0)
        dept_wt = this_df.groupby("department")["avg_wait_minutes"].mean()
        overloaded = dept_vps[dept_vps > 7].sort_values(ascending=False)
        wt_breach  = dept_wt[dept_wt > 20].sort_values(ascending=False)
        double_p   = [d for d in dept_vps.index
                      if d in dept_wt.index and dept_vps[d] > 7 and dept_wt[d] > 20]
        fac_vps_t  = vis_t / tot(this_df, "staff_count") if tot(this_df, "staff_count") > 0 else 0
        fac_vps_p  = vis_p / tot(prior_df, "staff_count") if tot(prior_df, "staff_count") > 0 else 0
        wt_t = float(this_df["avg_wait_minutes"].mean())
        wt_p = float(prior_df["avg_wait_minutes"].mean())
        np_rate_t  = np_t / vis_t * 100 if vis_t > 0 else 0
        np_rate_p  = np_p / vis_p * 100 if vis_p > 0 else 0

        s.update({
            "no_show_rate_this": nsr_t, "no_show_rate_prior": nsr_p,
            "no_show_rate_delta_pp": pp(nsr_t, nsr_p),
            "no_show_trend": trend(early_df, late_df, "no_show_count", higher_good=False),
            "no_show_above_benchmark": [(d, round(v, 1))
                                        for d, v in dept_nsr[dept_nsr > 10].sort_values(ascending=False).items()],
            "worst_no_show_dept": (dept_nsr.idxmax(), round(dept_nsr.max(), 1)),
            "no_show_revenue_loss": round(nos_t * rpv_t, 0),
            "total_visits_this": int(vis_t), "total_visits_prior": int(vis_p),
            "visits_delta_pct": pct(vis_t, vis_p),
            "visits_trend": trend(early_df, late_df, "total_visits"),
            "new_patient_rate_this": round(np_rate_t, 1),
            "new_patient_rate_prior": round(np_rate_p, 1),
            "new_patient_rate_delta_pp": pp(np_rate_t, np_rate_p),
            "new_patients_this": int(np_t), "new_patients_prior": int(np_p),
            "new_patients_delta_pct": pct(np_t, np_p),
            "new_patients_trend": trend(early_df, late_df, "new_patients"),
            "visits_per_staff_this": round(fac_vps_t, 1),
            "visits_per_staff_prior": round(fac_vps_p, 1),
            "overloaded_depts": [(d, round(v, 1)) for d, v in overloaded.items()],
            "most_overloaded_dept": (dept_vps.idxmax(), round(dept_vps.max(), 1)),
            "wait_time_this": round(wt_t, 1), "wait_time_prior": round(wt_p, 1),
            "wait_time_delta": round(wt_t - wt_p, 1),
            "wait_trend": trend(early_df, late_df, "avg_wait_minutes", higher_good=False, agg="mean"),
            "wait_above_benchmark": [(d, round(v, 1)) for d, v in wt_breach.items()],
            "worst_wait_dept": (dept_wt.idxmax(), round(dept_wt.max(), 1)),
            "best_wait_dept":  (dept_wt.idxmin(), round(dept_wt.min(), 1)),
            "double_pressure_depts": double_p,
        })

    elif report_mode == "billing":
        rev_t = tot(this_df, "total_revenue"); rev_p = tot(prior_df, "total_revenue")
        sub_t = tot(this_df, "claims_submitted"); sub_p = tot(prior_df, "claims_submitted")
        den_t = tot(this_df, "claims_denied");  den_p = tot(prior_df, "claims_denied")
        app_t = tot(this_df, "claims_approved"); app_p = tot(prior_df, "claims_approved")
        rr_t  = wtd_rate(this_df, "claims_denied", "claims_submitted")
        rr_p  = wtd_rate(prior_df, "claims_denied", "claims_submitted")
        ar_t  = wtd_avg(this_df, "auto_resolve_pct", "claims_denied")
        ar_p  = wtd_avg(prior_df, "auto_resolve_pct", "claims_denied")
        rpc_t = rev_t / sub_t if sub_t > 0 else 0
        rpc_p = rev_p / sub_p if sub_p > 0 else 0

        dept_rr = this_df.groupby("department").apply(
            lambda g: g["claims_denied"].sum() / g["claims_submitted"].sum() * 100
            if g["claims_submitted"].sum() > 0 else 0)
        dept_ar_pct = this_df.groupby("department").apply(
            lambda g: (g["auto_resolve_pct"] * g["claims_denied"]).sum() / g["claims_denied"].sum()
            if g["claims_denied"].sum() > 0 else 0)
        dept_rev  = this_df.groupby("department")["total_revenue"].sum()
        dept_sub  = this_df.groupby("department")["claims_submitted"].sum()
        dept_den  = this_df.groupby("department")["claims_denied"].sum()
        dept_risk = (dept_den * (dept_rev / dept_sub)).sort_values(ascending=False)
        dept_ar_days = this_df.groupby("department")["avg_ar_days"].mean()
        double_t = [(d, round(dept_rr[d], 1), round(dept_ar_days[d], 1))
                    for d in dept_rr.index
                    if d in dept_ar_days.index and dept_rr[d] > 10 and dept_ar_days[d] > 35]

        app_rate_t = app_t / sub_t * 100 if sub_t > 0 else 0
        app_rate_p = app_p / sub_p * 100 if sub_p > 0 else 0
        shortfall  = round(den_t * max(0.0, 68.0 - ar_t) / 100, 0)

        s.update({
            "rejection_rate_this": rr_t, "rejection_rate_prior": rr_p,
            "rejection_rate_delta_pp": pp(rr_t, rr_p),
            "rejection_trend": trend(early_df, late_df, "claims_denied", higher_good=False),
            "rejection_above_benchmark": [(d, round(v, 1))
                                          for d, v in dept_rr[dept_rr > 10].sort_values(ascending=False).items()],
            "rejection_critical": [(d, round(v, 1))
                                   for d, v in dept_rr[dept_rr > 15].sort_values(ascending=False).items()],
            "worst_rejection_dept": (dept_rr.idxmax(), round(dept_rr.max(), 1)),
            "best_rejection_dept":  (dept_rr.idxmin(), round(dept_rr.min(), 1)),
            "auto_resolve_this": ar_t, "auto_resolve_prior": ar_p,
            "auto_resolve_delta_pp": pp(ar_t, ar_p),
            "auto_resolve_trend": trend(early_df, late_df, "auto_resolve_pct", agg="mean"),
            "auto_resolve_below_benchmark": [(d, round(v, 1))
                                             for d, v in dept_ar_pct[dept_ar_pct < 68].sort_values().items()],
            "auto_resolve_shortfall_claims": int(shortfall),
            "dollars_at_risk_this": round(den_t * rpc_t, 0),
            "dollars_at_risk_prior": round(den_p * rpc_p, 0),
            "dollars_at_risk_delta_pct": pct(den_t * rpc_t, den_p * rpc_p),
            "dollars_recoverable": round(den_t * rpc_t * ar_t / 100, 0),
            "top3_risk_depts": [(d, round(v, 0)) for d, v in dept_risk.head(3).items()],
            "approval_rate_this": round(app_rate_t, 1),
            "approval_rate_prior": round(app_rate_p, 1),
            "approval_rate_delta_pp": pp(app_rate_t, app_rate_p),
            "approval_trend": trend(early_df, late_df, "claims_approved"),
            "claims_submitted_this": int(sub_t), "claims_submitted_prior": int(sub_p),
            "double_trouble_depts": double_t,
        })

    return s


def _format_brief(s: dict, report_mode: str) -> str:
    """Format pre-computed signals into a structured text brief for the AI prompt."""
    w, p = s.get("week_this", "this week"), s.get("week_prior", "prior week")

    if report_mode == "financial":
        ar_str  = ", ".join(f"{d} ({v}d)" for d, v in s.get("ar_days_above_threshold", [])) or "None"
        top3    = ", ".join(f"{d} (${v:,.0f})" for d, v in s.get("top3_revenue_depts", []))
        dd, dv  = s.get("biggest_revenue_drop", ("—", 0))
        gd, gv  = s.get("biggest_revenue_gain", ("—", 0))
        ld, lv  = s.get("lowest_rpv_dept", ("—", 0))
        cd, cv  = s.get("lowest_coll_rate_dept", ("—", 0))
        nd, nv  = s.get("worst_no_show_dept", ("—", 0))
        lines = [
            f"FINANCIAL SIGNALS — Week of {w} vs {p}",
            "",
            "REVENUE:",
            f"  Facility total: ${s['revenue_this']:,.0f} vs ${s['revenue_prior']:,.0f} ({s['revenue_delta_pct']:+.1f}%). 4-week trend: {s['revenue_trend'].upper()}.",
            f"  Top 3 departments: {top3}.",
            f"  Biggest drop: {dd} (${dv:+,.0f}). Biggest gain: {gd} (${gv:+,.0f}).",
            "",
            "REVENUE PER VISIT:",
            f"  Facility: ${s['revenue_per_visit_this']:,.2f} vs ${s['revenue_per_visit_prior']:,.2f} ({s['revenue_per_visit_delta_pct']:+.1f}%).",
            f"  Lowest efficiency dept: {ld} (${lv:,.2f}/visit).",
            "",
            "COLLECTIONS:",
            f"  Rate: {s['collections_rate_this']}% vs {s['collections_rate_prior']}% ({s['collections_rate_delta_pp']:+.1f}pp). 4-week trend: {s['collections_trend'].upper()}.",
            f"  Uncollected this week: ${s['uncollected_this']:,.0f}.",
            f"  Lowest collections rate dept: {cd} ({cv}%).",
            "",
            "A/R DAYS (35-day threshold):",
            f"  Facility avg: {s['ar_days_this']}d vs {s['ar_days_prior']}d ({s['ar_days_delta']:+.1f}d). 4-week trend: {s['ar_trend'].upper()}.",
            f"  Above threshold: {ar_str}.",
            "",
            "DENIAL REVENUE IMPACT:",
            f"  Revenue at risk from denials: ${s['denial_revenue_risk_this']:,.0f} vs ${s['denial_revenue_risk_prior']:,.0f} ({s['denial_revenue_risk_delta_pct']:+.1f}%).",
            "",
            "NO-SHOW REVENUE LOSS:",
            f"  Estimated: ${s['no_show_revenue_loss']:,.0f}. Worst dept: {nd} (${nv:,.0f} lost).",
        ]

    elif report_mode == "clinical":
        nsr_str = ", ".join(f"{d} ({v}%)" for d, v in s.get("no_show_above_benchmark", [])) or "None"
        ol_str  = ", ".join(f"{d} ({v}:1)" for d, v in s.get("overloaded_depts", [])) or "None"
        wt_str  = ", ".join(f"{d} ({v}min)" for d, v in s.get("wait_above_benchmark", [])) or "None"
        dp_str  = ", ".join(s.get("double_pressure_depts", [])) or "None"
        wn, wv  = s.get("worst_no_show_dept", ("—", 0))
        mo, mv  = s.get("most_overloaded_dept", ("—", 0))
        ww, wwv = s.get("worst_wait_dept", ("—", 0))
        bw, bwv = s.get("best_wait_dept", ("—", 0))
        lines = [
            f"CLINICAL SIGNALS — Week of {w} vs {p}",
            "",
            "NO-SHOW RATE (10% benchmark):",
            f"  Facility: {s['no_show_rate_this']}% vs {s['no_show_rate_prior']}% ({s['no_show_rate_delta_pp']:+.1f}pp). 4-week trend: {s['no_show_trend'].upper()}.",
            f"  Above benchmark: {nsr_str}.",
            f"  Worst dept: {wn} ({wv}%). Estimated revenue loss from no-shows: ${s['no_show_revenue_loss']:,.0f}.",
            "",
            "PATIENT VOLUME:",
            f"  Total visits: {s['total_visits_this']:,} vs {s['total_visits_prior']:,} ({s['visits_delta_pct']:+.1f}%). 4-week trend: {s['visits_trend'].upper()}.",
            f"  New patient rate: {s['new_patient_rate_this']}% vs {s['new_patient_rate_prior']}% ({s['new_patient_rate_delta_pp']:+.1f}pp). Trend: {s['new_patients_trend'].upper()}.",
            f"  New patients: {s['new_patients_this']:,} vs {s['new_patients_prior']:,} ({s['new_patients_delta_pct']:+.1f}%).",
            "",
            "STAFF UTILIZATION (7:1 threshold):",
            f"  Facility avg: {s['visits_per_staff_this']} vs {s['visits_per_staff_prior']} visits/staff.",
            f"  Above 7:1: {ol_str}.",
            f"  Most overloaded: {mo} ({mv}:1).",
            "",
            "WAIT TIMES (20-min benchmark):",
            f"  Facility avg: {s['wait_time_this']}min vs {s['wait_time_prior']}min ({s['wait_time_delta']:+.1f}min). 4-week trend: {s['wait_trend'].upper()}.",
            f"  Above 20min: {wt_str}.",
            f"  Worst: {ww} ({wwv}min). Best: {bw} ({bwv}min).",
            f"  Double-pressure (high ratio + high wait): {dp_str}.",
        ]

    elif report_mode == "billing":
        rr_str  = ", ".join(f"{d} ({v}%)" for d, v in s.get("rejection_above_benchmark", [])) or "None"
        rr_crit = ", ".join(f"{d} ({v}%)" for d, v in s.get("rejection_critical", [])) or "None"
        ar_str  = ", ".join(f"{d} ({v}%)" for d, v in s.get("auto_resolve_below_benchmark", [])) or "None"
        r3_str  = ", ".join(f"{d} (${v:,.0f})" for d, v in s.get("top3_risk_depts", []))
        dt_str  = ", ".join(f"{d} (rej {rj}%, A/R {ar}d)"
                            for d, rj, ar in s.get("double_trouble_depts", [])) or "None"
        wr, wv  = s.get("worst_rejection_dept", ("—", 0))
        br, bv  = s.get("best_rejection_dept", ("—", 0))
        lines = [
            f"BILLING SIGNALS — Week of {w} vs {p}",
            "",
            "REJECTION RATE (10% benchmark):",
            f"  Facility: {s['rejection_rate_this']}% vs {s['rejection_rate_prior']}% ({s['rejection_rate_delta_pp']:+.1f}pp). 4-week trend: {s['rejection_trend'].upper()}.",
            f"  Above 10%: {rr_str}.",
            f"  CRITICAL above 15%: {rr_crit}.",
            f"  Worst: {wr} ({wv}%). Best: {br} ({bv}%).",
            "",
            "AUTO-RESOLVE RATE (68% target):",
            f"  Facility: {s['auto_resolve_this']}% vs {s['auto_resolve_prior']}% ({s['auto_resolve_delta_pp']:+.1f}pp). 4-week trend: {s['auto_resolve_trend'].upper()}.",
            f"  Below 68% target: {ar_str}.",
            f"  Shortfall: ~{s['auto_resolve_shortfall_claims']} denied claims should have been auto-resolvable.",
            "",
            "DOLLARS AT RISK:",
            f"  This week: ${s['dollars_at_risk_this']:,.0f} vs ${s['dollars_at_risk_prior']:,.0f} ({s['dollars_at_risk_delta_pct']:+.1f}%).",
            f"  Recoverable via auto-resolve: ${s['dollars_recoverable']:,.0f}.",
            f"  Top 3 risk departments: {r3_str}.",
            "",
            "APPROVAL TRAJECTORY:",
            f"  Approval rate: {s['approval_rate_this']}% vs {s['approval_rate_prior']}% ({s['approval_rate_delta_pp']:+.1f}pp). 4-week trend: {s['approval_trend'].upper()}.",
            f"  Claims submitted: {s['claims_submitted_this']:,} vs {s['claims_submitted_prior']:,}.",
            "",
            f"DOUBLE-TROUBLE DEPARTMENTS (high rejection + high A/R days): {dt_str}.",
        ]
    else:
        lines = [f"SIGNALS — Week of {w} vs {p}", json.dumps(s, indent=2)]

    return "\n".join(lines)


_SYSTEM_PROMPTS = {
    "financial": (
        "You are a CFO-level healthcare financial analyst reviewing a pre-computed weekly brief. "
        "Interpret the signals, explain business impact, and prioritize. "
        "Return a JSON object with an 'insights' array of exactly 4-5 items ranked by business impact. "
        "Each item: severity (critical|warning|ok|info), title (≤10 words, cite a number), "
        "detail (2-3 sentences with specific dollar amounts and percentages from the brief), "
        "recommendation (one specific, actionable step). "
        "Critical = immediate cash-flow risk >$50k or deteriorating A/R. "
        "Warning = threshold breach or negative trend. Ok = stable or improving. Info = context only."
    ),
    "clinical": (
        "You are a healthcare operations director reviewing a pre-computed weekly clinical brief. "
        "Interpret the signals, explain operational impact, and prioritize. "
        "Return a JSON object with an 'insights' array of exactly 4-5 items ranked by business impact. "
        "Each item: severity (critical|warning|ok|info), title (≤10 words, cite a number), "
        "detail (2-3 sentences with specific rates and department names from the brief), "
        "recommendation (one specific, actionable step). "
        "Critical = double-pressure department or facility no-show rate above 18%. "
        "Warning = benchmark breach or worsening 4-week trend. Ok = at or below benchmark."
    ),
    "billing": (
        "You are a billing manager for a healthcare organization reviewing a pre-computed weekly brief. "
        "Interpret the signals, explain revenue impact, and prioritize. "
        "Return a JSON object with an 'insights' array of exactly 4-5 items ranked by business impact. "
        "Each item: severity (critical|warning|ok|info), title (≤10 words, cite a number), "
        "detail (2-3 sentences with dollar amounts, rates, and department names from the brief), "
        "recommendation (one specific, actionable step). "
        "Critical = any department above 15% rejection or dollars-at-risk up >20% WoW. "
        "Warning = below benchmark or worsening trend. Ok = at or above target."
    ),
}


# ── Tool 2: Analyze trends ────────────────────────────────────────────────────

def analyze_trends(report_mode: str) -> str:
    """
    Analyze the loaded metrics data and return prioritized weekly insights.

    Args:
        report_mode: One of 'financial', 'clinical', or 'billing'.

    Returns:
        JSON string with a list of insights (severity, title, detail, recommendation).
    """
    session = _SESSION.get(report_mode)
    if not session or "df" not in session:
        return json.dumps({
            "success": False,
            "error": f"No data loaded for '{report_mode}'. Call fetch_data_csv first.",
        })

    df      = session["df"]
    signals = _compute_signals(df, report_mode)
    brief   = _format_brief(signals, report_mode)

    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    api_key  = os.getenv("AZURE_API_KEY", "")
    model    = os.getenv("MODEL_DEPLOYMENT_NAME", "o4-mini")
    insights = None

    if endpoint and api_key:
        try:
            from openai import OpenAI
            client   = OpenAI(base_url=endpoint, api_key=api_key)
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPTS[report_mode]},
                    {"role": "user",   "content": brief},
                ],
                response_format={"type": "json_object"},
            )
            parsed   = json.loads(response.choices[0].message.content)
            insights = parsed.get("insights", parsed)
        except Exception:
            pass

    if insights is None:
        mock = {
            "financial": [
                {"severity": "warning", "title": "Collections rate fell 2.9pp this week",
                 "detail": "Facility-wide collections rate dropped, leaving additional uncollected revenue. A/R aging is trending upward.",
                 "recommendation": "Pull A/R aging report and prioritize follow-up on accounts >35 days."},
                {"severity": "warning", "title": "Denial revenue risk increased this week",
                 "detail": "Revenue at risk from claim denials rose week-over-week. No-show revenue loss is compounding the shortfall.",
                 "recommendation": "Audit top denial departments and review prior-auth workflows."},
                {"severity": "info", "title": "Revenue per visit stable facility-wide",
                 "detail": "No major efficiency changes detected. One department is significantly below the facility average.",
                 "recommendation": "Review coding practices in the lowest revenue-per-visit department."},
            ],
            "clinical": [
                {"severity": "warning", "title": "Multiple departments above 10% no-show benchmark",
                 "detail": "Facility no-show rate is above target. Revenue lost to missed appointments is significant.",
                 "recommendation": "Implement SMS reminder 48h and 2h before appointment for high-risk departments."},
                {"severity": "warning", "title": "Staff utilization above 7:1 in several departments",
                 "detail": "Multiple departments exceed the visits-per-staff threshold. Wait times are elevated in the same areas.",
                 "recommendation": "Add per-diem staff coverage to double-pressure departments this week."},
                {"severity": "ok", "title": "New patient volume holding steady",
                 "detail": "New patient capture rate is stable week-over-week.",
                 "recommendation": "Maintain current referral and intake workflows."},
            ],
            "billing": [
                {"severity": "critical", "title": "Departments above 15% rejection rate this week",
                 "detail": "Critical-threshold rejection departments identified. Dollars at risk from denials are elevated.",
                 "recommendation": "Escalate to billing supervisor for immediate audit of critical departments."},
                {"severity": "warning", "title": "Auto-resolve rate below 68% target",
                 "detail": "Shortfall means denied claims are not being resolved automatically as expected.",
                 "recommendation": "Review classifier thresholds for prior-auth and modifier error categories."},
                {"severity": "ok", "title": "Approval rate stable week-over-week",
                 "detail": "Overall claim approval trajectory is holding. Best-performing department shows replicable practices.",
                 "recommendation": "Document best-performing department's billing workflow for team training."},
            ],
        }
        insights = mock.get(report_mode, mock["financial"])

    _SESSION[report_mode]["insights"] = insights

    for insight in insights:
        print(f"REPORT_INSIGHT:{json.dumps({'_mode': report_mode, **insight})}", flush=True)

    return json.dumps({
        "success":       True,
        "report_mode":   report_mode,
        "insight_count": len(insights),
        "insights":      insights,
        "next_step":     "Call generate_charts with the same report_mode.",
    })


# ── Tool 3: Generate charts ───────────────────────────────────────────────────

def generate_charts(report_mode: str) -> str:
    """
    Render weekly comparison charts for the report and save them as PNG files.

    Args:
        report_mode: One of 'financial', 'clinical', or 'billing'.

    Returns:
        JSON string with the number of charts generated and their file paths.
    """
    session = _SESSION.get(report_mode)
    if not session or "df" not in session:
        return json.dumps({
            "success": False,
            "error": f"No data loaded for '{report_mode}'. Call fetch_data_csv first.",
        })

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from reporting.chart_generator import generate_report_charts

    try:
        chart_paths = generate_report_charts(session["df"], report_mode)
        _SESSION[report_mode]["chart_paths"] = chart_paths
        return json.dumps({
            "success":     True,
            "report_mode": report_mode,
            "chart_count": len(chart_paths),
            "chart_paths": chart_paths,
            "next_step":   "Call build_report with the same report_mode.",
        })
    except Exception as e:
        _SESSION[report_mode]["chart_paths"] = []
        return json.dumps({"success": False, "error": str(e), "chart_paths": []})


# ── Tool 4: Build the HTML/PDF report ────────────────────────────────────────

def build_report(report_mode: str) -> str:
    """
    Assemble the weekly HTML insight report with embedded charts and convert to PDF.

    Args:
        report_mode: One of 'financial', 'clinical', or 'billing'.

    Returns:
        JSON string with the report ID and path to the HTML report file.
    """
    session = _SESSION.get(report_mode)
    if not session:
        return json.dumps({
            "success": False,
            "error": f"No session data for '{report_mode}'. Run earlier steps first.",
        })

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from reporting.report_builder import build_report as _build

    insights    = session.get("insights", [])
    chart_paths = session.get("chart_paths", [])

    try:
        result      = _build(insights, chart_paths, report_mode)
        html_path   = result["html_path"]
        report_id   = result["report_id"]
        _SESSION[report_mode]["report_path"] = html_path
        _SESSION[report_mode]["report_id"]   = report_id

        # Upload to Azure Blob and generate a 7-day SAS URL
        report_url = None
        conn_str   = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
        container  = os.getenv("AZURE_STORAGE_CONTAINER_REPORTS", "reports")
        if conn_str:
            try:
                from azure.storage.blob import (
                    BlobServiceClient, BlobSasPermissions,
                    ContentSettings, generate_blob_sas,
                )
                from datetime import timezone, timedelta
                blob_name  = f"{report_id}.html"
                svc        = BlobServiceClient.from_connection_string(conn_str)
                bc         = svc.get_blob_client(container=container, blob=blob_name)
                with open(html_path, "rb") as f:
                    bc.upload_blob(f, overwrite=True,
                                   content_settings=ContentSettings(content_type="text/html"))
                sas = generate_blob_sas(
                    account_name=svc.account_name,
                    container_name=container,
                    blob_name=blob_name,
                    account_key=svc.credential.account_key,
                    permission=BlobSasPermissions(read=True),
                    expiry=datetime.now(timezone.utc) + timedelta(days=7),
                )
                report_url = f"https://{svc.account_name}.blob.core.windows.net/{container}/{blob_name}?{sas}"
            except Exception:
                pass

        return json.dumps({
            "success":     True,
            "report_mode": report_mode,
            "report_id":   report_id,
            "html_path":   html_path,
            "report_url":  report_url,
            "pdf_path":    result.get("pdf_path"),
            "next_step":   "Call send_report_email with the same report_mode.",
        })
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})


# ── Tool 5: Send the report email ─────────────────────────────────────────────

def send_report_email(report_mode: str) -> str:
    """
    Email the compiled weekly report to the configured admin distribution list.

    Args:
        report_mode: One of 'financial', 'clinical', or 'billing'.

    Returns:
        JSON string with send status and message IDs.
    """
    session = _SESSION.get(report_mode)
    if not session or "report_path" not in session:
        return json.dumps({
            "success": False,
            "error": f"No report built for '{report_mode}'. Call build_report first.",
        })

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from reporting.email_sender import send_report

    recipients_str = os.getenv("ADMIN_EMAIL_RECIPIENTS", "")
    recipient_list = [r.strip() for r in recipients_str.split(",") if r.strip()]

    try:
        result = send_report(session["report_path"], report_mode, recipient_list)
        return json.dumps({"success": True, **result})
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)})
