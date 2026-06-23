"""
Data dictionary fetched dynamically from each dataset's ``data_dictionary.tsv``
file on OEDI.  The TSV is cached on disk under
``{cache_dir}/metadata/{dataset_type}_{release}/data_dictionary.tsv`` so the
remote fetch only happens once per dataset.
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Dict, Optional

import requests

logger = logging.getLogger(__name__)

_DATA_DICTIONARY_FILENAME = "data_dictionary.tsv"


def _cache_path(cache_dir: Path, dataset_type: str, release: str) -> Path:
    return (
        Path(cache_dir)
        / "metadata"
        / f"{dataset_type}_{release}"
        / _DATA_DICTIONARY_FILENAME
    )


def _parse_tsv(text: str) -> Dict[str, Dict[str, str]]:
    """Parse the TSV text into ``{field_name: {column: value, ...}}``."""
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    parsed: Dict[str, Dict[str, str]] = {}
    for row in reader:
        key = (row.get("field_name") or "").strip()
        if not key:
            continue
        parsed[key] = {k: (v or "") for k, v in row.items()}
    return parsed


def fetch_data_dictionary(
    base_uri: str,
    cache_dir: Path,
    dataset_type: str,
    release: str,
) -> Dict[str, Dict[str, str]]:
    """
    Fetch ``data_dictionary.tsv`` from *base_uri* and return a dict keyed by
    metadata column name.

    The TSV is written to ``{cache_dir}/metadata/{dataset_type}_{release}/data_dictionary.tsv``
    on first fetch; subsequent calls read from the cached file with no HTTP
    request.

    Args:
        base_uri: Dataset base URI (``DatasetConfig.base_uri``).
        cache_dir: Local cache root directory.
        dataset_type: ``"resstock"`` or ``"comstock"``.
        release: Release identifier (e.g. ``"2025_release_1"``).

    Returns:
        Dict mapping ``field_name`` → ``{field_description, field_units, ...}``.
        Returns an empty dict if the file cannot be fetched (logged at WARNING).
    """
    cache_file = _cache_path(cache_dir, dataset_type, release)
    if cache_file.exists():
        return _parse_tsv(cache_file.read_text())

    url = f"{base_uri.rstrip('/')}/{_DATA_DICTIONARY_FILENAME}"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        text = response.text
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch data dictionary from %s: %s", url, exc)
        return {}

    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(text)
    return _parse_tsv(text)


def get_field_description(
    data_dict: Dict[str, Dict[str, str]],
    column: str,
    default: Optional[str] = None,
) -> str:
    """
    Return the ``field_description`` for *column* from *data_dict*.

    Falls back to *default* when the column or its description is missing; if
    *default* is also ``None``, the column name itself is returned.
    """
    entry = data_dict.get(column)
    if entry:
        desc = entry.get("field_description", "").strip()
        if desc:
            return desc
    if default is not None:
        return default
    return column
