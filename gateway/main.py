"""モデルをロードしない統合API。推論は検証済みC++バックエンドへ転送する。"""

import asyncio
import base64
from contextlib import asynccontextmanager
from contextvars import ContextVar
from io import BytesIO
import json
import logging
import os
from pathlib import Path
import secrets
import time
from typing import Annotated, Literal
import uuid

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
import httpx
import jsonschema
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, ValidationError, field_validator

GEN_MODEL = "qwen-image-2.1-turbo"
VLM_MODEL = "qwen-image-analysis"
GEN_FILE = "Qwen-Image-2.1-Turbo-AD-Q6_K.gguf"
VLM_FILE = "Qwen-Image-2.1-Turbo-Abliterated-Uncensored-Q8_0.gguf"
SIGMAS = [1.0, 0.978453, 0.95418, 0.926626, 0.89508, 0.845148, 0.704534, 0.414568, 0.0]
MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_BODY_BYTES = 32 * 1024 * 1024
MAX_REPLY_BYTES = 64 * 1024 * 1024
logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("image-api")
logging.getLogger("httpx").setLevel(logging.WARNING)
INFERENCE_ID = ContextVar("inference_id", default=None)


def log(event, **values):
    # トークン、入力本文、Base64、画像本体はログへ出さない。
    logger.info(json.dumps({"event": event, **values}, ensure_ascii=False))


def error_body(status, message, request_id):
    return {"error": {"message": message, "type": "invalid_request_error" if status < 500 else "server_error",
                      "code": status, "request_id": request_id}}


class AccessMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started = time.monotonic()
        headers = dict(scope.get("headers", []))
        try:
            declared_length = int(headers.get(b"content-length", b"0") or b"0")
        except ValueError:
            return await JSONResponse(error_body(400, "Content-Lengthが不正です", request_id), 400)(scope, receive, send)
        if declared_length > MAX_BODY_BYTES:
            return await JSONResponse(error_body(413, "リクエストが大きすぎます", request_id), 413)(scope, receive, send)
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > MAX_BODY_BYTES:
                raise HTTPException(413, "リクエストが大きすぎます")
            return message

        async def logged_send(message):
            if message["type"] == "http.response.start":
                message["headers"].append((b"x-request-id", request_id.encode()))
                log("api_response", request_id=request_id, status=message["status"],
                    elapsed_seconds=round(time.monotonic() - started, 3))
            await send(message)

        log("api_received", request_id=request_id, method=scope["method"], path=scope["path"])
        await self.app(scope, limited_receive, logged_send)


@asynccontextmanager
async def lifespan(app):
    app.state.client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5, write=10, pool=5), trust_env=False)
    app.state.urls = {"gen": os.getenv("IMAGE_GEN_URL", "http://image-gen:8001"),
                      "vlm": os.getenv("IMAGE_VLM_URL", "http://image-vlm:8002")}
    app.state.admission = asyncio.Lock()
    app.state.active = None
    app.state.uncertain = False
    app.state.api_timeout = float(os.getenv("API_TIMEOUT_SECONDS", "600"))
    app.state.started_at = int(time.time())
    log("api_started", generation_model=GEN_MODEL, analysis_model=VLM_MODEL)
    yield
    if app.state.active and not app.state.active.done():
        app.state.active.cancel()
        await asyncio.gather(app.state.active, return_exceptions=True)
    await app.state.client.aclose()


app = FastAPI(title="Image Studio API", lifespan=lifespan)
app.add_middleware(AccessMiddleware)


@app.exception_handler(HTTPException)
async def http_error(request, error):
    log("api_error", request_id=request.state.request_id, status=error.status_code,
        cause=error.detail if isinstance(error.detail, str) else "request_error")
    return JSONResponse(error_body(error.status_code, error.detail, request.state.request_id),
                        error.status_code, headers=error.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(request, error):
    # Pydanticの既定応答のinputフィールドに画像が含まれることを避ける。
    issues = [{key: item[key] for key in ("loc", "msg", "type")} for item in error.errors()]
    body = error_body(422, "入力形式が不正です", request.state.request_id)
    body["error"]["details"] = issues
    return JSONResponse(body, 422)


def model_check(model, backend):
    names = (GEN_MODEL, GEN_FILE, GEN_FILE.removesuffix(".gguf")) if backend == "gen" else (VLM_MODEL, VLM_FILE, VLM_FILE.removesuffix(".gguf"))
    if model not in names:
        raise HTTPException(404, "指定モデルは利用できません")


def size_check(size):
    try:
        width, height = [int(value) for value in size.split("x")]
    except (ValueError, AttributeError):
        raise HTTPException(422, "sizeは1024x1024の形式で指定してください")
    if any(value < 64 or value > 2048 or value % 64 for value in (width, height)):
        raise HTTPException(422, "縦横は64〜2048の64の倍数で指定してください")
    return width, height


def image_check(data, png_only=False):
    if not data or len(data) > (MAX_REPLY_BYTES if png_only else MAX_IMAGE_BYTES):
        raise HTTPException(413, "画像は16MiB以下で指定してください")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in (("PNG",) if png_only else ("PNG", "JPEG")):
                raise ValueError("format")
            if image.width * image.height > 16 * 1024 * 1024:
                raise ValueError("pixels")
            mime = "image/png" if image.format == "PNG" else "image/jpeg"
            image.verify()
        with Image.open(BytesIO(data)) as image:
            image.load()
    except Exception:
        raise HTTPException(422, "有効なPNGまたはJPEG画像を指定してください")
    return mime


def image_data_url(encoded):
    declared = None
    if encoded.startswith("data:"):
        header, _, encoded = encoded.partition(",")
        if header not in ("data:image/png;base64", "data:image/jpeg;base64"):
            raise HTTPException(422, "PNG/JPEGのBase64データURLを指定してください")
        declared = header[5:].split(";")[0]
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise HTTPException(422, "画像のBase64形式が不正です")
    mime = image_check(data)
    if declared and declared != mime:
        raise HTTPException(422, "画像形式とデータURLが一致しません")
    return "data:" + mime + ";base64," + base64.b64encode(data).decode()


def schema_check(response_format):
    if not response_format or response_format.get("type", "text") == "text":
        return None
    kind = response_format.get("type")
    if kind == "json_object":
        schema = response_format.get("schema", {"type": "object"})
    elif kind == "json_schema":
        wrapper = response_format.get("json_schema")
        if not isinstance(wrapper, dict):
            raise HTTPException(422, "json_schemaの形式が不正です")
        schema = wrapper.get("schema")
    else:
        raise HTTPException(422, "response_formatが不正です")
    if not isinstance(schema, dict):
        raise HTTPException(422, "JSONスキーマを指定してください")

    def inspect(value):
        if isinstance(value, dict):
            if "$ref" in value and not value["$ref"].startswith("#"):
                raise ValueError("external ref")
            for item in value.values():
                inspect(item)
        elif isinstance(value, list):
            for item in value:
                inspect(item)
    try:
        inspect(schema)
        validator = jsonschema.validators.validator_for(schema)
        validator.check_schema(schema)
    except Exception:
        raise HTTPException(422, "JSONスキーマが不正です。外部参照は使用できません")
    return validator(schema)


async def json_call(backend, method, path, payload=None, timeout=None):
    try:
        headers = {"X-Request-ID": INFERENCE_ID.get()} if INFERENCE_ID.get() else None
        response = await app.state.client.request(method, app.state.urls[backend] + path, json=payload, timeout=timeout, headers=headers)
    except httpx.TimeoutException:
        if method == "POST":
            app.state.uncertain = True
        raise HTTPException(504, "バックエンドの応答がタイムアウトしました")
    except httpx.RequestError:
        if method == "POST":
            app.state.uncertain = True
        raise HTTPException(502, "バックエンドとの通信に失敗しました")
    if response.status_code >= 400:
        code = 503 if response.status_code == 503 else response.status_code if response.status_code < 500 else 502
        raise HTTPException(code, "バックエンドがリクエストを処理できませんでした")
    try:
        if len(response.content) > MAX_REPLY_BYTES:
            raise ValueError("size")
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError("shape")
        return value
    except (ValueError, TypeError):
        raise HTTPException(502, "バックエンドの応答形式が不正です")


async def probe(backend):
    try:
        value = await json_call(backend, "GET", "/v1/models" if backend == "gen" else "/health", timeout=2)
        return bool(value.get("data")) if backend == "gen" else value.get("status") == "ok"
    except HTTPException:
        return False


async def gpu_memory():
    # GPU・ホストPID・Dockerソケットをゲートウェイへ渡さず、実測値だけ読む。
    result = {}
    root = Path(os.getenv("TELEMETRY_DIR", "/telemetry"))
    for backend in ("gen", "vlm"):
        try:
            value = json.loads((root / (backend + ".json")).read_text())
            if time.time() - value["measured_at"] <= 5 and type(value["gpu_mib"]) is int:
                result[backend] = value["gpu_mib"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return result


async def run_work(function, request_id, backend):
    context_token = INFERENCE_ID.set(request_id)
    started = time.monotonic()
    stop = asyncio.Event()
    observations = []

    async def monitor():
        last = started
        while True:
            observed = await gpu_memory()
            if backend in observed:
                observations.append(observed[backend])
            now = time.monotonic()
            if now - last >= 30:
                log("inference_progress", request_id=request_id, backend=backend,
                    elapsed_seconds=round(now - started, 3), gpu_mib=observed.get(backend))
                last = now
            if stop.is_set():
                break
            try:
                await asyncio.wait_for(stop.wait(), 1)
            except asyncio.TimeoutError:
                pass

    log("inference_started", request_id=request_id, backend=backend)
    sampling = asyncio.create_task(monitor())
    outcome = "success"
    try:
        return await function()
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except Exception as error:
        outcome = "error"
        log("inference_error", request_id=request_id, backend=backend,
            cause=type(error).__name__, status=getattr(error, "status_code", 502))
        raise
    finally:
        stop.set()
        await sampling
        log("inference_finished", request_id=request_id, backend=backend, outcome=outcome,
            elapsed_seconds=round(time.monotonic() - started, 3), gpu_samples=len(observations),
            gpu_observed_max_mib=max(observations) if observations else None)
        INFERENCE_ID.reset(context_token)


async def start_work(function, request_id, backend):
    if not await probe(backend):
        raise HTTPException(503, "モデルの準備ができていません")
    async with app.state.admission:
        if app.state.uncertain:
            raise HTTPException(503, "バックエンドの処理状態を確認できません")
        if app.state.active and not app.state.active.done():
            raise HTTPException(429, "別の推論を実行中です", headers={"Retry-After": "2"})
        task = asyncio.create_task(run_work(function, request_id, backend))
        # クライアント切断・API期限切れでも、GPUの処理が終わるまで予約を保持する。
        task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
        app.state.active = task
        return task


async def wait_work(task):
    try:
        return await asyncio.wait_for(asyncio.shield(task), app.state.api_timeout)
    except asyncio.TimeoutError:
        raise HTTPException(504, "推論の応答期限を超えました。処理終了まで次の推論は受け付けません")


class Generation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    model: str = GEN_MODEL
    prompt: str = Field(min_length=1, max_length=8000)
    size: str = "1024x1024"
    n: Literal[1] = 1
    seed: StrictInt | None = Field(default=None, ge=-1, le=2**63 - 1)
    output_format: Literal["png"] = "png"
    response_format: Literal["b64_json"] = "b64_json"

    @field_validator("n", mode="before")
    @classmethod
    def one_image(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("nは整数の1を指定してください")
        return value


async def generate(body, reference=None):
    width, height = size_check(body.size)
    payload = {"prompt": body.prompt, "width": width, "height": height, "batch_count": 1,
               "seed": body.seed if body.seed is not None else secrets.randbelow(2**31),
               "output_format": "png", "sample_params": {"sample_steps": 8, "sample_method": "euler",
               "guidance": {"txt_cfg": 1.0}, "custom_sigmas": SIGMAS}}
    if reference:
        payload.update(ref_images=reference if isinstance(reference, list) else [reference], strength=1.0)
    try:
        accepted = await json_call("gen", "POST", "/sdcpp/v1/img_gen", payload, timeout=10)
        poll_url = accepted["poll_url"]
        if not isinstance(poll_url, str) or not poll_url.startswith("/sdcpp/v1/jobs/job_"):
            raise ValueError("job")
        log("backend_job_started", request_id=INFERENCE_ID.get(), job_id=accepted.get("id"), backend="gen")
    except (HTTPException, KeyError, ValueError) as error:
        if not isinstance(error, HTTPException) or error.status_code >= 500:
            app.state.uncertain = True
        raise error if isinstance(error, HTTPException) else HTTPException(502, "生成ジョブの応答が不正です")
    while True:
        try:
            job = await json_call("gen", "GET", poll_url, timeout=10)
        except HTTPException as error:
            if error.status_code in (404, 410):
                raise HTTPException(502, "生成ジョブが失われました")
            # 受付済みジョブの状態不明時に、別のGPU推論を開始しない。
            await asyncio.sleep(2)
            continue
        if job.get("status") in ("failed", "cancelled"):
            raise HTTPException(502, "画像生成バックエンドが推論に失敗しました")
        if job.get("status") == "completed":
            log("backend_job_finished", request_id=INFERENCE_ID.get(), job_id=accepted.get("id"), status="completed")
            try:
                images = job["result"]["images"]
                if len(images) != 1:
                    raise ValueError("count")
                encoded = images[0]["b64_json"]
                output = base64.b64decode(encoded, validate=True)
                image_check(output, png_only=True)
                with Image.open(BytesIO(output)) as image:
                    if image.size != (width, height):
                        raise ValueError("dimensions")
                return {"created": int(time.time()), "data": [{"b64_json": encoded}]}
            except (KeyError, ValueError, TypeError, HTTPException):
                raise HTTPException(502, "バックエンドが正常なPNGを返しませんでした")
        if job.get("status") not in ("queued", "generating"):
            app.state.uncertain = True
            raise HTTPException(502, "生成ジョブの状態が不正です")
        await asyncio.sleep(1)


@app.get("/health")
async def health():
    gen, vlm = await asyncio.gather(probe("gen"), probe("vlm"))
    ready = gen and vlm and not app.state.uncertain
    return JSONResponse({"status": "ok" if ready else "degraded", "services": {"image-api": True, "image-gen": gen, "image-vlm": vlm},
                         "inference_busy": bool(app.state.active and not app.state.active.done()),
                         "state_uncertain": app.state.uncertain}, 200 if ready else 503)


@app.get("/v1/models")
async def models():
    gen, vlm = await asyncio.gather(probe("gen"), probe("vlm"))
    return {"object": "list", "data": [{"id": name, "object": "model", "owned_by": "local", "created": app.state.started_at}
            for name, ready in ((GEN_MODEL, gen), (VLM_MODEL, vlm)) if ready]}


@app.post("/v1/images/generations")
async def generations(body: Generation, request: Request):
    model_check(body.model, "gen")
    size_check(body.size)
    return await wait_work(await start_work(lambda: generate(body), request.state.request_id, "gen"))


@app.post("/v1/images/edits")
async def edits(request: Request, image: Annotated[UploadFile, File()], prompt: Annotated[str, Form()],
                model: Annotated[str, Form()] = GEN_MODEL, size: Annotated[str, Form()] = "1024x1024",
                n: Annotated[int, Form()] = 1, seed: Annotated[int | None, Form()] = None,
                output_format: Annotated[str, Form()] = "png", response_format: Annotated[str, Form()] = "b64_json"):
    form = await request.form()
    if set(form) - {"image", "model", "prompt", "size", "n", "seed", "output_format", "response_format"} or len(form.getlist("image")) != 1:
        raise HTTPException(422, "画像は1枚のみ指定できます。マスク編集は未対応です")
    try:
        body = Generation(model=model, prompt=prompt, size=size, n=n, seed=seed,
                          output_format=output_format, response_format=response_format)
    except ValidationError:
        raise HTTPException(422, "編集パラメータが不正です")
    model_check(body.model, "gen")
    size_check(body.size)
    data = await image.read(MAX_IMAGE_BYTES + 1)
    mime = await asyncio.to_thread(image_check, data)
    reference = "data:" + mime + ";base64," + base64.b64encode(data).decode()
    return await wait_work(await start_work(lambda: generate(body, reference), request.state.request_id, "gen"))


@app.post("/v1/images/compositions")
async def compositions(request: Request, image1: Annotated[UploadFile, File()], image2: Annotated[UploadFile, File()],
                       prompt: Annotated[str, Form()], model: Annotated[str, Form()] = GEN_MODEL,
                       size: Annotated[str, Form()] = "1024x1024", n: Annotated[int, Form()] = 1,
                       seed: Annotated[int | None, Form()] = None, output_format: Annotated[str, Form()] = "png",
                       response_format: Annotated[str, Form()] = "b64_json"):
    form = await request.form()
    if set(form) - {"image1", "image2", "model", "prompt", "size", "n", "seed", "output_format", "response_format"} or any(len(form.getlist(key)) != 1 for key in ("image1", "image2")):
        raise HTTPException(422, "合成にはimage1とimage2を各1枚指定してください")
    try:
        body = Generation(model=model, prompt=prompt, size=size, n=n, seed=seed,
                          output_format=output_format, response_format=response_format)
    except ValidationError:
        raise HTTPException(422, "合成パラメータが不正です")
    model_check(body.model, "gen")
    size_check(body.size)
    references = []
    for uploaded in (image1, image2):
        data = await uploaded.read(MAX_IMAGE_BYTES + 1)
        mime = await asyncio.to_thread(image_check, data)
        references.append("data:" + mime + ";base64," + base64.b64encode(data).decode())
    return await wait_work(await start_work(lambda: generate(body, references), request.state.request_id, "gen"))


class Chat(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str = VLM_MODEL
    messages: list[dict] = Field(min_length=1, max_length=128)
    stream: StrictBool = False
    n: Literal[1] = 1
    max_tokens: StrictInt | None = Field(default=None, ge=1, le=4096)
    max_completion_tokens: StrictInt | None = Field(default=None, ge=1, le=4096)
    response_format: dict | None = None

    @field_validator("n", mode="before")
    @classmethod
    def one_completion(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("nは整数の1を指定してください")
        return value


def chat_payload(body):
    model_check(body.model, "vlm")
    payload = body.model_dump(exclude_none=True)
    payload["model"] = VLM_MODEL
    if body.max_tokens is not None and body.max_completion_tokens is not None:
        raise HTTPException(422, "max_tokensとmax_completion_tokensはどちらか一方を指定してください")
    payload.pop("max_completion_tokens", None)
    payload["max_tokens"] = body.max_tokens or body.max_completion_tokens or 512
    for message in payload["messages"]:
        if message.get("role") not in ("system", "user", "assistant", "tool", "developer"):
            raise HTTPException(422, "messagesのroleが不正です")
        content = message.get("content")
        if content is not None and not isinstance(content, (str, list)):
            raise HTTPException(422, "messagesのcontentが不正です")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict) or part.get("type") not in ("text", "image_url"):
                    raise HTTPException(422, "textまたはimage_urlを指定してください")
                if part["type"] == "image_url":
                    if not isinstance(part.get("image_url"), dict):
                        raise HTTPException(422, "image_urlの形式が不正です")
                    url = part["image_url"].get("url")
                    if not isinstance(url, str) or not url.startswith("data:"):
                        raise HTTPException(422, "画像はPNG/JPEGのBase64データURLで指定してください")
                    part["image_url"]["url"] = image_data_url(url)
                elif not isinstance(part.get("text"), str):
                    raise HTTPException(422, "textは文字列で指定してください")
    return payload, schema_check(body.response_format)


def validate_chat_reply(reply, validator):
    try:
        if reply.get("object") != "chat.completion" or not reply.get("choices"):
            raise ValueError("shape")
        content = reply["choices"][0]["message"].get("content")
        if validator:
            value = json.loads(content)
            validator.validate(value)
            return value
        return content
    except Exception:
        raise HTTPException(502, "解析バックエンドの応答またはJSONスキーマが不正です")


async def complete_chat(payload, validator):
    reply = await json_call("vlm", "POST", "/v1/chat/completions", payload)
    validate_chat_reply(reply, validator)
    return reply


async def streaming_work(payload, validator, channel, ready):
    response = None
    try:
        upstream = app.state.client.build_request("POST", app.state.urls["vlm"] + "/v1/chat/completions", json=payload)
        response = await app.state.client.send(upstream, stream=True)
        if response.status_code >= 400:
            code = response.status_code if response.status_code < 500 else 503 if response.status_code == 503 else 502
            raise HTTPException(code, "チャットバックエンドが失敗しました")
        if "text/event-stream" not in response.headers.get("content-type", ""):
            raise HTTPException(502, "ストリーミング応答形式が不正です")
        if not validator:
            ready.set_result(True)
        saved, content, total, done = [], "", 0, False
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            total += len(line)
            if total > 4 * 1024 * 1024:
                raise HTTPException(502, "ストリーミング応答が大きすぎます")
            value = line[5:].strip()
            if value == "[DONE]":
                done = True
            else:
                event = json.loads(value)
                if "error" in event:
                    raise HTTPException(502, "ストリーミング推論が失敗しました")
                for choice in event.get("choices", []):
                    content += choice.get("delta", {}).get("content") or ""
            encoded = (line + "\n\n").encode()
            if validator:
                saved.append(encoded)
            else:
                await channel.put(encoded)
        if not done:
            raise HTTPException(502, "ストリーミングが正常に完了しませんでした")
        if validator:
            try:
                validator.validate(json.loads(content))
            except (ValueError, jsonschema.ValidationError):
                raise HTTPException(502, "ストリーミングJSONがスキーマに一致しません")
            # JSONストリームは検証完了後に配信し、不正なJSONを成功として返さない。
            ready.set_result(True)
            for encoded in saved:
                await channel.put(encoded)
    except Exception as error:
        failure = error if isinstance(error, HTTPException) else HTTPException(502, "ストリーミング通信に失敗しました")
        if not ready.done():
            ready.set_exception(failure)
        else:
            await channel.put(("data: " + json.dumps(error_body(failure.status_code, failure.detail, channel.request_id)) + "\n\n").encode())
        raise failure
    finally:
        if response:
            await response.aclose()
        await channel.put(None)


class StreamChannel:
    def __init__(self, request_id):
        self.queue = asyncio.Queue(maxsize=32)
        self.connected = True
        self.request_id = request_id

    async def put(self, value):
        # 切断後もバックエンドを最後まで読み、別のGPU推論と重ねない。
        while self.connected:
            try:
                await asyncio.wait_for(self.queue.put(value), 1)
                return
            except asyncio.TimeoutError:
                pass


@app.post("/v1/chat/completions")
async def chat(body: Chat, request: Request):
    payload, validator = await asyncio.to_thread(chat_payload, body)
    if not body.stream:
        return await wait_work(await start_work(lambda: complete_chat(payload, validator), request.state.request_id, "vlm"))
    channel = StreamChannel(request.state.request_id)
    ready = asyncio.get_running_loop().create_future()
    await start_work(lambda: streaming_work(payload, validator, channel, ready), request.state.request_id, "vlm")
    try:
        await wait_work(ready)
    except Exception:
        channel.connected = False
        raise

    async def events():
        try:
            while True:
                value = await channel.queue.get()
                if value is None:
                    break
                yield value
        finally:
            channel.connected = False
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = VLM_MODEL
    prompt: str = Field(min_length=1, max_length=8000)
    image: str = Field(min_length=1, max_length=24 * 1024 * 1024)
    format: Literal["text", "json"] = "text"
    schema_: dict | None = Field(default=None, alias="schema")


@app.post("/v1/images/analyze")
async def analyze(body: Analysis, request: Request):
    model_check(body.model, "vlm")
    encoded = await asyncio.to_thread(image_data_url, body.image)
    payload = {"model": VLM_MODEL, "messages": [{"role": "user", "content": [
        {"type": "text", "text": body.prompt}, {"type": "image_url", "image_url": {"url": encoded}}]}],
        "max_tokens": 512, "temperature": 0, "stream": False}
    validator = None
    if body.format == "json":
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "image_analysis", "strict": True, "schema": body.schema_ or {"type": "object"}}}
        validator = schema_check(payload["response_format"])
    elif body.schema_ is not None:
        raise HTTPException(422, "schemaはformat=jsonで指定してください")
    started = time.monotonic()
    reply = await wait_work(await start_work(lambda: complete_chat(payload, validator), request.state.request_id, "vlm"))
    return {"model": body.model, "result": validate_chat_reply(reply, validator),
            "processing_seconds": round(time.monotonic() - started, 3)}
