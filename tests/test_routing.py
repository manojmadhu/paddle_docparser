"""Routing behaviour: each page should take the cheapest route that handles it."""
import io

import pytest
from PIL import Image

from docparse.loaders import UnsupportedFileError, detect_file_type, parse_page_range


def test_detects_file_types(png_bytes, native_pdf):
    assert detect_file_type(png_bytes) == "png"
    assert detect_file_type(native_pdf) == "pdf"
    with pytest.raises(UnsupportedFileError):
        detect_file_type(b"PK\x03\x04 not a document")


def test_page_range():
    assert parse_page_range("1-3,5", 10) == [0, 1, 2, 4]
    assert parse_page_range(None, 3) == [0, 1, 2]
    assert parse_page_range("9-12", 10) == [8, 9]


def test_native_pdf_uses_text_layer_without_ocr(parser, fake, native_pdf):
    result = parser.parse(native_pdf, "invoice.pdf")
    page = result["pages"][0]

    assert page["source"] == "text_layer"
    assert "ocr" not in fake.calls                 # no OCR on a digital page
    texts = " ".join(b.get("text") or "" for b in page["blocks"])
    assert "INVOICE 2026-0042" in texts
    table = next(b for b in page["blocks"] if b["type"] == "table")
    assert table["rows"][0] == ["Item", "Qty", "Price"]
    assert ["Ink", "1", "25.00"] in table["rows"]
    assert "Paper" not in texts                     # table text not duplicated as paragraphs


def test_scanned_pdf_goes_to_ocr(parser, fake, scanned_pdf):
    page = parser.parse(scanned_pdf)["pages"][0]
    assert page["source"] == "ocr"
    assert "ocr" in fake.calls
    assert page["blocks"][0]["text"] == "TOTAL 42.00"


def test_plain_text_image_skips_structure_model(parser, fake, png_bytes):
    page = parser.parse(png_bytes)["pages"][0]
    assert page["routes"] == ["ocr"]
    assert not any(c.startswith("structure") for c in fake.calls)
    assert not any(c.startswith("recognize") for c in fake.calls)  # confident line: no re-read


def test_table_on_image_runs_only_table_module(parser, fake, png_bytes):
    fake.layout = [{"label": "table", "score": 0.9, "bbox": [5, 5, 300, 200]}]
    page = parser.parse(png_bytes)["pages"][0]

    assert "structure:tables" in fake.calls        # charts/formulas not switched on
    table = page["blocks"][0]
    assert table["type"] == "table"
    assert table["rows"] == [["Item", "Price"], ["Tea", "3"]]


def test_chart_is_converted_to_rows(parser, fake, png_bytes):
    fake.layout = [{"label": "chart", "score": 0.9, "bbox": [0, 0, 400, 300]}]
    fake.structure = {"parsing_res_list": [{"block_label": "chart", "block_bbox": [0, 0, 400, 300],
                                            "block_content": "Month | Sales\nJan | 10\nFeb | 12"}]}
    page = parser.parse(png_bytes)["pages"][0]
    assert "structure:charts" in fake.calls
    assert page["blocks"][0]["rows"] == [["Month", "Sales"], ["Jan", "10"], ["Feb", "12"]]


def test_low_confidence_lines_are_reread(parser, fake, png_bytes):
    fake.lines = [
        {"text": "Dear Sam", "confidence": 0.55, "bbox": [10, 10, 200, 40]},
        {"text": "see you", "confidence": 0.60, "bbox": [10, 50, 200, 80]},
        {"text": "Printed footer", "confidence": 0.98, "bbox": [10, 300, 300, 330]},
    ]
    page = parser.parse(png_bytes)["pages"][0]

    assert "recognize:2" in fake.calls             # only the 2 weak lines were cropped
    assert page["handwriting_likely"] is True
    assert "handwriting" in page["routes"]
    lines = [l for b in page["blocks"] for l in b["lines"]]
    assert sum(1 for l in lines if l.get("escalated")) == 2


def test_rotated_page_is_turned_upright(parser, fake):
    buf = io.BytesIO()
    Image.new("RGB", (600, 300), "white").save(buf, "PNG")
    fake.angle = 90
    page = parser.parse(buf.getvalue())["pages"][0]
    assert page["orientation"] == 90
    assert (page["width"], page["height"]) == (300, 600)  # reported in the upright frame


def test_large_images_are_downscaled_and_boxes_mapped_back(fake):
    from docparse import DocumentParser, Settings
    parser = DocumentParser(Settings(max_image_side=1000), models=fake)
    buf = io.BytesIO()
    Image.new("RGB", (4000, 2000), "white").save(buf, "PNG")
    page = parser.parse(buf.getvalue())["pages"][0]
    assert (page["width"], page["height"]) == (4000, 2000)
    assert page["blocks"][0]["lines"][0]["bbox"] == [40.0, 40.0, 800.0, 120.0]  # x4 scale
