# Utility functions for handling load profiles
# This module contains common utilities for processing and validating
# electricity load profiles, particularly for use with PySAM and
# utility bill calculations

import numpy as np
from datetime import datetime
from typing import Optional, Tuple, Dict, Any
import logging

logger = logging.getLogger(__name__)

# Constants used across the codebase
HOURS_PER_YEAR = 8760
DEFAULT_RESOLUTION_MINUTES = 60
SUPPORTED_RESOLUTIONS = [15, 30, 60]


def validate_load_profile(
    load_profile: np.ndarray,
    expected_length: int = HOURS_PER_YEAR,
    # allow_truncate: bool = False, # REMOVED - now always strict
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Validate load profile data format and content. Raises ValueError on length mismatch.

    Args:
        load_profile: Numpy array containing load data
        expected_length: Expected length of the profile (default: 8760 hours)

    Returns:
        Tuple of (validated_profile, validation_info)

    Raises:
        ValueError: If load profile length does not match expected_length.
    """
    validation_info = {
        "original_length": len(load_profile),
        "was_modified": False,  # Modification for NaN/negative is still possible
        "validation_warnings": [],
        "validation_errors": [],  # Added for explicit errors
    }

    # Check for NaN values
    nan_count = np.isnan(load_profile).sum()
    if nan_count > 0:
        warning_msg = f"Profile contains {nan_count} NaN values. Replacing with 0.0."
        validation_info["validation_warnings"].append(warning_msg)
        if logger:
            logger.warning(warning_msg)

        # Replace NaN values with zeros
        load_profile = np.nan_to_num(load_profile, nan=0.0)
        validation_info["was_modified"] = True
        validation_info["nan_replaced"] = nan_count

    # Check for negative values
    negative_count = (load_profile < 0).sum()
    if negative_count > 0:
        warning_msg = f"Profile contains {negative_count} negative values."
        validation_info["validation_warnings"].append(warning_msg)
        if logger:
            logger.warning(
                warning_msg
            )  # Keep as warning, calculation might handle negatives differently

    # Handle incorrect length - NOW RAISES ERROR
    if len(load_profile) != expected_length:
        error_msg = f"Load profile length is {len(load_profile)}, but exactly {expected_length} is required."
        validation_info["validation_errors"].append(error_msg)
        if logger:
            logger.error(error_msg)
        raise ValueError(error_msg)  # Raise error instead of modifying

    validation_info["final_length"] = len(load_profile)
    return load_profile, validation_info


def ensure_8760_hours(
    load_profile: np.ndarray,
) -> np.ndarray:
    """
    Ensure load profile has exactly 8760 hours. Raises ValueError if not.

    Args:
        load_profile: Numpy array with load data

    Returns:
        Numpy array with exactly 8760 hours

    Raises:
        ValueError: If the input load profile does not have exactly 8760 hours.
    """
    # Directly use the strict validation function
    # It will raise ValueError if the length is not HOURS_PER_YEAR
    validated_profile, validation_info = validate_load_profile(
        load_profile,
        expected_length=HOURS_PER_YEAR,
    )
    if validation_info["validation_errors"]:
        # Should have already raised, but double-check
        raise ValueError(validation_info["validation_errors"][0])

    # Log if NaNs were replaced, but length error is handled by raising
    if validation_info["was_modified"] and "nan_replaced" in validation_info:
        logger.warning(
            f"Profile modified during validation: {validation_info['nan_replaced']} NaN values replaced with 0."
        )

    return validated_profile


def align_to_monday_start(
    load_profile: np.ndarray,
    start_timestamp: Optional[datetime] = None,
) -> Tuple[np.ndarray, int]:
    """
    Align timeseries data to start on Monday for PySAM compatibility.

    Args:
        load_profile: Hourly timeseries data
        start_timestamp: Datetime of the first hour in the timeseries

    Returns:
        tuple: (aligned_data, hours_shifted)
    """
    # Default to Monday if no timestamp provided
    if start_timestamp is None:
        logger.warning("No timestamp provided, assuming data already starts on Monday")
        return load_profile, 0

    # Get day of week (0=Monday in datetime.weekday())
    start_day = start_timestamp.weekday()

    if start_day == 0:  # Already starts on Monday
        logger.debug("Timeseries already starts on Monday, no alignment needed")
        return load_profile, 0

    # Calculate hours to shift (each day = 24 hours)
    hours_to_shift = start_day * 24
    logger.info(f"Shifting timeseries by {hours_to_shift} hours to start on Monday")

    # Ensure data is exactly 8760 hours for alignment
    load_profile = ensure_8760_hours(load_profile)

    # Rotate the data to start on Monday
    aligned_data = np.roll(load_profile, -hours_to_shift)

    return aligned_data, hours_to_shift


def convert_to_hourly(
    data: np.ndarray,
    interval_minutes: int = 15,
) -> np.ndarray:
    """
    Convert sub-hourly data to hourly using aggregation.

    Args:
        data: Numpy array with load data at sub-hourly resolution
        interval_minutes: Time interval of the data in minutes

    Returns:
        Numpy array with hourly values
    """
    if interval_minutes == 60:
        return data

    if interval_minutes not in SUPPORTED_RESOLUTIONS:
        logger.warning(
            f"Unsupported resolution: {interval_minutes} minutes. Supported: {SUPPORTED_RESOLUTIONS}"
        )

    points_per_hour = int(60 / interval_minutes)
    n_rows = len(data)

    # Calculate complete hours
    n_hours = n_rows // points_per_hour
    if n_rows % points_per_hour != 0:
        remainder = n_rows % points_per_hour
        logger.warning(f"Truncating {remainder} points to ensure complete hours")

    # Reshape data to aggregate by hour
    reshaped_data = data[: n_hours * points_per_hour].reshape(n_hours, points_per_hour)
    hourly_data = reshaped_data.sum(axis=1)

    logger.debug(
        f"Converted {n_rows} points at {interval_minutes}-minute resolution to {len(hourly_data)} hourly points"
    )

    return hourly_data


def extract_timestamp(
    timestamp_data: Any,
) -> Optional[datetime]:
    """
    Extract and process a timestamp from various formats.
    Usually the ComStock and ResStock timestamp should be consistent
    but this enables a more general approach

    Args:
        timestamp_data: Timestamp in various formats (string, int, datetime object)

    Returns:
        datetime object or None if not available/parsable
    """
    if timestamp_data is None:
        return None

    start_timestamp = None

    try:
        # Handle different timestamp formats
        if isinstance(timestamp_data, datetime):
            start_timestamp = timestamp_data
        elif isinstance(timestamp_data, str):
            # Try common formats
            try:
                start_timestamp = datetime.fromisoformat(
                    timestamp_data.replace("Z", "+00:00")
                )
            except ValueError:
                try:
                    # Try with dateutil parser as fallback
                    from dateutil import parser

                    start_timestamp = parser.parse(timestamp_data)
                except Exception as e:
                    logger.warning(
                        f"Failed to parse timestamp: {timestamp_data}. Error: {str(e)}"
                    )
                    return None
        elif isinstance(timestamp_data, (int, float)):
            # Assume unix timestamp
            start_timestamp = datetime.fromtimestamp(timestamp_data)

        if start_timestamp and (
            start_timestamp.minute != 0 or start_timestamp.second != 0
        ):
            # Round down to the nearest hour
            start_timestamp = start_timestamp.replace(minute=0, second=0, microsecond=0)
            logger.debug(f"Adjusted to hourly timestamp: {start_timestamp}")

        return start_timestamp

    except Exception as e:
        logger.warning(f"Error extracting timestamp: {str(e)}")
        return None


def prepare_for_bill_calculation(
    load_profile: np.ndarray,
    start_timestamp: Optional[datetime] = None,
    interval_minutes: int = 15,
) -> Dict[str, Any]:
    """
    Prepare a load profile for utility bill calculation.

    This function performs necessary processing steps:
    1. Convert sub-hourly data to hourly
    2. Ensure exactly 8760 hours (raises ValueError otherwise)
    3. Align to start on Monday (PySAM bill calculator needs that)

    Args:
        load_profile: Load profile data (numpy array)
        start_timestamp: Optional timestamp of first data point
        interval_minutes: Time interval of input data in minutes (default: 15)

    Returns:
        Dictionary with prepared profile and metadata
    """
    # Ensure numpy array
    if not isinstance(load_profile, np.ndarray):
        load_profile = np.array(load_profile)

    # Use the provided interval_minutes parameter
    original_length = len(load_profile)
    expected_length = (
        HOURS_PER_YEAR * (60 // interval_minutes)
        if interval_minutes < 60
        else HOURS_PER_YEAR
    )

    # Convert to hourly (assuming 15-min input)
    # logger.info(f"Converting {interval_minutes}-minute data (length {original_length}) to hourly.")
    load_profile_hourly = convert_to_hourly(load_profile, interval_minutes)

    # Ensure 8760 hours AFTER conversion (raises ValueError if conversion failed)
    load_profile_8760 = ensure_8760_hours(load_profile_hourly)

    # Align to Monday
    aligned_profile, hours_shifted = align_to_monday_start(
        load_profile_8760, start_timestamp
    )

    # Prepare result
    result = {
        "load_profile": aligned_profile,
        "metadata": {
            "original_length": original_length,
            "original_resolution": interval_minutes,
            "converted_to_hourly": True,
            "final_length": len(aligned_profile),
            "hours_shifted": hours_shifted,
            "monday_aligned": hours_shifted == 0 or start_timestamp is None,
            "preparation_time": datetime.now().isoformat(),
        },
    }

    return result
