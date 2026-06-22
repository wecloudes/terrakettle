"""Project CRUD, admin auth failures, and token mint/list/revoke."""

from conftest import admin_headers, make_project, mint_token


def test_create_project_ok(client):
    resp = make_project(client, "alpha", "Alpha")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {"slug": "alpha", "name": "Alpha"}


def test_create_project_wrong_admin_key(client):
    resp = client.post("/api/v1/projects", json={"slug": "x", "name": "X"},
                       headers={"Authorization": "Bearer wrong-key"})
    assert resp.status_code == 403


def test_create_project_missing_auth(client):
    resp = client.post("/api/v1/projects", json={"slug": "x", "name": "X"})
    assert resp.status_code == 401


def test_create_project_duplicate_slug(client):
    assert make_project(client, "dup", "Dup").status_code == 200
    resp = make_project(client, "dup", "Dup again")
    assert resp.status_code == 409


def test_delete_project(client):
    make_project(client, "gone", "Gone")
    resp = client.delete("/api/v1/projects/gone", headers=admin_headers())
    assert resp.status_code == 204
    # The project page should now 404.
    assert client.get("/p/gone").status_code == 404


def test_token_mint_list_revoke_roundtrip(client):
    make_project(client, "tok", "Tok")

    # Mint with a label.
    mint = client.post("/api/v1/projects/tok/tokens",
                       data={"label": "ci"}, headers=admin_headers())
    assert mint.status_code == 200, mint.text
    minted = mint.json()
    assert minted["project"] == "tok"
    assert minted["token"].startswith("tk_tok_")

    # List: secret never returned, but id + label are.
    listed = client.get("/api/v1/projects/tok/tokens", headers=admin_headers())
    assert listed.status_code == 200
    rows = listed.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["label"] == "ci"
    assert "id" in row
    assert "token" not in row and "token_hash" not in row

    # Revoke by id -> 204, list now empty.
    tid = row["id"]
    rev = client.delete(f"/api/v1/projects/tok/tokens/{tid}",
                        headers=admin_headers())
    assert rev.status_code == 204
    assert client.get("/api/v1/projects/tok/tokens",
                      headers=admin_headers()).json() == []


def test_token_mint_no_label(client):
    make_project(client, "nolabel", "NoLabel")
    token = mint_token(client, "nolabel")
    assert token.startswith("tk_nolabel_")


def test_mint_token_unknown_project(client):
    resp = client.post("/api/v1/projects/ghost/tokens",
                       data={}, headers=admin_headers())
    assert resp.status_code == 404
