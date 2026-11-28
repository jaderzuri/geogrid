from app.commercial import landing_diagnostics_gate
from app.config.maps import map_settings
from app.interpretation import generate_full_interpretation
from app.main import index


def test_openfreemap_default_requires_own_approval(monkeypatch):
    monkeypatch.delenv("GEOGRID_MAP_PROVIDER", raising=False)
    monkeypatch.delenv("GEOGRID_MAP_PROVIDER_COMMERCIAL_USE_APPROVED", raising=False)
    monkeypatch.setenv("GEOGRID_MAPTILER_COMMERCIAL_PLAN_CONFIRMED", "true")
    monkeypatch.setenv("GEOGRID_MAPTILER_API_KEY", "must-not-be-in-openfreemap-page")
    assert map_settings() == ("openfreemap", "https://tiles.openfreemap.org/styles/liberty")
    assert "openfreemap_commercial_use_unapproved" in landing_diagnostics_gate()["blockers"]
    html = index().body.decode()
    assert "maplibre-gl@4.7.1" in html
    assert 'content="https://tiles.openfreemap.org/styles/liberty"' in html
    assert "must-not-be-in-openfreemap-page" not in html


def test_invalid_map_style_cannot_pass_commercial_gate(monkeypatch):
    monkeypatch.setenv("GEOGRID_MAP_PROVIDER", "openfreemap")
    monkeypatch.setenv("GEOGRID_MAP_STYLE_URL", "https://user:secret@example.test/map")
    monkeypatch.setenv("GEOGRID_MAP_PROVIDER_COMMERCIAL_USE_APPROVED", "true")
    assert map_settings() == ("unavailable", "")
    assert "map_provider_unavailable" in landing_diagnostics_gate()["blockers"]
    assert "user:secret" not in index().body.decode()


def test_competition_uses_measured_difficulty_with_coverage():
    analysis = {
        "ida_average": 70,
        "ida_evaluated_points": 2,
        "ida_coverage_pct": 50,
        "ice": {"counts": {}},
    }
    summary = generate_full_interpretation(analysis, "padaria")["competition"]
    assert "difícil (IDA 70/100)" in summary
    assert "50% dos pontos válidos" in summary
    assert "Indeterminada" not in summary

    analysis["ida_evaluated_points"] = 0
    summary = generate_full_interpretation(analysis, "padaria")["competition"]
    assert "Dificuldade indeterminada" in summary
