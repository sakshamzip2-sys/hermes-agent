"""``http_request`` — structured HTTP for the agent (CORE tool).

The one justified always-on addition from the upgrade plan. Without it the model
reaches HTTP only through ``terminal`` (curl string-building) or ``execute_code``,
both of which invite the classic JSON-escaping / quoting failure mode. This tool
takes structured inputs (method, url, headers, query, typed body, auth) and
returns a structured result (status, headers, parsed JSON, timing, truncation
flag), surfacing errors as readable fields instead of raw stack traces.

Security:
- Egress is routed through ``tools.url_safety`` (SSRF): private/internal IPs and
  cloud-metadata endpoints are blocked pre-flight AND on every redirect hop.
- ``auth`` (bearer/basic) and sensitive headers (authorization/cookie/api-key/
  token) are never written to logs.
- Responses are capped at ``max_response_bytes`` (truncate + flag) so a huge
  body can't blow up the context.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from tools.registry import registry, tool_error, tool_result
from tools.url_safety import is_safe_url, normalize_url_for_request

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_MS = 30_000
_DEFAULT_MAX_BYTES = 5_000_000
_ALLOWED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"}
# Header names whose values must never reach the logs.
_SENSITIVE_HEADER_KEYS = {"authorization", "cookie", "set-cookie", "x-api-key",
                          "api-key", "x-auth-token", "token", "proxy-authorization"}


def _redact_headers(headers: Dict[str, Any]) -> Dict[str, str]:
    """Return a copy of headers safe to log (sensitive values masked)."""
    out: Dict[str, str] = {}
    for k, v in (headers or {}).items():
        out[k] = "<redacted>" if str(k).lower() in _SENSITIVE_HEADER_KEYS else str(v)
    return out


def _build_request_kwargs(args: dict) -> tuple[dict, Optional[str]]:
    """Translate tool args into httpx request kwargs. Returns (kwargs, error)."""
    headers = dict(args.get("headers") or {})

    # Auth — never logged. Mutually exclusive bearer/basic.
    auth = args.get("auth") or {}
    httpx_auth = None
    if isinstance(auth, dict) and auth:
        if "bearer" in auth and auth["bearer"]:
            headers["Authorization"] = f"Bearer {auth['bearer']}"
        elif "basic" in auth and isinstance(auth["basic"], dict):
            b = auth["basic"]
            httpx_auth = (str(b.get("username", "")), str(b.get("password", "")))

    # Body — exactly one of json/text/form, auto Content-Type.
    body_kwargs: Dict[str, Any] = {}
    provided = [k for k in ("json", "text", "form") if args.get(k) is not None]
    if len(provided) > 1:
        return {}, f"Provide at most one body type, got {provided}"
    if "json" in provided:
        body_kwargs["json"] = args["json"]
    elif "form" in provided:
        body_kwargs["data"] = args["form"]
    elif "text" in provided:
        body_kwargs["content"] = str(args["text"]).encode("utf-8")
        headers.setdefault("Content-Type", "text/plain; charset=utf-8")

    kwargs: Dict[str, Any] = {"headers": headers, **body_kwargs}
    if httpx_auth is not None:
        kwargs["auth"] = httpx_auth
    if args.get("query"):
        kwargs["params"] = args["query"]
    return kwargs, None


def http_request(args: dict, **_kw) -> str:
    """Execute a structured HTTP request and return a structured result."""
    import httpx

    method = str(args.get("method", "GET")).strip().upper()
    if method not in _ALLOWED_METHODS:
        return tool_error(f"Unsupported method {method!r}. Use one of: "
                          f"{', '.join(sorted(_ALLOWED_METHODS))}.")

    raw_url = args.get("url")
    if not raw_url or not isinstance(raw_url, str):
        return tool_error("'url' is required and must be a string.")
    url = normalize_url_for_request(raw_url)

    # SSRF pre-flight: block private/internal/metadata targets (fail-closed).
    if not is_safe_url(url):
        return tool_error(
            "Request blocked: the URL resolves to a private/internal or "
            "cloud-metadata address (SSRF protection). Set "
            "security.allow_private_urls: true to allow private hosts.",
            blocked=True,
        )

    timeout_s = max(0.1, float(args.get("timeout_ms", _DEFAULT_TIMEOUT_MS)) / 1000.0)
    follow_redirects = bool(args.get("follow_redirects", True))
    max_bytes = int(args.get("max_response_bytes", _DEFAULT_MAX_BYTES))

    req_kwargs, err = _build_request_kwargs(args)
    if err:
        return tool_error(err)

    def _ssrf_redirect_guard(response):
        """Re-validate each redirect target — a 302 to 169.254.169.254 must not
        slip past the pre-flight check."""
        if response.is_redirect:
            location = response.headers.get("location")
            if location:
                target = str(httpx.URL(response.url).join(location))
                if not is_safe_url(target):
                    raise httpx.HTTPError(
                        f"Redirect blocked by SSRF protection: {target}")

    logger.info("http_request %s %s headers=%s", method, url,
                _redact_headers(req_kwargs.get("headers", {})))

    start = time.monotonic()
    try:
        with httpx.Client(follow_redirects=follow_redirects,
                          timeout=timeout_s,
                          event_hooks={"response": [_ssrf_redirect_guard]}) as client:
            resp = client.request(method, url, **req_kwargs)
            # Cap the body — read raw bytes and truncate before decoding.
            raw = resp.content
            truncated = len(raw) > max_bytes
            body_bytes = raw[:max_bytes] if truncated else raw
            try:
                body_text = body_bytes.decode(resp.encoding or "utf-8", errors="replace")
            except (LookupError, TypeError):
                body_text = body_bytes.decode("utf-8", errors="replace")
    except httpx.TimeoutException:
        return tool_error(f"Request timed out after {timeout_s:.1f}s", kind="timeout")
    except httpx.ConnectError as exc:
        return tool_error(f"Connection failed (DNS/refused): {exc}", kind="connect")
    except httpx.HTTPError as exc:
        # Includes our SSRF-redirect block + TLS errors.
        msg = str(exc)
        kind = "ssrf" if "SSRF" in msg else "tls" if "certificate" in msg.lower() else "http"
        return tool_error(f"HTTP error: {msg}", kind=kind)
    except Exception as exc:  # noqa: BLE001
        return tool_error(f"Request failed: {exc}", kind="error")
    elapsed_ms = round((time.monotonic() - start) * 1000.0, 1)

    body_json = None
    ctype = resp.headers.get("content-type", "")
    if "json" in ctype.lower():
        try:
            import json as _json
            body_json = _json.loads(body_text)
        except Exception:
            body_json = None

    return tool_result({
        "status": resp.status_code,
        "status_text": resp.reason_phrase,
        "ok": 200 <= resp.status_code < 300,
        "headers": dict(resp.headers),
        "body_text": body_text,
        "body_json": body_json,
        "elapsed_ms": elapsed_ms,
        "truncated": truncated,
        "final_url": str(resp.url),
    })


def check_http_request_requirements() -> bool:
    """Always available — httpx ships with the agent's dependencies."""
    return True


HTTP_REQUEST_SCHEMA = {
    "name": "http_request",
    "description": (
        "Make a structured HTTP request and get a structured response. Prefer "
        "this over curl-in-terminal for any API call — it handles JSON encoding, "
        "auth, query strings, and timeouts without shell-escaping pitfalls, and "
        "returns parsed JSON + status + timing. Egress is SSRF-protected "
        "(private/internal/metadata addresses are blocked). Provide at most one "
        "body via json/text/form. Auth values are never logged. Responses are "
        "capped at max_response_bytes (truncated + flagged)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "method": {"type": "string", "enum": sorted(_ALLOWED_METHODS),
                       "description": "HTTP method. Default GET."},
            "url": {"type": "string", "description": "Absolute http(s) URL."},
            "headers": {"type": "object", "description": "Request headers."},
            "query": {"type": "object", "description": "Query params (URL-encoded)."},
            "json": {"description": "JSON body (object/array/value). Sets application/json."},
            "text": {"type": "string", "description": "Raw text body."},
            "form": {"type": "object", "description": "Form-encoded body (x-www-form-urlencoded)."},
            "auth": {"type": "object", "description":
                     "One of {\"bearer\": \"<token>\"} or "
                     "{\"basic\": {\"username\": \"u\", \"password\": \"p\"}}. Never logged."},
            "timeout_ms": {"type": "integer", "description": "Timeout in ms. Default 30000."},
            "follow_redirects": {"type": "boolean", "description": "Default true (each hop SSRF-checked)."},
            "max_response_bytes": {"type": "integer",
                                   "description": "Body cap. Default 5000000 (truncate+flag)."},
        },
        "required": ["url"],
    },
    "input_examples": [
        {"method": "GET", "url": "https://api.example.com/status"},
        {"method": "POST", "url": "https://api.example.com/items",
         "json": {"name": "widget", "qty": 3},
         "auth": {"bearer": "TOKEN"}},
        {"method": "GET", "url": "https://api.example.com/search",
         "query": {"q": "hello world", "limit": 10}},
    ],
}


registry.register(
    name="http_request",
    toolset="http_request",
    schema=HTTP_REQUEST_SCHEMA,
    handler=http_request,
    check_fn=check_http_request_requirements,
    emoji="🌐",
)
