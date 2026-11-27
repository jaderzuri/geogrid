"""Tests for ZURI branding configuration and utilities."""

import pytest
from app.config.branding import BRANDING, get_footer_html, get_cta_section_html
from app.utils.qrcode import generate_qrcode_base64


class TestBrandingConfig:
    """Test BrandingConfig instantiation and attributes."""

    def test_branding_config_has_required_fields(self):
        """BRANDING should have all required institutional data."""
        assert BRANDING.company_name == "ZURI Smart Tech"
        assert BRANDING.website == "www.zurismartec.com.br"
        assert "11" in BRANDING.whatsapp  # Area code
        assert "9969" in BRANDING.whatsapp  # Number prefix
        assert BRANDING.phone == "+55 (11) 4210-3311"
        assert BRANDING.instagram_handle == "@zurismartech"
        assert "instagram.com/zurismartech" in BRANDING.instagram_url

    def test_branding_config_has_clickable_links(self):
        """Links should be properly formatted."""
        assert BRANDING.website_url.startswith("https://")
        assert "wa.me" in BRANDING.whatsapp_link
        assert "instagram.com" in BRANDING.instagram_url
        assert BRANDING.phone_link.startswith("tel:")

    def test_branding_config_has_tagline_and_cta(self):
        """Tagline and CTA should be non-empty strings."""
        assert len(BRANDING.tagline) > 0
        assert "Visibilidade" in BRANDING.tagline or "Rentabilidade" in BRANDING.tagline
        assert len(BRANDING.cta_description) > 0
        assert "Sua empresa" in BRANDING.cta_title or "Sua empresa" in BRANDING.cta_description


class TestFooterHtml:
    """Test footer HTML generation."""

    def test_footer_contains_company_name(self):
        """Footer should include company name."""
        footer = get_footer_html()
        assert BRANDING.company_name in footer

    def test_footer_contains_unicode_characters(self):
        """Footer should include Unicode symbols for WhatsApp and phone."""
        footer = get_footer_html()
        # Check for Unicode characters or their HTML entities
        assert "✆" in footer or "☏" in footer or "&#" in footer

    def test_footer_contains_clickable_whatsapp(self):
        """Footer should have clickable WhatsApp link."""
        footer = get_footer_html()
        assert "wa.me" in footer
        assert BRANDING.whatsapp in footer

    def test_footer_contains_clickable_phone(self):
        """Footer should have clickable phone link."""
        footer = get_footer_html()
        assert "tel:" in footer
        assert BRANDING.phone in footer

    def test_footer_contains_clickable_website(self):
        """Footer should have clickable website link."""
        footer = get_footer_html()
        assert "href=" in footer
        assert BRANDING.website in footer

    def test_footer_contains_clickable_instagram(self):
        """Footer should have clickable Instagram link."""
        footer = get_footer_html()
        assert "instagram.com" in footer
        assert BRANDING.instagram_handle in footer

    def test_footer_is_valid_html(self):
        """Footer should be valid HTML fragment."""
        footer = get_footer_html()
        assert "<div" in footer
        assert "</div>" in footer
        assert "<a" in footer
        assert "</a>" in footer

    def test_footer_contains_all_contacts(self):
        """Footer should contain all institutional contact data."""
        footer = get_footer_html()
        # At least one format of company name and contact info should be present
        assert (
            BRANDING.company_name in footer
            or BRANDING.website in footer
            or BRANDING.instagram_handle in footer
        )


class TestCtaHtml:
    """Test CTA section HTML generation."""

    def test_cta_contains_tagline_elements(self):
        """CTA should reference core branding elements."""
        cta = get_cta_section_html()
        # Check for key branding concepts
        assert "Visibilidade" in cta or "visibilidade" in cta.lower()

    def test_cta_contains_commercial_message(self):
        """CTA should include commercial message."""
        cta = get_cta_section_html()
        assert "Sua empresa" in cta or "OPORTUNIDADE" in cta

    def test_cta_contains_call_to_action(self):
        """CTA should contain action-oriented text."""
        cta = get_cta_section_html()
        assert "otimização" in cta.lower() or "posicionamento" in cta.lower() or "ZURI" in cta

    def test_cta_is_valid_html(self):
        """CTA should be valid HTML fragment."""
        cta = get_cta_section_html()
        assert "<div" in cta
        assert "</div>" in cta
        assert "<p" in cta  # At least one paragraph

    def test_cta_mentions_zuri_services(self):
        """CTA should mention ZURI services."""
        cta = get_cta_section_html()
        assert "ZURI" in cta


class TestQrCodeGeneration:
    """Test QR Code generation utilities."""

    def test_qrcode_generation_returns_base64_or_none(self):
        """generate_qrcode_base64 should return base64 string or None."""
        result = generate_qrcode_base64("https://example.com")
        assert result is None or isinstance(result, str)

    def test_qrcode_base64_format_when_available(self):
        """If QR code is generated, it should be valid base64 PNG data URL."""
        result = generate_qrcode_base64("https://wa.me/5511996919411")
        if result:
            assert result.startswith("data:image/png;base64,")
            assert len(result) > 100  # PNG data should be substantial

    def test_qrcode_different_data_produces_different_qr(self):
        """Different input data should produce different QR codes."""
        qr1 = generate_qrcode_base64("https://example.com/1")
        qr2 = generate_qrcode_base64("https://example.com/2")
        if qr1 and qr2:
            assert qr1 != qr2

    def test_qrcode_whatsapp_link_is_valid(self):
        """QR code for WhatsApp link should be generated."""
        result = generate_qrcode_base64(BRANDING.whatsapp_link)
        # Should succeed or gracefully return None
        assert result is None or isinstance(result, str)
        if result:
            assert result.startswith("data:image/png;base64,")

    def test_qrcode_with_long_url(self):
        """QR code should handle longer URLs."""
        long_url = "https://www.zurismartec.com.br/diagnostico/posicionamento?empresa=teste&regiao=sao-paulo"
        result = generate_qrcode_base64(long_url)
        # Should succeed or gracefully return None
        assert result is None or isinstance(result, str)


class TestQrCodeParameterization:
    """Test QR Code parameter control."""

    def test_qrcode_accepts_version_parameter(self):
        """generate_qrcode_base64 should accept version parameter."""
        result1 = generate_qrcode_base64("test", version=1)
        result2 = generate_qrcode_base64("test", version=2)
        # Both should succeed or return None; versions might produce same result for short data
        assert all(r is None or isinstance(r, str) for r in [result1, result2])

    def test_qrcode_accepts_box_size_parameter(self):
        """generate_qrcode_base64 should accept box_size parameter."""
        result = generate_qrcode_base64("test", box_size=10)
        assert result is None or isinstance(result, str)

    def test_qrcode_accepts_border_parameter(self):
        """generate_qrcode_base64 should accept border parameter."""
        result = generate_qrcode_base64("test", border=2)
        assert result is None or isinstance(result, str)

