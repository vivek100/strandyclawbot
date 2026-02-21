"""Datadog trace query helpers for ClawdBot."""

from __future__ import annotations

import datetime
import os
from typing import Any

try:
    import httpx
except Exception:  # pragma: no cover
    httpx = None

try:
    from datadog_api_client import ApiClient, Configuration
    from datadog_api_client.exceptions import ApiException
    from datadog_api_client.v2.api.logs_api import LogsApi
    from datadog_api_client.v2.api.spans_api import SpansApi
    from datadog_api_client.v2.model.logs_list_request import LogsListRequest
    from datadog_api_client.v2.model.logs_list_request_page import LogsListRequestPage
    from datadog_api_client.v2.model.logs_query_filter import LogsQueryFilter
    from datadog_api_client.v2.model.logs_sort import LogsSort
    from datadog_api_client.v2.model.spans_list_request import SpansListRequest
    from datadog_api_client.v2.model.spans_list_request_attributes import SpansListRequestAttributes
    from datadog_api_client.v2.model.spans_list_request_data import SpansListRequestData
    from datadog_api_client.v2.model.spans_list_request_page import SpansListRequestPage
    from datadog_api_client.v2.model.spans_list_request_type import SpansListRequestType
    from datadog_api_client.v2.model.spans_query_filter import SpansQueryFilter
    from datadog_api_client.v2.model.spans_sort import SpansSort
except Exception:  # pragma: no cover
    ApiClient = None
    ApiException = None
    Configuration = None
    LogsApi = None
    SpansApi = None
    LogsListRequest = None
    LogsListRequestPage = None
    LogsQueryFilter = None
    LogsSort = None
    SpansListRequest = None
    SpansListRequestAttributes = None
    SpansListRequestData = None
    SpansListRequestPage = None
    SpansListRequestType = None
    SpansQueryFilter = None
    SpansSort = None


class DatadogClientError(RuntimeError):
    """Raised when Datadog client operations fail."""


def _iso_utc(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _dd_service() -> str:
    return os.getenv("DD_SERVICE", "clawdbot-agent")


def _get_dd_config() -> Any:
    if Configuration is None:
        raise DatadogClientError("datadog-api-client is not installed. Run: pip install datadog-api-client")

    api_key = os.getenv("DD_API_KEY")
    app_key = os.getenv("DD_APP_KEY")
    if not api_key:
        raise DatadogClientError("DD_API_KEY is not set.")
    if not app_key:
        raise DatadogClientError("DD_APP_KEY is not set. Required for Datadog read APIs.")

    config = Configuration()
    config.api_key["apiKeyAuth"] = api_key
    config.api_key["appKeyAuth"] = app_key
    config.server_variables["site"] = os.getenv("DD_SITE", "datadoghq.com")
    return config


def _safe_get(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _textify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        if "value" in value:
            return _textify(value.get("value"))
        return str(value)
    return str(value)


def _extract_span_data(item: Any) -> dict[str, Any]:
    attrs = _safe_get(item, "attributes", {}) or {}
    meta = _safe_get(attrs, "meta", {}) or {}
    tags = _safe_get(attrs, "tags", {}) or {}
    error = _safe_get(attrs, "error", {}) or {}

    # Supports both standard spans API shape (meta.input/meta.output)
    # and LLM Observability export shape (attributes.input/attributes.output).
    input_meta = _safe_get(meta, "input", {}) or _safe_get(attrs, "input", {})
    output_meta = _safe_get(meta, "output", {}) or _safe_get(attrs, "output", {})

    return {
        "span_id": _safe_get(item, "id"),
        "trace_id": _safe_get(attrs, "trace_id"),
        "start": _safe_get(attrs, "start_timestamp"),
        "end": _safe_get(attrs, "end_timestamp"),
        "duration_ms": float(_safe_get(attrs, "duration", 0) or 0) / 1_000_000,
        "kind": _safe_get(attrs, "type", "") or _safe_get(attrs, "kind", ""),
        "resource_name": _safe_get(attrs, "resource_name", ""),
        "session_id": _safe_get(tags, "session_id", ""),
        "input": _textify(input_meta),
        "output": _textify(output_meta),
        "error": _safe_get(error, "message", "") or "",
    }


def _list_llmobs_spans(query: str, hours_back: int, limit: int, span_kind: str | None = None) -> list[dict[str, Any]]:
    if httpx is None:
        raise DatadogClientError("httpx is not installed. Run: pip install httpx")
    api_key = os.getenv("DD_API_KEY")
    app_key = os.getenv("DD_APP_KEY")
    if not api_key:
        raise DatadogClientError("DD_API_KEY is not set.")
    if not app_key:
        raise DatadogClientError("DD_APP_KEY is not set. Required for Datadog read APIs.")

    site = os.getenv("DD_SITE", "datadoghq.com")
    ml_app = os.getenv("DD_LLMOBS_ML_APP", "clawdbot")
    now = datetime.datetime.now(datetime.UTC)
    from_time = now - datetime.timedelta(hours=hours_back)

    params: dict[str, str] = {
        "filter[from]": _iso_utc(from_time),
        "filter[to]": _iso_utc(now),
        "filter[ml_app]": ml_app,
        "page[limit]": str(limit),
        "sort": "-timestamp",
    }
    if span_kind:
        params["filter[span_kind]"] = span_kind
    if query.strip():
        params["filter[query]"] = query

    url = f"https://api.{site}/api/v2/llm-obs/v1/spans/events"
    headers = {
        "DD-API-KEY": api_key,
        "DD-APPLICATION-KEY": app_key,
    }

    response = httpx.get(url, params=params, headers=headers, timeout=30)
    if response.status_code >= 400:
        raise DatadogClientError(f"LLMObs query failed ({response.status_code}): {response.text[:300]}")
    payload = response.json()
    return [_extract_span_data(item) for item in (payload.get("data") or [])]


def _list_spans(query: str, hours_back: int, limit: int) -> list[dict[str, Any]]:
    if SpansApi is None:
        raise DatadogClientError("datadog-api-client is not installed. Run: pip install datadog-api-client")

    now = datetime.datetime.now(datetime.UTC)
    from_time = now - datetime.timedelta(hours=hours_back)

    body = SpansListRequest(
        data=SpansListRequestData(
            type=SpansListRequestType.SEARCH_REQUEST,
            attributes=SpansListRequestAttributes(
                filter=SpansQueryFilter(
                    query=query,
                    _from=_iso_utc(from_time),
                    to=_iso_utc(now),
                ),
                sort=SpansSort.TIMESTAMP_DESCENDING,
                page=SpansListRequestPage(limit=limit),
            ),
        )
    )

    config = _get_dd_config()
    with ApiClient(config) as api_client:
        try:
            response = SpansApi(api_client).list_spans(body=body)
            return [_extract_span_data(item) for item in (_safe_get(response, "data", []) or [])]
        except Exception as exc:
            status = getattr(exc, "status", None)
            # Datadog's spans endpoint can return transient/internal 5xx in some orgs.
            # Fall back to logs search so history tools still work.
            if status in {500, 501, 502, 503, 504}:
                return _list_logs_fallback(api_client, query=query, from_time=from_time, now=now, limit=limit)
            raise


def _extract_log_data(item: Any) -> dict[str, Any]:
    attrs = _safe_get(item, "attributes", {}) or {}
    message = _safe_get(attrs, "message", "") or ""
    timestamp = _safe_get(attrs, "timestamp", "")
    service = _safe_get(attrs, "service", "") or ""

    return {
        "span_id": _safe_get(item, "id"),
        "trace_id": "",
        "start": timestamp,
        "end": timestamp,
        "duration_ms": 0.0,
        "kind": "log",
        "resource_name": service,
        "session_id": "",
        "input": message,
        "output": "",
        "error": "",
    }


def _list_logs_fallback(api_client: Any, query: str, from_time: datetime.datetime, now: datetime.datetime, limit: int) -> list[dict[str, Any]]:
    if LogsApi is None:
        return []

    logs_query = query
    # Span-specific filters don't apply to logs query language.
    logs_query = logs_query.replace("@span.kind:agent", "").replace("@span.kind:tool", "").strip()
    if not logs_query:
        logs_query = f"service:{_dd_service()}"

    body = LogsListRequest(
        filter=LogsQueryFilter(
            query=logs_query,
            _from=_iso_utc(from_time),
            to=_iso_utc(now),
        ),
        page=LogsListRequestPage(limit=limit),
        sort=LogsSort.TIMESTAMP_DESCENDING,
    )

    response = LogsApi(api_client).list_logs(body=body)
    return [_extract_log_data(item) for item in (_safe_get(response, "data", []) or [])]


def fetch_recent_agent_spans(hours_back: int = 24, limit: int = 50) -> list[dict[str, Any]]:
    safe_hours = max(1, min(int(hours_back), 168))
    safe_limit = max(1, min(int(limit), 100))
    # Primary: LLM Observability export API (matches data visible in LLMObs UI).
    # Fallback: generic spans API.
    try:
        return _list_llmobs_spans(query="", hours_back=safe_hours, limit=safe_limit, span_kind="agent")
    except Exception:
        query = f'@span.kind:agent service:{_dd_service()}'
        return _list_spans(query=query, hours_back=safe_hours, limit=safe_limit)


def fetch_tool_spans(session_id: str, hours_back: int = 48, limit: int = 100) -> list[dict[str, Any]]:
    safe_hours = max(1, min(int(hours_back), 720))
    safe_limit = max(1, min(int(limit), 100))
    q = f'@span.kind:tool service:{_dd_service()}'
    if session_id.strip():
        q += f" @meta.tags.session_id:{session_id.strip()}"
    return _list_spans(query=q, hours_back=safe_hours, limit=safe_limit)


def search_spans_by_keyword(keyword: str, hours_back: int = 168, limit: int = 20) -> list[dict[str, Any]]:
    text = (keyword or "").strip()
    if not text:
        return []
    safe_hours = max(1, min(int(hours_back), 720))
    safe_limit = max(1, min(int(limit), 100))
    # Prefer LLMObs export query syntax first.
    try:
        return _list_llmobs_spans(query=f'"{text}"', hours_back=safe_hours, limit=safe_limit, span_kind=None)
    except Exception:
        query = f'service:{_dd_service()} "{text}"'
        return _list_spans(query=query, hours_back=safe_hours, limit=safe_limit)
