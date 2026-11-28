"""Paid fallback fetcher: DataForSEO Google Maps SERP API (~$0.002/point on the live endpoint).

Set DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD and GEOGRID_FETCHER=dataforseo.
Business lookup (one request per new business) still uses the free Playwright resolver.
"""
import os
from decimal import Decimal, InvalidOperation

import httpx

from .base import MAX_RESULTS, Fetcher, FetchStatusError

ENDPOINT = "https://api.dataforseo.com/v3/serp/google/maps/live/advanced"

_PENDING_CODES = {20100, 40601, 40602}
_ERROR_CATEGORIES = {
    40000: "request_error",
    40001: "request_error",
    40002: "request_error",
    40003: "request_error",
    40004: "request_error",
    40006: "request_error",
    40007: "request_notice",
    401: "authentication_error",
    40100: "authentication_error",
    40101: "technical_error",
    40103: "technical_error",
    40104: "authentication_error",
    40106: "partial_results",
    40105: "task_unavailable",
    40107: "technical_error",
    402: "insufficient_balance",
    40200: "insufficient_balance",
    40201: "access_denied",
    40202: "rate_limit",
    40203: "budget_limit",
    40205: "duplicate_task_limit",
    40206: "duplicate_task_limit",
    40207: "access_denied",
    40209: "rate_limit",
    40210: "insufficient_balance",
    40400: "request_error",
    40401: "task_unavailable",
    40402: "request_error",
    40403: "task_unavailable",
    40404: "request_error",
    40405: "request_error",
    40406: "request_error",
    40407: "request_error",
    40408: "request_error",
    40501: "request_error",
    40502: "request_error",
    40503: "request_error",
    40504: "request_error",
    40505: "request_error",
    40506: "request_error",
    50000: "technical_error",
    50001: "technical_error",
    50100: "request_error",
    50301: "technical_error",
    50303: "technical_error",
    50304: "technical_error",
    50401: "technical_error",
    50402: "technical_error",
}


def _status_code(value) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


class DataForSEOFetcher:
    def __init__(self, resolver: Fetcher, login: str | None = None, password: str | None = None, zoom: int = 14,
                 lang: str | None = None):
        login = login or os.environ.get("DATAFORSEO_LOGIN")
        password = password or os.environ.get("DATAFORSEO_PASSWORD")
        if not login or not password:
            raise RuntimeError("DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD must be set")
        self.zoom = zoom
        self.lang = (lang or os.environ.get("DATAFORSEO_LANGUAGE_CODE", "pt")).strip() or "pt"
        self._client = httpx.AsyncClient(auth=(login, password), timeout=90)
        self._resolver = resolver

    @staticmethod
    def _raise_status(
        code: int | None,
        message: str | None,
        *,
        results: list[dict] | None = None,
        cost_usd: Decimal | None = None,
    ) -> None:
        category = _ERROR_CATEGORIES.get(code, "technical_error")
        if code == 40102:
            outcome, category = "no_results", "no_results"
        elif code in _PENDING_CODES:
            outcome, category = "pending", "task_pending"
        else:
            outcome = "error"
        detail = message or "DataForSEO returned an unspecified status"
        prefix = f"DataForSEO {code}" if code is not None else "DataForSEO"
        raise FetchStatusError(
            f"{prefix}: {detail}",
            outcome=outcome,
            category=category,
            api_status_code=code,
            api_status_message=message,
            results=results,
            cost_usd=cost_usd,
        )

    @staticmethod
    def _task_cost(payload: dict) -> Decimal | None:
        tasks = payload.get("tasks")
        if not isinstance(tasks, list) or len(tasks) != 1 or not isinstance(tasks[0], dict):
            return None
        value = tasks[0].get("cost")
        if value is None or isinstance(value, bool):
            return None
        try:
            cost = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            return None
        if not cost.is_finite() or cost < 0:
            return None
        total = payload.get("cost")
        if total is not None:
            if isinstance(total, bool):
                return None
            try:
                total_cost = Decimal(str(total))
            except (InvalidOperation, ValueError, TypeError):
                return None
            if not total_cost.is_finite() or total_cost != cost:
                return None
        return cost

    @staticmethod
    def _parse_items(task: dict) -> list[dict]:
        result = task.get("result")
        if result is None or result == []:
            return []
        if not isinstance(result, list) or not isinstance(result[0], dict):
            raise ValueError("invalid result structure")
        items = result[0].get("items") or []
        if not isinstance(items, list):
            raise ValueError("invalid items structure")
        out = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("invalid result item")
            if item.get("type") != "maps_search":
                continue
            rating = item.get("rating") or {}
            out.append({
                "name": item.get("title"),
                "cid": str(item["cid"]) if item.get("cid") else None,
                "place_id": item.get("place_id"),
                "lat": item.get("latitude"),
                "lng": item.get("longitude"),
                "rating": rating.get("value"),
                "reviews": rating.get("votes_count"),
                "category": item.get("category"),
                "address": item.get("address"),
                "phone": item.get("phone"),
                "url": item.get("url"),
                "position": item.get("position"),
            })
        return out[:MAX_RESULTS]

    @classmethod
    def _raise_http_error(cls, response: httpx.Response) -> None:
        code = response.status_code
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            provider_code = _status_code(payload.get("status_code"))
            provider_message = payload.get("status_message")
            if provider_code is not None:
                cls._raise_status(
                    provider_code,
                    provider_message,
                    cost_usd=cls._task_cost(payload),
                )

        category = {
            401: "authentication_error",
            402: "insufficient_balance",
            404: "request_error",
            429: "rate_limit",
            500: "technical_error",
        }.get(code, "technical_error")
        message = (
            payload.get("status_message")
            if isinstance(payload, dict) and isinstance(payload.get("status_message"), str)
            else response.reason_phrase or "HTTP request failed"
        )
        raise FetchStatusError(
            f"DataForSEO HTTP {code}: {message}",
            category=category,
            api_status_code=code,
            api_status_message=message,
            cost_usd=cls._task_cost(payload) if isinstance(payload, dict) else None,
        )

    async def fetch(self, keyword: str, lat: float, lng: float) -> list[dict]:
        results, _ = await self.fetch_with_cost(keyword, lat, lng)
        return results

    async def fetch_with_cost(
        self, keyword: str, lat: float, lng: float
    ) -> tuple[list[dict], Decimal | None]:
        body = [{"keyword": keyword, "location_coordinate": f"{lat},{lng},{self.zoom}z",
                 "language_code": self.lang, "depth": MAX_RESULTS}]
        try:
            resp = await self._client.post(ENDPOINT, json=body)
        except httpx.HTTPError as error:
            raise FetchStatusError(
                "DataForSEO HTTP transport failure",
                category="technical_error",
            ) from error

        if resp.is_error:
            self._raise_http_error(resp)

        try:
            payload = resp.json()
        except ValueError as error:
            raise FetchStatusError(
                "DataForSEO returned invalid JSON",
                category="technical_error",
            ) from error

        if not isinstance(payload, dict):
            raise FetchStatusError(
                "DataForSEO returned an invalid response object",
                category="technical_error",
            )

        top_code = _status_code(payload.get("status_code"))
        top_message = payload.get("status_message")
        if not isinstance(top_message, str):
            top_message = None
        cost_usd = self._task_cost(payload)
        if top_code is not None and top_code != 20000:
            self._raise_status(top_code, top_message, cost_usd=cost_usd)

        tasks = payload.get("tasks")
        if not isinstance(tasks, list) or not tasks or not isinstance(tasks[0], dict):
            self._raise_status(
                top_code,
                top_message or "DataForSEO response did not contain a task",
                cost_usd=cost_usd,
            )

        task = tasks[0]
        task_code = _status_code(task.get("status_code"))
        task_message = task.get("status_message")
        if not isinstance(task_message, str):
            task_message = None
        if task_code != 20000:
            try:
                results = self._parse_items(task) if task_code == 40106 else []
            except (AttributeError, TypeError, ValueError) as error:
                raise FetchStatusError(
                    f"DataForSEO {task_code}: Invalid result payload",
                    category="technical_error",
                    api_status_code=task_code,
                    api_status_message=task_message,
                    cost_usd=cost_usd,
                ) from error
            self._raise_status(
                task_code,
                task_message,
                results=results,
                cost_usd=cost_usd,
            )

        try:
            results = self._parse_items(task)
        except (AttributeError, TypeError, ValueError) as error:
            raise FetchStatusError(
                f"DataForSEO {task_code}: Invalid result payload",
                category="technical_error",
                api_status_code=task_code,
                api_status_message=task_message,
                cost_usd=cost_usd,
            ) from error
        if not results:
            raise FetchStatusError(
                "DataForSEO returned no organic Maps results",
                outcome="no_results",
                category="no_results",
                api_status_code=task_code,
                api_status_message=task_message,
                cost_usd=cost_usd,
            )
        return results, cost_usd

    async def resolve_business(self, query: str) -> dict:
        return await self._resolver.resolve_business(query)

    async def close(self) -> None:
        await self._client.aclose()
        await self._resolver.close()
