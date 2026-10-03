import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.main import CODE_LENGTH, create_app

TARGET_URL = "https://example.com/some/very/long/path?with=query&more=1"


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def client(db_path):
    app = create_app(db_path=str(db_path))
    with TestClient(app) as test_client:
        yield test_client


def create_link(client, url=TARGET_URL):
    response = client.post("/links", json={"url": url})
    assert response.status_code == 201, response.text
    return response.json()


def stored_rows(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute("SELECT code, url, clicks FROM links").fetchall()
    finally:
        conn.close()


def test_create_link_returns_201_with_short_code(client):
    body = create_link(client)

    assert body["url"] == TARGET_URL
    assert len(body["code"]) == CODE_LENGTH
    assert body["clicks"] == 0
    assert body["short_url"].endswith("/" + body["code"])
    assert body["created_at"]


def test_create_link_accepts_basic_http_variants(client):
    for url in (
        "http://example.com",
        "https://example.com:8443/a/b#frag",
        "http://example.com/?q=1",
    ):
        response = client.post("/links", json={"url": url})
        assert response.status_code == 201, response.text
        assert response.json()["url"] == url


def test_redirect_returns_302_with_location(client):
    body = create_link(client)

    response = client.get(f"/{body['code']}", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == TARGET_URL


def test_redirect_increments_clicks_every_time(client):
    body = create_link(client)

    for expected in (1, 2, 3):
        assert client.get(f"/{body['code']}", follow_redirects=False).status_code == 302
        stats = client.get(f"/links/{body['code']}").json()
        assert stats["clicks"] == expected


def test_unknown_code_returns_404_not_200(client):
    redirect = client.get("/zzzzzzz", follow_redirects=False)
    stats = client.get("/links/zzzzzzz")

    assert redirect.status_code == 404
    assert stats.status_code == 404
    assert redirect.json()["detail"]


@pytest.mark.parametrize(
    "bad_url",
    [
        "",
        "   ",
        "not a url",
        "example.com",
        "/relative/path",
        "ftp://example.com/file",
        "javascript:alert(1)",
        "http://",
        "https://",
        "data:text/plain,hi",
    ],
)
def test_malformed_url_returns_400_and_is_not_stored(client, db_path, bad_url):
    response = client.post("/links", json={"url": bad_url})

    assert response.status_code == 400
    assert response.json()["detail"]
    assert stored_rows(db_path) == []


def test_overlong_url_returns_400(client, db_path):
    response = client.post("/links", json={"url": "https://example.com/" + "a" * 5000})

    assert response.status_code == 400
    assert stored_rows(db_path) == []


def test_missing_url_field_is_rejected(client, db_path):
    response = client.post("/links", json={})

    assert response.status_code == 422
    assert stored_rows(db_path) == []


def test_stats_endpoint_reports_link(client):
    body = create_link(client)

    stats = client.get(f"/links/{body['code']}").json()

    assert stats["code"] == body["code"]
    assert stats["url"] == TARGET_URL
    assert stats["clicks"] == 0


def test_generated_codes_are_unique(client):
    codes = {create_link(client, f"https://example.com/page/{i}")["code"] for i in range(30)}

    assert len(codes) == 30


def test_same_url_twice_returns_the_same_code(client, db_path):
    first = create_link(client)

    again = client.post("/links", json={"url": TARGET_URL})

    assert again.status_code == 200
    assert again.json()["code"] == first["code"]
    assert again.json()["clicks"] == first["clicks"]
    assert len(stored_rows(db_path)) == 1


def test_repeat_submission_is_not_silent_and_keeps_counts(client, db_path):
    first = create_link(client)
    client.get(f"/{first['code']}", follow_redirects=False)
    client.get(f"/{first['code']}", follow_redirects=False)

    again = client.post("/links", json={"url": TARGET_URL})

    assert again.status_code == 200
    assert again.json()["clicks"] == 2
    assert len(stored_rows(db_path)) == 1
    assert client.get(f"/links/{first['code']}").json()["clicks"] == 2


def test_same_code_not_reused_for_different_urls(client):
    first = create_link(client, "https://example.com/a")
    second = create_link(client, "https://example.com/b")

    assert first["code"] != second["code"]


def test_startup_merges_duplicate_urls_from_legacy_schema(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE links (code TEXT PRIMARY KEY, url TEXT NOT NULL,"
        " clicks INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO links VALUES ('old1', ?, 2, '2026-01-01T00:00:00+00:00')", (TARGET_URL,)
    )
    conn.execute(
        "INSERT INTO links VALUES ('old2', ?, 3, '2026-01-02T00:00:00+00:00')", (TARGET_URL,)
    )
    conn.commit()
    conn.close()

    app = create_app(db_path=str(db_path))
    with TestClient(app) as restarted:
        rows = stored_rows(db_path)
        response = restarted.post("/links", json={"url": TARGET_URL})

    assert [row[0] for row in rows] == ["old1"]
    assert rows[0][2] == 5
    assert response.status_code == 200
    assert response.json()["code"] == "old1"
    assert response.json()["clicks"] == 5


def test_root_reports_service_name(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.json()["name"] == "ShortLink"


def test_cors_allows_any_browser_origin_by_default(client):
    response = client.get("/", headers={"Origin": "http://localhost:3000"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"


def test_cors_headers_present_on_redirect(client):
    body = create_link(client)

    response = client.get(
        f"/{body['code']}", headers={"Origin": "http://localhost:3000"}, follow_redirects=False
    )

    assert response.status_code == 302
    assert response.headers["access-control-allow-origin"] == "*"
    assert "location" in response.headers["access-control-expose-headers"].lower()


def test_cors_preflight_for_create(client):
    response = client.options(
        "/links",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_cors_restricted_to_configured_origins(tmp_path):
    app = create_app(
        db_path=str(tmp_path / "test.db"),
        cors_origins=["http://localhost:3000"],
    )
    with TestClient(app) as restricted:
        allowed = restricted.get("/", headers={"Origin": "http://localhost:3000"})
        denied = restricted.get("/", headers={"Origin": "http://evil.example"})

    assert allowed.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-origin" not in denied.headers


def test_openapi_documents_302_not_307(client):
    spec = client.get("/openapi.json").json()
    responses = spec["paths"]["/{code}"]["get"]["responses"]

    assert "302" in responses
    assert "307" not in responses
    assert "404" in responses
    assert responses["302"]["description"] != "Successful Response"
    assert "location" in responses["302"]["headers"]


def test_openapi_documents_error_codes(client):
    spec = client.get("/openapi.json").json()

    create_responses = spec["paths"]["/links"]["post"]["responses"]
    assert "201" in create_responses
    assert "400" in create_responses

    stats_responses = spec["paths"]["/links/{code}"]["get"]["responses"]
    assert "404" in stats_responses


def test_documented_status_codes_match_real_responses(client):
    body = create_link(client)

    redirect = client.get(f"/{body['code']}", follow_redirects=False)
    unknown = client.get("/doesnotexist", follow_redirects=False)
    bad = client.post("/links", json={"url": "javascript:alert(1)"})

    assert (redirect.status_code, unknown.status_code, bad.status_code) == (302, 404, 400)
