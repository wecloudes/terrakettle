"""Report push: roundtrip, status counts, and auth failures."""

from conftest import (make_project, mint_token, push_files, token_headers)


REPORT = [
    {"unit": "a", "status": "drift"},
    {"unit": "b", "status": "clean"},
    {"unit": "c", "status": "error"},
]


def test_push_roundtrip_and_counts(client):
    make_project(client, "push", "Push")
    token = mint_token(client, "push")

    resp = client.post(
        "/api/v1/runs",
        files=push_files(REPORT, html="<html>report</html>",
                         data_js="window.DATA={}"),
        data={"run_id": "run-001"},
        headers=token_headers(token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["run_id"] == "run-001"
    assert body["project"] == "push"
    assert body["total"] == 3
    assert body["drift"] == 1
    assert body["clean"] == 1
    assert body["error"] == 1
    assert body["timeout"] == 0
    # html was pushed, so a report_url is present.
    assert body["report_url"] is not None
    assert body["report_url"].endswith("/p/push/runs/run-001/")


def test_push_run_id_from_filename(client):
    """When run_id form field is omitted, it derives from the report file."""
    make_project(client, "fname", "Fname")
    token = mint_token(client, "fname")
    files = push_files([{"unit": "u", "status": "clean"}])
    # report file is named "report.json" -> stem "report"
    resp = client.post("/api/v1/runs", files=files,
                       headers=token_headers(token))
    assert resp.status_code == 201, resp.text
    assert resp.json()["run_id"] == "report"


def test_push_no_auth(client):
    make_project(client, "noauth", "NoAuth")
    resp = client.post("/api/v1/runs", files=push_files(REPORT),
                       data={"run_id": "r"})
    assert resp.status_code == 401


def test_push_bad_token(client):
    make_project(client, "badtok", "BadTok")
    resp = client.post("/api/v1/runs", files=push_files(REPORT),
                       data={"run_id": "r"},
                       headers=token_headers("tk_bogus_nope"))
    assert resp.status_code == 403


def test_push_invalid_json(client):
    make_project(client, "badjson", "BadJson")
    token = mint_token(client, "badjson")
    files = {"report": ("report.json", b"not json", "application/json")}
    resp = client.post("/api/v1/runs", files=files, data={"run_id": "r"},
                       headers=token_headers(token))
    assert resp.status_code == 400
