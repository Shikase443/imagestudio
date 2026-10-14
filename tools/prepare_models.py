"""固定マニフェストに従ってモデルを取得し、サイズとSHA256を検証する。"""

import hashlib
import argparse
import json
import os
from pathlib import Path
import time
import urllib.request

ROOT = Path("/models")
CHUNK = 8 * 1024 * 1024


def log(event, **values):
    # バイト数は実測の累計値。取得内容やモデルの中身はログへ出さない。
    print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)


def verify(path, item):
    size = path.stat().st_size
    if size != item["size"]:
        raise ValueError(f"容量不一致: {path.name}: {size} != {item['size']}")
    digest = hashlib.sha256()
    processed = 0
    started = last = time.monotonic()
    with path.open("rb") as source:
        while data := source.read(CHUNK):
            digest.update(data)
            processed += len(data)
            now = time.monotonic()
            if now - last >= 30:
                log("検証進捗", file=item["path"], bytes=processed,
                    total_bytes=size, percent=round(processed / size * 100, 2),
                    elapsed_seconds=round(now - started, 2))
                last = now
    actual = digest.hexdigest()
    if actual != item["sha256"]:
        raise ValueError(f"SHA256不一致: {path.name}: {actual}")
    log("検証成功", file=item["path"], bytes=size, sha256=actual,
        elapsed_seconds=round(time.monotonic() - started, 2))
    return {**item, "verified_size": size, "verified_sha256": actual}


def download(item):
    target = ROOT / item["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        # 既存の完成ファイルを上書きせず、検証して再利用する。
        return verify(target, item)
    partial = target.with_name(target.name + ".part")
    started = time.monotonic()
    for attempt in range(1, 5):
        offset = partial.stat().st_size if partial.exists() else 0
        if offset == item["size"]:
            break
        if offset > item["size"]:
            raise ValueError(f"途中ファイルが予定サイズを超えています: {partial.name}")
        url = (f"https://huggingface.co/{item['repo']}/resolve/{item['revision']}/"
               f"{item['path']}?download=true&request_time={time.time_ns()}")
        headers = {"User-Agent": "qwenimage-model-preparation/1"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers),
                                        timeout=60) as response:
                if offset and (response.status != 206 or not response.headers.get(
                        "Content-Range", "").startswith(f"bytes {offset}-")):
                    raise RuntimeError("サーバーが正しい位置からの再開に対応しませんでした")
                log("取得開始", file=item["path"], attempt=attempt,
                    bytes=offset, total_bytes=item["size"])
                last = time.monotonic()
                with partial.open("ab" if offset else "wb") as destination:
                    while data := response.read(CHUNK):
                        destination.write(data)
                        offset += len(data)
                        if offset > item["size"]:
                            raise ValueError("取得データが予定サイズを超えました")
                        now = time.monotonic()
                        if now - last >= 30:
                            log("取得進捗", file=item["path"], bytes=offset,
                                total_bytes=item["size"],
                                percent=round(offset / item["size"] * 100, 2),
                                elapsed_seconds=round(now - started, 2))
                            last = now
                    destination.flush()
                    os.fsync(destination.fileno())
            if offset != item["size"]:
                raise RuntimeError(f"取得が途中で終了: {offset}/{item['size']}")
            break
        except Exception as error:
            log("取得エラー", file=item["path"], attempt=attempt,
                cause=str(error), elapsed_seconds=round(time.monotonic() - started, 2))
            if attempt == 4:
                raise
            time.sleep(3)
    result = verify(partial, item)
    # 検証成功したファイルだけをロード可能な最終名に変更する。
    partial.replace(target)
    log("取得完了", file=item["path"], bytes=result["verified_size"],
        elapsed_seconds=round(time.monotonic() - started, 2))
    return result


if __name__ == "__main__":
    # 追加量子化の試験でも本番用マニフェスト・検証結果を上書きしない。
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--verification", default="verification.json")
    args = parser.parse_args()
    manifest = json.loads((ROOT / args.manifest).read_text())
    results = [download(item) for item in manifest["files"]]
    (ROOT / args.verification).write_text(
        json.dumps({"files": results}, ensure_ascii=False, indent=2) + "\n")
    log("全モデル検証成功", file_count=len(results),
        total_bytes=sum(item["verified_size"] for item in results))
