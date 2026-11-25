from pathlib import Path

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from app.main import index


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def _page_content() -> str:
    html = index().body.decode("utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    leaflet_stub = """
      <script>
        window.testMarkers = [];
        window.L = {
          map: () => ({setView() { return this; }}),
          control: {zoom: () => ({addTo() {}})},
          tileLayer: (url, options) => {
            window.tileLayerConfig = {url, options};
            return {addTo() {}};
          },
          marker: (latlng, options) => {
            const marker = {
              latlng, options,
              addTo() { return this; },
              bindPopup(callback) { this.popupCallback = callback; return this; },
              bindTooltip() { return this; },
              on() { return this; },
              remove() {},
            };
            window.testMarkers.push(marker);
            return marker;
          },
          divIcon: (options) => options,
          point: (point) => point,
          layerGroup: () => ({addTo() { return this; }, clearLayers() {}}),
        };
      </script>
    """
    return (
        html
        .replace('<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">', "")
        .replace('<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>', leaflet_stub)
        .replace('<link rel="stylesheet" href="/static/style.css">', f"<style>{css}</style>")
        .replace('<script src="/static/app.js"></script>', f"<script>{app_js}</script>")
        .replace("<head>", '<head><base href="http://smart-grid.test/">')
    )


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def help_page(browser, monkeypatch):
    monkeypatch.setenv("GEOGRID_MAPTILER_API_KEY", "test-maptiler-key")
    page: Page = browser.new_page(viewport={"width": 1280, "height": 900})
    page.route(
        "**/api/businesses",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body="[]",
        ),
    )
    page.route(
        "http://smart-grid.test/",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body=_page_content(),
        ),
    )
    page.goto("http://smart-grid.test/", wait_until="domcontentloaded")
    yield page
    page.close()


def test_help_menu_opens_and_closes_with_button_and_escape(help_page):
    button = help_page.locator("#helpMenuBtn")
    dialog = help_page.locator("#helpPanel")

    assert help_page.title() == "Smart Grid by ZURI"
    assert help_page.locator(".brand-name").inner_text().startswith("◉\nSmart Grid by ZURI")
    button.click()
    assert dialog.is_visible()
    assert button.get_attribute("aria-expanded") == "true"
    assert help_page.locator("#closeHelpPanel").evaluate("(el) => el === document.activeElement")

    help_page.keyboard.press("Escape")
    assert not dialog.is_visible()
    expect(button).to_have_attribute("aria-expanded", "false")
    assert button.evaluate("(el) => el === document.activeElement")

    button.click()
    help_page.locator("#closeHelpPanel").click()
    assert not dialog.is_visible()


def test_help_panel_explains_indicators_views_and_unavailable_data(help_page):
    help_page.locator("#helpMenuBtn").click()
    text = help_page.locator("#helpPanel").inner_text()

    for label in ("ARP", "ATRP", "SoLV", "Found", "IVL", "IDA", "ICM", "ICP-B V2", "IOP"):
        assert label in text
    for detail in ("Rank", "Dificuldade", "Oportunidade", "1–3", "4–10", "11–20", "20+",
                   "0–34,9", "35–69,9", "70–100", "Sem dados", "erro", "não prevê sucesso"):
        assert detail in text
    assert "Experimental" in text
    assert "Protótipo não validado" in text


def test_scan_view_explains_coverage_and_distinguishes_point_outcomes(help_page):
    help_page.route(
        "http://smart-grid.test/api/scans/9",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body="""{
              "id": 9, "status": "done", "keyword": "serviço local",
              "business_name": "Empresa", "business_cid": "target",
              "business_place_id": null, "created": "2025-01-01T00:00:00Z",
              "grid_size": 3, "spacing_km": 1, "center_lat": -23.5, "center_lng": -46.6,
              "progress": 4, "total": 9, "metrics": {},
              "comparison": null, "competitors": [],
              "points": [
                {"row": 0, "col": 0, "lat": -23.5, "lng": -46.6,
                 "rank": null, "status": "ok", "results": [{"name": "Concorrente", "cid": "other"}]},
                {"row": 0, "col": 1, "lat": -23.5, "lng": -46.5,
                 "rank": null, "status": "no_results", "error_category": "no_results",
                 "api_status_code": 40102, "api_status_message": "No Search Results", "results": []},
                {"row": 0, "col": 2, "lat": -23.5, "lng": -46.4,
                 "rank": null, "status": "error", "error_category": "insufficient_balance",
                 "api_status_code": 40200, "api_status_message": "Payment required",
                 "error": "DataForSEO 40200: Payment required", "results": []},
                {"row": 1, "col": 0, "lat": -23.4, "lng": -46.6,
                 "rank": null, "status": "pending", "error_category": "task_pending",
                 "api_status_code": 20100, "api_status_message": "Task created", "results": []}
              ]
            }""",
        ),
    )
    help_page.route(
        "**/api/scans/9/analysis**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"metrics": {}, "ida_average": null, "ida_evaluated_points": 0, "ida_coverage_pct": 0, "ice": {"counts": {}}, "icm": {}, "icpb_v2": {}, "iop": {}, "points": []}',
        ),
    )

    help_page.evaluate("openScan(9)")

    coverage = help_page.locator("#coverageInfo")
    expect(coverage).to_contain_text("1/9 pontos válidos (11.1%)")
    expect(coverage).to_contain_text("sem resultados: 1")
    expect(coverage).to_contain_text("falhos: 1")
    expect(coverage).to_contain_text("pendentes: 6")
    expect(coverage).to_contain_text("não representa probabilidade")
    marker_html = help_page.evaluate(
        "window.testMarkers.map((marker) => marker.options.icon?.html).filter(Boolean)"
    )
    assert any('class="pin r4"' in item and ">20+</div>" in item for item in marker_html)
    assert any('class="pin no-results"' in item and ">Ø</div>" in item for item in marker_html)
    assert any('class="pin rx"' in item and ">!</div>" in item for item in marker_html)
    assert any('class="pin pending"' in item and ">…</div>" in item for item in marker_html)
    popup_texts = help_page.evaluate(
        """window.testMarkers
          .filter((marker) => marker.popupCallback)
          .map((marker) => marker.popupCallback())"""
    )
    assert any("não é contado como posição 20+" in popup for popup in popup_texts)
    assert any("consulta válida; empresa fora do ranking" in popup for popup in popup_texts)
    assert any("Saldo insuficiente" in popup and "40200" in popup for popup in popup_texts)


def test_partial_scan_keeps_point_states_visible_in_pdf_print_view(help_page):
    help_page.route(
        "http://smart-grid.test/api/scans/22",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body="""{
              "id": 22, "status": "partial", "finished": "2026-01-01T00:00:00Z",
              "keyword": "serviço local", "business_name": "Empresa",
              "business_cid": "target", "business_place_id": null,
              "created": "2026-01-01T00:00:00Z", "grid_size": 3, "spacing_km": 1,
              "center_lat": -23.5, "center_lng": -46.6, "progress": 3, "total": 9,
              "metrics": {"arp": 1, "atrp": 1, "solv": 100, "found_pct": 100},
              "comparison": null, "competitors": [],
              "points": [
                {"row": 0, "col": 0, "lat": -23.5, "lng": -46.6,
                 "rank": 1, "status": "ok", "results": [{"name": "Empresa", "cid": "target"}]},
                {"row": 0, "col": 1, "lat": -23.5, "lng": -46.5,
                 "rank": null, "status": "no_results", "results": []},
                {"row": 0, "col": 2, "lat": -23.5, "lng": -46.4,
                 "rank": null, "status": "error", "error": "Provider error", "results": []},
                {"row": 1, "col": 0, "lat": -23.4, "lng": -46.6,
                 "rank": null, "status": "pending", "results": []}
              ]
            }""",
        ),
    )
    help_page.route(
        "**/api/scans/22/analysis**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"metrics": {}, "ida_average": null, "ida_evaluated_points": 0, "ida_coverage_pct": 0, "ice": {"counts": {}}, "icm": {}, "icpb_v2": {}, "iop": {}, "points": []}',
        ),
    )

    help_page.evaluate("window.print = () => { window.__printCalls = (window.__printCalls || 0) + 1; }")
    help_page.evaluate("openScan(22)")

    assert help_page.locator("#deltaBtn").is_disabled()
    expect(help_page.locator("#coverageInfo")).to_contain_text("Execução encerrada parcialmente")
    marker_html = help_page.evaluate(
        "window.testMarkers.map((marker) => marker.options.icon?.html).filter(Boolean)"
    )
    assert any('class="pin no-results"' in item for item in marker_html)
    assert any('class="pin rx"' in item for item in marker_html)
    assert any('class="pin pending"' in item for item in marker_html)

    help_page.locator("#printBtn").click()
    assert help_page.evaluate("window.__printCalls") >= 1
    help_page.emulate_media(media="print")
    assert help_page.locator("#side").evaluate("(el) => getComputedStyle(el).display") == "none"
    assert help_page.locator("#scanCard").evaluate("(el) => getComputedStyle(el).display") != "none"


def test_help_panel_fits_and_scrolls_on_mobile(help_page):
    help_page.set_viewport_size({"width": 390, "height": 700})
    help_page.locator("#helpMenuBtn").click()

    dimensions = help_page.locator("#helpPanel").evaluate(
        """(dialog) => ({
          width: dialog.getBoundingClientRect().width,
          viewportWidth: window.innerWidth,
          contentHeight: dialog.querySelector(".help-panel-content").clientHeight,
          contentScrollHeight: dialog.querySelector(".help-panel-content").scrollHeight,
        })"""
    )
    assert dimensions["width"] <= dimensions["viewportWidth"]
    assert dimensions["contentScrollHeight"] > dimensions["contentHeight"]


def test_tile_layer_uses_maptiler_xyz_url_and_required_attribution(help_page):
    config = help_page.evaluate("window.tileLayerConfig")

    assert config["url"] == (
        "https://api.maptiler.com/maps/streets-v4/{z}/{x}/{y}.png"
        "?key=test-maptiler-key"
    )
    assert "tile.openstreetmap.org" not in config["url"]
    assert config["options"]["maxZoom"] == 19
    assert 'href="https://www.maptiler.com/copyright/"' in config["options"]["attribution"]
    assert 'href="https://www.openstreetmap.org/copyright"' in config["options"]["attribution"]
    assert "test-maptiler-key" not in (STATIC / "app.js").read_text(encoding="utf-8")


def test_index_injects_maptiler_key_from_environment_safely(monkeypatch):
    monkeypatch.setenv("GEOGRID_MAPTILER_API_KEY", 'key" onload="alert(1)')

    html = index().body.decode("utf-8")

    assert 'content="key&quot; onload=&quot;alert(1)"' in html
    assert "__GEOGRID_MAPTILER_API_KEY__" not in html


def test_missing_maptiler_key_does_not_fall_back_to_public_osm(browser, monkeypatch):
    monkeypatch.delenv("GEOGRID_MAPTILER_API_KEY", raising=False)
    html = index().body.decode("utf-8")
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    leaflet_stub = """
      <script>
        window.L = {
          map: () => ({setView() { return this; }}),
          control: {zoom: () => ({addTo() {}})},
          tileLayer: (url, options) => {
            window.tileLayerConfig = {url, options};
            return {addTo() {}};
          },
          layerGroup: () => ({addTo() {}, clearLayers() {}}),
        };
      </script>
    """
    content = (
        html
        .replace('<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">', "")
        .replace('<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>', leaflet_stub)
        .replace('<script src="/static/app.js"></script>', f"<script>{app_js}</script>")
    )
    page = browser.new_page()
    page.route("**/api/businesses", lambda route: route.fulfill(status=200, body="[]"))
    page.set_content(content, wait_until="domcontentloaded")

    assert page.evaluate("window.tileLayerConfig === undefined")
    assert page.locator("#mapTileStatus").is_visible()
    assert "tile.openstreetmap.org" not in app_js
    page.close()


def test_indicator_preferences_default_persist_and_restore(help_page):
    help_page.locator("#helpMenuBtn").click()
    toggles = help_page.locator("[data-indicator-toggle]")
    expect(toggles).to_have_count(9)
    for toggle in toggles.all():
        expect(toggle).to_be_checked()

    arp = help_page.locator('[data-indicator-toggle="arp"]')
    arp.uncheck()
    expect(help_page.locator('[data-indicator-item="arp"]')).to_have_class("help-item is-disabled")
    expect(help_page.locator('[data-indicator-item="arp"] [data-indicator-state]')).to_have_text("Inativo")
    help_page.locator("#applyIndicatorPreferences").click()
    assert help_page.evaluate(
        "JSON.parse(localStorage.getItem('geogrid.indicatorPreferences')).arp"
    ) is False

    help_page.reload()
    expect(help_page.locator('[data-indicator-toggle="arp"]')).not_to_be_checked()
    help_page.locator("#helpMenuBtn").click()
    help_page.locator("#restoreIndicatorDefaults").click()
    expect(help_page.locator("[data-indicator-toggle]")).to_have_count(9)
    for toggle in help_page.locator("[data-indicator-toggle]").all():
        expect(toggle).to_be_checked()
    assert help_page.evaluate(
        "Object.values(JSON.parse(localStorage.getItem('geogrid.indicatorPreferences'))).every(Boolean)"
    ) is True
    help_page.locator("#deselectAllIndicators").click()
    for toggle in help_page.locator("[data-indicator-toggle]").all():
        expect(toggle).not_to_be_checked()
    help_page.locator("#applyIndicatorPreferences").click()
    assert help_page.evaluate(
        "Object.values(JSON.parse(localStorage.getItem('geogrid.indicatorPreferences'))).every((enabled) => !enabled)"
    ) is True
    help_page.locator("#selectAllIndicators").click()
    for toggle in help_page.locator("[data-indicator-toggle]").all():
        expect(toggle).to_be_checked()
    help_page.locator("#applyIndicatorPreferences").click()
    assert help_page.evaluate(
        "Object.values(JSON.parse(localStorage.getItem('geogrid.indicatorPreferences'))).every(Boolean)"
    ) is True


def test_indicator_preferences_hide_views_cards_and_competitor_columns(help_page):
    page_errors = []
    help_page.on("pageerror", lambda error: page_errors.append(str(error)))
    help_page.route(
        "http://smart-grid.test/api/scans/1",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body="""{
              "id": 1, "status": "done", "keyword": "coffee", "business_name": "Cafe",
              "business_cid": "cafe", "business_place_id": null, "created": "2025-01-01T00:00:00Z",
              "grid_size": 1, "spacing_km": 1, "center_lat": 39.5, "center_lng": -98.35,
              "progress": 1, "total": 1, "metrics": {"arp": 2, "atrp": 2, "solv": 100, "found_pct": 100},
              "comparison": null,
              "points": [{"row": 0, "col": 0, "status": "ok", "rank": 2,
                "lat": 39.5, "lng": -98.35, "results": [{"name": "Cafe", "cid": "cafe"}]}],
              "competitors": [{"name": "Cafe", "cid": "cafe", "solv": 100, "atrp": 2, "found_pct": 100}]
            }""",
        ),
    )
    help_page.route(
        "**/api/scans/1/analysis**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body="""{
              "metrics": {"ivl": 85}, "ida_average": 42,
              "ida_evaluated_points": 1, "ida_coverage_pct": 100,
              "ice": {"counts": {"direct": 1, "indirect": 0, "keyword_only": 0, "indeterminate": 0}},
              "points": [{"row": 0, "col": 0, "difficulty": {"ida": 42, "level": "média"}}],
              "icm": {"score": 50}, "icpb_v2": {"score": 60},
              "iop": {"score": 70, "points": [{"row": 0, "col": 0, "score": 70}]}
            }""",
        ),
    )
    help_page.evaluate("openScan(1)")
    expect(help_page.locator('[data-indicator-card="arp"]')).to_be_visible()
    expect(help_page.locator("#analysisInfo")).to_contain_text("IDA:")
    help_page.locator("#difficultyBtn").click()
    expect(help_page.locator("#difficultyLegend")).to_be_visible()
    help_page.locator("#helpMenuBtn").click()

    help_page.locator('[data-indicator-toggle="ida"]').uncheck()
    help_page.locator('[data-indicator-toggle="arp"]').uncheck()
    help_page.locator('[data-indicator-toggle="solv"]').uncheck()
    help_page.locator("#applyIndicatorPreferences").click()

    expect(help_page.locator("#rankLegend")).to_be_visible()
    expect(help_page.locator("#difficultyLegend")).to_be_hidden()
    expect(help_page.locator("#difficultyBtn")).to_be_hidden()
    expect(help_page.locator('[data-indicator-card="arp"]')).to_have_count(0)
    expect(help_page.locator('[data-indicator-card="solv"]')).to_have_count(0)
    expect(help_page.locator('[data-indicator-card="atrp"]')).to_be_visible()
    expect(help_page.locator('#compTable th[data-indicator-column="solv"]')).to_be_hidden()
    expect(help_page.locator('#compTable td[data-indicator-column="solv"]')).to_be_hidden()

    help_page.locator("#closeHelpPanel").click()
    help_page.locator("#helpMenuBtn").click()
    help_page.locator('[data-indicator-toggle="ida"]').check()
    help_page.locator("#applyIndicatorPreferences").click()
    help_page.locator("#closeHelpPanel").click()
    help_page.locator("#opportunityBtn").click()
    expect(help_page.locator("#opportunityLegend")).to_be_visible()
    help_page.locator("#helpMenuBtn").click()
    help_page.locator('[data-indicator-toggle="iop"]').uncheck()
    help_page.locator("#applyIndicatorPreferences").click()
    expect(help_page.locator("#rankLegend")).to_be_visible()
    expect(help_page.locator("#opportunityBtn")).to_be_hidden()
    assert page_errors == []


def test_malformed_indicator_preferences_default_to_all_enabled(help_page):
    help_page.evaluate("localStorage.setItem('geogrid.indicatorPreferences', 'null')")
    help_page.reload()

    for toggle in help_page.locator("[data-indicator-toggle]").all():
        expect(toggle).to_be_checked()
