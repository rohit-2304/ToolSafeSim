"""End-to-end pipeline runner for ToolSafeSim (Phases 8 & 9).

Executes the hybrid generation campaign (live LLM + systematic red-team/benign trajectories),
extracts sanitized samples, splits by scenario family, and exports the final dataset bundle to:
/home/rohit/Rohit/agent-security/data/
"""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

from .agents import (
    FinalAnswer,
    GroqClient,
    ResponseCache,
    TokenBucketRateLimiter,
    UsageLedger,
)
from .dataset import (
    build_dataset_from_traces,
    export_dataset_bundle,
    split_dataset_by_family,
)
from .generate import (
    CampaignGenerator,
    GenerationConfig,
    build_default_campaign_plan,
    is_episode_complete,
)
from .scenarios.factory import ScenarioFactory

DATA_DIR = Path(__file__).resolve().parents[3] / "data"
RUNS_DIR = Path(__file__).resolve().parents[2] / "runs"


def run_hybrid_pipeline(
    data_dir: Path = DATA_DIR,
    runs_dir: Path = RUNS_DIR,
    max_live_episodes: int = 5,
    max_budget_tokens: int = 120000,
    pacing_delay: float = 12.0,
    verbose: bool = False,
) -> int:
    print("================================================================================")
    print("TOOLSAFESIM: HYBRID DATASET GENERATION & EXPORT (PHASES 8 & 9)")
    print(f"Data Export Directory: {data_dir}")
    print(f"Runs Directory:        {runs_dir}")
    print(f"Verbose Mode:          {'ON' if verbose else 'OFF'}")
    print("================================================================================\n")

    episodes_dir = runs_dir / "episodes"
    episodes_dir.mkdir(parents=True, exist_ok=True)

    # 1. Import existing completed live LLM traces from smoke test
    smoke_dir = runs_dir / "smoke_test"
    imported_live_count = 0
    if smoke_dir.is_dir():
        for ep_file in smoke_dir.glob("episode_*.jsonl"):
            if is_episode_complete(ep_file):
                dest = episodes_dir / ep_file.name
                if not dest.exists():
                    shutil.copy2(ep_file, dest)
                    imported_live_count += 1
    if imported_live_count > 0:
        print(f"[*] Imported {imported_live_count} completed live Groq LLM episode traces from smoke test.")

    # 2. Configure live GroqClient with persistent ledger & rate limiting
    cache = ResponseCache(runs_dir / "cache")
    ledger_file = runs_dir / "ledger.json"
    ledger = UsageLedger(max_budget_tokens=max_budget_tokens, ledger_file=ledger_file)
    limiter = TokenBucketRateLimiter(pacing_delay=pacing_delay)
    client = GroqClient(cache=cache, rate_limiter=limiter, ledger=ledger)

    if client.api_key:
        print(f"[*] Live Groq Client active (Key: {client.api_key[:6]}...{client.api_key[-4:]})")
        print(f"    - Current token usage: {ledger.total_tokens} / {max_budget_tokens} budget ceiling")
        print(f"    - Rate limiter pacing: {limiter.pacing_delay}s between calls")

    factory = ScenarioFactory()
    config = GenerationConfig(runs_dir=runs_dir)
    generator = CampaignGenerator(config=config, client=client)

    plan = build_default_campaign_plan(factory, config)
    print(f"[*] Prepared campaign matrix: {len(plan)} planned scenario trajectories.")

    # Convert plan into hybrid executable trajectories with stratified round-robin live allocation
    # 1. Identify candidate safe trajectories (those without pre-planned attack scripts)
    safe_indices = [i for i, item in enumerate(plan) if "scripted_plan" not in item]

    # 2. Group safe candidate indices by scenario family
    by_family: dict[str, list[int]] = {}
    for idx in safe_indices:
        fid = plan[idx]["family_id"]
        by_family.setdefault(fid, []).append(idx)

    # 3. Round-robin select across all families up to max_live_episodes
    selected_live_indices: set[int] = set()
    all_fams = list(by_family.keys())
    round_idx = 0
    while len(selected_live_indices) < max_live_episodes and len(selected_live_indices) < len(safe_indices):
        added_any = False
        for fid in all_fams:
            fam_items = by_family[fid]
            if round_idx < len(fam_items):
                selected_live_indices.add(fam_items[round_idx])
                added_any = True
                if len(selected_live_indices) >= max_live_episodes:
                    break
        if not added_any:
            break
        round_idx += 1

    # 4. Build executable plan
    executable_plan = []
    live_count = 0
    for idx, item in enumerate(plan):
        fid = item["family_id"]
        _, _, safe_path, _ = factory.create_scenario(fid, seed=item["seed"])
        if "scripted_plan" in item:
            # Red-team attack variant (deterministic to guarantee exact UNSAFE labels)
            executable_plan.append(item)
        elif client.api_key and idx in selected_live_indices:
            # Live LLM trajectory across balanced families!
            live_count += 1
            executable_plan.append(item)
        else:
            # Deterministic safe trajectory fallback
            script = list(safe_path) + [FinalAnswer(content="Workflow completed successfully.")]
            new_item = dict(item)
            new_item["scripted_plan"] = script
            executable_plan.append(new_item)

    print(f"[*] Campaign plan: {live_count} live LLM episodes + {len(executable_plan) - live_count} systematic/adversarial trajectories.")

    print("[*] Executing hybrid campaign (skipping previously completed episodes)...")
    start_gen = time.time()
    report = generator.generate_campaign(executable_plan, verbose=verbose)
    gen_duration = time.time() - start_gen

    print(f"[+] Campaign finished in {gen_duration:.1f}s.")
    print(f"    - Completed episodes: {report.completed_episodes} / {report.total_episodes} ({report.completion_rate:.1%})")
    print(f"    - Policy blocks (attacks caught): {report.terminated_by_policy}")
    print(f"    - Raw samples generated: {report.total_samples} ({report.safe_samples} SAFE, {report.unsafe_samples} UNSAFE)")

    # 3. Build dataset from episode traces (Phase 9)
    print("\n[*] Building and deduplicating dataset from episode traces...")
    bundle = build_dataset_from_traces(episodes_dir)
    print(f"[+] Extracted {len(bundle.samples)} unique tool-call samples across {len(bundle.scenarios)} scenarios.")
    if bundle.diagnostics:
        print(f"    - Diagnostics logged: {len(bundle.diagnostics)} entries (deduped/unresolved).")

    # 4. Family-based zero-leakage split
    print("[*] Splitting dataset by family (Held-out families 'X7', 'X8' strictly in test)...")
    splits = split_dataset_by_family(bundle.samples)
    print(f"[+] Splits created: Train={len(splits.train)}, Val={len(splits.val)}, Test={len(splits.test)}")

    # 5. Export final dataset package to /home/rohit/Rohit/agent-security/data/
    print(f"\n[*] Exporting final dataset bundle to {data_dir}...")
    manifest = export_dataset_bundle(bundle, splits, output_dir=data_dir)

    print("\n================================================================================")
    print("DATASET EXPORT COMPLETE!")
    print(f"Total Samples: {manifest['total_samples']} (SAFE: {manifest['label_distribution']['SAFE']}, UNSAFE: {manifest['label_distribution']['UNSAFE']})")
    print(f"Splits: Train={manifest['splits']['train']} | Val={manifest['splits']['val']} | Test={manifest['splits']['test']}")
    print("\nNaive Baseline Accuracies on Test Split:")
    baselines = manifest["baseline_metrics"]
    print(f"  - Majority Class Baseline:        {baselines['majority_accuracy']:.2%}")
    print(f"  - Keyword Distinctive Baseline:    {baselines['keyword_accuracy']:.2%}")
    print(f"  - TF-IDF Centroid Classifier:     {baselines['tfidf_centroid_accuracy']:.2%}")
    print(f"  - Shortcut Probe Gate:            {'PASSED (<= 65%)' if baselines['gate_passed'] else 'FAILED'}")
    print("\nGenerated Files:")
    for fname, sha in manifest["sha256_checksums"].items():
        print(f"  - {fname:<25} SHA-256: {sha[:16]}...")
    print("  - dataset_manifest.json")
    print("================================================================================")
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="ToolSafeSim Hybrid Dataset Generator & Exporter")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print per-episode execution status")
    parser.add_argument("-l", "--live-episodes", type=int, default=5, help="Target number of live LLM episodes (default: 5)")
    parser.add_argument("-b", "--max-budget", type=int, default=120000, help="Maximum token budget ceiling (default: 120000)")
    parser.add_argument("-p", "--pacing", type=float, default=12.0, help="Minimum pacing delay between LLM calls in seconds (default: 12.0)")
    args = parser.parse_args()

    return run_hybrid_pipeline(
        max_live_episodes=args.live_episodes,
        max_budget_tokens=args.max_budget,
        pacing_delay=args.pacing,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    sys.exit(main())
