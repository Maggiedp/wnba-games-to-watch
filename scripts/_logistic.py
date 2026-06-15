"""Minimal logistic regression via IRLS (numpy only).

Offline analysis helper for the rest/travel investigation. Returns coefficients,
standard errors (sqrt of the diagonal of the inverse Fisher information), Wald
z-statistics and two-sided p-values. A design-matrix intercept column is added
automatically, so coef[0] is the intercept and coef[1:] align with X's columns.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class LogisticResult:
    coef: np.ndarray  # shape (1 + n_features,)
    stderr: np.ndarray
    zvalues: np.ndarray
    pvalues: np.ndarray


def _norm_sf(z: float) -> float:
    """Two-sided tail of the standard normal via erfc (no scipy)."""
    return math.erfc(abs(z) / math.sqrt(2.0))


def logistic_fit(
    x: np.ndarray, y: np.ndarray, max_iter: int = 100, tol: float = 1e-8
) -> LogisticResult:
    """Fit logit(P(y=1)) = b0 + X·b by IRLS. X shape (n, k); y shape (n,)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    n = x.shape[0]
    design = np.column_stack([np.ones(n), x])  # intercept + features
    beta = np.zeros(design.shape[1])

    for _ in range(max_iter):
        eta = design @ beta
        p = 1.0 / (1.0 + np.exp(-eta))
        w = np.clip(p * (1.0 - p), 1e-12, None)
        # Fisher scoring: beta += (XᵀWX)⁻¹ Xᵀ(y - p)
        grad = design.T @ (y - p)
        hess = design.T @ (design * w[:, None])
        step = np.linalg.solve(hess, grad)
        beta = beta + step
        if np.max(np.abs(step)) < tol:
            break

    # Covariance = inverse Fisher information at the optimum.
    eta = design @ beta
    p = 1.0 / (1.0 + np.exp(-eta))
    w = np.clip(p * (1.0 - p), 1e-12, None)
    cov = np.linalg.inv(design.T @ (design * w[:, None]))
    stderr = np.sqrt(np.diag(cov))
    zvalues = beta / stderr
    pvalues = np.array([_norm_sf(z) for z in zvalues])
    return LogisticResult(coef=beta, stderr=stderr, zvalues=zvalues, pvalues=pvalues)
