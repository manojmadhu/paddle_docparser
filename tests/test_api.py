from fastapi.testclient import TestClient

from docparse.api import create_app


def test_parse_endpoint(parser, native_pdf):
    with TestClient(create_app(parser, warmup=False)) as client:
        r = client.post("/v1/parse", files={"file": ("invoice.pdf", native_pdf, "application/pdf")})
        assert r.status_code == 200
        body = r.json()
        assert body["file_type"] == "pdf"
        assert all("lines" not in b for b in body["pages"][0]["blocks"])  # trimmed by default

        md = client.post("/v1/parse?output=markdown", files={"file": ("invoice.pdf", native_pdf)})
        assert "| Item | Qty | Price |" in md.text


def test_rejects_unknown_files(parser):
    with TestClient(create_app(parser, warmup=False)) as client:
        r = client.post("/v1/parse", files={"file": ("notes.docx", b"PK\x03\x04xxxx")})
        assert r.status_code == 415
