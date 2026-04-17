"""Cell grouping by metric value — GMM and K-means clustering.

Pure computation: arrays in, GroupingResult out.  No GUI or HDF5 coupling.
Groups are always ordered by ascending mean metric value (group 1 = lowest).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import pandas as pd
from numpy.typing import NDArray

logger = logging.getLogger(__name__)

MIN_CELLS_DEFAULT = 10


@dataclass(frozen=True)
class GroupingResult:
    """Result of clustering cells into groups.

    Attributes:
        group_assignments: Series with index=cell_label, value=group_id
            (1-indexed integers, ordered by ascending mean metric value).
        n_groups: Number of groups assigned.
        group_means: Mean metric value per group, ascending order.
    """

    group_assignments: pd.Series
    n_groups: int
    group_means: list[float]


def _reorder_by_mean(
    raw_labels: NDArray[np.int32],
    values: NDArray[np.float64],
    cell_labels: NDArray[np.int32],
    n_groups: int,
) -> GroupingResult:
    """Reorder cluster labels so group 1 has the lowest mean value."""
    group_means_unsorted: list[float] = []
    for g in range(n_groups):
        mask = raw_labels == g
        group_means_unsorted.append(float(np.mean(values[mask])))

    sort_order = np.argsort(group_means_unsorted)
    label_remap = np.zeros(n_groups, dtype=np.int32)
    for new_idx, old_idx in enumerate(sort_order):
        label_remap[old_idx] = new_idx + 1  # 1-indexed

    remapped = label_remap[raw_labels]
    group_means = [group_means_unsorted[i] for i in sort_order]

    assignments = pd.Series(
        data=remapped.astype(int),
        index=pd.Index(cell_labels, name="label"),
        name="group",
    )

    return GroupingResult(
        group_assignments=assignments,
        n_groups=n_groups,
        group_means=group_means,
    )


def group_cells_gmm(
    values: NDArray[np.float64],
    cell_labels: NDArray[np.int32],
    criterion: str = "bic",
    max_components: int = 10,
    min_cells: int = MIN_CELLS_DEFAULT,
) -> GroupingResult:
    """Group cells using Gaussian Mixture Model with auto component selection.

    Parameters
    ----------
    values : 1-D array of metric values (one per cell).
    cell_labels : 1-D array of cell label IDs (same length as values).
    criterion : "bic" or "silhouette" for auto-selecting the number of
        components.
    max_components : Upper limit on components to test.
    min_cells : If fewer cells than this, return a single group.

    Returns
    -------
    GroupingResult with 1-indexed group assignments ordered by ascending mean.
    """
    from sklearn.mixture import GaussianMixture

    if len(values) < min_cells:
        logger.warning(
            "Only %d cells — using single group (need %d for GMM)",
            len(values), min_cells,
        )
        return _single_group(values, cell_labels)

    X = values.reshape(-1, 1)
    n_unique = len(np.unique(values))
    max_k = min(max_components, len(values) // 5, n_unique)
    max_k = max(max_k, 1)

    if criterion == "bic":
        best_gmm = _fit_gmm_bic(X, max_k)
    elif criterion == "silhouette":
        best_gmm = _fit_gmm_silhouette(X, values, max_k)
    else:
        raise ValueError(f"Unknown criterion: {criterion!r}")

    n_groups = best_gmm.n_components
    raw_labels = best_gmm.predict(X).astype(np.int32)

    if n_groups == 1:
        logger.info("GMM selected 1 component — homogeneous population")

    return _reorder_by_mean(raw_labels, values, cell_labels, n_groups)


def _fit_gmm_bic(X: NDArray, max_k: int):
    """Select best GMM by BIC (lower is better)."""
    from sklearn.mixture import GaussianMixture

    best_bic = float("inf")
    best_gmm = None

    for k in range(1, max_k + 1):
        gmm = GaussianMixture(
            n_components=k, covariance_type="full",
            n_init=5, random_state=42,
        )
        gmm.fit(X)
        bic = gmm.bic(X)
        if bic < best_bic:
            best_bic = bic
            best_gmm = gmm

    return best_gmm


def _fit_gmm_silhouette(X: NDArray, values: NDArray, max_k: int):
    """Select best GMM by silhouette score (higher is better)."""
    from sklearn.metrics import silhouette_score
    from sklearn.mixture import GaussianMixture

    # Subsample for large datasets (silhouette is O(n^2))
    sample_size = min(len(values), 5000)
    if sample_size < len(values):
        rng = np.random.RandomState(42)
        idx = rng.choice(len(values), sample_size, replace=False)
        X_sample = X[idx]
    else:
        X_sample = X

    best_score = -1.0
    best_gmm = None

    for k in range(1, max_k + 1):
        gmm = GaussianMixture(
            n_components=k, covariance_type="full",
            n_init=5, random_state=42,
        )
        gmm.fit(X)

        if k == 1:
            # Silhouette undefined for 1 cluster — use as fallback
            if best_gmm is None:
                best_gmm = gmm
            continue

        labels = gmm.predict(X_sample)
        if len(set(labels)) < 2:
            continue

        score = silhouette_score(X_sample, labels)
        if score > best_score:
            best_score = score
            best_gmm = gmm

    return best_gmm


def group_cells_kmeans(
    values: NDArray[np.float64],
    cell_labels: NDArray[np.int32],
    n_clusters: int,
    min_cells: int = MIN_CELLS_DEFAULT,
) -> GroupingResult:
    """Group cells using K-means with a user-specified number of clusters.

    Parameters
    ----------
    values : 1-D array of metric values (one per cell).
    cell_labels : 1-D array of cell label IDs (same length as values).
    n_clusters : Number of clusters to create.
    min_cells : If fewer cells than this, return a single group.

    Returns
    -------
    GroupingResult with 1-indexed group assignments ordered by ascending mean.
    """
    from sklearn.cluster import KMeans

    if len(values) < min_cells:
        logger.warning(
            "Only %d cells — using single group (need %d for K-means)",
            len(values), min_cells,
        )
        return _single_group(values, cell_labels)

    # Cap n_clusters at the number of unique values
    n_unique = len(np.unique(values))
    actual_k = min(n_clusters, n_unique, len(values))
    if actual_k < n_clusters:
        logger.warning(
            "Reduced k from %d to %d (only %d unique values / %d cells)",
            n_clusters, actual_k, n_unique, len(values),
        )

    X = values.reshape(-1, 1)
    kmeans = KMeans(n_clusters=actual_k, n_init=10, random_state=42)
    raw_labels = kmeans.fit_predict(X).astype(np.int32)

    return _reorder_by_mean(raw_labels, values, cell_labels, actual_k)


def _single_group(
    values: NDArray[np.float64],
    cell_labels: NDArray[np.int32],
) -> GroupingResult:
    """Assign all cells to group 1."""
    assignments = pd.Series(
        data=np.ones(len(cell_labels), dtype=int),
        index=pd.Index(cell_labels, name="label"),
        name="group",
    )
    return GroupingResult(
        group_assignments=assignments,
        n_groups=1,
        group_means=[float(np.mean(values))],
    )
