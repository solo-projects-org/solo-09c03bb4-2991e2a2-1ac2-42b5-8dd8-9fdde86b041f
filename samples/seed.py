#!/usr/bin/env python3
"""把 samples/lamps.json 逐条登记到运行中的 API（仅用标准库）。

用法:
    python3 samples/seed.py [--base-url http://127.0.0.1:8000] [--file samples/lamps.json]
已登记的灯号会跳过，可重复执行。
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def post(base_url: str, path: str, payload: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="登记样例灯具")
    parser.add_argument("--base-url", default=os.environ.get("LAMPS_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--file", default=os.path.join(os.path.dirname(__file__), "lamps.json"))
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as f:
        lamps = json.load(f)

    ok = skipped = 0
    for lamp in lamps:
        status, body = post(args.base_url, "/lamps", lamp)
        if status == 201:
            ok += 1
            print(f"[OK ] {lamp['lamp_id']} 登记成功 -> {body}")
        elif status == 409:
            skipped += 1
            print(f"[SKIP] {lamp['lamp_id']} 已登记: {body.get('detail')}")
        else:
            print(f"[ERR] {lamp['lamp_id']} HTTP {status}: {body}", file=sys.stderr)
            return 1

    print(f"\n完成：新登记 {ok} 盏，跳过已存在 {skipped} 盏，共 {len(lamps)} 条")
    print(f"Swagger 文档: {args.base_url}/docs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
