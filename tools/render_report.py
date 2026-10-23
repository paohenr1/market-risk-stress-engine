"""Optional ReportLab PDF/SVG renderer. Numerical data never goes to stdout."""

import argparse
import hashlib
import html
import importlib.metadata
import json
from pathlib import Path
import platform
import sys

from reportlab.graphics import renderSVG
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.graphics.shapes import Drawing, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Preformatted


PALETTE = [colors.HexColor(v) for v in ("#214f73", "#b35a27", "#47816c")]
WIDTH, HEIGHT = 612, 792


def digest(body):
    return hashlib.sha256(body).hexdigest()


def chart_drawing(record, run_id):
    drawing = Drawing(504, 214)
    count = max((len(s["values"]) for s in record["series"]), default=0)
    if not count:
        raise ValueError("Empty chart")
    if record["kind"] == "bar":
        chart = VerticalBarChart()
        chart.data = [s["values"] for s in record["series"]]
        chart.categoryAxis.categoryNames = [s.replace(" ", "\n", 1) for s in record["labels"]]
        chart.categoryAxis.labels.fontSize = 7
        chart.categoryAxis.labels.fontName = "Helvetica"
        chart.categoryAxis.labels.boxAnchor = "n"
        chart.bars[0].fillColor = PALETTE[0]
    elif record["kind"] == "line":
        chart = LinePlot()
        chart.data = [[(float(x), y) for x, y in zip(record["labels"], s["values"])] for s in record["series"]]
        chart.xValueAxis.labels.fontSize = 7
        chart.xValueAxis.labels.fontName = "Helvetica"
        # Let the value axis choose spaced ticks from the real coordinates.
        # Fractional shifts must not be truncated or replaced by a default grid.
        chart.xValueAxis.labelTextFormat = lambda v: f"{v:g}"
        for i in range(len(record["series"])):
            chart.lines[i].strokeColor = PALETTE[i % len(PALETTE)]
            chart.lines[i].strokeWidth = 1.5
            if len(chart.data[i]) == 1:
                chart.lines[i].symbol = makeMarker("FilledCircle", size=6, fillColor=chart.lines[i].strokeColor)
        chart.joinedLines = 1
    else:
        raise ValueError("Unknown chart type")
    chart.x, chart.y, chart.width, chart.height = 55, 53, 420, 114
    axis = chart.valueAxis if record["kind"] == "bar" else chart.yValueAxis
    axis.labels.fontSize = 7
    axis.labels.fontName = "Helvetica"
    axis.labelTextFormat = lambda v: f"{v / 1000:g}"
    values = [v for s in record["series"] for v in s["values"]]
    low, high = min(0, min(values)), max(0, max(values))
    margin = max((high - low) * .12, 1)
    axis.valueMin, axis.valueMax = low - margin, high + margin
    if record["kind"] == "bar":
        # Keep labels below the plot, even when the zero axis crosses a negative bar.
        chart.categoryAxis.labels.dy = -(-axis.valueMin / (axis.valueMax - axis.valueMin) * chart.height) - 6
    axis.visibleGrid = True
    axis.gridStrokeColor = colors.HexColor("#dce3e8")
    axis.strokeColor = colors.HexColor("#697985")
    drawing.add(chart)
    drawing.add(String(3, 190, record["title"], fontName="Helvetica-Bold", fontSize=9, fillColor=PALETTE[0]))
    drawing.add(String(3, 176, record["y_label"] + " (thousands)", fontSize=7, fillColor=colors.HexColor("#445563")))
    for i, series in enumerate(record["series"]):
        drawing.add(String(55 + i * 145, 21, series["name"], fontSize=7, fillColor=PALETTE[i % len(PALETTE)]))
    drawing.add(String(3, 3, "Saved run: " + run_id, fontSize=6.5, fillColor=colors.HexColor("#52616d")))
    return drawing


def render(report: Path, output: Path):
    manifest = json.loads((report / "report_manifest.json").read_bytes())
    expected = {"document.json", "report.md", "endpoints.csv", "paths.csv", "evaluation.csv", "selected_holdout.csv",
                "approximations.csv", "curve_overlays.csv", "macro_conditions.csv"}
    if manifest["schema_version"] != 1 or set(manifest["files"]) != expected:
        raise ValueError("Report schema mismatch")
    if any(digest((report / name).read_bytes()) != sha for name, sha in manifest["files"].items()):
        raise ValueError("Report content hash mismatch")
    document = json.loads((report / "document.json").read_bytes())
    if document["run_id"] != manifest["run_id"] or len(document["pages"]) != manifest["planned_pages"]:
        raise ValueError("Report identity mismatch")
    output.mkdir(parents=True, exist_ok=False)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("ReportBody", fontName="Helvetica", fontSize=10.2, leading=14.5, textColor=colors.HexColor("#263746"), spaceAfter=9, splitLongWords=True)
    title = ParagraphStyle("ReportTitle", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=18, leading=21, textColor=PALETTE[0], spaceAfter=16)
    cell = ParagraphStyle("ReportCell", parent=body, fontSize=8, leading=10.5, spaceAfter=0, wordWrap="CJK")
    caption = ParagraphStyle("ReportCaption", parent=body, fontSize=8, leading=10.5, textColor=colors.HexColor("#52616d"), spaceAfter=7)
    code = ParagraphStyle("ReportCode", fontName="Courier", fontSize=7.2, leading=11, alignment=TA_LEFT)
    drawings = {name: chart_drawing(record, document["run_id"]) for name, record in document["charts"].items()}
    for name, drawing in drawings.items():
        renderSVG.drawToFile(drawing, str(output / f"{name}.svg"))
    story = []
    chart_pages = []
    for i, p in enumerate(document["pages"], 1):
        if i > 1:
            story.append(PageBreak())
        story.append(Paragraph(html.escape(p["title"]), title))
        if i == 1:
            story.append(Paragraph(html.escape(document["title"]), styles["Heading2"]))
            story.append(Paragraph(html.escape(document["subtitle"]), caption))
        for text in p["paragraphs"]:
            story.append(Paragraph(html.escape(text), body))
        for t in p["tables"]:
            if any(len(row) != len(t["headers"]) for row in t["rows"]):
                raise ValueError("Table shape mismatch")
            values = [[Paragraph(html.escape(str(v)), cell) for v in row] for row in [t["headers"], *t["rows"]]]
            columns = len(t["headers"])
            widths = [504 / columns] * columns
            if columns == 2:
                widths = [155, 349]
            elif columns == 7:
                widths = [91, 26, 29, 90, 90, 90, 88]
                widths = [v * 504 / sum(widths) for v in widths]
            grid = Table(values, colWidths=widths, hAlign="LEFT", repeatRows=1)
            grid.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6eef4")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LINEBELOW", (0, 0), (-1, 0), .7, colors.HexColor("#b6c7d3")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f7f9")])]))
            story += [grid, Spacer(1, 7), Paragraph(html.escape(t["caption"]) + " Saved run: " + document["run_id"], caption)]
        if p["chart"]:
            story += [Spacer(1, 4), drawings[p["chart"]]]
            chart_pages.append(i)
        if p["code"]:
            story += [Spacer(1, 6), Preformatted(p["code"], code)]
    page_count = 0

    def decorate(canvas, doc):
        nonlocal page_count
        page_count += 1
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#52616d"))
        canvas.drawString(54, HEIGHT - 30, "MARKET RISK | TECHNICAL REPORT | LOCAL RESEARCH USE")
        canvas.line(54, 44, WIDTH - 54, 44)
        canvas.drawString(54, 30, "Saved run: " + document["run_id"])
        canvas.drawRightString(WIDTH - 54, 30, f"{doc.page}")

    pdf = output / "technical_report.pdf"
    doc = SimpleDocTemplate(str(pdf), pagesize=(WIDTH, HEIGHT), leftMargin=54, rightMargin=54,
                            topMargin=52, bottomMargin=55, title=document["title"], author="Market risk research pipeline", invariant=1)
    doc.build(story, onFirstPage=decorate, onLaterPages=decorate)
    if page_count != len(document["pages"]):
        raise ValueError("Report overflow: actual page count differs from the planned layout")
    record = {"schema_version": 1, "run_id": document["run_id"], "pages": page_count,
              "report_manifest_sha256": digest((report / "report_manifest.json").read_bytes()),
              "python_version": platform.python_version(),
              "dependencies": {p: importlib.metadata.version(p) for p in ("reportlab", "pillow", "charset-normalizer")},
              "renderer_sha256": digest(Path(__file__).read_bytes().replace(b"\r\n", b"\n")),
              "chart_pages": chart_pages,
              "files": {p.name: digest(p.read_bytes()) for p in sorted(output.iterdir()) if p.is_file()},
              "visual_review": "pending"}
    (output / "render_manifest.json").write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"status": "rendered", "run_id": document["run_id"], "pages": page_count, "charts": len(drawings),
            "output": str(output.resolve()), "pdf_sha256": record["files"]["technical_report.pdf"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(render(args.report, args.output or args.report / "rendered")))
    except (ValueError, KeyError, TypeError, OSError):
        print(json.dumps({"status": "invalid", "error": "Report integrity, schema, output path or page layout failed"}), file=sys.stderr)
        sys.exit(2)
