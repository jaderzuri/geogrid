"""
PDF generation and validation utilities for Smart Grid reports.

Uses Playwright to generate test PDFs for homologation and visual verification.
Requires: pip install playwright
Run: playwright install chromium
"""

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Optional

try:
    from playwright.async_api import async_playwright, Browser, Page
except ImportError:
    Browser = None
    Page = None

from app.config.branding import get_footer_html, get_cta_section_html, BRANDING
from app.utils.qrcode import generate_qrcode_base64


async def generate_pdf(
    html_content: str,
    output_path: str,
    wait_selector: str = "#map",
    wait_timeout_ms: int = 5000,
) -> bool:
    """
    Generate a PDF from HTML content using Playwright.

    Args:
        html_content: Complete HTML document as string
        output_path: Where to save the PDF file
        wait_selector: CSS selector to wait for before rendering
        wait_timeout_ms: Maximum wait time for selector (milliseconds)

    Returns:
        True if successful, False otherwise
    """
    if Browser is None:
        print("ERROR: playwright not installed. Run: pip install playwright")
        return False

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()

            # Set viewport to A4 landscape dimensions
            await page.set_viewport_size({"width": 1191, "height": 842})

            # Load HTML
            await page.set_content(html_content)

            # Wait for content to load
            try:
                await page.wait_for_selector(wait_selector, timeout=wait_timeout_ms)
            except:
                print(f"WARNING: Selector {wait_selector} not found, proceeding anyway")

            # Generate PDF with A4 landscape orientation
            await page.pdf(
                path=output_path,
                format="A4",
                landscape=True,
                margin={"top": "15mm", "right": "15mm", "bottom": "15mm", "left": "15mm"},
                print_background=True,
            )

            await browser.close()

        print(f"✓ PDF generated: {output_path}")
        return True

    except Exception as e:
        print(f"ERROR generating PDF: {e}")
        return False


async def generate_test_pdfs(
    output_dir: str = "tests/pdfs",
    grids: list[int] | None = None,
) -> dict[str, str]:
    """
    Generate test PDFs for different grid sizes.

    Args:
        output_dir: Directory to save PDFs
        grids: Grid sizes to test (default: [3, 7, 13])

    Returns:
        Dictionary mapping grid size to PDF path
    """
    if grids is None:
        grids = [3, 7, 13]

    os.makedirs(output_dir, exist_ok=True)

    results = {}

    for grid_size in grids:
        # Generate synthetic HTML for grid
        html = generate_sample_html(grid_size)
        pdf_path = os.path.join(output_dir, f"test_grid_{grid_size}x{grid_size}.pdf")

        success = await generate_pdf(html, pdf_path)
        if success:
            results[f"{grid_size}x{grid_size}"] = pdf_path
        else:
            results[f"{grid_size}x{grid_size}"] = None

    return results


def generate_sample_html(grid_size: int = 3, include_branding: bool = True) -> str:
    """
    Generate sample HTML document for testing PDF layout.

    Args:
        grid_size: Grid dimension (e.g., 3 for 3x3)
        include_branding: Include ZURI branding, CTA, and QR Code

    Returns:
        Complete HTML document as string
    """
    total_points = grid_size * grid_size

    # Generate mock metrics
    metrics_html = "".join(
        f'<div class="metric"><span>{label}</span><strong>{value}</strong></div>'
        for label, value in [
            ("ARP", "5.2"),
            ("ATRP", "8.4"),
            ("SoLV", "45%"),
            ("IVL", "72"),
        ]
    )

    # Generate mock competitors table
    rows_html = "".join(
        f"""<tr>
            <td>{i + 1}</td>
            <td>Empresa Teste {i + 1}</td>
            <td>{round(100 - i * 10)}%</td>
            <td>{5 + i}</td>
            <td>{round(80 - i * 5)}%</td>
            <td>★ 4.{5 - (i % 5)}</td>
        </tr>"""
        for i in range(min(10, total_points))
    )

    base_html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Smart Grid PDF Test - Grid {grid_size}×{grid_size}</title>
    <link rel="stylesheet" href="/static/landing.css">
    <link rel="stylesheet" href="/static/print.css">
    <style>
        body {{ margin: 0; padding: 20px; background: white; }}
        .report {{ max-width: 1000px; margin: 0 auto; }}
        @media print {{
            body {{ padding: 0; }}
            .page-break-before {{ page-break-before: always; }}
        }}
    </style>
</head>
<body>
    <div class="report">
        <div class="card">
            <h2>Resultado do diagnóstico</h2>
            <p><strong>Palavra-chave:</strong> Teste de PDF</p>
            <p><strong>Grid:</strong> {grid_size}×{grid_size} ({total_points} pontos)</p>
            <p><strong>Pontos processados:</strong> {total_points}/{total_points}</p>
            <p><strong>Cobertura:</strong> 100%</p>
        </div>

        <div class="card">
            <h3>Indicadores de visibilidade</h3>
            <div class="metrics">
                {metrics_html}
            </div>
        </div>

        <div class="card">
            <div id="map" style="width: 100%; height: 300px; border: 1px solid #ccc; display: flex; align-items: center; justify-content: center; background: #f0f0f0;">
                <span style="color: #999;">Mapa centralizado com {total_points} pontos</span>
            </div>
        </div>

        <div class="card">
            <h3>Concorrentes observados</h3>
            <table>
                <thead>
                    <tr>
                        <th>#</th>
                        <th>Empresa</th>
                        <th>SoLV</th>
                        <th>ATRP</th>
                        <th>Found</th>
                        <th>Rating</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>
        </div>

        <div class="card">
            <h3>Interpretação</h3>
            <p>A pesquisa foi concluída em todos os {total_points} pontos. Os indicadores de visibilidade indicam posicionamento moderado na região analisada.</p>
            <p>Recomenda-se avaliar estratégias de otimização de Perfil da Empresa no Google para melhorar a presença local.</p>
        </div>

        <div class="legend">
            <span><i class="sw r1"></i>1–3</span>
            <span><i class="sw r2"></i>4–10</span>
            <span><i class="sw r3"></i>11–20</span>
            <span><i class="sw r4"></i>20+</span>
            <span><i class="sw no-results"></i>Sem resultados</span>
        </div>
    </div>
</body>
</html>"""

    if include_branding:
        return add_branding_to_html(base_html, include_cta=True, include_qrcode=True)
    return base_html


def add_branding_to_html(html_content: str, include_cta: bool = True, include_qrcode: bool = True) -> str:
    """
    Adiciona branding institucional e CTA comercial ao HTML antes de gerar PDF.
    
    Args:
        html_content: HTML original
        include_cta: Incluir seção de CTA comercial
        include_qrcode: Incluir QR Code na última página
        
    Returns:
        HTML modificado com branding
    """
    footer = get_footer_html()
    
    # Adicionar footer antes de </body>
    html_with_footer = html_content.replace("</body>", f"{footer}\n</body>")
    
    # Adicionar CTA se solicitado
    if include_cta:
        cta = get_cta_section_html()
        # Inserir CTA antes do footer
        html_with_footer = html_with_footer.replace(
            "</body>",
            f"{cta}\n</body>"
        )
    
    # Adicionar QR Code na última página se solicitado
    if include_qrcode:
        qrcode_base64 = generate_qrcode_base64(BRANDING.whatsapp_link)
        if qrcode_base64:
            qrcode_html = f"""
            <div style="
                page-break-before: always;
                text-align: center;
                padding: 40px 20px;
                font-family: Arial, sans-serif;
            ">
                <h2 style="margin-bottom: 20px; color: #0066cc;">Entre em contato</h2>
                <p style="font-size: 12px; color: #666; margin-bottom: 20px;">
                    Escaneie o QR Code para conversar com a ZURI no WhatsApp
                </p>
                <img src="{qrcode_base64}" alt="WhatsApp QR Code" style="width: 200px; height: 200px; margin: 20px auto;" />
                <p style="font-size: 11px; color: #333; margin-top: 20px;">
                    <strong>{BRANDING.whatsapp}</strong>
                </p>
                <p style="font-size: 10px; color: #666;">
                    {BRANDING.whatsapp_link}
                </p>
            </div>
            """
            html_with_footer = html_with_footer.replace("</body>", f"{qrcode_html}\n</body>")
    
    return html_with_footer


if __name__ == "__main__":
    # Test PDF generation
    print("Generating test PDFs...")
    results = asyncio.run(generate_test_pdfs())
    for grid, path in results.items():
        status = "✓" if path else "✗"
        print(f"{status} {grid}: {path}")
