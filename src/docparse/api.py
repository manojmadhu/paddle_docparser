"""HTTP API.  Run:  uvicorn docparse.api:app --host 0.0.0.0 --port 8080"""
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse

from .loaders import UnsupportedFileError
from .parser import DocumentParser, TooManyPagesError
from .utils import to_markdown


def create_app(parser: DocumentParser | None = None, warmup: bool = True) -> FastAPI:
    state = {"parser": parser, "ready": False}

    @asynccontextmanager
    async def lifespan(_app):
        if state["parser"] is None:
            state["parser"] = DocumentParser()
        if warmup and hasattr(state["parser"].models, "warmup"):
            await run_in_threadpool(state["parser"].models.warmup)  # load models before traffic
        state["ready"] = True
        yield

    app = FastAPI(title="PaddleOCR Document Parser", version="1.0.0", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok", "ready": state["ready"]}

    @app.post("/v1/parse")
    async def parse(
        file: UploadFile = File(...),
        pages: str | None = Query(None, description="Pages to read, e.g. '1-3,5'"),
        output: str = Query("json", pattern="^(json|markdown)$"),
        include_lines: bool = Query(False, description="Include every text line with its box"),
    ):
        p: DocumentParser = state["parser"]
        data = await file.read()
        if len(data) > p.s.max_upload_mb * 1024 * 1024:
            raise HTTPException(413, f"File is larger than {p.s.max_upload_mb} MB.")
        if not data:
            raise HTTPException(400, "Empty file.")
        try:
            result = await run_in_threadpool(p.parse, data, file.filename, pages)
        except UnsupportedFileError as e:
            raise HTTPException(415, str(e))
        except TooManyPagesError as e:
            raise HTTPException(413, str(e))

        if output == "markdown":
            return PlainTextResponse(to_markdown(result), media_type="text/markdown")
        if not include_lines:
            for page in result["pages"]:
                for block in page["blocks"]:
                    block.pop("lines", None)
        return result

    return app


app = create_app()
