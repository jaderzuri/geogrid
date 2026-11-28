"""Public map configuration and provider-specific commercial approval."""

import os
from urllib.parse import urlsplit


def map_settings() -> tuple[str, str]:
    provider = os.environ.get("GEOGRID_MAP_PROVIDER", "openfreemap").strip().lower()
    if provider == "maptiler":
        return provider, ""
    if provider != "openfreemap":
        return "unavailable", ""
    style = os.environ.get("GEOGRID_MAP_STYLE", "liberty").strip().lower()
    if style not in {"liberty", "bright", "positron"}:
        return "unavailable", ""
    url = os.environ.get("GEOGRID_MAP_STYLE_URL", "").strip()
    url = url or f"https://tiles.openfreemap.org/styles/{style}"
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return "unavailable", ""
    except ValueError:
        return "unavailable", ""
    return provider, url


def map_commercial_blocker() -> str | None:
    provider, _ = map_settings()
    flags = {
        "openfreemap": (
            "GEOGRID_MAP_PROVIDER_COMMERCIAL_USE_APPROVED",
            "openfreemap_commercial_use_unapproved",
        ),
        "maptiler": (
            "GEOGRID_MAPTILER_COMMERCIAL_PLAN_CONFIRMED",
            "maptiler_commercial_plan_unconfirmed",
        ),
    }
    if provider not in flags:
        return "map_provider_unavailable"
    variable, blocker = flags[provider]
    if os.environ.get(variable, "").strip().lower() in {"1", "true", "yes", "on"}:
        return None
    return blocker
