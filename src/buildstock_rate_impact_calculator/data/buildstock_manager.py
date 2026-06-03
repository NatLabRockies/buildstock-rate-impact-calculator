# cl_BuildStockDataManager.py

"""
BuildStock Data Manager - Unified manager for ResStock and ComStock datasets.

This module provides a unified interface for accessing and processing both ResStock 
(residential) and ComStock (commercial) building stock data from OEDI, with efficient 
batch processing capabilities and intelligent caching.

Version: 1.0.0
Compatible with: ResStock 2024 Release 2, ComStock 2024 Release 2
"""

import polars as pl
import polars.selectors as cs
import pandas as pd
import numpy as np
import fsspec
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Any, Optional, Tuple, Union, Set
from pathlib import Path
from datetime import datetime, timedelta
import json
import gzip
import io
import tarfile
import requests
from dataclasses import dataclass
from tqdm import tqdm
import logging
import threading

from buildstock_rate_impact_calculator.billing.load_profile_utils import (
    validate_load_profile, 
    ensure_8760_hours, 
    align_to_monday_start, 
    convert_to_hourly, 
    extract_timestamp, 
    prepare_for_bill_calculation,
    HOURS_PER_YEAR
)
from buildstock_rate_impact_calculator.core.logger_config import setup_logging, LogMessages

logger = logging.getLogger(__name__)


@dataclass
class DatasetConfig:
    """Configuration for specific dataset releases."""
    base_uri: str
    metadata_structure: str  # 'parquet' or 'csv_gz_tar'
    timeseries_structure: str
    default_load_column: str
    building_id_column: str
    state_column: str
    expected_resolution_minutes: int
    supported_upgrades: List[int]
    # Metadata path components (relative to base_uri)
    metadata_path: str = ""         # subdirectory under base_uri, e.g. "metadata_and_annual_results/national/parquet"
    baseline_filename: str = ""     # filename for upgrade-0 / baseline parquet
    upgrade_filename: str = ""      # filename template; use {upgrade_id:02d} for the zero-padded ID
    # Fuel energy consumption columns (annual kWh per building)
    natural_gas_energy_column: Optional[str] = None
    natural_gas_bill_column: Optional[str] = None
    propane_energy_column: Optional[str] = None
    propane_bill_column: Optional[str] = None
    fuel_oil_energy_column: Optional[str] = None
    fuel_oil_bill_column: Optional[str] = None


# OEDI public bucket — HTTPS base used in DATASET_CONFIGS, S3 base for native access
_OEDI_HTTPS_BASE = "https://oedi-data-lake.s3.amazonaws.com/"
_OEDI_S3_BASE = "s3://oedi-data-lake/"


class BuildStockDataManager:
    """
    Unified manager for ResStock and ComStock building data with efficient batch processing.
    
    This class provides a consistent interface for accessing both residential (ResStock) 
    and commercial (ComStock) building stock datasets from OEDI, with support for:
    - Efficient batch downloads using direct S3 access
    - Intelligent caching with resumable operations
    - Automatic data preparation for utility bill calculations
    - Consistent API regardless of dataset type
    
    Attributes:
        DATASET_CONFIGS: Pre-configured settings for supported dataset releases
        dataset_type: Current dataset type ('resstock' or 'comstock')
        release: Dataset release version
        cache_enabled: Whether local caching is enabled
        logger: Configured logger instance
    """
    
    # storage_options passed to Polars for anonymous S3 access
    _S3_ANON: Dict[str, Any] = {"aws_region": "us-west-2", "aws_skip_signature": "true"}

    # Pre-configured dataset specifications
    DATASET_CONFIGS = {
        'resstock_2024_release_2': DatasetConfig(
            base_uri="https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/2024/resstock_amy2018_release_2",
            metadata_structure="parquet",
            timeseries_structure="by_state",
            default_load_column="out.electricity.total.energy_consumption",
            building_id_column="bldg_id",
            state_column="in.state",
            expected_resolution_minutes=15,
            supported_upgrades=list(range(17)),  # 0-16 for ResStock
            metadata_path="metadata_and_annual_results/national/parquet",
            baseline_filename="baseline_metadata_and_annual_results.parquet",
            upgrade_filename="upgrade{upgrade_id:02d}_metadata_and_annual_results.parquet",
            natural_gas_energy_column="out.natural_gas.total.energy_consumption.kwh",
            natural_gas_bill_column="out.bills.natural_gas.usd",
            propane_energy_column="out.propane.total.energy_consumption.kwh",
            propane_bill_column="out.bills.propane.usd",
            fuel_oil_energy_column="out.fuel_oil.total.energy_consumption.kwh",
            fuel_oil_bill_column="out.bills.fuel_oil.usd",
        ),
        'resstock_2025_release_1': DatasetConfig(
            base_uri="https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/2025/resstock_amy2018_release_1",
            metadata_structure="parquet",
            timeseries_structure="by_state",
            default_load_column="out.electricity.total.energy_consumption..kwh",
            building_id_column="bldg_id",
            state_column="in.state",
            expected_resolution_minutes=15,
            supported_upgrades=list(range(17)),  # 0-16 for ResStock
            metadata_path="metadata_and_annual_results/national/full/parquet",
            baseline_filename="upgrade0.parquet",
            upgrade_filename="upgrade{upgrade_id}.parquet",
            natural_gas_energy_column="out.natural_gas.total.energy_consumption..kwh",
            natural_gas_bill_column="out.utility_bills.bills.natural_gas_bill..usd",
            propane_energy_column="out.propane.total.energy_consumption..kwh",
            propane_bill_column="out.utility_bills.propane_bills..usd",
            fuel_oil_energy_column="out.fuel_oil.total.energy_consumption..kwh",
            fuel_oil_bill_column="out.utility_bills.fuel_oil_bill..usd",
        ),
        'comstock_2024_release_2': DatasetConfig(
            base_uri="https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/2024/comstock_amy2018_release_2",
            metadata_structure="parquet",
            timeseries_structure="by_state",
            default_load_column="out.electricity.total.energy_consumption",
            building_id_column="bldg_id",
            state_column="in.state",
            expected_resolution_minutes=15,
            supported_upgrades=list(range(40)),  # 0-39 for ComStock
            metadata_path="metadata_and_annual_results_aggregates/national/basic/parquet",
            baseline_filename="baseline_agg_basic.parquet",
            upgrade_filename="upgrade{upgrade_id:02d}_agg_basic.parquet",
            natural_gas_energy_column="out.natural_gas.total.energy_consumption..kwh",
            propane_energy_column="out.propane.total.energy_consumption..kwh",
            fuel_oil_energy_column="out.fuel_oil.total.energy_consumption..kwh",
            # TODO: Add ComStock default bill columns once column names are confirmed
        ),
        'comstock_2025_release_3': DatasetConfig(
            base_uri="https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/end-use-load-profiles-for-us-building-stock/2025/comstock_amy2018_release_3",
            metadata_structure="parquet",
            timeseries_structure="by_state",
            default_load_column="out.electricity.total.energy_consumption",
            building_id_column="bldg_id",
            state_column="in.state",
            expected_resolution_minutes=15,
            supported_upgrades=list(range(40)),  # 0-39 for ComStock
            metadata_path="metadata_and_annual_results_aggregates/national/basic/parquet",
            baseline_filename="upgrade0_agg_basic.parquet",
            upgrade_filename="upgrade{upgrade_id}_agg_basic.parquet",
            natural_gas_energy_column="out.natural_gas.total.energy_consumption..kwh",
            propane_energy_column="out.propane.total.energy_consumption..kwh",
            fuel_oil_energy_column="out.fuel_oil.total.energy_consumption..kwh",
            # TODO: Add ComStock default bill columns once column names are confirmed
        )
    }
    
    def __init__(
        self, 
        dataset_type: str = 'resstock',
        release: str = '2024_release_2',
        metadata_uri: Optional[str] = None,
        cache_enabled: bool = True,
        cache_dir: Optional[Path] = None,
        log_file: Optional[Path] = None,
        max_workers: int = 32
    ):
        """
        Initialize the BuildStock data manager.
        
        Args:
            dataset_type: Type of dataset ('resstock' or 'comstock')
            release: Release version (e.g., '2024_release_2')
            metadata_uri: Custom metadata URI (overrides default if provided)
            cache_enabled: Enable local caching for downloads and processing
            cache_dir: Custom cache directory (defaults to ./buildstock_cache)
            log_file: Optional path to log l
            max_workers: Maximum number of concurrent download workers
            
        Raises:
            ValueError: If dataset_type/release combination is not supported
        """
        # Validate and set dataset configuration
        config_key = f"{dataset_type}_{release}"
        if config_key not in self.DATASET_CONFIGS:
            available_configs = list(self.DATASET_CONFIGS.keys())
            raise ValueError(
                f"Unsupported dataset configuration: {config_key}. "
                f"Available configurations: {available_configs}"
            )
        
        self.dataset_type = dataset_type
        self.release = release
        self.config = self.DATASET_CONFIGS[config_key]
        self.max_workers = max_workers
        
        # Set up logging
        setup_logging(log_file=log_file)
        self.logger = logging.getLogger(__name__)

        # Configure caching
        self.cache_enabled = cache_enabled
        if cache_enabled:
            self.cache_dir = cache_dir or Path("./buildstock_cache")
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            self._setup_cache_structure()

        # Set metadata URI
        if metadata_uri:
            self.metadata_uri = metadata_uri
        else:
            self.metadata_uri = self._get_default_metadata_uri()

        # Anonymous S3 filesystem (used by legacy exists/open methods)
        self.fs = fsspec.filesystem("s3", anon=True)

        # Initialize state storage
        self._metadata = None
        self._filtered_metadata = None
        self._state_mapping = {}
        
        self.logger.info(
            f"Initialized BuildStock data manager: {dataset_type} {release}, "
            f"cache_enabled={cache_enabled}, max_workers={max_workers}"
        )
    
    def _setup_cache_structure(self) -> None:
        """Set up cache directory structure."""
        self.cache_metadata_dir = self.cache_dir / "metadata"
        self.cache_timeseries_dir = self.cache_dir / "timeseries" 
        self.cache_processed_dir = self.cache_dir / "processed"
        
        for cache_subdir in [self.cache_metadata_dir, self.cache_timeseries_dir, self.cache_processed_dir]:
            cache_subdir.mkdir(parents=True, exist_ok=True)
    
    @staticmethod
    def _to_s3_uri(https_uri: str) -> str:
        """Convert an OEDI HTTPS URI to a native S3 URI.

        Example::

            https://oedi-data-lake.s3.amazonaws.com/nrel-pds-building-stock/...
            → s3://oedi-data-lake/nrel-pds-building-stock/...
        """
        return https_uri.replace(_OEDI_HTTPS_BASE, _OEDI_S3_BASE)

    def _get_default_metadata_uri(self) -> str:
        """Get default metadata URI based on dataset configuration."""
        uri = (
            f"{self.config.base_uri}/{self.config.metadata_path}"
            f"/{self.config.baseline_filename}"
        )
        return self._to_s3_uri(uri)

    def get_metadata_uri_for_upgrade(self, upgrade_id: int) -> str:
        """Return the metadata URI for a specific upgrade.

        Args:
            upgrade_id: Upgrade index (0 = baseline).

        Returns:
            String URI pointing to the upgrade's metadata/annual-results file.
        """
        if upgrade_id == 0:
            return self.metadata_uri
        filename = self.config.upgrade_filename.format(upgrade_id=upgrade_id)
        uri = f"{self.config.base_uri}/{self.config.metadata_path}/{filename}"
        return self._to_s3_uri(uri)

    def get_fuel_columns_for_type(self, fuel_type: str) -> tuple[Optional[str], Optional[str]]:
        """Return (energy_column, bill_column) for the given fuel type from this dataset's config.

        Args:
            fuel_type: One of "natural_gas", "propane", "fuel_oil".

        Returns:
            Tuple of (energy_column_name, bill_column_name); either may be None if
            the dataset does not provide that column.
        """
        col_map = {
            "natural_gas": ("natural_gas_energy_column", "natural_gas_bill_column"),
            "propane":      ("propane_energy_column",     "propane_bill_column"),
            "fuel_oil":     ("fuel_oil_energy_column",    "fuel_oil_bill_column"),
        }
        if fuel_type not in col_map:
            raise ValueError(f"Unknown fuel type: {fuel_type}")
        energy_attr, bill_attr = col_map[fuel_type]
        return (
            getattr(self.config, energy_attr, None),
            getattr(self.config, bill_attr, None),
        )
    
    def _deduplicate_columns(self, requested_columns: List[str]) -> List[str]:
        """
        Deduplicate column list while preserving order and ensuring required columns are included.
        
        Args:
            requested_columns: List of columns that may contain duplicates
            
        Returns:
            Deduplicated list of columns
        """
        required_columns = [self.config.building_id_column, self.config.state_column]
        
        # Use dict.fromkeys() to preserve order while removing duplicates
        all_columns = list(dict.fromkeys(requested_columns + required_columns))
        
        return all_columns
    
    def load_metadata(self, columns: Optional[List[str]] = None, force_reload: bool = False) -> pl.DataFrame:
        """
        Load metadata from the dataset with intelligent caching.
        
        Args:
            columns: Optional list of columns to load (for memory efficiency)
            force_reload: Force reload even if cached version exists
            
        Returns:
            Metadata as a Polars DataFrame
            
        Raises:
            Exception: If metadata loading fails
        """
        self.logger.info(LogMessages.PROCESSING_START.format("metadata loading"))
        
        # Check cache first
        cache_key = f"{self.dataset_type}_{self.release}_metadata.parquet"
        cache_file = self.cache_metadata_dir / cache_key if self.cache_enabled else None
        
        if cache_file and cache_file.exists() and not force_reload:
            self.logger.info(f"Loading metadata from cache: {cache_file}")
            try:
                if columns:
                    # Deduplicate columns and ensure required columns are included
                    columns_to_load = self._deduplicate_columns(columns)
                    
                    # Check which columns actually exist in cache
                    # available_columns = list(pl.scan_parquet(cache_file).schema.keys())
                    available_columns = list(pl.scan_parquet(cache_file).collect_schema().keys())
                    final_columns = [col for col in columns_to_load if col in available_columns]
                    
                    if final_columns:
                        self._metadata = pl.scan_parquet(cache_file).select(final_columns).collect()
                    else:
                        # If no requested columns exist, load all
                        self._metadata = pl.read_parquet(cache_file)
                else:
                    self._metadata = pl.read_parquet(cache_file)
                    
                self._update_state_mapping(self._metadata)
                self.logger.info("Successfully loaded metadata from cache")
                return self._metadata
                
            except Exception as e:
                self.logger.warning(f"Failed to load from cache: {e}. Fetching fresh data.")
        
        # Load fresh metadata based on dataset type
        try:
            if self.config.metadata_structure == 'parquet':
                metadata = self._load_metadata_parquet(columns)
            elif self.config.metadata_structure == 'csv_gz_tar':
                metadata = self._load_metadata_csv_gz_tar(columns)
            else:
                raise ValueError(f"Unsupported metadata structure: {self.config.metadata_structure}")
            
            self._metadata = metadata
            self._update_state_mapping(self._metadata)
            
            # Cache the loaded metadata
            if self.cache_enabled and cache_file:
                try:
                    self._metadata.write_parquet(cache_file, compression='snappy')
                    self.logger.info(f"Cached metadata to: {cache_file}")
                except Exception as e:
                    self.logger.warning(f"Failed to cache metadata: {e}")
            
            self.logger.info(LogMessages.PROCESSING_COMPLETE.format("metadata loading"))
            self.logger.debug(LogMessages.data_summary("metadata", metadata))
            
            return metadata
            
        except Exception as e:
            self.logger.error(LogMessages.PROCESSING_ERROR.format("metadata loading", str(e)))
            raise
    
    def _load_metadata_parquet(self, columns: Optional[List[str]] = None) -> pl.DataFrame:
        """Load metadata from Parquet format (ResStock)."""
        self.logger.info(f"Loading Parquet metadata from: {self.metadata_uri}")

        try:
            if columns:
                columns_to_load = self._deduplicate_columns(columns)
                metadata = (
                    pl.scan_parquet(
                        self.metadata_uri,
                        storage_options=self._S3_ANON
                    )
                    .select(columns_to_load)
                    .collect()
                )
            else:
                metadata = pl.read_parquet(
                    self.metadata_uri,
                    storage_options=self._S3_ANON
                )

            return metadata

        except Exception as e:
            self.logger.error(f"Error loading Parquet metadata: {e}")
            raise
    
    def _load_metadata_csv_gz_tar(self, columns: Optional[List[str]] = None) -> pl.DataFrame:
        """Load metadata from CSV.GZ.TAR format (ComStock)."""
        self.logger.info(f"Loading CSV.GZ.TAR metadata from: {self.metadata_uri}")
        
        try:
            response = requests.get(self.metadata_uri, stream=True)
            response.raise_for_status()

            with gzip.GzipFile(fileobj=response.raw) as gz_file:
                decompressed_archive_bytes = io.BytesIO(gz_file.read())
                
                with tarfile.open(fileobj=decompressed_archive_bytes, mode='r:') as tar:
                    # Find CSV file in TAR archive
                    csv_member_info = None
                    for member in tar.getmembers():
                        if member.isfile() and member.name.lower().endswith('.csv'):
                            csv_member_info = member
                            break
                    
                    if not csv_member_info:
                        raise FileNotFoundError("Could not find CSV file in TAR archive")
                    
                    extracted_csv_file = tar.extractfile(csv_member_info)
                    if not extracted_csv_file:
                        raise IOError("Could not extract CSV file from TAR archive")

                    # Read CSV with error handling
                    try:
                        metadata = pl.read_csv(
                            extracted_csv_file,
                            schema_overrides={'in.number_stories': pl.Utf8}
                        )
                    except Exception as e:
                        # Fallback: read as all strings
                        self.logger.warning(f"Initial CSV read failed ({e}). Using fallback method.")
                        extracted_csv_file_fallback = tar.extractfile(csv_member_info)
                        if not extracted_csv_file_fallback:
                            raise IOError("Could not re-extract CSV file for fallback")
                        
                        # Get preview to determine schema
                        preview_df = pl.read_csv(extracted_csv_file_fallback, infer_schema_length=10, n_rows=5)
                        all_string_schema = {col: pl.Utf8 for col in preview_df.columns}
                        
                        # Re-extract for final read
                        extracted_csv_file_final = tar.extractfile(csv_member_info)
                        if not extracted_csv_file_final:
                            raise IOError("Could not re-extract CSV file for final read")
                        
                        metadata = pl.read_csv(
                            extracted_csv_file_final,
                            schema_overrides=all_string_schema,
                            ignore_errors=True
                        )

            # Filter columns if specified
            if columns:
                columns_to_keep = self._deduplicate_columns(columns)
                available_columns = metadata.columns
                final_columns = [col for col in columns_to_keep if col in available_columns]
                
                if final_columns:
                    metadata = metadata.select(final_columns)
                # If no requested columns exist, keep all metadata

            return metadata
            
        except Exception as e:
            self.logger.error(f"Error loading CSV.GZ.TAR metadata: {e}")
            raise
    
    def _update_state_mapping(self, metadata: pl.DataFrame) -> None:
        """Update internal state mapping from building ID to state."""
        if (self.config.building_id_column in metadata.columns and 
            self.config.state_column in metadata.columns):
            
            self._state_mapping.clear()
            for row in metadata.select([self.config.building_id_column, self.config.state_column]).iter_rows(named=True):
                bldg_id = row[self.config.building_id_column]
                state = row[self.config.state_column]
                if state and pd.notna(state) and str(state).strip():
                    self._state_mapping[bldg_id] = str(state).strip()
    
    def get_available_columns(self) -> List[str]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Not called by the application.

        Get all available columns in the metadata.
        
        Returns:
            List of column names
        """
        try:
            if self.config.metadata_structure == 'parquet':
                return list(pl.scan_parquet(self.metadata_uri).schema.keys())
            else:
                # For CSV.GZ.TAR, we need to load a sample to get columns
                if self._metadata is not None:
                    return self._metadata.columns
                else:
                    # Load just the required columns to get schema info
                    sample_metadata = self.load_metadata(columns=[self.config.building_id_column])
                    return sample_metadata.columns
        except Exception as e:
            self.logger.error(f"Error getting available columns: {str(e)}")
            raise
    
    def filter_by_criteria(self, criteria_dict: Dict[str, Any]) -> pl.DataFrame:
        """
        Filter metadata by multiple criteria.
        
        Args:
            criteria_dict: Dictionary mapping column names to filter values
                
        Returns:
            Filtered metadata as a Polars DataFrame
        """
        self.logger.info(f"Filtering metadata by criteria: {criteria_dict}")
        
        try:
            # Load metadata if not already loaded
            if self._metadata is None:
                # Get columns needed for filtering plus required columns
                columns_needed = self._deduplicate_columns(list(criteria_dict.keys()))
                self.load_metadata(columns_needed)
            
            # Check if we need additional columns not currently loaded
            missing_columns = [col for col in criteria_dict.keys() if col not in self._metadata.columns]
            if missing_columns:
                # Need to reload with additional columns
                current_columns = self._metadata.columns
                all_needed_columns = self._deduplicate_columns(current_columns + missing_columns)
                self.load_metadata(all_needed_columns, force_reload=True)
            
            # Start with all data
            filtered_df = self._metadata
            
            # Apply each filter criterion
            for column, value in criteria_dict.items():
                if column not in filtered_df.columns:
                    raise ValueError(f"Column '{column}' not found in metadata")
                
                # Apply filter based on value type
                if isinstance(value, list):
                    # Multiple possible values (OR condition)
                    filtered_df = filtered_df.filter(pl.col(column).is_in(value))
                else:
                    # Single value
                    filtered_df = filtered_df.filter(pl.col(column) == value)
            
            self._filtered_metadata = filtered_df
            
            # Update state mapping with filtered data
            self._update_state_mapping(filtered_df)
            
            self.logger.info(f"Filtering complete. Matched {len(filtered_df)} records.")
            return filtered_df
            
        except Exception as e:
            self.logger.error(f"Error filtering metadata: {str(e)}")
            raise
    
    def get_building_ids(self, filtered_metadata: Optional[pl.DataFrame] = None) -> List[Any]:
        """
        Extract building IDs from filtered metadata.
        
        Args:
            filtered_metadata: Optional filtered metadata DataFrame
            
        Returns:
            List of building IDs
        """
        try:
            if filtered_metadata is not None:
                metadata = filtered_metadata
            elif self._filtered_metadata is not None:
                metadata = self._filtered_metadata
            else:
                metadata = self._metadata

            if metadata is None or metadata.is_empty():
                self.logger.warning("No metadata available. Loading default metadata.")
                self.load_metadata([self.config.building_id_column, self.config.state_column])
                metadata = self._metadata
            
            building_ids = metadata[self.config.building_id_column].to_list()
            self.logger.info(f"Extracted {len(building_ids)} building IDs.")
            return building_ids
            
        except Exception as e:
            self.logger.error(f"Error extracting building IDs: {str(e)}")
            raise

    def get_upgrade_applicability(
        self,
        upgrade_id: int,
        building_ids: List[Any],
    ) -> Dict[Any, bool]:
        """
        Return a mapping of building_id → applicable (bool) for the given upgrade.

        For upgrade 0 (baseline), all buildings are treated as applicable.
        For non-baseline upgrades, reads the ``applicability`` column from the
        upgrade metadata parquet. Buildings absent from the upgrade metadata
        are treated as not applicable.

        Args:
            upgrade_id: Upgrade scenario ID (0 = baseline, always True).
            building_ids: List of building IDs to check.

        Returns:
            Dict mapping building_id → bool for every ID in building_ids.
        """
        if upgrade_id == 0:
            return {bid: True for bid in building_ids}

        uri = self.get_metadata_uri_for_upgrade(upgrade_id)
        bldg_col = self.config.building_id_column

        try:
            # df = (
            #     pl.scan_parquet(uri, storage_options=self._S3_ANON)
            #     .select([bldg_col, "applicability"])
            #     .filter(pl.col(bldg_col).is_in(building_ids))
            #     .collect()
            # )
            # result: Dict[Any, bool] = {
            #     row[bldg_col]: bool(row["applicability"])
            #     for row in df.iter_rows(named=True)
            # }
            # # Buildings absent from the upgrade metadata default to not applicable
            # for bid in building_ids:
            #     if bid not in result:
            #         result[bid] = False
            # return result
            df = (
                pl.scan_parquet(uri, storage_options=self._S3_ANON)
                .filter(
                    pl.any_horizontal(
                        cs.starts_with('upgrade.').is_not_null()
                    )
                )
                .select('bldg_id')
                .collect()
            )
            applicable_ids = set(df.to_series().to_list())
            result = dict.fromkeys(building_ids, False)
            for ids in applicable_ids.intersection(building_ids):
                result[ids] = True
            return result
        except Exception as exc:
            self.logger.warning(
                f"Could not fetch applicability for upgrade {upgrade_id}: {exc}. "
                "Treating all buildings as applicable."
            )
            return {bid: True for bid in building_ids}

    def estimate_download_size(self, building_ids: List[Any], upgrades: Optional[List[int]] = None) -> int:
        """
        Estimate the total download size in bytes for given building IDs and upgrades.

        Uses a per-building-per-upgrade average file size derived from empirical
        OEDI data measurements (~90 KB per 1-hour timeseries parquet file).

        Args:
            building_ids: List of building IDs to estimate for.
            upgrades: List of upgrade scenarios (defaults to [0] for baseline only).

        Returns:
            Estimated total size in bytes.
        """
        if upgrades is None:
            upgrades = [0]
        BYTES_PER_BUILDING_PER_UPGRADE = 90_000  # ~90 KB empirical average
        return len(building_ids) * len(upgrades) * BYTES_PER_BUILDING_PER_UPGRADE

    def get_available_filter_columns(self, metadata_df: pl.DataFrame) -> List[str]:
        """
        Return metadata columns suitable for user-facing filtering.

        Includes only columns that start with 'in.' (input characteristics),
        excluding the building ID column and any output/weight columns.

        Args:
            metadata_df: Loaded metadata DataFrame.

        Returns:
            Sorted list of filterable column names.
        """
        return sorted([
            col for col in metadata_df.columns
            if col.startswith("in.")
            and col != self.config.state_column
        ])

    def get_column_unique_values(self, metadata_df: pl.DataFrame, column: str) -> List[Any]:
        """
        Return sorted unique values for a metadata column.

        Args:
            metadata_df: Loaded metadata DataFrame.
            column: Column name to get unique values for.

        Returns:
            Sorted list of unique values.

        Raises:
            ValueError: If the column does not exist in the DataFrame.
        """
        if column not in metadata_df.columns:
            raise ValueError(f"Column '{column}' not found in metadata DataFrame.")
        return sorted(metadata_df[column].drop_nulls().unique().to_list())

    def filter_metadata(self, metadata_df: pl.DataFrame, filters: Dict[str, Any]) -> pl.DataFrame:
        """
        Filter a metadata DataFrame by a dictionary of column→values criteria.

        Each filter is applied as an AND condition. If a filter value is a list,
        the column must match any of the values (IN filter). If a scalar, exact match.

        Args:
            metadata_df: Loaded metadata DataFrame.
            filters: Dict mapping column name to a value or list of values.

        Returns:
            Filtered DataFrame.

        Raises:
            ValueError: If a filter column does not exist in the DataFrame.
        """
        result = metadata_df
        for column, value in filters.items():
            if column not in result.columns:
                raise ValueError(f"Filter column '{column}' not found in DataFrame.")
            if isinstance(value, list):
                result = result.filter(pl.col(column).is_in(value))
            else:
                result = result.filter(pl.col(column) == value)
        return result

    def download_timeseries_with_progress(
        self,
        building_ids: List[Any],
        upgrades: List[int],
        output_dir: Path,
        progress_cb: Optional[Any],
        cancel_event: Optional[Any] = None,
        load_column: Optional[str] = None,
        timestamp_column: str = 'timestamp',
    ) -> Dict[str, "pl.DataFrame"]:
        """
        Download timeseries parquet files for given buildings/upgrades with progress
        callbacks and cancellation support.

        Only the timestamp and load columns are fetched from each remote file, and
        values are aggregated to hourly resolution before being written locally.
        Files are written to output_dir as ``{building_id}_{upgrade}.parquet``.
        If cancel_event is set before or during download, any files already written
        to output_dir by this call are deleted and an empty dict is returned.

        Args:
            building_ids: List of building IDs to download.
            upgrades: List of upgrade scenario IDs.
            output_dir: Local directory to write parquet files.
            progress_cb: Optional callable ``(done: int, total: int, msg: str)``
                         called after each successful file download.
            cancel_event: Optional ``threading.Event``; checked between downloads.
            load_column: Name of the energy consumption column to read. Defaults to
                         the dataset's configured default load column.
            timestamp_column: Name of the timestamp column. Defaults to 'timestamp'.

        Returns:
            Dict mapping ``"{building_id}_{upgrade}"`` to the loaded DataFrame
            (hourly, with only timestamp and load columns).
            Empty dict if cancelled before any downloads completed.
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        load_col = load_column or self.config.default_load_column

        tasks = []
        for building_id in building_ids:
            state = self.get_building_state(building_id)
            if state is None:
                self.logger.warning(f"No state for building {building_id}, skipping.")
                continue
            for upgrade in upgrades:
                try:
                    uri = self._get_load_profile_uri(building_id, upgrade, state)
                    tasks.append((building_id, upgrade, uri))
                except ValueError as exc:
                    self.logger.warning(f"Cannot build URI for {building_id}/{upgrade}: {exc}")

        total = len(tasks)
        results: Dict[str, pl.DataFrame] = {}
        written_files: List[Path] = []
        done_count = 0
        lock = threading.Lock()

        def _fetch_one(
            building_id: Any, upgrade: int, uri: str
        ) -> Optional[Tuple[str, pl.DataFrame, Path]]:
            """Fetch two columns from S3 via Parquet projection, aggregate, write locally."""
            if cancel_event is not None and cancel_event.is_set():
                return None
            df = (
                pl.scan_parquet(
                    uri,
                    storage_options=self._S3_ANON
                )
                .select([timestamp_column, load_col])
                .group_by_dynamic(
                    pl.col(timestamp_column),
                    every="1h",
                    closed="right",
                    label="right",
                )
                .agg(pl.col(load_col).sum())
                .collect()
            )
            out_path = output_dir / f"{building_id}_{upgrade}.parquet"
            df.write_parquet(out_path)
            return f"{building_id}_{upgrade}", df, out_path

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_task = {
                executor.submit(_fetch_one, bid, upg, uri): (bid, upg)
                for bid, upg, uri in tasks
            }

            for future in as_completed(future_to_task):
                # Check cancellation after each completed future
                if cancel_event is not None and cancel_event.is_set():
                    executor.shutdown(wait=False, cancel_futures=True)
                    self.logger.info("Download cancelled — cleaning up written files.")
                    with lock:
                        for f in written_files:
                            try:
                                f.unlink(missing_ok=True)
                            except Exception:
                                pass
                    return {}

                bid, upg = future_to_task[future]
                try:
                    result = future.result()
                except Exception as exc:
                    self.logger.warning(f"Failed to download {bid}/{upg}: {exc}")
                    continue

                if result is None:
                    continue

                key, df, out_path = result
                with lock:
                    results[key] = df
                    written_files.append(out_path)
                    done_count += 1
                    current = done_count

                if progress_cb is not None:
                    try:
                        progress_cb(current, total, f"Downloaded {bid} upgrade {upg}")
                    except Exception:
                        pass

        return results

    def load_cached_timeseries(
        self,
        building_ids: List[Any],
        upgrades: List[int],
        output_dir: Path,
    ) -> Dict[str, "pl.DataFrame"]:
        """
        Load previously-downloaded timeseries parquet files without hitting the network.

        For each (building_id, upgrade) pair, looks for a file named
        ``{building_id}_{upgrade}.parquet`` in output_dir. Only files that
        actually exist are loaded; missing ones are silently skipped.

        Args:
            building_ids: List of building IDs to look up.
            upgrades: List of upgrade scenario IDs.
            output_dir: Directory that was passed to download_timeseries_with_progress.

        Returns:
            Dict mapping ``"{building_id}_{upgrade}"`` to the loaded DataFrame.
            Only contains entries for files that exist on disk.
        """
        output_dir = Path(output_dir)
        results: Dict[str, pl.DataFrame] = {}
        for building_id in building_ids:
            for upgrade in upgrades:
                path = output_dir / f"{building_id}_{upgrade}.parquet"
                if path.exists():
                    try:
                        results[f"{building_id}_{upgrade}"] = pl.read_parquet(path)
                    except Exception as exc:
                        self.logger.warning(
                            f"Could not read cached file {path}: {exc}"
                        )
        return results

    def get_building_state(self, building_id: Any) -> Optional[str]:
        """
        Get the state for a building ID.
        
        Args:
            building_id: Building ID
            
        Returns:
            State code or None if not found
        """
        return self._state_mapping.get(building_id)
    
    def _get_load_profile_uri(self, building_id: Any, upgrade: int = 0, state: Optional[str] = None) -> str:
        """
        Construct URI for a building's load profile.
        
        Args:
            building_id: Building ID
            upgrade: Upgrade scenario number
            state: Two-letter state code (if None, will be looked up)
            
        Returns:
            URI to the load profile Parquet file
            
        Raises:
            ValueError: If state cannot be determined or upgrade value is invalid
        """
        # Validate upgrade range
        if upgrade not in self.config.supported_upgrades:
            raise ValueError(
                f"Upgrade {upgrade} not supported for {self.dataset_type}. "
                f"Supported upgrades: {self.config.supported_upgrades}"
            )
        
        # Get state if not provided
        if state is None:
            state = self.get_building_state(building_id)
            if state is None:
                raise ValueError(f"Could not determine state for building {building_id}")
        
        # Construct the URI as a native S3 URI for efficient access
        uri = self._to_s3_uri(
            f"{self.config.base_uri}/timeseries_individual_buildings/"
            f"{self.config.timeseries_structure}/upgrade={upgrade}/"
            f"state={state}/{building_id}-{upgrade}.parquet"
        )

        return uri
    
    def load_and_prepare_profiles_efficient(
        self,
        building_ids: List[Any],
        upgrades: List[int] = [0],
        max_workers: Optional[int] = None,
        enable_resume: bool = True
    ) -> Dict[str, Dict[str, Any]]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Only called from ``load_building_profiles()`` and
            ``load_and_prepare_profiles()``, neither of which is called by the
            application.

        Efficiently load and prepare multiple building profiles with batch processing.
        
        This method uses direct S3 access with high parallelization to 
        improve download performance compared to sequential processing.
        
        Args:
            building_ids: List of building IDs to process
            upgrades: List of upgrade scenarios to process
            max_workers: Maximum concurrent workers (defaults to instance setting)
            enable_resume: Enable resumable downloads using cache
            
        Returns:
            Dictionary mapping "building_id_upgrade" keys to prepared profile data
            
        Raises:
            ValueError: If building states cannot be determined
        """
        max_workers = max_workers or self.max_workers
        total_combinations = len(building_ids) * len(upgrades)
        
        self.logger.info(
            f"Starting efficient batch processing: {len(building_ids)} buildings × "
            f"{len(upgrades)} upgrades = {total_combinations} combinations"
        )
        
        # Validate that we have state information
        missing_states = [bid for bid in building_ids if self.get_building_state(bid) is None]
        if missing_states:
            self.logger.warning(f"{len(missing_states)} buildings missing state information")
            # Try to load state information if metadata not loaded
            if self._metadata is None:
                self.load_metadata([self.config.building_id_column, self.config.state_column])
            
            # Re-check after loading metadata
            still_missing = [bid for bid in missing_states if self.get_building_state(bid) is None]
            if still_missing:
                raise ValueError(f"Cannot determine states for buildings: {still_missing[:10]}...")
        
        # Generate all file processing tasks
        tasks = []
        for building_id in building_ids:
            state = self.get_building_state(building_id)
            for upgrade in upgrades:
                task_key = f"{building_id}_{upgrade}"
                try:
                    uri = self._get_load_profile_uri(building_id, upgrade, state)
                    tasks.append({
                        'key': task_key,
                        'building_id': building_id,
                        'upgrade': upgrade,
                        'state': state,
                        'uri': uri
                    })
                except ValueError as e:
                    self.logger.warning(f"Skipping {task_key}: {e}")
                    continue
        
        if not tasks:
            # Check if all tasks were skipped
            if total_combinations > 0:
                 self.logger.warning("No valid tasks could be created. All building/upgrade combinations might be invalid.")
                 return {} # Return empty if all combinations were invalid
            else: # No building_ids or upgrades provided
                raise ValueError("No building_ids or upgrades provided, resulting in no tasks.")

        
        # Check cache for resumable downloads
        cached_results = {}
        remaining_tasks = tasks
        
        if enable_resume and self.cache_enabled:
            cached_results, remaining_tasks = self._check_cache_for_resume(tasks)
            if cached_results:
                self.logger.info(f"Resuming: found {len(cached_results)} cached results, "
                               f"{len(remaining_tasks)} remaining")
        
        # Process remaining tasks
        if remaining_tasks:
            batch_results = self._process_tasks_batch(remaining_tasks, max_workers)
            
            # Combine cached and new results
            all_results = {**cached_results, **batch_results}
        else:
            all_results = cached_results
        
        # Save successful results to cache
        if self.cache_enabled and enable_resume:
            self._save_results_to_cache(all_results)
        
        success_count = len([r for r in all_results.values() if 'error' not in r])
        self.logger.info(f"Batch processing complete: {success_count}/{len(all_results)} successful")
        
        return all_results
    
    def _check_cache_for_resume(self, tasks: List[Dict]) -> Tuple[Dict, List[Dict]]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Only called from ``load_and_prepare_profiles_efficient()``,
            which is not called by the application.

        Check cache for existing results to enable resume functionality.
        """
        cached_results = {}
        remaining_tasks = []
        
        cache_file = self.cache_processed_dir / f"{self.dataset_type}_{self.release}_processed.json"
        
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    cache_data = json.load(f)
                
                for task in tasks:
                    task_key = task['key']
                    if task_key in cache_data:
                        # Validate cached result has required structure
                        cached_item = cache_data[task_key]
                        if 'load_profile' in cached_item or 'error' in cached_item:
                            cached_results[task_key] = cached_item
                            continue
                    
                    remaining_tasks.append(task)
                    
            except Exception as e:
                self.logger.warning(f"Error reading cache file: {e}")
                remaining_tasks = tasks
        else:
            remaining_tasks = tasks
        
        return cached_results, remaining_tasks
    
    def _save_results_to_cache(self, results: Dict[str, Dict[str, Any]]) -> None:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Only called from ``load_and_prepare_profiles_efficient()``,
            which is not called by the application.

        Save processing results to cache for resume functionality.
        """
        cache_file = self.cache_processed_dir / f"{self.dataset_type}_{self.release}_processed.json"
        
        try:
            # Load existing cache if it exists
            existing_cache = {}
            if cache_file.exists():
                with open(cache_file, 'r') as f:
                    existing_cache = json.load(f)
            
            # Update with new results (only successful or errored ones, not in-progress)
            for key, value in results.items():
                if 'load_profile' in value or 'error' in value: # Ensure it's a final state
                     existing_cache[key] = value
            
            # Save updated cache
            with open(cache_file, 'w') as f:
                json.dump(existing_cache, f, indent=2, default=str) # default=str for non-serializable types
                
            self.logger.debug(f"Updated cache with {len(results)} results")
            
        except Exception as e:
            self.logger.warning(f"Failed to save results to cache: {e}")
    
    def _process_tasks_batch(self, tasks: List[Dict], max_workers: int) -> Dict[str, Dict[str, Any]]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Only called from ``load_and_prepare_profiles_efficient()``,
            which is not called by the application.

        Process a batch of download/preparation tasks with parallel execution.
        """
        def process_single_task(task):
            """Process a single building-upgrade combination."""
            try:
                return self._download_and_prepare_single_profile(
                    task['uri'], 
                    task['building_id'], 
                    task['upgrade'], 
                    task['state']
                )
            except Exception as e:
                self.logger.warning(f"Task failed for {task['key']}: {e}")
                return task['key'], {
                    'error': str(e),
                    'building_id': task['building_id'],
                    'upgrade': task['upgrade'],
                    'state': task['state']
                }
        
        results = {}
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_task = {executor.submit(process_single_task, task): task for task in tasks}
            
            # Process completed tasks with progress bar
            with tqdm(total=len(tasks), desc="Processing profiles") as pbar:
                for future in as_completed(future_to_task):
                    try:
                        result = future.result()
                        if result:
                            key, data = result
                            results[key] = data
                    except Exception as e:
                        task = future_to_task[future]
                        self.logger.error(f"Exception processing task {task['key']}: {e}")
                        # Store error information if future.result() itself fails
                        results[task['key']] = {
                            'error': f"Unhandled exception in task processing: {str(e)}",
                            'building_id': task['building_id'],
                            'upgrade': task['upgrade'],
                            'state': task['state']
                        }
                    finally:
                        pbar.update(1)
        
        return results
    
    def _download_and_prepare_single_profile(
        self, 
        uri: str, 
        building_id: Any, 
        upgrade: int, 
        state: str
    ) -> Tuple[str, Dict[str, Any]]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Only called from ``_process_tasks_batch()``, which is
            not called by the application.

        Download and prepare a single load profile with complete processing pipeline.
        """
        task_key = f"{building_id}_{upgrade}"
        
        try:
            # Check if file exists
            if not self.fs.exists(uri):
                return task_key, {
                    'error': f'File not found: {uri}',
                    'building_id': building_id,
                    'upgrade': upgrade,
                    'state': state
                }
            
            # Read parquet file directly
            # with self.fs.open(uri, 'rb') as f:
                # df = pl.read_parquet(f)
            df = pl.scan_parquet(uri)
            
            # if df.is_empty():
            #     return task_key, {
            #         'error': 'Empty parquet file',
            #         'building_id': building_id,
            #         'upgrade': upgrade,
            #         'state': state
            #     }
            
            # Apply complete preparation pipeline
            prepared_result = self._prepare_load_profile_complete(df)
            
            # Add task-specific metadata
            prepared_result['metadata'].update({
                'building_id': building_id,
                'upgrade': upgrade,
                'state': state,
                'source_uri': uri,
                'dataset_type': self.dataset_type,
                'release': self.release
            })
            
            return task_key, {
                'building_id': building_id,
                'upgrade': upgrade,
                'state': state,
                'load_profile': prepared_result['load_profile'].tolist(),  # Convert to list for JSON serialization
                'metadata': prepared_result['metadata']
            }
            
        except Exception as e:
            return task_key, {
                'error': str(e),
                'building_id': building_id,
                'upgrade': upgrade,
                'state': state
            }

    def _prepare_load_profile_complete(
        self, 
        df: pl.LazyFrame, 
        column_name: Optional[str] = None,
        timestamp_column: str = 'timestamp'
    ) -> Dict[str, Any]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Only called from ``_download_and_prepare_single_profile()``
            and ``prepare_for_utility_bill_calculation()``, neither of which is called
            by the application.

        Complete preparation pipeline that replicates prepare_for_utility_bill_calculation
        but optimized for the known dataset structures.
        """
        # Find the appropriate load column
        load_column = column_name or self.config.default_load_column
        
        # if load_column not in df.columns:
        #     # Try to find alternative column
        #     electricity_cols = [col for col in df.columns if 'electricity' in col.lower() and 'energy_consumption' in col.lower()]
        #     if not electricity_cols: # Broader search if specific not found
        #         electricity_cols = [col for col in df.columns if 'electricity' in col.lower()]

        #     if electricity_cols:
        #         load_column = electricity_cols[0]
        #         self.logger.debug(f"Using alternative column: {load_column}")
        #     else:
        #         raise ValueError(f"Cannot find electricity load column in: {df.columns}")
        
        # # Extract timestamp if available
        # start_timestamp = None
        # if timestamp_column in df.columns:
        #     try:
        #         start_timestamp = extract_timestamp(
        #             df[timestamp_column].head(1).item(), 
        #             logger=self.logger
        #         )
        #     except Exception as e:
        #         self.logger.warning(f"Could not extract timestamp from column '{timestamp_column}': {e}. Proceeding without it.")
        #         start_timestamp = None # Ensure it's None if extraction fails

        try:
            df = (df
                  .select(pl.col([timestamp_column, load_column]))
                  .group_by_dynamic(pl.col(timestamp_column), every='1h', closed='right', label='right')
                  .agg(pl.col(load_column).sum())
                  .collect()
                )
        except Exception as e:
            raise ValueError(f"Error during profile extraction: {e}")

        start_timestamp = df.select(timestamp_column).head(1).item()
        # Extract load data as numpy array
        load_profile_np = df.select(load_column).to_series().to_numpy()
        
        # Apply the standard preparation pipeline from cl_LoadProfileUtils
        result = prepare_for_bill_calculation(
            load_profile=load_profile_np,
            start_timestamp=start_timestamp,
            interval_minutes=60,
        )
        
        # Enhance metadata with dataset-specific information
        result['metadata'].update({
            'column_used': load_column,
            'dataset_type': self.dataset_type,
            'expected_resolution_minutes': 60,
            'file_columns': df.columns
        })
        
        return result
    
    # =============================================================================
    # BACKWARD COMPATIBILITY METHODS - Same interface as ResStockDataManager
    # =============================================================================
    
    def load_building_profiles(
        self, 
        building_ids: List[Any], 
        column: Optional[str] = None,
        batch_size: int = 100, 
        parallel: bool = True, 
        max_workers: Optional[int] = None,
        upgrade: int = 0
    ) -> Dict[Any, pl.DataFrame]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Not called by the application. The app downloads
            timeseries via ``download_timeseries_with_progress()`` and reads cached
            files with ``load_cached_timeseries()``.

        Load load profiles for multiple buildings (backward compatibility method).
        
        This method maintains the same interface as ResStockDataManager for compatibility
        but uses the new efficient batch processing internally when possible.
        
        Args:
            building_ids: List of building IDs
            column: Optional load profile column to load
            batch_size: Number of buildings to process in a batch (ignored in efficient mode)
            parallel: Whether to process batches in parallel
            max_workers: Maximum number of worker threads
            upgrade: Upgrade scenario (0 for baseline)
            
        Returns:
            Dictionary mapping building IDs to load profiles as Polars DataFrames
        """
        self.logger.info(f"Loading {len(building_ids)} building profiles for upgrade {upgrade}")
        
        max_workers = max_workers or self.max_workers
        results = {}
        
        # For single upgrade, use efficient batch processing for larger batches
        if len(building_ids) > 10:  # Use efficient method for larger batches
            try:
                efficient_results = self.load_and_prepare_profiles_efficient(
                    building_ids=building_ids,
                    upgrades=[upgrade],
                    max_workers=max_workers,
                    enable_resume=True # Use resume for this potentially long operation
                )
                
                # Convert back to expected format (raw DataFrames)
                for building_id in building_ids:
                    key = f"{building_id}_{upgrade}"
                    if key in efficient_results and 'error' not in efficient_results[key]:
                        profile_data = efficient_results[key]
                        load_data = np.array(profile_data['load_profile']) # Comes as list from efficient loader
                        
                        # Determine the effective load column name
                        effective_load_column = column or profile_data['metadata'].get('column_used', self.config.default_load_column)

                        df_data = {
                            'index': list(range(len(load_data))),
                            effective_load_column: load_data
                        }
                        
                        # Add timestamp if we want to maintain compatibility
                        # Use metadata from efficient loader if available
                        meta_start_ts = profile_data['metadata'].get('start_timestamp')
                        start_date = meta_start_ts if isinstance(meta_start_ts, datetime) else datetime(2018, 1, 1) 
                        
                        # Determine time delta based on data length, assume hourly if 8760
                        if len(load_data) == HOURS_PER_YEAR:
                            time_delta_hours = 1
                        else: # Assume sub-hourly, default to expected resolution
                            time_delta_hours = self.config.expected_resolution_minutes / 60.0

                        timestamps = [start_date + timedelta(hours=i * time_delta_hours) for i in range(len(load_data))]
                        df_data['timestamp'] = timestamps
                        
                        results[building_id] = pl.DataFrame(df_data)
                    else:
                        self.logger.warning(f"Failed to load profile for building {building_id} via efficient method.")
                
                return results
                
            except Exception as e:
                self.logger.warning(f"Efficient loading for compatibility failed: {e}. Falling back to sequential method.")
        
        # Fallback to sequential processing for small batches or on error
        return self._load_building_profiles_sequential(
            building_ids, column, batch_size, parallel, max_workers, upgrade
        )
    
    def _load_building_profiles_sequential(
        self,
        building_ids: List[Any], 
        column: Optional[str] = None,
        batch_size: int = 100, # batch_size is not directly used here but kept for signature
        parallel: bool = True, 
        max_workers: Optional[int] = None,
        upgrade: int = 0
    ) -> Dict[Any, pl.DataFrame]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Only called from ``load_building_profiles()``, which
            is not called by the application.

        Sequential loading method for backward compatibility.
        """
        results = {}
        
        def load_single_profile(b_id):
            try:
                state = self.get_building_state(b_id)
                if not state:
                    self.logger.warning(f"State not found for building {b_id}, cannot load profile.")
                    return b_id, None
                
                uri = self._get_load_profile_uri(b_id, upgrade, state)
                
                if not self.fs.exists(uri):
                    self.logger.warning(f"Profile not found at URI: {uri} for building {b_id}")
                    return b_id, None
                
                with self.fs.open(uri, 'rb') as f:
                    df = pl.read_parquet(f)
                
                if df.is_empty():
                    self.logger.warning(f"Profile for building {b_id} is empty.")
                    return b_id, None
                
                # Add timestamp column for compatibility if not present
                if 'timestamp' not in df.columns:
                    # Try to infer start time, else default
                    start_date = datetime(2018, 1, 1) # Default AMY year
                    
                    # Determine time delta based on expected resolution
                    time_delta_minutes = self.config.expected_resolution_minutes
                    time_delta = timedelta(minutes=time_delta_minutes)

                    df = df.with_row_index(name="_row_nr").with_columns(
                        pl.lit(start_date) + pl.col("_row_nr") * time_delta
                    ).rename({"literal": "timestamp"}).drop("_row_nr")
                
                # Select specific column if requested, else return full df
                # This part is tricky, as the original ResStockDataManager returned full DF.
                # If 'column' is provided, it implies user wants *that* column.
                # The efficient loader might have picked a different 'default_load_column'.
                # For sequential, we assume the 'column' if provided is the primary target.
                # However, the signature is usually for the main load column.
                # Let's stick to returning the full df for now, as per original behavior.
                # If a specific column is needed, it can be selected by the caller.
                
                return b_id, df
                
            except Exception as e:
                self.logger.error(f"Error loading profile for building {b_id}: {e}")
                return b_id, None
        
        if parallel and len(building_ids) > 1:
            effective_max_workers = max_workers or min(self.max_workers, len(building_ids))
            
            with ThreadPoolExecutor(max_workers=effective_max_workers) as executor:
                future_to_id = {executor.submit(load_single_profile, bid): bid for bid in building_ids}
                
                with tqdm(total=len(building_ids), desc="Loading profiles (sequential compat)") as pbar:
                    for future in as_completed(future_to_id):
                        try:
                            b_id_res, df_res = future.result()
                            if df_res is not None:
                                results[b_id_res] = df_res
                        except Exception as e: # Should be caught by load_single_profile, but as safety
                            b_id_failed = future_to_id[future]
                            self.logger.error(f"Unhandled exception loading building {b_id_failed}: {e}")
                        finally:
                            pbar.update(1)
        else:
            # Sequential processing
            for b_id_seq in tqdm(building_ids, desc="Loading profiles (sequential compat)"):
                b_id_res_seq, df_res_seq = load_single_profile(b_id_seq)
                if df_res_seq is not None:
                    results[b_id_res_seq] = df_res_seq
        
        return results
    
    def prepare_for_utility_bill_calculation(
        self,
        load_profile_data: Union[np.ndarray, pl.DataFrame],
        column_name: Optional[str] = None,
        timestamp_column: str = 'timestamp'
    ) -> Dict[str, Any]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Only called from
            ``prepare_building_profiles_for_calculation()``, which is not called
            by the application.

        Prepare a load profile for utility bill calculation (backward compatibility).
        
        Args:
            load_profile_data: Load profile data as numpy array or Polars DataFrame
            column_name: Name of the column containing load data (if DataFrame)
            timestamp_column: Name of timestamp column (if DataFrame)

        Returns:
            Dictionary with prepared load profile and metadata
        """
        if isinstance(load_profile_data, pl.DataFrame):
            return self._prepare_load_profile_complete(
                load_profile_data, 
                column_name, 
                timestamp_column
            )
        elif isinstance(load_profile_data, np.ndarray):
            # Use the standard utility function for numpy arrays
            return prepare_for_bill_calculation(
                load_profile=load_profile_data,
                start_timestamp=None, # Cannot infer from raw numpy array here
                interval_minutes=self.config.expected_resolution_minutes  # <-- FIXED
            )
        else:
            raise TypeError(f"Unsupported input type: {type(load_profile_data)}")
    
    def prepare_building_profiles_for_calculation(
        self,
        building_profiles: Dict[Any, Union[pl.DataFrame, np.ndarray]],
        column_name: Optional[str] = None,
        timestamp_column: str = 'timestamp'
    ) -> Dict[Any, Dict[str, Any]]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Only called from ``load_and_prepare_profiles()``,
            which is not called by the application.

        Prepare multiple building profiles for utility bill calculation (backward compatibility).
        
        Args:
            building_profiles: Dictionary mapping building IDs to load profiles
            column_name: Name of the column containing load data (if DataFrame)
            timestamp_column: Name of timestamp column (if DataFrame)

        Returns:
            Dictionary mapping building IDs to prepared profile data
        """
        prepared_data = {}
        failed_count = 0

        for bldg_id, profile in tqdm(building_profiles.items(), desc="Preparing profiles (compat)"):
            try:
                prepared = self.prepare_for_utility_bill_calculation(
                    profile,
                    column_name=column_name,
                    timestamp_column=timestamp_column
                )

                # Add building ID to metadata
                if 'metadata' not in prepared:
                    prepared['metadata'] = {}
                prepared['metadata']['building_id'] = bldg_id # Ensure building_id is in metadata
                prepared_data[bldg_id] = prepared

            except Exception as e:
                self.logger.error(f"Error preparing profile for building {bldg_id}: {str(e)}")
                failed_count += 1

        success_count = len(prepared_data)
        self.logger.info(f"Prepared {success_count} profiles, {failed_count} failed")
        
        return prepared_data
    
    def load_and_prepare_profiles(
        self,
        building_ids: List[Any],
        column: Optional[str] = None, # column often refers to the target load column
        batch_size: int = 100,
        parallel: bool = True,
        max_workers: Optional[int] = None,
        upgrade: int = 0
    ) -> Dict[Any, Dict[str, Any]]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Not called by the application. The app uses
            ``download_timeseries_with_progress()`` and ``load_cached_timeseries()``
            for data acquisition instead of this combined pipeline.

        Load and prepare profiles for multiple buildings (backward compatibility).
        
        This method combines load_building_profiles and prepare_building_profiles_for_calculation
        but uses efficient batch processing when possible.
        
        Args:
            building_ids: List of building IDs
            column: Optional load profile column to load (primarily for fallback sequential)
            batch_size: Number of buildings to process in a batch
            parallel: Whether to process batches in parallel
            max_workers: Maximum number of worker threads
            upgrade: Upgrade scenario (0 for baseline)
            
        Returns:
            Dictionary mapping building IDs to prepared profile data
        """
        self.logger.info(f"Loading and preparing profiles for {len(building_ids)} buildings (compat)")
        
        # Use efficient method directly
        if len(building_ids) > 5 or not column: # Use efficient method for reasonable batch sizes or if column not critical
            try:
                efficient_results = self.load_and_prepare_profiles_efficient(
                    building_ids=building_ids,
                    upgrades=[upgrade],
                    max_workers=max_workers or self.max_workers,
                    enable_resume=True
                )
                
                # Filter out failed results and format for compatibility
                prepared_data = {}
                for building_id in building_ids:
                    key = f"{building_id}_{upgrade}"
                    if key in efficient_results and 'error' not in efficient_results[key]:
                        result = efficient_results[key]
                        # Ensure 'building_id' is in metadata for downstream consistency
                        result['metadata']['building_id'] = building_id 
                        prepared_data[building_id] = {
                            'load_profile': np.array(result['load_profile']), # Ensure numpy array
                            'metadata': result['metadata']
                        }
                    elif key in efficient_results and 'error' in efficient_results[key]:
                         self.logger.warning(f"Efficient method failed for {key}: {efficient_results[key]['error']}")
                    else:
                        self.logger.warning(f"Result for {key} not found in efficient_results.")

                if prepared_data or not building_ids: # Return if successful or no IDs to process
                    return prepared_data
                else: # If all failed in efficient mode, log and fall back
                    self.logger.warning("Efficient method yielded no successful results. Falling back.")
                
            except Exception as e:
                self.logger.warning(f"Efficient load_and_prepare_profiles method failed: {e}. Using fallback.")
        
        # Fallback to original two-step process (less efficient)
        self.logger.info("Using fallback two-step loading and preparation.")
        profiles = self.load_building_profiles( # This itself might use efficient path for loading raw DFs
            building_ids=building_ids,
            column=column,
            batch_size=batch_size,
            parallel=parallel,
            max_workers=max_workers,
            upgrade=upgrade
        )
        
        if not profiles:
            self.logger.warning("Fallback loading step yielded no profiles.")
            return {}

        prepared_data = self.prepare_building_profiles_for_calculation(
            building_profiles=profiles,
            column_name=column # Pass column_name here for preparation step
        )
        
        return prepared_data
    
    # =============================================================================
    # UTILITY METHODS
    # =============================================================================
    
    def get_dataset_info(self) -> Dict[str, Any]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Not called by the application.

        Get comprehensive information about the current dataset configuration.
        
        Returns:
            Dictionary with dataset information and statistics
        """
        info = {
            'dataset_type': self.dataset_type,
            'release': self.release,
            'config': {
                'base_uri': self.config.base_uri,
                'default_load_column': self.config.default_load_column,
                'supported_upgrades': self.config.supported_upgrades,
                'expected_resolution_minutes': self.config.expected_resolution_minutes
            },
            'cache_enabled': self.cache_enabled,
            'max_workers': self.max_workers
        }
        
        # Add metadata statistics if loaded
        if self._metadata is not None:
            info['metadata_stats'] = {
                'total_buildings': len(self._metadata),
                'columns_available': len(self._metadata.columns),
                'states_represented': (len(self._metadata[self.config.state_column].unique()) 
                                       if self.config.state_column in self._metadata.columns and not self._metadata.is_empty() 
                                       else 0)
            }
        
        if self._filtered_metadata is not None:
            info['filtered_stats'] = {
                'filtered_buildings': len(self._filtered_metadata)
            }
        
        return info
    
    def clear_cache(self, cache_type: str = 'all') -> None:
        """
        Clear cached data.
        
        Args:
            cache_type: Type of cache to clear ('metadata', 'timeseries', 'processed', 'all')
        """
        if not self.cache_enabled:
            self.logger.warning("Cache is not enabled")
            return
        
        import shutil
        
        try:
            if cache_type in ['metadata', 'all']:
                if hasattr(self, 'cache_metadata_dir') and self.cache_metadata_dir.exists():
                    shutil.rmtree(self.cache_metadata_dir)
                    self.cache_metadata_dir.mkdir(parents=True, exist_ok=True)
                    self.logger.info("Cleared metadata cache")
            
            if cache_type in ['timeseries', 'all']:
                if hasattr(self, 'cache_timeseries_dir') and self.cache_timeseries_dir.exists():
                    shutil.rmtree(self.cache_timeseries_dir)
                    self.cache_timeseries_dir.mkdir(parents=True, exist_ok=True)
                    self.logger.info("Cleared timeseries cache")
            
            if cache_type in ['processed', 'all']:
                if hasattr(self, 'cache_processed_dir') and self.cache_processed_dir.exists():
                    shutil.rmtree(self.cache_processed_dir)
                    self.cache_processed_dir.mkdir(parents=True, exist_ok=True)
                    self.logger.info("Cleared processed cache")
                    
        except Exception as e:
            self.logger.error(f"Error clearing cache: {e}")
    
    def validate_building_ids(self, building_ids: List[Any]) -> Dict[str, List[Any]]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Not called by the application.

        Validate that building IDs exist in the dataset and have state information.
        
        Args:
            building_ids: List of building IDs to validate
            
        Returns:
            Dictionary with 'valid', 'invalid', and 'missing_state' lists
        """
        if self._metadata is None:
            self.load_metadata([self.config.building_id_column, self.config.state_column])
        
        if self._metadata is None or self._metadata.is_empty(): # Check if metadata loading failed or was empty
            self.logger.warning("Metadata is not loaded or empty. Cannot validate building IDs.")
            return {'valid': [], 'invalid': building_ids, 'missing_state': []}

        available_ids = set(self._metadata[self.config.building_id_column].to_list())
        
        valid = []
        invalid = []
        missing_state = []
        
        for bid in building_ids:
            if bid not in available_ids:
                invalid.append(bid)
            elif not self.get_building_state(bid): # State might be missing even if ID exists
                missing_state.append(bid)
            else:
                valid.append(bid)
        
        return {
            'valid': valid,
            'invalid': invalid, 
            'missing_state': missing_state
        }
    
    def __repr__(self) -> str:
        """String representation of the BuildStock data manager."""
        return (f"BuildStockDataManager(dataset_type='{self.dataset_type}', "
                f"release='{self.release}', cache_enabled={self.cache_enabled})")


# =============================================================================
# CONVENIENCE FUNCTIONS FOR BACKWARD COMPATIBILITY
# =============================================================================

class ResStockDataManager(BuildStockDataManager):
    """
    Backward compatibility class that inherits from BuildStockDataManager.
    
    This allows existing code using ResStockDataManager to work without changes
    while benefiting from the new efficient processing capabilities.
    """
    
    def __init__(self, metadata_uri: Optional[str] = None, log_file: Optional[Path] = None, **kwargs):
        """Initialize with ResStock defaults."""
        super().__init__(
            dataset_type='resstock',
            release='2024_release_2', # Default release for ResStock
            metadata_uri=metadata_uri,
            log_file=log_file,
            **kwargs
        )
        
        # Set attributes for backward compatibility expected by older code
        self.BASE_URI = self.config.base_uri
        self.BUILDING_ID_COLUMN = self.config.building_id_column
        self.DEFAULT_LOAD_COLUMN = self.config.default_load_column
