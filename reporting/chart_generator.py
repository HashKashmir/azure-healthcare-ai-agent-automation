"""
Chart generator — produces matplotlib PNGs for the weekly admin report.

Three charts per mode, each tied directly to a signal the AI will comment on:

  Financial
    1. Bar  — Revenue this week vs. prior week by department
    2. Line — Collections rate (%) 4-week trend with facility average
    3. Bar  — A/R days by department with 35-day threshold line

  Clinical
    1. Bar  — No-show rate by department with 10% benchmark line
    2. Line — No-show rate 4-week trend with 10% benchmark
    3. Bar  — Staff utilization (visits/staff) by department with 7:1 threshold

  Billing
    1. Bar  — Rejection rate by department with 10% and 15% threshold lines
    2. Line — Rejection rate 4-week trend with 10% benchmark
    3. Bar  — Auto-resolve rate by department with 68% target line
"""

import os
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import pandas as pd

COLOR_PRIMARY   = "#1565C0"
COLOR_SECONDARY = "#90CAF9"
COLOR_WARNING   = "#E53935"
COLOR_CRITICAL  = "#B71C1C"
COLOR_OK        = "#43A047"
COLOR_NEUTRAL   = "#757575"

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "report_charts")


def generate_report_charts(df: pd.DataFrame, report_mode: str) -> list[str]:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    df = df.copy()
    df["week_start_date"] = pd.to_datetime(df["week_start_date"])
    df = df.sort_values("week_start_date")
    weeks = sorted(df["week_start_date"].unique())

    if len(weeks) < 2 or "department" not in df.columns:
        return []

    this_week  = weeks[-1]
    prior_week = weeks[-2]
    this_df    = df[df["week_start_date"] == this_week]
    prior_df   = df[df["week_start_date"] == prior_week]
    depts      = sorted(df["department"].unique())
    paths      = []

    if report_mode == "financial":
        p = _revenue_bar(this_df, prior_df, depts, this_week, prior_week, ts)
        if p: paths.append(p)
        p = _collections_rate_line(df, weeks, ts)
        if p: paths.append(p)
        p = _ar_days_bar(this_df, depts, ts)
        if p: paths.append(p)

    elif report_mode == "clinical":
        p = _no_show_rate_bar(this_df, depts, ts)
        if p: paths.append(p)
        p = _no_show_rate_line(df, weeks, ts)
        if p: paths.append(p)
        p = _staff_utilization_bar(this_df, depts, ts)
        if p: paths.append(p)

    elif report_mode == "billing":
        p = _rejection_rate_bar(this_df, depts, ts)
        if p: paths.append(p)
        p = _rejection_rate_line(df, weeks, ts)
        if p: paths.append(p)
        p = _auto_resolve_bar(this_df, depts, ts)
        if p: paths.append(p)

    return paths


# ── Financial charts ──────────────────────────────────────────────────────────

def _revenue_bar(this_df, prior_df, depts, this_week, prior_week, ts):
    if "total_revenue" not in this_df.columns:
        return None
    this_vals  = [this_df[this_df["department"] == d]["total_revenue"].sum() for d in depts]
    prior_vals = [prior_df[prior_df["department"] == d]["total_revenue"].sum() for d in depts]
    x, w = range(len(depts)), 0.38
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.bar([i - w/2 for i in x], prior_vals, width=w, color=COLOR_SECONDARY, edgecolor="white",
           label=f"Prior ({prior_week.strftime('%b %d')})")
    ax.bar([i + w/2 for i in x], this_vals,  width=w, color=COLOR_PRIMARY,   edgecolor="white",
           label=f"This week ({this_week.strftime('%b %d')})")
    ax.set_xticks(list(x)); ax.set_xticklabels([depts[i] for i in x], rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("Revenue ($)", fontsize=10)
    ax.set_title("Weekly Revenue — This Week vs. Prior Week by Department", fontsize=12, fontweight="bold")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    ax.legend(fontsize=9); ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, f"financial_revenue_bar_{ts}.png")
    fig.savefig(path, dpi=120); plt.close(fig)
    return path


def _collections_rate_line(df, weeks, ts):
    if "collections_amount" not in df.columns or "total_revenue" not in df.columns:
        return None
    weekly = df.groupby("week_start_date").apply(
        lambda g: g["collections_amount"].sum() / g["total_revenue"].sum() * 100
        if g["total_revenue"].sum() > 0 else 0
    ).reset_index(name="collections_rate")
    weekly = weekly.sort_values("week_start_date")
    overall_avg = round(weekly["collections_rate"].mean(), 1)
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(weekly["week_start_date"], weekly["collections_rate"],
            color=COLOR_PRIMARY, linewidth=2.5, marker="o", markersize=7, label="Collections Rate (%)")
    ax.axhline(overall_avg, color=COLOR_NEUTRAL, linewidth=1.5, linestyle="--",
               label=f"4-week avg: {overall_avg}%")
    ax.set_xlabel("Week", fontsize=10); ax.set_ylabel("Collections Rate (%)", fontsize=10)
    ax.set_title("4-Week Collections Rate Trend (%)", fontsize=12, fontweight="bold")
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %d"))
    ax.tick_params(axis="x", rotation=20); ax.legend(fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.1f}%"))
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, f"financial_collections_rate_line_{ts}.png")
    fig.savefig(path, dpi=120); plt.close(fig)
    return path


def _ar_days_bar(this_df, depts, ts):
    if "avg_ar_days" not in this_df.columns:
        return None
    vals = [this_df[this_df["department"] == d]["avg_ar_days"].mean() for d in depts]
    threshold = 35.0
    colors = [COLOR_WARNING if v > threshold else COLOR_PRIMARY for v in vals]
    fig, ax = plt.subplots(figsize=(12, 5))
    bars = ax.bar(depts, vals, color=colors, edgecolor="white", width=0.6)
    ax.axhline(threshold, color=COLOR_CRITICAL, linewidth=1.8, linestyle="--",
               label=f"35-day threshold")
    for bar, val in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.4,
                f"{val:.0f}d", ha="center", va="bottom", fontsize=8,
                color=COLOR_WARNING if val > threshold else COLOR_NEUTRAL)
    ax.set_xticks(range(len(depts))); ax.set_xticklabels(depts, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("Avg A/R Days", fontsize=10)
    ax.set_title("A/R Days by Department — Red Bars Exceed 35-Day Threshold", fontsize=12, fontweight="bold")
    ax.legend(fontsize=9); ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, f"financial_ar_days_bar_{ts}.png")
    fig.savefig(path, dpi=120); plt.close(fig)
    return path


# ── Clinical charts ───────────────────────────────────────────────────────────

def _no_show_rate_bar(this_df, depts, ts):
    if "no_show_count" not in this_df.columns or "total_visits" not in this_df.columns:
        return None
    vals = []
    for d in depts:
        dg = this_df[this_df["department"] == d]
        v = dg["no_show_count"].sum() / dg["total_visits"].sum() * 100 if dg["total_visits"].sum() > 0 else 0
        vals.append(round(v, 1))
    benchmark = 10.0
    colors = [COLOR_WARNING if v > benchmark else COLOR_OK for v in vals]
    fig, ax = plt.subplots(figsize=(12, 5))
    bars = ax.bar(depts, vals, color=colors, edgecolor="white", width=0.6)
    ax.axhline(benchmark, color=COLOR_CRITICAL, linewidth=1.8, linestyle="--",
               label=f"10% benchmark")
    for bar, val in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.2,
                f"{val:.1f}%", ha="center", va="bottom", fontsize=8,
                color=COLOR_WARNING if val > benchmark else COLOR_OK)
    ax.set_xticks(range(len(depts))); ax.set_xticklabels(depts, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("No-Show Rate (%)", fontsize=10)
    ax.set_title("No-Show Rate by Department — Red Bars Exceed 10% Benchmark", fontsize=12, fontweight="bold")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.legend(fontsize=9); ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, f"clinical_no_show_bar_{ts}.png")
    fig.savefig(path, dpi=120); plt.close(fig)
    return path


def _no_show_rate_line(df, weeks, ts):
    if "no_show_count" not in df.columns or "total_visits" not in df.columns:
        return None
    weekly = df.groupby("week_start_date").apply(
        lambda g: g["no_show_count"].sum() / g["total_visits"].sum() * 100
        if g["total_visits"].sum() > 0 else 0
    ).reset_index(name="no_show_rate")
    weekly = weekly.sort_values("week_start_date")
    benchmark = 10.0
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(weekly["week_start_date"], weekly["no_show_rate"],
            color=COLOR_PRIMARY, linewidth=2.5, marker="o", markersize=7, label="No-Show Rate (%)")
    ax.axhline(benchmark, color=COLOR_WARNING, linewidth=1.5, linestyle="--",
               label=f"10% benchmark")
    ax.set_xlabel("Week", fontsize=10); ax.set_ylabel("No-Show Rate (%)", fontsize=10)
    ax.set_title("4-Week No-Show Rate Trend", fontsize=12, fontweight="bold")
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %d"))
    ax.tick_params(axis="x", rotation=20); ax.legend(fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.1f}%"))
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, f"clinical_no_show_line_{ts}.png")
    fig.savefig(path, dpi=120); plt.close(fig)
    return path


def _staff_utilization_bar(this_df, depts, ts):
    if "total_visits" not in this_df.columns or "staff_count" not in this_df.columns:
        return None
    vals = []
    for d in depts:
        dg = this_df[this_df["department"] == d]
        v = dg["total_visits"].sum() / dg["staff_count"].sum() if dg["staff_count"].sum() > 0 else 0
        vals.append(round(v, 1))
    threshold = 7.0
    colors = [COLOR_WARNING if v > threshold else COLOR_OK for v in vals]
    fig, ax = plt.subplots(figsize=(12, 5))
    bars = ax.bar(depts, vals, color=colors, edgecolor="white", width=0.6)
    ax.axhline(threshold, color=COLOR_CRITICAL, linewidth=1.8, linestyle="--",
               label="7:1 threshold")
    for bar, val in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.05,
                f"{val:.1f}", ha="center", va="bottom", fontsize=8,
                color=COLOR_WARNING if val > threshold else COLOR_OK)
    ax.set_xticks(range(len(depts))); ax.set_xticklabels(depts, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("Visits per Staff Member", fontsize=10)
    ax.set_title("Staff Utilization by Department — Red Bars Exceed 7:1 Threshold", fontsize=12, fontweight="bold")
    ax.legend(fontsize=9); ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, f"clinical_staff_util_bar_{ts}.png")
    fig.savefig(path, dpi=120); plt.close(fig)
    return path


# ── Billing charts ────────────────────────────────────────────────────────────

def _rejection_rate_bar(this_df, depts, ts):
    if "claims_denied" not in this_df.columns or "claims_submitted" not in this_df.columns:
        return None
    vals = []
    for d in depts:
        dg = this_df[this_df["department"] == d]
        v = dg["claims_denied"].sum() / dg["claims_submitted"].sum() * 100 if dg["claims_submitted"].sum() > 0 else 0
        vals.append(round(v, 1))
    warning_threshold  = 10.0
    critical_threshold = 15.0
    colors = [COLOR_CRITICAL if v > critical_threshold
              else (COLOR_WARNING if v > warning_threshold else COLOR_OK) for v in vals]
    fig, ax = plt.subplots(figsize=(12, 5))
    bars = ax.bar(depts, vals, color=colors, edgecolor="white", width=0.6)
    ax.axhline(warning_threshold,  color=COLOR_WARNING,  linewidth=1.5, linestyle="--",
               label="10% benchmark")
    ax.axhline(critical_threshold, color=COLOR_CRITICAL, linewidth=1.8, linestyle=":",
               label="15% critical threshold")
    for bar, val in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.2,
                f"{val:.1f}%", ha="center", va="bottom", fontsize=8)
    ax.set_xticks(range(len(depts))); ax.set_xticklabels(depts, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("Rejection Rate (%)", fontsize=10)
    ax.set_title("Claim Rejection Rate by Department — Warning >10%, Critical >15%", fontsize=12, fontweight="bold")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.legend(fontsize=9); ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, f"billing_rejection_bar_{ts}.png")
    fig.savefig(path, dpi=120); plt.close(fig)
    return path


def _rejection_rate_line(df, weeks, ts):
    if "claims_denied" not in df.columns or "claims_submitted" not in df.columns:
        return None
    weekly = df.groupby("week_start_date").apply(
        lambda g: g["claims_denied"].sum() / g["claims_submitted"].sum() * 100
        if g["claims_submitted"].sum() > 0 else 0
    ).reset_index(name="rejection_rate")
    weekly = weekly.sort_values("week_start_date")
    benchmark = 10.0
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(weekly["week_start_date"], weekly["rejection_rate"],
            color=COLOR_PRIMARY, linewidth=2.5, marker="o", markersize=7, label="Rejection Rate (%)")
    ax.axhline(benchmark, color=COLOR_WARNING, linewidth=1.5, linestyle="--",
               label=f"10% benchmark")
    ax.set_xlabel("Week", fontsize=10); ax.set_ylabel("Rejection Rate (%)", fontsize=10)
    ax.set_title("4-Week Claim Rejection Rate Trend", fontsize=12, fontweight="bold")
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %d"))
    ax.tick_params(axis="x", rotation=20); ax.legend(fontsize=9)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.1f}%"))
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, f"billing_rejection_line_{ts}.png")
    fig.savefig(path, dpi=120); plt.close(fig)
    return path


def _auto_resolve_bar(this_df, depts, ts):
    if "auto_resolve_pct" not in this_df.columns or "claims_denied" not in this_df.columns:
        return None
    vals = []
    for d in depts:
        dg = this_df[this_df["department"] == d]
        denied = dg["claims_denied"].sum()
        v = (dg["auto_resolve_pct"] * dg["claims_denied"]).sum() / denied if denied > 0 else 0
        vals.append(round(v, 1))
    target = 68.0
    colors = [COLOR_OK if v >= target else COLOR_WARNING for v in vals]
    fig, ax = plt.subplots(figsize=(12, 5))
    bars = ax.bar(depts, vals, color=colors, edgecolor="white", width=0.6)
    ax.axhline(target, color=COLOR_CRITICAL, linewidth=1.8, linestyle="--",
               label=f"68% target")
    for bar, val in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3,
                f"{val:.1f}%", ha="center", va="bottom", fontsize=8,
                color=COLOR_OK if val >= target else COLOR_WARNING)
    ax.set_xticks(range(len(depts))); ax.set_xticklabels(depts, rotation=30, ha="right", fontsize=9)
    ax.set_ylabel("Auto-Resolve Rate (%)", fontsize=10)
    ax.set_title("Auto-Resolve Rate by Department — Green Bars at or Above 68% Target", fontsize=12, fontweight="bold")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:.0f}%"))
    ax.legend(fontsize=9); ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    path = os.path.join(OUTPUT_DIR, f"billing_auto_resolve_bar_{ts}.png")
    fig.savefig(path, dpi=120); plt.close(fig)
    return path
