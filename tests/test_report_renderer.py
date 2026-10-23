"""Optional renderer checks; execute with the pinned reporting environment."""

import importlib.util
import io
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET


@unittest.skipUnless(importlib.util.find_spec("reportlab"), "Optional ReportLab rendering environment required")
class RendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "tools/render_report.py"
        spec = importlib.util.spec_from_file_location("report_renderer", path)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def test_fractional_nonstandard_coordinates_and_unambiguous_thousands_scale(self):
        from reportlab.graphics import renderSVG
        record = {"kind": "line", "title": "Synthetic fractional grid", "y_label": "USD", "labels": ["-0.75", "0", "0.75", "12.5"],
                  "series": [{"name": "Fictional", "values": [10000, 0, -10000, -20000]}]}
        drawing = self.module.chart_drawing(record, "synthetic_run")
        chart = drawing.contents[0]
        self.assertEqual([x for x, y in chart.data[0]], [-.75, 0.0, .75, 12.5])
        self.assertEqual(chart.xValueAxis.labelTextFormat(.75), "0.75")
        self.assertEqual(chart.yValueAxis.labelTextFormat(10000), "10")
        svg = ET.fromstring(renderSVG.drawToString(drawing))
        self.assertIn("USD (thousands)", [e.text for e in svg.iter() if e.tag.endswith("text")])

    def test_negative_bar_category_labels_remain_below_plot(self):
        record = {"kind": "bar", "title": "Synthetic negative bar", "y_label": "USD", "labels": ["First", "Second"],
                  "series": [{"name": "Fictional", "values": [10000, -20000]}]}
        drawing = self.module.chart_drawing(record, "synthetic_run")
        chart = drawing.contents[0]
        zero_offset = -chart.valueAxis.valueMin / (chart.valueAxis.valueMax - chart.valueAxis.valueMin) * chart.height
        self.assertLess(zero_offset + chart.categoryAxis.labels.dy, 0)
        self.assertEqual(chart.valueAxis.labelTextFormat(-10000), "-10")

    def test_singleton_series_have_visible_svg_and_pdf_marks(self):
        from reportlab.graphics import renderPDF, renderSVG
        from reportlab.pdfgen.canvas import Canvas

        class RecordingCanvas(Canvas):
            def __init__(self, stream):
                super().__init__(stream, invariant=1)
                self.filled_circles = []

            def circle(self, x_cen, y_cen, r, stroke=1, fill=0):
                if fill and r > 0:
                    self.filled_circles.append((x_cen, y_cen, r))
                return super().circle(x_cen, y_cen, r, stroke=stroke, fill=fill)

        cases = [
            ("One-quarter conditional path", ["1"], [[-1200]], 1),
            ("One-element parallel-shift comparison", ["0.75"], [[-1200], [-1300], [-1250]], 3),
            ("Mixed conditional horizons", ["1", "2", "3"], [[-1200], [400, 800, 1200]], 1),
            ("Ordinary multi-quarter paths", ["1", "2"], [[-1200, -1600], [400, 800]], 0),
        ]
        for title, labels, values, expected_marks in cases:
            with self.subTest(title=title):
                record = {"kind": "line", "title": title, "y_label": "USD", "labels": labels,
                          "series": [{"name": f"Series {i}", "values": v} for i, v in enumerate(values)]}
                drawing = self.module.chart_drawing(record, "synthetic_run")
                chart = drawing.contents[0]
                expected_data = [[(float(x), y) for x, y in zip(labels, v)] for v in values]
                self.assertEqual(chart.data, expected_data)
                svg = ET.fromstring(renderSVG.drawToString(drawing))
                circles = [e for e in svg.iter() if e.tag.endswith("circle")]
                self.assertEqual(len(circles), expected_marks)
                for circle in circles:
                    self.assertGreater(float(circle.attrib["r"]), 0)
                    self.assertNotIn("fill: none", circle.attrib.get("style", ""))
                stream = io.BytesIO()
                canvas = RecordingCanvas(stream)
                renderPDF.draw(drawing, canvas, 0, 0)
                canvas.save()
                self.assertEqual(len(canvas.filled_circles), expected_marks)
                self.assertTrue(stream.getvalue().startswith(b"%PDF-"))
                self.assertEqual(chart.data, expected_data)
