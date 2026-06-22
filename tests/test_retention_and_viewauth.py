"""Retention pruning and view-password session gating.

Both need a separately-configured app, built via the ``make_client`` factory
(which clears the settings lru_cache and the storage singleton per build).
"""

from conftest import (make_project, mint_token, push_files, token_headers)


def test_retention_keeps_only_max_runs(make_client):
    client = make_client(MAX_RUNS_PER_PROJECT=2)
    make_project(client, "ret", "Ret")
    token = mint_token(client, "ret")

    for n in (1, 2, 3):
        resp = client.post(
            "/api/v1/runs",
            files=push_files([{"unit": "u", "status": "clean"}]),
            data={"run_id": f"run-{n}"},
            headers=token_headers(token),
        )
        assert resp.status_code == 201, resp.text

    # Verify via feed (machine-readable) that only the newest 2 remain.
    feed = client.get("/p/ret/feed.json").json()
    remaining = {item["run_id"] for item in feed["items"]}
    assert remaining == {"run-2", "run-3"}, remaining
    # And the project page renders without the pruned run.
    page = client.get("/p/ret")
    assert page.status_code == 200
    assert "run-1" not in page.text


def test_view_auth_redirects_without_session(make_client):
    client = make_client(VIEW_PASSWORD="secret")
    make_project(client, "secured", "Secured")

    # TestClient follows redirects by default; disable to observe the 302.
    resp = client.get("/p/secured", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["location"]


def test_view_auth_login_then_access(make_client):
    client = make_client(VIEW_PASSWORD="secret")
    make_project(client, "secured", "Secured")

    # Wrong password -> 401, no session granted.
    bad = client.post("/login", data={"password": "nope", "next": "/p/secured"},
                      follow_redirects=False)
    assert bad.status_code == 401

    # Right password -> redirect + session cookie set on the client jar.
    good = client.post("/login",
                       data={"password": "secret", "next": "/p/secured"},
                       follow_redirects=False)
    assert good.status_code in (302, 303)
    assert "tk_session" in good.cookies or "tk_session" in client.cookies

    # Now the gated page is reachable.
    page = client.get("/p/secured")
    assert page.status_code == 200


def test_view_auth_public_paths_open(make_client):
    """healthz and badge stay reachable (no 302) even with view auth on.

    (/metrics is also a public path, but exercising it here would trip the
    metrics sqlite3.Row bug since a project exists; see test_metrics_*.)
    """
    client = make_client(VIEW_PASSWORD="secret")
    make_project(client, "pub", "Pub")
    assert client.get("/healthz", follow_redirects=False).status_code == 200
    assert client.get("/p/pub/badge.svg", follow_redirects=False).status_code == 200
