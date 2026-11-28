"""Protected Basic A4 reports composed from stored scan points."""

import asyncio
import json
from html import escape
from urllib.parse import urlsplit

from fastapi import HTTPException, Response
from playwright.async_api import Error as BrowserError, async_playwright

from .config.maps import map_commercial_blocker, map_settings

_RENDER_LIMIT = asyncio.Semaphore(1)
_STYLE = "https://tiles.openfreemap.org/styles/liberty"
_JS = "https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.js"
_CSS = "https://cdn.jsdelivr.net/npm/maplibre-gl@4.7.1/dist/maplibre-gl.css"


def _report_html(scans: list[dict]) -> str:
    maps = []
    sections = []
    business = escape(scans[0]["business_name"])
    intro = ""
    if len(scans) > 1:
        keywords = ", ".join(escape(scan["keyword"]) for scan in scans)
        intro = f"<p>{len(scans)} pesquisas concluídas: {keywords}. Os indicadores de cada palavra-chave são apresentados separadamente.</p>"
    for index, scan in enumerate(scans):
        points = scan["points"]
        metrics = scan.get("metrics") or {}
        indicators = " · ".join(
            f"{label}: {escape(str(metrics[key])) if metrics.get(key) is not None else '—'}{unit if metrics.get(key) is not None else ''}"
            for key, label, unit in (("arp", "ARP", ""), ("atrp", "ATRP", ""),
                                     ("solv", "SoLV", "%"), ("found_pct", "Presença", "%"))
        )
        competitors = "".join(
            "<li>" + escape(str(row.get("name") or "Empresa")) + " · SoLV "
            + escape(str(row.get("solv", "—"))) + "%</li>"
            for row in (scan.get("competitors") or [])[:5]
        )
        sections.append(f"""
          <section><h2>{escape(scan["keyword"])}</h2>
          <p>{scan["grid_size"]} × {scan["grid_size"]} · {scan["spacing_km"]:g} km · {indicators}</p>
          <p>{sum(point["status"] == "ok" for point in points)}/{scan["total"]} pontos com resultados válidos.</p>
          <div class="map" id="map-{index}"></div>
          <p>1–3 verde · 4–10 amarelo · 11–20 laranja · 20+ vermelho · Ø sem resultados · ! falha · … pendente</p>
          <p>OpenFreeMap · © OpenMapTiles · Dados © OpenStreetMap contributors</p>
          {f"<h3>Concorrentes observados</h3><ol>{competitors}</ol>" if competitors else ""}
          </section>
        """)
        maps.append([{key: point[key] for key in ("lat", "lng", "rank", "status")} for point in points])
    data = json.dumps(maps, allow_nan=False).replace("<", "\\u003c")
    return f"""<!doctype html><html lang="pt-BR"><meta charset="utf-8">
      <link rel="stylesheet" href="{_CSS}" crossorigin="anonymous"
        integrity="sha384-MinO0mNliZ3vwppuPOUnGa+iq619pfMhLVUXfC4LHwSCvF9H+6P/KO4Q7qBOYV5V">
      <script src="{_JS}" crossorigin="anonymous"
        integrity="sha384-SYKAG6cglRMN0RVvhNeBY0r3FYKNOJtznwA0v7B5Vp9tr31xAHsZC0DqkQ/pZDmj"></script>
      <style>
        @page {{ size: A4 portrait; margin: 13mm; }}
        body {{ font: 14px sans-serif; color: #17253a; margin: 0; }}
        main {{ width: 680px; }}
        section + section {{ break-before: page; }}
        .map {{ width: 680px; height: 470px; border: 1px solid #888; break-inside: avoid; }}
        .rank {{ width: 26px; height: 26px; border-radius: 50%; text-align: center;
                 line-height: 26px; border: 1px solid white; font-weight: bold; background: #e5e7eb; }}
      </style>
      <main><h1>Smart Grid by ZURI · {business}</h1>{intro}{"".join(sections)}</main>
      <script>
        window.reportReady = false;
        window.reportFailed = false;
        const groups = {data};
        (async () => {{
          await document.fonts.ready;
          for (const [i, points] of groups.entries()) {{
            await new Promise((resolve, reject) => {{
              const map = new maplibregl.Map({{
                container: "map-" + i, style: "{_STYLE}", interactive: false,
                attributionControl: false, preserveDrawingBuffer: true,
                center: [points[0].lng, points[0].lat],
              }});
              map.on("error", () => {{ window.reportFailed = true; reject(Error("Basemap unavailable")); }});
              const bounds = new maplibregl.LngLatBounds();
              for (const point of points) {{
                const coord = [point.lng, point.lat];
                bounds.extend(coord);
                const mark = document.createElement("div");
                mark.className = "rank";
                mark.textContent = point.status === "ok" ? (point.rank ?? "20+")
                  : point.status === "no_results" ? "Ø" : point.status === "pending" ? "…" : "!";
                if (point.status === "ok") {{
                  mark.style.background = point.rank == null ? "#ef7777" : point.rank <= 3 ? "#6ad393"
                    : point.rank <= 10 ? "#ffdf71" : "#ffae68";
                }}
                new maplibregl.Marker({{element: mark}}).setLngLat(coord).addTo(map);
              }}
              map.fitBounds(bounds, {{padding: 34, animate: false, maxZoom: 17}});
              map.on("idle", () => {{
                if (map.isStyleLoaded() && map.areTilesLoaded()) resolve();
              }});
            }});
          }}
          window.reportReady = true;
        }})().catch(() => {{ window.reportFailed = true; }});
      </script></html>"""


async def basic_pdf(scans: list[dict]) -> Response:
    if map_settings() != ("openfreemap", _STYLE) or map_commercial_blocker():
        raise HTTPException(503, "A cartografia Liberty precisa estar configurada e aprovada.")
    if not scans or any(
        not scan.get("finished") or scan["status"] not in {"done", "partial"}
        or not scan["points"] or len(scan["points"]) != scan["total"]
        for scan in scans
    ):
        raise HTTPException(409, "A pesquisa ainda não possui grade concluída para relatório.")
    if _RENDER_LIMIT.locked():
        raise HTTPException(503, "Outro relatório está sendo renderizado.")
    try:
        async with _RENDER_LIMIT, asyncio.timeout(30), async_playwright() as engine:
            async with await engine.chromium.launch(args=["--enable-unsafe-swiftshader"]) as browser:
                page = await browser.new_page(viewport={"width": 720, "height": 960})
                async def allow_report_assets(route):
                    url = route.request.url
                    parsed = urlsplit(url)
                    if url in {_JS, _CSS} or (
                        parsed.scheme == "https" and parsed.netloc == "tiles.openfreemap.org"
                    ):
                        await route.fallback()
                    else:
                        await route.abort()

                await page.route("**/*", allow_report_assets)
                await page.emulate_media(media="print")
                await page.set_content(_report_html(scans), wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_function("window.reportReady || window.reportFailed", timeout=18000)
                if await page.evaluate("window.reportFailed"):
                    raise HTTPException(503, "Liberty não carregou integralmente.")
                pdf = await page.pdf(format="A4", prefer_css_page_size=True, print_background=True)
    except (BrowserError, TimeoutError, ValueError) as error:
        raise HTTPException(503, "Não foi possível renderizar integralmente o relatório Liberty.") from error
    return Response(pdf, media_type="application/pdf", headers={
        "Cache-Control": "private, no-store",
        "Content-Disposition": 'inline; filename="relatorio-smart-grid.pdf"',
    })
