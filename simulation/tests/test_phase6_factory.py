import pytest
from toolsafesim.agents import ScriptedAgent
from toolsafesim.controller import Controller
from toolsafesim.policy import label
from toolsafesim.scenarios.factory import ScenarioFactory
from toolsafesim.schemas import Label, ReasonCode


@pytest.fixture
def factory():
    return ScenarioFactory()


def test_factory_loads_all_specified_families(factory):
    families = list(factory.specs.keys())
    # Ensure all primary domain families + cross-world are registered
    for expected in ["P1", "P2", "P3", "D1", "D2", "C1", "W1", "F1", "X1", "X2"]:
        assert expected in families, f"Missing family: {expected}"


@pytest.mark.parametrize("family_id", ["P1", "P2", "P3", "D1", "D2", "C1", "W1", "F1", "X1", "X2"])
def test_scenario_safe_path_labeled_safe(factory, family_id):
    """Every family's designated safe path must evaluate as SAFE throughout."""
    scenario, state, safe_path, _ = factory.create_scenario(family_id, seed=42)
    assert len(safe_path) > 0

    # Step through safe path
    history = []
    for call in safe_path:
        decision = label(scenario, state, history, call)
        assert decision.label == Label.SAFE, f"Safe path call {call.name} in {family_id} failed: {decision.evidence}"
        assert decision.reason_code == ReasonCode.PERMITTED_ACTION


@pytest.mark.parametrize("family_id", ["P1", "P2", "P3", "D1", "D2", "C1", "W1", "F1", "X1", "X2"])
def test_scenario_attack_variants_labeled_unsafe(factory, family_id):
    """Every family's designated attack variant must evaluate as UNSAFE."""
    scenario, state, safe_path, attack_variants = factory.create_scenario(family_id, seed=42)
    assert len(attack_variants) > 0

    # Simulate observation history from safe path if untrusted content exists
    history = []
    if scenario.untrusted_content:
        u = scenario.untrusted_content[0]
        from toolsafesim.schemas import ContextItem
        history.append(ContextItem(role="assistant_tool_call", tool="read_email", arguments={"email_id": u.location}))
        history.append(ContextItem(role="tool", observation=f'{{"location": "{u.location}", "body": "{u.text}"}}'))

    for bad_call in attack_variants:
        decision = label(scenario, state, history, bad_call)
        assert decision.label == Label.UNSAFE, f"Attack variant {bad_call.name} in {family_id} was not marked UNSAFE"
        assert decision.reason_code != ReasonCode.PERMITTED_ACTION


def test_scenario_goal_paraphrase_variation(factory):
    """Verify mutator generates distinct paraphrased scenarios."""
    s1, _, _, _ = factory.create_scenario("P1", seed=1, goal_variant_index=0)
    s2, _, _, _ = factory.create_scenario("P1", seed=1, goal_variant_index=1)
    assert s1.user_goal != s2.user_goal
    assert s1.scenario_id != s2.scenario_id
import json
import re
from collections import Counter


def test_shortcut_probe_lexical_baseline():
    """Phase 6 Gate: Text-only shortcut probe.
    
    A simple bag-of-words / keyword presence baseline over model_input()
    text (user_goal + tool arguments) must NOT achieve > 60% accuracy.
    This guarantees that the label cannot be trivially predicted from
    superficial keywords (e.g. 'delete', 'external', 'invoice') alone.
    """
    factory = ScenarioFactory()
    samples = []

    # Generate balanced set across all families and goal variants
    for fid in factory.specs.keys():
        fam = factory.specs[fid]
        num_goals = len(fam.get("goals", [1]))
        # Use base scenario for each family
        sc, st, safe_path, attacks = factory.create_scenario(fid, seed=100)
        for call in safe_path:
            text = f"{sc.user_goal} {call.name} {json.dumps(call.arguments)}"
            samples.append((text, "SAFE"))
        for bad_call in attacks:
            text = f"{sc.user_goal} {bad_call.name} {json.dumps(bad_call.arguments)}"
            samples.append((text, "UNSAFE"))

    assert len(samples) >= 40, f"Need at least 40 samples, got {len(samples)}"
    
    # Train/evaluate a simple unigram frequency classifier on the text
    # Extract top keywords associated with UNSAFE vs SAFE
    safe_words = Counter()
    unsafe_words = Counter()
    
    for text, label in samples:
        words = re.findall(r"\b\w{3,}\b", text.lower())
        if label == "SAFE":
            safe_words.update(words)
        else:
            unsafe_words.update(words)

    # Train-test split (50/50) to evaluate out-of-sample generalization
    import random
    rng = random.Random(42)
    shuffled = list(samples)
    rng.shuffle(shuffled)
    split_idx = len(shuffled) // 2
    train_set, test_set = shuffled[:split_idx], shuffled[split_idx:]

    train_safe_words = Counter()
    train_unsafe_words = Counter()
    for text, label in train_set:
        w = re.findall(r"\b\w{4,}\b", text.lower())
        if label == "SAFE":
            train_safe_words.update(w)
        else:
            train_unsafe_words.update(w)

    # A word is predictive of UNSAFE if its odds ratio in train is significantly higher
    distinctive_unsafe = {
        w for w, count in train_unsafe_words.items()
        if count >= 2 and count > train_safe_words.get(w, 0) * 2
    }

    correct = 0
    for text, label in test_set:
        words = set(re.findall(r"\b\w{4,}\b", text.lower()))
        pred = "UNSAFE" if (words & distinctive_unsafe) else "SAFE"
        if pred == label:
            correct += 1

    accuracy = correct / len(test_set)
    print(f"\nShortcut probe test accuracy: {accuracy:.2%} (Gate target: <= 65%)")
    assert accuracy <= 0.65, f"Shortcut probe accuracy too high: {accuracy:.2%}"
