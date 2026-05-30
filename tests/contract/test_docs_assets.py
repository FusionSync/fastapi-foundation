from fastapi.testclient import TestClient

from core.app import create_app
from core.config import Settings


def test_docs_use_swagger_package_local_assets() -> None:
    client = TestClient(create_app(Settings()))

    response = client.get("/docs")

    assert response.status_code == 200
    assert "cdn.jsdelivr.net" not in response.text
    assert "swagger-ui-dist" not in response.text
    assert 'href="/docs/static/swagger-ui.css"' in response.text
    assert 'src="/docs/static/swagger-ui-bundle.js"' in response.text
    assert "script-src 'self' 'unsafe-inline'" in response.headers[
        "Content-Security-Policy"
    ]


def test_swagger_package_static_assets_are_served() -> None:
    client = TestClient(create_app(Settings()))

    css_response = client.get("/docs/static/swagger-ui.css")
    js_response = client.get("/docs/static/swagger-ui-bundle.js")

    assert css_response.status_code == 200
    assert "text/css" in css_response.headers["content-type"]
    assert js_response.status_code == 200
    assert "javascript" in js_response.headers["content-type"]


def test_regular_routes_keep_strict_csp() -> None:
    client = TestClient(create_app(Settings()))

    response = client.get("/version")

    assert response.status_code == 200
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
    )
