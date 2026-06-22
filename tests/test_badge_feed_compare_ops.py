"""Smoke tests: badge, feed, compare, healthz, metrics."""

import pytest

from conftest import (make_project, mint_token, push_files, token_headers)


def _seed(client, slug="s"):
    make_project(client, slug, slug.title())
    token = mint_token(client, slug)
    client.post(
        "/api/v1/runs",
        files=push_files([{"unit": "u", "status": "drift"}], html="<html>x</html>"),
        data={"run_id": "run-a"},
        headers=token_headers(token),
    )
    return token


def test_badge_svg(client):
    _seed(client, "badge")
    resp = client.get("/p/badge/badge.svg")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/svg+xml"
    assert "<svg" in resp.text


def test_badge_no_data(client):
    make_project(client, "empty", "Empty")
    resp = client.get("/p/empty/badge.svg")
    assert resp.status_code == 200
    assert "no data" in resp.text


def test_feed_json(client):
    _seed(client, "feedj")
    resp = client.get("/p/feedj/feed.json")
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    body = resp.json()
    assert body["project"] == "feedj"
    assert len(body["items"]) == 1
    assert body["items"][0]["run_id"] == "run-a"


def test_feed_xml(client):
    _seed(client, "feedx")
    resp = client.get("/p/feedx/feed.xml")
    assert resp.status_code == 200
    assert "xml" in resp.headers["content-type"]
    assert "<rss" in resp.text


def test_compare_no_target(client):
    _seed(client, "cmp")
    resp = client.get("/p/cmp/compare")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_compare_with_target(client):
    token = _seed(client, "cmp2")
    # Add a second run so the comparison has a base.
    client.post(
        "/api/v1/runs",
        files=push_files([{"unit": "u", "status": "clean"}]),
        data={"run_id": "run-b"},
        headers=token_headers(token),
    )
    resp = client.get("/p/cmp2/compare", params={"target": "run-b"})
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db"] is True
    assert body["storage"] is True


def test_metrics_build_info(client):
    """build_info is exposed even with no projects (zero-project path)."""
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "terrakettle_build_info" in resp.text


def test_metrics_with_projects(client):
    _seed(client, "metr")
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "terrakettle_projects_total" in resp.text
    assert "terrakettle_project_last_drift" in resp.text
