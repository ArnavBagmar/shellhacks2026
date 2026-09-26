"""A dev-only stand-in for the step 7 inference service.

It implements the F4 contract exactly -- same models, the capture stamp echoed
byte-identically, no clock reads -- and answers with one deterministic
detection whose crop index advances every request, so bearings sweep the circle
and the phone sees movement. ``--delay-ms`` induces uplink latency, which is
how the "one request in flight" rule is exercised end to end.

    PYTHONPATH=src uv run python -m cane.bridge.dev_stub_server --port 8000 --delay-ms 0

Step 7 replaces this with the real service; nothing else in the bridge knows
the difference.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import time
from typing import Annotated, Sequence

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from pydantic import ValidationError

from cane.cloud.api_models import CROP_COUNT, Detection, InferenceMeta, InferenceResponse

DEFAULT_PORT = 8000
DEFAULT_HOST = "127.0.0.1"
DEFAULT_DELAY_MS = 0
UNPROCESSABLE = 422
JSON_MEDIA_TYPE = "application/json"
STUB_CLASS = "person"
STUB_CONFIDENCE = 0.9
STUB_BOX = (0.5, 0.5, 0.12, 0.35)  # cx, cy, w, h
MS_PER_S = 1000.0


def _parse_meta(raw: str) -> InferenceMeta:
    try:
        return InferenceMeta.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=UNPROCESSABLE, detail=json.loads(exc.json())) from exc


def _stub_detection(crop_index: int) -> Detection:
    cx, cy, w, h = STUB_BOX
    return Detection(
        crop_index=crop_index,
        cx=cx,
        cy=cy,
        w=w,
        h=h,
        cls=STUB_CLASS,
        confidence=STUB_CONFIDENCE,
        track_id=f"{crop_index}:0:1",
        closing_mps=None,
        range_band="mid",
    )


def build_app(delay_ms: int = DEFAULT_DELAY_MS) -> FastAPI:
    app = FastAPI(title="cane dev inference stub", version="0.1.0")
    crop_cycle = itertools.cycle(range(CROP_COUNT))

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        return {"status": "ok", "stub": True, "delay_ms": delay_ms}

    @app.post("/infer")
    async def infer(
        meta: Annotated[str, Form()],
        crop_0: Annotated[UploadFile, File()],
        crop_1: Annotated[UploadFile, File()],
        crop_2: Annotated[UploadFile, File()],
        crop_3: Annotated[UploadFile, File()],
        crop_4: Annotated[UploadFile, File()],
        crop_5: Annotated[UploadFile, File()],
    ) -> Response:
        started = time.perf_counter()
        parsed = _parse_meta(meta)
        crops = (crop_0, crop_1, crop_2, crop_3, crop_4, crop_5)
        sizes = [len(await crop.read()) for crop in crops]
        if any(size == 0 for size in sizes):
            raise HTTPException(status_code=UNPROCESSABLE, detail=f"empty crop: {sizes}")
        if delay_ms:
            await asyncio.sleep(delay_ms / MS_PER_S)
        body = InferenceResponse(
            schema=1,
            # Echoed, never recomputed: the cloud is timeless.
            t_capture_ms=parsed.t_capture_ms,
            inference_ms=(time.perf_counter() - started) * MS_PER_S,
            detections=(_stub_detection(next(crop_cycle)),),
        )
        return Response(
            content=body.model_dump_json(by_alias=True), media_type=JSON_MEDIA_TYPE
        )

    return app


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m cane.bridge.dev_stub_server")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--delay-ms", type=int, default=DEFAULT_DELAY_MS, help="induced inference latency"
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - a server loop
    import uvicorn

    args = parse_args(argv)
    uvicorn.run(build_app(delay_ms=args.delay_ms), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
