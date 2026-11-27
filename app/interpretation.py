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
    
    # Check for technical failures
    if failed > 0:
        if valid == 0:
            return "Não foi possível concluir a pesquisa devido a problemas técnicos. Tente novamente mais tarde."
        return (
            f"A pesquisa foi concluída com interrupções. {valid}/{requested} pontos foram "
            f"processados com sucesso, mas {failed} ponto{'s' if failed > 1 else ''} apresentou problemas técnicos."
        )
    
    # All points processed successfully
    if coverage_pct == 100.0:
        # All points have results
        if no_results == 0:
            return (
                f"A pesquisa foi concluída com sucesso em todos os {requested} pontos. "
                f"Empresa encontrada em {round(found_pct)}% das localidades analisadas."
            )
        
        # Some points have no results
        no_results_pct = round(100 * no_results / valid, 1) if valid > 0 else 0
        found_pct_of_valid = round(100 * (valid - no_results) / valid, 1) if valid > 0 else 0
        
        return (
            f"A pesquisa foi concluída em todos os {valid} pontos. "
            f"Em {no_results_pct}% das localidades não foram retornadas empresas para a palavra-chave pesquisada. "
            f"Recomenda-se avaliar palavras-chave alternativas e sua relevância para a região."
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
    
    if not found_pct or found_pct == 0:
        return (
            f"A empresa não foi encontrada na busca por '{keyword}' em nenhuma localidade analisada. "
            f"Verificar estratégia de SEO local e presença no Google Business."
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
        top_names = ", ".join([c.get("name", "Empresa") for c in top_competitors[:3]])
        result += f" Principais concorrentes: {top_names}."
    
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
    
    return {
        "coverage": generate_coverage_interpretation(metrics),
        "positioning": generate_positioning_interpretation(metrics, keyword),
        "competition": generate_competition_interpretation(ice, keyword),
    }
