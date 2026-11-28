import os
from pathlib import Path

import pytest
from fastapi import HTTPException
from playwright.async_api import Browser

from app import server_reports
from app.grid import make_grid


def example_scan(keyword):
    return {
        "business_name": "Empresa <fixture>", "keyword": keyword,
        "status": "done", "finished": "2026-11-28", "grid_size": 7,
        "spacing_km": 1, "total": 49,
        "points": [
            {"lat": point.lat, "lng": point.lng, "rank": (point.row % 7) + 1, "status": "ok"}
            for point in make_grid(-23.5, -46.6, 7, 1)
        ],
    }


def test_consolidated_report_escapes_data_and_has_separate_maps():
    html = server_reports._report_html([example_scan(text) for text in ("café", "pães", "<script>")])
    assert all(f'id="map-{index}"' in html for index in range(3))
    assert "&lt;script&gt;" in html and "Empresa &lt;fixture&gt;" in html
    assert "A4 portrait" in html
    assert "fitBounds(bounds" in html


@pytest.mark.asyncio
async def test_pdf_gate_blocks_without_cartography_review(monkeypatch):
    monkeypatch.delenv("GEOGRID_MAP_PROVIDER_COMMERCIAL_USE_APPROVED", raising=False)
    with pytest.raises(HTTPException) as error:
        await server_reports.basic_pdf([example_scan("padaria")])
    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_chromium_report_with_offline_cartography(monkeypatch, tmp_path):
    assets = os.environ.get("GEOGRID_TEST_MAPLIBRE_ASSETS")
    if not assets:
        pytest.skip("requires the pinned MapLibre JS/CSS assets")
    monkeypatch.setenv("GEOGRID_MAP_PROVIDER", "openfreemap")
    monkeypatch.setenv("GEOGRID_MAP_PROVIDER_COMMERCIAL_USE_APPROVED", "true")
    new_page = Browser.new_page
    style = {
        "version": 8, "sources": {},
        "layers": [{"id": "land", "type": "background", "paint": {"background-color": "#dce9e3"}}],
    }

    async def page_with_fixture(browser, **options):
        page = await new_page(browser, **options)

        async def asset(route):
            url = route.request.url
            headers = {"access-control-allow-origin": "*"}
            if url == server_reports._STYLE:
                await route.fulfill(json=style, headers=headers)
            elif url in {server_reports._JS, server_reports._CSS}:
                await route.fulfill(path=Path(assets) / url.split("/")[-1], headers=headers)
            else:
                await route.abort()

        await page.route("**/*", asset)
        return page

    monkeypatch.setattr(Browser, "new_page", page_with_fixture)
    for name, keywords in [("basico", ["padaria"]), ("consolidado", ["padaria", "café", "pão"])]:
        response = await server_reports.basic_pdf([example_scan(keyword) for keyword in keywords])
        assert response.body.startswith(b"%PDF")
        assert response.headers["cache-control"] == "private, no-store"
        assert response.body.count(b"/Type /Page\n") == len(keywords)
        evidence = os.environ.get("GEOGRID_TEST_PDF_EVIDENCE")
        (Path(evidence) if evidence else tmp_path).joinpath(f"{name}.pdf").write_bytes(response.body)
