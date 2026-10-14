# API


接続先は `http://localhost:8000`。OpenAI互換クライアントの base_url は `http://localhost:8000/v1` です。
認証トークンは不要です。Authorizationヘッダーなしで呼び出せます。OpenAI SDKがapi_keyの指定を要求する場合は、任意の文字列（例：unused）を指定してください。

| 用途 | メソッド・パス | モデル |
|---|---|---|
| 稼働確認 | GET `/health` | 不要 |
| モデル一覧 | GET `/v1/models` | 不要 |
| 画像生成 | POST `/v1/images/generations` | `qwen-image-2.1-turbo` |
| 画像編集 | POST `/v1/images/edits` | `qwen-image-2.1-turbo` |
| 2画像合成 | POST `/v1/images/compositions` | `qwen-image-2.1-turbo` |
| 簡易画像解析 | POST `/v1/images/analyze` | `qwen-image-analysis` |
| OpenAI互換画像解析・チャット | POST `/v1/chat/completions` | `qwen-image-analysis` |

生成・編集は8ステップ、Euler、CFG=1、検証済みTurbo用sigma固定。エンコーダーと解析モデルはQ8_0です。

以下はLinuxシェルの例です。

```sh
curl http://localhost:8000/v1/images/generations \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen-image-2.1-turbo","prompt":"A red teapot and a white cup on a wooden table","size":"1024x1024","n":1,"seed":42}'

curl http://localhost:8000/v1/images/edits \
  -F model=qwen-image-2.1-turbo -F image=@input.png \
  -F 'prompt=Change the teapot to blue. Keep the cup white.' -F size=1024x1024
```

生成・編集の応答は `{"created":...,"data":[{"b64_json":"..."}]}`。`b64_json` をBase64復号するとPNGになります。
編集にはPNGまたはJPEGを1枚指定します。マスク編集は未対応です。

2画像合成は追加した独立APIです。既存の生成・編集・解析APIの仕様は変更していません。`image1` と `image2` を各1枚、PNGまたはJPEGで指定します。各画像は16MiBまで、multipart本文全体は32MiBまでです。応答は生成・編集と同じPNGの `b64_json` です。

```sh
curl http://localhost:8000/v1/images/compositions \
  -F image1=@first.png -F image2=@second.png \
  -F 'prompt=Place the person from image 1 in the landscape from image 2.' \
  -F size=1024x1024 -F seed=42
```

画像解析は `/v1/chat/completions` に標準の `messages` を送ります。画像は `image_url.url` に `data:image/png;base64,...` またはJPEGのdata URIを指定してください。HTTP画像URLは未対応です。

```json
{
  "model": "qwen-image-analysis",
  "messages": [{"role": "user", "content": [
    {"type": "text", "text": "画像を日本語で説明してください。"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,<画像のBase64>"}}
  ]}],
  "max_tokens": 256,
  "stream": false
}
```

`stream:true` はOpenAI互換SSEです。`response_format` の `json_object`、`json_schema` に対応します。JSON指定のSSEは全体を検証してから送るため、最初の応答まで待ち時間があります。

簡易解析は `{"image":"<Base64またはdata URI>","prompt":"画像を説明してください","format":"text"}` を `/v1/images/analyze` に送ります。`format:"json"` と `schema` で構造化解析を指定できます。

同時推論は生成・解析を合わせて1件です。実行中の追加要求は429と `Retry-After: 2` を返します。生成・編集の `n` は1、サイズは各辺64〜2048の64の倍数、画像入力は16MiBまで、要求本文は32MiBまでです。

APIの待ち時間上限は600秒。タイムアウトやクライアント切断後もバックエンド処理が完了するまで同時実行制限を保持します。バックエンド停止時は503、異常な応答は502を返します。
