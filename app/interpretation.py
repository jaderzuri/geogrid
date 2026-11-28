"""Automatic interpretation generation for scan analysis.

Provides human-readable insights based on scan metrics and coverage.
"""


def generate_coverage_interpretation(metrics: dict) -> str:
    """Generate interpretation of scan coverage and results.
    
    Args:
        metrics: Dictionary containing:
            - requested_points: Total points requested
            - valid_points: Points successfully processed
            - no_results_points: Points with no results (legitimate)
            - failed_points: Points that failed technically
            - pending_points: Points still pending
            - coverage_pct: Coverage percentage
            - found_pct: Percentage of found results (among valid points)
    
    Returns:
        Human-readable interpretation in Portuguese.
    """
    requested = metrics.get("requested_points", 0)
    valid = metrics.get("valid_points", 0)
    no_results = metrics.get("no_results_points", 0)
    failed = metrics.get("failed_points", 0)
    pending = metrics.get("pending_points", 0)
    coverage_pct = metrics.get("coverage_pct", 0.0)
    found_pct = metrics.get("found_pct", 0.0)
    
    if requested == 0:
        return "Nenhum ponto foi processado."
    
    # Check for incomplete processing
    if pending > 0:
        return f"A pesquisa ainda está em processamento. {valid}/{requested} pontos foram processados até o momento."
    
    processed = valid + no_results + failed

    # Check for technical failures
    if failed > 0:
        if valid == 0:
            failure_detail = (
                f"{failed} ponto apresentou falha técnica"
                if failed == 1
                else f"{failed} pontos apresentaram falhas técnicas"
            )
            no_result_detail = (
                f"{no_results} consulta não retornou resultados"
                if no_results == 1
                else f"{no_results} consultas não retornaram resultados"
            )
            return (
                "Não há pontos com resultados válidos para avaliar. "
                f"{failure_detail}; {no_result_detail}. "
                "A ausência de resultados não comprova inexistência de empresas ou de demanda."
            )
        no_result_detail = (
            f"{no_results} consulta não retornou resultados"
            if no_results == 1
            else f"{no_results} consultas não retornaram resultados"
        )
        failure_detail = (
            f"{failed} ponto apresentou problema técnico"
            if failed == 1
            else f"{failed} pontos apresentaram problemas técnicos"
        )
        return (
            f"A pesquisa foi concluída com interrupções. {valid}/{requested} pontos foram "
            f"processados com resultados válidos; {no_result_detail} e "
            f"{failure_detail}. "
            "A leitura comercial se limita aos pontos válidos."
        )
    
    # A query with no results is operationally complete, but has no result coverage.
    if processed >= requested or coverage_pct == 100.0:
        if no_results == 0:
            return (
                f"A pesquisa foi concluída com sucesso em todos os {requested} pontos. "
                f"Empresa encontrada em {round(found_pct)}% das localidades analisadas."
            )
        
        return (
            f"A execução foi concluída nos {requested} pontos, sem falhas técnicas. "
            f"Foram retornados resultados em {valid}/{requested} pontos ({coverage_pct}% de cobertura de resultados); "
            f"{no_results} consulta{'s' if no_results != 1 else ''} "
            f"{'não retornou' if no_results == 1 else 'não retornaram'} empresas para a palavra-chave. "
            "Ausência de resultados retornados não comprova inexistência de empresas ou de demanda."
        )
    
    # Partial coverage (but all attempted points are done)
    if coverage_pct > 0:
        return (
            f"A pesquisa foi parcialmente concluída. {valid}/{requested} pontos foram "
            f"processados ({coverage_pct}%). Recomenda-se tentar novamente para completar a análise."
        )
    
    return "Nenhum ponto pôde ser processado. Verifique a conexão e tente novamente."


def generate_positioning_interpretation(metrics: dict, keyword: str) -> str:
    """Generate interpretation of target's positioning.
    
    Args:
        metrics: Dictionary containing:
            - found_pct: Percentage of points where target was found
            - arp: Average Rank Position (when found)
            - solv: Share of Local Voice (percentage)
            - top10_pct: Percentage of top-10 positions
        keyword: Search keyword
    
    Returns:
        Human-readable positioning summary.
    """
    found_pct = metrics.get("found_pct", 0.0)
    arp = metrics.get("arp")
    solv = metrics.get("solv", 0.0)
    top10_pct = metrics.get("top10_pct", 0.0)
    
    if "valid_points" in metrics and metrics.get("valid_points", 0) == 0:
        failed = metrics.get("failed_points", 0)
        no_results = metrics.get("no_results_points", 0)
        pending = metrics.get("pending_points", 0)
        if pending > 0:
            return "Ainda não há pontos válidos suficientes para avaliar o posicionamento."
        if failed > 0:
            return (
                "Não foi possível avaliar o posicionamento devido a falhas técnicas; "
                "os resultados disponíveis não permitem uma conclusão comercial."
            )
        if no_results > 0:
            return (
                f"O provedor não retornou resultados para '{keyword}' em {no_results} consultas. "
                "Sem resultados válidos, não é possível avaliar o posicionamento; "
                "isso não comprova inexistência de empresas ou de demanda."
            )
        return "Não há pontos válidos suficientes para avaliar o posicionamento."

    if not found_pct or found_pct == 0:
        valid_points = metrics.get("valid_points")
        observed = (
            f" nos {valid_points} pontos válidos analisados"
            if valid_points is not None
            else ""
        )
        return (
            f"A empresa não foi localizada entre os resultados retornados para '{keyword}'{observed}. "
            "Isso não comprova inexistência de empresas ou de demanda. "
            "Verificar a estratégia de SEO local e a presença no Google Business."
        )
    
    if found_pct < 30:
        visibility = "fraca"
        recommendation = "Recomenda-se aumentar a presença em localidades onde não aparece."
    elif found_pct < 60:
        visibility = "moderada"
        recommendation = "Há oportunidade de melhorar a cobertura em mais localidades."
    elif found_pct < 85:
        visibility = "boa"
        recommendation = "Manter e reforçar a presença local onde já é visível."
    else:
        visibility = "excelente"
        recommendation = "Continuar as estratégias atuais, que já apresentam boa cobertura."
    
    positioning = ""
    if arp is not None:
        if arp <= 3:
            positioning = f" Posição média: topo 3 (ARP: {arp})"
        elif arp <= 10:
            positioning = f" Posição média: topo 10 (ARP: {arp})"
        else:
            positioning = f" Posição média: fora do topo 10 (ARP: {arp})"
    
    return (
        f"Visibilidade {visibility} em '{keyword}': encontrada em {found_pct}% das localidades.{positioning} "
        f"{recommendation}"
    )


def generate_competition_interpretation(ice: dict, keyword: str) -> str:
    """Generate interpretation of competitive landscape.
    
    Args:
        ice: Competitive environment indicator
        keyword: Search keyword
    
    Returns:
        Human-readable competition summary.
    """
    if not ice or not isinstance(ice, dict):
        return "Análise de competição indisponível."
    
    level = ice.get("level", "unknown")
    confidence = ice.get("confidence", "low")
    top_competitors = ice.get("top_competitors", [])
    
    level_text = {
        "muito_alta": "Muito Alta",
        "alta": "Alta",
        "moderada": "Moderada",
        "baixa": "Baixa",
    }.get(level, "Indeterminada")
    
    result = f"Competição em '{keyword}': Nível {level_text}."
    
    if top_competitors and len(top_competitors) > 0:
        top_names = []
        for c in top_competitors[:3]:
            if isinstance(c, dict):
                top_names.append(c.get("name", "Empresa"))
            else:
                top_names.append(str(c))
        result += f" Principais concorrentes: {', '.join(top_names)}."
    
    return result


def generate_full_interpretation(analysis: dict, keyword: str) -> dict:
    """Generate complete interpretation based on analysis.
    
    Args:
        analysis: Full analysis dictionary from /api/scans/{scan_id}/analysis
        keyword: Search keyword
    
    Returns:
        Dictionary with interpretation sections:
        - coverage: Coverage and processing interpretation
        - positioning: Target positioning interpretation
        - competition: Competitive landscape interpretation
    """
    metrics = analysis.get("metrics", {})
    ice = analysis.get("ice")
    score = analysis.get("ida_average")
    ice_summary = generate_competition_interpretation(ice, keyword)
    if score is not None and 0 <= score <= 100 and analysis.get("ida_evaluated_points", 0) > 0:
        level = "fácil" if score < 35 else "intermediária" if score < 70 else "difícil"
        competition = (
            f"Dificuldade em '{keyword}': {level} (IDA {score:g}/100). "
            f"Faixas operacionais do IDA. Cobertura: {analysis.get('ida_coverage_pct', 0):g}% dos pontos válidos."
        )
        if isinstance(ice, dict) and ice.get("level") in {"muito_alta", "alta", "moderada", "baixa"}:
            competition += f" {ice_summary}"
    else:
        competition = (
            "Dificuldade indeterminada: nenhum ponto possui dados suficientes para calcular o IDA. "
            f"{ice_summary}"
        )

    return {
        "coverage": generate_coverage_interpretation(metrics),
        "positioning": generate_positioning_interpretation(metrics, keyword),
        "competition": competition,
    }
