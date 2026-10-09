"""Evaluation helpers for the next-day PM10 risk model.

All functions take plain numpy arrays: `y` holds 0/1 labels in time order and `p` holds
probabilities (or a binary prediction for the persistence baseline).
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import average_precision_score, brier_score_loss, roc_auc_score

# Candidate decision thresholds for the F1 search.
THRESHOLD_GRID = np.round(np.arange(0.01, 1.0, 0.01), 2)
BOOTSTRAP_REPLICATES = 1000
BOOTSTRAP_BLOCK_DAYS = 14
BOOTSTRAP_SEED = 20261007


def class_balance(y: np.ndarray) -> dict:
    y = np.asarray(y)
    positives = int(np.sum(y == 1))
    return {
        "n": int(len(y)),
        "positives": positives,
        "negatives": int(len(y) - positives),
        "positive_rate": float(positives / len(y)) if len(y) else None,
    }


def confusion(y: np.ndarray, predicted: np.ndarray) -> dict:
    y = np.asarray(y).astype(int)
    predicted = np.asarray(predicted).astype(int)
    return {
        "tn": int(np.sum((y == 0) & (predicted == 0))),
        "fp": int(np.sum((y == 0) & (predicted == 1))),
        "fn": int(np.sum((y == 1) & (predicted == 0))),
        "tp": int(np.sum((y == 1) & (predicted == 1))),
    }


def precision_recall_f1(y: np.ndarray, predicted: np.ndarray) -> dict:
    c = confusion(y, predicted)
    precision = c["tp"] / (c["tp"] + c["fp"]) if (c["tp"] + c["fp"]) else 0.0
    recall = c["tp"] / (c["tp"] + c["fn"]) if (c["tp"] + c["fn"]) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": float(precision), "recall": float(recall), "f1": float(f1), **c}


def choose_threshold_max_f1(y: np.ndarray, p: np.ndarray) -> float:
    """Decision threshold on the grid that maximises F1 (first maximum; positive if p >= t)."""
    scores = [precision_recall_f1(y, (p >= t).astype(int))["f1"] for t in THRESHOLD_GRID]
    return float(THRESHOLD_GRID[int(np.argmax(scores))])


def calibration_bins(y: np.ndarray, p: np.ndarray, n_bins: int = 10) -> list[dict]:
    """Ten equal-width probability bins: predicted mean, observed rate and count per bin."""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    index = np.minimum(np.digitize(p, edges[1:-1], right=False), n_bins - 1)
    bins = []
    for b in range(n_bins):
        inside = index == b
        count = int(inside.sum())
        bins.append(
            {
                "bin": b,
                "lower": float(edges[b]),
                "upper": float(edges[b + 1]),
                "count": count,
                "predicted_mean": float(p[inside].mean()) if count else None,
                "observed_rate": float(y[inside].mean()) if count else None,
            }
        )
    return bins


def block_bootstrap_auc(
    y: np.ndarray,
    p: np.ndarray,
    replicates: int | None = None,
    block: int | None = None,
    seed: int | None = None,
) -> dict:
    """Moving-block bootstrap interval for ROC-AUC.

    Blocks of consecutive days keep the day-to-day dependence of the series, which a plain
    resample of single days would ignore and so make the interval too narrow.
    """
    replicates = BOOTSTRAP_REPLICATES if replicates is None else replicates
    block = BOOTSTRAP_BLOCK_DAYS if block is None else block
    seed = BOOTSTRAP_SEED if seed is None else seed
    y = np.asarray(y)
    p = np.asarray(p)
    n = len(y)
    rng = np.random.default_rng(seed)
    n_blocks = int(np.ceil(n / block))
    starts_max = n - block
    values = []
    for _ in range(replicates):
        starts = rng.integers(0, starts_max + 1, size=n_blocks)
        index = (starts[:, None] + np.arange(block)[None, :]).ravel()[:n]
        yy = y[index]
        if yy.min() == yy.max():
            continue
        values.append(roc_auc_score(yy, p[index]))
    values = np.asarray(values)
    return {
        "method": f"moving-block bootstrap, block {block} days, {replicates} replicates",
        "seed": seed,
        "valid_replicates": int(len(values)),
        "ci95_low": float(np.percentile(values, 2.5)),
        "ci95_high": float(np.percentile(values, 97.5)),
    }


def evaluate(
    y: np.ndarray,
    p: np.ndarray,
    threshold: float | None,
    with_calibration: bool = True,
    with_bootstrap: bool = False,
) -> dict:
    """Ranking, probability and threshold metrics for one set of rows."""
    y = np.asarray(y).astype(int)
    p = np.asarray(p, dtype=float)
    result = {
        "n": int(len(y)),
        "positive_rate": float(y.mean()),
        "roc_auc": float(roc_auc_score(y, p)),
        "pr_auc": float(average_precision_score(y, p)),
        "brier": float(brier_score_loss(y, p)),
    }
    if threshold is not None:
        result["threshold"] = float(threshold)
        result.update(precision_recall_f1(y, (p >= threshold).astype(int)))
    if with_calibration:
        result["calibration_bins"] = calibration_bins(y, p)
    if with_bootstrap:
        result["roc_auc_bootstrap"] = block_bootstrap_auc(y, p)
    return result
