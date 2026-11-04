"""Excel workbook with live formulas over a bounded aggregate of verified data.

openpyxl writes the formulas, and LibreOffice then recalculates them and adds the PivotTable
(scripts/lo_finalize.py). verify_workbook compares the calculated cells with values Python computed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import numpy as np
import openpyxl
import pandas as pd
from openpyxl.chart import LineChart, Reference
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule, FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo

from .audit import read_json, write_json
from .monitoring import SIMULATION_BANNER

FONT = "Arial"
HEADER_FILL = PatternFill("solid", fgColor="1F3A5F")
INPUT_FILL = PatternFill("solid", fgColor="FFF8DC")
BANNER_FILL = PatternFill("solid", fgColor="EEF3F9")
GREEN, RED, AMBER = (
    PatternFill("solid", bgColor="D5EFDF"),
    PatternFill("solid", bgColor="F8D7DA"),
    PatternFill("solid", bgColor="FFF0C2"),
)
REFERENCE_TOLERANCE = 1e-9
PROVENANCE = "Source: public LendingClub accepted loans 2007-2018 (Kaggle mirror). Independent educational analysis. Not a bank system and not bank policy."


def _title(ws, text: str, note: str = PROVENANCE) -> None:
    ws["A1"] = text
    ws["A1"].font = Font(name=FONT, size=15, bold=True, color="1F3A5F")
    ws["A2"] = note
    ws["A2"].font = Font(name=FONT, size=9, italic=True, color="52514E")
    ws.sheet_view.showGridLines = False


def _header(ws, row: int, labels: list[str], column: int = 1) -> None:
    for offset, label in enumerate(labels):
        cell = ws.cell(row=row, column=column + offset, value=label)
        cell.font = Font(name=FONT, size=10, bold=True, color="FFFFFF")
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")


def _section(ws, row: int, text: str) -> None:
    ws.cell(row=row, column=1, value=text).font = Font(
        name=FONT, size=11, bold=True, color="1F3A5F"
    )


def _finish(ws, widths: dict[str, float]) -> None:
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is not None and cell.font.name != FONT:
                cell.font = Font(name=FONT, size=10, bold=cell.font.bold, color=cell.font.color)
    for column, width in widths.items():
        ws.column_dimensions[column].width = width


def _raw_export(wb, scored: pd.DataFrame) -> dict[str, str]:
    ws = wb.create_sheet("Raw Export")
    table = (
        scored.groupby(
            ["issue_year", "term_months", "split", "grade", "fico_band", "dti_band"], observed=True
        )
        .agg(
            loans=("default_flag", "size"),
            defaults=("default_flag", "sum"),
            loan_amount=("loan_amnt", "sum"),
        )
        .reset_index()
    )
    table.columns = [
        "Issue Year",
        "Term Months",
        "Split",
        "Grade",
        "FICO Band",
        "DTI Band",
        "Loans",
        "Defaults",
        "Loan Amount",
    ]
    ws.append(list(table.columns))
    for row in table.itertuples(index=False):
        ws.append([v.item() if hasattr(v, "item") else v for v in row])
    last = len(table) + 1
    data_table = Table(displayName="RawData", ref=f"A1:I{last}")
    data_table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    ws.add_table(data_table)
    ws.freeze_panes = "A2"
    _finish(ws, {c: 14 for c in "ABCDEFGHI"})
    return {
        name: f"'Raw Export'!${col}$2:${col}${last}"
        for name, col in zip(
            ["year", "term", "split", "grade", "fico", "dti", "loans", "defaults", "amount"],
            "ABCDEFGHI",
            strict=True,
        )
    }


def _score_buckets(wb, scored: pd.DataFrame) -> dict[str, str]:
    ws = wb.create_sheet("Score Buckets")
    test = scored[scored["split"] == "test"]
    bucket = (np.floor(test["score"] * 200 + 1e-9) / 200).round(3)
    table = test.groupby(bucket)["default_flag"].agg(["size", "sum"]).reset_index()
    table.columns = ["Score Bucket Lower", "Loans", "Defaults"]
    ws.append(list(table.columns))
    for row in table.itertuples(index=False):
        ws.append([float(row[0]), int(row[1]), int(row[2])])
    last = len(table) + 1
    _finish(ws, {"A": 18, "B": 12, "C": 12})
    return {
        "bucket": f"'Score Buckets'!$A$2:$A${last}",
        "loans": f"'Score Buckets'!$B$2:$B${last}",
        "defaults": f"'Score Buckets'!$C$2:$C${last}",
    }


def _executive(
    wb, r: dict[str, str], refs: dict[str, float], kpis: pd.DataFrame, meta: dict[str, Any]
) -> dict[str, str]:
    ws = wb["Executive Summary"]
    _title(ws, "Credit Risk Portfolio Report")
    ws["A3"] = (
        f"Report period: {meta['period']}. This is the run period, not a data month. Loans were issued 2007-2015 with outcomes observed by early 2019."
    )
    ws["A4"] = (
        f"Source SHA-256 {meta['sha'][:16]}... | Input rows {meta['input_rows']:,} | Generated by the reporting pipeline."
    )
    for row in (3, 4):
        ws.cell(row=row, column=1).font = Font(name=FONT, size=9, color="52514E")
    _section(
        ws,
        6,
        "Headline figures: live formulas over 'Raw Export', checked against the Python pipeline",
    )
    _header(ws, 7, ["Measure", "Workbook formula", "Python reference", "Check"])
    d, loans, y, t, g = r["defaults"], r["loans"], r["year"], r["term"], r["grade"]

    def share_d_g(year):
        high = "+".join(f'SUMIFS({loans},{y},{year},{t},36,{g},"{x}")' for x in "DEFG")
        return f"=({high})/SUMIFS({loans},{y},{year},{t},36)"

    items = [
        ("Matured loans", f"=SUM({loans})", refs["cohort_loans"], "#,##0"),
        ("Defaults", f"=SUM({d})", refs["cohort_defaults"], "#,##0"),
        ("Observed default rate", "=B9/B8", refs["cohort_rate"], "0.00%"),
        (
            "Default rate, 2014 vintage, 36-month",
            f"=SUMIFS({d},{y},2014,{t},36)/SUMIFS({loans},{y},2014,{t},36)",
            refs["rate_2014"],
            "0.00%",
        ),
        (
            "Default rate, 2015 vintage, 36-month",
            f"=SUMIFS({d},{y},2015,{t},36)/SUMIFS({loans},{y},2015,{t},36)",
            refs["rate_2015"],
            "0.00%",
        ),
        (
            "Change, 2015 vs 2014 (points)",
            "=B12-B11",
            refs["rate_2015"] - refs["rate_2014"],
            "0.00%",
        ),
        ("Grade D-G share of 2015 volume", share_d_g(2015), refs["share_dg_2015"], "0.00%"),
        ("Grade D-G share of 2014 volume", share_d_g(2014), refs["share_dg_2014"], "0.00%"),
    ]
    for offset, (label, formula, reference, fmt) in enumerate(items):
        row = 8 + offset
        ws.cell(row=row, column=1, value=label)
        ws.cell(row=row, column=2, value=formula).number_format = fmt
        ws.cell(row=row, column=3, value=reference).number_format = fmt
        ws.cell(row=row, column=4, value=f'=IF(ABS(B{row}-C{row})<0.000000001,"MATCH","CHECK")')
    ws.conditional_formatting.add(
        "D8:D15", CellIsRule(operator="equal", formula=['"MATCH"'], fill=GREEN)
    )
    ws.conditional_formatting.add(
        "D8:D15", CellIsRule(operator="equal", formula=['"CHECK"'], fill=RED)
    )

    _section(
        ws, 17, "Reporting indicators (pipeline values, looked up from 'Definitions' with XLOOKUP)"
    )
    _header(ws, 18, ["ID", "Indicator", "Value", "Basis"])
    for offset, row_data in enumerate(kpis.itertuples(index=False)):
        row = 19 + offset
        ws.cell(row=row, column=1, value=row_data.kpi_id)
        ws.cell(
            row=row,
            column=2,
            value=f'=_xlfn.XLOOKUP(A{row},Definitions!$A$5:$A$16,Definitions!$B$5:$B$16,"not found")',
        )
        cell = ws.cell(
            row=row,
            column=3,
            value=f'=_xlfn.XLOOKUP(A{row},Definitions!$A$5:$A$16,Definitions!$C$5:$C$16,"not found")',
        )
        cell.number_format = (
            "0.00"
            if row_data.kpi_id == "K06"
            else ("0.0000" if row_data.kpi_id in ("K07", "K09") else "0.00%")
        )
        ws.cell(
            row=row,
            column=4,
            value=f'=_xlfn.XLOOKUP(A{row},Definitions!$A$5:$A$16,Definitions!$E$5:$E$16,"not found")',
        )
    row = 32
    _section(ws, row, "Assumptions and limits")
    notes = [
        "Default = Charged Off or Default status. Only matured vintages are used, to avoid censoring bias from unresolved recent loans.",
        "Approved loans only: declined applicants have no outcome, so nothing here measures what a policy change would do.",
        "Model scores are estimates from public historical data, not validated PD estimates. 'Strategy What-If' is retrospective and hypothetical.",
        f"Any simulated evidence in this workbook is labelled: '{SIMULATION_BANNER}'",
    ]
    for offset, note in enumerate(notes):
        ws.cell(row=row + 1 + offset, column=1, value=note).font = Font(
            name=FONT, size=9, color="52514E"
        )
    _finish(ws, {"A": 40, "B": 44, "C": 18, "D": 26})
    return {
        "B8": refs["cohort_loans"],
        "B9": refs["cohort_defaults"],
        "B10": refs["cohort_rate"],
        "B11": refs["rate_2014"],
        "B12": refs["rate_2015"],
        "B14": refs["share_dg_2015"],
        "B15": refs["share_dg_2014"],
    }


def _segments(wb, r: dict[str, str], scored: pd.DataFrame) -> dict[str, Any]:
    ws = wb.create_sheet("Portfolio Segments")
    _title(ws, "Portfolio Segments")
    ws["A3"] = (
        "A formula-driven summary (COUNTIFS and SUMIFS over 'Raw Export'). See 'Pivot Grade by Year' for the PivotTable."
    )
    ws["A3"].font = Font(name=FONT, size=9, color="52514E")
    checks: dict[str, float] = {}
    row = 5
    ranges = {}
    for title, key, column in [
        ("By grade", "grade", "grade"),
        ("By FICO band", "fico", "fico_band"),
        ("By DTI band", "dti", "dti_band"),
    ]:
        _section(ws, row, title)
        _header(
            ws,
            row + 1,
            [
                "Segment",
                "Cube Rows",
                "Loans",
                "Defaults",
                "Default Rate",
                "Share of Loans",
                "Share of Defaults",
                "Rate vs Portfolio",
                "Small Sample",
            ],
        )
        start = row + 2
        for value in sorted(scored[column].dropna().unique()):
            row_index = start + len(ranges.get(key, []))
            ranges.setdefault(key, []).append(value)
            ws.cell(row=row_index, column=1, value=value)
            ws.cell(row=row_index, column=2, value=f"=COUNTIFS({r[key]},A{row_index})")
            ws.cell(
                row=row_index, column=3, value=f"=SUMIFS({r['loans']},{r[key]},A{row_index})"
            ).number_format = "#,##0"
            ws.cell(
                row=row_index, column=4, value=f"=SUMIFS({r['defaults']},{r[key]},A{row_index})"
            ).number_format = "#,##0"
            ws.cell(
                row=row_index, column=5, value=f"=IFERROR(D{row_index}/C{row_index},0)"
            ).number_format = "0.00%"
            ws.cell(
                row=row_index, column=6, value=f"=C{row_index}/SUM({r['loans']})"
            ).number_format = "0.00%"
            ws.cell(
                row=row_index, column=7, value=f"=D{row_index}/SUM({r['defaults']})"
            ).number_format = "0.00%"
            ws.cell(
                row=row_index,
                column=8,
                value=f"=E{row_index}/(SUM({r['defaults']})/SUM({r['loans']}))",
            ).number_format = '0.00"x"'
            ws.cell(row=row_index, column=9, value=f'=IF(C{row_index}<500,"Under 500","OK")')
            part = scored[scored[column] == value]
            checks[f"E{row_index}"] = float(part["default_flag"].mean())
        end = start + len(ranges[key]) - 1
        ws.conditional_formatting.add(
            f"E{start}:E{end}",
            ColorScaleRule(
                start_type="min", start_color="FFFFFF", end_type="max", end_color="7FB2E5"
            ),
        )
        ws.conditional_formatting.add(
            f"I{start}:I{end}", CellIsRule(operator="equal", formula=['"Under 500"'], fill=AMBER)
        )
        row = end + 2
    _finish(ws, {"A": 22, **{c: 13 for c in "BCDEFGHI"}})
    return checks


def _vintages(wb, r: dict[str, str], scored: pd.DataFrame) -> None:
    ws = wb.create_sheet("Vintage Trends")
    _title(ws, "Vintage Trends, 36-Month Matured Loans")
    _header(
        ws,
        4,
        [
            "Issue Year",
            "Loans",
            "Defaults",
            "Default Rate",
            "Change From Prior",
            "Mean Score Not Shown",
        ],
    )
    ws.cell(row=4, column=6).value = "Sample Note"
    years = sorted(scored.loc[scored["term_months"] == 36, "issue_year"].unique())
    for offset, year in enumerate(years):
        row = 5 + offset
        ws.cell(row=row, column=1, value=int(year))
        ws.cell(
            row=row, column=2, value=f"=SUMIFS({r['loans']},{r['year']},A{row},{r['term']},36)"
        ).number_format = "#,##0"
        ws.cell(
            row=row, column=3, value=f"=SUMIFS({r['defaults']},{r['year']},A{row},{r['term']},36)"
        ).number_format = "#,##0"
        ws.cell(row=row, column=4, value=f"=C{row}/B{row}").number_format = "0.00%"
        if offset:
            ws.cell(row=row, column=5, value=f"=D{row}-D{row - 1}").number_format = "+0.00%;-0.00%"
        ws.cell(row=row, column=6, value=f'=IF(B{row}<500,"Small sample","")')
    last = 4 + len(years)
    chart = LineChart()
    chart.title = "Observed default rate by issue year"
    chart.y_axis.title, chart.x_axis.title = "Default rate", "Issue year"
    chart.y_axis.number_format = "0%"
    chart.add_data(Reference(ws, min_col=4, min_row=4, max_row=last), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=5, max_row=last))
    chart.y_axis.delete, chart.x_axis.delete = False, False
    chart.legend = None
    chart.height, chart.width = 8, 16
    ws.add_chart(chart, "H4")
    _finish(ws, {c: 14 for c in "ABCDEF"})


def _strategy(wb, s: dict[str, str]) -> None:
    ws = wb.create_sheet("Strategy What-If")
    _title(
        ws,
        "Strategy What-If (Hypothetical)",
        "Illustrative thresholds and unit costs on real 2015 outcomes. Retrospective, not causal, not bank policy. Change the shaded cells.",
    )
    labels = [
        ("Score threshold (multiples of 0.005)", 0.20, "0.000"),
        ("Maximum referred share", 0.10, "0.0%"),
        ("Cost of a missed default (units)", 5, "0"),
        ("Cost of a needless review (units)", 1, "0"),
    ]
    for offset, (label, value, fmt) in enumerate(labels):
        ws.cell(row=4 + offset, column=1, value=label)
        cell = ws.cell(row=4 + offset, column=2, value=value)
        cell.fill, cell.number_format, cell.font = (
            INPUT_FILL,
            fmt,
            Font(name=FONT, size=10, color="0000FF"),
        )
    validation = DataValidation(
        type="decimal",
        operator="between",
        formula1="0",
        formula2="1",
        showErrorMessage=True,
        error="Enter a value from 0 to 1.",
    )
    ws.add_data_validation(validation)
    validation.add("B4:B5")
    cost_check = DataValidation(
        type="decimal",
        operator="greaterThanOrEqual",
        formula1="0",
        showErrorMessage=True,
        error="Costs cannot be negative.",
    )
    ws.add_data_validation(cost_check)
    cost_check.add("B6:B7")
    _section(ws, 9, "Result for the threshold above")
    outputs = [
        ("Loans in 2015 vintage", f"=SUM({s['loans']})", "#,##0"),
        ("Defaults in 2015 vintage", f"=SUM({s['defaults']})", "#,##0"),
        ("Loans referred", f'=SUMIFS({s["loans"]},{s["bucket"]},">="&B4)', "#,##0"),
        ("Referred share", "=B12/B10", "0.00%"),
        ("Defaults captured", f'=SUMIFS({s["defaults"]},{s["bucket"]},">="&B4)', "#,##0"),
        ("Default capture", "=B14/B11", "0.00%"),
        ("Default rate among referred", "=IFERROR(B14/B12,0)", "0.00%"),
        ("Default rate among not referred", "=IFERROR((B11-B14)/(B10-B12),0)", "0.00%"),
        ("Hypothetical cost (units)", "=(B11-B14)*B6+(B12-B14)*B7", "#,##0"),
        ("Within capacity?", '=IF(B13<=B5,"Within capacity","Over capacity")', "General"),
    ]
    for offset, (label, formula, fmt) in enumerate(outputs):
        ws.cell(row=10 + offset, column=1, value=label)
        ws.cell(row=10 + offset, column=2, value=formula).number_format = fmt
    ws.conditional_formatting.add(
        "B19", CellIsRule(operator="equal", formula=['"Within capacity"'], fill=GREEN)
    )
    ws.conditional_formatting.add(
        "B19", CellIsRule(operator="equal", formula=['"Over capacity"'], fill=RED)
    )
    _section(ws, 21, "Sensitivity to the score threshold")
    _header(
        ws,
        22,
        ["Threshold", "Referred Share", "Default Capture", "Rate Among Referred", "Cost (units)"],
    )
    grid = [round(0.05 + 0.025 * i, 3) for i in range(13)]
    for offset, threshold in enumerate(grid):
        row = 23 + offset
        ws.cell(row=row, column=1, value=threshold).number_format = "0.000"
        ws.cell(
            row=row, column=2, value=f'=SUMIFS({s["loans"]},{s["bucket"]},">="&A{row})/$B$10'
        ).number_format = "0.0%"
        ws.cell(
            row=row, column=3, value=f'=SUMIFS({s["defaults"]},{s["bucket"]},">="&A{row})/$B$11'
        ).number_format = "0.0%"
        ws.cell(
            row=row,
            column=4,
            value=f'=IFERROR(SUMIFS({s["defaults"]},{s["bucket"]},">="&A{row})/SUMIFS({s["loans"]},{s["bucket"]},">="&A{row}),0)',
        ).number_format = "0.0%"
        ws.cell(
            row=row, column=5, value=f"=($B$11-C{row}*$B$11)*$B$6+(B{row}*$B$10-C{row}*$B$11)*$B$7"
        ).number_format = "#,##0"
    last = 22 + len(grid)
    ws.conditional_formatting.add(f"B23:B{last}", FormulaRule(formula=["B23>$B$5"], fill=AMBER))
    chart = LineChart()
    chart.title = "Referred share and default capture by threshold"
    chart.y_axis.number_format = "0%"
    chart.y_axis.title, chart.x_axis.title = "Share", "Score threshold"
    chart.add_data(
        Reference(ws, min_col=2, max_col=3, min_row=22, max_row=last), titles_from_data=True
    )
    chart.set_categories(Reference(ws, min_col=1, min_row=23, max_row=last))
    chart.y_axis.delete, chart.x_axis.delete = False, False
    chart.height, chart.width = 9, 17
    ws.add_chart(chart, "G4")
    _finish(ws, {"A": 36, "B": 18, "C": 16, "D": 18, "E": 14})


def _quality(
    wb,
    settings,
    spark: dict[str, Any],
    pandas_result: dict[str, Any],
    warehouse_result: dict[str, Any],
) -> None:
    ws = wb.create_sheet("Data Quality")
    _title(ws, "Data Quality and Reconciliation")
    counts = spark["counts"]
    _section(ws, 4, "Row reconciliation")
    rows = [
        ("Input rows", counts["input_rows"]),
        ("Quarantined rows", counts["quarantined_rows"]),
        ("Accepted rows", spark["silver_rows_read_back_from_hdfs"]),
    ]
    for offset, (label, value) in enumerate(rows):
        ws.cell(row=5 + offset, column=1, value=label)
        ws.cell(row=5 + offset, column=2, value=value).number_format = "#,##0"
    ws["A8"] = "Input = accepted + quarantined"
    ws["B8"] = '=IF(B5=B6+B7,"PASS","FAIL")'
    _section(ws, 10, "Quarantine rules (a row is removed, with its reason recorded)")
    _header(
        ws,
        11,
        ["Rule", "Category", "Rows Affected", "Share of Input", "Spark = Pandas", "Description"],
    )
    row = 12
    for rule in settings.contract["quarantine_rules"]:
        ws.cell(row=row, column=1, value=rule["id"])
        ws.cell(row=row, column=2, value=rule["category"])
        ws.cell(row=row, column=3, value=counts[rule["id"]]).number_format = "#,##0"
        ws.cell(row=row, column=4, value=f"=C{row}/$B$5").number_format = "0.000%"
        ws.cell(
            row=row,
            column=5,
            value="PASS" if counts[rule["id"]] == pandas_result["counts"][rule["id"]] else "FAIL",
        )
        ws.cell(row=row, column=6, value=rule["description"])
        row += 1
    row += 1
    _section(ws, row, "Warning flags (counted, rows kept)")
    _header(
        ws,
        row + 1,
        ["Flag", "Type", "Rows Affected", "Share of Accepted", "Spark = Pandas", "Description"],
    )
    row += 2
    for flag in settings.contract["warning_flags"]:
        ws.cell(row=row, column=1, value=flag["id"])
        ws.cell(row=row, column=2, value="profile")
        ws.cell(row=row, column=3, value=counts[flag["id"]]).number_format = "#,##0"
        ws.cell(row=row, column=4, value=f"=C{row}/$B$7").number_format = "0.000%"
        ws.cell(
            row=row,
            column=5,
            value="PASS" if counts[flag["id"]] == pandas_result["counts"][flag["id"]] else "FAIL",
        )
        ws.cell(row=row, column=6, value=flag["description"])
        row += 1
    row += 1
    _section(ws, row, "Layer controls and warehouse integrity")
    _header(ws, row + 1, ["Control", "Layer", "Status", "", "", "Observed vs Expected"])
    row += 2
    first = row
    for check in pandas_result["layer_checks"]:
        ws.cell(row=row, column=1, value=check["check_name"])
        ws.cell(row=row, column=2, value="pipeline")
        ws.cell(row=row, column=3, value=check["status"])
        ws.cell(row=row, column=6, value=f"{check['observed']} vs {check['expected']}")
        row += 1
    for check in warehouse_result["integrity_checks"]:
        ws.cell(row=row, column=1, value=check["check_name"])
        ws.cell(row=row, column=2, value="warehouse")
        ws.cell(row=row, column=3, value=check["status"])
        ws.cell(row=row, column=6, value=f"{check['violations']} violations")
        row += 1
    for zone in ("B8", f"E12:E{row}", f"C{first}:C{row}"):
        ws.conditional_formatting.add(
            zone, CellIsRule(operator="equal", formula=['"PASS"'], fill=GREEN)
        )
        ws.conditional_formatting.add(
            zone, CellIsRule(operator="equal", formula=['"FAIL"'], fill=RED)
        )
    _finish(ws, {"A": 40, "B": 14, "C": 13, "D": 13, "E": 13, "F": 95})


def _definitions(wb, kpis: pd.DataFrame, settings) -> None:
    ws = wb.create_sheet("Definitions")
    _title(ws, "Definitions and Data Dictionary")
    _section(ws, 3, "Reporting indicators")
    _header(ws, 4, ["ID", "Indicator", "Value", "Population", "Basis", "Note"])
    for offset, row in enumerate(kpis.itertuples(index=False)):
        for column, value in enumerate(
            [row.kpi_id, row.indicator, float(row.value), row.population, row.basis, row.note],
            start=1,
        ):
            ws.cell(row=5 + offset, column=column, value=value)
    ws.cell(row=18, column=1, value="Terms").font = Font(
        name=FONT, size=11, bold=True, color="1F3A5F"
    )
    terms = [
        (
            "Default",
            "loan_status is Charged Off or Default. Fully Paid is the non-default outcome. Open loans have no label.",
        ),
        (
            "Matured cohort",
            "36-month loans issued through 2015 and 60-month loans issued through 2013, all with a final status.",
        ),
        (
            "Relative risk",
            "Default rate of the segment divided by the default rate of the comparison group.",
        ),
        (
            "PSI",
            "Sum over bins of (current share - baseline share) x ln(current share / baseline share). Its cut-offs are a heuristic.",
        ),
        ("Out-of-time", "Scored on loans issued after every training loan, here the 2015 vintage."),
        (
            "Pivot note",
            "'Pivot Grade by Year' is a real PivotTable over 'Raw Export'. Refresh it if that data changes.",
        ),
    ]
    for offset, (term, meaning) in enumerate(terms):
        ws.cell(row=19 + offset, column=1, value=term)
        ws.cell(row=19 + offset, column=2, value=meaning)
    _finish(ws, {"A": 16, "B": 46, "C": 12, "D": 38, "E": 26, "F": 44})


def build_workbook(settings, period: str, path: Path) -> dict[str, Any]:
    gold, results = settings.path("gold"), settings.path("results")
    scored = pd.read_parquet(gold / "scored_cohort.parquet")
    kpis = pd.read_csv(gold / "kpi_indicators.csv")
    spark, pandas_result, warehouse_result = (
        read_json(results / f"{n}.json") for n in ("spark_etl", "pandas_etl", "warehouse")
    )
    y36 = {
        y: scored[(scored["issue_year"] == y) & (scored["term_months"] == 36)] for y in (2014, 2015)
    }
    refs = {
        "cohort_loans": int(len(scored)),
        "cohort_defaults": int(scored["default_flag"].sum()),
        "cohort_rate": float(scored["default_flag"].mean()),
        "rate_2014": float(y36[2014]["default_flag"].mean()),
        "rate_2015": float(y36[2015]["default_flag"].mean()),
        "share_dg_2015": float(y36[2015]["grade"].isin(list("DEFG")).mean()),
        "share_dg_2014": float(y36[2014]["grade"].isin(list("DEFG")).mean()),
    }
    wb = openpyxl.Workbook()
    wb.active.title = "Executive Summary"
    # Data sheets first so their ranges are known, then ordered for the reader.
    raw_refs = _raw_export(wb, scored)
    score_refs = _score_buckets(wb, scored)
    expected = {
        "Executive Summary": _executive(
            wb,
            raw_refs,
            refs,
            kpis,
            {
                "period": period,
                "sha": settings.pipeline["source"]["expected_sha256"],
                "input_rows": spark["counts"]["input_rows"],
            },
        )
    }
    expected["Portfolio Segments"] = _segments(wb, raw_refs, scored)
    _vintages(wb, raw_refs, scored)
    _strategy(wb, score_refs)
    _quality(wb, settings, spark, pandas_result, warehouse_result)
    _definitions(wb, kpis, settings)
    order = [
        "Executive Summary",
        "Portfolio Segments",
        "Vintage Trends",
        "Strategy What-If",
        "Data Quality",
        "Definitions",
        "Raw Export",
        "Score Buckets",
    ]
    wb._sheets = [wb[name] for name in order]
    test = scored[scored["split"] == "test"]
    flagged = test["score"] >= 0.20
    expected["Strategy What-If"] = {
        "B13": float(flagged.mean()),
        "B15": float(test.loc[flagged, "default_flag"].sum() / test["default_flag"].sum()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return expected


def finalize_with_libreoffice(
    settings, path: Path, render_pdf: Path | None = None
) -> dict[str, Any]:
    """Recalculate and add a PivotTable by running scripts/lo_finalize.py as a LibreOffice macro.

    LibreOffice's standalone Python is killed by macOS on launch here, so the code runs inside soffice.
    """
    with tempfile.TemporaryDirectory() as tmp:
        macros = Path(tmp) / "profile" / "user" / "Scripts" / "python"
        macros.mkdir(parents=True)
        shutil.copy(settings.root / "scripts" / "lo_finalize.py", macros / "lo_finalize.py")
        result_file = Path(tmp) / "result.json"
        env = {
            **os.environ,
            "LO_XLSX": str(path),
            "LO_PDF": str(render_pdf or ""),
            "LO_RESULT": str(result_file),
        }
        command = [
            "soffice",
            f"-env:UserInstallation=file://{tmp}/profile",
            "--headless",
            "--norestore",
            "--nologo",
            "vnd.sun.star.script:lo_finalize.py$run?language=Python&location=user",
        ]
        subprocess.run(command, env=env, capture_output=True, text=True, timeout=300)
        if not result_file.exists():
            raise RuntimeError("LibreOffice finished without running the finalize macro.")
        outcome = json.loads(result_file.read_text())
    if not outcome.get("pivot_created"):
        raise RuntimeError(f"LibreOffice finalize failed: {outcome}")
    return outcome


def verify_workbook(path: Path, expected: dict[str, dict[str, float]]) -> dict[str, Any]:
    """Compare calculated cells with Python values, scan for errors, and inspect the file structure."""
    values = openpyxl.load_workbook(path, data_only=True)
    formulas = openpyxl.load_workbook(path)
    rows, errors, formula_count, uncached = [], [], 0, 0
    for ws in formulas.worksheets:
        cached = values[ws.title]
        for line in ws.iter_rows():
            for cell in line:
                if isinstance(cell.value, str) and cell.value.startswith("="):
                    formula_count += 1
                    result = cached[cell.coordinate].value
                    if result is None:
                        uncached += 1
                    if isinstance(result, str) and result.startswith("#"):
                        errors.append(f"{ws.title}!{cell.coordinate}: {result}")
    for sheet, cells in expected.items():
        for coordinate, reference in cells.items():
            actual = values[sheet][coordinate].value
            rows.append(
                {
                    "sheet": sheet,
                    "cell": coordinate,
                    "workbook": actual,
                    "python": reference,
                    "match": actual is not None
                    and abs(float(actual) - float(reference)) <= REFERENCE_TOLERANCE,
                }
            )
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    return {
        "cells_compared": len(rows),
        "cells_matching": sum(r["match"] for r in rows),
        "mismatches": [r for r in rows if not r["match"]],
        "formula_count": formula_count,
        "formula_errors": errors,
        "formulas_without_cached_value": uncached,
        "functions_used": sorted(
            {
                f
                for f in ("SUMIFS", "COUNTIFS", "XLOOKUP", "IFERROR", "SUM")
                if any(
                    f in str(c.value)
                    for ws in formulas.worksheets
                    for line in ws.iter_rows(max_row=60)
                    for c in line
                    if isinstance(c.value, str)
                )
            }
        ),
        "pivot_table_parts": [n for n in names if n.startswith("xl/pivotTables/pivotTable")],
        "chart_parts": [n for n in names if n.startswith("xl/charts/chart")],
        "table_parts": [n for n in names if n.startswith("xl/tables/")],
        "sheets": values.sheetnames,
        "conditional_format_sheets": [
            ws.title for ws in formulas.worksheets if len(ws.conditional_formatting)
        ],
        "data_validation_sheets": [
            ws.title
            for ws in formulas.worksheets
            if ws.data_validations and len(ws.data_validations.dataValidation)
        ],
        "opened_in_excel_desktop": False,
        "verified_engine": "LibreOffice recalculation, read back with openpyxl",
    }


def build_and_verify(
    settings, period: str, out_dir: Path, render_pdf: Path | None = None
) -> dict[str, Any]:
    path = Path(out_dir) / "excel" / "credit_risk_portfolio_report.xlsx"
    expected = build_workbook(settings, period, path)
    finalize = finalize_with_libreoffice(settings, path, render_pdf)
    result = verify_workbook(path, expected)
    result.update(
        {
            "path": str(path.relative_to(settings.root))
            if path.is_relative_to(settings.root)
            else str(path),
            "pivot": finalize,
        }
    )
    write_json(settings.path("results") / "excel_verification.json", result)
    return result
