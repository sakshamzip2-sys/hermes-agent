"""LSP code-intelligence tool — go-to-definition, find-references, hover, and
document symbols, on top of the existing per-language LSP servers.

OpenComputer already surfaces LSP *diagnostics* automatically after edits
(tools/file_operations.py). This adds the *navigation* half — the thing a
coding agent needs to safely change a `charge()` / `refund()` path: see every
caller before editing it. It's model-agnostic (a plain tool any provider can
call) and an edge over the existing `agent/lsp` core service — no provider
coupling. Gated by a check_fn so it only appears when LSP is actually active.

Positions are 1-based in the tool surface (matching `read_file`'s line numbers
and how humans/editors count); converted to LSP's 0-based internally.
"""

import json
import os
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urlparse

from tools.registry import registry, tool_error

# LSP SymbolKind → human label (subset that matters; falls back to the number).
_SYMBOL_KIND = {
    1: "file", 2: "module", 3: "namespace", 4: "package", 5: "class",
    6: "method", 7: "property", 8: "field", 9: "constructor", 10: "enum",
    11: "interface", 12: "function", 13: "variable", 14: "constant",
    15: "string", 16: "number", 17: "boolean", 18: "array", 19: "object",
    20: "key", 21: "null", 22: "enum-member", 23: "struct", 24: "event",
    25: "operator", 26: "type-parameter",
}

_METHOD = {
    "definition": "textDocument/definition",
    "references": "textDocument/references",
    "hover": "textDocument/hover",
    "symbols": "textDocument/documentSymbol",
}


def _check_lsp() -> bool:
    """Available only when an LSP service is live (servers installed + a git
    workspace). Per-file support is still re-checked in the handler."""
    try:
        from agent.lsp import get_service
        svc = get_service()
        return svc is not None and svc.is_active()
    except Exception:
        return False


def _uri_to_path(uri: str) -> str:
    if not isinstance(uri, str):
        return str(uri)
    if uri.startswith("file://"):
        try:
            return unquote(urlparse(uri).path)
        except Exception:
            return uri
    return uri


def _loc_str(loc: Dict[str, Any]) -> Optional[str]:
    """Format an LSP Location | LocationLink as 'path:line:col' (1-based)."""
    if not isinstance(loc, dict):
        return None
    uri = loc.get("uri") or loc.get("targetUri")
    rng = loc.get("range") or loc.get("targetRange") or loc.get("targetSelectionRange")
    if not uri or not isinstance(rng, dict):
        return None
    start = rng.get("start", {})
    line = int(start.get("line", 0)) + 1
    col = int(start.get("character", 0)) + 1
    path = _uri_to_path(uri)
    try:
        path = os.path.relpath(path)
    except Exception:
        pass
    return f"{path}:{line}:{col}"


def _format_locations(result: Any) -> List[str]:
    if result is None:
        return []
    items = result if isinstance(result, list) else [result]
    out = []
    for it in items:
        s = _loc_str(it)
        if s:
            out.append(s)
    return out


def _format_hover(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    contents = result.get("contents")
    if isinstance(contents, dict):  # MarkupContent {kind, value}
        return str(contents.get("value", "")).strip()
    if isinstance(contents, str):
        return contents.strip()
    if isinstance(contents, list):  # array of strings / {language, value}
        parts = []
        for c in contents:
            if isinstance(c, dict):
                parts.append(str(c.get("value", "")))
            else:
                parts.append(str(c))
        return "\n".join(p for p in parts if p).strip()
    return ""


def _format_symbols(result: Any, depth: int = 0) -> List[str]:
    """Flatten DocumentSymbol[] (hierarchical) or SymbolInformation[]."""
    if not isinstance(result, list):
        return []
    out: List[str] = []
    for sym in result:
        if not isinstance(sym, dict):
            continue
        name = sym.get("name", "?")
        kind_raw = sym.get("kind")
        kind = _SYMBOL_KIND.get(kind_raw, str(kind_raw)) if isinstance(kind_raw, int) else "?"
        # DocumentSymbol has .range; SymbolInformation has .location.range
        rng = sym.get("range") or (sym.get("location") or {}).get("range") or {}
        line = int(rng.get("start", {}).get("line", 0)) + 1
        out.append(f"{'  ' * depth}{name} ({kind}) :{line}")
        children = sym.get("children")
        if isinstance(children, list) and children:
            out.extend(_format_symbols(children, depth + 1))
    return out


LSP_SCHEMA = {
    "name": "lsp",
    "description": (
        "Code intelligence via the language server (go-to-definition, "
        "find-references, hover, document symbols). Use this to navigate code "
        "precisely instead of guessing with text search — e.g. find every "
        "CALLER of a function before you change it, jump to where a symbol is "
        "DEFINED, read a symbol's type/signature (hover), or list all symbols "
        "in a file.\n\n"
        "Positions are 1-based (line/column as shown by read_file). "
        "actions: 'definition' | 'references' | 'hover' need file_path+line+column; "
        "'symbols' needs only file_path."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["definition", "references", "hover", "symbols"],
                "description": "Which query to run.",
            },
            "file_path": {"type": "string", "description": "File to query."},
            "line": {
                "type": "integer",
                "description": "1-based line of the symbol (required except for 'symbols').",
            },
            "column": {
                "type": "integer",
                "description": "1-based column of the symbol (required except for 'symbols').",
            },
            "include_declaration": {
                "type": "boolean",
                "description": "For 'references': include the declaration itself (default false).",
                "default": False,
            },
        },
        "required": ["action", "file_path"],
    },
    "input_examples": [
        {"action": "definition", "file_path": "src/billing/charge.py", "line": 42, "column": 9},
        {"action": "references", "file_path": "src/billing/charge.py", "line": 42, "column": 9, "include_declaration": True},
        {"action": "hover", "file_path": "src/app.py", "line": 10, "column": 15},
        {"action": "symbols", "file_path": "src/app.py"},
    ],
}


def _handle_lsp(args: dict, **_kw) -> str:
    action = args.get("action")
    file_path = args.get("file_path")
    if action not in _METHOD:
        return tool_error(
            f"lsp: unknown action {action!r}. Use one of: {', '.join(_METHOD)}."
        )
    if not file_path or not isinstance(file_path, str):
        return tool_error("lsp: 'file_path' is required.")
    if not os.path.exists(file_path):
        return tool_error(f"lsp: file not found: {file_path}")
    if action != "symbols":
        if args.get("line") is None or args.get("column") is None:
            return tool_error(
                f"lsp: action '{action}' requires 1-based 'line' and 'column'."
            )

    try:
        from agent.lsp import get_service
        svc = get_service()
    except Exception as e:  # noqa: BLE001
        return tool_error(f"lsp: service unavailable ({e}).")
    if svc is None or not svc.enabled_for(file_path):
        return json.dumps({
            "ok": False,
            "reason": "LSP not available for this file (no language server / "
                      "outside a git workspace / unsupported language).",
        })

    # 1-based (tool surface) → 0-based (LSP).
    line0 = max(0, int(args.get("line", 1)) - 1)
    col0 = max(0, int(args.get("column", 1)) - 1)

    result = svc.query_sync(
        file_path,
        _METHOD[action],
        line=line0,
        character=col0,
        include_declaration=bool(args.get("include_declaration", False)),
    )

    if action in ("definition", "references"):
        locs = _format_locations(result)
        return json.dumps({"action": action, "locations": locs, "count": len(locs)})
    if action == "hover":
        return json.dumps({"action": "hover", "hover": _format_hover(result)})
    # symbols
    syms = _format_symbols(result)
    return json.dumps({"action": "symbols", "symbols": syms, "count": len(syms)})


registry.register(
    name="lsp",
    toolset="lsp",
    schema=LSP_SCHEMA,
    handler=_handle_lsp,
    check_fn=_check_lsp,
    emoji="🧭",
    max_result_size_chars=60_000,
)
