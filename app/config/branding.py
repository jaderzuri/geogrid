# app/config/branding.py
"""Configuração centralizada de identidade visual e branding ZURI."""

from dataclasses import dataclass
from html import escape
from typing import NamedTuple


class BrandingConfig(NamedTuple):
    """Configuração de identidade visual da ZURI."""

    # Informações institucionais
    company_name: str = "ZURI Smartech"
    tagline: str = "Aumentar Visibilidade, Fortalecer Credibilidade e Viabilizar Rentabilidade"
    
    # Contato
    whatsapp: str = "+55 (11) 99691-9411"
    whatsapp_link: str = "https://wa.me/5511996919411"
    whatsapp_icon: str = "✆"  # U+2706
    
    phone: str = "+55 (11) 4210-3311"
    phone_link: str = "tel:+551142103311"
    phone_icon: str = "☏"  # U+260F
    
    # Web
    website: str = "zurismartech.com.br"
    website_url: str = "https://zurismartech.com.br"
    
    instagram_handle: str = "@zurismartech"
    instagram_url: str = "https://www.instagram.com/zurismartech/"
    
    # CTA Comercial
    cta_title: str = "Sua empresa pode conquistar mais visibilidade no Google"
    cta_description: str = "Conheça as soluções de otimização e posicionamento digital da ZURI."
    
    # Estilo
    footer_font: str = "Arial, sans-serif"
    footer_font_size: str = "9px"
    footer_color: str = "#333333"
    footer_line_height: str = "1.4"


# Instância global padrão
BRANDING = BrandingConfig()


def get_footer_html() -> str:
    """Gera HTML do rodapé institucional."""
    return f"""
<div style="
    text-align: center;
    font-family: {BRANDING.footer_font};
    font-size: {BRANDING.footer_font_size};
    color: {BRANDING.footer_color};
    line-height: {BRANDING.footer_line_height};
    padding: 10px 0;
    border-top: 1px solid #cccccc;
    margin-top: 15px;
">
    <p style="margin: 2px 0;">
        <strong>{BRANDING.company_name}</strong>
    </p>
    <p style="margin: 2px 0;">
        {BRANDING.whatsapp_icon} <a href="{BRANDING.whatsapp_link}" style="color: #25D366; text-decoration: none;">{BRANDING.whatsapp}</a> 
        {BRANDING.phone_icon} <a href="{BRANDING.phone_link}" style="color: #0066cc; text-decoration: none;">{BRANDING.phone}</a>
    </p>
    <p style="margin: 2px 0;">
        <a href="{BRANDING.website_url}" style="color: #0066cc; text-decoration: none;">{BRANDING.website}</a> 
        | <a href="{BRANDING.instagram_url}" style="color: #E4405F; text-decoration: none;">{BRANDING.instagram_handle}</a>
    </p>
    <p style="margin: 2px 0; font-size: 8px; color: #666666;">
        <em>"{BRANDING.tagline}"</em>
    </p>
</div>
"""


def get_cta_section_html() -> str:
    """Gera seção de CTA comercial."""
    return f"""
<div style="
    background-color: #f5f5f5;
    border-left: 4px solid #0066cc;
    padding: 15px;
    margin: 20px 0;
    font-family: {BRANDING.footer_font};
    page-break-inside: avoid;
">
    <p style="
        margin: 0 0 8px 0;
        font-weight: bold;
        font-size: 11px;
        color: #0066cc;
    ">
        OPORTUNIDADE COMERCIAL
    </p>
    <p style="
        margin: 0 0 8px 0;
        font-size: 10px;
        color: #333333;
    ">
        {BRANDING.cta_title}
    </p>
    <p style="
        margin: 0;
        font-size: 9px;
        color: #666666;
        line-height: 1.4;
    ">
        {BRANDING.cta_description}
    </p>
</div>
"""


def get_print_report_sections(qr_data_uri: str) -> tuple[str, str]:
    """Return institutional introduction and closing sections for the UI report."""
    if not qr_data_uri.startswith("data:image/svg+xml;base64,"):
        raise ValueError("A generated SVG QR Code data URL is required")

    company = escape(BRANDING.company_name)
    whatsapp = escape(BRANDING.whatsapp)
    whatsapp_link = escape(BRANDING.whatsapp_link, quote=True)
    phone = escape(BRANDING.phone)
    phone_link = escape(BRANDING.phone_link, quote=True)
    website = escape(BRANDING.website)
    website_url = escape(BRANDING.website_url, quote=True)
    instagram = escape(BRANDING.instagram_handle)
    instagram_url = escape(BRANDING.instagram_url, quote=True)
    tagline = escape(BRANDING.tagline)
    qr = escape(qr_data_uri, quote=True)

    intro = f"""
<section class="print-only print-intro" aria-label="Apresentação do relatório">
  <div class="print-brand">Smart Grid by ZURI <span>{company}</span></div>
  <p>Este diagnóstico apresenta os resultados observados para a palavra-chave e os pontos desta grade no momento da pesquisa. As posições por ponto são observações locais; o ATRP representa a posição média calculada para a empresa.</p>
</section>
"""
    closing = f"""
<section class="print-only print-closing" aria-label="Contato institucional">
  <div class="print-cta">
    <div>
      <h2>Conheça a otimização do Perfil da Empresa no Google</h2>
      <p>Converse com a {company} para conhecer os serviços de otimização e posicionamento digital para sua empresa.</p>
      <p class="print-tagline">{tagline}</p>
      <p><a href="{whatsapp_link}" target="_blank" rel="noopener noreferrer">{escape(BRANDING.whatsapp_icon)} {whatsapp}</a></p>
      <p><a href="{phone_link}">{escape(BRANDING.phone_icon)} {phone}</a></p>
      <p><a href="{website_url}" target="_blank" rel="noopener noreferrer">{website}</a> · <a href="{instagram_url}" target="_blank" rel="noopener noreferrer">{instagram}</a></p>
    </div>
    <a class="print-qr-link" href="{whatsapp_link}" target="_blank" rel="noopener noreferrer">
      <img src="{qr}" alt="QR Code para conversar com a ZURI pelo WhatsApp">
      <span>Aponte a câmera para conversar pelo WhatsApp</span>
    </a>
  </div>
</section>
"""
    return intro, closing
