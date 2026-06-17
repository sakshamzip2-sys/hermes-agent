"""``web_extract_structured`` — extract a web page into a caller-specified JSON
schema (or clean markdown), at scale.

``web_extract`` already returns markdown. This LAZY tool adds the missing
capability: hand it a URL (or a few) plus a JSON Schema and it returns structured
data matching that schema, by fetching the page (reusing the ``web_extract``
backend) and running one auxiliary-LLM extraction pass. Without a schema it just
returns the cleaned markdown for each URL.

Model-agnostic: the extraction model is resolved from the user's auxiliary
config via ``_resolve_web_extract_auxiliary`` (the same seam web_extract uses),
never a hardcoded vendor.

LAZY, not core: it is surfaced via tool_search, not loaded on every call.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from tools.registry import registry, tool_error, tool_result

logger = logging.getLogger(__name__)

_MAX_URLS = 5
_MAX_CONTENT_CHARS = 40_000  # cap content fed to the extractor


async def _extract_one(url: str, content: str, schema: Dict[str, Any],
                       model: Optional[str]) -> Dict[str, Any]:
    """Run a single schema-extraction LLM pass over fetched content."""
    from tools.web_tools import (
        _resolve_web_extract_auxiliary,
        async_call_llm,
        extract_content_or_reasoning,
    )

    aux_client, effective_model, extra_body = _resolve_web_extract_auxiliary(model)
    if aux_client is None or not effective_model:
        return {"url": url, "error": "no auxiliary model available for extraction"}

    snippet = content[:_MAX_CONTENT_CHARS]
    system_prompt = (
        "You extract structured data from web content. Return ONLY a single JSON "
        "value that strictly conforms to the provided JSON Schema — no prose, no "
        "markdown fences. Use null for fields you cannot find; never invent data."
    )
    user_prompt = (
        f"JSON Schema:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
        f"Source URL: {url}\n\nCONTENT:\n{snippet}\n\n"
        "Return the JSON value matching the schema now."
    )
    call_kwargs: Dict[str, Any] = {
        "task": "web_extract",
        "model": effective_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 4000,
    }
    if extra_body:
        call_kwargs["extra_body"] = extra_body

    try:
        response = await async_call_llm(**call_kwargs)
        raw = extract_content_or_reasoning(response) or ""
    except Exception as exc:  # noqa: BLE001
        return {"url": url, "error": f"extraction LLM call failed: {exc}"}

    data, parsed = _parse_json_payload(raw)
    if data is None or not parsed:
        return {"url": url, "error": "extractor did not return valid JSON",
                "raw": raw[:500]}
    # Actually validate against the caller's schema — ``schema_valid`` must mean
    # "conforms to the schema", not merely "parsed as JSON" (a downstream
    # consumer trusting it should be able to). Web-extracted data is untrusted.
    schema_valid = _validate_schema(data, schema)
    return {"url": url, "data": data, "schema_valid": schema_valid}


_KNOWN_SCHEMA_TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def _validate_schema(value: Any, schema: Any) -> bool:
    """Minimal, dependency-free JSON-Schema check (type/required/properties/items).

    Fails CLOSED on an unknown/typo'd ``type`` so attacker-controlled extracted
    data can't be stamped ``schema_valid: true`` by slipping past validation.
    """
    if not isinstance(schema, dict):
        return False
    t = schema.get("type")
    if t == "null":
        return value is None
    if t is not None and t not in _KNOWN_SCHEMA_TYPES:
        return False
    if t == "object":
        if not isinstance(value, dict):
            return False
        for req in schema.get("required", []) or []:
            if req not in value:
                return False
        for k, sub in (schema.get("properties") or {}).items():
            if k in value and not _validate_schema(value[k], sub):
                return False
        return True
    if t == "array":
        if not isinstance(value, list):
            return False
        items = schema.get("items")
        return all(_validate_schema(v, items) for v in value) if items else True
    if t == "string":
        return isinstance(value, str)
    if t in ("number", "integer"):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    return True


def _parse_json_payload(raw: str):
    """Parse a JSON value from an LLM response, tolerating ```json fences."""
    text = raw.strip()
    if text.startswith("```"):
        # strip a leading ```json / ``` fence and the trailing ```
        text = text.split("\n", 1)[1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    try:
        return json.loads(text), True
    except Exception:
        # Best-effort: find the first {...} or [...] span.
        for opener, closer in (("{", "}"), ("[", "]")):
            i, j = text.find(opener), text.rfind(closer)
            if 0 <= i < j:
                try:
                    return json.loads(text[i:j + 1]), True
                except Exception:
                    continue
        return None, False


async def web_extract_structured_tool(args: dict, **_kw) -> str:
    urls_in = args.get("urls") or ([args["url"]] if args.get("url") else [])
    if not isinstance(urls_in, list) or not urls_in:
        return tool_error("Provide 'url' (string) or 'urls' (list).")
    urls: List[str] = [str(u) for u in urls_in][:_MAX_URLS]
    schema = args.get("schema")
    model = args.get("model")

    # Fetch raw markdown via the existing web_extract backend (no summarization
    # — we want the content for extraction).
    from tools.web_tools import web_extract_tool
    raw_json = await web_extract_tool(urls, format="markdown", use_llm_processing=False)
    try:
        fetched = json.loads(raw_json)
    except Exception:
        return tool_error("web_extract backend returned unparseable result", raw=raw_json[:500])

    # Normalize fetched results into {url: content}.
    contents: Dict[str, str] = {}
    results = fetched.get("results") if isinstance(fetched, dict) else None
    if isinstance(results, list):
        for r in results:
            if isinstance(r, dict) and r.get("url"):
                contents[r["url"]] = str(r.get("content") or "")
    elif isinstance(fetched, dict) and fetched.get("content"):
        contents[urls[0]] = str(fetched["content"])

    if not contents:
        return tool_error("No content fetched for the given URL(s).",
                          detail=fetched if isinstance(fetched, dict) else None)

    # No schema → return cleaned markdown per URL.
    if not schema:
        return tool_result({
            "format": "markdown",
            "results": [{"url": u, "markdown": c} for u, c in contents.items()],
        })

    if not isinstance(schema, dict):
        return tool_error("'schema' must be a JSON Schema object.")

    out: List[Dict[str, Any]] = []
    for u in urls:
        if u in contents:
            out.append(await _extract_one(u, contents[u], schema, model))
    return tool_result({"format": "structured", "schema": schema, "results": out})


def check_web_extract_structured() -> bool:
    """Available when the web-extract backend + an auxiliary model are usable."""
    try:
        from tools.web_tools import check_web_api_key
        return bool(check_web_api_key())
    except Exception:
        return False


WEB_EXTRACT_STRUCTURED_SCHEMA = {
    "name": "web_extract_structured",
    "description": (
        "Extract one or more web pages into a caller-specified JSON Schema, or "
        "clean markdown if no schema is given. Use when you need STRUCTURED data "
        "off a page (prices, specs, a table, contact info, a list of items) "
        "rather than prose — provide a JSON Schema and get back validated JSON "
        "per URL. For a plain readable copy, omit schema. Distinct from "
        "web_search (find pages) and web_extract (markdown only)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "A single URL to extract."},
            "urls": {"type": "array", "items": {"type": "string"},
                     "description": f"Up to {_MAX_URLS} URLs (alternative to 'url')."},
            "schema": {"type": "object",
                       "description": "JSON Schema the result must match. Omit for markdown."},
            "model": {"type": "string",
                      "description": "Optional auxiliary model override for extraction."},
        },
        "required": [],
    },
    "input_examples": [
        {"url": "https://example.com/product/123",
         "schema": {"type": "object", "properties": {
             "name": {"type": "string"}, "price": {"type": "number"},
             "in_stock": {"type": "boolean"}}}},
        {"url": "https://example.com/article"},
    ],
}


registry.register(
    name="web_extract_structured",
    toolset="web_research",  # non-core toolset → LAZY (deferred behind tool_search)
    schema=WEB_EXTRACT_STRUCTURED_SCHEMA,
    handler=web_extract_structured_tool,
    check_fn=check_web_extract_structured,
    is_async=True,
    emoji="🗂️",
    max_result_size_chars=100_000,
)
