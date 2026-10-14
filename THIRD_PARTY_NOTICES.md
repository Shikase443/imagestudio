# 依存ソフト・モデルのライセンス

このリポジトリのMIT Licenseは自作コードとレシピに適用されます。外部ソフトやモデルを同じライセンスで再許諾するものではありません。

| 使用するもの | 配布元・条件 |
|---|---|
| stable-diffusion.cpp | [MIT License](https://github.com/leejet/stable-diffusion.cpp/blob/master/LICENSE) |
| llama.cpp | [MIT License](https://github.com/ggml-org/llama.cpp/blob/master/LICENSE) |
| CUDAベースイメージ | [NVIDIA CUDAのライセンス](https://docs.nvidia.com/cuda/eula/index.html) |
| Pythonベースイメージ | [Pythonのライセンス](https://docs.python.org/3/license.html) |
| 生成モデルのGGUF | [AtomicChat配布元](https://huggingface.co/AtomicChat/Qwen-Image-2.1-Turbo-GGUF)、[Qwen Research License](https://huggingface.co/Qwen/Qwen-Image-2.1-Turbo/blob/main/LICENSE) |
| テキストエンコーダー・解析モデル | [AtomicChat配布元](https://huggingface.co/AtomicChat/Qwen-Image-2.1-Turbo-Uncensored-GGUF)、Apache-2.0（Qwen3-VL由来） |
| VAE | [Comfy-Org配布元](https://huggingface.co/Comfy-Org/Qwen-Image-2.1)、[Qwen Research License](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE) |

Python依存の固定バージョンは `gateway/requirements.lock.txt` にあります。各パッケージのライセンスは配布元およびインストールされたパッケージのライセンス情報を確認してください。OSパッケージとビルド依存も、それぞれの条件に従います。

バックエンドをビルドするDockerfileは、取得したソースのLICENSEを実行イメージ内の `/usr/local/share/sd-build/licenses` または `/usr/local/share/llama-build/licenses` に残します。完成イメージを再配布する場合は、そのイメージに含まれる依存物全体の表示・配布条件も確認してください。

モデルは同梱しません。利用者が `config/models.json` に記録した配布元・リビジョンから取得します。量子化されていても元のモデルの条件は変わりません。生成モデルの非商用条件は、レシピを公開することと、モデルを商用利用することを分けて判断してください。

製品名はImage Studioです。モデル名・APIモデルID・依存ソフト名は対応するものを説明するために使用しています。各提供元との提携・公式承認を示すものではありません。
