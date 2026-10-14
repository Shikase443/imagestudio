"""WebUIのAPI中継を実バックエンドなしで検証する。"""
import unittest
import httpx
import server


class WebUITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.context = server.lifespan(server.app)
        await self.context.__aenter__()
        await server.app.state.client.aclose()
        self.requests = []
        self.failure = None
        self.status = 200
        server.app.state.client = httpx.AsyncClient(base_url="http://image-api:8000",
                                                   transport=httpx.MockTransport(self.backend))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url="http://ui.test")

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.context.__aexit__(None, None, None)

    def backend(self, request):
        self.requests.append(request)
        if self.failure:
            raise self.failure("simulated", request=request)
        return httpx.Response(self.status, content=b'data: {"choices":[]}\n\ndata: [DONE]\n\n',
                              headers={"content-type": "text/event-stream", "x-request-id": "test-id", "retry-after": "2"})

    async def test_static_page_and_readiness(self):
        self.assertEqual((await self.client.get("/ready")).status_code, 200)
        self.assertIn("Image Studio", (await self.client.get("/")).text)
        self.assertEqual((await self.client.get("/app.js")).status_code, 200)

    async def test_stream_and_multipart_are_preserved(self):
        response = await self.client.post("/api/v1/chat/completions", content=b'{"stream":true}',
                                          headers={"Content-Type": "application/json"})
        self.assertIn("[DONE]", response.text)
        self.assertEqual(response.headers["x-request-id"], "test-id")
        self.assertEqual(self.requests[-1].content, b'{"stream":true}')
        await self.client.post("/api/v1/images/edits", files={"image": ("sample.png", b"fixture", "image/png")},
                               data={"prompt": "make blue"})
        self.assertIn(b"make blue", self.requests[-1].content)
        self.assertIn("boundary=", self.requests[-1].headers["content-type"])

    async def test_errors_and_busy_status_are_preserved(self):
        self.status = 429
        response = await self.client.post("/api/v1/images/generations", json={"prompt": "test"})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["retry-after"], "2")
        for failure, status in ((httpx.ConnectError, 503), (httpx.ReadTimeout, 504)):
            self.failure = failure
            self.assertEqual((await self.client.get("/api/health")).status_code, status)

    async def test_composition_proxy(self):
        response = await self.client.post("/api/v1/images/compositions", data={"prompt": "combine"},
            files={"image1": ("first.png", b"first-image", "image/png"), "image2": ("second.jpg", b"second-image", "image/jpeg")})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.requests[-1].url.path, "/v1/images/compositions")
        self.assertIn(b'name="image1"', self.requests[-1].content)
        self.assertIn(b'name="image2"', self.requests[-1].content)

    async def test_restricted_paths_and_body_limit(self):
        self.assertEqual((await self.client.get("/api/arbitrary")).status_code, 404)
        self.assertEqual((await self.client.post("/api/health")).status_code, 404)
        self.assertEqual(self.requests, [])
        limit = server.LIMIT
        try:
            server.LIMIT = 4
            self.assertEqual((await self.client.post("/api/v1/images/generations", content=b"12345")).status_code, 413)
            self.assertEqual(self.requests, [])
        finally:
            server.LIMIT = limit
