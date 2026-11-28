"""Fail-closed commercial processing gates for the Landing Page queue."""

import os
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from .config.maps import map_commercial_blocker


_REQUIRED_APPROVALS = (
    ("ZURI_COMMERCIAL_DIAGNOSTICS_ENABLED", "commercial_diagnostics_disabled"),
    ("ZURI_DATAFORSEO_COMMERCIAL_USE_APPROVED", "dataforseo_use_not_approved"),
    ("ZURI_GOOGLE_UPSTREAM_USE_APPROVED", "google_upstream_use_not_approved"),
)


def landing_diagnostics_gate() -> dict:
    """Return whether Landing Page requests may enter the paid processing queue."""

    blockers = [
        blocker
        for variable, blocker in _REQUIRED_APPROVALS
        if os.environ.get(variable, "").strip().lower() not in {"1", "true", "yes", "on"}
    ]
    map_blocker = map_commercial_blocker()
    if map_blocker:
        blockers.append(map_blocker)
    return {"enabled": not blockers, "blockers": blockers}


def real_express_gate() -> dict:
    """Keep the live Express-to-n8n path closed until all operational approvals exist."""

    landing_gate = landing_diagnostics_gate()
    blockers = list(landing_gate["blockers"])
    required = (
        ("ZURI_N8N_EXPRESS_PIPELINE_ENABLED", "n8n_express_pipeline_disabled"),
        (
            "ZURI_GOOGLE_COORDINATE_STORAGE_APPROVED",
            "google_coordinate_storage_not_approved",
        ),
    )
    blockers.extend(
        blocker
        for variable, blocker in required
        if not _enabled(os.environ.get(variable))
    )
    if not os.environ.get("ZURI_N8N_LEADS_WEBHOOK_URL", "").strip():
        blockers.append("n8n_leads_webhook_not_configured")
    try:
        products = product_catalog()
    except ValueError:
        blockers.append("product_configuration_invalid")
    else:
        express = next(item for item in products if item["id"] == "express")
        if express["grid_size"] != 3 or express["keywords"] != 1:
            blockers.append("express_must_use_one_keyword_and_grid_3x3")
        if not _enabled(os.environ.get("ZURI_PRODUCT_EXPRESS_ENABLED", "true")):
            blockers.append("express_product_disabled")
    return {"enabled": not blockers, "blockers": blockers}


def prospecting_storage_approved() -> bool:
    """Require a separate approval before retaining or exporting prospecting data."""

    return _enabled(os.environ.get("ZURI_PROSPECTING_DATA_USE_APPROVED"))


def product_catalog() -> list[dict]:
    """Return centrally configured experimental products; sales stay disabled."""

    def configured_integer(variable: str, default: int, allowed: set[int]) -> int:
        try:
            value = int(os.environ.get(variable, str(default)))
        except ValueError as error:
            raise ValueError(f"{variable} must be an integer") from error
        if value not in allowed:
            raise ValueError(f"{variable} must be one of {sorted(allowed)}")
        return value

    definitions = (
        (
            "express",
            "Express",
            "ZURI_PRICE_EXPRESS_BRL",
            "0.00",
            "one_time",
            configured_integer("ZURI_PRODUCT_EXPRESS_GRID_SIZE", 3, {3, 7}),
            configured_integer("ZURI_PRODUCT_EXPRESS_KEYWORDS", 1, set(range(1, 11))),
            False,
        ),
        (
            "professional",
            "Professional",
            "ZURI_PRICE_PROFESSIONAL_BRL",
            "29.90",
            "one_time",
            configured_integer("ZURI_PRODUCT_PROFESSIONAL_GRID_SIZE", 7, {3, 7}),
            configured_integer("ZURI_PRODUCT_PROFESSIONAL_KEYWORDS", 1, set(range(1, 11))),
            False,
        ),
        (
            "monitor",
            "Monitor",
            "ZURI_PRICE_MONITOR_BRL",
            "97.00",
            "monthly_assisted",
            None,
            1,
            True,
        ),
    )
    products = []
    for (
        product_id,
        name,
        variable,
        default_price,
        billing,
        grid_size,
        keywords,
        assisted,
    ) in definitions:
        try:
            price = Decimal(os.environ.get(variable, default_price).strip())
        except InvalidOperation as error:
            raise ValueError(f"{variable} must be a decimal BRL amount") from error
        if (
            not price.is_finite()
            or price < 0
            or price.as_tuple().exponent < -2
        ):
            raise ValueError(
                f"{variable} must be a non-negative BRL amount with at most two decimals"
            )
        products.append(
            {
                "id": product_id,
                "name": name,
                "price_brl": f"{price:.2f}",
                "billing": billing,
                "grid_size": grid_size,
                "keywords": keywords,
                "assisted": assisted,
                "available": False,
                "checkout_url": None,
            }
        )
    return products


def _enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def simulator_enabled() -> bool:
    """Simulation requires explicit opt-in and never enables a real provider."""

    return _enabled(os.environ.get("ZURI_COMMERCIAL_SIMULATOR_ENABLED"))


def simulator_ready() -> bool:
    return (
        simulator_enabled()
        and os.environ.get("GEOGRID_BUSINESS_FINDER_PROVIDER", "").strip().lower()
        == "simulator"
    )


def _simulated_checkout_url(product_id: str) -> str | None:
    if not simulator_enabled():
        return None
    value = os.environ.get(
        f"ZURI_DOM_CHECKOUT_URL_{product_id.upper()}", ""
    ).strip()
    if not value:
        return None
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.lower().endswith(".example.test")
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("simulated checkout URLs must use an https *.example.test host")
    return value


def commercial_product_catalog() -> list[dict]:
    """Product configuration exposed to the simulated commercial experience."""

    products = product_catalog()
    for product in products:
        configured_enabled = _enabled(
            os.environ.get(
                f"ZURI_PRODUCT_{product['id'].upper()}_ENABLED",
                "true",
            )
        )
        product["configured_enabled"] = configured_enabled
        product["available"] = simulator_ready() and configured_enabled
        product["diagnostic_enabled"] = (
            product["available"] and product["id"] in {"express", "professional"}
        )
        product["checkout_url"] = (
            _simulated_checkout_url(product["id"])
            if product["id"] == "professional" and product["available"]
            else None
        )
        product["points"] = (
            product["grid_size"] ** 2 if product["grid_size"] is not None else None
        )
        product["mode"] = "simulated" if simulator_enabled() else "disabled"
    return products


def commercial_limits() -> dict[str, int]:
    """Read bounded operational limits for the commercial simulation."""

    defaults = {
        "daily_diagnostics": 100,
        "daily_points": 2500,
        "pending_diagnostics": 25,
        "pending_points": 500,
    }
    limits = {}
    for key, default in defaults.items():
        variable = f"ZURI_COMMERCIAL_{key.upper()}_LIMIT"
        try:
            value = int(os.environ.get(variable, str(default)))
        except ValueError as error:
            raise ValueError(f"{variable} must be a positive integer") from error
        if not 1 <= value <= 100_000:
            raise ValueError(f"{variable} must be between 1 and 100000")
        limits[key] = value
    return limits
