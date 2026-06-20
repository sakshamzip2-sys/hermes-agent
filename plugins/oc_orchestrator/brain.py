"""The thin advisory brain: LLM judgement at exactly the ambiguous decision points.

Per the design, mechanism (caps, recovery, liveness, the driver tick) is
deterministic code; the brain is consulted ONLY for genuinely ambiguous policy and
ALWAYS has a deterministic safe-default fallback, so the system keeps making correct
safety decisions when the model is unavailable, slow, or returns garbage.

Four entrypoints, each (goal/signature, *, llm=None) -> decision:
  1. route_decompose  -> {shape, lead, subtasks}   fallback: deterministic router + per-profile slice
  2. classify_failure -> retry|reassign|escalate|abort   fallback: retry-once-then-escalate
  3. need_verifier    -> {verify: bool, reviewer}   fallback: deterministic-checks-only + a flag
  4. fanout_or_stop   -> {approve: bool}            fallback: refuse (the safe direction)

``llm`` is an INJECTED callable ``(prompt:str) -> str`` returning the model's raw
text (expected JSON). It is model-agnostic: gateway_llm() builds one backed by the
gateway, which routes any configured model. The brain validates and bounds every
LLM output, so a hallucinated profile or a 50-way fan-out can never escape the
candidate set or the cap.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Callable, Dict, List, Optional

from . import caps, decompose, router

LLMCall = Callable[[str], str]


def gateway_llm(model: str = "claude-sonnet-4-6", *, gateway: Optional[str] = None,
                token: Optional[str] = None, max_tokens: int = 700) -> LLMCall:
    """Build a model-agnostic llm callable backed by the gateway chat-completions
    endpoint (which routes any configured model). Returns the raw assistant text."""
    base = (gateway or os.environ.get("OC_EVAL_GATEWAY") or "http://127.0.0.1:8642").rstrip("/")
    tok = token or os.environ.get("OC_EVAL_TOKEN") or "oc-hermes-local-test"

    def _call(prompt: str) -> str:
        body = json.dumps({
            "model": model, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            f"{base}/v1/chat/completions", data=body,
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read())
        return d["choices"][0]["message"]["content"]
    return _call


def _extract_json(text: str):
    """Pull the first JSON object/array out of a model response (tolerant of code
    fences and surrounding prose). Returns the parsed value or None."""
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    candidate = fence.group(1) if fence else text
    m = re.search(r"(\{.*\}|\[.*\])", candidate, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except Exception:
        return None


# 1. route + decompose ------------------------------------------------------- #

def route_decompose(goal: str, available_profiles: List[str], *,
                    llm: Optional[LLMCall] = None, max_fanout: Optional[int] = None) -> Dict:
    """Decide shape + lead deterministically (the safe floor), then for a swarm
    decompose into per-profile subtasks. The LLM (if given) proposes the
    decomposition; decompose() validates it against the candidate set and hard-caps
    the fan-out, so a bad LLM output cannot breach caps. Brain-down -> deterministic
    per-profile slices."""
    decision = router.route(goal, available_profiles=available_profiles)
    candidates = decision.candidates or [decision.profile]

    llm_fn = None
    if llm is not None and decision.shape == "swarm":
        def llm_fn(g, cands):  # adapt the raw-text llm into decompose's contract
            prompt = (
                "You are an orchestrator. Decompose this goal into independent subtasks, "
                "one per specialist, choosing only from the allowed specialists.\n"
                f"Goal: {g}\nAllowed specialists: {', '.join(cands)}\n"
                'Reply ONLY with a JSON array like '
                '[{"profile": "<specialist>", "subtask": "<what they do>"}].')
            parsed = _extract_json(llm(prompt))
            return parsed if isinstance(parsed, list) else []

    try:
        subtasks = decompose.decompose(goal, candidates, llm=llm_fn, max_fanout=max_fanout)
    except Exception:
        # Brain-down safety: if the model errors mid-decompose, fall back to the
        # deterministic per-profile decomposition so orchestration never stalls.
        subtasks = decompose.decompose(goal, candidates, llm=None, max_fanout=max_fanout)
    return {
        "shape": decision.shape,
        "lead": decision.profile,
        "rationale": decision.rationale,
        "subtasks": [{"profile": s.profile, "subtask": s.subtask} for s in subtasks],
    }


# 2. classify failure -------------------------------------------------------- #

_RECOVERY_ACTIONS = {"retry", "reassign", "escalate", "abort"}


def classify_failure(signature: Dict, *, llm: Optional[LLMCall] = None,
                     attempt_no: int = 1, max_attempts: int = 3) -> str:
    """Decide retry|reassign|escalate|abort. Deterministic policy first; the LLM is
    consulted only for an opaque error and only its validated answer is used.
    Fallback: retry while under the attempt cap, else escalate."""
    reason = (signature.get("reason") or "").lower()
    # Deterministic policy table (no LLM needed for the clear cases).
    if reason in ("process_died", "timeout", "transient"):
        return "retry" if attempt_no <= max_attempts else "escalate"
    if reason in ("security", "approval", "untrusted_input", "budget"):
        return "escalate"
    if reason in ("repeated_failure", "same_signature_twice"):
        return "reassign"
    # Ambiguous (opaque error result): ask the brain, validate, safe-default.
    if llm is not None:
        ans = _extract_json(llm(
            "A worker failed with this signature. Choose exactly one recovery action "
            "from: retry, reassign, escalate, abort. Reply ONLY as "
            '{"action": "<one>"}.\nSignature: ' + json.dumps(signature)))
        if isinstance(ans, dict) and ans.get("action") in _RECOVERY_ACTIONS:
            return ans["action"]
    return "retry" if attempt_no <= max_attempts else "escalate"


# 3. need verifier ----------------------------------------------------------- #

def need_verifier(task: Dict, artifact: Dict, *, llm: Optional[LLMCall] = None) -> Dict:
    """Decide whether a separate reviewer is needed (for non-code tasks where
    deterministic checks do not fully cover correctness). Fallback: no extra
    reviewer, flag the coverage gap for observability."""
    if (task.get("kind") or "") == "code":
        return {"verify": True, "reviewer": "reviewer"}  # code always gets the gate
    if llm is not None:
        ans = _extract_json(llm(
            "Does this task's artifact need an independent reviewer sign-off beyond "
            'deterministic checks? Reply ONLY as {"verify": true|false}.\n'
            f"Task: {json.dumps(task)}\nArtifact: {json.dumps(artifact)}"))
        if isinstance(ans, dict) and isinstance(ans.get("verify"), bool):
            return {"verify": ans["verify"], "reviewer": "reviewer" if ans["verify"] else None}
    return {"verify": False, "reviewer": None, "coverage_gap": True}


# 4. fanout or stop ---------------------------------------------------------- #

def fanout_or_stop(request: Dict, *, llm: Optional[LLMCall] = None) -> Dict:
    """Approve or refuse a worker's request for more workers. ALWAYS still subject
    to spawn_guarded downstream. Fallback: refuse (the safe direction)."""
    requested = int(request.get("requested", 0) or 0)
    ceiling = caps.HARD_CEILINGS["max_fanout"]
    if requested <= 0 or requested > ceiling:
        return {"approve": False, "reason": "out_of_bounds"}
    if llm is not None:
        ans = _extract_json(llm(
            "Should the orchestrator approve this request for more workers? Be "
            'conservative. Reply ONLY as {"approve": true|false}.\n'
            f"Request: {json.dumps(request)}"))
        if isinstance(ans, dict) and isinstance(ans.get("approve"), bool):
            return {"approve": ans["approve"], "reason": "brain"}
    return {"approve": False, "reason": "safe_default_refuse"}
