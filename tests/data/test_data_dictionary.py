"""
Tests for the ``data_dictionary`` module: HTTP fetch with on-disk cache,
TSV parsing, and the ``get_field_description`` lookup helper.

No network access — ``requests.get`` is patched in every test.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from buildstock_rate_impact_calculator.data import data_dictionary


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE_URI = "https://oedi-data-lake.s3.amazonaws.com/some/dataset"
_DATASET_TYPE = "resstock"
_RELEASE = "2025_release_1"

_SAMPLE_TSV = (
    "field_name\tfield_description\tfield_units\tallowable_enumerations\n"
    "in.state\tUSPS state abbreviation\t\tAL|AK|AZ\n"
    "in.sqft..ft2\tConditioned floor area\tft2\t\n"
    "bldg_id\tBuilding identifier\t\t\n"
)


def _make_response(text: str, status_code: int = 200) -> MagicMock:
    """Build a mock requests.Response-like object."""
    resp = MagicMock()
    resp.text = text
    resp.status_code = status_code
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(
            f"{status_code} Client Error"
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# fetch_data_dictionary
# ---------------------------------------------------------------------------


class TestFetchDataDictionary:
    def test_fetch_data_dictionary_writes_cache(self, tmp_path: Path) -> None:
        with patch(
            "buildstock_rate_impact_calculator.data.data_dictionary.requests.get",
            return_value=_make_response(_SAMPLE_TSV),
        ) as mock_get:
            result = data_dictionary.fetch_data_dictionary(
                _BASE_URI, tmp_path, _DATASET_TYPE, _RELEASE
            )

        mock_get.assert_called_once()
        cache_file = (
            tmp_path
            / "metadata"
            / f"{_DATASET_TYPE}_{_RELEASE}"
            / "data_dictionary.tsv"
        )
        assert cache_file.exists()
        assert cache_file.read_text() == _SAMPLE_TSV
        assert "in.state" in result
        assert result["in.state"]["field_description"] == "USPS state abbreviation"
        assert result["in.sqft..ft2"]["field_units"] == "ft2"

    def test_fetch_data_dictionary_uses_cache_when_present(
        self, tmp_path: Path
    ) -> None:
        cache_file = (
            tmp_path
            / "metadata"
            / f"{_DATASET_TYPE}_{_RELEASE}"
            / "data_dictionary.tsv"
        )
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(_SAMPLE_TSV)

        with patch(
            "buildstock_rate_impact_calculator.data.data_dictionary.requests.get"
        ) as mock_get:
            result = data_dictionary.fetch_data_dictionary(
                _BASE_URI, tmp_path, _DATASET_TYPE, _RELEASE
            )

        mock_get.assert_not_called()
        assert result["bldg_id"]["field_description"] == "Building identifier"

    def test_fetch_data_dictionary_returns_empty_on_404(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with patch(
            "buildstock_rate_impact_calculator.data.data_dictionary.requests.get",
            return_value=_make_response("Not Found", status_code=404),
        ):
            with caplog.at_level("WARNING"):
                result = data_dictionary.fetch_data_dictionary(
                    _BASE_URI, tmp_path, _DATASET_TYPE, _RELEASE
                )

        assert result == {}
        cache_file = (
            tmp_path
            / "metadata"
            / f"{_DATASET_TYPE}_{_RELEASE}"
            / "data_dictionary.tsv"
        )
        assert not cache_file.exists()

    def test_fetch_data_dictionary_returns_empty_on_network_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        with patch(
            "buildstock_rate_impact_calculator.data.data_dictionary.requests.get",
            side_effect=requests.ConnectionError("boom"),
        ):
            with caplog.at_level("WARNING"):
                result = data_dictionary.fetch_data_dictionary(
                    _BASE_URI, tmp_path, _DATASET_TYPE, _RELEASE
                )

        assert result == {}


# ---------------------------------------------------------------------------
# get_field_description
# ---------------------------------------------------------------------------


class TestGetFieldDescription:
    def test_get_field_description_returns_field_description_when_present(self) -> None:
        data_dict = {"in.state": {"field_description": "USPS state abbreviation"}}
        assert (
            data_dictionary.get_field_description(data_dict, "in.state")
            == "USPS state abbreviation"
        )

    def test_get_field_description_falls_back_to_default(self) -> None:
        data_dict: dict[str, dict[str, str]] = {}
        assert (
            data_dictionary.get_field_description(
                data_dict, "in.unknown", default="fallback"
            )
            == "fallback"
        )

    def test_get_field_description_falls_back_to_column_name(self) -> None:
        data_dict: dict[str, dict[str, str]] = {}
        assert (
            data_dictionary.get_field_description(data_dict, "in.unknown")
            == "in.unknown"
        )
