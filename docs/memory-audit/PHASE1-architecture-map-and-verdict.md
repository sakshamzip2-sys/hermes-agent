# Phase 1 — Memory Architecture Map and Working/Broken Verdict

Status: COMPLETE. This file is the gate for design work (Phases 2 and 3 follow).
Method: every verdict below is backed by code read at file:line AND by a command actually
run against the real system. Live command output lives in `evidence/EVIDENCE-LOG.md`
(entries E1 to E6). Nothing here is inferred from the code alone.

Repo: `OpenComputerV2/` (the `hermes-agent` fork, CLI `hermes`/`oc`). Audit date 2026-06-20.

---

## 0. One-paragraph verdict

The orchestrator does NOT hold a combined FTS5 + G-Brain + Honcho memory the way the mission
assumed. v2 memory is **single-provider**: exactly one external memory provider is active for
the agent's recall, and today that is **Honcho**, whose server is **down**, so the agent's
automatic cross-session recall is currently empty (failing open, silently). The pieces
themselves are mostly sound in isolation: the session FTS5 store works, the holographic
SQLite+FTS5+HRR fact store works (but is dormant, not the active provider), the GBrain engine
works (its v2 server is down), and the per-agent "Hermes" isolation (one `state.db` per
agent profile) works and is genuinely isolated. The real architectural gap is that **there is
no retrieval-and-merge layer**: the three stores are bolted on separately, combined only in a
display-only aggregator that the model never sees. That gap is the spine of the Phase 3 design.

---

## 1. Per-component verdict table

| Component | Mission's role | Real role in v2 | Verdict | Evidence |
|-----------|----------------|-----------------|---------|----------|
| **Orchestrator session FTS5** (`hermes_state.py`, `session_search`) | keyword recall over history | Same. SQLite `messages_fts` (unicode61) + `messages_fts_trigram`, trigger-synced, bm25 + snippet. Model-invoked tool, zero cost. | **WORKING** | E1: live bm25 + trigram MATCH on the real 1,875-row `~/.hermes/state.db`; write/index roundtrip |
| **Holographic provider** (`plugins/memory/holographic/`) | "the FTS5 store" | The real fact-memory layer: SQLite + FTS5 + HRR vectors; tools `fact_store`/`fact_feedback`; dedup, trust, soft supersession, hybrid retrieval. No keys, no services. | **WORKING but DORMANT** (not the active provider; no `memory_store.db` exists) | E2: live add/dedup/FTS/HRR-probe/cross-session on temp DB; discovery + tools confirmed |
| **Honcho** (`plugins/memory/honcho/`) | identity/representation layer | The single active external provider. AI-native peer memory by Plastic Labs; writes each turn, recalls via `peer.context()` + dialectic. All LLM via OpenRouter. | **PARTIALLY WIRED** (code correct, SDK 2.0.1 present; server :8000 DOWN -> recall returns empty, fails open silently) | E6: SDK import; `prefetch`->"" and `sync_turn` no-raise with server down (0.10s / 0.00s) |
| **G-Brain** (Garry Tan's `gbrain`, via MCP + aggregator) | "the brain/graph store" | NOT a memory provider. Postgres-native knowledge brain (hybrid RAG + self-wiring graph + synthesis), reached as `mcp_gbrain_*` tools (model-invoked) and the read-only display aggregator. | **ENGINE WORKING; v2 SERVER DOWN** (:3131 and its :5432 engine down -> agent tools + Memory-tab pane non-functional, recoverable) | E5: live offline PGLite write + tsvector search; v2 server health 000 |
| **Read/merge path** (combine-on-read) | "real retrieval + merge layer" | None for the agent. One provider's prefetch is string-joined; the only multi-store merge is the display aggregator. | **BROKEN relative to the intended architecture** | E3: `prefetch_all` = `"\n\n".join`; `"Only one external provider"` rejection; aggregator is display-only |
| **Hermes per-agent isolation** (`agent-profiles/<slug>/state.db`) | "one isolated DB per sub-agent" | Exactly that, FOR the gateway-persona path. Per-agent SQLite+FTS5 store, explicit `session_db_override`, slug-validated, fail-closed. | **WORKING** (gateway personas) | E4: airtight disjoint-token isolation; traversal slugs rejected |
| **Delegate sub-agent isolation** (`delegate_tool` spawns) | (same expectation) | DIFFERENT mechanism: delegate children SHARE the parent's `state.db`, and `session_search` is DB-wide + unscanned. A child/sibling sub-agent's `session_search` can see the whole orchestrator history, unfenced. | **LEAK (real)** | E9 (post-design verification): `delegate_tool.py:1347` shares db; `search_messages` has no session_id filter; `session_search_tool.py` scan count = 0 |

> Correction (post-design verification, req #1 honesty): the Phase 1 "isolation airtight" verdict
> was correct for the gateway `agent-profiles` persona path (E4) but INCOMPLETE. The design
> council's red-team surfaced, and I independently verified (E9), that the `delegate_tool`
> sub-agent path shares the parent `state.db` and that `session_search` is DB-wide and unscanned.
> This is the real cross-agent leak requirement #10 must close, and the locked design closes it
> (lineage-scope `session_search` by default + threat-scan on return; see PHASE3 Decision B Part 1).

Legend: WORKING = exercised live and behaves correctly. PARTIALLY WIRED = code correct but a
runtime dependency is down. BROKEN = the capability the mission expected does not exist.

---

## 2. Mission's intended architecture vs reality

The mission's mental model (to be verified, and now verified as WRONG in two places):

> The orchestrator is the memory hub holding the combined stack FTS5 + G-Brain + Honcho, with
> a real retrieval and merge layer. Each sub-agent has its own isolated FTS5 ("Hermes"), one
> DB per profile, and plugs into the orchestrator.

What is actually true:

1. **Sub-agent isolation is real and correct.** Each frontend agent gets
   `~/.hermes/agent-profiles/<slug>/state.db` (its own SQLite + FTS5), created lazily by the
   gateway, slug-validated against path traversal, fail-closed so a turn can never silently
   land in the shared DB. Proven live (E4). This matches the mission's "Hermes" layer exactly.
   Note: "Hermes" is not a separate library; it is the `hermes` agent's own SessionDB scoped
   per profile via `HERMES_HOME` / explicit `session_db_override`.

2. **The orchestrator does NOT combine three stores on read.** Memory is single-provider
   (`memory.provider`, one string). The model sees three INDEPENDENT channels per turn (see
   section 4), and the only place all three planes are queried together and merged is the
   display-only `/api/memory` aggregator, which the model never sees. There is no reranker, no
   cross-store join, no shared key. Proven live (E3).

3. **G-Brain is not part of automatic recall at all.** It is model-invoked MCP tools plus the
   display aggregator. It only enters the model's context if the model decides to call an
   `mcp_gbrain_*` tool.

So the mission's "harvest the good parts and make the combined stack real" reduces to a
concrete build target: **add the retrieval-and-merge layer that does not exist yet**, while
keeping the single-provider write contract clean.

---

## 3. The actual architecture map

### Substrates (three disjoint stores, no shared key)

```
ORCHESTRATOR (main agent, ~/.hermes/)
  |
  |-- Local Markdown        MEMORY.md / USER.md / SOUL.md   (tools/memory_tool.py)
  |     - curated text, loaded ONCE into the system prompt as a frozen snapshot
  |
  |-- Session SQLite        ~/.hermes/state.db  (hermes_state.py)
  |     - messages + messages_fts (FTS5) + messages_fts_trigram
  |     - recalled only via the model-invoked `session_search` tool
  |
  |-- Active provider       Honcho server :8000  (plugins/memory/honcho/)   [DOWN]
  |     - the ONE external provider slot; writes each turn, recalls peer.context()+dialectic
  |     - holographic (~/.hermes/memory_store.db) would occupy this slot instead if selected
  |
  +-- G-Brain (NOT a provider)  gbrain serve :3131 -> Postgres :5432  [DOWN]
        - reached as mcp_gbrain_* tools (model-invoked) and the read-only aggregator

SUB-AGENTS (per frontend agent)
  +-- ~/.hermes/agent-profiles/<slug>/state.db   (own SQLite + FTS5, isolated)   [WORKING]
        - plus ~/.hermes/agent-memory/<name>/MEMORY.md for CLI/team subagent TYPES (markdown)
```

### Write path (per completed turn)

```
turn completes
  -> run_agent.py:_sync_external_memory_for_turn (background worker thread, off turn path)
       -> MemoryManager.sync_all(user, assistant) -> each provider.sync_turn()   [1 provider]
            -> Honcho: enqueue add_messages over HTTP (storage now, deriver LLM later)
  -> SessionDB.append_message (normal persistence) -> INSERT triggers populate messages_fts
  -> (only if the model calls the `memory` tool) write MEMORY.md/USER.md to disk
       AND mirror that write into the provider via MemoryManager.on_memory_write
```
Consequence: a single fact can land in up to three substrates (local MD, provider, session
FTS5) with no transaction and no cross-store dedup. The aggregator has to do heavy near-dup
collapsing on read precisely because of this.

### Read path (what the model actually sees each turn)

```
system prompt (built once per session):
  [frozen Local MD snapshot]  +  [provider static block]

current user message (rebuilt each turn):
  + MemoryManager.prefetch_all(query)  ==  "\n\n".join(each provider.prefetch())   [1 provider]
        (no rerank, no dedup; with Honcho down this is empty)

model-invoked tools (only if the model chooses):
  + session_search(...)   -> session FTS5
  + mcp_gbrain_*(...)      -> G-Brain
```
There is no step that queries the substrates together and ranks them against each other for
the agent. The `/api/memory` aggregator does query all three concurrently, but only for the
frontend Memory tab.

---

## 4. The central finding (drives Phase 3)

**There is no retrieval-and-merge layer on the agent's recall path.** Evidence (E3):
`agent/memory_manager.py:473-493` `prefetch_all` is a `for provider in self._providers:` loop
that string-joins results; `agent/memory_manager.py:364-377` hard-rejects any second external
provider; the only concurrent multi-store query is `gateway/platforms/memory_aggregator.py:
466-486` which returns a `{local, honcho, gbrain}` envelope for display.

Implications the design must address:
- No unified ranking across local / provider / FTS5 / G-Brain. The model gets whatever each
  surfaces, separately and unranked.
- Double/triple writes with no reconciliation; dedup is deferred to a display-time cleaner.
- Frozen-snapshot lag: a fact saved mid-session is not in the system prompt until the next
  session; the provider mirror may surface it sooner, creating temporary skew.
- Best-effort writes: a wedged provider silently drops the external write while local + FTS5
  succeed, so the stores drift.
- "Triple memory" is real only on the display tab, not in agent recall.

---

## 5. Operational state snapshot (live, audit time)

| Service | Port | State | Effect |
|---------|------|-------|--------|
| hermes gateway (`oc gateway run`) | 8642 | UP (PID 963, editable install of this repo) | `/api/memory` aggregator serves the local plane |
| Honcho API + pgvector + redis | 8000/5432/6379 | DOWN (Docker daemon not running) | active provider recall returns empty, silently |
| GBrain serve + worker | 3131 | DOWN (launchd last-exit 1) | `mcp_gbrain_*` tools + Memory-tab gbrain pane dead |
| GBrain Postgres engine | 5432 | DOWN | GBrain serve would crash-loop until this is up |
| ollama | 11434 | UP | unrelated |

Active config: `memory.provider: honcho` (`~/.hermes/config.yaml:430`); `mcp_servers.gbrain`
configured (line 711). holographic is installed but not selected (no `memory_store.db`).

---

## 6. Open questions and assumptions (carried into Phase 2/3)

Assumptions made (call them out, do not silently resolve):
- A1: The mission's "FTS5 store on the orchestrator" maps to BOTH the session FTS5
  (`state.db`) and the holographic provider's FTS5 (`memory_store.db`). I treat the
  holographic provider as the intended durable fact store, since session FTS5 is conversation
  history, not curated memory.
- A2: "Hermes" = the per-profile SessionDB isolation (mechanism B, `agent-profiles/`), not a
  separate library. Confirmed by `docs/design/profile-builder.md` and live isolation tests.
- A3: G-Brain is intentionally NOT a memory provider (it occupies no provider slot); this is a
  deliberate v2 "capability at the edges" choice, not an omission.

Open questions for the design phase (and for the user where flagged):
- Q1: Should the agent's recall actually combine stores (build the missing merge layer), or is
  the single-provider + model-invoked-tools model the intended end state? The mission says
  combine; I will design the merge layer.
- Q2: Honcho full store+recall is unproven live (server down). Bringing it up needs Docker +
  OpenRouter credits (flagged as paid/heavy). Same for the G-Brain hybrid/vector path.
- Q3: SDK/server version skew for Honcho (client 2.0.1 vs server 3.0.9 checked out) is an
  untested compatibility risk for peer cards / conclusions.
- Q4: Does any production frontend path send `oc_agent_id` for the main chat, or only for the
  `/app/agents` personas? Determines how often the per-agent DB is exercised vs the shared DB.
- Q5: Honcho `recall_mode` (tools vs inject) controls whether the provider contributes to
  prefetch at all; needs confirming against the live config.
