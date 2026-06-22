#!/usr/bin/env python3
"""Push a Terrahawk report to a Terrakettle server (stdlib only).

Intended to run in CI right after `terrahawk`, e.g.:

    python3 push.py \
        --url https://terrakettle.example.com \
        --token "$TERRAKETTLE_TOKEN" \
        --results-dir terrahawk_results

It finds the newest report triple (.json / .html / _data.js) in the results
directory and uploads it. Uses only the Python standard library so it can run
inside the Terrahawk Docker image without extra dependencies.
"""

import argparse
import glob
import mimetypes
import os
import sys
import urllib.request
import uuid


def _newest_report(results_dir):
    jsons = sorted(glob.glob(os.path.join(results_dir, "terrahawk_*.json")))
    if not jsons:
        sys.exit(f"No terrahawk_*.json found in {results_dir}")
    return jsons[-1]


def _multipart(fields, files):
    """Build a multipart/form-data body. files: list of (field, path, ctype)."""
    boundary = uuid.uuid4().hex
    body = bytearray()
    for name, value in fields.items():
        if value is None:
            continue
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += f"{value}\r\n".encode()
    for name, path, ctype in files:
        fname = os.path.basename(path)
        with open(path, "rb") as f:
            data = f.read()
        body += f"--{boundary}\r\n".encode()
        body += (f'Content-Disposition: form-data; name="{name}"; '
                 f'filename="{fname}"\r\n').encode()
        body += f"Content-Type: {ctype}\r\n\r\n".encode()
        body += data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return f"multipart/form-data; boundary={boundary}", bytes(body)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="Terrakettle base URL")
    ap.add_argument("--token", default=os.environ.get("TERRAKETTLE_TOKEN"),
                    help="Per-project push token (or $TERRAKETTLE_TOKEN)")
    ap.add_argument("--results-dir", default="terrahawk_results")
    ap.add_argument("--report", help="Specific report .json (default: newest)")
    args = ap.parse_args()

    if not args.token:
        sys.exit("Push token required (--token or $TERRAKETTLE_TOKEN)")

    json_path = args.report or _newest_report(args.results_dir)
    stem = os.path.basename(json_path)[:-len(".json")]
    d = os.path.dirname(json_path)
    html_path = os.path.join(d, f"{stem}.html")
    data_js_path = os.path.join(d, f"{stem}_data.js")

    files = [("report", json_path, "application/json")]
    if os.path.exists(html_path):
        files.append(("html", html_path, "text/html"))
    if os.path.exists(data_js_path):
        files.append(("data_js", data_js_path, "application/javascript"))

    ctype, body = _multipart({"run_id": stem}, files)
    req = urllib.request.Request(
        args.url.rstrip("/") + "/api/v1/runs", data=body, method="POST",
        headers={"Authorization": f"Bearer {args.token}", "Content-Type": ctype},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print(resp.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"Push failed: {e.code} {e.read().decode()}")


if __name__ == "__main__":
    main()
