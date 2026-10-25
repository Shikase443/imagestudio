"""ブラウザと既存画像APIを同一オリジンで接続する。モデルはロードしない。"""
from contextlib import asynccontextmanager
import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask

LIMIT = 32 * 1024 * 1024
ROUTES = {"health": "GET", "v1/models": "GET", "v1/images/generations": "POST",
          "v1/images/edits": "POST", "v1/images/compositions": "POST",
          "v1/images/analyze": "POST", "v1/chat/completions": "POST"}


@asynccontextmanager
async def lifespan(app):
    async with httpx.AsyncClient(base_url=os.getenv("IMAGE_API_URL", "http://127.0.0.1:8000"),
                                 timeout=httpx.Timeout(660, connect=5), trust_env=False) as client:
        app.state.client = client
        yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/ready")
async def ready():
    return {"status": "ok"}


@app.api_route("/api/{path:path}", methods=["GET", "POST"])
async def proxy(path: str, request: Request):
    if ROUTES.get(path) != request.method:
        return JSONResponse({"error": {"message": "このAPIは利用できません"}}, 404)
    body = bytearray()
    async for part in request.stream():
        body.extend(part)
        if len(body) > LIMIT:
            return JSONResponse({"error": {"message": "要求本文は32MiB以内にしてください"}}, 413)
    headers = {}
    if request.headers.get("content-type"):
        headers["content-type"] = request.headers["content-type"]
    try:
        upstream = app.state.client.build_request(request.method, "/" + path, headers=headers, content=bytes(body))
        response = await app.state.client.send(upstream, stream=True)
    except httpx.TimeoutException:
        return JSONResponse({"error": {"message": "APIへの接続がタイムアウトしました"}}, 504)
    except httpx.RequestError:
        return JSONResponse({"error": {"message": "画像APIに接続できません"}}, 503)
    forwarded = {key: value for key, value in response.headers.items()
                 if key in {"content-type", "x-request-id", "retry-after"}}
    forwarded.update({"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
    return StreamingResponse(response.aiter_bytes(), status_code=response.status_code,
                             headers=forwarded, background=BackgroundTask(response.aclose))


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")
