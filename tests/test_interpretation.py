"""Tests for automatic interpretation generation."""

from app.interpretation import (
    generate_coverage_interpretation,
    generate_positioning_interpretation,
    generate_competition_interpretation,
)


class TestCoverageInterpretation:
    def test_no_points_processed(self):
        metrics = {"requested_points": 0}
        result = generate_coverage_interpretation(metrics)
        assert "Nenhum ponto foi processado" in result

    def test_pending_points(self):
        metrics = {
            "requested_points": 9,
            "valid_points": 3,
            "no_results_points": 0,
            "failed_points": 0,
            "pending_points": 6,
            "coverage_pct": 33.3,
            "found_pct": 100.0,
        }
        result = generate_coverage_interpretation(metrics)
        assert "em processamento" in result
        assert "3/9" in result or "3 de 9" in result

    def test_complete_processing_all_results(self):
        metrics = {
            "requested_points": 9,
            "valid_points": 9,
            "no_results_points": 0,
            "failed_points": 0,
            "pending_points": 0,
            "coverage_pct": 100.0,
            "found_pct": 100.0,
        }
        result = generate_coverage_interpretation(metrics)
        assert "sucesso" in result.lower()
        assert "100%" in result
        assert "nenhum ponto" not in result.lower()

    def test_complete_processing_some_no_results(self):
        metrics = {
            "requested_points": 9,
            "valid_points": 3,
            "no_results_points": 6,
            "failed_points": 0,
            "pending_points": 0,
            "coverage_pct": 33.3,
            "found_pct": 0.0,
        }
        result = generate_coverage_interpretation(metrics)
        assert "execução foi concluída" in result.lower()
        assert "3/9 pontos" in result
        assert "não comprova inexistência" in result

    def test_technical_failures_with_some_valid(self):
        metrics = {
            "requested_points": 9,
            "valid_points": 6,
            "no_results_points": 2,
            "failed_points": 3,
            "pending_points": 0,
            "coverage_pct": 66.7,
            "found_pct": 50.0,
        }
        result = generate_coverage_interpretation(metrics)
        assert "interrupções" in result
        assert "6/9" in result or "6 de 9" in result
        assert "problemas técnicos" in result

    def test_complete_failure(self):
        metrics = {
            "requested_points": 9,
            "valid_points": 0,
            "no_results_points": 0,
            "failed_points": 9,
            "pending_points": 0,
            "coverage_pct": 0.0,
            "found_pct": 0.0,
        }
        result = generate_coverage_interpretation(metrics)
        assert "falhas técnicas" in result
        assert "não comprova inexistência" in result
        assert "não há pontos com resultados válidos" in result.lower()


class TestPositioningInterpretation:
    def test_not_found_anywhere(self):
        metrics = {
            "found_pct": 0.0,
            "arp": None,
            "solv": 0.0,
            "top10_pct": 0.0,
        }
        result = generate_positioning_interpretation(metrics, "encanador")
        assert "não foi localizada" in result
        assert "encanador" in result
        assert "Google Business" in result

    def test_no_results_and_no_valid_points_do_not_claim_business_absence(self):
        metrics = {
            "found_pct": 0.0,
            "valid_points": 0,
            "no_results_points": 9,
            "failed_points": 0,
            "pending_points": 0,
            "arp": None,
            "solv": 0.0,
            "top10_pct": 0.0,
        }

        result = generate_positioning_interpretation(metrics, "encanador")

        assert "não é possível avaliar o posicionamento" in result
        assert "não comprova inexistência" in result
        assert "empresa não foi localizada" not in result.lower()

    def test_zero_visibility_is_limited_to_valid_points_when_some_queries_fail(self):
        metrics = {
            "found_pct": 0.0,
            "valid_points": 3,
            "no_results_points": 0,
            "failed_points": 6,
            "pending_points": 0,
            "arp": None,
            "solv": 0.0,
            "top10_pct": 0.0,
        }

        result = generate_positioning_interpretation(metrics, "encanador")

        assert "nos 3 pontos válidos analisados" in result
        assert "não comprova inexistência" in result

    def test_weak_visibility(self):
        metrics = {
            "found_pct": 20.0,
            "arp": 15.5,
            "solv": 10.0,
            "top10_pct": 5.0,
        }
        result = generate_positioning_interpretation(metrics, "restaurante")
        assert "fraca" in result
        assert "20" in result  # 20% ou 20.0%
        assert "aumentar" in result

    def test_moderate_visibility(self):
        metrics = {
            "found_pct": 50.0,
            "arp": 8.2,
            "solv": 25.0,
            "top10_pct": 60.0,
        }
        result = generate_positioning_interpretation(metrics, "dentista")
        assert "moderada" in result
        assert "50" in result  # 50% ou 50.0%
        assert "oportunidade" in result

    def test_good_visibility_top_3(self):
        metrics = {
            "found_pct": 85.0,
            "arp": 2.5,
            "solv": 70.0,
            "top10_pct": 100.0,
        }
        result = generate_positioning_interpretation(metrics, "advogado")
        assert "boa" in result or "excelente" in result
        assert "85" in result  # 85% ou 85.0%
        assert "topo 3" in result or "ARP: 2.5" in result

    def test_excellent_visibility(self):
        metrics = {
            "found_pct": 95.0,
            "arp": 1.8,
            "solv": 85.0,
            "top10_pct": 100.0,
        }
        result = generate_positioning_interpretation(metrics, "pet shop")
        assert "excelente" in result
        assert "95" in result  # 95% ou 95.0%
        assert "Continuar" in result


class TestCompetitionInterpretation:
    def test_no_ice_data(self):
        result = generate_competition_interpretation(None, "teste")
        assert "indisponível" in result.lower()

    def test_high_competition(self):
        ice = {
            "level": "muito_alta",
            "confidence": "high",
            "top_competitors": [
                {"name": "Empresa A", "category": "categoria1"},
                {"name": "Empresa B", "category": "categoria2"},
            ],
        }
        result = generate_competition_interpretation(ice, "palavra-chave")
        assert "Muito Alta" in result
        assert "palavra-chave" in result
        assert "Empresa A" in result
        assert "Empresa B" in result

    def test_low_competition(self):
        ice = {
            "level": "baixa",
            "confidence": "medium",
            "top_competitors": [],
        }
        result = generate_competition_interpretation(ice, "nicho")
        assert "Baixa" in result
        assert "nicho" in result
