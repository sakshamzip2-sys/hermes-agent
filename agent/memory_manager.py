"""MemoryManager — orchestrates memory providers for the agent.

Single integration point in run_agent.py. Replaces scattered per-backend
code with one manager that delegates to registered providers.

Only ONE external plugin provider is allowed at a time — attempting to
register a second external provider is rejected with a warning.  This
prevents tool schema bloat and conflicting memory backends.

Usage in run_agent.py:
    self._memory_manager = MemoryManager()
    # Only ONE of these:
    self._memory_manager.add_provider(plugin_provider)

    # System prompt
    prompt_parts.append(self._memory_manager.build_system_prompt())

    # Pre-turn
    context = self._memory_manager.prefetch_all(user_message)

    # Post-turn
    self._memory_manager.sync_all(user_msg, assistant_response)
    self._memory_manager.queue_prefetch_all(user_msg)
"""

from __future__ import annotations

import logging
import re
import inspect
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from agent.memory_provider import MemoryProvider
from agent.skill_commands import extract_user_instruction_from_skill_message
from tools.registry import tool_error

logger = logging.getLogger(__name__)

# How long shutdown_all() waits for in-flight background sync/prefetch work
# to drain before abandoning it. A wedged provider must never block process
# teardown indefinitely — the worker threads are daemon, so anything still
# running past this window dies with the interpreter.
_SYNC_DRAIN_TIMEOUT_S = 5.0


def memory_provider_tools_enabled(enabled_toolsets: Optional[List[str]]) -> bool:
    """Return whether external memory-provider tools should be exposed."""
    if enabled_toolsets is None:
        return True
    if not enabled_toolsets:
        return False
    if "memory" in enabled_toolsets:
        return True

    try:
        from toolsets import resolve_toolset

        return any("memory" in resolve_toolset(name) for name in enabled_toolsets)
    except Exception:
        logger.debug("Failed to resolve enabled toolsets for memory-provider tools", exc_info=True)
        return False


def inject_memory_provider_tools(agent: Any) -> int:
    """Append external memory-provider tool schemas to an agent tool surface."""
    memory_manager = getattr(agent, "_memory_manager", None)
    tools = getattr(agent, "tools", None)
    if not memory_manager or tools is None:
        return 0

    existing_tool_names = {
        tool.get("function", {}).get("name")
        for tool in tools
        if isinstance(tool, dict)
    }
    if (
        "memory" not in existing_tool_names
        and not memory_provider_tools_enabled(getattr(agent, "enabled_toolsets", None))
    ):
        return 0

    get_schemas = getattr(memory_manager, "get_all_tool_schemas", None)
    if not callable(get_schemas):
        return 0

    valid_tool_names = getattr(agent, "valid_tool_names", None)
    if valid_tool_names is None:
        valid_tool_names = set()
        agent.valid_tool_names = valid_tool_names

    added = 0
    for schema in get_schemas():
        if not isinstance(schema, dict):
            continue
        tool_name = schema.get("name", "")
        if not tool_name or tool_name in existing_tool_names:
            continue
        tools.append({"type": "function", "function": schema})
        valid_tool_names.add(tool_name)
        existing_tool_names.add(tool_name)
        added += 1

    return added


# ---------------------------------------------------------------------------
# Context fencing helpers
# ---------------------------------------------------------------------------

_FENCE_TAG_RE = re.compile(r'</?\s*memory-context\s*>', re.IGNORECASE)
_INTERNAL_CONTEXT_RE = re.compile(
    r'<\s*memory-context\s*>[\s\S]*?</\s*memory-context\s*>',
    re.IGNORECASE,
)
_INTERNAL_NOTE_RE = re.compile(
    # Matches the leading system/memory note this module wraps recalled memory
    # in, so a provider that re-injects a pre-wrapped block (or an old cached
    # one) gets the note stripped even when the fence tags are gone. Recognises
    # BOTH the legacy "[System note: ... Treat as ...]" wording AND the current
    # "[These are notes you (the assistant) saved ...]" first-person framing.
    r'\[(?:System note:\s*The following is recalled memory context,\s*NOT new user input\.\s*'
    r'Treat as (?:informational background data|authoritative reference data[^\]]*)\.'
    r'|These are notes you \(the assistant\) saved[^\]]*)\]\s*',
    re.IGNORECASE,
)


def sanitize_context(text: str) -> str:
    """Strip fence tags, injected context blocks, and system notes from provider output."""
    text = _INTERNAL_CONTEXT_RE.sub('', text)
    text = _INTERNAL_NOTE_RE.sub('', text)
    text = _FENCE_TAG_RE.sub('', text)
    return text


class StreamingContextScrubber:
    """Stateful scrubber for streaming text that may contain split memory-context spans.

    The one-shot ``sanitize_context`` regex cannot survive chunk boundaries:
    a ``<memory-context>`` opened in one delta and closed in a later delta
    leaks its payload to the UI because the non-greedy block regex needs
    both tags in one string.  This scrubber runs a small state machine
    across deltas, holding back partial-tag tails and discarding
    everything inside a span (including the system-note line).

    Usage::

        scrubber = StreamingContextScrubber()
        for delta in stream:
            visible = scrubber.feed(delta)
            if visible:
                emit(visible)
        trailing = scrubber.flush()  # at end of stream
        if trailing:
            emit(trailing)

    The scrubber is re-entrant per agent instance.  Callers building new
    top-level responses (new turn) should create a fresh scrubber or call
    ``reset()``.
    """

    _OPEN_TAG = "<memory-context>"
    _CLOSE_TAG = "</memory-context>"

    def __init__(self) -> None:
        self._in_span: bool = False
        self._buf: str = ""
        self._at_block_boundary: bool = True

    def reset(self) -> None:
        self._in_span = False
        self._buf = ""
        self._at_block_boundary = True

    def feed(self, text: str) -> str:
        """Return the visible portion of ``text`` after scrubbing.

        Any trailing fragment that could be the start of an open/close tag
        is held back in the internal buffer and surfaced on the next
        ``feed()`` call or discarded/emitted by ``flush()``.
        """
        if not text:
            return ""
        buf = self._buf + text
        self._buf = ""
        out: list[str] = []

        while buf:
            if self._in_span:
                idx = buf.lower().find(self._CLOSE_TAG)
                if idx == -1:
                    # Hold back a potential partial close tag; drop the rest
                    held = self._max_partial_suffix(buf, self._CLOSE_TAG)
                    self._buf = buf[-held:] if held else ""
                    return "".join(out)
                # Found close — skip span content + tag, continue
                buf = buf[idx + len(self._CLOSE_TAG):]
                self._in_span = False
            else:
                idx = self._find_boundary_open_tag(buf)
                if idx == -1:
                    # No open tag — hold back a potential partial open tag
                    held = (
                        self._max_pending_open_suffix(buf)
                        or self._max_partial_suffix(buf, self._OPEN_TAG)
                    )
                    if held:
                        self._append_visible(out, buf[:-held])
                        self._buf = buf[-held:]
                    else:
                        self._append_visible(out, buf)
                    return "".join(out)
                # Emit text before the tag, enter span
                if idx > 0:
                    self._append_visible(out, buf[:idx])
                buf = buf[idx + len(self._OPEN_TAG):]
                self._in_span = True

        return "".join(out)

    def flush(self) -> str:
        """Emit any held-back buffer at end-of-stream.

        If we're still inside an unterminated span the remaining content is
        discarded (safer: leaking partial memory context is worse than a
        truncated answer).  Otherwise the held-back partial-tag tail is
        emitted verbatim (it turned out not to be a real tag).
        """
        if self._in_span:
            self._buf = ""
            self._in_span = False
            return ""
        tail = self._buf
        self._buf = ""
        return tail

    @staticmethod
    def _max_partial_suffix(buf: str, tag: str) -> int:
        """Return the length of the longest buf-suffix that is a tag-prefix.

        Case-insensitive.  Returns 0 if no suffix could start the tag.
        """
        tag_lower = tag.lower()
        buf_lower = buf.lower()
        max_check = min(len(buf_lower), len(tag_lower) - 1)
        for i in range(max_check, 0, -1):
            if tag_lower.startswith(buf_lower[-i:]):
                return i
        return 0

    def _find_boundary_open_tag(self, buf: str) -> int:
        """Find an opening fence only when it starts a block-like span."""
        buf_lower = buf.lower()
        search_start = 0
        while True:
            idx = buf_lower.find(self._OPEN_TAG, search_start)
            if idx == -1:
                return -1
            if self._is_block_boundary(buf, idx) and self._has_block_opener_suffix(buf, idx):
                return idx
            search_start = idx + 1

    def _max_pending_open_suffix(self, buf: str) -> int:
        """Hold a complete boundary tag until the following char confirms it."""
        if not buf.lower().endswith(self._OPEN_TAG):
            return 0
        idx = len(buf) - len(self._OPEN_TAG)
        if not self._is_block_boundary(buf, idx):
            return 0
        return len(self._OPEN_TAG)

    def _has_block_opener_suffix(self, buf: str, idx: int) -> bool:
        after_idx = idx + len(self._OPEN_TAG)
        if after_idx >= len(buf):
            return False
        return buf[after_idx] in "\r\n"

    def _is_block_boundary(self, buf: str, idx: int) -> bool:
        if idx == 0:
            return self._at_block_boundary
        preceding = buf[:idx]
        last_newline = preceding.rfind("\n")
        if last_newline == -1:
            return self._at_block_boundary and preceding.strip() == ""
        return preceding[last_newline + 1:].strip() == ""

    def _append_visible(self, out: list[str], text: str) -> None:
        if not text:
            return
        out.append(text)
        self._update_block_boundary(text)

    def _update_block_boundary(self, text: str) -> None:
        last_newline = text.rfind("\n")
        if last_newline != -1:
            self._at_block_boundary = text[last_newline + 1:].strip() == ""
        else:
            self._at_block_boundary = self._at_block_boundary and text.strip() == ""


def build_memory_context_block(raw_context: str) -> str:
    """Wrap prefetched memory in a fenced block with system note."""
    if not raw_context or not raw_context.strip():
        return ""
    clean = sanitize_context(raw_context)
    if clean != raw_context:
        logger.warning("memory provider returned pre-wrapped context; stripped")
    # Threat-scan provider output before injecting it as "authoritative
    # reference data". Provider memory (Honcho dialectic output, GBrain pages)
    # is untrusted: a poisoned past session could embed prompt-injection text
    # that this block would otherwise hand the model as trusted memory
    # (Hermes #3943). sanitize_context() only strips re-injected fence tags —
    # it is NOT a payload scan. On a hit, withhold the body entirely.
    try:
        from tools.threat_patterns import scan_for_threats
        threats = scan_for_threats(clean, scope="strict")
    except Exception:  # pragma: no cover - scanner must never break recall
        threats = []
    if threats:
        logger.warning(
            "memory context contained threat patterns %s; withheld from prompt",
            sorted(set(threats)),
        )
        clean = (
            "[BLOCKED: recalled memory context matched injection/threat "
            f"patterns ({', '.join(sorted(set(threats)))}) and was withheld "
            "from the prompt.]"
        )
    return (
        "<memory-context>\n"
        "[These are notes you (the assistant) saved to your own persistent "
        "long-term memory during earlier sessions with this user. They were "
        "retrieved by the memory system for this turn, not supplied by the "
        "user. Use them as your own recollection when they are relevant.]\n\n"
        f"{clean}\n"
        "</memory-context>"
    )


class _FixedCandidateAdapter:
    """A MergeLayer StoreAdapter that returns a pre-built candidate list.

    Used to fold the registered providers' prefetch output into the fusion as a
    single extra plane: their candidates are gathered eagerly (outside the
    adapter) and replayed here, so the MergeLayer treats them as just-another
    plane (per-plane scan + RRF + source-tier prior) without the providers
    needing to implement the adapter protocol themselves.
    """

    def __init__(self, name: str, candidates: List[Any]) -> None:
        self.name = name
        self._candidates = list(candidates)

    def search(self, query: str, *, limit: int) -> List[Any]:
        return list(self._candidates[:limit])


class MemoryManager:
    """Orchestrates the built-in provider plus at most one external provider.

    The builtin provider is always first. Only one non-builtin (external)
    provider is allowed.  Failures in one provider never block the other.
    """

    def __init__(self) -> None:
        self._providers: List[MemoryProvider] = []
        self._tool_to_provider: Dict[str, MemoryProvider] = {}
        self._has_external: bool = False  # True once a non-builtin provider is added
        # Background executor for end-of-turn sync/prefetch. Lazily created on
        # first use so the common builtin-only path spawns no extra threads.
        # A single worker serializes a provider's writes (turn N must land
        # before turn N+1) and caps thread growth at one per manager. See
        # _submit_background() and the sync_all/queue_prefetch_all rationale.
        self._sync_executor: Optional[ThreadPoolExecutor] = None
        self._sync_executor_lock = threading.Lock()
        # -- Combine-on-read MergeLayer wiring (GAP-1 / GAP-4, additive + gated) -
        # These handles are attached by agent_init ONLY when memory.merge.enabled
        # (or memory.holographic_plane.enabled) is set, so the default install
        # has them all None and prefetch_all runs the EXACT legacy concat path.
        # The holographic store is the SEPARATE local fact plane (memory_store.db)
        # read by the MergeLayer and written out-of-band by reconcile; it is NOT
        # the registered provider (Honcho stays the sole registered provider).
        self._holographic_store: Optional[Any] = None
        self._session_db: Optional[Any] = None
        # Resolved + validated memory.merge config block (rrf_k, plane_weights,
        # source_tier_prior, abstention_floor, per_source_floors, enabled).
        # Empty dict => merge disabled (legacy concat path).
        self._merge_config: Dict[str, Any] = {}
        # Most recent MergeLayer RecallTrace (req #4 observability). None until
        # the merge path runs at least once. Read-only side channel; never on
        # the prompt.
        self._last_recall_trace: Optional[Dict[str, Any]] = None

    # -- Combine-on-read wiring (attached by agent_init when gated on) -------

    def attach_merge_planes(
        self,
        *,
        holographic_store: Optional[Any] = None,
        session_db: Optional[Any] = None,
        merge_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Attach the local fact plane + session DB + merge config for recall.

        Called from agent_init ONLY when the merge layer (or the holographic
        plane) is gated on. Everything is additive: with no attachment (or
        ``merge.enabled`` false) ``prefetch_all`` runs the legacy concat path
        unchanged. Honcho remains the sole REGISTERED provider; the holographic
        store is read here out-of-band as an extra local plane, never registered.
        """
        if holographic_store is not None:
            self._holographic_store = holographic_store
        if session_db is not None:
            self._session_db = session_db
        if merge_config is not None:
            self._merge_config = dict(merge_config)

    def _merge_enabled(self) -> bool:
        """Whether the combine-on-read MergeLayer should drive recall.

        True ONLY when memory.merge.enabled is set AND at least one local plane
        handle (holographic store or session DB) was attached. Absent either, we
        fall back to the legacy per-provider concat so behaviour is unchanged.
        """
        if not self._merge_config.get("enabled", False):
            return False
        return self._holographic_store is not None or self._session_db is not None

    # -- Registration --------------------------------------------------------

    def add_provider(self, provider: MemoryProvider) -> None:
        """Register a memory provider.

        Built-in provider (name ``"builtin"``) is always accepted.
        Only **one** external (non-builtin) provider is allowed — a second
        attempt is rejected with a warning.
        """
        is_builtin = provider.name == "builtin"

        if not is_builtin:
            if self._has_external:
                existing = next(
                    (p.name for p in self._providers if p.name != "builtin"), "unknown"
                )
                logger.warning(
                    "Rejected memory provider '%s' — external provider '%s' is "
                    "already registered. Only one external memory provider is "
                    "allowed at a time. Configure which one via memory.provider "
                    "in config.yaml.",
                    provider.name, existing,
                )
                return
            self._has_external = True

        self._providers.append(provider)

        # Core tool names are reserved — a memory provider must never register
        # a tool that shadows a built-in (e.g. ``clarify``, ``delegate_task``).
        # Built-ins always win, so such a tool is dropped at agent init and
        # would otherwise linger in ``_tool_to_provider`` and hijack dispatch
        # (#40466). Reject it here, at the door, so it never enters the routing
        # table at all — matching the built-ins-always-win invariant used by
        # the TTS/browser/search provider registries.
        from toolsets import _HERMES_CORE_TOOLS

        _core_tool_names = set(_HERMES_CORE_TOOLS)

        # Index tool names → provider for routing
        for schema in provider.get_tool_schemas():
            tool_name = schema.get("name", "")
            if tool_name in _core_tool_names:
                logger.warning(
                    "Memory provider '%s' tool '%s' shadows a reserved core "
                    "tool name; registration ignored. Core tools always win — "
                    "rename the provider's tool to something unique.",
                    provider.name, tool_name,
                )
                continue
            if tool_name and tool_name not in self._tool_to_provider:
                self._tool_to_provider[tool_name] = provider
            elif tool_name in self._tool_to_provider:
                logger.warning(
                    "Memory tool name conflict: '%s' already registered by %s, "
                    "ignoring from %s",
                    tool_name,
                    self._tool_to_provider[tool_name].name,
                    provider.name,
                )

        logger.info(
            "Memory provider '%s' registered (%d tools)",
            provider.name,
            len(provider.get_tool_schemas()),
        )

    @property
    def providers(self) -> List[MemoryProvider]:
        """All registered providers in order."""
        return list(self._providers)

    def get_provider(self, name: str) -> Optional[MemoryProvider]:
        """Get a provider by name, or None if not registered."""
        for p in self._providers:
            if p.name == name:
                return p
        return None

    # -- System prompt -------------------------------------------------------

    def build_system_prompt(self) -> str:
        """Collect system prompt blocks from all providers.

        Returns combined text, or empty string if no providers contribute.
        Each non-empty block is labeled with the provider name.
        """
        blocks = []
        for provider in self._providers:
            try:
                block = provider.system_prompt_block()
                if block and block.strip():
                    blocks.append(block)
            except Exception as e:
                logger.warning(
                    "Memory provider '%s' system_prompt_block() failed: %s",
                    provider.name, e,
                )
        return "\n\n".join(blocks)

    # -- Prefetch / recall ---------------------------------------------------

    @staticmethod
    def _strip_skill_scaffolding(text: str) -> Optional[str]:
        """Return memory-worthy user text, or None to skip the turn.

        When a user invokes a /skill or /bundle, Hermes expands the turn into
        a model-facing message that embeds the entire skill body. Feeding that
        verbatim to memory providers pollutes their stores/embeddings with
        prompt scaffolding instead of what the user actually asked. We recover
        just the user's instruction here, once, for every provider — so this
        is fixed for the whole provider fan-out, not per backend.

        - Non-skill messages pass through unchanged.
        - Skill turns with a user instruction return that instruction.
        - Bare skill invocations (no instruction) return None → callers skip
          the turn, since there is no user content worth remembering.
        """
        return extract_user_instruction_from_skill_message(text)

    def prefetch_all(self, query: str, *, session_id: str = "") -> str:
        """Collect prefetch context for the turn.

        When the combine-on-read MergeLayer is gated ON (memory.merge.enabled
        AND a local plane attached) the query is routed through
        :meth:`_merge_recall`, which fuses the local planes (session FTS5 +
        holographic facts) plus the registered providers and returns a single
        ranked, per-plane-fenced block. When OFF (the default), it runs the
        EXACT legacy path: per-provider concat, empty providers skipped,
        failures in one provider never block others.

        Either way the returned text is RAW (not pre-fenced): the caller wraps
        it through ``build_memory_context_block`` (the final whole-block fence)
        at the injection site, so the fence still runs in both modes.
        """
        clean_query = self._strip_skill_scaffolding(query)
        if not clean_query:
            return ""

        if self._merge_enabled():
            try:
                merged = self._merge_recall(clean_query, session_id=session_id)
                if merged is not None:
                    return merged
                # merged is None => the merge path could not run (e.g. import
                # failure); fall through to the legacy concat so recall never
                # silently goes dark.
            except Exception as e:  # pragma: no cover - merge must never break recall
                logger.warning(
                    "MergeLayer recall failed (%s); falling back to concat path",
                    e,
                )

        parts = []
        for provider in self._providers:
            try:
                result = provider.prefetch(clean_query, session_id=session_id)
                if result and result.strip():
                    parts.append(result)
            except Exception as e:
                logger.debug(
                    "Memory provider '%s' prefetch failed (non-fatal): %s",
                    provider.name, e,
                )
        return "\n\n".join(parts)

    # -- Combine-on-read recall (gated by memory.merge.enabled) --------------

    def _build_merge_adapters(self) -> List[Any]:
        """Construct the MergeLayer adapters over the attached local planes.

        Returns the list of StoreAdapters: a SessionFTS5Adapter over the
        attached session DB (when present) and a HolographicAdapter over the
        attached holographic store (when present). Empty when nothing is
        attached. Import-time failures propagate to the caller, which falls back
        to the concat path.
        """
        from agent.memory_merge import HolographicAdapter, SessionFTS5Adapter

        adapters: List[Any] = []
        if self._session_db is not None:
            adapters.append(SessionFTS5Adapter(self._session_db))
        if self._holographic_store is not None:
            adapters.append(HolographicAdapter(self._holographic_store))
        return adapters

    def _provider_prefetch_candidates(
        self, query: str, *, session_id: str
    ) -> List[Any]:
        """Wrap each registered provider's prefetch output as merge Candidates.

        The registered providers (Honcho, etc.) stay first-class planes in the
        fusion: each provider's prefetch string is captured as a single
        Candidate so its content is fused, source-tier-weighted, and per-plane
        fenced alongside the local planes. A provider that errors or returns
        nothing simply contributes no candidate (graceful degradation).
        """
        from agent.memory_merge import Candidate

        out: List[Any] = []
        for provider in self._providers:
            name = getattr(provider, "name", "provider")
            try:
                result = provider.prefetch(query, session_id=session_id)
            except Exception as e:
                logger.debug(
                    "Memory provider '%s' prefetch failed during merge (non-fatal): %s",
                    name, e,
                )
                continue
            if result and result.strip():
                out.append(
                    Candidate(
                        id=name,
                        text_for_rerank=result,
                        source_store=name,
                        native_rank=1,
                        native_score=None,
                        metadata={"source_tier": "curated"},
                    )
                )
        return out

    def _merge_recall(self, query: str, *, session_id: str) -> Optional[str]:
        """Run the MergeLayer over the local planes + providers; render to text.

        Returns the rendered raw context block (possibly empty string when the
        merge abstains or finds nothing) on success, or ``None`` when the merge
        path could not be constructed at all (caller then falls back to concat).
        The returned text is RAW: the caller applies the whole-block fence.
        """
        try:
            from agent.memory_merge import MergeLayer
        except Exception:  # pragma: no cover - merge module must import in-tree
            return None

        adapters = self._build_merge_adapters()
        if not adapters and not self._providers:
            return None

        cfg = self._merge_config
        kwargs: Dict[str, Any] = {}
        if "rrf_k" in cfg:
            try:
                kwargs["rrf_k"] = int(cfg["rrf_k"])
            except (TypeError, ValueError):
                pass
        if isinstance(cfg.get("plane_weights"), dict):
            kwargs["plane_weights"] = dict(cfg["plane_weights"])
        if isinstance(cfg.get("source_tier_prior"), dict):
            kwargs["source_tier_prior"] = dict(cfg["source_tier_prior"])
        if "abstention_floor" in cfg:
            try:
                kwargs["abstention_floor"] = float(cfg["abstention_floor"])
            except (TypeError, ValueError):
                pass
        if "per_source_floors" in cfg:
            kwargs["per_source_floors"] = bool(cfg["per_source_floors"])
        if "stale_multiplier" in cfg:
            try:
                kwargs["stale_multiplier"] = float(cfg["stale_multiplier"])
            except (TypeError, ValueError):
                pass

        merge_layer = MergeLayer(**kwargs)

        # Pre-fetch the registered providers as extra planes, then fuse them
        # with the local-plane adapters via a single combined adapter list. The
        # provider candidates are passed in through a lightweight fixed adapter
        # so the MergeLayer treats them as just-another-plane.
        provider_cands = self._provider_prefetch_candidates(
            query, session_id=session_id
        )
        stores = list(adapters)
        if provider_cands:
            stores.append(_FixedCandidateAdapter("providers", provider_cands))

        ranked, trace = merge_layer.recall(query, stores=stores)
        # Expose the most recent trace for the supervisor / observability
        # surface (req #4). Cheap, read-only, never on the prompt path.
        self._last_recall_trace = trace
        return self._render_merged(ranked)

    @staticmethod
    def _render_merged(ranked: List[Any]) -> str:
        """Render fused candidates to a labeled raw context block.

        Mirrors the existing provider prefetch render style ("## <Plane>\n- ...")
        so the downstream fence + prompt assembly see the same shape they do for
        the legacy concat path. Empty input renders empty (abstention => no
        block injected). The returned text is RAW (not fenced).
        """
        if not ranked:
            return ""
        by_plane: Dict[str, List[str]] = {}
        order: List[str] = []
        for cand in ranked:
            plane = getattr(cand, "source_store", "memory")
            text = (getattr(cand, "text_for_rerank", "") or "").strip()
            if not text:
                continue
            if plane not in by_plane:
                by_plane[plane] = []
                order.append(plane)
            by_plane[plane].append(text)
        if not order:
            return ""
        sections: List[str] = []
        for plane in order:
            heading = "## Memory: " + plane
            lines = "\n".join(f"- {t}" for t in by_plane[plane])
            sections.append(heading + "\n" + lines)
        return "\n\n".join(sections)

    def queue_prefetch_all(self, query: str, *, session_id: str = "") -> None:
        """Queue background prefetch on all providers for the next turn.

        Provider work is dispatched to a background worker so a slow or
        wedged provider can never block the caller. See ``sync_all`` for
        the full rationale (agent stuck "running" minutes after a turn).
        """
        providers = list(self._providers)
        if not providers:
            return

        clean_query = self._strip_skill_scaffolding(query)
        if not clean_query:
            return

        def _run() -> None:
            for provider in providers:
                try:
                    provider.queue_prefetch(clean_query, session_id=session_id)
                except Exception as e:
                    logger.debug(
                        "Memory provider '%s' queue_prefetch failed (non-fatal): %s",
                        provider.name, e,
                    )

        self._submit_background(_run)

    # -- Sync ----------------------------------------------------------------

    @staticmethod
    def _provider_sync_accepts_messages(provider: MemoryProvider) -> bool:
        """Return whether sync_turn accepts a messages keyword."""
        try:
            signature = inspect.signature(provider.sync_turn)
        except (TypeError, ValueError):
            return True
        params = list(signature.parameters.values())
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params):
            return True
        return "messages" in signature.parameters

    def sync_all(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Sync a completed turn to all providers.

        Runs on a background worker thread, NOT inline on the
        turn-completion path. A provider's ``sync_turn`` may make a
        blocking network/daemon call (a misconfigured Hindsight daemon
        was observed blocking ~298s before failing); doing that inline
        held ``run_conversation`` open long after the user saw their
        response, so every interface (CLI, TUI, gateway) kept the agent
        marked "running" for minutes and any follow-up message triggered
        an aggressive interrupt. Dispatching off-thread means a slow or
        broken provider can never stall the turn — the sync simply
        completes (or fails, logged) in the background.

        Writes are serialized through a single worker so turn N lands
        before turn N+1; provider implementations don't need their own
        ordering guarantees.
        """
        providers = list(self._providers)
        if not providers:
            return

        clean_user_content = self._strip_skill_scaffolding(user_content)
        if not clean_user_content:
            return
        user_content = clean_user_content

        def _run() -> None:
            for provider in providers:
                try:
                    if messages is not None and self._provider_sync_accepts_messages(provider):
                        provider.sync_turn(
                            user_content,
                            assistant_content,
                            session_id=session_id,
                            messages=messages,
                        )
                    else:
                        provider.sync_turn(
                            user_content,
                            assistant_content,
                            session_id=session_id,
                        )
                except Exception as e:
                    logger.warning(
                        "Memory provider '%s' sync_turn failed: %s",
                        provider.name, e,
                    )

        self._submit_background(_run)

    # -- Background dispatch -------------------------------------------------

    def _submit_background(self, fn) -> None:
        """Run ``fn`` on the manager's background worker.

        The executor is created lazily and shared across calls. If the
        executor can't be created or has already been shut down, ``fn``
        runs inline as a last-resort fallback — losing the async benefit
        but never losing the write itself. ``fn`` must do its own
        per-provider error handling; this wrapper only guards executor
        plumbing.
        """
        executor = self._get_sync_executor()
        if executor is None:
            # Executor unavailable (shut down / creation failed) — run
            # inline rather than drop the work. Slow, but correct.
            try:
                fn()
            except Exception as e:  # pragma: no cover - fn guards internally
                logger.debug("Inline memory background task failed: %s", e)
            return
        try:
            executor.submit(fn)
        except RuntimeError:
            # Executor was shut down between the get and the submit
            # (teardown race). Fall back to inline.
            try:
                fn()
            except Exception as e:  # pragma: no cover - fn guards internally
                logger.debug("Inline memory background task failed: %s", e)

    def _get_sync_executor(self) -> Optional[ThreadPoolExecutor]:
        """Lazily create the single-worker background executor."""
        if self._sync_executor is not None:
            return self._sync_executor
        with self._sync_executor_lock:
            if self._sync_executor is None:
                try:
                    self._sync_executor = ThreadPoolExecutor(
                        max_workers=1,
                        thread_name_prefix="mem-sync",
                    )
                except Exception as e:  # pragma: no cover - resource exhaustion
                    logger.warning("Failed to create memory sync executor: %s", e)
                    return None
            return self._sync_executor

    def flush_pending(self, timeout: Optional[float] = None) -> bool:
        """Block until queued sync/prefetch work has drained.

        Single-worker executor means submitting a sentinel and waiting on
        it guarantees every previously-submitted task has run. Returns
        True if the barrier completed within ``timeout`` (or no executor
        exists), False on timeout. Used at real session boundaries and by
        tests that need to assert provider state deterministically.
        """
        executor = self._sync_executor
        if executor is None:
            return True
        try:
            fut = executor.submit(lambda: None)
        except RuntimeError:
            # Executor already shut down — nothing pending.
            return True
        try:
            fut.result(timeout=timeout)
            return True
        except Exception:
            return False

    # -- Tools ---------------------------------------------------------------

    def get_all_tool_schemas(self) -> List[Dict[str, Any]]:
        """Collect tool schemas from all providers.

        Reserved core tool names (``clarify``, ``delegate_task``, etc.) are
        skipped — they are rejected from the routing table in
        :meth:`add_provider`, so the manager must not advertise a schema it
        will never route. Built-ins always win (#40466).
        """
        from toolsets import _HERMES_CORE_TOOLS

        _core_tool_names = set(_HERMES_CORE_TOOLS)
        schemas = []
        seen = set()
        for provider in self._providers:
            try:
                for schema in provider.get_tool_schemas():
                    name = schema.get("name", "")
                    if name in _core_tool_names:
                        continue
                    if name and name not in seen:
                        schemas.append(schema)
                        seen.add(name)
            except Exception as e:
                logger.warning(
                    "Memory provider '%s' get_tool_schemas() failed: %s",
                    provider.name, e,
                )
        return schemas

    def get_all_tool_names(self) -> set:
        """Return set of all tool names across all providers."""
        return set(self._tool_to_provider.keys())

    def has_tool(self, tool_name: str) -> bool:
        """Check if any provider handles this tool."""
        return tool_name in self._tool_to_provider

    def handle_tool_call(
        self, tool_name: str, args: Dict[str, Any], **kwargs
    ) -> str:
        """Route a tool call to the correct provider.

        Returns JSON string result. Raises ValueError if no provider
        handles the tool.
        """
        provider = self._tool_to_provider.get(tool_name)
        if provider is None:
            return tool_error(f"No memory provider handles tool '{tool_name}'")
        try:
            return provider.handle_tool_call(tool_name, args, **kwargs)
        except Exception as e:
            logger.error(
                "Memory provider '%s' handle_tool_call(%s) failed: %s",
                provider.name, tool_name, e,
            )
            return tool_error(f"Memory tool '{tool_name}' failed: {e}")

    # -- Lifecycle hooks -----------------------------------------------------

    def on_turn_start(self, turn_number: int, message: str, **kwargs) -> None:
        """Notify all providers of a new turn.

        kwargs may include: remaining_tokens, model, platform, tool_count.
        """
        for provider in self._providers:
            try:
                provider.on_turn_start(turn_number, message, **kwargs)
            except Exception as e:
                logger.debug(
                    "Memory provider '%s' on_turn_start failed: %s",
                    provider.name, e,
                )

    def on_session_end(self, messages: List[Dict[str, Any]]) -> None:
        """Notify all providers of session end."""
        for provider in self._providers:
            try:
                provider.on_session_end(messages)
            except Exception as e:
                logger.debug(
                    "Memory provider '%s' on_session_end failed: %s",
                    provider.name, e,
                )

    def on_session_switch(
        self,
        new_session_id: str,
        *,
        parent_session_id: str = "",
        reset: bool = False,
        rewound: bool = False,
        **kwargs,
    ) -> None:
        """Notify all providers that the agent's session_id has rotated.

        Fires on ``/resume``, ``/branch``, ``/reset``, ``/new``, and
        context compression — any path that reassigns
        ``AIAgent.session_id`` without tearing the provider down.

        Providers keep running; they only need to refresh cached
        per-session state so subsequent writes land in the correct
        session's record. See ``MemoryProvider.on_session_switch`` for
        the full contract.

        ``rewound=True`` signals that session_id is unchanged but the
        transcript was truncated; providers caching per-turn document
        state should invalidate.
        """
        if not new_session_id:
            return
        # Only forward ``rewound`` when it's actually set. Passing it
        # unconditionally would inject ``rewound=False`` into every
        # provider's **kwargs for the common /resume, /branch, /new, and
        # compression paths, polluting providers that capture extra kwargs
        # (and breaking exact-dict assertions). The /undo path sets
        # rewound=True explicitly; everyone else stays clean.
        if rewound:
            kwargs["rewound"] = True
        for provider in self._providers:
            try:
                provider.on_session_switch(
                    new_session_id,
                    parent_session_id=parent_session_id,
                    reset=reset,
                    **kwargs,
                )
            except Exception as e:
                logger.debug(
                    "Memory provider '%s' on_session_switch failed: %s",
                    provider.name, e,
                )

    def on_pre_compress(self, messages: List[Dict[str, Any]]) -> str:
        """Notify all providers before context compression.

        Returns combined text from providers to include in the compression
        summary prompt. Empty string if no provider contributes.
        """
        parts = []
        for provider in self._providers:
            try:
                result = provider.on_pre_compress(messages)
                if result and result.strip():
                    parts.append(result)
            except Exception as e:
                logger.debug(
                    "Memory provider '%s' on_pre_compress failed: %s",
                    provider.name, e,
                )
        return "\n\n".join(parts)

    @staticmethod
    def _provider_memory_write_metadata_mode(provider: MemoryProvider) -> str:
        """Return how to pass metadata to a provider's memory-write hook."""
        try:
            signature = inspect.signature(provider.on_memory_write)
        except (TypeError, ValueError):
            return "keyword"

        params = list(signature.parameters.values())
        if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params):
            return "keyword"
        if "metadata" in signature.parameters:
            return "keyword"

        accepted = [
            p for p in params
            if p.kind in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
        ]
        if len(accepted) >= 4:
            return "positional"
        return "legacy"

    def on_memory_write(
        self,
        action: str,
        target: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Notify external providers when the built-in memory tool writes.

        Skips the builtin provider itself (it's the source of the write).
        """
        for provider in self._providers:
            if provider.name == "builtin":
                continue
            try:
                metadata_mode = self._provider_memory_write_metadata_mode(provider)
                if metadata_mode == "keyword":
                    provider.on_memory_write(
                        action, target, content, metadata=dict(metadata or {})
                    )
                elif metadata_mode == "positional":
                    provider.on_memory_write(action, target, content, dict(metadata or {}))
                else:
                    provider.on_memory_write(action, target, content)
            except Exception as e:
                logger.debug(
                    "Memory provider '%s' on_memory_write failed: %s",
                    provider.name, e,
                )

    def on_delegation(self, task: str, result: str, *,
                      child_session_id: str = "", **kwargs) -> None:
        """Notify all providers that a subagent completed."""
        for provider in self._providers:
            try:
                provider.on_delegation(
                    task, result, child_session_id=child_session_id, **kwargs
                )
            except Exception as e:
                logger.debug(
                    "Memory provider '%s' on_delegation failed: %s",
                    provider.name, e,
                )

    def shutdown_all(self) -> None:
        """Shut down all providers (reverse order for clean teardown).

        Drains the background sync/prefetch executor first (bounded by
        ``_SYNC_DRAIN_TIMEOUT_S``) so a turn's final sync has a chance to
        land before providers are torn down. The worker threads are
        daemon, so anything still wedged past the drain window dies with
        the interpreter rather than blocking exit.
        """
        self._drain_sync_executor()
        for provider in reversed(self._providers):
            try:
                provider.shutdown()
            except Exception as e:
                logger.warning(
                    "Memory provider '%s' shutdown failed: %s",
                    provider.name, e,
                )

    def _drain_sync_executor(self) -> None:
        """Shut down the background executor, waiting briefly for drain.

        Bounded by ``_SYNC_DRAIN_TIMEOUT_S``: a wedged provider must never
        hang process/session teardown. We stop accepting new work and
        cancel anything still queued, then wait at most the drain timeout
        for the currently-running task on a watcher thread. The worker is
        daemon, so an over-running task dies with the interpreter.
        """
        with self._sync_executor_lock:
            executor = self._sync_executor
            self._sync_executor = None
        if executor is None:
            return
        try:
            # Stop accepting new work and drop anything still queued, but
            # do NOT block here — cancel_futures cancels not-yet-started
            # tasks; the in-flight one keeps running on its daemon thread.
            executor.shutdown(wait=False, cancel_futures=True)
        except TypeError:
            # Older Python without cancel_futures kwarg.
            try:
                executor.shutdown(wait=False)
            except Exception as e:  # pragma: no cover
                logger.debug("Memory sync executor shutdown failed: %s", e)
            return
        except Exception as e:  # pragma: no cover
            logger.debug("Memory sync executor shutdown failed: %s", e)
            return
        # Give an in-flight sync a bounded chance to finish on a watcher
        # thread so we don't block the caller past the drain timeout.
        drainer = threading.Thread(
            target=lambda: self._bounded_executor_wait(executor),
            daemon=True,
            name="mem-sync-drain",
        )
        drainer.start()
        drainer.join(timeout=_SYNC_DRAIN_TIMEOUT_S)

    @staticmethod
    def _bounded_executor_wait(executor: ThreadPoolExecutor) -> None:
        try:
            executor.shutdown(wait=True)
        except Exception as e:  # pragma: no cover
            logger.debug("Memory sync executor drain wait failed: %s", e)

    def initialize_all(self, session_id: str, **kwargs) -> None:
        """Initialize all providers.

        Automatically injects ``hermes_home`` into *kwargs* so that every
        provider can resolve profile-scoped storage paths without importing
        ``get_hermes_home()`` themselves.
        """
        if "hermes_home" not in kwargs:
            from hermes_constants import get_hermes_home
            kwargs["hermes_home"] = str(get_hermes_home())
        for provider in self._providers:
            try:
                provider.initialize(session_id=session_id, **kwargs)
            except Exception as e:
                logger.warning(
                    "Memory provider '%s' initialize failed: %s",
                    provider.name, e,
                )
