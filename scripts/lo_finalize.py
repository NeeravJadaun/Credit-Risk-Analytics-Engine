"""A LibreOffice macro: recalculate, add a real PivotTable, save as xlsx. Run by excel_report.py."""

import json
import os

import uno
from com.sun.star.beans import PropertyValue
from com.sun.star.sheet.DataPilotFieldOrientation import COLUMN, DATA, ROW
from com.sun.star.sheet.GeneralFunction import SUM
from com.sun.star.table import CellAddress, CellRangeAddress


def prop(name, value):
    item = PropertyValue()
    item.Name, item.Value = name, value
    return item


def run():
    """Macro entry point. Arguments arrive by environment, because macros take none on the command line."""
    desktop = XSCRIPTCONTEXT.getDesktop()  # noqa: F821
    path, render_pdf, result_file = os.environ["LO_XLSX"], os.environ.get("LO_PDF", ""), os.environ["LO_RESULT"]
    try:
        outcome = finalize(desktop, path, render_pdf)
    except Exception as exc:  # reported to the caller, which then fails loudly
        outcome = {"pivot_created": False, "error": repr(exc)}
    with open(result_file, "w") as handle:
        json.dump(outcome, handle)
    desktop.terminate()


def finalize(desktop, path, render_pdf):
    url = uno.systemPathToFileUrl(path)
    document = desktop.loadComponentFromURL(url, "_blank", 0, (prop("Hidden", True),))
    document.calculateAll()
    sheets = document.Sheets
    raw = sheets.getByName("Raw Export")
    cursor = raw.createCursor()
    cursor.gotoEndOfUsedArea(False)
    last_row, last_col = cursor.RangeAddress.EndRow, cursor.RangeAddress.EndColumn
    position = 6  # after Definitions
    sheets.insertNewByName("Pivot Grade by Year", position)
    pivot_sheet = sheets.getByName("Pivot Grade by Year")
    pivot_sheet.getCellByPosition(0, 0).String = "Loans and defaults by grade and issue year (PivotTable over Raw Export)"
    tables = pivot_sheet.getDataPilotTables()
    descriptor = tables.createDataPilotDescriptor()
    source = CellRangeAddress()
    source.Sheet = [s for s in range(sheets.Count) if sheets.getByIndex(s).Name == "Raw Export"][0]
    source.StartColumn, source.StartRow, source.EndColumn, source.EndRow = 0, 0, last_col, last_row
    descriptor.setSourceRange(source)
    fields = descriptor.getDataPilotFields()
    names = {fields.getByIndex(i).Name: fields.getByIndex(i) for i in range(fields.Count)}
    names["Grade"].Orientation = ROW
    names["Issue Year"].Orientation = COLUMN
    for measure in ("Loans", "Defaults"):
        names[measure].Orientation = DATA
        names[measure].Function = SUM
    anchor = CellAddress()
    anchor.Sheet = position
    anchor.Column, anchor.Row = 0, 2
    tables.insertNewByName("GradeByYear", anchor, descriptor)
    document.calculateAll()
    document.storeToURL(url, (prop("FilterName", "Calc MS Excel 2007 XML"),))
    created = tables.getByName("GradeByYear")
    output = created.getOutputRange()
    if render_pdf:
        for name in ("Raw Export", "Score Buckets"):
            sheets.getByName(name).IsVisible = False
        document.storeToURL(uno.systemPathToFileUrl(render_pdf), (prop("FilterName", "calc_pdf_Export"),))
    document.close(True)
    return {"pivot_created": True, "pivot_output_rows": output.EndRow - output.StartRow + 1, "pivot_output_columns": output.EndColumn - output.StartColumn + 1}


g_exportedScripts = (run,)
