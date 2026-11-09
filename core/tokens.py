"""Party links and QR codes (PNG). The QR encodes PUBLIC_BASE_URL + the random token."""

from __future__ import annotations

import io

import qrcode

from core.models import Party, Plaintiff
from core.settings import get_settings


def party_url(party: Party) -> str:
    return f"{get_settings().public_base_url}/r/{party.token}"


def policy_url(plaintiff: Plaintiff) -> str:
    return f"{get_settings().public_base_url}/policy/{plaintiff.portfolio_token}"


def qr_png(data: str, box_size: int = 8) -> bytes:
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=box_size, border=2
    )
    qr.add_data(data)
    qr.make(fit=True)
    image = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
