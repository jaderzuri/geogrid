import json
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect, sync_playwright

from app.main import index


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def _page_content() -> str:
    html = index().body.decode("utf-8")
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    print_css = (STATIC / "print.css").read_text(encoding="utf-8")
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    leaflet_stub = """
      <script>
        window.testMarkers = [];
        window.testMapCalls = [];
        window.L = {
          map: () => {
            window.testMap = {
              setView() { return this; },
              fitBounds() { return this; },
              invalidateSize(options) {
                window.testMapCalls.push(["invalidateSize", options]);
                return this;
              },
              panTo(center, options) {
                window.testMapCalls.push(["panTo", center, options]);
                return this;
              },
            };
            return window.testMap;
          },
          control: {zoom: () => ({addTo() {}})},
          tileLayer: (url, options) => {
            window.tileLayerConfig = {url, options};
            return {
              addTo() { return this; },
              on(event, callback) { if (event === "load") callback(); return this; },
            };
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
        .replace('<link rel="stylesheet" href="/static/print.css">', f"<style>{print_css}</style>")
        .replace('<script defer src="/static/app.js"></script>', f"<script>{app_js}</script>")
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
    monkeypatch.setenv("GEOGRID_MAP_PROVIDER", "maptiler")
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


def test_scan_interpretation_is_rendered_for_complete_partial_empty_and_failed_scans(help_page):
    scenarios = {
        31: {
            "status": "done",
            "states": ["ok"] * 9,
            "text": "Execução concluída em todos os 9 pontos.",
        },
        32: {
            "status": "partial",
            "states": ["ok"] * 3 + ["no_results"] * 2 + ["error"] * 4,
            "text": "Pesquisa concluída com interrupções; leitura limitada aos pontos válidos.",
        },
        33: {
            "status": "done",
            "states": ["no_results"] * 9,
            "text": "A ausência de resultados não comprova inexistência de empresas ou de demanda.",
        },
        34: {
            "status": "failed",
            "states": ["error"] * 9,
            "text": "Não foi possível avaliar o posicionamento devido a falhas técnicas.",
        },
        35: {
            "status": "done",
            "states": ["ok"] * 3 + ["no_results"] * 6,
            "text": (
                "A execução foi concluída em todos os 9 pontos; 3/9 pontos válidos. "
                "A ausência de resultados não comprova inexistência de empresas ou de demanda."
            ),
        },
    }

    def fulfill_scan(route):
        scan_id = int(route.request.url.rsplit("/", 1)[-1])
        scenario = scenarios[scan_id]
        points = [
            {
                "row": index // 3,
                "col": index % 3,
                "lat": -23.5 + (index // 3) * 0.01,
                "lng": -46.6 + (index % 3) * 0.01,
                "rank": 1 if state == "ok" else None,
                "status": state,
                "results": [{"name": "Empresa", "cid": "target"}] if state == "ok" else [],
            }
            for index, state in enumerate(scenario["states"])
        ]
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "id": scan_id,
                "status": scenario["status"],
                "finished": "2026-11-27T00:00:00Z",
                "keyword": "serviço local",
                "business_name": "Empresa",
                "business_cid": "target",
                "business_place_id": None,
                "created": "2026-11-27T00:00:00Z",
                "grid_size": 3,
                "spacing_km": 1,
                "center_lat": -23.5,
                "center_lng": -46.6,
                "progress": 9,
                "total": 9,
                "metrics": {},
                "comparison": None,
                "competitors": [],
                "points": points,
            }),
        )

    def fulfill_analysis(route):
        scan_id = int(route.request.url.split("/api/scans/")[1].split("/")[0])
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps({
                "metrics": {},
                "ida_evaluated_points": 3,
                "ida_coverage_pct": 100,
                "ice": {
                    "valid_points": 3,
                    "total_points": 9,
                    "counts": {"direct": 1, "indirect": 0, "keyword_only": 0, "indeterminate": 0},
                },
                "icm": {"observed_occurrences": 1},
                "icpb_v2": {"valid_points": 3, "total_points": 9},
                "iop": {},
                "points": [],
                "interpretation": {
                    "coverage": "Execução: " + scenarios[scan_id]["text"],
                    "positioning": "Posicionamento contextualizado para os dados disponíveis.",
                    "competition": "Concorrência observada conforme as respostas recebidas.",
                },
            }),
        )

    help_page.route("http://smart-grid.test/api/scans/*/analysis*", fulfill_analysis)
    help_page.route("http://smart-grid.test/api/scans/*", fulfill_scan)

    for scan_id, scenario in scenarios.items():
        help_page.evaluate(f"openScan({scan_id})")
        expect(help_page.locator("#interpretationInfo")).to_be_visible()
        expect(help_page.locator('[data-interpretation="coverage"]')).to_contain_text(
            scenario["text"]
        )
        expect(help_page.locator('[data-interpretation="positioning"]')).to_contain_text(
            "Posicionamento contextualizado"
        )
        if scan_id == 35:
            coverage = help_page.locator("#coverageInfo")
            expect(coverage).to_contain_text("3/9 pontos válidos")
            expect(coverage).to_contain_text("sem resultados: 6")
            expect(coverage).to_contain_text("falhos: 0")
            expect(help_page.locator('[data-interpretation="coverage"]')).to_contain_text(
                "não comprova inexistência"
            )


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
    help_page.wait_for_function("window.__printCalls >= 1")
    help_page.emulate_media(media="print")
    assert help_page.locator("#side").evaluate("(el) => getComputedStyle(el).display") == "none"
    assert help_page.locator("#scanCard").evaluate("(el) => getComputedStyle(el).display") != "none"


def test_real_ui_injects_branding_and_a4_print_dimensions(help_page):
    intro = help_page.locator(".print-intro")
    closing = help_page.locator(".print-closing")

    assert intro.count() == 1
    assert closing.count() == 1
    expect(intro).to_contain_text("Smart Grid by ZURI")
    expect(intro).to_contain_text("ZURI Smartech")
    expect(closing).to_contain_text("Aumentar Visibilidade")
    assert help_page.locator(".print-qr-link").get_attribute("href") == (
        "https://wa.me/5511996919411"
    )
    assert help_page.locator(".print-qr-link img").get_attribute("src").startswith(
        "data:image/svg+xml;base64,"
    )
    assert help_page.locator('.print-closing a[href="tel:+551142103311"]').count() == 1
    assert help_page.locator(
        '.print-closing a[href="https://www.instagram.com/zurismartech/"]'
    ).count() == 1

    help_page.emulate_media(media="print")
    assert intro.evaluate("(el) => getComputedStyle(el).display") != "none"
    dimensions = help_page.locator("#map").evaluate(
        """(el) => ({
          width: el.getBoundingClientRect().width,
          height: el.getBoundingClientRect().height,
        })"""
    )
    assert dimensions["width"] == pytest.approx(267 * 96 / 25.4, abs=1)
    assert dimensions["height"] == pytest.approx(420 * 96 / 72, abs=1)


def test_large_grid_uses_compact_centered_markers_in_print(help_page):
    grid_size = 13
    points = [
        {
            "row": row,
            "col": col,
            "lat": -23.5 + (row - 6) * 0.01,
            "lng": -46.6 + (col - 6) * 0.01,
            "rank": 1,
            "status": "ok",
            "results": [],
        }
        for row in range(grid_size)
        for col in range(grid_size)
    ]
    scan = {
        "id": 1313,
        "status": "done",
        "finished": "2026-11-27T00:00:00Z",
        "keyword": "serviço local",
        "business_name": "Empresa",
        "business_cid": "target",
        "business_place_id": None,
        "created": "2026-11-27T00:00:00Z",
        "grid_size": grid_size,
        "spacing_km": 1,
        "center_lat": -23.5,
        "center_lng": -46.6,
        "progress": grid_size**2,
        "total": grid_size**2,
        "metrics": {},
        "comparison": None,
        "competitors": [],
        "points": points,
    }
    help_page.route(
        "**/api/scans/1313/analysis*",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"metrics": {}, "ice": {"counts": {}}, "icm": {}, "icpb_v2": {}, "iop": {}, "points": []}',
        ),
    )
    help_page.route(
        "**/api/scans/1313",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(scan),
        ),
    )

    help_page.evaluate("openScan(1313)")
    marker_data = help_page.evaluate(
        """() => {
          const markers = window.testMarkers.filter(
            (marker) => marker.options.icon?.html?.includes('class="pin ')
          );
          return {
            sizes: markers.map((marker) => marker.options.icon.iconSize),
            locations: [...new Set(markers.map((marker) => marker.latlng.join(',')))],
          };
        }"""
    )
    assert len(marker_data["locations"]) == grid_size**2
    assert marker_data["sizes"]
    assert all(size == [24, 24] for size in marker_data["sizes"])

    help_page.emulate_media(media="print")
    help_page.evaluate("window.dispatchEvent(new Event('beforeprint'))")
    assert help_page.evaluate("window.testMapCalls.at(-1)") == [
        "panTo",
        [-23.5, -46.6],
        {"animate": False},
    ]


def test_diagnostic_page_requires_capability_before_showing_private_report(help_page):
    diagnostic_html = (STATIC / "diagnostic.html").read_text(encoding="utf-8")
    diagnostic_js = (STATIC / "diagnostic.js").read_text(encoding="utf-8")
    landing_css = (STATIC / "landing.css").read_text(encoding="utf-8")
    diagnostic_html = (
        diagnostic_html
        .replace('<link rel="stylesheet" href="/static/landing.css">', f"<style>{landing_css}</style>")
        .replace('<link rel="stylesheet" href="/static/print.css">', "")
        .replace('<script src="/static/diagnostic.js" defer></script>', "")
        .replace("</body>", f"<script>{diagnostic_js}</script></body>")
    )
    help_page.route(
        "http://smart-grid.test/diagnostico*",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body=diagnostic_html,
        ),
    )
    request_id = "5ba9d98b-9a7f-4f4e-a907-88bbeb1a1b22"
    token = "a" * 64
    help_page.add_init_script(
        """(() => {
          window.__privateRequests = [];
          window.fetch = async (url, options = {}) => {
            const request = {
              url: String(url),
              method: options.method || 'GET',
              body: options.body ? JSON.parse(options.body) : null,
            };
            window.__privateRequests.push(request);
            const result = request.url.includes('/report/')
              ? {
                  authorized: true,
                  report: {
                    business: {name: 'Empresa sintética'},
                    keyword: 'serviço local',
                    coverage: {coverage_pct: 100, valid_points: 9, requested_points: 9},
                    analysis: {metrics: {arp: 2}},
                  },
                }
              : {
                  authorized: true,
                  request_id: '5ba9d98b-9a7f-4f4e-a907-88bbeb1a1b22',
                  status: 'done',
                  progress: 9,
                  total: 9,
                  finished: '2026-11-27T00:00:00Z',
                  report_ready: true,
                };
            return new Response(JSON.stringify(result), {
              status: 200,
              headers: {'Content-Type': 'application/json'},
            });
          };
        })();"""
    )

    help_page.goto("http://smart-grid.test/diagnostico")
    expect(help_page.locator("#status-message")).to_contain_text("incompleto ou inválido")
    assert help_page.evaluate("window.__privateRequests") == []
    assert help_page.locator("#status-details").is_hidden()
    assert help_page.locator("#report").is_hidden()

    help_page.goto(
        f"http://smart-grid.test/diagnostico?authorized=1#request_id={request_id}&token={token}"
    )
    expect(help_page.locator("#status-details")).to_be_visible()
    requests = help_page.evaluate("window.__privateRequests")
    assert len(requests) == 1
    assert token not in requests[0]["url"]
    assert requests[0]["method"] == "POST"
    assert requests[0]["body"]["status_token"] == token
    assert help_page.locator("#report").is_hidden()

    help_page.locator("#report-link").click()
    expect(help_page.locator("#report")).to_be_visible()
    expect(help_page.locator("#report-content")).to_contain_text("Empresa sintética")
    requests = help_page.evaluate("window.__privateRequests")
    assert len(requests) == 2
    assert token not in requests[1]["url"]
    assert requests[1]["method"] == "POST"
    assert requests[1]["body"]["status_token"] == token


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
    monkeypatch.setenv("GEOGRID_MAP_PROVIDER", "maptiler")
    monkeypatch.setenv("GEOGRID_MAPTILER_API_KEY", 'key" onload="alert(1)')

    html = index().body.decode("utf-8")

    assert 'content="key&quot; onload=&quot;alert(1)"' in html
    assert "__GEOGRID_MAPTILER_API_KEY__" not in html
    assert "__ZURI_PRINT_INTRO__" not in html
    assert "__ZURI_PRINT_CLOSING__" not in html
    assert "Smart Grid by ZURI" in html
    assert "ZURI Smartech" in html
    assert "https://zurismartech.com.br" in html
    assert "https://wa.me/5511996919411" in html
    assert "data:image/svg+xml;base64," in html


def test_missing_maptiler_key_does_not_fall_back_to_public_osm(browser, monkeypatch):
    monkeypatch.setenv("GEOGRID_MAP_PROVIDER", "maptiler")
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
        .replace('<script defer src="/static/app.js"></script>', f"<script>{app_js}</script>")
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


def test_competitor_contacts_and_point_positions_are_rendered_without_inference(help_page):
    help_page.evaluate(
        """
        () => {
          S.scan = {
            business_name: "Alvo",
            points: [
              {row: 0, col: 0, status: "ok", results: [
                {name: "Concorrente Celular", cid: "cid-mobile", place_id: "place-mobile",
                 phone: "+55 11 99999-9999", url: "https://mobile.example.br", position: 4},
                {name: "Concorrente Fixo", cid: "cid-fixed", place_id: "place-fixed",
                 phone: "+55 11 3333-4444", position: 1},
                {name: "Concorrente sem contato", cid: "cid-none", position: null},
              ]},
            ],
            competitors: [
              {name: "Concorrente Celular", cid: "cid-mobile", place_id: "place-mobile",
               solv: 10, atrp: 4, found_pct: 20, rating: 4.5},
              {name: "Concorrente Fixo", cid: "cid-fixed", place_id: "place-fixed",
               solv: 20, atrp: 1, found_pct: 40, rating: 4.2},
              {name: "Concorrente sem contato", cid: "cid-none",
               solv: 0, atrp: 21, found_pct: 0, rating: null},
            ],
          };
          renderCompetitors();
        }
        """
    )

    rows = help_page.locator("#compTable tbody tr")
    assert rows.count() == 3
    mobile = rows.nth(0)
    assert mobile.locator(".competitor-contact").get_by_text("Celular").count() == 1
    assert mobile.locator('a[href="tel:+5511999999999"]').count() == 1
    assert mobile.locator('a[href="https://mobile.example.br/"]').count() == 1
    assert "CID: cid-mobile" in mobile.locator(".competitor-identifiers").inner_text()
    assert "Place ID: place-mobile" in mobile.locator(".competitor-identifiers").inner_text()
    assert "posição original observada: #4 no ponto L1, C1" in mobile.locator(
        ".position-summary"
    ).inner_text()
    assert "ATRP: 4" in mobile.locator("td").nth(5).inner_text()

    fixed = rows.nth(1)
    assert fixed.locator(".competitor-contact").get_by_text("Telefone fixo").count() == 1

    unavailable = rows.nth(2)
    assert unavailable.locator(".competitor-contact").get_by_text(
        "Contato indisponível"
    ).count() == 1
    assert "CID: cid-none" in unavailable.locator(".competitor-identifiers").inner_text()
    assert "Place ID: Indisponível" in unavailable.locator(
        ".competitor-identifiers"
    ).inner_text()
    assert help_page.evaluate(
        'classifyBrazilPhone("+44 20 7946 0958")'
    ) == "Tipo não classificado"


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


def test_basic_mode_hides_advanced_columns_without_removing_them(help_page):
    contact = help_page.locator("#compTable thead th").nth(2)
    mode = help_page.locator("#analysisMode")

    mode.select_option("basic")
    assert contact.evaluate("(cell) => getComputedStyle(cell).display") == "none"
    mode.select_option("advanced")
    assert contact.evaluate("(cell) => getComputedStyle(cell).display") != "none"


def test_unavailable_map_blocks_manual_print(help_page):
    help_page.evaluate("""
        () => {
          window.print = () => { window.printed = true; };
          failMap("Falha de cartografia simulada");
          document.querySelector("#printBtn").click();
        }
    """)
    expect(help_page.locator("#mapTileStatus")).to_have_text("Falha de cartografia simulada")
    assert help_page.evaluate("window.printed === undefined")
