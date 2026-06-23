"""
Per-row conversions for default-mode (no custom rate) fuel bills.

ComStock stores default fuel bills as different quantities across releases:
    - 2024 r2: per-building USD; weight the value at row time.
    - 2025 r3: ``weighted_*_billion_usd`` (= ``fuel_use × state_avg_rate ×
      weight`` in billion USD); scale by 1e9 to recover the row's weighted USD.

ResStock per-fuel default bills are already per-building USD with weight
applied only at totals aggregation, so the value passes through unchanged.
"""

from __future__ import annotations

from ..data.buildstock_manager import DatasetConfig


def convert_default_fuel_bill(
    value: float,
    dataset_config: DatasetConfig,
    weight: float,
) -> float:
    """
    Convert a raw default-mode fuel-bill value into per-row weighted USD.

    Args:
        value: Raw value pulled from the metadata column.
        dataset_config: Dataset configuration; must have ``dataset_type`` and
            ``release`` populated (done by ``BuildStockDataManager.__init__``).
        weight: Per-row sample weight. Used for ComStock 2024 r2; ignored for
            ComStock 2025 r3 (weight is already baked into the value) and for
            ResStock (weight is applied only at totals time).

    Returns:
        Per-row weighted USD as a float.

    Raises:
        ValueError: If the ``(dataset_type, release)`` combination is unknown.
    """
    dataset_type = dataset_config.dataset_type
    release = dataset_config.release

    if dataset_type == "resstock":
        return float(value)
    if dataset_type == "comstock":
        if release == "2024_release_2":
            return float(value) * float(weight)
        if release == "2025_release_3":
            return float(value) * 1.0e9

    raise ValueError(
        f"Cannot convert default-mode fuel bill for unsupported dataset "
        f"({dataset_type!r}, {release!r})."
    )
