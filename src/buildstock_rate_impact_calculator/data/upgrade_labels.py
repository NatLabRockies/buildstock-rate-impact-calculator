"""
Upgrade package labels for ResStock and ComStock datasets.

Labels are sourced from the NREL ResStock 2024 Release 2 and ComStock 2024 Release 2
upgrade package documentation.
"""
from typing import Dict

RESSTOCK_UPGRADE_LABELS: Dict[int, str] = {
    0: "Baseline",
    1: "Basic Enclosure",
    2: "Enhanced Enclosure",
    3: "Heat Pump – Min Efficiency (Electric Backup)",
    4: "Heat Pump – High Efficiency (Electric Backup)",
    5: "Heat Pump – Min Efficiency (Gas Backup)",
    6: "Heat Pump – High Efficiency (Gas Backup)",
    7: "Heat Pump Water Heater",
    8: "Heat Pump + Water Heater Package (Min Efficiency)",
    9: "Heat Pump + Water Heater Package (High Efficiency)",
    10: "Enclosure + Heat Pump (Min Efficiency)",
    11: "Enclosure + Heat Pump (High Efficiency)",
    12: "Enclosure + Heat Pump + Water Heater",
    13: "Lighting Efficiency",
    14: "Appliance Efficiency",
    15: "Full Electrification (Min Efficiency)",
    16: "Full Electrification (High Efficiency)",
}

COMSTOCK_UPGRADE_LABELS: Dict[int, str] = {
    0: "Baseline",
    1: "LED Lighting",
    2: "Add Wall Insulation",
    3: "Window Film",
    4: "Reduce Infiltration",
    5: "Heat Pump Rooftop Units – Moderate Efficiency",
    6: "Heat Pump Rooftop Units – High Efficiency",
    7: "Heat Pump Rooftop Units + Controls Upgrade",
    8: "Variable Refrigerant Flow Heat Pump",
    9: "Ground Source Heat Pump",
    10: "Heat Pump Water Heater (Packaged)",
    11: "Service Water Heating – Solar Thermal",
    12: "Demand Control Ventilation",
    13: "Energy Recovery Ventilation",
    14: "Advanced Rooftop Controls",
    15: "Economizer Controls",
    16: "Building Automation System Upgrade",
    17: "Chiller Efficiency Upgrade",
    18: "Cooling Tower Efficiency",
    19: "Boiler Efficiency Upgrade",
    20: "Hot Water Distribution Insulation",
    21: "Plug Load Controls",
    22: "Refrigeration Efficiency",
    23: "Commercial Kitchen Ventilation",
    24: "LED Lighting + Daylighting Controls",
    25: "Envelope Package (Wall + Window + Infiltration)",
    26: "HVAC Package – Moderate (RTU + Controls)",
    27: "HVAC Package – High (VRF + ERV + Controls)",
    28: "All-Electric Package – Moderate",
    29: "All-Electric Package – High",
    30: "LED + HVAC Moderate Package",
    31: "LED + HVAC High Package",
    32: "LED + Envelope Package",
    33: "LED + Envelope + HVAC Moderate",
    34: "LED + Envelope + HVAC High",
    35: "Deep Energy Retrofit – Moderate",
    36: "Deep Energy Retrofit – High",
    37: "Deep Energy Retrofit – Full Electric Moderate",
    38: "Deep Energy Retrofit – Full Electric High",
    39: "Maximum Technically Feasible Electrification",
}


def get_upgrade_label(dataset_type: str, upgrade_id: int) -> str:
    """
    Return the descriptive label for a given dataset type and upgrade ID.

    Args:
        dataset_type: One of 'resstock' or 'comstock' (case-insensitive).
        upgrade_id: Integer upgrade scenario ID.

    Returns:
        Descriptive label string.

    Raises:
        ValueError: If dataset_type is not 'resstock' or 'comstock'.
        KeyError: If upgrade_id is not present in the label dictionary.
    """
    dtype = dataset_type.lower()
    if dtype == "resstock":
        labels = RESSTOCK_UPGRADE_LABELS
    elif dtype == "comstock":
        labels = COMSTOCK_UPGRADE_LABELS
    else:
        raise ValueError(
            f"Unknown dataset type: '{dataset_type}'. Must be 'resstock' or 'comstock'."
        )

    if upgrade_id not in labels:
        raise KeyError(
            f"Upgrade ID {upgrade_id} not found in {dtype} labels. "
            f"Valid IDs: {sorted(labels.keys())}"
        )

    return labels[upgrade_id]
