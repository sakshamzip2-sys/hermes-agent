# Federated / Hierarchical Dreaming: Lead Engineer Verdict

## 1. The idea, restated faithfully

You proposed making dreaming a **tree** instead of a flat process. Concretely:

- **Leaf tier:** every specialized sub-agent (Atlas / Forge / Sage / Quill / Ledger / Scout, plus delegate children) owns its own memory stack (SQLite + FTS5 + markdown) **and** its own dreaming process that consolidates that agent's lived experience.
- **Root tier:** each leaf's dreaming **rolls UP** into the orchestrating agent's dreaming, which runs its own consolidation pass over the combined stack.
- **External tier:** the orchestrator's dreaming **federates UP/ACROSS** to Honcho's Deriver/Dreaming and GBrain's autopilot dream cron.

Net shape: a 4-tier recursive aggregation tree. Leaves dream locally, roll up to the orchestrator, which federates with the two external brains' dream loops. The whole thing is a hierarchy of LLM consolidators where each level's output becomes the next level's input.

This was evaluated, not implemented, after a 5-persona adversarial debate (DeepMind distributed-systems, Anthropic context-engineering, xAI lean/first-principles, OpenAI RL/reward, Microsoft GraphRAG/governance), three independent research streams, and a full cross-examination round.

## 2. The verdict

**ADOPT WITH MODIFICATIONS, where the modification is a near-total collapse of the topology: keep per-agent isolated stores and ONE outcome-gated, one-way promotion edge; DROP the per-leaf dreamers, the recursive upward roll-up, and the bidirectional external federation.** The core reason is decisive and was reached unanimously by all five personas and all three research streams: the load-bearing novelty of your idea (leaves dreaming and rolling UP, orchestrator rolling UP into the external brains) **reverses a safety invariant that v2's own shipped code enforces twice** (`plugins/dream_orchestrator/importer.py` `is_derived_line` AND `plugins/dreaming/candidates.py` `is_derived_fact`), whose stated purpose is literally "no recursion -> no model collapse." The upward edge plus the existing downward Honcho -> GBrain -> local cross-feed closes a cycle (leaf -> orchestrator -> GBrain dreams -> imports back DOWN to local -> re-dreamed as a leaf input), which is the precise recursive-summarization loop that model collapse (Shumailov, Nature 2024) and the Faulty-Memory results (ARC-AGI 100% -> 54%, an ALFWorld step collapsing 50 items to 1) document as catastrophic. The good 80% of your instinct is real and largely **already designed** as Phase 3 Decision B (gated, default-OFF, single-namespace promotion). The literal tree is the one shape the frontier does NOT run and your own codebase forbids.

## 3. What the debate agreed is STRONG

- **The direction is validated by the frontier.** Hierarchical multi-agent memory measurably helps: G-Memory (NeurIPS 2025) reports +20.89% embodied success and +10.12% QA accuracy from a three-tier hierarchy; Generative Agents' reflection ablation collapses coherent behavior within ~48 simulated hours without roll-up; GraphRAG's bottom-up community summaries cut root-level tokens ~97%. Your instinct that consolidation should be hierarchical and experience-driven is not a fantasy.
- **Per-agent isolated stores are correct and already airtight.** `agent-profiles/<slug>/state.db` isolation is verified fail-closed (Phase 1 E4). The leaf substrate of your tree is real for gateway personas.
- **Background ("sleep-time") consolidation off the hot path is the right pattern.** Letta sleep-time compute, Anthropic Claude Dreaming, and v2's own background dreamer all confirm it. You correctly keep dreaming off the turn path.
- **The external-federation half is already built and correct.** `plugins/dream_orchestrator` already drives local + Honcho `schedule_dream` + GBrain `autopilot-cycle` behind one `DreamTarget` interface with health-probe skip, a SQLite ledger + global lock, and idempotent runs. The "federate the trigger" part of your idea exists.
- **Consolidating the consolidator away from the actor is universally adopted.** Letta's separate sleep-time agent, v2's background dreamer: separating "who consolidates" from "who acts" is the right boundary, and you got it.

## 4. The real GAPS the debate exposed, with a concrete fix for each

**Gap 1 — Compounding drift UP the tree (the killer).** Each upward hop is a generative LLM paraphrase with a nonzero flip/drop rate p; fidelity survival across L hops is roughly (1-p)^L. A 4-tier tree paraphrases a fact up to 4 times before the root. Recursive summarization provably amplifies hallucination unless every level re-grounds in source, and model collapse loses the rare-but-true tail FIRST.
- **Fix:** Cap paraphrase depth at **L=1**. Leaves do NOT dream. A fact is distilled exactly once (the single promotion step), so (1-p)^L = (1-p). Every promoted line carries a **grounding pointer** back to its verbatim leaf source line, and the eval gate FAILS if any orchestrator/shared fact has no resolvable source (GraphRAG contextual-augmentation re-anchoring).

**Gap 2 — Duplication of Honcho/GBrain dreaming.** Honcho's Deriver and GBrain's autopilot-cycle ARE consolidation engines. A leaf dreamer + orchestrator dreamer over the same turns = 3-4 consolidation policies fighting over one fact with no shared key (Phase 1's central finding: three disjoint stores, no shared key).
- **Fix:** Downgrade the external brains from "federation peers" to **sinks**. Route **one plane per fact** (Decision C): identity -> Honcho raw turns (let its Deriver synthesize), entities -> GBrain, exact facts -> holographic. Never add a local -> external up-link. Each engine consolidates its own plane only.

**Gap 3 — Consistency / staleness compounds with depth.** A multi-tier tree stacks async lags: leaf dream cycle, then orchestrator dream cycle, then external Deriver. A user correction can take multiple cycles to reach the orchestrator's context, which confidently serves the stale rolled-up copy. There is no version vector or t_invalid propagation across tiers; the bi-temporal substrate is not yet built.
- **Fix:** Removing the leaf and external upward tiers removes the inter-tier staleness entirely. Cross-leaf consolidation becomes **lazy / read-time** (DRIFT-style, LazyGraphRAG): the orchestrator runs the MergeLayer at recall time over the leaf stores a query touches, not an eager scheduled re-reduce. Read-time fusion has no inter-tier staleness to control. Land the **bi-temporal supersede** (t_valid / t_invalid / supersedes_id, never hard-delete) before any promotion.

**Gap 4 — Cost explosion.** Per-agent dreaming = one **capable-model** consolidation pass per persona (6+), plus orchestrator, plus 2 external = N+3 passes per cycle, most over near-empty or short-lived stacks, on infra already hitting OpenRouter 402s. LazyGraphRAG shows eager hierarchical summarization can hit ~$33k on an enterprise corpus vs 0.1% of that lazy.
- **Fix:** No per-leaf dreamer at all (leaves are short-lived, share state.db on the delegate path, and have no durable corpus worth an LLM loop). Promotion is a cheap distillation gated by a threshold, not a scheduled dream. Any global "community" insight is computed lazily on demand, never on a cron over every leaf.

**Gap 5 — Premature complexity at the actual scale.** Ground truth: one user, one box, ~1.8k-row state.db, Honcho and GBrain currently DOWN, holographic dormant. The fan-out a tree optimizes for does not exist. Building 4 reduce levels + cross-level provenance + a staleness controller is managing coordination cost you manufactured.
- **Fix:** Ship the **one-level gated promotion** (Phase 3 Decision B2) as the entire "roll-up." It is ~80-85% already designed. Add depth only if and when a real multi-agent fan-out with a measured global-query workload appears.

## 5. What real teams do, and how the idea matches or diverges

| System | What they do | Match / Diverge |
|---|---|---|
| **Generative Agents** (Park et al., arXiv:2304.03442) | Single-agent reflection tree: cluster observations -> synthesize higher-level reflections. Ablation collapses behavior in ~48h. | Matches the value of roll-up, but it is **intra-agent**, not cross-agent. |
| **GraphRAG** (Microsoft, arXiv:2404.16130) + **LazyGraphRAG** + **DRIFT** | Leiden hierarchical community summaries, map-reduce on READ. Summaries are re-groundable to source spans; lazy variant defers rollup to query time at ~0.1% cost. | Matches hierarchy SHAPE; **diverges** on the safety property: GraphRAG is a read-time aggregation over a re-groundable static graph. Your tree is a write-path generative cascade with no surviving source link. |
| **G-Memory** (NeurIPS 2025, arXiv:2506.07398) | Three-tier hierarchy FOR multi-agent systems (+10-21%). Crucially it is **ONE shared hierarchy** with task-time insight distillation, NOT N per-agent dreamers rolling up. Organized around outcomes. | Closest precedent; validates direction but uses **centralized tier consolidation**, not your recursive per-agent tree. |
| **Letta** sleep-time compute (arXiv:2504.13171) | ONE separate sleep-time agent rewrites the primary's memory async on a stronger model. | Matches "consolidator != actor"; **diverges** on topology: pair-wise single-level, never a tree. |
| **Anthropic Claude Dreaming** (May 2026) | Scheduled nightly consolidation for a single managed agent: merge dupes, recency-wins contradictions, prune stale. | Single managed agent, scheduled, single-level. |
| **Mem0 / Zep / MongoDB / Collaborative Memory** | Centralized store, partition by agent_id; isolated-by-default + gated promotion into ONE shared namespace; bi-temporal validity. | This is exactly **Phase 3 Decision B/C**. The market converged on "one store, partition by agent_id," not a per-agent-store tree. |
| **MiTa** (manager-member) | Genuine two-tier rollup (members -> one manager that summarizes), ~68% gain. Stops at TWO levels. | The defensible ceiling on depth: two, not arbitrary. |

**Bottom line of the precedent:** every credible team does isolated-by-default + ONE-level gated/distilled promotion (or one shared hierarchy with centralized tiers), grounded and outcome-aware. **Nobody runs N independent per-agent dreamers recursively rolling up and federating bidirectionally with two external dream loops.** That exact topology is novel in the bad way: more failure surface, no validation, and it re-opens the model-collapse loop your own code forbids.

## 6. Recommended design IF adopted-with-modifications

This is a **buildable extension of what you are already building**, not a rewrite. Mapping to your architecture: the reconcile engine = the dreaming pass; `agent-profiles/<slug>/state.db` = the per-agent stack; promotion to `orchestrator/shared` = the roll-up edge; the existing `dream_orchestrator` Honcho -> GBrain plumbing = the external federation.

**The shape: a two-level, one-way, outcome-gated, lazy, eval-gated single edge.** Call it "federated dreaming, flattened."

### BUILD-NOW (in strict order)

0. **Close the C-4 shared-state leak FIRST.** Delegate children currently SHARE the parent `state.db` and `session_search` is DB-wide and unfenced. Lineage-scope `session_search` + threat-scan the delegate path. **No promotion of any kind is trustworthy until this lands** — promoting before it launders unscanned, possibly-poisoned child content into the orchestrator's authoritative memory through a pass that looks trustworthy.
1. **Ship the Phase 3 MergeLayer (read-path RRF fusion + rerank) and the req-#7 gold-set eval.** This is the real verified bottleneck (Phase 1: the missing retrieve-and-merge layer, not too few dreamers). The orchestrator has **no combined stack to dream over** until this exists. `merge.enabled` stays false until cross-store fused recall@5 clears the frozen floor.
2. **The ONE promotion edge (= Decision B2, hardened).** On delegation/persona completion: take the distilled `<=500`-char summary -> extract atomic facts -> reconcile (semantic dedup, recency-wins bi-temporal supersede) into the single `orchestrator/shared` schema-namespace column. Default OFF, summarize-only, background, threat-scanned. Two additions on top of plain B2:
   - **Grounding contract:** every promoted fact carries a resolvable pointer back to its verbatim leaf source line; eval fails on any unresolvable fact.
   - **Outcome gate (the one genuinely new, defensible primitive):** acceptance is conditioned on the producing agent's `turn_score` via the existing `outcome_link.py` seam — high-outcome runs promote, low-outcome runs promote nothing, abstain in the +/-0.05 dead-band. This is the frontier lesson (Memory-R1 trains ADD/UPDATE/DELETE/NOOP on downstream reward; G-Memory organizes its hierarchy around outcomes). It suppresses noise at the source so multiplicative drift never gets fuel.
3. **Provenance anti-recursion marker on every promoted line.** Extend the existing `_PROVENANCE_RE` / `is_derived_fact` marker to a third source `delegate#<slug>` so the orchestrator's own dreamer **excludes promoted facts from re-dreaming**. Non-negotiable model-collapse guard; it already exists, you extend it.

### DEFER

- **Lazy cross-leaf "community" insight at query time** (DRIFT-style), only over leaf stores a query touches. Add after the MergeLayer is solid and only if global/thematic queries appear.
- **Bi-temporal substrate hardening** (t_valid / t_invalid / supersedes_id, invalidate-don't-delete) — required before promotion is fully trustworthy; sequence it with the edge.

### NEVER

- **Per-leaf dreaming processes.** Leaves stay dumb and isolated. They accumulate their store; they do not run their own LLM consolidation loop.
- **Upward recursive roll-up beyond one level.** Depth cap = 2 (leaf -> orchestrator/shared). No leaf-of-leaf, no orchestrator re-dreaming its own promoted output.
- **A local -> external up-link into Honcho/GBrain dream loops.** Keep Honcho -> GBrain -> local strictly one-way DOWN. External brains are **sinks** routed one-plane-per-fact, never peers in an upward federation. This is the line that closes the collapse cycle; do not cross it.

**Interlink summary:** leaves write isolated stores; the orchestrator's existing single dreamer consolidates the combined stack AFTER the MergeLayer gives it one to read; the only upward flow is one outcome-gated, grounded, provenance-marked, default-OFF distilled-summary promotion into `orchestrator/shared`; the external brains keep dreaming one-way DOWN as today and receive one-plane-per-fact routed writes. Zero new core surface.

## 7. The single most important thing to prove with an eval BEFORE building any of it

**An outcome-conditioned, closed-loop falsification test for the promotion edge — recall AND fabrication AND collapse-detection, not recall alone.** Recall@k and fabrication-rate are read-path fidelity metrics that are **blind to tail-loss**: you cannot measure recall of a fact consolidation already deleted, so a recall/fabrication gate would green-light a tree that has already collapsed. The gate must therefore measure all three:

> Inject one fact at a leaf with a **known turn_score sign**, run N promotion cycles, and require: **(a)** orchestrator recall@5 of the leaf-originated fact does not regress below the frozen floor; **(b)** FABRICATION rate ~ 0, where fabrication = a promoted fact with no resolvable source line; **(c)** bounded population variance of promoted-fact confidence (the collapse detector — variance collapse is the model-collapse fingerprint) AND verified t_invalid propagation (a leaf-level invalidation actually invalidates the promoted copy upstream).

The promotion flag may not flip from OFF to summarize in any default until this is green. If outcome-gated promotion cannot beat "no promotion" on recall while holding fabrication at floor and variance bounded, it does not ship — and the burden for ever adding more depth or any upward external edge is a **held-out task-success delta**, which neither the five positions nor the three research streams could produce.

## 8. Open questions for you

1. **Scope confirmation:** Are you comfortable that "federated/hierarchical dreaming" ships as a single outcome-gated promotion edge plus the existing one-way external federation — i.e., the per-leaf dreamers and the upward external edge are explicitly DROPPED? Or is there a concrete user-facing capability you have in mind that genuinely REQUIRES the recursive upward edge and is impossible with one-way federation + one-level promotion? (No persona could name one; if you can, that reopens the analysis.)
2. **Sequencing:** Do you accept the hard precondition order — close C-4 leak -> ship MergeLayer + gold-set eval -> only then enable the promotion edge, default-OFF? This means dreaming improvements wait behind the read-path merge work, which is the verified real bottleneck.
3. **Outcome signal trust:** The whole defensible novelty rests on `turn_score` being a meaningful experience signal. How much do you trust the current `outcome_link.py` per-turn scoring? If it is noisy, the outcome gate degrades to a random promotion filter and we should ship vanilla Decision B without it until the score is trustworthy.
4. **Cost ceiling:** Even the flattened design adds capable-model distillation passes on delegation. Is there a per-user monthly token budget for background memory work I should design the trigger thresholds against (size/novelty floor before a delegation promotes)?
5. **External brains reality:** Honcho and GBrain are DOWN at audit time. Do you want the promotion edge to function fully on the local plane alone (recommended, since the external tier is a sink, not a dependency), or should it block until both external brains are back up?
