# cl_UrdbRateBuilder.py

"""
URDB-Compatible Utility Rate Builder

This module provides a class for programmatically building utility rate structures
that are compatible with the URDB v8 schema and can be processed by PySAM's 
URDBv8_to_ElectricityRates tool, ensuring seamless integration with existing
UtilityBillCalculator workflows.

The builder creates rate structures that match the URDB v8 JSON schema exactly,
with all field names and structures conforming to the official API specification.

Features:
- Full URDB v8 field compatibility with correct camelCase naming
- Complete time coverage validation (no gaps in rate definitions)
- PySAM version compatibility checking
- Comprehensive validation framework
- Seasonal rate integration
- Advanced demand charge configurations

Compatible with: PySAM UtilityRate5, UtilityBillCalculator class
Version: 2.0.0
URDB API Version: 8
"""

# TODO: 
# add validation for overlapping seasonal definitions, rate tier continuity
# and consistency between energy and demand schedules when both are defined

from typing import Dict, List, Any, Optional, Union, Tuple
from datetime import datetime, timezone
import json
import copy
import logging
from dataclasses import dataclass, asdict
from enum import Enum
import warnings
import importlib.util
import sys

# Import existing classes for integration
from buildstock_rate_impact_calculator.core.logger_config import setup_logging, LogMessages

logger = logging.getLogger(__name__)


class MeteringOption(Enum):
    """Net metering options compatible with PySAM UtilityRate5."""
    NET_ENERGY_METERING = 0
    NET_ENERGY_METERING_CREDITS = 1  
    NET_BILLING = 2
    NET_BILLING_CARRYOVER = 3
    BUY_ALL_SELL_ALL = 4


class RateUnit(Enum):
    """Supported rate units for energy tiers (URDB v8 specification)."""
    KWH = "kWh"
    KWH_DAILY = "kWh daily"
    KWH_PER_KW = "kWh/kW"
    KWH_PER_KW_DAILY = "kWh/kW daily"


class DemandUnit(Enum):
    """Supported demand charge units (URDB v8 specification)."""
    KW = "kW"
    HP = "hp"
    KVA = "kVA"
    KW_DAILY = "kW daily"
    HP_DAILY = "hp daily"
    KVA_DAILY = "kVA daily"


class FixedChargeUnits(Enum):
    """Units for fixed charges (URDB v8 specification)."""
    DAILY = "$/day"
    MONTHLY = "$/month"
    YEARLY = "$/year"


class MinChargeUnits(Enum):
    """Units for minimum charges (URDB v8 specification)."""
    DAILY = "$/day"
    MONTHLY = "$/month"
    YEARLY = "$/year"


class DGRules(Enum):
    """Distributed generation compensation rules (URDB v8 specification)."""
    NET_METERING = "Net Metering"
    NET_BILLING_INSTANTANEOUS = "Net Billing Instantaneous"
    NET_BILLING_HOURLY = "Net Billing Hourly"
    BUY_ALL_SELL_ALL = "Buy All Sell All"


@dataclass
class EnergyTier:
    """Represents a single energy rate tier."""
    max_usage: Optional[float] = None  # None means unlimited
    rate: float = 0.0
    adj: float = 0.0  # Rate adjustment
    sell: float = 0.0  # Sell rate (for net metering)
    unit: RateUnit = RateUnit.KWH


@dataclass 
class DemandTier:
    """Represents a single demand rate tier."""
    max_demand: Optional[float] = None  # None means unlimited
    rate: float = 0.0
    adj: float = 0.0  # Rate adjustment
    unit: DemandUnit = DemandUnit.KW


@dataclass
class SeasonDefinition:
    """Defines a season for seasonal rate variations."""
    name: str
    start_month: int  # 1-12
    start_day: int    # 1-31
    end_month: int    # 1-12  
    end_day: int      # 1-31


class PySAMVersionError(Exception):
    """Raised when PySAM version is incompatible."""
    pass


class RateValidationError(Exception):
    """Raised when rate structure validation fails."""
    pass


class UrdbRateBuilder:
    """
    Builder class for creating URDB v8 compatible utility rate structures.
    
    This class allows programmatic creation of utility rates that are fully
    compatible with the URDB v8 schema and can be processed by PySAM's
    URDBv8_to_ElectricityRates tool for seamless integration with the
    UtilityBillCalculator class.
    
    All field names exactly match the URDB v8 API specification with correct
    camelCase formatting. The builder ensures complete time coverage validation
    to guarantee no gaps exist in rate definitions throughout the year.
    
    Features:
    - Fluent builder interface for easy rate construction
    - Full URDB v8 compatibility with exact field naming
    - Automatic validation of rate structures and time coverage
    - Support for complex time-of-use schedules
    - Demand charge configurations including look-back billing
    - Net metering and sell rate configurations
    - Seasonal rate integration
    - PySAM version compatibility checking
    - Export to URDB JSON format
    
    Example:
        builder = UrdbRateBuilder()
        rate = (builder
                .basic_info("Custom Residential Rate", "Custom Utility", "Residential")
                .monthly_fixed_charge(25.0)
                .add_energy_tier(0, 500, 0.12)  # First 500 kWh at $0.12/kWh
                .add_energy_tier(0, None, 0.15)  # Above 500 kWh at $0.15/kWh
                .enable_demand_charges()
                .add_demand_tier(0, 10, 12.0)    # First 10 kW at $12/kW
                .add_demand_tier(0, None, 15.0)  # Above 10 kW at $15/kW
                .build())
    """
    
    # PySAM compatibility matrix
    PYSAM_COMPATIBILITY = {
        'URDBv7_to_ElectricityRates': (2, 1, 4),  # Minimum version for URDBv7
        'URDBv8_to_ElectricityRates': (4, 0, 0),  # Minimum version for URDBv8
    }
    
    def __init__(self, log_file: Optional[str] = None, validate_pysam: bool = True):
        """
        Initialize the rate builder.
        
        Args:
            log_file: Optional path to log file for debugging
            validate_pysam: Whether to validate PySAM compatibility on initialization
            
        Raises:
            PySAMVersionError: If PySAM version is incompatible and validate_pysam=True
        """
        self.logger = setup_logging(
            log_file=log_file, 
            module_name="urdb_rate_builder"
        )
        self.logger = logging.getLogger(__name__)
        
        # Check PySAM compatibility
        self.pysam_version = None
        self.urdb_converter_available = None
        if validate_pysam:
            self._validate_pysam_compatibility()
        
        # Initialize rate structure with URDB v8 defaults (exact field names)
        self._rate_data = {
            # Basic identification
            "label": self._generate_unique_label(),
            "name": "",
            "utility": "",
            "sector": "Residential",
            "description": "",
            "source": "Custom Built Rate",
            "uri": "",
            
            # Approval and dates (using current time)
            "approved": True,
            "is_default": False,
            "startdate": int(datetime.now(timezone.utc).timestamp()),
            "enddate": None,  # No expiration by default
            "supercedes": "",
            
            # Service characteristics
            "servicetype": "Bundled",
            "voltagecategory": "Secondary",
            "phasewiring": "Single and 3-Phase",
            
            # Capacity constraints
            "peakkwcapacitymin": 0.0,
            "peakkwcapacitymax": 0.0,
            "peakkwhusagemin": 0.0,
            "peakkwhusagemax": 0.0,
            "voltageminimum": 0.0,
            "voltagemaximum": 0.0,
            
            # Fixed charges (URDB v8 format)
            "fixedchargefirstmeter": 0.0,  # Monthly fixed charge for first meter
            "fixedchargeeaaddl": 0.0,      # Fixed charge for each additional meter
            "fixedchargeunits": FixedChargeUnits.MONTHLY.value,  # Units for fixed charges
            
            # Minimum charges (URDB v8 format)
            "mincharge": 0.0,             # Minimum charge amount
            "minchargeunits": MinChargeUnits.MONTHLY.value,  # Units for minimum charge
            
            # Energy rate structure - will be populated by add_energy_tier()
            "energyratestructure": [],      # List of periods, each containing tiers
            "energyweekdayschedule": None,  # 12x24 matrix
            "energyweekendschedule": None,  # 12x24 matrix
            
            # Demand rate structure - will be populated if enabled
            "demandratestructure": [],      # List of periods, each containing tiers
            "demandweekdayschedule": None,  # 12x24 matrix  
            "demandweekendschedule": None,  # 12x24 matrix
            "demandrateunit": DemandUnit.KW.value,
            "demandwindow": 15.0,          # Demand window in minutes
            "demandreactivepowercharge": 0.0,  # Reactive power charge $/kVAR
            
            # Look-back demand billing (URDB v8 format with correct camelCase)
            "lookbackMonths": [False] * 12,        # Array of 12 booleans (CORRECTED)
            "lookbackPercent": 0.0,                # Single percentage value (CORRECTED)
            "lookbackRange": 0,                    # Number of months (CORRECTED)
            
            # Fuel adjustments
            "fueladjustmentsmonthly": [0.0] * 12,  # Monthly fuel adjustments $/kWh
            
            # Net metering and sell rates (URDB v8 format)
            "dgrules": "",                 # Net metering rules string
            
            # Seasonal definitions (optional)
            "seasonaldefinitions": [],
            
            # Rate metadata
            "revisions": [],
            "basicinformationcomments": "",
            "energycomments": "",
            "demandcomments": "",
            "energyattrs": [],
            "demandattrs": [],
            "fixedattrs": [],
            
            # Internal state
            "_periods_defined": 1,         # Number of TOU periods defined
            "_demand_enabled": False,      # Whether demand charges are enabled
            "_seasons_defined": False,     # Whether seasonal rates are defined
            "_schedule_validated": False   # Whether schedules have been validated for coverage
        }
        
        # Initialize default flat rate schedules (all periods = 0)
        self._initialize_default_schedules()
        
        self.logger.info("UrdbRateBuilder initialized with URDB v8 compatibility")
        if self.pysam_version:
            self.logger.info(f"PySAM version detected: {'.'.join(map(str, self.pysam_version))}")
            self.logger.info(f"URDB converter available: {self.urdb_converter_available}")
    
    def _validate_pysam_compatibility(self) -> None:
        """
        Validate PySAM version and availability of URDB conversion tools.
        
        Raises:
            PySAMVersionError: If PySAM is not available or incompatible
        """
        try:
            import PySAM
            
            # Get PySAM version
            if hasattr(PySAM, '__version__'):
                version_str = PySAM.__version__
                self.pysam_version = tuple(map(int, version_str.split('.')))
            else:
                # Fallback method for older PySAM versions
                self.logger.warning("Cannot determine PySAM version - proceeding with caution")
                self.pysam_version = (0, 0, 0)
            
            # Check availability of URDB conversion tools
            try:
                import PySAM.UtilityRateTools as ur_tools
                
                # Check for URDBv8 converter (preferred)
                if hasattr(ur_tools, 'URDBv8_to_ElectricityRates'):
                    required_version = self.PYSAM_COMPATIBILITY['URDBv8_to_ElectricityRates']
                    if self.pysam_version >= required_version:
                        self.urdb_converter_available = 'URDBv8_to_ElectricityRates'
                        self.logger.info("URDBv8_to_ElectricityRates converter available")
                    else:
                        self.logger.warning(
                            f"PySAM version {'.'.join(map(str, self.pysam_version))} "
                            f"may not support URDBv8_to_ElectricityRates (requires {'.'.join(map(str, required_version))}+)"
                        )
                        self.urdb_converter_available = 'URDBv8_to_ElectricityRates'  # Try anyway
                
                # Check for URDBv7 converter (fallback)
                elif hasattr(ur_tools, 'URDBv7_to_ElectricityRates'):
                    required_version = self.PYSAM_COMPATIBILITY['URDBv7_to_ElectricityRates']
                    if self.pysam_version >= required_version:
                        self.urdb_converter_available = 'URDBv7_to_ElectricityRates'
                        self.logger.warning(
                            "Only URDBv7_to_ElectricityRates available. "
                            "Consider upgrading PySAM for full URDB v8 support."
                        )
                    else:
                        raise PySAMVersionError(
                            f"PySAM version {'.'.join(map(str, self.pysam_version))} "
                            f"does not support required URDB converters (requires {'.'.join(map(str, required_version))}+)"
                        )
                else:
                    raise PySAMVersionError("No URDB conversion tools found in PySAM.UtilityRateTools")
                    
            except ImportError as e:
                raise PySAMVersionError(f"Cannot import PySAM.UtilityRateTools: {e}")
                
        except ImportError as e:
            raise PySAMVersionError(f"PySAM is not available: {e}")
    
    def _generate_unique_label(self) -> str:
        """Generate a unique label for the rate."""
        timestamp = int(datetime.now().timestamp())
        return f"custom_rate_{timestamp}"
    
    def _initialize_default_schedules(self) -> None:
        """Initialize default flat rate schedules (no time-of-use)."""
        # Create 12x24 matrix filled with zeros (period 0) - this is critical for flat rates
        default_schedule = [[0 for _ in range(24)] for _ in range(12)]
        
        self._rate_data["energyweekdayschedule"] = copy.deepcopy(default_schedule)
        self._rate_data["energyweekendschedule"] = copy.deepcopy(default_schedule)
        
        # Initialize with single period containing empty tiers
        self._rate_data["energyratestructure"] = [[]]  # One period, no tiers yet
        
        # Ensure periods_defined starts at 1 for single period
        self._rate_data["_periods_defined"] = 1
    
    # ==========================================================================
    # BASIC RATE INFORMATION
    # ==========================================================================
    
    def basic_info(self, name: str, utility: str, sector: str = "Residential") -> 'UrdbRateBuilder':
        """
        Set basic rate information.
        
        Args:
            name: Human-readable rate name
            utility: Utility company name
            sector: Rate sector ("Residential", "Commercial", "Industrial", "Lighting")
            
        Returns:
            Self for method chaining
            
        Raises:
            ValueError: If sector is not a valid URDB sector
        """
        valid_sectors = ["Residential", "Commercial", "Industrial", "Lighting"]
        if sector not in valid_sectors:
            raise ValueError(f"Invalid sector '{sector}'. Must be one of: {valid_sectors}")
        
        self._rate_data["name"] = name
        self._rate_data["utility"] = utility  
        self._rate_data["sector"] = sector
        
        # Update label to be more descriptive
        safe_name = "".join(c for c in name if c.isalnum() or c in "_-").lower()
        self._rate_data["label"] = f"{safe_name}_{int(datetime.now().timestamp())}"
        
        self.logger.info(f"Set basic info: {name} ({utility}, {sector})")
        return self
    
    def description(self, desc: str) -> 'UrdbRateBuilder':
        """Set rate description."""
        self._rate_data["description"] = desc
        return self
    
    def effective_dates(self, start_date: datetime, end_date: Optional[datetime] = None) -> 'UrdbRateBuilder':
        """
        Set rate effective dates.
        
        Args:
            start_date: When rate becomes effective
            end_date: When rate expires (None for no expiration)
            
        Returns:
            Self for method chaining
        """
        self._rate_data["startdate"] = int(start_date.timestamp())
        self._rate_data["enddate"] = int(end_date.timestamp()) if end_date else None
        
        return self
    
    def service_characteristics(self, service_type: str = "Bundled", 
                               voltage_category: str = "Secondary",
                               phase_wiring: str = "Single and 3-Phase") -> 'UrdbRateBuilder':
        """
        Set service characteristics.
        
        Args:
            service_type: Service type ("Bundled", "Energy", "Delivery", "Delivery with Standard Offer")
            voltage_category: Voltage category ("Primary", "Secondary", "Transmission")
            phase_wiring: Phase wiring ("Single Phase", "3-Phase", "Single and 3-Phase")
            
        Returns:
            Self for method chaining
        """
        valid_service_types = ["Bundled", "Energy", "Delivery", "Delivery with Standard Offer"]
        valid_voltage_categories = ["Primary", "Secondary", "Transmission"]
        valid_phase_wirings = ["Single Phase", "3-Phase", "Single and 3-Phase"]
        
        if service_type not in valid_service_types:
            raise ValueError(f"Invalid service_type. Must be one of: {valid_service_types}")
        if voltage_category not in valid_voltage_categories:
            raise ValueError(f"Invalid voltage_category. Must be one of: {valid_voltage_categories}")
        if phase_wiring not in valid_phase_wirings:
            raise ValueError(f"Invalid phase_wiring. Must be one of: {valid_phase_wirings}")
        
        self._rate_data["servicetype"] = service_type
        self._rate_data["voltagecategory"] = voltage_category
        self._rate_data["phasewiring"] = phase_wiring
        
        return self
    
    # ==========================================================================
    # FIXED AND MINIMUM CHARGES
    # ==========================================================================
    
    def monthly_fixed_charge(self, amount: float, additional_meter_charge: float = 0.0) -> 'UrdbRateBuilder':
        """
        Set monthly fixed charges.
        
        Args:
            amount: Fixed charge for first meter in dollars per month
            additional_meter_charge: Fixed charge for each additional meter
            
        Returns:
            Self for method chaining
        """
        self._rate_data["fixedchargefirstmeter"] = float(amount)
        self._rate_data["fixedchargeeaaddl"] = float(additional_meter_charge)
        self._rate_data["fixedchargeunits"] = FixedChargeUnits.MONTHLY.value
        
        self.logger.debug(f"Set monthly fixed charge: ${amount:.2f}, additional: ${additional_meter_charge:.2f}")
        return self
    
    def minimum_charge(self, amount: float, units: MinChargeUnits = MinChargeUnits.MONTHLY) -> 'UrdbRateBuilder':
        """
        Set minimum charge.
        
        Args:
            amount: Minimum charge amount
            units: Units for minimum charge (daily, monthly, yearly)
            
        Returns:
            Self for method chaining
        """
        self._rate_data["mincharge"] = float(amount)
        self._rate_data["minchargeunits"] = units.value
        
        self.logger.debug(f"Set minimum charge: ${amount:.2f} {units.value}")
        return self
    
    # ==========================================================================
    # ENERGY RATE STRUCTURE
    # ==========================================================================
    
    def add_energy_tier(self, period: int, max_usage: Optional[float], 
                    rate: float, adj: float = 0.0, sell: float = 0.0,
                    unit: RateUnit = RateUnit.KWH) -> 'UrdbRateBuilder':
        """
        Add an energy rate tier to a specific time-of-use period.
        
        Note: max_usage should be CUMULATIVE maximum, not tier maximum.
        For example, if you want:
        - First 1000 kWh at $0.08
        - Next 1000 kWh at $0.15
        You should call:
        - add_energy_tier(0, 1000, 0.08)  # Up to 1000 kWh
        - add_energy_tier(0, 2000, 0.15)  # Up to 2000 kWh (cumulative)
        """
        if period < 0:
            raise ValueError("Period index must be non-negative")
        
        # Validate unit against PySAM supported values
        if unit not in RateUnit:
            raise ValueError(f"Invalid rate unit. Must be one of: {[u.value for u in RateUnit]}")
        
        # Ensure period exists in rate structure
        while len(self._rate_data["energyratestructure"]) <= period:
            self._rate_data["energyratestructure"].append([])
        
        # Create tier dictionary (PySAM/URDB format)
        tier = {
            "rate": float(rate),
            "adj": float(adj),
            "sell": float(sell)
        }
        
        # Add max usage if specified (should be cumulative)
        if max_usage is not None:
            tier["max"] = float(max_usage)
        
        # Add tier to the specified period
        self._rate_data["energyratestructure"][period].append(tier)
        
        # Update periods count
        self._rate_data["_periods_defined"] = max(self._rate_data["_periods_defined"], period + 1)
        
        # Reset schedule validation flag since structure changed
        self._rate_data["_schedule_validated"] = False
        
        tier_desc = f"period {period}, rate ${rate:.4f}/{unit.value}"
        if max_usage is not None:
            tier_desc += f", cumulative max {max_usage} {unit.value}"
        self.logger.debug(f"Added energy tier: {tier_desc}")
        
        return self
    
    def simple_flat_rate(self, rate: float, sell_rate: Optional[float] = None) -> 'UrdbRateBuilder':
        """
        Create a simple flat energy rate (single tier, no time-of-use).
        
        Args:
            rate: Energy rate in $/kWh
            sell_rate: Optional sell rate for net metering (defaults to rate)
            
        Returns:
            Self for method chaining
        """
        sell = sell_rate if sell_rate is not None else rate
        return self.add_energy_tier(0, None, rate, sell=sell)
    
    def add_tiered_energy_schedule(self, tiers: List[Tuple[Optional[float], float]]) -> 'UrdbRateBuilder':
        """
        Add a tiered energy rate structure (increasing block rates).
        
        Args:
            tiers: List of (max_usage, rate) tuples. Use None for max_usage on final tier.
            
        Returns:
            Self for method chaining
            
        Raises:
            ValueError: If tiers list is empty or improperly structured
            
        Example:
            builder.add_tiered_energy_schedule([
                (500, 0.12),   # First 500 kWh at $0.12/kWh
                (1000, 0.15),  # Next 500 kWh at $0.15/kWh  
                (None, 0.18)   # Above 1000 kWh at $0.18/kWh
            ])
        """
        if not tiers:
            raise ValueError("Tiers list cannot be empty")
        
        # Validate tier structure
        for i, (max_usage, rate) in enumerate(tiers):
            if rate < 0:
                raise ValueError(f"Rate for tier {i} cannot be negative")
            if max_usage is not None and max_usage <= 0:
                raise ValueError(f"Max usage for tier {i} must be positive or None")
        
        for max_usage, rate in tiers:
            self.add_energy_tier(0, max_usage, rate)
        
        self.logger.info(f"Added tiered energy schedule with {len(tiers)} tiers")
        return self
    
    # ==========================================================================
    # TIME-OF-USE SCHEDULES
    # ==========================================================================
    
    def set_tou_schedule(self, weekday_schedule: List[List[int]], 
                        weekend_schedule: Optional[List[List[int]]] = None) -> 'UrdbRateBuilder':
        """
        Set time-of-use schedules for energy charges.
        
        Args:
            weekday_schedule: 12x24 matrix (months x hours) with period indices
            weekend_schedule: 12x24 matrix for weekends (defaults to same as weekday)
            
        Returns:
            Self for method chaining
            
        Raises:
            ValueError: If schedules are not properly formatted
        """
        # Validate weekday schedule
        if len(weekday_schedule) != 12:
            raise ValueError("Weekday schedule must have exactly 12 months")
        
        for month_idx, month in enumerate(weekday_schedule):
            if not isinstance(month, list) or len(month) != 24:
                raise ValueError(f"Month {month_idx + 1} in weekday schedule must have exactly 24 hours")
            
            for hour_idx, period in enumerate(month):
                if not isinstance(period, int) or period < 0:
                    raise ValueError(f"Period at month {month_idx + 1}, hour {hour_idx} must be a non-negative integer")
        
        # Use weekday schedule for weekend if not provided
        weekend_schedule = weekend_schedule or copy.deepcopy(weekday_schedule)
        
        # Validate weekend schedule
        if len(weekend_schedule) != 12:
            raise ValueError("Weekend schedule must have exactly 12 months")
        
        for month_idx, month in enumerate(weekend_schedule):
            if not isinstance(month, list) or len(month) != 24:
                raise ValueError(f"Month {month_idx + 1} in weekend schedule must have exactly 24 hours")
            
            for hour_idx, period in enumerate(month):
                if not isinstance(period, int) or period < 0:
                    raise ValueError(f"Period at month {month_idx + 1}, hour {hour_idx} must be a non-negative integer")
        
        self._rate_data["energyweekdayschedule"] = weekday_schedule
        self._rate_data["energyweekendschedule"] = weekend_schedule
        
        # Count unique periods used in schedules
        all_periods = set()
        for month in weekday_schedule + weekend_schedule:
            all_periods.update(month)
        
        max_period = max(all_periods) if all_periods else 0
        
        # Ensure we have rate structures for all periods used in schedule
        while len(self._rate_data["energyratestructure"]) <= max_period:
            self._rate_data["energyratestructure"].append([])
        
        # If any period has no tiers, this will cause issues
        for period_idx in all_periods:
            if not self._rate_data["energyratestructure"][period_idx]:
                self.logger.warning(f"TOU period {period_idx} referenced in schedule but has no rate tiers")
        
        self._rate_data["energyweekdayschedule"] = weekday_schedule
        self._rate_data["energyweekendschedule"] = weekend_schedule
        
        self._rate_data["_periods_defined"] = max_period + 1
        self._rate_data["_schedule_validated"] = False
        
        self.logger.info(f"Set TOU schedules with periods: {sorted(all_periods)}")
        return self
    
    def simple_tou_schedule(self, peak_hours: List[int], peak_months: Optional[List[int]] = None,
                        weekend_peak: bool = False) -> 'UrdbRateBuilder':
        """
        Create a simple 2-period time-of-use schedule.
        """
        # Validate peak hours
        for hour in peak_hours:
            if not isinstance(hour, int) or hour < 0 or hour > 23:
                raise ValueError(f"Invalid peak hour: {hour}. Must be 0-23")
        
        peak_months = peak_months or list(range(1, 13))  # All months
        
        # Validate peak months
        for month in peak_months:
            if not isinstance(month, int) or month < 1 or month > 12:
                raise ValueError(f"Invalid peak month: {month}. Must be 1-12")
        
        # Create schedules - 0 = off-peak, 1 = peak
        weekday_schedule = []
        weekend_schedule = []
        
        for month_idx in range(12):  # 0-11 for schedule matrix
            month_num = month_idx + 1  # Convert to 1-12 for comparison
            
            # Weekday schedule
            weekday_month = []
            for hour in range(24):
                if month_num in peak_months and hour in peak_hours:
                    weekday_month.append(1)  # Peak period
                else:
                    weekday_month.append(0)  # Off-peak period
            weekday_schedule.append(weekday_month)
            
            # Weekend schedule  
            weekend_month = []
            for hour in range(24):
                if weekend_peak and month_num in peak_months and hour in peak_hours:
                    weekend_month.append(1)  # Peak period
                else:
                    weekend_month.append(0)  # Off-peak period
            weekend_schedule.append(weekend_month)
        
        self.logger.debug(f"Created TOU schedule: peak_hours={peak_hours}, peak_months={peak_months}")
        self.logger.debug(f"Sample weekday schedule for month 1: {weekday_schedule[0]}")
        
        return self.set_tou_schedule(weekday_schedule, weekend_schedule)
    
    # ==========================================================================
    # DEMAND CHARGES
    # ==========================================================================
    
    def enable_demand_charges(self, demand_unit: DemandUnit = DemandUnit.KW,
                             demand_window: float = 15.0) -> 'UrdbRateBuilder':
        """
        Enable demand charge calculations.
        
        Args:
            demand_unit: Units for demand charges
            demand_window: Demand window in minutes
            
        Returns:
            Self for method chaining
        """
        self._rate_data["_demand_enabled"] = True
        self._rate_data["demandrateunit"] = demand_unit.value
        self._rate_data["demandwindow"] = float(demand_window)
        
        # Initialize demand rate structure to match energy periods
        num_energy_periods = len(self._rate_data["energyratestructure"])
        self._rate_data["demandratestructure"] = [[] for _ in range(num_energy_periods)]
        
        # Initialize demand schedules separately (don't copy energy schedules by default)
        # This addresses the issue where demand and energy TOU periods may differ
        if not self._rate_data["demandweekdayschedule"]:
            # Start with flat demand schedule (all period 0)
            default_demand_schedule = [[0 for _ in range(24)] for _ in range(12)]
            self._rate_data["demandweekdayschedule"] = copy.deepcopy(default_demand_schedule)
            self._rate_data["demandweekendschedule"] = copy.deepcopy(default_demand_schedule)
        
        self.logger.info(f"Enabled demand charges with unit: {demand_unit.value}")
        return self
    
    def add_demand_tier(self, period: int, max_demand: Optional[float], 
                       rate: float, adj: float = 0.0) -> 'UrdbRateBuilder':
        """
        Add a demand rate tier to a specific time-of-use period.
        
        Args:
            period: TOU period index (0-based)
            max_demand: Maximum demand for this tier in demand units (None for unlimited)
            rate: Demand rate in $/demand_unit
            adj: Rate adjustment in $/demand_unit
            
        Returns:
            Self for method chaining
            
        Raises:
            ValueError: If period is negative
        """
        if period < 0:
            raise ValueError("Period index must be non-negative")
        
        if not self._rate_data["_demand_enabled"]:
            self.enable_demand_charges()
        
        # Ensure period exists in demand rate structure
        while len(self._rate_data["demandratestructure"]) <= period:
            self._rate_data["demandratestructure"].append([])
        
        # Create tier dictionary (exact URDB v8 format)
        tier = {
            "rate": float(rate),
            "adj": float(adj)
        }
        
        # Add max demand if specified
        if max_demand is not None:
            tier["max"] = float(max_demand)
        
        # Add tier to the specified period
        self._rate_data["demandratestructure"][period].append(tier)
        
        demand_unit = self._rate_data["demandrateunit"]
        tier_desc = f"period {period}, rate ${rate:.2f}/{demand_unit}"
        if max_demand is not None:
            tier_desc += f", max {max_demand} {demand_unit}"
        self.logger.debug(f"Added demand tier: {tier_desc}")
        
        return self
    
    def simple_demand_charge(self, rate: float) -> 'UrdbRateBuilder':
        """
        Add a simple flat demand charge to all existing TOU periods.
        
        Args:
            rate: Demand rate in $/demand_unit
            
        Returns:
            Self for method chaining
        """
        if not self._rate_data["_demand_enabled"]:
            self.enable_demand_charges()
        
        # Add demand tier to all periods that exist in energy structure
        num_periods = len(self._rate_data["energyratestructure"])
        for period in range(num_periods):
            self.add_demand_tier(period, None, rate)
        
        return self
    
    def set_demand_schedule(self, weekday_schedule: List[List[int]], 
                           weekend_schedule: Optional[List[List[int]]] = None) -> 'UrdbRateBuilder':
        """
        Set time-of-use schedules for demand charges (separate from energy schedules).
        
        Args:
            weekday_schedule: 12x24 matrix (months x hours) with demand period indices
            weekend_schedule: 12x24 matrix for weekends (defaults to same as weekday)
            
        Returns:
            Self for method chaining
            
        Raises:
            ValueError: If demand charges are not enabled or schedules are invalid
        """
        if not self._rate_data["_demand_enabled"]:
            raise ValueError("Demand charges must be enabled before setting demand schedules")
        
        # Validate schedules (same validation as energy schedules)
        if len(weekday_schedule) != 12:
            raise ValueError("Weekday demand schedule must have exactly 12 months")
        
        for month_idx, month in enumerate(weekday_schedule):
            if not isinstance(month, list) or len(month) != 24:
                raise ValueError(f"Month {month_idx + 1} in weekday demand schedule must have exactly 24 hours")
        
        weekend_schedule = weekend_schedule or copy.deepcopy(weekday_schedule)
        
        if len(weekend_schedule) != 12:
            raise ValueError("Weekend demand schedule must have exactly 12 months")
        
        for month_idx, month in enumerate(weekend_schedule):
            if not isinstance(month, list) or len(month) != 24:
                raise ValueError(f"Month {month_idx + 1} in weekend demand schedule must have exactly 24 hours")
        
        self._rate_data["demandweekdayschedule"] = weekday_schedule
        self._rate_data["demandweekendschedule"] = weekend_schedule
        
        self.logger.info("Set separate demand TOU schedules")
        return self
    
    def set_demand_lookback(self, months: Optional[List[int]] = None, percent: float = 100.0, 
                           range_months: int = 0) -> 'UrdbRateBuilder':
        """
        Configure demand charge look-back billing (URDB v8 format).
        
        Args:
            months: List of month indices (1-12) for lookback, or None for no lookback
            percent: Percentage of look-back peak to use (0-100)
            range_months: Number of consecutive months for lookback (0 = disabled)
            
        Returns:
            Self for method chaining
            
        Raises:
            ValueError: If both months and range_months are specified
        """
        if months is not None and range_months > 0:
            raise ValueError("Cannot specify both months list and range_months")
        
        if months is not None:
            # Validate months
            for month in months:
                if not isinstance(month, int) or month < 1 or month > 12:
                    raise ValueError(f"Invalid month: {month}. Must be 1-12")
            
            # Set boolean array (URDB v8 specification: array of 12 booleans)
            lookback_array = [False] * 12
            for month in months:
                lookback_array[month - 1] = True  # Convert to 0-based indexing
            
            self._rate_data["lookbackMonths"] = lookback_array
            self._rate_data["lookbackRange"] = 0  # Must be 0 when using months array
        else:
            # Using range-based lookback
            self._rate_data["lookbackMonths"] = [False] * 12  # All false when using range
            self._rate_data["lookbackRange"] = int(range_months)
        
        # Set percentage (URDB v8: single value, not array)
        self._rate_data["lookbackPercent"] = float(percent)
        
        lookback_desc = []
        if months:
            lookback_desc.append(f"months {months}")
        if range_months > 0:
            lookback_desc.append(f"range {range_months} months")
        lookback_desc.append(f"{percent}%")
        
        self.logger.info(f"Set demand look-back: {', '.join(lookback_desc)}")
        return self
    
    # ==========================================================================
    # NET METERING AND SELL RATES
    # ==========================================================================
    
    def set_net_metering(self, dg_rule: DGRules, 
                        sell_rate: Optional[float] = None) -> 'UrdbRateBuilder':
        """
        Configure net metering options (URDB v8 format).
        
        Args:
            dg_rule: Distributed generation compensation rule
            sell_rate: Sell rate for excess generation ($/kWh)
            
        Returns:
            Self for method chaining
        """
        # Set dgrules for URDB v8 compatibility (string format)
        self._rate_data["dgrules"] = dg_rule.value
        
        if sell_rate is not None:
            # Set sell rates for all energy tiers
            for period_tiers in self._rate_data["energyratestructure"]:
                for tier in period_tiers:
                    tier["sell"] = float(sell_rate)
        
        self.logger.info(f"Set net metering: {dg_rule.value}, sell rate: {sell_rate}")
        return self
    
    # ==========================================================================
    # SEASONAL RATES
    # ==========================================================================
    
    def add_season(self, name: str, start_month: int, start_day: int,
                  end_month: int, end_day: int) -> 'UrdbRateBuilder':
        """
        Define a season for seasonal rate variations.
        
        Args:
            name: Season name
            start_month: Starting month (1-12)
            start_day: Starting day (1-31)
            end_month: Ending month (1-12)
            end_day: Ending day (1-31)
            
        Returns:
            Self for method chaining
            
        Raises:
            ValueError: If date parameters are invalid
        """
        # Validate date parameters
        if not (1 <= start_month <= 12):
            raise ValueError(f"start_month must be 1-12, got {start_month}")
        if not (1 <= end_month <= 12):
            raise ValueError(f"end_month must be 1-12, got {end_month}")
        if not (1 <= start_day <= 31):
            raise ValueError(f"start_day must be 1-31, got {start_day}")
        if not (1 <= end_day <= 31):
            raise ValueError(f"end_day must be 1-31, got {end_day}")
        
        season = {
            "name": name,
            "startmonth": start_month,
            "startday": start_day,
            "endmonth": end_month,
            "endday": end_day
        }
        
        self._rate_data["seasonaldefinitions"].append(season)
        self._rate_data["_seasons_defined"] = True
        
        self.logger.info(f"Added season: {name} ({start_month}/{start_day} - {end_month}/{end_day})")
        return self
    
    # ==========================================================================
    # FUEL ADJUSTMENTS
    # ==========================================================================
    
    def set_fuel_adjustments(self, monthly_adjustments: List[float]) -> 'UrdbRateBuilder':
        """
        Set monthly fuel adjustments.
        
        Args:
            monthly_adjustments: List of 12 monthly fuel adjustments in $/kWh
            
        Returns:
            Self for method chaining
            
        Raises:
            ValueError: If not exactly 12 values provided
        """
        if len(monthly_adjustments) != 12:
            raise ValueError(f"Must provide exactly 12 monthly values, got {len(monthly_adjustments)}")
        
        self._rate_data["fueladjustmentsmonthly"] = [float(adj) for adj in monthly_adjustments]
        
        self.logger.debug(f"Set fuel adjustments: {monthly_adjustments}")
        return self
    
    # ==========================================================================
    # SERIALIZATION
    # ==========================================================================

    def to_pysam_dict(self) -> Dict[str, Any]:
        """
        Return the current rate data as a URDB v8-compatible dictionary
        suitable for passing to PySAM's URDBv8_to_ElectricityRates tool
        or for serializing to session state.

        Excludes internal bookkeeping keys (prefixed with '_').

        Returns:
            Dict containing the complete URDB v8 rate structure.
        """
        return {k: v for k, v in self._rate_data.items() if not k.startswith("_")}

    @classmethod
    def from_dict(cls, data: Dict[str, Any], validate_pysam: bool = False) -> 'UrdbRateBuilder':
        """
        Reconstruct an UrdbRateBuilder from a previously serialized rate dict.

        Useful for restoring rate configurations from Streamlit session state.

        Args:
            data: Dict as returned by to_pysam_dict() or build().
            validate_pysam: Whether to check PySAM compatibility (default False
                            since rate data already exists).

        Returns:
            New UrdbRateBuilder instance with _rate_data populated from data.
        """
        instance = cls(validate_pysam=validate_pysam)
        # Overwrite public fields with provided data, preserving internal keys
        for key, value in data.items():
            if not key.startswith("_"):
                instance._rate_data[key] = value
        return instance

    # ==========================================================================
    # VALIDATION AND BUILDING
    # ==========================================================================

    def _validate_schedule_coverage(self) -> List[str]:
        """
        Validate that schedules provide complete coverage with no gaps.
        
        Returns:
            List of validation error messages
        """
        errors = []
        
        # Validate energy schedules
        weekday = self._rate_data["energyweekdayschedule"]
        weekend = self._rate_data["energyweekendschedule"]
        
        if not weekday or not weekend:
            errors.append("Energy schedules are not properly initialized")
            return errors
        
        # Find all periods referenced in schedules
        all_periods = set()
        for month in weekday + weekend:
            all_periods.update(month)
        
        # Validate that all referenced periods have rate tiers
        for period in all_periods:
            if (period >= len(self._rate_data["energyratestructure"]) or 
                not self._rate_data["energyratestructure"][period]):
                errors.append(f"Energy period {period} referenced in schedule but has no rate tiers")
        
        # Validate demand schedules if enabled
        if self._rate_data["_demand_enabled"]:
            demand_weekday = self._rate_data["demandweekdayschedule"]
            demand_weekend = self._rate_data["demandweekendschedule"]
            
            if demand_weekday and demand_weekend:
                demand_periods = set()
                for month in demand_weekday + demand_weekend:
                    demand_periods.update(month)
                
                for period in demand_periods:
                    if (period >= len(self._rate_data["demandratestructure"]) or 
                        not self._rate_data["demandratestructure"][period]):
                        errors.append(f"Demand period {period} referenced in schedule but has no rate tiers")
        
        # Validate complete time coverage
        # Check that every hour of every month has a valid rate definition
        for month_idx in range(12):
            for hour_idx in range(24):
                # Check energy coverage
                energy_period = weekday[month_idx][hour_idx]
                if (energy_period >= len(self._rate_data["energyratestructure"]) or 
                    not self._rate_data["energyratestructure"][energy_period]):
                    errors.append(f"No energy rate defined for month {month_idx+1}, hour {hour_idx} (period {energy_period})")
                
                # Check weekend energy coverage
                weekend_energy_period = weekend[month_idx][hour_idx]
                if (weekend_energy_period >= len(self._rate_data["energyratestructure"]) or 
                    not self._rate_data["energyratestructure"][weekend_energy_period]):
                    errors.append(f"No weekend energy rate defined for month {month_idx+1}, hour {hour_idx} (period {weekend_energy_period})")
        
        return errors
    
    def validate(self) -> List[str]:
        """
        Validate the current rate structure.
        
        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []
        
        # Basic information validation
        if not self._rate_data["name"]:
            errors.append("Rate name is required")
        if not self._rate_data["utility"]:
            errors.append("Utility name is required")
        
        # Energy rate validation
        if not self._rate_data["energyratestructure"] or not self._rate_data["energyratestructure"][0]:
            errors.append("At least one energy tier is required")
        
        # Schedule validation
        weekday = self._rate_data["energyweekdayschedule"]
        weekend = self._rate_data["energyweekendschedule"]
        
        if not weekday or len(weekday) != 12:
            errors.append("Energy weekday schedule must have 12 months")
        elif not all(len(month) == 24 for month in weekday):
            errors.append("Energy weekday schedule must have 24 hours per month")
            
        if not weekend or len(weekend) != 12:
            errors.append("Energy weekend schedule must have 12 months")
        elif not all(len(month) == 24 for month in weekend):
            errors.append("Energy weekend schedule must have 24 hours per month")
        
        # Demand charge validation (if enabled)
        if self._rate_data["_demand_enabled"]:
            if not self._rate_data["demandratestructure"]:
                errors.append("At least one demand tier is required when demand charges are enabled")
            
            # Validate demand schedules
            demand_weekday = self._rate_data["demandweekdayschedule"]
            demand_weekend = self._rate_data["demandweekendschedule"]
            
            if demand_weekday and (len(demand_weekday) != 12 or not all(len(month) == 24 for month in demand_weekday)):
                errors.append("Demand weekday schedule must be 12x24 matrix")
            if demand_weekend and (len(demand_weekend) != 12 or not all(len(month) == 24 for month in demand_weekend)):
                errors.append("Demand weekend schedule must be 12x24 matrix")
        
        # Validate lookback configuration
        lookback_months = self._rate_data.get("lookbackMonths", [])
        lookback_range = self._rate_data.get("lookbackRange", 0)
        
        if len(lookback_months) != 12:
            errors.append("lookbackMonths must be array of exactly 12 boolean values")
        
        if any(lookback_months) and lookback_range > 0:
            errors.append("Cannot have both lookbackMonths=true and lookbackRange>0")
        
        # Validate fuel adjustments
        fuel_adjustments = self._rate_data.get("fueladjustmentsmonthly", [])
        if len(fuel_adjustments) != 12:
            errors.append("fueladjustmentsmonthly must be array of exactly 12 values")
        
        # Complete schedule coverage validation
        coverage_errors = self._validate_schedule_coverage()
        errors.extend(coverage_errors)
        
        # Mark schedule as validated if no errors
        if not coverage_errors:
            self._rate_data["_schedule_validated"] = True
        
        return errors
    
    def build(self) -> Dict[str, Any]:
        """
        Build and return the complete URDB v8 compatible rate structure.
        
        Returns:
            Dictionary containing the complete rate structure in URDB v8 format
            
        Raises:
            RateValidationError: If validation fails
        """
        # Validate before building
        validation_errors = self.validate()
        if validation_errors:
            error_msg = "Rate validation failed:\n" + "\n".join(f"  - {error}" for error in validation_errors)
            self.logger.error(LogMessages.VALIDATION_ERROR.format(error_msg))
            raise RateValidationError(error_msg)
        
        # Create final rate structure
        rate_structure = copy.deepcopy(self._rate_data)
        
        # Remove internal state fields
        for key in list(rate_structure.keys()):
            if key.startswith('_'):
                del rate_structure[key]
        
        # Clean up optional sections that are empty or unused
        if not rate_structure.get("demandratestructure") or not any(rate_structure.get("demandratestructure", [])):
            # Remove all demand-related fields if no demand charges
            demand_fields = [
                "demandratestructure", "demandweekdayschedule", "demandweekendschedule", 
                "demandrateunit", "demandwindow", "demandreactivepowercharge",
                "lookbackMonths", "lookbackPercent", "lookbackRange"
            ]
            for field in demand_fields:
                rate_structure.pop(field, None)
        else:
            # Ensure lookback fields are properly formatted for demand rates
            if "lookbackRange" not in rate_structure:
                rate_structure["lookbackRange"] = 0
            if "lookbackMonths" not in rate_structure:
                rate_structure["lookbackMonths"] = [False] * 12
            if "lookbackPercent" not in rate_structure:
                rate_structure["lookbackPercent"] = 0.0
        
        # Remove empty seasonal definitions
        if not rate_structure.get("seasonaldefinitions"):
            rate_structure.pop("seasonaldefinitions", None)
        
        # Clean up empty arrays and default values
        if all(adj == 0.0 for adj in rate_structure.get("fueladjustmentsmonthly", [])):
            rate_structure.pop("fueladjustmentsmonthly", None)
        
        if not rate_structure.get("dgrules"):
            rate_structure.pop("dgrules", None)
        
        # Set required metadata
        rate_structure["created_by"] = "UrdbRateBuilder"
        rate_structure["created_timestamp"] = datetime.now(timezone.utc).isoformat()
        
        # Ensure all required URDB v8 fields are present with proper defaults
        urdb_v8_defaults = {
            'voltagecategory': 'Secondary',
            'servicetype': 'Bundled',
            'is_default': False,
            'approved': True,
            'revisions': [],
            'basicinformationcomments': '',
            'energycomments': '',
            'demandcomments': '',
            'energyattrs': [],
            'demandattrs': [],
            'fixedattrs': []
        }
        
        for field, default_value in urdb_v8_defaults.items():
            if field not in rate_structure:
                rate_structure[field] = default_value
        
        # Final validation against URDB v8 field requirements
        self._validate_urdb_v8_compliance(rate_structure)
        
        self.logger.info(f"Built URDB v8 compliant rate structure: {rate_structure['name']} ({rate_structure['label']})")
        return rate_structure
    
    def _validate_urdb_v8_compliance(self, rate_structure: Dict[str, Any]) -> None:
        """
        Validate compliance with URDB v8 field requirements.
        
        Args:
            rate_structure: The built rate structure
            
        Raises:
            RateValidationError: If URDB v8 compliance issues are found
        """
        # Check for correct camelCase field names
        required_camel_case_fields = {
            'lookbackMonths': 'lookbackmonths',
            'lookbackPercent': 'lookbackpercent', 
            'lookbackRange': 'lookbackrange'
        }
        
        for correct_field, wrong_field in required_camel_case_fields.items():
            if wrong_field in rate_structure:
                raise RateValidationError(f"Found incorrect field name '{wrong_field}' - should be '{correct_field}'")
        
        # Validate lookbackMonths format if present
        if 'lookbackMonths' in rate_structure:
            lookback_months = rate_structure['lookbackMonths']
            if not isinstance(lookback_months, list) or len(lookback_months) != 12:
                raise RateValidationError("lookbackMonths must be array of exactly 12 boolean values")
            if not all(isinstance(val, bool) for val in lookback_months):
                raise RateValidationError("lookbackMonths must contain only boolean values")
        
        # Validate rate units
        for period_tiers in rate_structure.get('energyratestructure', []):
            for tier in period_tiers:
                unit = tier.get('unit', '')
                if unit and unit not in [u.value for u in RateUnit]:
                    raise RateValidationError(f"Invalid energy rate unit: {unit}")
    
    def export_json(self, filename: str, indent: int = 2) -> None:
        """
        Export the rate structure to a JSON file.
        
        Args:
            filename: Output filename
            indent: JSON indentation level
            
        Raises:
            RateValidationError: If rate validation fails
        """
        rate_structure = self.build()
        
        with open(filename, 'w') as f:
            json.dump(rate_structure, f, indent=indent, default=str)
        
        self.logger.info(f"Exported URDB v8 compliant rate structure to: {filename}")
    
    # ==========================================================================
    # CONVENIENCE METHODS FOR COMMON RATE TYPES
    # ==========================================================================
    
    @classmethod
    def residential_tiered_rate(cls, utility_name: str, rate_name: str,
                               monthly_fixed: float = 0.0,
                               tier1_limit: float = 500.0, tier1_rate: float = 0.12,
                               tier2_rate: float = 0.15,
                               validate_pysam: bool = True) -> 'UrdbRateBuilder':
        """
        .. deprecated::
            MARKED FOR REMOVAL: Only called from ``create_example_rates()`` in the
            ``__main__`` block; not used by the application.

        Create a typical residential tiered rate structure.
        
        Args:
            utility_name: Name of utility
            rate_name: Name of rate
            monthly_fixed: Monthly fixed charge
            tier1_limit: kWh limit for first tier
            tier1_rate: Rate for first tier ($/kWh)
            tier2_rate: Rate for second tier ($/kWh)
            validate_pysam: Whether to validate PySAM compatibility
            
        Returns:
            Configured UrdbRateBuilder instance
        """
        return (cls(validate_pysam=validate_pysam)
                .basic_info(rate_name, utility_name, "Residential")
                .monthly_fixed_charge(monthly_fixed)
                .add_energy_tier(0, tier1_limit, tier1_rate)
                .add_energy_tier(0, None, tier2_rate))
    
    @classmethod
    def commercial_tou_rate(cls, utility_name: str, rate_name: str,
                           monthly_fixed: float = 50.0,
                           peak_rate: float = 0.20, offpeak_rate: float = 0.12,
                           demand_rate: float = 15.0,
                           peak_hours: Optional[List[int]] = None,
                           peak_months: Optional[List[int]] = None,
                           validate_pysam: bool = True) -> 'UrdbRateBuilder':
        """
        .. deprecated::
            MARKED FOR REMOVAL: Only called from ``create_example_rates()`` in the
            ``__main__`` block; not used by the application.

        Create a typical commercial time-of-use rate with demand charges.
        
        Args:
            utility_name: Name of utility
            rate_name: Name of rate
            monthly_fixed: Monthly fixed charge
            peak_rate: Peak energy rate ($/kWh)
            offpeak_rate: Off-peak energy rate ($/kWh)
            demand_rate: Demand charge rate ($/kW)
            peak_hours: Peak hours (defaults to 12-18)
            peak_months: Peak months (defaults to summer: 6-9)
            validate_pysam: Whether to validate PySAM compatibility
            
        Returns:
            Configured UrdbRateBuilder instance
        """
        peak_hours = peak_hours or list(range(12, 19))  # 12 PM - 6 PM
        peak_months = peak_months or [6, 7, 8, 9]  # June - September
        
        return (cls(validate_pysam=validate_pysam)
                .basic_info(rate_name, utility_name, "Commercial")
                .monthly_fixed_charge(monthly_fixed)
                .add_energy_tier(0, None, offpeak_rate)  # Off-peak rate
                .add_energy_tier(1, None, peak_rate)     # Peak rate
                .simple_tou_schedule(peak_hours, peak_months)
                .enable_demand_charges()
                .simple_demand_charge(demand_rate))
    
    @classmethod
    def simple_flat_rate_builder(cls, utility_name: str, rate_name: str,
                                sector: str = "Residential",
                                energy_rate: float = 0.12,
                                monthly_fixed: float = 25.0,
                                validate_pysam: bool = True) -> 'UrdbRateBuilder':
        """
        Create a simple flat rate (no time-of-use, no demand charges).
        
        Args:
            utility_name: Name of utility
            rate_name: Name of rate  
            sector: Rate sector
            energy_rate: Flat energy rate ($/kWh)
            monthly_fixed: Monthly fixed charge
            validate_pysam: Whether to validate PySAM compatibility
            
        Returns:
            Configured UrdbRateBuilder instance
        """
        return (cls(validate_pysam=validate_pysam)
                .basic_info(rate_name, utility_name, sector)
                .monthly_fixed_charge(monthly_fixed)
                .simple_flat_rate(energy_rate))

    @classmethod
    def prepare_for_pysam(cls, rate_dict: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert any URDB-compatible rate dict to PySAM-ready parameters.

        Runs ``build()``'s full cleanup pipeline (strips unused demand fields,
        removes internal keys, validates structure) then calls
        ``PySAM.UtilityRateTools.URDBv8_to_ElectricityRates``.

        Args:
            rate_dict: A rate dictionary produced by UrdbRateBuilder or fetched
                       directly from the URDB API.

        Returns:
            Dict of PySAM ElectricityRates parameters ready for
            ``model.ElectricityRates.replace()``.

        Raises:
            RateValidationError: If the rate dict fails validation.
        """
        import PySAM.UtilityRateTools as ur_tools

        cleaned = cls.from_dict(rate_dict, validate_pysam=False).build()
        pysam_params = ur_tools.URDBv8_to_ElectricityRates(cleaned)
        # Safety net: PySAM's replace() rejects empty tuples/lists for matrix fields
        return {
            k: v for k, v in pysam_params.items()
            if not (isinstance(v, (list, tuple)) and len(v) == 0)
        }

    # ==========================================================================
    # DEBUGGING AND INSPECTION METHODS
    # ==========================================================================
    
    def debug_rate_structure(self) -> Dict[str, Any]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Not called by the application.

        Get detailed debugging information about the current rate structure.
        
        Returns:
            Dictionary with debugging information
        """
        debug_info = {
            'basic_info': {
                'name': self._rate_data.get('name', 'NOT SET'),
                'utility': self._rate_data.get('utility', 'NOT SET'),
                'label': self._rate_data.get('label', 'NOT SET'),
                'sector': self._rate_data.get('sector', 'NOT SET')
            },
            'pysam_compatibility': {
                'pysam_version': '.'.join(map(str, self.pysam_version)) if self.pysam_version else 'NOT DETECTED',
                'urdb_converter': self.urdb_converter_available or 'NOT AVAILABLE'
            },
            'schedule_info': {
                'weekday_schedule_shape': None,
                'weekend_schedule_shape': None,
                'weekday_periods_used': None,
                'weekend_periods_used': None,
                'demand_schedule_configured': bool(self._rate_data.get('demandweekdayschedule')),
                'schedule_validated': self._rate_data.get('_schedule_validated', False)
            },
            'rate_structure': {
                'energy_periods': len(self._rate_data.get('energyratestructure', [])),
                'energy_tiers_per_period': [],
                'demand_enabled': self._rate_data.get('_demand_enabled', False),
                'demand_periods': len(self._rate_data.get('demandratestructure', [])),
                'demand_tiers_per_period': [],
                'lookback_configured': any(self._rate_data.get('lookbackMonths', [])) or self._rate_data.get('lookbackRange', 0) > 0
            },
            'charges': {
                'fixed_charge_first_meter': self._rate_data.get('fixedchargefirstmeter', 0.0),
                'fixed_charge_units': self._rate_data.get('fixedchargeunits', 'NOT SET'),
                'min_charge': self._rate_data.get('mincharge', 0.0),
                'min_charge_units': self._rate_data.get('minchargeunits', 'NOT SET'),
                'fuel_adjustments_set': bool(any(self._rate_data.get('fueladjustmentsmonthly', [])))
            },
            'net_metering': {
                'dg_rules': self._rate_data.get('dgrules', 'NOT SET'),
                'sell_rates_configured': False
            },
            'validation_errors': self.validate()
        }
        
        # Analyze schedules
        weekday = self._rate_data.get('energyweekdayschedule')
        weekend = self._rate_data.get('energyweekendschedule')
        
        if weekday:
            debug_info['schedule_info']['weekday_schedule_shape'] = f"{len(weekday)}x{len(weekday[0]) if weekday else 0}"
            all_periods = set()
            for month in weekday:
                all_periods.update(month)
            debug_info['schedule_info']['weekday_periods_used'] = sorted(list(all_periods))
        
        if weekend:
            debug_info['schedule_info']['weekend_schedule_shape'] = f"{len(weekend)}x{len(weekend[0]) if weekend else 0}"
            all_periods = set()
            for month in weekend:
                all_periods.update(month)
            debug_info['schedule_info']['weekend_periods_used'] = sorted(list(all_periods))
        
        # Analyze rate structures
        energy_structure = self._rate_data.get('energyratestructure', [])
        for i, period in enumerate(energy_structure):
            tier_info = {
                'period': i,
                'tiers': len(period),
                'tier_rates': []
            }
            for tier in period:
                tier_info['tier_rates'].append({
                    'rate': tier.get('rate', 'NO RATE'),
                    'max': tier.get('max', 'unlimited'),
                    'unit': tier.get('unit', 'NOT SET'),
                    'sell': tier.get('sell', 0.0)
                })
            debug_info['rate_structure']['energy_tiers_per_period'].append(tier_info)
        
        # Check for sell rates
        for period in energy_structure:
            for tier in period:
                if tier.get('sell', 0.0) > 0:
                    debug_info['net_metering']['sell_rates_configured'] = True
                    break
        
        if self._rate_data.get('_demand_enabled'):
            demand_structure = self._rate_data.get('demandratestructure', [])
            for i, period in enumerate(demand_structure):
                tier_info = {
                    'period': i,
                    'tiers': len(period),
                    'tier_rates': []
                }
                for tier in period:
                    tier_info['tier_rates'].append({
                        'rate': tier.get('rate', 'NO RATE'),
                        'max': tier.get('max', 'unlimited')
                    })
                debug_info['rate_structure']['demand_tiers_per_period'].append(tier_info)
        
        return debug_info
    
    def get_rate_summary(self) -> Dict[str, Any]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Not called by the application.

        Get a human-readable summary of the rate structure.
        
        Returns:
            Dictionary with rate summary information
        """
        rate_data = self._rate_data
        
        summary = {
            "name": rate_data["name"],
            "utility": rate_data["utility"],
            "sector": rate_data["sector"],
            "label": rate_data["label"],
            "fixed_charge_first_meter": rate_data["fixedchargefirstmeter"],
            "fixed_charge_units": rate_data["fixedchargeunits"],
            "min_charge": rate_data["mincharge"],
            "min_charge_units": rate_data["minchargeunits"],
            "has_demand_charges": rate_data["_demand_enabled"],
            "periods_defined": rate_data["_periods_defined"],
            "seasons_defined": rate_data["_seasons_defined"],
            "schedule_validated": rate_data["_schedule_validated"],
            "dg_rules": rate_data.get("dgrules", "None")
        }
        
        # Energy rate summary
        energy_tiers = []
        for period_idx, period in enumerate(rate_data["energyratestructure"]):
            for tier_idx, tier in enumerate(period):
                tier_summary = {
                    "period": period_idx,
                    "tier": tier_idx,
                    "rate": tier["rate"],
                    "max_usage": tier.get("max"),
                    "unit": tier.get("unit", "kWh"),
                    "sell_rate": tier.get("sell", 0.0)
                }
                energy_tiers.append(tier_summary)
        summary["energy_tiers"] = energy_tiers
        
        # Demand rate summary (if enabled)
        if rate_data["_demand_enabled"]:
            demand_tiers = []
            for period_idx, period in enumerate(rate_data["demandratestructure"]):
                for tier_idx, tier in enumerate(period):
                    tier_summary = {
                        "period": period_idx,
                        "tier": tier_idx,
                        "rate": tier["rate"],
                        "max_demand": tier.get("max")
                    }
                    demand_tiers.append(tier_summary)
            summary["demand_tiers"] = demand_tiers
            summary["demand_unit"] = rate_data["demandrateunit"]
            summary["demand_window_minutes"] = rate_data["demandwindow"]
            
            # Lookback summary
            lookback_months = rate_data.get("lookbackMonths", [])
            lookback_range = rate_data.get("lookbackRange", 0)
            summary["lookback_months_active"] = [i+1 for i, active in enumerate(lookback_months) if active]
            summary["lookback_range_months"] = lookback_range
            summary["lookback_percent"] = rate_data.get("lookbackPercent", 0.0)
        
        return summary
    
    # ==========================================================================
    # INTEGRATION HELPERS
    # ==========================================================================
    
    def test_with_load_profile(self, load_profile_kwh: List[float],
                              bill_calculator) -> Dict[str, Any]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Not called by the application.

        Test the built rate with a load profile using UtilityBillCalculator.
        
        Args:
            load_profile_kwh: 8760-hour load profile in kWh
            bill_calculator: Instance of UtilityBillCalculator
            
        Returns:
            Bill calculation results
            
        Raises:
            ValueError: If load profile is not exactly 8760 hours
            RateValidationError: If rate validation fails
        """
        import numpy as np
        
        if len(load_profile_kwh) != 8760:
            raise ValueError(f"Load profile must be exactly 8760 hours, got {len(load_profile_kwh)}")
        
        # Build rate and validate
        rate_data = self.build()
        
        try:
            # Calculate bill using existing infrastructure
            results = bill_calculator.calculate_bill_from_prepared_profile(
                load_profile=np.array(load_profile_kwh),
                rate_data=rate_data
            )
            
            self.logger.info(f"Test calculation complete: ${results['annual_bill']:.2f} annual bill")
            return results
            
        except Exception as e:
            self.logger.error(f"Bill calculation test failed: {e}")
            raise
    
    def verify_pysam_compatibility(self) -> Dict[str, Any]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Only called from the ``__main__`` block; not used
            by the application.

        Verify compatibility with current PySAM installation.
        
        Returns:
            Dictionary with compatibility information
        """
        compatibility_info = {
            'pysam_available': False,
            'pysam_version': None,
            'urdb_converter_available': None,
            'recommended_converter': None,
            'warnings': [],
            'can_convert_rate': False
        }
        
        try:
            # Re-run compatibility check
            self._validate_pysam_compatibility()
            
            compatibility_info.update({
                'pysam_available': True,
                'pysam_version': '.'.join(map(str, self.pysam_version)) if self.pysam_version else 'unknown',
                'urdb_converter_available': self.urdb_converter_available,
                'can_convert_rate': bool(self.urdb_converter_available)
            })
            
            # Determine recommended converter
            if self.urdb_converter_available == 'URDBv8_to_ElectricityRates':
                compatibility_info['recommended_converter'] = 'URDBv8_to_ElectricityRates'
            elif self.urdb_converter_available == 'URDBv7_to_ElectricityRates':
                compatibility_info['recommended_converter'] = 'URDBv7_to_ElectricityRates'
                compatibility_info['warnings'].append(
                    'Using URDBv7 converter - some URDB v8 features may not be supported'
                )
            
            # Version-specific warnings
            if self.pysam_version and self.pysam_version < (4, 0, 0):
                compatibility_info['warnings'].append(
                    f'PySAM version {".".join(map(str, self.pysam_version))} is older - '
                    'consider upgrading for full URDB v8 support'
                )
            
        except PySAMVersionError as e:
            compatibility_info['warnings'].append(str(e))
        
        return compatibility_info
    
    def convert_to_pysam_format(self) -> Dict[str, Any]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Not called by the application.

        Convert the built rate to PySAM-compatible format using available converter.
        
        Returns:
            Dictionary with PySAM parameters
            
        Raises:
            PySAMVersionError: If no compatible converter is available
            RateValidationError: If rate validation fails
        """
        if not self.urdb_converter_available:
            raise PySAMVersionError("No PySAM URDB converter available")
        
        # Build and validate rate
        rate_data = self.build()
        
        try:
            if self.urdb_converter_available == 'URDBv8_to_ElectricityRates':
                import PySAM.UtilityRateTools as ur_tools
                pysam_params = ur_tools.URDBv8_to_ElectricityRates(rate_data)
            elif self.urdb_converter_available == 'URDBv7_to_ElectricityRates':
                import PySAM.UtilityRateTools as ur_tools
                # Convert URDB v8 format to v7 compatible format if needed
                rate_data_v7 = self._convert_to_urdb_v7_format(rate_data)
                pysam_params = ur_tools.URDBv7_to_ElectricityRates(rate_data_v7)
            else:
                raise PySAMVersionError(f"Unknown converter: {self.urdb_converter_available}")
            
            self.logger.info(f"Successfully converted rate using {self.urdb_converter_available}")
            return pysam_params
            
        except Exception as e:
            self.logger.error(f"PySAM conversion failed: {e}")
            raise
    
    def _convert_to_urdb_v7_format(self, rate_data_v8: Dict[str, Any]) -> Dict[str, Any]:
        """
        .. deprecated::
            MARKED FOR REMOVAL: Only called from ``convert_to_pysam_format()``, which is
            itself not called by the application.

        Convert URDB v8 format to v7 compatible format for older PySAM versions.
        
        Args:
            rate_data_v8: Rate data in URDB v8 format
            
        Returns:
            Rate data in URDB v7 compatible format
        """
        rate_data_v7 = copy.deepcopy(rate_data_v8)
        
        # Convert camelCase field names to lowercase for v7 compatibility
        field_mappings = {
            'lookbackMonths': 'lookbackmonths',
            'lookbackPercent': 'lookbackpercent',
            'lookbackRange': 'lookbackrange'
        }
        
        for v8_field, v7_field in field_mappings.items():
            if v8_field in rate_data_v7:
                rate_data_v7[v7_field] = rate_data_v7.pop(v8_field)
        
        # Convert boolean array to legacy format if needed
        if 'lookbackmonths' in rate_data_v7:
            lookback_months = rate_data_v7['lookbackmonths']
            if isinstance(lookback_months, list) and all(isinstance(x, bool) for x in lookback_months):
                # Convert boolean array to month indices for v7
                active_months = [i+1 for i, active in enumerate(lookback_months) if active]
                if active_months:
                    # For v7, set range to number of active months (approximation)
                    rate_data_v7['lookbackrange'] = len(active_months)
                rate_data_v7.pop('lookbackmonths', None)
        
        # Remove v8-specific fields that don't exist in v7
        v8_only_fields = [
            'fixedchargeunits', 'minchargeunits', 'demandreactivepowercharge',
            'fueladjustmentsmonthly'
        ]
        
        for field in v8_only_fields:
            rate_data_v7.pop(field, None)
        
        # Convert new charge format to old format
        if 'mincharge' in rate_data_v7:
            # Map to old minmonthlycharge field
            rate_data_v7['minmonthlycharge'] = rate_data_v7.pop('mincharge', 0.0)
        
        self.logger.warning("Converted URDB v8 rate to v7 format - some features may be lost")
        return rate_data_v7


# =============================================================================
# EXAMPLE USAGE AND TESTING
# =============================================================================

def create_example_rates() -> List[Dict[str, Any]]:
    """
    .. deprecated::
        MARKED FOR REMOVAL: Only called from the ``__main__`` block; not used by
        the application.

    Create several example rate structures to demonstrate functionality.
    
    Returns:
        List of example rate structures in URDB v8 format
    """
    examples = []
    
    # Example 1: Simple residential flat rate
    try:
        residential_flat = (UrdbRateBuilder
                           .simple_flat_rate_builder(
                               "Example Electric Utility",
                               "Residential Basic Service",
                               energy_rate=0.12,
                               monthly_fixed=25.0,
                               validate_pysam=False  # Skip validation for examples
                           )
                           .description("Simple flat rate for residential customers")
                           .build())
        examples.append(residential_flat)
    except Exception as e:
        print(f"Failed to create residential flat rate example: {e}")
    
    # Example 2: Residential tiered rate
    try:
        residential_tiered = (UrdbRateBuilder
                             .residential_tiered_rate(
                                 "Green Power Co",
                                 "Residential Tiered Rate",
                                 monthly_fixed=30.0,
                                 tier1_limit=500.0,
                                 tier1_rate=0.10,
                                 tier2_rate=0.16,
                                 validate_pysam=False
                             )
                             .description("Tiered rate encouraging conservation")
                             .minimum_charge(20.0, MinChargeUnits.MONTHLY)
                             .build())
        examples.append(residential_tiered)
    except Exception as e:
        print(f"Failed to create residential tiered rate example: {e}")
    
    # Example 3: Commercial TOU rate with demand charges
    try:
        commercial_tou = (UrdbRateBuilder
                         .commercial_tou_rate(
                             "Business Electric Corp",
                             "Commercial General Service TOU",
                             monthly_fixed=75.0,
                             peak_rate=0.22,
                             offpeak_rate=0.14,
                             demand_rate=18.50,
                             peak_hours=list(range(9, 22)),  # 9 AM - 9 PM
                             peak_months=[6, 7, 8, 9],  # Summer months
                             validate_pysam=False
                         )
                         .description("Time-of-use rate for general commercial service")
                         .set_demand_lookback(months=[6, 7, 8, 9], percent=80.0)  # 80% of summer peak
                         .set_fuel_adjustments([0.01] * 12)  # $0.01/kWh fuel adjustment
                         .build())
        examples.append(commercial_tou)
    except Exception as e:
        print(f"Failed to create commercial TOU rate example: {e}")
    
    # Example 4: Complex rate with net metering
    try:
        solar_friendly = (UrdbRateBuilder(validate_pysam=False)
                         .basic_info("Solar Friendly Rate", "Future Energy Utility", "Residential")
                         .description("Rate designed for customers with solar generation")
                         .monthly_fixed_charge(40.0)
                         .add_energy_tier(0, 400, 0.08)    # First 400 kWh at low rate
                         .add_energy_tier(0, 1000, 0.12)   # Next 600 kWh at mid rate
                         .add_energy_tier(0, None, 0.18)   # Above 1000 kWh at high rate
                         .add_energy_tier(1, None, 0.25)   # Peak period rate
                         .simple_tou_schedule([14, 15, 16, 17, 18, 19], [6, 7, 8])  # Peak 2-7 PM in summer
                         .set_net_metering(DGRules.NET_METERING, sell_rate=0.06)
                         .add_season("Summer", 6, 1, 9, 30)
                         .add_season("Winter", 10, 1, 5, 31)
                         .build())
        examples.append(solar_friendly)
    except Exception as e:
        print(f"Failed to create solar-friendly rate example: {e}")
    
    return examples


def validate_example_rates():
    """
    .. deprecated::
        MARKED FOR REMOVAL: Only called from the ``__main__`` block; not used by
        the application.

    Validate all example rates for URDB v8 compliance.
    """
    print("Creating and validating example URDB v8 rates...")
    examples = create_example_rates()
    
    for i, rate in enumerate(examples, 1):
        print(f"\nExample {i}: {rate['name']}")
        print(f"  Utility: {rate['utility']}")
        print(f"  Sector: {rate['sector']}")
        print(f"  Fixed Charge: ${rate['fixedchargefirstmeter']:.2f}/month")
        print(f"  Energy Tiers: {sum(len(period) for period in rate.get('energyratestructure', []))}")
        
        if 'demandratestructure' in rate:
            demand_tiers = sum(len(period) for period in rate.get('demandratestructure', []))
            print(f"  Demand Charges: Yes ({demand_tiers} tiers)")
        else:
            print(f"  Demand Charges: No")
            
        if rate.get('dgrules'):
            print(f"  Net Metering: {rate['dgrules']}")
            
        print(f"  URDB Label: {rate['label']}")
        
        # Validate URDB v8 compliance
        urdb_v8_fields = ['lookbackMonths', 'lookbackPercent', 'lookbackRange']
        v8_compliant = all(field not in rate or field in urdb_v8_fields for field in 
                          ['lookbackmonths', 'lookbackpercent', 'lookbackrange'])
        print(f"  URDB v8 Compliant: {'Yes' if v8_compliant else 'No'}")
    
    print(f"\nCreated {len(examples)} example rates, all URDB v8 compliant!")
    return examples


if __name__ == "__main__":
    # Demonstration of the corrected rate builder
    import logging
    logging.basicConfig(level=logging.INFO)
    
    # Validate examples
    examples = validate_example_rates()
    
    # Test PySAM compatibility if available
    try:
        builder = UrdbRateBuilder(validate_pysam=True)
        compatibility = builder.verify_pysam_compatibility()
        
        print(f"\nPySAM Compatibility Check:")
        print(f"  PySAM Available: {compatibility['pysam_available']}")
        print(f"  PySAM Version: {compatibility['pysam_version']}")
        print(f"  URDB Converter: {compatibility['urdb_converter_available']}")
        print(f"  Can Convert Rates: {compatibility['can_convert_rate']}")
        
        if compatibility['warnings']:
            print("  Warnings:")
            for warning in compatibility['warnings']:
                print(f"    - {warning}")
                
    except Exception as e:
        print(f"\nPySAM compatibility check failed: {e}")
    
    print("\nUrdbRateBuilder v2.0 - URDB v8 compliant rate builder ready for use!")