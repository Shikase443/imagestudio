# Image Studio

NVIDIA DGX Spark向けの画像AIサービスです。画像の生成・編集・2画像合成・解析を、日本語WebUIとHTTP APIから利用できます。WebUIはスマートフォンにも対応します。

モデル本体は同梱しません。利用者が配布元から取得し、依存パッケージの導入・ビルド・実行はすべてコンテナ内で行います。

## 動作条件

DGX Spark（GB10・共有メモリ128GB）での利用を前提としています。Linux ARM64、CUDA 13、Compute Capability 12.1（SM 121a）に固定したGPUレシピです。Docker ComposeとNVIDIA Container Toolkitが利用できる環境が必要です。

Macや他のGPU向けの構成は含みません。DGX Spark以外での動作は保証していません。

モデルの合計容量は約17.3GBです。CUDAのビルドキャッシュも含めたディスク容量と、生成・解析モデルを同時に保持できるGPUメモリ／共有メモリを確保してください。依存パッケージのインストールとモデルの実行はコンテナ内で行います。

## 起動

リポジトリのルートで実行します。

```sh
mkdir -p models output/telemetry
printf 'HOST_UID=%s\nHOST_GID=%s\nAPI_PORT=8000\nWEBUI_PORT=8080\n' "$(id -u)" "$(id -g)" > .env
```

`.env.example` は設定例です。`.env` はGitの対象外です。

モデルのライセンスを確認したうえで、利用者自身が配布元から取得します。固定リビジョン・容量・SHA256は `config/models.json` に記録しています。

```sh
docker compose --profile tools run --rm model-download
docker compose build image-gen image-vlm image-api
docker compose build image-webui
docker compose up -d --no-build
docker compose ps
```

| 用途 | DGX Spark上からの接続先 |
|---|---|
| WebUI | `http://localhost:8080` |
| API（OpenAI互換のbase_url） | `http://localhost:8000/v1` |

別端末から開く場合は、`localhost` をDGX Sparkのホスト名またはIPアドレスに置き換えてください。ポートを変更した場合は、`.env` の `WEBUI_PORT`・`API_PORT` に合わせます。

現在のAPIは認証トークンなしで利用できます。

## 使い方

- **生成**：文章から画像を作成します。
- **編集**：画像1枚の指定部分を変更します。入力画像の縦横比から出力サイズを自動選択します。
- **合成**：画像1を基準に、画像2から服・色・物体・背景・画風などを参照します。
- **解析**：画像について質問し、テキストまたはJSONで回答を受け取ります。

編集プリセットを選び、必要な対象や変更内容を入力すると指示文が入ります。文章は直接修正できます。部分編集は文章による指定で、マスク・ブラシによる領域指定は未対応です。

通常はランダムで生成し、結果に実際のシードを表示します。「このシードを再利用」で次の処理へ指定でき、詳細設定で手動入力やランダムへの復帰もできます。再現には入力画像・指示・サイズなども同じ条件にする必要があります。

入力画像と履歴は処理中も表示します。結果欄は待機エフェクトに切り替わり、完了後に新しい結果を表示します。履歴はブラウザ内に6枚まで保持され、ページを閉じるか再読み込みすると消えます。保存したい画像はPNGをダウンロードしてください。

## API

既存のモデルIDは互換性のため維持しています。生成・編集・合成は `qwen-image-2.1-turbo`、解析は `qwen-image-analysis` です。これは対応モデルを示す識別子で、製品名ではありません。

| 用途 | エンドポイント |
|---|---|
| 稼働状態 | GET `/health` |
| モデル一覧 | GET `/v1/models` |
| 生成 | POST `/v1/images/generations` |
| 1枚編集 | POST `/v1/images/edits` |
| 2画像合成 | POST `/v1/images/compositions` |
| 簡易解析 | POST `/v1/images/analyze` |
| OpenAI互換解析 | POST `/v1/chat/completions` |

要求・応答の例は [docs/API.md](docs/API.md) を参照してください。

同時推論は全体で1件です。実行中の追加要求には429を返します。画像入力は各16MiB、要求全体は32MiBまでです。生成条件は8ステップ・Euler・CFG=1・Turbo用sigma固定、エンコーダーと解析モデルはQ8_0です。

## 構成

```text
compose.yaml       コンテナ構成
gateway/           統合API
webui/             WebUIとAPI中継
image-gen/         生成バックエンドのビルド・起動
image-vlm/         解析バックエンドのビルド・起動
config/            モデル取得・検証設定（重みは含まない）
tools/             取得とメモリ観測
tests/             個別環境に依存しないテスト
docs/              APIの利用方法
models/            利用者が取得するモデル（Git対象外）
output/            実行時データ（Git対象外）
```

## 検証

API・WebUIのテストはモデルをロードせず、コンテナ内で実行できます。

```sh
docker compose run --rm --no-deps -v "$PWD/tests:/tests:ro" --entrypoint python image-api -B -m unittest discover -s /tests -p test_gateway.py
docker compose run --rm --no-deps -w /webui -v "$PWD/tests:/tests:ro" --entrypoint python image-webui -B -m unittest discover -s /tests -p test_webui.py
```

PNGメタデータのテストはNode.jsの標準機能のみを使います。

```sh
node --test tests/test_png_seed.mjs
```

## ライセンス

自作のAPI・WebUI・レシピは [MIT License](LICENSE) です。依存ソフト、CUDAイメージ、モデルのライセンスは別です。[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) を参照してください。

生成モデルはQwen Research Licenseで、研究・評価目的の非商用利用に限定されています。商用利用には配布元との別契約が必要です。モデルの重みはこのリポジトリで再配布しません。モデル取得・利用は利用者自身が各配布元の条件に従って行ってください。

Image Studioは独立したプロジェクトで、NVIDIAやモデル提供元の公式製品ではありません。DGX Sparkの名称は対応ハードウェアを説明するために使用しています。
