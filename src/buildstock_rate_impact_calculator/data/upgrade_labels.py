"""
Upgrade package labels fetched dynamically from each dataset's ``upgrades_lookup.json``
file on OEDI.  Labels are cached in-process after the first fetch.
"""

from __future__ import annotations

import logging
from typing import Dict

import requests

logger = logging.getLogger(__name__)

_UPGRADES_FILENAME = "upgrades_lookup.json"

# Module-level cache: base_uri -> {upgrade_id -> label}
_label_cache: Dict[str, Dict[int, str]] = {}

# Known typos in published upgrades_lookup.json files:
# (base_uri suffix, upgrade_id) -> (published label, corrected label).
# Applied only while the published label still matches, so an upstream fix
# makes the correction a no-op.
_LABEL_CORRECTIONS: dict[tuple[str, int], tuple[str, str]] = {
    ("2025/resstock_amy2018_release_1", 9): (
        "Heat Pumps Water Heater",
        "Heat Pump Water Heater",
    ),
}


def _apply_label_corrections(base_uri: str, labels: dict[int, str]) -> None:
    """Fix known typos in *labels* (in place) for the dataset at *base_uri*."""
    base = base_uri.rstrip("/")
    for (suffix, upgrade_id), (wrong, right) in _LABEL_CORRECTIONS.items():
        if base.endswith(suffix) and labels.get(upgrade_id) == wrong:
            labels[upgrade_id] = right


def fetch_upgrade_labels(base_uri: str) -> Dict[int, str]:
    """
    Fetch upgrade labels from ``upgrades_lookup.json`` at *base_uri*.

    Results are cached in memory so the remote file is only fetched once per
    process lifetime.

    Args:
        base_uri: Base URI of the dataset (``DatasetConfig.base_uri``).

    Returns:
        Dict mapping upgrade ID (``int``) to label string.  Returns an empty
        dict if the file cannot be fetched.
    """
    if base_uri in _label_cache:
        return _label_cache[base_uri]

    url = f"{base_uri.rstrip('/')}/{_UPGRADES_FILENAME}"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        raw: Dict[str, str] = response.json()
        labels: Dict[int, str] = {int(k): v for k, v in raw.items()}
        _apply_label_corrections(base_uri, labels)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch upgrade labels from %s: %s", url, exc)
        labels = {}

    _label_cache[base_uri] = labels
    return labels


def get_upgrade_label(base_uri: str, upgrade_id: int) -> str:
    """
    Return the descriptive label for *upgrade_id* by reading the dataset's
    remote ``upgrades_lookup.json``.

    Args:
        base_uri: Base URI of the dataset (``DatasetConfig.base_uri``).
        upgrade_id: Integer upgrade scenario ID.

    Returns:
        Descriptive label string.

    Raises:
        KeyError: If *upgrade_id* is not present in the fetched labels.
    """
    labels = fetch_upgrade_labels(base_uri)
    if upgrade_id not in labels:
        raise KeyError(
            f"Upgrade ID {upgrade_id} not found in labels fetched from {base_uri}. "
            f"Available IDs: {sorted(labels.keys())}"
        )
    return labels[upgrade_id]
