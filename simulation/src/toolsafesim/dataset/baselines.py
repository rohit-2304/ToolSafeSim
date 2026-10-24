"""Naive and lexical baseline classifiers for ToolSafeSim (Phase 9).

Evaluates:
- Majority class baseline
- Keyword heuristic baseline
- Pure-Python TF-IDF centroid classifier baseline

Guarantees that superficial lexical shortcuts alone cannot predict the SAFE/UNSAFE label.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Any

from ..schemas import Label, Sample
from .split import DatasetSplits


def serialize_model_input_text(sample: Sample) -> str:
    """Concatenate visible fields into clean text for lexical analysis."""
    inp = sample.model_input()
    parts = [inp.get("user_goal", "")]
    for ctx in inp.get("context", []):
        if ctx.get("arguments"):
            parts.append(json.dumps(ctx["arguments"]))
        if ctx.get("observation"):
            parts.append(ctx["observation"][:150])
    call = inp.get("proposed_tool_call", {})
    parts.append(call.get("name", ""))
    parts.append(json.dumps(call.get("arguments", {})))
    return " ".join(parts).lower()


def tokenize(text: str) -> list[str]:
    return re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())


def evaluate_baselines(splits: DatasetSplits) -> dict[str, Any]:
    """Train baselines on train split and evaluate accuracy on test split."""
    train_samples = splits.train
    test_samples = splits.test

    assert len(train_samples) > 0, "Train split cannot be empty"
    assert len(test_samples) > 0, "Test split cannot be empty"

    train_labels = [s.label.value for s in train_samples]
    test_labels = [s.label.value for s in test_samples]

    # 1. Majority baseline
    label_counts = Counter(train_labels)
    majority_label = label_counts.most_common(1)[0][0]
    majority_correct = sum(1 for tl in test_labels if tl == majority_label)
    majority_acc = majority_correct / len(test_labels)

    # 2. Keyword heuristic baseline
    train_safe_words: Counter[str] = Counter()
    train_unsafe_words: Counter[str] = Counter()

    for s in train_samples:
        words = set(tokenize(serialize_model_input_text(s)))
        if s.label == Label.SAFE:
            train_safe_words.update(words)
        else:
            train_unsafe_words.update(words)

    # A word is distinctive of UNSAFE if its frequency in UNSAFE >= 2 and ratio >= 2.0
    distinctive_unsafe = {
        w for w, cnt in train_unsafe_words.items()
        if cnt >= 2 and cnt > train_safe_words.get(w, 0) * 2
    }

    keyword_correct = 0
    for s in test_samples:
        words = set(tokenize(serialize_model_input_text(s)))
        pred = Label.UNSAFE.value if (words & distinctive_unsafe) else Label.SAFE.value
        if pred == s.label.value:
            keyword_correct += 1
    keyword_acc = keyword_correct / len(test_samples)

    # 3. TF-IDF Centroid Classifier baseline
    # Build vocabulary & document frequencies on train
    train_docs = [tokenize(serialize_model_input_text(s)) for s in train_samples]
    test_docs = [tokenize(serialize_model_input_text(s)) for s in test_samples]
    
    num_docs = len(train_docs)
    doc_freqs: Counter[str] = Counter()
    for doc in train_docs:
        doc_freqs.update(set(doc))

    vocab = {w for w, df in doc_freqs.items() if df >= 2}
    vocab_list = sorted(vocab)
    idf = {w: math.log((num_docs + 1) / (doc_freqs[w] + 1)) + 1.0 for w in vocab_list}

    def compute_tfidf(tokens: list[str]) -> dict[str, float]:
        tf = Counter(tokens)
        vec = {w: (tf[w] / len(tokens)) * idf[w] for w in tf if w in idf}
        norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
        return {w: v / norm for w, v in vec.items()}

    train_vecs = [compute_tfidf(d) for d in train_docs]
    test_vecs = [compute_tfidf(d) for d in test_docs]

    # Compute centroids for SAFE and UNSAFE
    safe_indices = [i for i, lbl in enumerate(train_labels) if lbl == Label.SAFE.value]
    unsafe_indices = [i for i, lbl in enumerate(train_labels) if lbl == Label.UNSAFE.value]

    def centroid(indices: list[int]) -> dict[str, float]:
        if not indices:
            return {}
        accum: Counter[str] = Counter()
        for idx in indices:
            accum.update(train_vecs[idx])
        return {w: val / len(indices) for w, val in accum.items()}

    safe_center = centroid(safe_indices)
    unsafe_center = centroid(unsafe_indices)

    def dot_product(v1: dict[str, float], v2: dict[str, float]) -> float:
        return sum(v1[k] * v2.get(k, 0.0) for k in v1)

    tfidf_correct = 0
    for i, tvec in enumerate(test_vecs):
        sim_safe = dot_product(tvec, safe_center)
        sim_unsafe = dot_product(tvec, unsafe_center)
        pred = Label.UNSAFE.value if sim_unsafe > sim_safe else Label.SAFE.value
        if pred == test_labels[i]:
            tfidf_correct += 1

    tfidf_acc = tfidf_correct / len(test_labels)

    return {
        "majority_class": majority_label,
        "majority_accuracy": round(majority_acc, 4),
        "keyword_accuracy": round(keyword_acc, 4),
        "tfidf_centroid_accuracy": round(tfidf_acc, 4),
        "gate_target_accuracy": "<= 0.65",
        "gate_passed": max(keyword_acc, tfidf_acc) <= 0.65,
    }
