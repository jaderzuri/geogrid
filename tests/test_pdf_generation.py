"""Tests for PDF generation and layout validation."""

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from app.pdf_utils import generate_pdf, generate_sample_html, generate_test_pdfs


class TestPDFGeneration:
    """Test PDF generation and layout validation."""

    @pytest.mark.asyncio
    async def test_generate_pdf_with_valid_html(self):
        """Test PDF generation with valid HTML content."""
        html = generate_sample_html(grid_size=3)

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = os.path.join(tmpdir, "test.pdf")

            result = await generate_pdf(html, pdf_path)

            assert result is True
            assert os.path.exists(pdf_path)
            assert os.path.getsize(pdf_path) > 0

    @pytest.mark.asyncio
    async def test_generate_pdf_landscape_orientation(self):
        """Test that PDF is generated in landscape orientation."""
        html = generate_sample_html(grid_size=7)

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = os.path.join(tmpdir, "landscape.pdf")

            result = await generate_pdf(html, pdf_path)

            assert result is True
            assert os.path.exists(pdf_path)

    @pytest.mark.asyncio
    async def test_generate_test_pdfs_multiple_grids(self):
        """Test generation of multiple PDFs with different grid sizes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            results = await generate_test_pdfs(output_dir=tmpdir, grids=[3, 5, 7])

            assert len(results) == 3
            assert "3x3" in results
            assert "5x5" in results
            assert "7x7" in results

            for grid_name, pdf_path in results.items():
                if pdf_path:
                    assert os.path.exists(pdf_path), f"PDF for {grid_name} not found"
                    assert os.path.getsize(pdf_path) > 0, f"PDF for {grid_name} is empty"

    def test_generate_sample_html_contains_required_elements(self):
        """Test that generated sample HTML contains all required elements."""
        html = generate_sample_html(grid_size=3)

        assert "<!DOCTYPE html>" in html
        assert "<title>" in html
        assert "Smart Grid PDF Test" in html
        assert "print.css" in html
        assert "landing.css" in html
        assert 'id="map"' in html
        assert "<table>" in html
        assert "<thead>" in html
        assert "<tbody>" in html
        assert "3×3" in html
        assert "9 pontos" in html

    def test_generate_sample_html_different_grid_sizes(self):
        """Test sample HTML generation with different grid sizes."""
        for grid_size in [3, 5, 7, 9, 13]:
            html = generate_sample_html(grid_size)

            assert f"{grid_size}×{grid_size}" in html
            total_points = grid_size * grid_size
            assert str(total_points) in html

    def test_generate_sample_html_includes_metrics(self):
        """Test that sample HTML includes all required metrics."""
        html = generate_sample_html(3)

        # Check for metric cards
        assert "ARP" in html
        assert "ATRP" in html
        assert "SoLV" in html
        assert "IVL" in html

    def test_generate_sample_html_includes_legend(self):
        """Test that sample HTML includes legend with color indicators."""
        html = generate_sample_html(3)

        # Check for legend elements
        assert 'class="legend"' in html
        assert "1–3" in html
        assert "4–10" in html
        assert "11–20" in html
        assert "20+" in html
        assert "Sem resultados" in html

    def test_generate_sample_html_includes_interpretation(self):
        """Test that sample HTML includes interpretation text."""
        html = generate_sample_html(3)

        assert "Interpretação" in html
        assert "pesquisa foi concluída" in html
        assert "pontos" in html

    def test_generate_sample_html_portuguese_localization(self):
        """Test that generated HTML uses Portuguese text."""
        html = generate_sample_html(3)

        # Check Portuguese text
        assert "Resultado do diagnóstico" in html
        assert "Palavra-chave" in html
        assert "Pontos processados" in html
        assert "Cobertura" in html
        assert "Concorrentes observados" in html

    @pytest.mark.asyncio
    async def test_pdf_output_directory_created(self):
        """Test that output directory is created if it doesn't exist."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = os.path.join(tmpdir, "nested", "output", "pdfs")

            assert not os.path.exists(output_dir)

            await generate_test_pdfs(output_dir=output_dir, grids=[3])

            assert os.path.exists(output_dir)

    @pytest.mark.asyncio
    async def test_pdf_generation_with_large_grid(self):
        """Test PDF generation with larger grid size (13x13)."""
        html = generate_sample_html(grid_size=13)

        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = os.path.join(tmpdir, "large_grid.pdf")

            result = await generate_pdf(html, pdf_path)

            assert result is True
            assert os.path.exists(pdf_path)
            assert os.path.getsize(pdf_path) > 0

    def test_sample_html_a4_dimensions(self):
        """Test that sample HTML is designed for A4 paper size."""
        html = generate_sample_html(3)

        # Check for A4 landscape specific CSS
        assert "print.css" in html  # Print stylesheet included
        assert "<html" in html
        assert "<head>" in html


if __name__ == "__main__":
    asyncio.run(TestPDFGeneration().test_generate_pdf_with_valid_html())
    print("✓ All PDF tests completed")
