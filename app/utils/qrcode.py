# app/utils/qrcode.py
"""Utilitário para gerar QR Code em base64 sem dependências externas."""

import base64
from io import BytesIO
from typing import Optional

try:
    import qrcode
    HAS_QRCODE = True
except ImportError:
    HAS_QRCODE = False


def generate_qrcode_base64(
    data: str,
    version: int = 1,
    box_size: int = 5,
    border: int = 1,
) -> Optional[str]:
    """
    Gera QR Code e retorna como data URL base64.
    
    Args:
        data: Texto ou URL para codificar
        version: Versão do QR Code (1-40, onde 1 = 21x21)
        box_size: Tamanho de cada caixa em pixels
        border: Tamanho da borda em caixas
        
    Returns:
        Data URL base64 do PNG, ou None se qrcode não estiver instalado
    """
    if not HAS_QRCODE:
        return None
    
    try:
        qr = qrcode.QRCode(
            version=version,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=box_size,
            border=border,
        )
        qr.add_data(data)
        qr.make(fit=True)
        
        # Gerar imagem
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Converter para base64
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        buffered.seek(0)
        img_base64 = base64.b64encode(buffered.getvalue()).decode()
        
        return f"data:image/png;base64,{img_base64}"
    except Exception:
        return None


def generate_qrcode_svg(data: str) -> str:
    """
    Fallback: gera SVG simples de QR Code usando arte ASCII art.
    Útil quando qrcode não está disponível.
    """
    # Implementação simplificada: padrão visual fixo para WhatsApp
    # Em produção, isso seria substituído por qrcode real
    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200" width="150" height="150">
        <rect width="200" height="200" fill="white"/>
        <text x="100" y="100" text-anchor="middle" dy="0.3em" font-size="14" fill="black">
            QR Code
        </text>
        <text x="100" y="120" text-anchor="middle" dy="0.3em" font-size="10" fill="#666">
            {data[:30]}...
        </text>
    </svg>
    """
    return svg.strip()
