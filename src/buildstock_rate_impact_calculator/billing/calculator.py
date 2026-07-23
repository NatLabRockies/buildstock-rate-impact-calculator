# cl_UtilityBillCalculator.py

# Classes to calculate utility bills using URDB data schemas
# and utilizing PySAM to calculate the bills

import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
import numpy as np
import datetime
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import threading
import polars as pl

# PySAM specific imports
import PySAM.Utilityrate5 as ur5
import PySAM.UtilityRateTools  # For URDB conversion
import PySAM.LoadTools  # For monthly peak calculation

from buildstock_rate_impact_calculator.rates.urdb_client import URDBClient, RateAnalyzer
from buildstock_rate_impact_calculator.rates.rate_builder import UrdbRateBuilder
from dotenv import load_dotenv
from buildstock_rate_impact_calculator.core.logger_config import (
    setup_logging,
    LogMessages,
)
from buildstock_rate_impact_calculator.billing.load_profile_utils import (
    validate_load_profile,
    HOURS_PER_YEAR,
)

# Load environment variables for access to URDB
load_dotenv()

logger = logging.getLogger(__name__)


class UtilityBillCalculator:
    """
    Calculates utility bills using PySAM's Utilityrate5 module.

    This class integrates with URDB rate structures, typically fetched via `URDBClient`
    or constructed using `UrdbRateBuilder`. It leverages PySAM's internal tools
    for converting URDB data and calculating bills based on various rate components
    like fixed charges, tiered energy/demand rates, Time-of-Use (TOU) periods,
    and minimum charges.

    It requires pre-processed hourly load profiles (8760 data points,
    Monday-aligned) for accurate annual bill calculations.
    """

    DEFAULT_LOAD_COLUMN = "out.electricity.total.energy_consumption"

    def __init__(
        self, load_column: Optional[str] = None, log_file: Optional[Path] = None
    ):
        """
        Initialize the UtilityBillCalculator.

        Args:
            load_column (Optional[str]): An informational identifier for the typical source
                                         column of electricity load data when reading from
                                         files/sources that specify columns. Defaults to
                                         'out.electricity.total.energy_consumption'.
            log_file (Optional[Path]): Path to a file for logging. If None, logs to console.

        Raises:
            ValueError: If URDB rate lookup is attempted via fetch_rate() but
                        OPENEI_API_KEY is not configured in the environment.
        """
        self.logger = setup_logging(
            log_file=log_file, log_level=logging.DEBUG, module_name="utility_calculator"
        )
        self.logger = logging.getLogger(__name__)
        self.load_column = load_column or self.DEFAULT_LOAD_COLUMN
        self.logger.debug(
            f"Calculator expects load data typically from column: {self.load_column}"
        )

        self.urdb_api_key = os.getenv("OPENEI_API_KEY")
        self.logger.debug("Initializing PySAM Utilityrate5 model.")
        self.rate_model = ur5.new()
        if self.urdb_api_key:
            self.urdb_client = URDBClient(self.urdb_api_key)
        else:
            self.urdb_client = None
            self.logger.debug(
                "OPENEI_API_KEY not set; URDB rate lookup will not be available."
            )
        self._setup_required_parameters()

    def _setup_required_parameters(self):
        """
        Sets up essential base parameters for the PySAM Utilityrate5 model.
        These are typically for a single-year analysis without generation or escalation.
        """
        self.logger.debug(
            "Setting up base PySAM parameters for a single-year, load-only analysis."
        )
        try:
            # Lifetime parameters for a single-year analysis
            self.rate_model.Lifetime.analysis_period = 1
            self.rate_model.Lifetime.system_use_lifetime_output = (
                0  # 0 for 8760 load/gen, 1 for lifetime array
            )

            # Inflation and degradation are 0 for a current-year bill calculation
            self.rate_model.Lifetime.inflation_rate = 0.0
            self.rate_model.SystemOutput.degradation = [
                0.0
            ]  # No degradation for load-only analysis

            # SystemOutput parameters assuming no on-site generation
            self.rate_model.SystemOutput.gen = [0.0] * HOURS_PER_YEAR

            # Default metering option (Net Metering is often a safe default for load-only,
            # but URDBv8_to_ElectricityRates might override this if specified in the rate)
            self.rate_model.ElectricityRates.ur_metering_option = 0  # 0: Net Metering
            self.logger.debug("Base PySAM parameters set successfully.")
        except Exception as e:
            self.logger.error(
                LogMessages.CONFIG_ERROR.format(
                    f"PySAM base parameter setup failed: {str(e)}"
                ),
                exc_info=True,
            )
            raise RuntimeError(f"Failed to set up base PySAM parameters: {e}") from e

    def _validate_load_data(self, load_profile: np.ndarray) -> np.ndarray:
        """
        Validates the load profile, ensuring it's a numpy array with 8760 data points.
        NaN values are replaced with 0.0. Warnings are logged for modifications.

        Args:
            load_profile (np.ndarray): Input load profile data.

        Returns:
            np.ndarray: Validated (NaN-corrected) load profile as a numpy array.

        Raises:
            ValueError: If load profile length is not exactly 8760 after basic checks.
        """
        self.logger.debug(
            f"Validating load profile. Expected length: {HOURS_PER_YEAR} points."
        )

        # Use the utility function which raises ValueError on length mismatch
        # and handles NaN replacement.
        validated_profile, validation_info = validate_load_profile(
            load_profile,
            expected_length=HOURS_PER_YEAR,
        )

        for warning_msg in validation_info["validation_warnings"]:
            self.logger.warning(f"Load Profile Validation Warning: {warning_msg}")
        if validation_info[
            "validation_errors"
        ]:  # Should have already raised from validate_load_profile
            error_msg = "; ".join(validation_info["validation_errors"])
            self.logger.error(
                LogMessages.VALIDATION_ERROR.format(f"Load profile: {error_msg}")
            )
            raise ValueError(f"Load profile validation failed: {error_msg}")

        self.logger.debug("Load profile validation successful.")
        return validated_profile

    def fetch_rate(
        self,
        location: Optional[Tuple[float, float]] = None,
        utility_name: Optional[str] = None,
        sector: str = "Residential",
    ) -> Dict[str, Any]:
        """
        Fetches the highest priority, applicable, current utility rate from URDB.

        This method utilizes the `URDBClient` to find rates, which prioritizes
        NREL-vetted (approved) rates and then sorts by the newest effective date.
        It returns the single, top-priority rate found for the given criteria.

        Args:
            location (Optional[Tuple[float, float]]): Latitude and longitude coordinates.
            utility_name (Optional[str]): Name of the utility company.
            sector (str): The rate sector (e.g., "Residential", "Commercial", "Industrial").
                          Defaults to "Residential".

        Returns:
            Dict[str, Any]: A dictionary containing the rate data for the selected
                            top-priority utility rate.

        Raises:
            ValueError: If neither `location` nor `utility_name` is provided, if
                        no applicable current rates are found for the specified criteria,
                        or if OPENEI_API_KEY is not configured.
            Exception: For underlying API request errors or other issues during fetching
                       from the `URDBClient`.
        """
        if not self.urdb_api_key or self.urdb_client is None:
            err_msg = (
                "OPENEI_API_KEY is not configured. Set the OPENEI_API_KEY environment "
                "variable to enable URDB rate lookup."
            )
            self.logger.error(err_msg)
            raise ValueError(err_msg)

        search_criteria_log_msg = ""
        prioritized_rates: List[Dict[str, Any]] = []

        if location:
            lat, lon = location
            search_criteria_log_msg = f"location=({lat},{lon}), sector='{sector}'"
            self.logger.info(
                LogMessages.RATE_FETCH_START.format(search_criteria_log_msg)
            )
            try:
                prioritized_rates = self.urdb_client.get_applicable_rates_for_location(
                    lat=lat, lon=lon, sector=sector
                )
            except Exception as e:
                self.logger.error(
                    f"Error fetching applicable rates for {search_criteria_log_msg}: {str(e)}",
                    exc_info=True,
                )
                raise RuntimeError(
                    f"Failed to fetch applicable rate data for location: {e}"
                ) from e
        elif utility_name:
            search_criteria_log_msg = f"utility='{utility_name}', sector='{sector}'"
            self.logger.info(
                LogMessages.RATE_FETCH_START.format(search_criteria_log_msg)
            )
            try:
                prioritized_rates = self.urdb_client.get_applicable_rates_for_utility(
                    utility_name=utility_name, sector=sector
                )
            except Exception as e:
                self.logger.error(
                    f"Error fetching applicable rates for {search_criteria_log_msg}: {str(e)}",
                    exc_info=True,
                )
                raise RuntimeError(
                    f"Failed to fetch applicable rate data for utility: {e}"
                ) from e
        else:
            err_msg = (
                "Rate fetching requires either a location (lat, lon) or a utility_name."
            )
            self.logger.error(err_msg)
            raise ValueError(err_msg)

        if not prioritized_rates:
            err_msg = (
                f"No applicable current rates found for {search_criteria_log_msg}."
            )
            self.logger.error(err_msg)
            raise ValueError(err_msg)

        selected_rate = prioritized_rates[0]  # The first rate is the highest priority

        rate_label = selected_rate.get("label", "N/A")
        rate_name = selected_rate.get("name", "N/A")
        rate_utility = selected_rate.get("utility", "N/A")
        is_approved = selected_rate.get("approved", False)
        start_date_ts = selected_rate.get("startdate")
        start_date_log = str(start_date_ts)
        if isinstance(start_date_ts, (int, float)):
            try:
                parsed_start_dt = RateAnalyzer._parse_urdb_date(start_date_ts)
                if parsed_start_dt:
                    start_date_log = parsed_start_dt.strftime("%Y-%m-%d")
            except Exception:
                pass  # Ignore parsing errors for logging only

        log_details = (
            f"Rate Label: {rate_label} (Name: '{rate_name}', Utility: '{rate_utility}'), "
            f"Approved: {is_approved}, StartDate: {start_date_log}."
        )
        if len(prioritized_rates) > 1:
            log_details += (
                f" (Selected top from {len(prioritized_rates)} applicable rates found)."
            )

        self.logger.info(LogMessages.RATE_FETCH_COMPLETE.format(log_details))
        self.logger.debug(
            f"Full selected rate data (first 200 chars): {str(selected_rate)[:200]}..."
        )
        return selected_rate

    def setup_rate_structure(self, rate_data: Dict[str, Any]) -> None:
        """
        Sets up the PySAM Utilityrate5 model using a URDB-compatible rate data dictionary.

        This method leverages `PySAM.UtilityRateTools.URDBv8_to_ElectricityRates`
        to convert the input `rate_data` (expected to be in URDB JSON format)
        into a dictionary of parameters that can be directly assigned to the PySAM model.
        This is the recommended and most robust way to load URDB rates into PySAM.

        Args:
            rate_data (Dict[str, Any]): A dictionary containing the utility rate
                                        structure, conforming to the URDB schema. This
                                        can be raw data from the URDB API or constructed
                                        by tools like `UrdbRateBuilder`.

        Raises:
            ValueError: If `rate_data` is not a dictionary or if critical errors occur
                        during the conversion or assignment process (e.g., missing keys
                        that the PySAM tool expects, or invalid value types).
            RuntimeError: For unexpected errors during the setup that are not related
                          to data validation.
        """
        rate_identifier = rate_data.get("label", rate_data.get("name", "Unknown Rate"))
        self.logger.info(
            f"Setting up PySAM rate structure for: '{rate_identifier}' using PySAM's URDBv8 conversion tool."
        )
        self.logger.debug(f"Input rate_data keys: {list(rate_data.keys())}")

        if not isinstance(rate_data, dict):
            self.logger.error(
                "Invalid rate_data provided for setup; expected a dictionary."
            )
            raise ValueError("Invalid rate_data: Must be a dictionary.")

        try:
            # Convert via UrdbRateBuilder so that build()'s cleanup pipeline runs
            # (strips unused demand fields, removes None/internal keys, etc.) before
            # calling URDBv8_to_ElectricityRates.
            pysam_params = UrdbRateBuilder.prepare_for_pysam(rate_data)
            self.logger.debug(
                f"prepare_for_pysam generated {len(pysam_params)} PySAM parameters."
            )

            # # Assign the converted parameters to the PySAM Utilityrate5 model
            # for key, value in pysam_params.items():
            #     try:
            #         self.rate_model.value(key, value)
            #         # Example of more verbose logging if needed:
            #         # value_repr = str(value)
            #         # if len(value_repr) > 100: value_repr = value_repr[:100] + "..."
            #         # self.logger.debug(f"  Assigned PySAM parameter: {key} = {value_repr}")
            #     except Exception as assign_err:
            #         # Log warnings for individual assignment errors but continue if possible,
            #         # as some parameters might be optional or have defaults.
            #         self.logger.warning(
            #             f"Could not assign PySAM parameter '{key}' from URDB conversion. "
            #             f"Value (first 100 chars): '{str(value)[:100]}...'. Error: {assign_err}"
            #         )

            try:  # Use replace method to assign all parameters at once
                self.rate_model.ElectricityRates.replace(pysam_params)
            except Exception as replace_err:
                self.logger.error(
                    f"Failed to replace PySAM ElectricityRates parameters: {replace_err}",
                    exc_info=True,
                )
                raise ValueError(
                    f"Failed to assign rate parameters to PySAM model: {replace_err}"
                ) from replace_err

            # Log key parameters for verification after setup
            self.logger.debug(
                f"Verification - Monthly Fixed Charge: ${self.rate_model.ElectricityRates.ur_monthly_fixed_charge:.2f}"
            )
            self.logger.debug(
                f"Verification - Energy TOU Matrix (first 5 rows): {self.rate_model.ElectricityRates.ur_ec_tou_mat[:5]}"
            )
            if self.rate_model.ElectricityRates.ur_dc_enable:
                self.logger.debug("Verification - Demand Charges: ENABLED")
                self.logger.debug(
                    f"Verification - Demand TOU Matrix (first 5 rows): {self.rate_model.ElectricityRates.ur_dc_tou_mat[:5]}"
                )
            else:
                self.logger.debug("Verification - Demand Charges: DISABLED")

            self.logger.info(
                f"PySAM rate structure setup complete for '{rate_identifier}'."
            )

        except KeyError as ke:
            msg = f"KeyError during URDBv8 conversion for rate '{rate_identifier}': Missing expected key '{ke}' in rate_data or tool output."
            self.logger.error(msg, exc_info=True)
            raise ValueError(
                f"Invalid rate data structure for URDBv8 conversion: {msg}"
            ) from ke
        except ValueError as ve:
            msg = f"ValueError during URDBv8 conversion or assignment for rate '{rate_identifier}': {ve}."
            self.logger.error(msg, exc_info=True)
            raise ValueError(
                f"Invalid value in rate data for URDBv8 conversion: {msg}"
            ) from ve
        except Exception as e:
            msg = f"Unexpected error during rate structure setup for '{rate_identifier}': {str(e)}."
            self.logger.error(msg, exc_info=True)
            raise RuntimeError(f"Failed to set up rate structure: {msg}") from e

    def calculate_bill_from_prepared_profile(
        self, load_profile: np.ndarray, rate_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Calculates the annual utility bill for a given prepared load profile.

        The load profile must be a numpy array of 8760 hourly values, aligned
        to start on a Monday. If `rate_data` is provided, the PySAM model is
        configured with this new rate structure before calculation; otherwise,
        the existing rate structure in the model is used.

        Args:
            load_profile (np.ndarray): An 8760-hour, Monday-aligned load profile.
            rate_data (Optional[Dict[str, Any]]): If provided, this URDB-compatible
                                                  rate structure is set up in the model.

        Returns:
            Dict[str, Any]: A dictionary containing calculated bill components:
                            - 'annual_bill': Total annual bill ($).
                            - 'annual_energy_kwh': Total annual energy consumption (kWh).
                            - 'average_rate_ct_kwh': Average rate (cents/kWh).
                            - 'monthly_bills': List of 12 monthly bills ($).
                            - 'monthly_energy_kwh': List of 12 monthly energy consumptions (kWh).
                            - 'monthly_peak_demand_kw': List of 12 monthly peak demands (kW).
                            - 'annual_peak_demand_kw': Highest monthly peak demand (kW).
                            - And other monthly charge breakdowns (fixed, energy, demand, min).
                            - '_metadata': Dictionary with calculation timestamp and rate info.

        Raises:
            ValueError: If load profile validation fails, or if no rate structure is
                        provided and none is configured in the model.
            RuntimeError: For PySAM execution errors or issues processing results.
                          Includes parsed PySAM error messages where possible.
        """
        # self.logger.info(LogMessages.CALCULATION_START)
        calc_metadata = {}

        try:
            # 1. Validate the input load profile
            validated_load = self._validate_load_data(load_profile)
            calc_metadata["load_profile_sum_kwh"] = float(np.sum(validated_load))

            # 2. Set up the rate structure if new data is provided
            if rate_data is not None:
                self.setup_rate_structure(rate_data)  # Uses the refactored method
                calc_metadata["rate_label_used"] = rate_data.get("label", "N/A")
                calc_metadata["rate_name_used"] = rate_data.get("name", "N/A")
            else:  # Using previously set rate
                # Try to get identifier from current model state if possible (might not be directly stored)
                calc_metadata["rate_label_used"] = "Previously Configured"
                calc_metadata["rate_name_used"] = "Previously Configured"

            # 3. Check if a rate structure is effectively configured
            # A basic check: if no energy rates AND no fixed charge AND demand is disabled,
            # it's likely unconfigured or a $0 rate.
            no_energy_rates = (
                not self.rate_model.ElectricityRates.ur_ec_tou_mat
                and not self.rate_model.ElectricityRates.ur_ec_flat_mat
            )
            no_fixed_charge = (
                self.rate_model.ElectricityRates.ur_monthly_fixed_charge == 0.0
            )
            demand_disabled = self.rate_model.ElectricityRates.ur_dc_enable == 0

            if no_energy_rates and no_fixed_charge and demand_disabled:
                if (
                    rate_data is None
                ):  # Only error if no *new* rate was provided to set up
                    msg = "No rate data provided and no valid rate structure previously set up in the model."
                    self.logger.error(msg)
                    raise ValueError(msg)
                else:  # If rate_data was provided but resulted in $0 structure, log it.
                    self.logger.warning(
                        f"Provided rate_data for '{calc_metadata['rate_label_used']}' "
                        "resulted in a structure with no energy, fixed, or demand charges."
                    )

            # 4. Assign the validated load profile to the PySAM model
            # PySAM's `load` attribute expects a Python list.
            self.rate_model.Load.load = validated_load.tolist()
            self.logger.debug(
                f"Assigned load profile (sum: {calc_metadata['load_profile_sum_kwh']:.2f} kWh) "
                f"to PySAM model. First 5 values: {self.rate_model.Load.load[:5]}"
            )

            # 5. Calculate and set monthly peak demands for the simulation year
            # This is crucial for rates with billing demand.
            # PySAM.LoadTools.get_monthly_peaks expects a list or tuple.
            # `datatype=1` signifies AC kWh load data.
            monthly_peaks_kw = PySAM.LoadTools.get_monthly_peaks(
                validated_load.tolist(), 1
            )
            self.rate_model.ElectricityRates.ur_yearzero_usage_peaks = (
                monthly_peaks_kw  # Expects a tuple
            )
            self.logger.debug(
                f"Set PySAM 'ur_yearzero_usage_peaks': {monthly_peaks_kw}"
            )

            # Diagnostic logging before execution
            self.logger.debug(
                f"Pre-execution - Energy TOU Matrix: {self.rate_model.ElectricityRates.ur_ec_tou_mat[:3]}"
            )
            self.logger.debug(
                f"Pre-execution - Fixed Monthly Charge: ${self.rate_model.ElectricityRates.ur_monthly_fixed_charge:.2f}"
            )
            if self.rate_model.ElectricityRates.ur_dc_enable:
                self.logger.debug(
                    f"Pre-execution - Demand Charges ENABLED. Demand TOU Matrix: {self.rate_model.ElectricityRates.ur_dc_tou_mat[:3]}"
                )

            # 6. Execute the PySAM Utilityrate5 simulation
            self.logger.debug("Executing PySAM Utilityrate5 simulation...")
            self.rate_model.execute()
            self.logger.debug("PySAM simulation execution complete.")

            # 7. Extract and process results from PySAM Outputs
            outputs = self.rate_model.Outputs

            annual_bill = float(getattr(outputs, "utility_bill_wo_sys_year1", 0.0))
            annual_energy_kwh = float(
                getattr(
                    outputs,
                    "year1_electric_load",
                    calc_metadata["load_profile_sum_kwh"],
                )
            )

            monthly_bills_raw = getattr(
                outputs, "year1_monthly_utility_bill_wo_sys", None
            )
            if monthly_bills_raw is None and hasattr(
                outputs, "utility_bill_wo_sys_ym"
            ):  # Fallback for older/multi-year output
                multi_year_bills = outputs.utility_bill_wo_sys_ym
                if (
                    multi_year_bills
                    and isinstance(multi_year_bills, (list, tuple))
                    and len(multi_year_bills[0]) == 12
                ):
                    monthly_bills_raw = multi_year_bills[0]

            monthly_bills = (
                list(monthly_bills_raw)
                if isinstance(monthly_bills_raw, (list, tuple))
                and len(monthly_bills_raw) == 12
                else [0.0] * 12
            )

            # Recalculate annual_bill from sum of monthly if primary attribute was zero or inconsistent
            # This adds robustness if 'utility_bill_wo_sys_year1' is missing or incorrect.
            sum_monthly = sum(monthly_bills)
            if annual_bill == 0.0 and sum_monthly != 0.0:
                self.logger.warning(
                    f"'utility_bill_wo_sys_year1' was 0 but sum of monthly bills is ${sum_monthly:.2f}. Using sum of monthly for annual total."
                )
                annual_bill = sum_monthly
            elif (
                abs(annual_bill - sum_monthly) > 0.01 and sum_monthly != 0.0
            ):  # Check for significant discrepancy
                self.logger.warning(
                    f"Discrepancy: 'utility_bill_wo_sys_year1' (${annual_bill:.2f}) vs. "
                    f"sum of monthly_bills (${sum_monthly:.2f}). Using 'utility_bill_wo_sys_year1'."
                )

            if (
                annual_bill == 0.0
                and annual_energy_kwh > 0.0
                and (not (no_energy_rates and no_fixed_charge and demand_disabled))
            ):
                self.logger.warning(
                    f"Calculation for rate '{calc_metadata['rate_label_used']}' resulted in a $0.00 annual bill "
                    f"despite positive energy consumption ({annual_energy_kwh:.2f} kWh) and a non-$0 rate structure. "
                    "Check PySAM parameters and rate details carefully."
                )

            # Safely extract monthly demand charge components
            dc_flat_monthly_raw = getattr(
                outputs, "year1_monthly_dc_fixed_without_system", [0.0] * 12
            )
            dc_tou_monthly_raw = getattr(
                outputs, "year1_monthly_dc_tou_without_system", [0.0] * 12
            )

            dc_flat_monthly = (
                list(dc_flat_monthly_raw)
                if isinstance(dc_flat_monthly_raw, (list, tuple))
                and len(dc_flat_monthly_raw) == 12
                else [0.0] * 12
            )
            dc_tou_monthly = (
                list(dc_tou_monthly_raw)
                if isinstance(dc_tou_monthly_raw, (list, tuple))
                and len(dc_tou_monthly_raw) == 12
                else [0.0] * 12
            )

            total_monthly_demand_charges = (
                np.array(dc_flat_monthly) + np.array(dc_tou_monthly)
            ).tolist()

            monthly_peaks_output = list(
                getattr(outputs, "year1_monthly_peak_wo_system", [0.0] * 12)
            )

            results = {
                "annual_energy_kwh": annual_energy_kwh,
                "annual_bill": annual_bill,
                "average_rate_ct_kwh": (annual_bill / annual_energy_kwh) * 100.0
                if annual_energy_kwh > 0
                else 0.0,
                "monthly_bills": monthly_bills,
                "monthly_energy_kwh": list(
                    getattr(outputs, "year1_monthly_load", [0.0] * 12)
                ),
                "monthly_peak_demand_kw": monthly_peaks_output,
                "annual_peak_demand_kw": float(max(monthly_peaks_output))
                if monthly_peaks_output and any(m > 0 for m in monthly_peaks_output)
                else 0.0,
                "monthly_fixed_charges": list(
                    getattr(outputs, "year1_monthly_fixed_without_system", [0.0] * 12)
                ),
                "monthly_energy_charges": list(
                    getattr(
                        outputs, "year1_monthly_ec_charge_without_system", [0.0] * 12
                    )
                ),
                "monthly_demand_charges_total": total_monthly_demand_charges,  # Combined flat and TOU
                "monthly_demand_charges_flat": dc_flat_monthly,
                "monthly_demand_charges_tou": dc_tou_monthly,
                "monthly_min_charges": list(
                    getattr(outputs, "year1_monthly_minimum_without_system", [0.0] * 12)
                ),
            }

            calc_metadata["calculation_timestamp_utc"] = datetime.datetime.now(
                datetime.timezone.utc
            ).isoformat()
            results["_metadata"] = calc_metadata

            # self.logger.info(LogMessages.CALCULATION_COMPLETE)
            # self.logger.info(
            #     f"Rate '{calc_metadata['rate_label_used']}': "
            #     f"Annual Bill: ${results['annual_bill']:,.2f}, "
            #     f"Avg Rate: {results['average_rate_ct_kwh']:.2f} cents/kWh"
            # )
            return results

        except ValueError as ve:  # From validation or setup
            self.logger.error(
                LogMessages.CALCULATION_ERROR.format(
                    f"Input validation or rate setup issue: {ve}"
                ),
                exc_info=True,
            )
            raise  # Re-raise to be caught by caller
        except Exception as e:  # From PySAM execution or result processing
            pysam_error_msg = "Undetermined PySAM or post-processing error."
            # Attempt to get detailed errors from PySAM if execution failed
            if (
                hasattr(self.rate_model, "Outputs")
                and hasattr(self.rate_model.Outputs, "errors")
                and self.rate_model.Outputs.errors
            ):
                errors_list = self.rate_model.Outputs.errors
                pysam_error_msg = "; ".join(
                    [
                        err.decode("utf-8", "replace")
                        if isinstance(err, bytes)
                        else str(err)
                        for err in errors_list
                    ]
                )
            elif (
                "execution error" in str(e).lower() or "ssc error" in str(e).lower()
            ):  # SSC errors often have specific formats
                parts = str(e).split(
                    "):", 1
                )  # Try to split at the first occurrence of '):'
                if len(parts) > 1 and parts[1].strip():
                    pysam_error_msg = parts[1].strip()
                else:  # Fallback if split doesn't yield a clear message part
                    pysam_error_msg = str(e)
            else:  # Generic exception message
                pysam_error_msg = str(e)

            final_error_message = f"Bill calculation failed for rate '{calc_metadata.get('rate_label_used', 'Unknown')}': {pysam_error_msg}"
            self.logger.error(
                LogMessages.CALCULATION_ERROR.format(final_error_message), exc_info=True
            )
            # For extensive debugging, one might log the full model state:
            # self.logger.debug(f"PySAM Model State at failure: {self.rate_model.export()}")
            raise RuntimeError(final_error_message) from e

    def calculate_bills_from_prepared_data(
        self,
        prepared_data: Dict[Any, Dict[str, Any]],
        rate_data: Optional[Dict[str, Any]] = None,
        max_workers: Optional[int] = None,
    ) -> Dict[Any, Dict[str, Any]]:
        """
        Calculate utility bills for multiple buildings using prepared load profile data
        and a single, shared utility rate structure.

        Args:
            prepared_data (Dict[Any, Dict[str, Any]]):
                A dictionary mapping unique building identifiers (Any type) to their
                prepared data. Each building's data must be a dictionary containing
                at least a 'load_profile' key with a valid 8760-hour, Monday-aligned
                numpy array of hourly load values.
            rate_data (Optional[Dict[str, Any]]):
                A single URDB-compatible rate structure dictionary to be applied to all
                buildings in the batch. If None, the method uses the rate structure
                that is currently configured in the calculator's PySAM model.
            max_workers (Optional[int]):
                Maximum number of worker threads. If None, defaults to min(32, len(prepared_data), (os.cpu_count() or 1) + 4).

        Returns:
            Dict[Any, Dict[str, Any]]:
                A dictionary mapping building identifiers to their bill calculation results.
                If a calculation fails for a specific building, its entry will contain
                an 'error' key with a descriptive message and its 'building_id'.

        Raises:
            ValueError: If `rate_data` is None and no rate structure is pre-configured.
            RuntimeError: If setting up the shared `rate_data` fails.
        """
        num_buildings = len(prepared_data)
        rate_id_for_log = "Previously Configured"
        if rate_data:
            rate_id_for_log = rate_data.get(
                "label", rate_data.get("name", "Unknown Rate from input")
            )

        # Determine optimal number of workers
        if max_workers is None:
            max_workers = min(32, num_buildings, (os.cpu_count() or 1) + 4)

        self.logger.info(
            f"Starting batch bill calculation for {num_buildings} buildings using rate: '{rate_id_for_log}' with {max_workers} workers."
        )

        # Setup the shared rate structure once if new data is provided
        if rate_data is not None:
            try:
                self.setup_rate_structure(rate_data)
                self.logger.info(
                    f"Successfully applied shared rate structure '{rate_id_for_log}' for batch calculation."
                )
            except (ValueError, RuntimeError) as setup_err:
                self.logger.error(
                    f"Failed to set up shared rate structure '{rate_id_for_log}': {setup_err}. Aborting batch calculation.",
                    exc_info=True,
                )
                # Return errors for all buildings as the shared rate setup failed
                return {
                    bldg_id: {
                        "error": f"Shared rate setup failed: {setup_err}",
                        "building_id": bldg_id,
                        "_metadata": {"rate_label_used": rate_id_for_log},
                    }
                    for bldg_id in prepared_data.keys()
                }
        else:  # No new rate_data, check if a rate is already configured
            no_energy_rates = (
                not self.rate_model.ElectricityRates.ur_ec_tou_mat
                and not self.rate_model.ElectricityRates.ur_ec_flat_mat
            )
            no_fixed_charge = (
                self.rate_model.ElectricityRates.ur_monthly_fixed_charge == 0.0
            )
            demand_disabled = self.rate_model.ElectricityRates.ur_dc_enable == 0
            if no_energy_rates and no_fixed_charge and demand_disabled:
                msg = "No rate data provided for batch and no valid rate structure previously set up in the model. Cannot calculate bills."
                self.logger.error(msg)
                return {
                    bldg_id: {
                        "error": msg,
                        "building_id": bldg_id,
                        "_metadata": {"rate_label_used": rate_id_for_log},
                    }
                    for bldg_id in prepared_data.keys()
                }
            self.logger.info(
                "Using previously configured rate structure for batch calculation."
            )

        def calculate_single_building(item_tuple):
            """Helper function to calculate bill for a single building."""
            bldg_id, data_entry = item_tuple

            try:
                # Create a separate calculator instance for this thread to avoid conflicts
                thread_calculator = UtilityBillCalculator(
                    load_column=self.load_column,
                    log_file=None,  # Avoid file logging conflicts in parallel
                )

                # Copy the rate structure from the main calculator
                # Since we already validated and set up the rate, export and import it
                # if rate_data is not None:
                #     thread_calculator.setup_rate_structure(rate_data)
                # else:
                # Copy the current rate model state
                main_model_params = self.rate_model.export()
                thread_calculator.rate_model.assign(main_model_params)

                if not isinstance(data_entry, dict):
                    raise TypeError(
                        "Prepared data entry for building must be a dictionary."
                    )
                load_profile_for_bldg = data_entry.get("load_profile")
                if load_profile_for_bldg is None:
                    raise KeyError(
                        "Missing 'load_profile' key in prepared data for building."
                    )
                if not isinstance(load_profile_for_bldg, np.ndarray):
                    raise TypeError("'load_profile' for building is not a numpy array.")

                # Calculate bill using the thread-local calculator
                results_for_bldg = (
                    thread_calculator.calculate_bill_from_prepared_profile(
                        load_profile_for_bldg,
                        rate_data=None,  # Rate already set up in thread calculator
                    )
                )

                # Enhance metadata specific to this building from the prepared_data
                if "_metadata" not in results_for_bldg:
                    results_for_bldg["_metadata"] = {}
                if "metadata" in data_entry and isinstance(
                    data_entry["metadata"], dict
                ):
                    # Merge, giving precedence to specific calc metadata if keys overlap
                    original_prep_metadata = data_entry["metadata"]
                    for k, v in original_prep_metadata.items():
                        if (
                            k not in results_for_bldg["_metadata"]
                        ):  # Add if not already set by calc
                            results_for_bldg["_metadata"][k] = v
                results_for_bldg["_metadata"]["building_id"] = (
                    bldg_id  # Ensure building ID is in final results
                )
                # Ensure rate identifier used for this batch is also in metadata
                results_for_bldg["_metadata"]["batch_rate_label_used"] = rate_id_for_log

                return bldg_id, results_for_bldg, None

            except (ValueError, TypeError, KeyError) as input_err:
                # Errors related to this specific building's data validity
                error_msg = (
                    f"Input data error for building '{bldg_id}': {str(input_err)}"
                )
                return (
                    bldg_id,
                    None,
                    {
                        "error": error_msg,
                        "building_id": bldg_id,
                        "_metadata": {"batch_rate_label_used": rate_id_for_log},
                    },
                )
            except RuntimeError as calc_err:
                # Errors during the actual bill calculation for this specific building
                error_msg = (
                    f"Bill calculation failed for building '{bldg_id}': {str(calc_err)}"
                )
                return (
                    bldg_id,
                    None,
                    {
                        "error": error_msg,
                        "building_id": bldg_id,
                        "_metadata": {"batch_rate_label_used": rate_id_for_log},
                    },
                )
            except Exception as general_err:
                # Catch any other unexpected errors for this building
                error_msg = f"Unexpected error processing building '{bldg_id}': {str(general_err)}"
                return (
                    bldg_id,
                    None,
                    {
                        "error": error_msg,
                        "building_id": bldg_id,
                        "_metadata": {"batch_rate_label_used": rate_id_for_log},
                    },
                )

        bill_results: Dict[Any, Dict[str, Any]] = {}
        success_count = 0
        fail_count = 0

        # for bldg_id, data_entry in prepared_data.items():
        #     self.logger.debug(f"Processing building ID: {bldg_id} for bill calculation.")
        #     try:
        #         if not isinstance(data_entry, dict):
        #              raise TypeError("Prepared data entry for building must be a dictionary.")
        #         load_profile_for_bldg = data_entry.get('load_profile')
        #         if load_profile_for_bldg is None:
        #             raise KeyError("Missing 'load_profile' key in prepared data for building.")
        #         if not isinstance(load_profile_for_bldg, np.ndarray):
        #              raise TypeError("'load_profile' for building is not a numpy array.")

        #         # Calculate bill using the (now shared or pre-existing) rate in the model
        #         # Pass rate_data=None to use the model's current state.
        #         results_for_bldg = self.calculate_bill_from_prepared_profile(
        #             load_profile_for_bldg,
        #             rate_data=None
        #         )

        #         # Enhance metadata specific to this building from the prepared_data
        #         if '_metadata' not in results_for_bldg: results_for_bldg['_metadata'] = {}
        #         if 'metadata' in data_entry and isinstance(data_entry['metadata'], dict):
        #             # Merge, giving precedence to specific calc metadata if keys overlap
        #             original_prep_metadata = data_entry['metadata']
        #             for k, v in original_prep_metadata.items():
        #                 if k not in results_for_bldg['_metadata']: # Add if not already set by calc
        #                      results_for_bldg['_metadata'][k] = v
        #         results_for_bldg['_metadata']['building_id'] = bldg_id # Ensure building ID is in final results
        #         # Ensure rate identifier used for this batch is also in metadata
        #         results_for_bldg['_metadata']['batch_rate_label_used'] = rate_id_for_log

        #         bill_results[bldg_id] = results_for_bldg
        #         success_count += 1

        #     except (ValueError, TypeError, KeyError) as input_err:
        #         # Errors related to this specific building's data validity
        #         error_msg = f"Input data error for building '{bldg_id}': {str(input_err)}"
        #         self.logger.warning(f"{error_msg} Skipping this building.", exc_info=True)
        #         bill_results[bldg_id] = {"error": error_msg, "building_id": bldg_id, "_metadata":{"batch_rate_label_used": rate_id_for_log}}
        #         fail_count += 1
        #     except RuntimeError as calc_err:
        #         # Errors during the actual bill calculation for this specific building
        #         error_msg = f"Bill calculation failed for building '{bldg_id}': {str(calc_err)}"
        #         self.logger.error(error_msg, exc_info=True) # Log calculation errors more prominently
        #         bill_results[bldg_id] = {"error": error_msg, "building_id": bldg_id, "_metadata":{"batch_rate_label_used": rate_id_for_log}}
        #         fail_count += 1
        #     except Exception as general_err:
        #         # Catch any other unexpected errors for this building
        #         error_msg = f"Unexpected error processing building '{bldg_id}': {str(general_err)}"
        #         self.logger.error(error_msg, exc_info=True)
        #         bill_results[bldg_id] = {"error": error_msg, "building_id": bldg_id, "_metadata":{"batch_rate_label_used": rate_id_for_log}}
        #         fail_count += 1

        # Execute calculations in parallel
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_building = {
                executor.submit(calculate_single_building, item): item[0]
                for item in prepared_data.items()
            }

            # Process completed tasks
            for future in as_completed(future_to_building):
                bldg_id = future_to_building[future]
                try:
                    returned_bldg_id, success_result, error_result = future.result()

                    if success_result is not None:
                        bill_results[returned_bldg_id] = success_result
                        success_count += 1
                    else:
                        bill_results[returned_bldg_id] = error_result
                        fail_count += 1

                except Exception as exc:
                    error_msg = (
                        f"Parallel execution error for building '{bldg_id}': {str(exc)}"
                    )
                    self.logger.error(error_msg, exc_info=True)
                    bill_results[bldg_id] = {
                        "error": error_msg,
                        "building_id": bldg_id,
                        "_metadata": {"batch_rate_label_used": rate_id_for_log},
                    }
                    fail_count += 1

        self.logger.info(
            f"Batch bill calculation complete for rate '{rate_id_for_log}'. "
            f"Succeeded: {success_count}, Failed: {fail_count}."
        )
        return bill_results

    def _calc_one(
        self,
        bldg_id: Any,
        upgrade: int,
        load_array: "np.ndarray",
        pysam_params: Dict[str, Any],
        **per_row_meta: Any,
    ) -> Dict[str, Any]:
        """Calculate one bill row. Patchable method (used by tests).

        Extra per-row metadata (e.g. ``weight``, ``sqft``) is surfaced as
        columns on the result so downstream aggregation can use it without
        re-joining metadata.
        """
        try:
            model = ur5.new()
            # Base parameters
            model.Lifetime.analysis_period = 1
            model.Lifetime.system_use_lifetime_output = 0
            model.Lifetime.inflation_rate = 0.0
            model.SystemOutput.degradation = [0.0]
            model.SystemOutput.gen = [0.0] * HOURS_PER_YEAR
            model.ElectricityRates.ur_metering_option = 0

            # Apply pre-converted PySAM parameters directly.
            model.ElectricityRates.replace(copy.deepcopy(pysam_params))

            # Validate and assign load
            validated, _ = validate_load_profile(
                np.array(load_array), expected_length=HOURS_PER_YEAR
            )
            model.Load.load = validated.tolist()
            monthly_peaks = PySAM.LoadTools.get_monthly_peaks(validated.tolist(), 1)
            model.ElectricityRates.ur_yearzero_usage_peaks = monthly_peaks

            model.execute()

            annual_bill = float(
                getattr(model.Outputs, "utility_bill_wo_sys_year1", 0.0)
            )
            annual_kwh = float(getattr(model.Outputs, "year1_electric_load", 0.0))
            avg_rate = (annual_bill / annual_kwh * 100) if annual_kwh > 0 else 0.0
            monthly_raw = getattr(
                model.Outputs, "year1_monthly_utility_bill_wo_sys", None
            )
            monthly = list(monthly_raw) if monthly_raw is not None else [0.0] * 12

            def _sum12(attr: str) -> float:
                vals = getattr(model.Outputs, attr, None)
                if isinstance(vals, (list, tuple)) and vals:
                    return float(sum(vals))
                return 0.0

            monthly_peaks_out = getattr(
                model.Outputs, "year1_monthly_peak_wo_system", None
            )
            peak_demand_kw = (
                float(max(monthly_peaks_out))
                if isinstance(monthly_peaks_out, (list, tuple))
                and monthly_peaks_out
                and any(p > 0 for p in monthly_peaks_out)
                else 0.0
            )

            row = {
                "bldg_id": bldg_id,
                "upgrade": upgrade,
                "electricity_annual_bill": annual_bill,
                "electricity_annual_energy_kwh": annual_kwh,
                "electricity_average_rate_ct_kwh": avg_rate,
                "electricity_monthly_bills": str(monthly),
                "electricity_annual_fixed_charge": _sum12(
                    "year1_monthly_fixed_without_system"
                ),
                "electricity_annual_energy_charge": _sum12(
                    "year1_monthly_ec_charge_without_system"
                ),
                "electricity_annual_demand_charge": _sum12(
                    "year1_monthly_dc_fixed_without_system"
                )
                + _sum12("year1_monthly_dc_tou_without_system"),
                "electricity_annual_min_charge": _sum12(
                    "year1_monthly_minimum_without_system"
                ),
                "electricity_peak_demand_kw": peak_demand_kw,
                "error": None,
            }
            row.update(per_row_meta)
            return row
        except Exception as exc:
            self.logger.warning(
                f"Bill calculation failed for {bldg_id}/{upgrade}: {exc}"
            )
            row = {
                "bldg_id": bldg_id,
                "upgrade": upgrade,
                "electricity_annual_bill": None,
                "electricity_annual_energy_kwh": None,
                "electricity_average_rate_ct_kwh": None,
                "electricity_monthly_bills": None,
                "electricity_annual_fixed_charge": None,
                "electricity_annual_energy_charge": None,
                "electricity_annual_demand_charge": None,
                "electricity_annual_min_charge": None,
                "electricity_peak_demand_kw": None,
                "error": str(exc),
            }
            row.update(per_row_meta)
            return row

    def calculate_batch_bills(
        self,
        load_records: List[Any],
        rate_config: Dict[str, Any],
        progress_cb: Optional[Any] = None,
        max_workers: Optional[int] = None,
    ) -> pl.DataFrame:
        """
        Calculate utility bills for a batch of load records.

        Each record may be either:
          * a legacy 3-tuple ``(bldg_id, upgrade, load_array)``, or
          * a dict with keys ``bldg_id``, ``upgrade``, ``load_array`` and any
            number of additional per-row metadata fields (e.g. ``weight``,
            ``sqft``, ``row_idx``) that should be surfaced as result columns.

        The calculator passes ``load_array`` to PySAM **as-is** — any per-row
        load scaling (e.g. by ComStock ``weight``) is the caller's
        responsibility, applied before the record is constructed.

        Args:
            load_records: Records as described above.
            rate_config: URDB v8-compatible rate dict to apply to all records.
            progress_cb: Optional callable ``(done: int, total: int)`` called after
                each record is processed (success or failure).
            max_workers: Thread pool size (defaults to self.max_workers).

        Returns:
            Polars DataFrame with at least: ``bldg_id, upgrade,
            electricity_annual_bill, electricity_annual_energy_kwh,
            electricity_average_rate_ct_kwh, electricity_monthly_bills, error``.
            Any per-row metadata fields supplied via dict records appear as
            additional columns (filled with ``None`` for tuple-style records).
        """
        max_workers = max_workers or getattr(self, "max_workers", 4)
        total = len(load_records)
        completed_count = [0]
        count_lock = threading.Lock()

        # Convert rate dict once via UrdbRateBuilder so build()'s full cleanup
        # pipeline runs before the PySAM conversion, rather than doing ad-hoc
        # None/empty-tuple filtering inside _calc_one.
        pysam_params = UrdbRateBuilder.prepare_for_pysam(rate_config)

        # Collect the union of per-row metadata keys so we can null-fill rows
        # that didn't supply them (e.g. legacy tuple records).
        meta_keys: set = set()
        normalized_records: list[dict] = []
        for rec in load_records:
            if isinstance(rec, dict):
                bldg_id = rec["bldg_id"]
                upgrade = rec["upgrade"]
                load_array = rec["load_array"]
                extras = {
                    k: v
                    for k, v in rec.items()
                    if k not in ("bldg_id", "upgrade", "load_array")
                }
            else:
                # Legacy tuple form
                bldg_id, upgrade, load_array = rec
                extras = {}
            meta_keys.update(extras.keys())
            normalized_records.append(
                {
                    "bldg_id": bldg_id,
                    "upgrade": upgrade,
                    "load_array": load_array,
                    "extras": extras,
                }
            )

        rows: list[dict] = []

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(
                    self._calc_one,
                    rec["bldg_id"],
                    rec["upgrade"],
                    rec["load_array"],
                    pysam_params,
                    **rec["extras"],
                ): (rec["bldg_id"], rec["upgrade"])
                for rec in normalized_records
            }
            for future in as_completed(futures):
                row = future.result()
                # Ensure every row has every meta key (None where unspecified)
                for k in meta_keys:
                    row.setdefault(k, None)
                rows.append(row)
                with count_lock:
                    completed_count[0] += 1
                    done = completed_count[0]
                if progress_cb is not None:
                    try:
                        progress_cb(done, total)
                    except Exception:
                        pass

        if not rows:
            schema = {
                "bldg_id": pl.Int64,
                "upgrade": pl.Int64,
                "electricity_annual_bill": pl.Float64,
                "electricity_annual_energy_kwh": pl.Float64,
                "electricity_average_rate_ct_kwh": pl.Float64,
                "electricity_monthly_bills": pl.Utf8,
                "electricity_annual_fixed_charge": pl.Float64,
                "electricity_annual_energy_charge": pl.Float64,
                "electricity_annual_demand_charge": pl.Float64,
                "electricity_annual_min_charge": pl.Float64,
                "electricity_peak_demand_kw": pl.Float64,
                "error": pl.Utf8,
            }
            return pl.DataFrame(schema=schema)

        return pl.DataFrame(rows)
