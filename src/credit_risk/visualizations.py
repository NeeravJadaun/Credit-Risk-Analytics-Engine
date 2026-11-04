"""Charts built from gold tables, so no result is typed in by hand."""

from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
import seaborn as sns

from .audit import read_json
from .findings import wilson_interval
from .monitoring import SIMULATION_BANNER

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, MUTED, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
SOURCE = (
    "Source: public LendingClub accepted loans, 2007 to 2018. Independent educational analysis."
)
plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 10,
        "axes.edgecolor": GRID,
        "axes.labelcolor": MUTED,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.color": GRID,
        "grid.linewidth": 0.7,
        "axes.axisbelow": True,
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "axes.titlelocation": "left",
        "axes.titlesize": 12,
        "axes.titleweight": "bold",
        "axes.titlecolor": INK,
    }
)


def _save(fig, path, note=SOURCE):
    fig.text(0.01, -0.03, note, fontsize=7.5, color=MUTED)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _percent(axis):
    axis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))


def vintage_trend(gold, out):
    table = pd.read_csv(gold / "gold_vintage_term.csv")
    table = table[(table["loans"] >= 500)]
    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    for term, color in [("36 months", BLUE), ("60 months", ORANGE)]:
        part = table[table["term_label"] == term].sort_values("issue_year")
        low, high = wilson_interval(part["defaults"], part["loans"])
        ax.plot(part["issue_year"], part["default_rate"], color=color, lw=2, marker="o", ms=5)
        ax.fill_between(part["issue_year"], low, high, color=color, alpha=0.15, lw=0)
        ax.annotate(
            term,
            (part["issue_year"].iloc[-1], part["default_rate"].iloc[-1]),
            xytext=(8, 0),
            textcoords="offset points",
            color=INK,
            va="center",
            fontsize=9,
        )
    ax.set_title("Observed default rate by issue year, matured loans")
    ax.set_ylim(0, None)
    _percent(ax.yaxis)
    ax.set_xticks(sorted(table["issue_year"].unique()))
    ax.grid(axis="x", visible=False)
    _save(
        fig,
        out / "01_vintage_default_rate.png",
        SOURCE + " Vintages under 500 loans hidden. Bands are 95% intervals.",
    )


def model_comparison(gold, out, aspirational):
    metrics = pd.read_csv(gold / "model_metrics.csv")
    test = metrics[
        (metrics["split"] == "test") & (metrics["model"] != "prevalence_dummy")
    ].sort_values("roc_auc")
    fig, ax = plt.subplots(figsize=(8.5, 3.2))
    names = test["model"].str.replace("_", " ").str.capitalize()
    ax.barh(names, test["roc_auc"], color=BLUE, height=0.55)
    for y, value in enumerate(test["roc_auc"]):
        ax.text(value + 0.003, y, f"{value:.3f}", va="center", color=INK, fontsize=9)
    ax.axvline(aspirational, color=MUTED, ls="--", lw=1)
    ax.text(
        aspirational - 0.004,
        len(test) - 0.75,
        f"Aspirational {aspirational:.2f}, not reached",
        color=MUTED,
        fontsize=8.5,
        ha="right",
    )
    ax.set_xlim(0.5, 0.85)
    ax.set_title("ROC-AUC on the unseen 2015 vintage")
    ax.grid(axis="y", visible=False)
    _save(
        fig,
        out / "02_model_comparison.png",
        SOURCE + " Axis starts at 0.50, the score of a random ranking.",
    )


def calibration(gold, out):
    table = pd.read_csv(gold / "model_calibration.csv")
    fig, ax = plt.subplots(figsize=(5.4, 5))
    top = max(table["observed_rate"].max(), table["mean_predicted"].max()) * 1.1
    ax.plot([0, top], [0, top], color=MUTED, lw=1, ls="--")
    ax.plot(table["mean_predicted"], table["observed_rate"], color=BLUE, lw=2, marker="o", ms=5)
    ax.set_xlim(0, top)
    ax.set_ylim(0, top)
    ax.set_xlabel("Mean predicted default rate")
    ax.set_ylabel("Observed default rate")
    _percent(ax.xaxis)
    _percent(ax.yaxis)
    ax.set_title("Calibration on the 2015 vintage, by score decile")
    ax.annotate(
        "Above the dashed line: the model under-predicts",
        (top * 0.25, top * 0.62),
        color=MUTED,
        fontsize=8.5,
    )
    _save(fig, out / "03_calibration.png")


def fico_dti_heatmap(scored, out):
    data = scored[
        (scored["split"] == "test")
        & (scored["dti_band"] != "Missing")
        & (scored["dti_band"] != "5. 40+")
    ]
    rates = data.pivot_table(
        index="fico_band", columns="dti_band", values="default_flag", aggfunc="mean"
    )
    counts = data.pivot_table(
        index="fico_band", columns="dti_band", values="default_flag", aggfunc="size"
    )
    labels = rates.map(lambda v: f"{v:.1%}") + counts.map(lambda n: f"\nn={n:,}")
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    sns.heatmap(
        rates,
        annot=labels,
        fmt="",
        cmap=sns.light_palette(BLUE, as_cmap=True),
        linewidths=2,
        linecolor=SURFACE,
        cbar_kws={
            "format": matplotlib.ticker.PercentFormatter(1.0, decimals=0),
            "label": "Observed default rate",
        },
        ax=ax,
        annot_kws={"fontsize": 8.5},
    )
    ax.set_title("Default rate by FICO and debt-to-income, 2015 vintage")
    ax.set_xlabel("Debt-to-income band")
    ax.set_ylabel("FICO band")
    _save(fig, out / "04_fico_dti_heatmap.png")


def correlation(scored, out):
    columns = {
        "fico_mid": "FICO",
        "dti": "DTI",
        "revol_util": "Utilization",
        "annual_inc": "Income",
        "loan_amnt": "Loan amount",
        "int_rate": "Interest rate",
        "inq_last_6mths": "Inquiries",
        "loan_to_income": "Loan / income",
        "default_flag": "Default",
    }
    corr = scored[list(columns)].rename(columns=columns).corr(method="spearman")
    fig, ax = plt.subplots(figsize=(7.2, 5.8))
    sns.heatmap(
        corr,
        annot=True,
        fmt=".2f",
        vmin=-1,
        vmax=1,
        cmap=sns.diverging_palette(25, 250, s=80, l=55, as_cmap=True),
        mask=np.triu(np.ones_like(corr, dtype=bool), 1),
        linewidths=1.5,
        linecolor=SURFACE,
        annot_kws={"fontsize": 8},
        cbar_kws={"label": "Spearman correlation"},
        ax=ax,
    )
    ax.set_title("Rank correlations, matured loans")
    ax.grid(False)
    _save(fig, out / "05_correlation.png")


def findings_forest(gold, out):
    table = pd.read_csv(gold / "risk_findings.csv").sort_values("oot_relative_risk")
    fig, ax = plt.subplots(figsize=(9.5, 5))
    y = np.arange(len(table))
    colors = [ORANGE if lc else BLUE for lc in table["uses_lc_pricing"]]
    for i, (_, row) in enumerate(table.iterrows()):
        ax.plot([row["oot_rr_ci95_low"], row["oot_rr_ci95_high"]], [i, i], color=colors[i], lw=2)
        ax.plot(row["oot_relative_risk"], i, "o", color=colors[i], ms=6)
        ax.text(
            row["oot_rr_ci95_high"] * 1.03,
            i,
            f"{row['oot_relative_risk']:.2f}×",
            va="center",
            fontsize=8.5,
            color=INK,
        )
    ax.axvline(1, color=MUTED, lw=1, ls="--")
    ax.set_yticks(y, [n if len(n) < 58 else n[:55] + "..." for n in table["name"]], fontsize=8.5)
    ax.set_xscale("log")
    ax.set_xticks([1, 2, 3, 5])
    ax.xaxis.set_major_formatter(matplotlib.ticker.ScalarFormatter())
    ax.xaxis.set_minor_formatter(matplotlib.ticker.NullFormatter())
    ax.set_xlabel("Relative risk of default on the 2015 vintage (log scale, 95% interval)")
    ax.set_title("Pre-specified borrower segments, tested out of time")
    ax.grid(axis="y", visible=False)
    _save(
        fig,
        out / "06_relative_risk.png",
        SOURCE + " Blue: borrower attributes. Orange: LendingClub's own grade.",
    )


def capture_curve(scored, out):
    test = scored[scored["split"] == "test"].sort_values(["score", "loan_id"], ascending=False)
    share = np.arange(1, len(test) + 1) / len(test)
    capture = test["default_flag"].cumsum().to_numpy() / test["default_flag"].sum()
    fig, ax = plt.subplots(figsize=(7, 4.4))
    step = max(1, len(test) // 500)
    ax.plot(share[::step], capture[::step], color=BLUE, lw=2)
    ax.plot([0, 1], [0, 1], color=MUTED, lw=1, ls="--")
    ax.text(0.68, 0.60, "Random ranking", color=MUTED, fontsize=8.5, rotation=30)
    for mark in (0.05, 0.10, 0.20):
        index = int(mark * len(test)) - 1
        ax.plot(mark, capture[index], "o", color=BLUE, ms=6)
        ax.annotate(
            f"{mark:.0%} reviewed: {capture[index]:.1%} of defaults",
            (mark, capture[index]),
            xytext=(10, -4),
            textcoords="offset points",
            fontsize=8.5,
            color=INK,
        )
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    _percent(ax.xaxis)
    _percent(ax.yaxis)
    ax.set_xlabel("Share of loans hypothetically referred")
    ax.set_ylabel("Share of defaults captured")
    ax.set_title("Default capture by review volume, 2015 vintage")
    _save(
        fig,
        out / "07_capture_curve.png",
        SOURCE + " Retrospective, not evidence that a review would reduce defaults.",
    )


def simulated_monitoring(gold, out):
    table = pd.read_csv(gold / "monitoring_simulated_batches.csv")
    panels = [
        ("default_rate_abs_deviation", "Default-rate deviation"),
        ("score_psi", "Score PSI"),
        ("auc_drop", "AUC drop"),
        ("max_field_null_share", "Largest field null share"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(10, 5.6), sharex=True)
    for ax, (metric, title) in zip(axes.flat, panels, strict=True):
        part = table[table["metric"] == metric]
        labels = part["batch_id"].str[-2:]
        ax.plot(labels, part["value"], color=BLUE, lw=1.6, marker="o", ms=4)
        ax.axhline(part["warn_level"].iloc[0], color=MUTED, ls=":", lw=1)
        ax.axhline(part["critical_level"].iloc[0], color=MUTED, ls="--", lw=1)
        ax.set_title(title, fontsize=10)
        ax.grid(axis="x", visible=False)
    fig.suptitle(
        SIMULATION_BANNER + " Injected faults in batches 09 to 12.",
        x=0.01,
        ha="left",
        fontsize=12,
        fontweight="bold",
        color=INK,
    )
    fig.tight_layout(rect=(0, 0.03, 1, 0.97))
    _save(
        fig,
        out / "08_simulated_monitoring.png",
        "Simulation built from partitioned 2015 loans. Dotted line: warn. Dashed line: critical. Not observed portfolio history.",
    )


def plotly_explorer(scored, out):
    types = {
        "grade": "Grade",
        "fico_band": "FICO band",
        "dti_band": "DTI band",
        "utilization_band": "Utilization band",
        "income_band": "Income band",
        "loan_amount_band": "Loan amount band",
        "employment_band": "Employment length",
        "purpose": "Purpose",
    }
    records = []
    data = scored[scored["term_months"] == 36]
    for column in types:
        for year, part in [
            ("All years", data),
            *[(str(y), g) for y, g in data[data["issue_year"] >= 2012].groupby("issue_year")],
        ]:
            grouped = part.groupby(column)["default_flag"].agg(["size", "sum"]).reset_index()
            low, high = wilson_interval(grouped["sum"], grouped["size"])
            for i, row in grouped.iterrows():
                records.append(
                    {
                        "t": column,
                        "y": year,
                        "s": str(row[column]),
                        "n": int(row["size"]),
                        "d": int(row["sum"]),
                        "r": row["sum"] / row["size"],
                        "l": float(low[i]),
                        "h": float(high[i]),
                    }
                )
    figure = go.Figure(go.Bar(orientation="h", marker_color=BLUE))
    figure.update_layout(
        template="plotly_white",
        font={"family": "Arial", "color": INK},
        margin={"l": 170, "r": 30, "t": 30, "b": 50},
        height=520,
        xaxis={"tickformat": ".0%", "title": "Observed default rate", "rangemode": "tozero"},
        yaxis={"automargin": True},
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
    )
    body = pio.to_html(
        figure,
        include_plotlyjs="cdn",
        full_html=False,
        div_id="chart",
        config={"displaylogo": False},
    )
    options = "".join(f'<option value="{k}">{v}</option>' for k, v in types.items())
    years = "".join(
        f'<option value="{y}">{y}</option>' for y in ["All years", "2012", "2013", "2014", "2015"]
    )
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Segment Explorer</title>
<style>body{{font-family:Arial,sans-serif;background:{SURFACE};color:{INK};margin:24px auto;max-width:980px;padding:0 16px}}h1{{font-size:22px;margin:0 0 6px}}p{{color:{MUTED};font-size:14px;margin:0 0 14px}}label{{font-size:13px;color:{MUTED};margin-right:16px}}select{{font:inherit;color:{INK};padding:4px 6px;margin-left:6px}}</style></head><body>
<h1>Default rate by borrower segment</h1><p>36-month matured LendingClub loans. Public data, independent educational analysis. Hover a bar for the sample size and 95% interval.</p>
<label>Segment<select id="t">{options}</select></label><label>Issue year<select id="y">{years}</select></label>{body}
<script>const D={json.dumps(records)};function draw(){{const t=document.getElementById('t').value,y=document.getElementById('y').value;const R=D.filter(d=>d.t===t&&d.y===y).sort((a,b)=>a.s<b.s?1:-1);
Plotly.react('chart',[{{type:'bar',orientation:'h',x:R.map(d=>d.r),y:R.map(d=>d.s),marker:{{color:'{BLUE}'}},error_x:{{type:'data',symmetric:false,array:R.map(d=>d.h-d.r),arrayminus:R.map(d=>d.r-d.l),color:'{MUTED}',thickness:1}},customdata:R.map(d=>[d.n,d.d,d.l,d.h]),hovertemplate:'%{{y}}<br>Default rate: %{{x:.1%}}<br>95%% interval: %{{customdata[2]:.1%}} to %{{customdata[3]:.1%}}<br>Loans: %{{customdata[0]:,}}<br>Defaults: %{{customdata[1]:,}}<extra></extra>'}}],document.getElementById('chart').layout);}}
document.getElementById('t').onchange=draw;document.getElementById('y').onchange=draw;window.addEventListener('load',draw);</script></body></html>"""
    (out / "segment_explorer.html").write_text(html)


def build_all(settings) -> list[str]:
    gold, figures, plotly_dir = (
        settings.path("gold"),
        settings.path("figures"),
        settings.path("plotly"),
    )
    scored = pd.read_parquet(gold / "scored_cohort.parquet")
    model = read_json(settings.path("results") / "model.json")
    vintage_trend(gold, figures)
    model_comparison(gold, figures, model["aspirational_auc"])
    calibration(gold, figures)
    fico_dti_heatmap(scored, figures)
    correlation(scored, figures)
    findings_forest(gold, figures)
    capture_curve(scored, figures)
    simulated_monitoring(gold, figures)
    plotly_explorer(scored, plotly_dir)
    return sorted(p.name for p in figures.glob("*.png")) + ["segment_explorer.html"]
