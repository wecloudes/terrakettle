"""Web views: listings, run detail, report/file serving, traversal 404."""

from conftest import (make_project, mint_token, push_files, token_headers)


def _seed_run(client, slug="web", run_id="run-1", *, html="<html>R</html>"):
    make_project(client, slug, slug.title())
    token = mint_token(client, slug)
    resp = client.post(
        "/api/v1/runs",
        files=push_files([{"unit": "u", "status": "drift"}],
                         html=html, data_js="X=1"),
        data={"run_id": run_id},
        headers=token_headers(token),
    )
    assert resp.status_code == 201, resp.text
    return run_id


def test_index_ok(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_project_page_ok(client):
    _seed_run(client, "web")
    resp = client.get("/p/web")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_project_page_unknown_404(client):
    assert client.get("/p/nope").status_code == 404


def test_run_detail_wrapper(client):
    rid = _seed_run(client, "web")
    resp = client.get(f"/p/web/runs/{rid}")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_run_report_html_served(client):
    rid = _seed_run(client, "web", html="<html><body>HELLO_REPORT</body></html>")
    resp = client.get(f"/p/web/runs/{rid}/")
    assert resp.status_code == 200
    assert "HELLO_REPORT" in resp.text


def test_run_json_file_served(client):
    rid = _seed_run(client, "web")
    resp = client.get(f"/p/web/runs/{rid}/{rid}.json")
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["unit"] == "u"


def test_run_unknown_file_404(client):
    """A filename not recorded for the run must 404 (no traversal/leak)."""
    rid = _seed_run(client, "web")
    resp = client.get(f"/p/web/runs/{rid}/secret.txt")
    assert resp.status_code == 404


def test_run_unknown_run_404(client):
    _seed_run(client, "web")
    assert client.get("/p/web/runs/does-not-exist").status_code == 404
