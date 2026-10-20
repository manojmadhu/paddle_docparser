# docparse: PaddleOCR document parser

Turns PDFs and images into structured JSON (or Markdown): text, headings,
tables, charts, formulas, handwriting. Every page is routed automatically
to the cheapest pipeline that can read it, so simple pages stay fast and
only the hard parts pay for the heavy models.

## How routing works

```
file ──► PDF? ──yes──► page has a real text layer? ──yes──► read text + tables from PDF (no OCR, ~0.1 s)
  │                         │                                 ├─ big embedded image  → OCR that region only
  │                         no (scanned)                      └─ chart / borderless table → structure model on that region only
  │                         ▼
  └──── image ─────► fix orientation ─► layout check (small model)
                                          ├─ table / chart / formula / seal found → PP-StructureV3, only those modules on
                                          └─ plain text → fast OCR → re-read only low-confidence lines with the
                                                                      strong model (handwriting, faded print)
```

Nothing is run "just in case": the chart model never loads into a request
unless a chart is on the page, and handwriting re-reads only the weak lines,
not the whole page.

## Quick start (local, CPU)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

python -m docparse invoice.pdf receipt.jpg --out output/ --markdown
uvicorn docparse.api:app --port 8080
```

The first run downloads the models (a few hundred MB) into `~/.paddlex`.

## Docker

```bash
# CPU (models are baked into the image at build time)
docker compose up --build ocr                      # http://localhost:8080

# NVIDIA GPU (needs driver + NVIDIA Container Toolkit on the host)
docker compose --profile gpu up --build ocr-gpu    # http://localhost:8081
```

The GPU image automatically switches to the larger, more accurate server models.

## API

`POST /v1/parse` with a multipart `file` field.

| Query param | Default | Meaning |
|---|---|---|
| `pages` | all | e.g. `1-3,5` |
| `output` | `json` | `json` or `markdown` |
| `include_lines` | `false` | include each text line with its own box and score |

```bash
curl -F "file=@invoice.pdf" "http://localhost:8080/v1/parse?pages=1-2"
```

`GET /health` returns `{"status": "ok", "ready": true}` once models are loaded.

### Output

```json
{
  "filename": "invoice.pdf", "file_type": "pdf", "page_count": 1, "elapsed_ms": 80.1,
  "pages": [{
    "page": 1, "width": 1654, "height": 2339,
    "source": "text_layer", "routes": ["text_layer", "pdf_tables"],
    "orientation": 0, "handwriting_likely": false,
    "blocks": [
      {"type": "text",  "bbox": [200, 90, 1004, 212], "text": "INVOICE 2026-0042\n...", "confidence": 1.0},
      {"type": "table", "bbox": [200, 522, 487, 672], "rows": [["Item","Qty","Price"], ["Paper","2","10.00"]]}
    ]
  }]
}
```

- **Block types:** `title`, `heading`, `text`, `table` (`rows`, plus `html` from OCR),
  `chart` (`rows`), `formula` (`latex`), `image`, `caption`, `header`, `footer`, `seal`.
- **Coordinates** are pixels: of the image as uploaded (images), or of the page rendered
  at `render_dpi` (PDFs). If a page was rotated upright, boxes are in the upright frame.
- **`source`** says where text came from: `text_layer`, `ocr`, `structure`, `embedded_image_ocr`.

### Calling from .NET

```csharp
using var form = new MultipartFormDataContent();
form.Add(new StreamContent(File.OpenRead("invoice.pdf")), "file", "invoice.pdf");
var res = await http.PostAsync("http://ocr:8080/v1/parse", form);
var json = await res.Content.ReadAsStringAsync();
```

## Configuration

All settings are environment variables prefixed with `DOCPARSE_` (see
`src/docparse/config.py` and `.env.example`). The ones you are most likely to change:

| Variable | Default | Notes |
|---|---|---|
| `DOCPARSE_DEVICE` | `cpu` | `gpu:0` for GPU |
| `DOCPARSE_CPU_THREADS` | cores, max 8 | match physical cores |
| `DOCPARSE_ENABLE_CHARTS` / `_FORMULAS` / `_SEALS` | on / on / off | turn off what you never need |
| `DOCPARSE_HANDWRITING_THRESHOLD` | `0.80` | lines scoring below this are re-read |
| `DOCPARSE_HANDWRITING_PAGE_RATIO` | `0.30` | share of weak lines that marks a page handwritten |
| `DOCPARSE_MIN_TEXT_LAYER_CHARS` | `50` | fewer → treat PDF page as scanned |
| `DOCPARSE_RENDER_DPI` | `200` | 200–300 for small print |

## Performance (rough, per page)

| Page | CPU (8 cores) | GPU |
|---|---|---|
| Digital PDF page | ~0.1 s | ~0.1 s |
| Scanned printed text | 2–4 s | < 1 s |
| Scanned with tables | 4–8 s | ~1 s |
| Handwritten | 3–6 s | ~1 s |
| Chart → data | 10–30 s | 1–3 s |

Tips: use the GPU image for volume; turn off charts on CPU if you don't need chart data;
run one worker per container and scale by adding containers (Paddle is not thread-safe,
so requests inside one process are handled one at a time).

## Tests

```bash
pip install -e ".[dev]"
pytest
```

Tests use a fake model, so they check the routing and output format in about a second
without downloading anything. Before going live, run a sample of your real documents
through the CLI and look at the `routes` and `confidence` values.

## Known limits

- **Check rotation once:** if sideways scans come out with garbage text, the rotation
  direction for your model version is reversed; flip `ROTATE_TURNS` in `parser.py`
  (90 ↔ 270).
- Table cells that span several rows (`rowspan`) are not expanded in `rows`; the `html`
  field keeps the full structure.
- Very messy handwriting is better served by PaddleOCR-VL on a GPU.
