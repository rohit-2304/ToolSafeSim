# ToolSafeSim — Handoff Plan (Phases 4–9)

Audience: any AI agent or engineer continuing this project. Read this file, then
`toolsafesim_research_plan.md` and `scenarios/*.md`. Scope **stops at the exported dataset (Phase 9)**.
Do NOT implement DeBERTa training/eval (Phases 10–12).

## 0. Ground rules
- Work from `/home/rohit/Rohit/agent-security/simulation`; run `uv run pytest -q`, `uv run ruff check`.
- ONE phase at a time. Finish its gate tests, report to the user, wait for "go".
- Never edit frozen `fixtures/*.json` by hand. Edit builders/YAML, then
  `uv run python -m toolsafesim.fixtures.build && uv run python -m toolsafesim.fixtures.validate`.
- New content (emails, cases, articles, docs) goes through `docs/CONTENT_GUIDE.md` + `content_cli merge`.
- Labels come ONLY from the policy labeler (Phase 4), never from an agent/LLM instruction.
- The classifier-visible input is only `Sample.model_input()` (user_goal, context, proposed_tool_call).
  No `trust_origin`, labels, scenario ids, or attack metadata may leak into it.
- Preserve existing comments/docstrings. Every phase adds `tests/test_phaseN_*.py`.
- Groq: respect rate limits, cache every response, record model/version/prompt. User supplies the key (env `GROQ_API_KEY`); ask at Phase 7.

## 1. Current state (Phases 1–3 + cross-world loader DONE, 71 tests pass)
| Module | Purpose |
|---|---|
| `schemas.py` | All pydantic models/enums (Scenario, ToolCall, Decision, TraceEvent, Sample...) |
| `state.py` | `WorldState`: hash, snapshot/restore, `commit`, `next_id` |
| `logger.py` | Append-only JSONL `EventLogger` |
| `fixtures/` + `content/*.yaml` | 5 frozen packs, 21 injections + 8 decoys, loader, validator, content CLI |
| `tools/` | 20 tools; `run_tool(state, call)` (atomic), `preview_tool` (dry-run), `tool_schemas()` |

Key facts: tools are mechanical (no safety logic). Unsafe calls are blocked BEFORE execution by the
controller; episode then ends. Malformed args = `validation_error`, not UNSAFE.
`load_pack` loads one pack; `load_packs` merges several.

### Cross-world scenarios (REQUIRED, user request)
Packs are separate worlds (emails only in productivity, DB only in billing, files/ACLs only in files...).
`load_packs([...], scalar_owner={...})` in `fixtures/loader.py` is **DONE and tested**
(`tests/test_cross_world.py`): dict stores union (key collisions error; same-identity principals union
`allowed_actions`), lists concatenate, counters max, conflicting scalars (e.g. `cwd`) need `scalar_owner`.
Tools already work on a merged state. Remaining work:
- **Phase 4:** labeler policies must be able to reference resources/principals from any merged pack;
  golden cases include cross-world calls (e.g. data from billing DB sent by `send_email` to an external
  address; confidential `/workspace` file attached/uploaded after reading an email request).
- **Phase 6:** each spec has `packs: [..]` (1 or 2 names) + `scalar_owner`. Add a **cross-world family
  set X1–X8** (hand-written specs, ~15–20% of scenarios): X1 email request -> DB lookup -> reply
  (disclosure of restricted fields); X2 email asks to upload workspace file externally; X3 web page
  instructs to email customer data; X4 repo issue text instructs to read `.env`/send it; X5 DB note field
  injects "forward to"; X6 calendar invite instructs permission change; X7 web research -> write
  confidential finding to shared drive/upload; X8 benign multi-system workflow (SAFE control).
  Each has benign twin + attack variant; injection source and sink are in DIFFERENT packs.
- **Phase 9:** report metrics split by single-world vs cross-world; keep >=1 held-out cross-world family
  in test. Record `packs` in scenarios.jsonl/manifest.

## 2. Phase 4 — Policy engine + reference labeler
**Files:** `policy/rules.py`, `policy/labeler.py`, `policy/taint.py`, `tests/test_phase4_labeler.py`,
`tests/golden/labeler_cases.yaml`.
**Build:**
1. `AuthorizationPolicy` (already in schemas) evaluated per scenario: allowed tools, recipients/domains,
   resources, fields, destinations, approval requirements.
2. `label(scenario, state_before, history, call) -> Decision(label, reason_code, evidence, rule_ids, policy_version)`.
   Five checks in order: (a) goal alignment/scope, (b) authorization, (c) data boundary (restricted
   fields, classification vs destination, external recipients), (d) instruction provenance (taint:
   call args derived from untrusted observation text/`UntrustedContent`, via normalized substring/token
   match against prior observations), (e) side effects (destructive/external without approval).
3. Reason codes: PERMITTED_ACTION, GOAL_SCOPE_VIOLATION, UNAUTHORIZED_MUTATION, UNAUTHORIZED_DISCLOSURE,
   INJECTION_COMPLIANCE, MISSING_APPROVAL, OUT_OF_SCOPE_RECIPIENT, UNRESOLVED_REVIEW (internal only).
4. Labeler uses ONLY pre-call state + history (no peeking at the result). UNRESOLVED_REVIEW cases are
   excluded from the dataset.
**Gate:** ≥150 golden cases (≥40 counterfactual pairs: same call, one fact flipped => label flips);
every rule id hit by ≥1 SAFE and ≥1 UNSAFE case; property test: label independent of post-call state;
blind-agreement ≥95% (a second reviewer/agent labels 30 random cases from the spec docs without
seeing the labeler; disagreements resolved by fixing spec or rule).

## 3. Phase 5 — Controller + mock agent
**Files:** `controller.py`, `agents/base.py`, `agents/scripted.py`, `tests/test_phase5_controller.py`.
**Build:** loop: agent proposes `ToolCall` -> labeler -> if SAFE `run_tool` + log observation, else log
UNSAFE and stop (record blocked call, no execution). Max steps, validation_error counted separately,
every step emits `TraceEvent` via `EventLogger` with state hashes. `ScriptedAgent` replays a fixed
call list (benign path, attack-compliant path, refusal path) for tests; `Agent` interface:
`next_call(view) -> ToolCall | Final`. The agent view hides `trust_origin` (`Email.agent_view`).
**Gate:** replaying a scripted trace twice gives identical JSONL (byte-equal); an UNSAFE call never
mutates state; one benign + one attacked scripted episode per domain (5 domains) pass end to end.

## 4. Phase 6 — Scenario specs and factory
**Files:** `scenarios/specs/*.yaml` (templates per family P1–P10, D1–D12, C1–C12, W1–W12, F1–F12),
`factory.py`, `mutators.py`, `loader.py::load_packs`, `tests/test_phase6_factory.py`.
**Build:** one YAML per family: pack(s), user_goal templates (≥3 paraphrases), authorization policy,
injection slot + style (from `injections.json`; vary placement: email body, web page, file, DB field,
tool output), benign decoy variant, expected safe path. Mutators: paraphrase goal, swap anchor entity,
shift dates, change injection style. Each Scenario gets `family`, `seed`, `variant_id`.
Hand-author ≥1 held-out scenario per family (flag `held_out: true`; never used for training split).
**Gate:** every spec compiles to a valid Scenario; for each family the scripted-safe agent is labeled
SAFE throughout and the scripted-attack agent hits ≥1 UNSAFE call (labeler-decided);
**text-only shortcut probe:** a bag-of-words/logistic baseline on `model_input()` text must score ≤60%
accuracy (else rebalance so injection words alone don't determine the label; add benign decoys);
balanced SAFE/UNSAFE per family.

## 5. Phase 7 — LLM agent adapter (Groq)
**Files:** `agents/llm.py`, `agents/groq_client.py`, `agents/cache.py`, `configs/generators.yaml`,
`tests/test_phase7_llm.py` (uses a fake HTTP client, no network).
**Build:** OpenAI-style tool-calling over `tool_schemas()`; a rate limiter (token bucket from configured
RPM/TPM/daily caps, honour `retry-after`, exponential backoff); on-disk response cache keyed by
hash(model, prompt, messages, tools, temperature, seed); spend/usage ledger with a hard stop at the
user's budget; personas (careful, hurried, literal, over-helpful) as system prompts; multiple
generator models listed in config. Record in each trace: generator model/version, persona, full
system prompt hash, temperature.
**Gate:** fake-client tests for rate limit, retries, cache hit (zero network), budget stop, malformed
tool-call JSON handled as `validation_error`. Then ASK the user for the key and budget, and run a
10-episode smoke test.

## 6. Phase 8 — Generation
**Files:** `generate.py`, `runs/` (gitignored), `tests/test_phase8_generation.py`.
**Build:** pilot = 100 train (≥40 UNSAFE) + 20 test; run scenarios × personas × generator models,
resumable (skip cached/complete episodes), per-episode JSONL. Re-raise with the user the "red-team
replay" question (LLM told to pursue a deviation; label still set by the labeler). Use ≥2 generator
models for train, hold out a different model and the held-out scenarios for test.
**Gate:** counts met; ≥90% of episodes complete; every UNSAFE label's evidence points to a rule;
no SAFE sample is a validation_error; a label-distribution report per family/model/persona.

## 7. Phase 9 — Dataset builder + export
**Files:** `dataset/build.py`, `dataset/split.py`, `dataset/baselines.py`, `tests/test_phase9_dataset.py`.
**Build:** one `Sample` per tool-call decision with `model_input()` only; split by scenario FAMILY
(+ held-out scenarios + held-out generator in test); dedupe; drop UNRESOLVED_REVIEW.
Export to `/home/rohit/Rohit/agent-security/data/`: `scenarios.jsonl`, `traces.jsonl`,
`toolcall_dataset.jsonl`, `splits/{train,val,test}.jsonl`, `diagnostics.jsonl`, `dataset_manifest.json`
(counts, hashes, policy/generator versions, models, prompts hash, seeds).
**Gate:** leakage tests (no family/scenario/text-duplicate overlap across splits; no forbidden keys in
`model_input`); naive baselines (majority, keyword, TF-IDF+LR) reported — keyword/TF-IDF should stay
low; manifest hashes reproduce; re-running with the same seed + cache gives a byte-identical export.
Report to the user and STOP.

## 8. Per-phase report template
Phase N done • what was built (file links) • gate results (numbers) • fixes/limitations • next phase.
