"""実バックエンドを模して、統合APIの故障時動作と契約を検証する。"""

import asyncio
import base64
from io import BytesIO, StringIO
import json
import logging
import os
import unittest

import httpx
from PIL import Image

os.environ["IMAGE_GEN_URL"] = "http://gen.test"
os.environ["IMAGE_VLM_URL"] = "http://vlm.test"
import main

SCHEMA = {"type": "object", "properties": {"color": {"type": "string", "enum": ["blue"]}},
          "required": ["color"], "additionalProperties": False}


class GatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mode = "normal"
        self.down = None
        self.payload = None
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        image = BytesIO()
        Image.new("RGB", (64, 64), "blue").save(image, "PNG")
        self.png = image.getvalue()
        self.encoded = base64.b64encode(self.png).decode()
        self.url = "data:image/png;base64," + self.encoded
        self.lifespan = main.lifespan(main.app)
        await self.lifespan.__aenter__()
        await main.app.state.client.aclose()
        main.app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(self.backend))
        self.gpu_original = main.gpu_memory

        async def fake_gpu():
            return {"gen": 1000, "vlm": 2000}
        main.gpu_memory = fake_gpu
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://api.test")

    async def asyncTearDown(self):
        self.release.set()
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        main.gpu_memory = self.gpu_original

    async def backend(self, request):
        path = request.url.path
        if request.url.host == self.down:
            return httpx.Response(503, json={"error": "loading"})
        if path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "local"}]})
        if path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "POST":
            self.payload = json.loads(request.content)
            if self.mode == "http500":
                return httpx.Response(500, json={"error": "failure"})
            if self.mode == "timeout":
                raise httpx.ReadTimeout("simulated", request=request)
            if self.mode == "connection":
                raise httpx.ConnectError("simulated", request=request)
        if path == "/sdcpp/v1/img_gen":
            return httpx.Response(202, json={"id": "job_test", "poll_url": "/sdcpp/v1/jobs/job_test"})
        if path == "/sdcpp/v1/jobs/job_test":
            encoded = "not-png" if self.mode == "badpng" else self.encoded
            status = "failed" if self.mode == "jobfail" else "completed"
            return httpx.Response(200, json={"status": status, "result": {"images": [{"b64_json": encoded}]}})
        if path == "/v1/chat/completions":
            if self.mode == "hold":
                self.started.set()
                await self.release.wait()
            content = json.dumps({"color": 7 if self.mode == "badschema" else "blue"}) if "response_format" in self.payload else "青色です"
            if self.payload.get("stream"):
                if self.mode == "badstream":
                    return httpx.Response(200, text="data: {\"error\":{\"message\":\"fail\"}}\n\n", headers={"Content-Type": "text/event-stream"})
                event = {"id": "chatcmpl-test", "object": "chat.completion.chunk",
                         "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}]}
                return httpx.Response(200, text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n",
                                      headers={"Content-Type": "text/event-stream"})
            reply = {"id": "chatcmpl-test", "object": "chat.completion",
                     "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]}
            if self.mode == "badreply":
                reply["choices"] = []
            return httpx.Response(200, json=reply)
        return httpx.Response(404)

    def generation(self, **extra):
        return {"model": main.GEN_MODEL, "prompt": "a teapot", "size": "64x64", **extra}

    def chat(self, **extra):
        return {"model": main.VLM_MODEL, "messages": [{"role": "user", "content": [
            {"type": "text", "text": "what color"}, {"type": "image_url", "image_url": {"url": self.url}}]}], **extra}

    async def test_no_auth_and_health_models(self):
        self.assertEqual((await self.client.get("/health", headers={"Authorization": "Bearer unused"})).status_code, 200)
        health = await self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertIn("x-request-id", health.headers)
        self.assertEqual(len((await self.client.get("/v1/models")).json()["data"]), 2)

    async def test_generation_fixed_turbo(self):
        response = await self.client.post("/v1/images/generations", json=self.generation(seed=123))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["data"][0]["b64_json"], self.encoded)
        self.assertEqual(self.payload["seed"], 123)
        self.assertEqual(self.payload["sample_params"]["custom_sigmas"], main.SIGMAS)
        self.assertEqual(self.payload["sample_params"]["sample_steps"], 8)
        self.assertEqual(self.payload["sample_params"]["guidance"]["txt_cfg"], 1.0)

    async def test_edit_reference_only(self):
        response = await self.client.post("/v1/images/edits", data={"prompt": "make blue", "size": "64x64"},
                                          files={"image": ("input.png", self.png, "image/png")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.payload["ref_images"]), 1)
        self.assertNotIn("init_image", self.payload)
        self.assertEqual(self.payload["strength"], 1.0)

    async def test_composition_order_and_jpeg(self):
        second = BytesIO()
        Image.new("RGB", (64, 64), "red").save(second, "JPEG")
        jpeg = second.getvalue()
        response = await self.client.post("/v1/images/compositions", data={"prompt": "combine", "size": "64x64", "seed": "42"},
            files={"image1": ("first.png", self.png, "image/png"), "image2": ("second.jpg", jpeg, "image/jpeg")})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.payload["ref_images"], [self.url, "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()])
        self.assertNotIn("init_image", self.payload)
        self.assertEqual(self.payload["seed"], 42)
        self.assertEqual(self.payload["sample_params"]["custom_sigmas"], main.SIGMAS)

    async def test_composition_invalid_images(self):
        image = ("image.png", self.png, "image/png")
        for files in [[("image1", image)], [("image1", image), ("image2", image), ("image1", image)],
                      [("image1", image), ("image2", ("bad.png", b"invalid", "image/png"))]]:
            response = await self.client.post("/v1/images/compositions", data={"prompt": "combine"}, files=files)
            self.assertEqual(response.status_code, 422, response.text)

    def webp(self, animated=False):
        output = BytesIO()
        image = Image.new("RGBA", (64, 64), (10, 20, 30, 128))
        image.save(output, "WEBP", lossless=True, save_all=animated,
                   **({"append_images": [Image.new("RGBA", (64, 64), "red")], "duration": 100} if animated else {}))
        return output.getvalue()

    def assert_webp_normalized(self, reference):
        self.assertTrue(reference.startswith("data:image/png;base64,"))
        with Image.open(BytesIO(base64.b64decode(reference.split(",", 1)[1]))) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (64, 64))
            self.assertEqual(image.convert("RGBA").getpixel((0, 0)), (10, 20, 30, 128))

    async def test_webp_edit_and_composition_preserve_alpha_and_order(self):
        webp = ("input.webp", self.webp(), "image/webp")
        response = await self.client.post("/v1/images/edits", data={"prompt": "edit", "size": "64x64"}, files={"image": webp})
        self.assertEqual(response.status_code, 200, response.text)
        self.assert_webp_normalized(self.payload["ref_images"][0])
        for first in (True, False):
            response = await self.client.post("/v1/images/compositions", data={"prompt": "combine", "size": "64x64"},
                files={"image1": webp if first else ("input.png", self.png, "image/png"),
                       "image2": ("input.png", self.png, "image/png") if first else webp})
            self.assertEqual(response.status_code, 200, response.text)
            self.assert_webp_normalized(self.payload["ref_images"][0 if first else 1])
            self.assertEqual(self.payload["ref_images"][1 if first else 0], self.url)

    async def test_webp_analysis_and_chat_normalize_before_backend(self):
        encoded = base64.b64encode(self.webp()).decode()
        for value in (encoded, "data:image/webp;base64," + encoded):
            response = await self.client.post("/v1/images/analyze", json={"image": value, "prompt": "describe"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assert_webp_normalized(self.payload["messages"][0]["content"][1]["image_url"]["url"])
        for stream in (False, True):
            body = self.chat(stream=stream)
            body["messages"][0]["content"][1]["image_url"]["url"] = "data:image/webp;base64," + encoded
            response = await self.client.post("/v1/chat/completions", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assert_webp_normalized(self.payload["messages"][0]["content"][1]["image_url"]["url"])

    async def test_animated_corrupt_and_mislabeled_webp_rejected(self):
        for data in (self.webp(animated=True), b"RIFF\x00\x00\x00\x00WEBPbad"):
            response = await self.client.post("/v1/images/edits", data={"prompt": "edit"}, files={"image": ("bad.webp", data, "image/webp")})
            self.assertEqual(response.status_code, 422, response.text)
        for url in ("data:image/webp;base64," + self.encoded,
                    "data:image/png;base64," + base64.b64encode(self.webp()).decode()):
            response = await self.client.post("/v1/images/analyze", json={"image": url, "prompt": "describe"})
            self.assertEqual(response.status_code, 422, response.text)

    async def test_composition_invalid_parameters(self):
        files = {"image1": ("a.png", self.png, "image/png"), "image2": ("b.png", self.png, "image/png")}
        for extra in ({"n": "2"}, {"mask": "unsupported"}, {"size": "bad"}):
            response = await self.client.post("/v1/images/compositions", data={"prompt": "combine", **extra}, files=files)
            self.assertEqual(response.status_code, 422, response.text)

    async def test_invalid_generation_inputs(self):
        for extra in ({"n": 2}, {"n": True}, {"size": "1025x1024"}, {"size": "bad"},
                      {"prompt": " "}, {"seed": True}, {"output_format": "jpeg"}):
            with self.subTest(extra=extra):
                response = await self.client.post("/v1/images/generations", json=self.generation(**extra))
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual((await self.client.post("/v1/images/generations", json=self.generation(model="unknown"))).status_code, 404)

    async def test_mask_multiple_and_bad_image_rejected(self):
        for fields, files in [({"mask": "unsupported"}, [("image", ("i.png", self.png, "image/png"))]),
                              ({}, [("image", ("i.png", self.png, "image/png")), ("image", ("j.png", self.png, "image/png"))]),
                              ({}, [("image", ("bad.png", b"invalid", "image/png"))])]:
            response = await self.client.post("/v1/images/edits", data={"prompt": "edit", **fields}, files=files)
            self.assertEqual(response.status_code, 422)

    async def test_chat_and_token_alias(self):
        response = await self.client.post("/v1/chat/completions", json=self.chat(max_completion_tokens=16))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "青色です")
        self.assertEqual(self.payload["max_tokens"], 16)

    async def test_analyze_text_and_json(self):
        body = {"image": self.encoded, "prompt": "describe"}
        response = await self.client.post("/v1/images/analyze", json=body)
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json()["processing_seconds"], float)
        response = await self.client.post("/v1/images/analyze", json={**body, "format": "json", "schema": SCHEMA})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["result"], {"color": "blue"})

    async def test_bad_schema_output_is_502(self):
        self.mode = "badschema"
        response = await self.client.post("/v1/images/analyze", json={"image": self.encoded, "prompt": "describe", "format": "json", "schema": SCHEMA})
        self.assertEqual(response.status_code, 502)

    async def test_invalid_image_url_and_schema_are_4xx(self):
        for image_url in ("wrong", {"url": "http://127.0.0.1/private"}, {"url": "data:image/jpeg;base64," + self.encoded}):
            body = self.chat()
            body["messages"][0]["content"][1]["image_url"] = image_url
            self.assertEqual((await self.client.post("/v1/chat/completions", json=body)).status_code, 422)
        for format in ({"type": "json_schema", "json_schema": "bad"},
                       {"type": "json_schema", "json_schema": {"schema": {"$ref": "https://example.com/schema"}}}):
            self.assertEqual((await self.client.post("/v1/chat/completions", json=self.chat(response_format=format))).status_code, 422)

    async def test_stream_and_validated_json_stream(self):
        for format in (None, {"type": "json_schema", "json_schema": {"name": "test", "schema": SCHEMA}}):
            body = self.chat(stream=True)
            if format:
                body["response_format"] = format
            response = await self.client.post("/v1/chat/completions", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("text/event-stream", response.headers["content-type"])
            self.assertIn("data: [DONE]", response.text)

    async def test_bad_json_stream_is_502_before_headers(self):
        self.mode = "badschema"
        response = await self.client.post("/v1/chat/completions", json=self.chat(stream=True,
            response_format={"type": "json_schema", "json_schema": {"schema": SCHEMA}}))
        self.assertEqual(response.status_code, 502)

    async def test_bad_stream_has_explicit_error(self):
        self.mode = "badstream"
        response = await self.client.post("/v1/chat/completions", json=self.chat(stream=True))
        self.assertIn('"error"', response.text)
        self.assertNotIn("[DONE]", response.text)

    async def test_backend_down_is_503_and_health_degrades(self):
        self.down = "gen.test"
        self.assertEqual((await self.client.get("/health")).status_code, 503)
        self.assertEqual((await self.client.post("/v1/images/generations", json=self.generation())).status_code, 503)
        self.assertEqual(len((await self.client.get("/v1/models")).json()["data"]), 1)

    async def test_backend_failures_are_not_success(self):
        for mode, status in (("http500", 502), ("timeout", 504), ("connection", 502), ("jobfail", 502), ("badpng", 502)):
            self.mode = mode
            main.app.state.uncertain = False
            response = await self.client.post("/v1/images/generations", json=self.generation())
            self.assertEqual(response.status_code, status, response.text)

    async def test_invalid_chat_reply_is_502(self):
        self.mode = "badreply"
        self.assertEqual((await self.client.post("/v1/chat/completions", json=self.chat())).status_code, 502)

    async def test_global_inference_exclusion(self):
        self.mode = "hold"
        first = asyncio.create_task(self.client.post("/v1/chat/completions", json=self.chat()))
        await self.started.wait()
        response = await self.client.post("/v1/images/generations", json=self.generation())
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "2")
        self.release.set()
        self.assertEqual((await first).status_code, 200)

    async def test_api_timeout_keeps_reservation(self):
        self.mode = "hold"
        main.app.state.api_timeout = 0.05
        response = await self.client.post("/v1/chat/completions", json=self.chat())
        self.assertEqual(response.status_code, 504)
        self.assertFalse(main.app.state.active.done())
        response = await self.client.post("/v1/images/generations", json=self.generation())
        self.assertEqual(response.status_code, 429)
        self.release.set()
        await main.app.state.active

    async def test_uncertain_acceptance_blocks_new_work(self):
        self.mode = "timeout"
        self.assertEqual((await self.client.post("/v1/images/generations", json=self.generation())).status_code, 504)
        self.mode = "normal"
        self.assertEqual((await self.client.post("/v1/chat/completions", json=self.chat())).status_code, 503)

    async def test_body_limit_and_no_image_leak_in_errors(self):
        response = await self.client.post("/v1/images/analyze", json={"image": "secret-invalid-image", "prompt": "describe"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("secret-invalid-image", response.text)
        response = await self.client.post("/v1/images/generations", json=self.generation(),
                                          headers={"Content-Length": str(main.MAX_BODY_BYTES + 1)})
        self.assertEqual(response.status_code, 413)


if __name__ == "__main__":
    unittest.main(verbosity=2)
