# app/utils/qrcode.py
"""Geração local de QR Codes para relatórios."""

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
    """Generate an actual QR Code as inline SVG."""
    data_url = generate_qrcode_svg_data_url(data)
    encoded = data_url.partition(",")[2]
    return base64.b64decode(encoded).decode("utf-8")


def generate_qrcode_svg_data_url(data: str) -> str:
    """Generate a real QR Code as an SVG data URL without requiring Pillow."""
    if not HAS_QRCODE:
        raise RuntimeError("The qrcode package is required to generate report QR Codes")

    from qrcode.image.svg import SvgPathImage

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=4,
    )
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(image_factory=SvgPathImage)
    buffered = BytesIO()
    image.save(buffered)
    encoded = base64.b64encode(buffered.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"
