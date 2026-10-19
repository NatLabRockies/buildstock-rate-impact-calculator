"""
Map selector component for geographic filtering.

Renders a hierarchical Folium map:
- National view: state choropleth (click to select states or zoom in)
- State view: county or PUMA boundary layer within the selected state
  (ComStock: additionally offers Census Tract via CSV upload)

Uses streamlit-folium for rendering.
"""

from __future__ import annotations

import csv
import json
import re
from importlib.resources import files

import folium
import streamlit as st

try:
    from streamlit_folium import st_folium

    _FOLIUM_AVAILABLE = True
except ImportError:
    _FOLIUM_AVAILABLE = False


# ── GeoJSON data sources ──────────────────────────────────────────────────────
# All boundary layers are bundled as package data.
_DATA_PKG = "buildstock_rate_impact_calculator.data"
_STATES_GEOJSON_FILE = "us_states.geojson"
_COUNTY_GEOJSON_FILE = "cb_2019_us_county_500k.geojson"
_PUMA_GEOJSON_FILE = "cb_2019_us_puma10_500k.geojson"

# Keyless basemap (xyzservices name). CARTO tiles now require an API key.
_BASEMAP_TILES = "Esri.WorldGrayCanvas"

# Map US state names (from GeoJSON) → FIPS abbreviations used in BuildStock
_STATE_NAME_TO_ABBR: dict[str, str] = {
    "Alabama": "AL",
    "Alaska": "AK",
    "Arizona": "AZ",
    "Arkansas": "AR",
    "California": "CA",
    "Colorado": "CO",
    "Connecticut": "CT",
    "Delaware": "DE",
    "Florida": "FL",
    "Georgia": "GA",
    "Hawaii": "HI",
    "Idaho": "ID",
    "Illinois": "IL",
    "Indiana": "IN",
    "Iowa": "IA",
    "Kansas": "KS",
    "Kentucky": "KY",
    "Louisiana": "LA",
    "Maine": "ME",
    "Maryland": "MD",
    "Massachusetts": "MA",
    "Michigan": "MI",
    "Minnesota": "MN",
    "Mississippi": "MS",
    "Missouri": "MO",
    "Montana": "MT",
    "Nebraska": "NE",
    "Nevada": "NV",
    "New Hampshire": "NH",
    "New Jersey": "NJ",
    "New Mexico": "NM",
    "New York": "NY",
    "North Carolina": "NC",
    "North Dakota": "ND",
    "Ohio": "OH",
    "Oklahoma": "OK",
    "Oregon": "OR",
    "Pennsylvania": "PA",
    "Rhode Island": "RI",
    "South Carolina": "SC",
    "South Dakota": "SD",
    "Tennessee": "TN",
    "Texas": "TX",
    "Utah": "UT",
    "Vermont": "VT",
    "Virginia": "VA",
    "Washington": "WA",
    "West Virginia": "WV",
    "Wisconsin": "WI",
    "Wyoming": "WY",
    "District of Columbia": "DC",
}

# State abbreviation → 2-digit FIPS code
_STATE_ABBR_TO_FIPS: dict[str, str] = {
    "AL": "01",
    "AK": "02",
    "AZ": "04",
    "AR": "05",
    "CA": "06",
    "CO": "08",
    "CT": "09",
    "DE": "10",
    "DC": "11",
    "FL": "12",
    "GA": "13",
    "HI": "15",
    "ID": "16",
    "IL": "17",
    "IN": "18",
    "IA": "19",
    "KS": "20",
    "KY": "21",
    "LA": "22",
    "ME": "23",
    "MD": "24",
    "MA": "25",
    "MI": "26",
    "MN": "27",
    "MS": "28",
    "MO": "29",
    "MT": "30",
    "NE": "31",
    "NV": "32",
    "NH": "33",
    "NJ": "34",
    "NM": "35",
    "NY": "36",
    "NC": "37",
    "ND": "38",
    "OH": "39",
    "OK": "40",
    "OR": "41",
    "PA": "42",
    "RI": "44",
    "SC": "45",
    "SD": "46",
    "TN": "47",
    "TX": "48",
    "UT": "49",
    "VT": "50",
    "VA": "51",
    "WA": "53",
    "WV": "54",
    "WI": "55",
    "WY": "56",
}


# ── Package-data GeoJSON loaders ──────────────────────────────────────────────


@st.cache_data
def _load_utility_companies_csv() -> list[dict]:
    """Load bundled utility_companies.csv (label, uri, eia_utility_id)."""
    data_path = files(_DATA_PKG).joinpath("utility_companies.csv")
    with data_path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


@st.cache_data
def _load_eia_to_county_csv() -> list[dict]:
    """Load bundled eia_to_county.csv (eia_utility_id, STATEFP, COUNTYFP)."""
    data_path = files(_DATA_PKG).joinpath("eia_to_county.csv")
    with data_path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


@st.cache_data
def _load_county_geojson() -> dict:
    data_path = files(_DATA_PKG).joinpath(_COUNTY_GEOJSON_FILE)
    with data_path.open("r", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data
def _load_puma_geojson() -> dict:
    data_path = files(_DATA_PKG).joinpath(_PUMA_GEOJSON_FILE)
    with data_path.open("r", encoding="utf-8") as f:
        return json.load(f)


@st.cache_data
def _load_states_geojson() -> dict:
    """Load bundled US states GeoJSON and augment with state abbreviations."""
    data_path = files(_DATA_PKG).joinpath(_STATES_GEOJSON_FILE)
    with data_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    for feat in data["features"]:
        name = feat["properties"].get("name", "")
        feat["properties"]["_abbr"] = _STATE_NAME_TO_ABBR.get(name, "")
    return data


# ── GISJOIN helpers ───────────────────────────────────────────────────────────


def _county_gisjoin(props: dict) -> str:
    return f"G{props['STATEFP']}0{props['COUNTYFP']}0"


def _puma_gisjoin(props: dict) -> str:
    return f"G{props['STATEFP10']}0{props['PUMACE10']}"


def _filter_features_by_states(
    geojson: dict,
    statefps: list[str],
    fips_key: str,
    gisjoin_fn,
) -> dict:
    """Return a new FeatureCollection filtered to *multiple* state FIPS codes, with _gisjoin added."""
    statefp_set = set(statefps)
    filtered = []
    for feat in geojson["features"]:
        if feat["properties"].get(fips_key) in statefp_set:
            new_props = dict(feat["properties"])
            new_props["_gisjoin"] = gisjoin_fn(new_props)
            filtered.append({**feat, "properties": new_props})
    return {"type": "FeatureCollection", "features": filtered}


def _flatten_coords(geometry: dict) -> list[list[float]]:
    """Flatten a GeoJSON geometry to a flat list of [lng, lat] coordinate pairs."""
    geom_type = geometry.get("type", "")
    coords = geometry.get("coordinates", [])
    if geom_type == "Point":
        return [coords]
    if geom_type in ("LineString", "MultiPoint"):
        return list(coords)
    if geom_type in ("Polygon", "MultiLineString"):
        result: list = []
        for ring in coords:
            result.extend(ring)
        return result
    if geom_type == "MultiPolygon":
        result = []
        for polygon in coords:
            for ring in polygon:
                result.extend(ring)
        return result
    return []


def _bbox_center(features: list[dict]) -> tuple[float, float]:
    """Return the (lat, lng) center of the bounding box of a list of features."""
    lngs: list[float] = []
    lats: list[float] = []
    for feat in features:
        for coord in _flatten_coords(feat["geometry"]):
            lngs.append(coord[0])
            lats.append(coord[1])
    return (min(lats) + max(lats)) / 2, (min(lngs) + max(lngs)) / 2


def _zoom_for_bbox(
    lat_min: float, lat_max: float, lng_min: float, lng_max: float
) -> int:
    """Estimate an appropriate Leaflet zoom level to fit a geographic bounding box.

    streamlit-folium reads ``zoom_start`` from the Folium map object directly
    when using ``feature_group_to_add`` and does not execute the ``fitBounds``
    JavaScript that ``m.fit_bounds()`` injects.  This helper derives a zoom
    level from the degree span of the bounding box so that ``zoom_start`` is
    set to a sensible value instead of a hardcoded constant.
    """
    max_span = max(lat_max - lat_min, lng_max - lng_min)
    if max_span <= 0:
        return 10
    if max_span < 1:
        return 10
    if max_span < 2:
        return 9
    if max_span < 4:
        return 8
    if max_span < 8:
        return 7
    if max_span < 16:
        return 6
    if max_span < 32:
        return 5
    return 4


# ── Static layer styles (defined once at module level) ───────────────────────
# Using module-level constants avoids re-creating dicts on every fragment rerun
# and eliminates per-feature conditional branching in the style callbacks.
_STYLE_SEL = {
    "fillColor": "#2196F3",
    "color": "#1565C0",
    "weight": 2,
    "fillOpacity": 0.5,
}
_STYLE_UNSEL = {
    "fillColor": "#ECEFF1",
    "color": "#78909C",
    "weight": 1,
    "fillOpacity": 0.3,
}
_STYLE_SEL_SUB = {
    "fillColor": "#2196F3",
    "color": "#1565C0",
    "weight": 2,
    "fillOpacity": 0.6,
}
_STYLE_UNSEL_SUB = {
    "fillColor": "#ECEFF1",
    "color": "#78909C",
    "weight": 1,
    "fillOpacity": 0.25,
}


# ── Public component ──────────────────────────────────────────────────────────


def render_map_selector(
    dataset_type: str,
    initial_states: list[str],
    initial_county_ids: list[str],
    initial_puma_ids: list[str],
) -> dict:
    """
    Render an interactive Folium map for geographic selection.

    **State view** shows a national choropleth with a multiselect widget.
    After states are chosen, *Select Counties* / *Select PUMAs* buttons drill
    into a sub-level view covering all selected states.  Selections for
    counties and PUMAs are tracked independently (mixed mode).

    Args:
        dataset_type: ``'resstock'`` or ``'comstock'``
        initial_states: Previously saved state abbreviations.
        initial_county_ids: Previously saved county GISJOIN IDs.
        initial_puma_ids: Previously saved PUMA GISJOIN IDs.

    Returns:
        Dict with ``selected_states``, ``selected_county_ids``,
        ``selected_puma_ids`` reflecting the current map UI selections
        (not yet saved to AppState).
    """
    if not _FOLIUM_AVAILABLE:
        st.error("streamlit-folium is not installed. Run: pixi install")
        return {
            "selected_states": initial_states,
            "selected_county_ids": initial_county_ids,
            "selected_puma_ids": initial_puma_ids,
        }

    # PUMA selection is not supported for ComStock (county-only metadata).
    allow_puma = (dataset_type or "").lower() != "comstock"

    # ── Initialise session state ──────────────────────────────────────────────
    if "_map_view_mode" not in st.session_state:
        st.session_state["_map_view_mode"] = "state"
    if "_map_county_sel" not in st.session_state:
        st.session_state["_map_county_sel"] = set(initial_county_ids)
    if "_map_puma_sel" not in st.session_state:
        st.session_state["_map_puma_sel"] = set(initial_puma_ids)

    if not allow_puma and st.session_state["_map_view_mode"] == "puma":
        # e.g. dataset switched to ComStock while the PUMA view was open
        st.session_state["_map_view_mode"] = "state"

    mode = st.session_state["_map_view_mode"]

    if mode == "state":
        _render_state_selection_view(initial_states, allow_puma)
        selected_states = st.session_state.get("_map_selected_states", initial_states)
        return {
            "selected_states": selected_states,
            "selected_county_ids": list(st.session_state["_map_county_sel"]),
            "selected_puma_ids": list(st.session_state["_map_puma_sel"]),
        }

    # county or puma sub-level mode
    selected_states = st.session_state.get("_map_selected_states", initial_states)
    _render_sub_level_view(selected_states, mode)
    return {
        "selected_states": selected_states,
        "selected_county_ids": list(st.session_state["_map_county_sel"]),
        "selected_puma_ids": list(st.session_state["_map_puma_sel"]),
    }


# ── Internal renderers ────────────────────────────────────────────────────────


@st.fragment
def _render_state_selection_view(
    current_states: list[str], allow_puma: bool = True
) -> None:
    """Render the national state choropleth with click-to-toggle selection."""

    sel_key = "_map_states_sel"
    click_key = "_map_states_last_click"

    if sel_key not in st.session_state:
        st.session_state[sel_key] = set(current_states)

    selected_set: set[str] = st.session_state[sel_key]

    geojson = _load_states_geojson()

    _map_version = st.session_state.get("_map_version", 0)
    _map_key = f"national_map_v{_map_version}"

    # Freeze the initial viewport in session state the first time this map
    # version is rendered.  Subsequent fragment reruns (state clicks) always
    # pass the same location/zoom_start to folium.Map, so the serialized HTML
    # is identical and streamlit-folium keeps the existing Leaflet instance
    # without re-initialising — preventing unwanted re-zoom on every click.
    _vp_key = f"_national_map_vp_v{_map_version}"
    if _vp_key not in st.session_state:
        if selected_set:
            _sel_init = [
                f
                for f in geojson["features"]
                if f["properties"].get("_abbr", "") in selected_set
            ]
            _coords: list[list[float]] = []
            for _f in _sel_init:
                _coords.extend(_flatten_coords(_f["geometry"]))
            if _coords:
                _lats = [c[1] for c in _coords]
                _lngs = [c[0] for c in _coords]
                st.session_state[_vp_key] = {
                    "lat": (min(_lats) + max(_lats)) / 2,
                    "lng": (min(_lngs) + max(_lngs)) / 2,
                    "zoom": _zoom_for_bbox(
                        min(_lats), max(_lats), min(_lngs), max(_lngs)
                    ),
                }
            else:
                st.session_state[_vp_key] = {"lat": 38.5, "lng": -96.0, "zoom": 4}
        else:
            st.session_state[_vp_key] = {"lat": 38.5, "lng": -96.0, "zoom": 4}

    _vp = st.session_state[_vp_key]
    m = folium.Map(
        location=[_vp["lat"], _vp["lng"]],
        zoom_start=_vp["zoom"],
        tiles=_BASEMAP_TILES,
    )

    # Split into two static-style layers — eliminates 51 per-feature Python
    # callbacks and lets Folium serialize each layer's style once.
    _sel_feats = [
        f
        for f in geojson["features"]
        if f["properties"].get("_abbr", "") in selected_set
    ]
    _unsel_feats = [
        f
        for f in geojson["features"]
        if f["properties"].get("_abbr", "") not in selected_set
    ]
    fg = folium.FeatureGroup(name="US States")
    if _unsel_feats:
        folium.GeoJson(
            {"type": "FeatureCollection", "features": _unsel_feats},
            style_function=lambda _: _STYLE_UNSEL,
            tooltip=folium.GeoJsonTooltip(
                fields=["name", "_abbr"], aliases=["State:", "Code:"], sticky=False
            ),
            smooth_factor=1.5,
        ).add_to(fg)
    if _sel_feats:
        folium.GeoJson(
            {"type": "FeatureCollection", "features": _sel_feats},
            style_function=lambda _: _STYLE_SEL,
            tooltip=folium.GeoJsonTooltip(
                fields=["name", "_abbr"], aliases=["State:", "Code:"], sticky=False
            ),
            smooth_factor=1.5,
        ).add_to(fg)

    map_data = st_folium(
        m,
        feature_group_to_add=fg,
        width="100%",
        height=450,
        key=_map_key,
        returned_objects=["last_object_clicked", "last_object_clicked_tooltip"],
    )

    # ── Click-to-toggle state selection ───────────────────────────────────────
    current_click = map_data.get("last_object_clicked") if map_data else None
    current_tooltip = map_data.get("last_object_clicked_tooltip") if map_data else None

    if (
        current_click
        and current_tooltip
        and current_click != st.session_state.get(click_key)
    ):
        st.session_state[click_key] = current_click
        tooltip_str = str(current_tooltip)
        # Match state name from tooltip (longest-first to handle e.g. "West Virginia" vs "Virginia")
        clicked_abbr = None
        for name in sorted(_STATE_NAME_TO_ABBR.keys(), key=len, reverse=True):
            if name in tooltip_str:
                clicked_abbr = _STATE_NAME_TO_ABBR[name]
                break
        if clicked_abbr:
            if clicked_abbr in selected_set:
                selected_set.discard(clicked_abbr)
            else:
                selected_set.add(clicked_abbr)
            st.session_state[sel_key] = selected_set
            st.rerun(scope="fragment")

    # ── Display selected states ───────────────────────────────────────────────
    chosen = sorted(selected_set)
    if chosen:
        st.caption(f"{len(chosen)} state(s) selected: {', '.join(chosen)}")
    else:
        st.caption("Click states on the map to select them.")

    # Persist for sub-level views
    st.session_state["_map_selected_states"] = chosen

    # Drill-down buttons (visible only when at least one state is selected).
    # ComStock offers counties only — its metadata has no PUMA resolution.
    if chosen:
        if allow_puma:
            col1, col2 = st.columns(2)
            with col1:
                if st.button(
                    "Select Counties", width="stretch", key="btn_select_counties"
                ):
                    st.session_state["_map_view_mode"] = "county"
                    st.rerun(scope="app")
            with col2:
                if st.button("Select PUMAs", width="stretch", key="btn_select_pumas"):
                    st.session_state["_map_view_mode"] = "puma"
                    st.rerun(scope="app")
        else:
            if st.button("Select Counties", width="stretch", key="btn_select_counties"):
                st.session_state["_map_view_mode"] = "county"
                st.rerun(scope="app")

    # Show existing sub-level selection counts as a reminder
    n_counties = len(st.session_state.get("_map_county_sel", set()))
    n_pumas = len(st.session_state.get("_map_puma_sel", set()))
    if n_counties or n_pumas:
        parts = []
        if n_counties:
            parts.append(f"{n_counties} county(ies)")
        if n_pumas:
            parts.append(f"{n_pumas} PUMA(s)")
        st.caption(f"Sub-level selections in memory: {', '.join(parts)}")


@st.fragment
def _render_sub_level_view(selected_states: list[str], geo_level: str) -> None:
    """Render county or PUMA boundaries for all *selected_states* with click-to-toggle."""

    if st.button("↩ Return to State Selection", key="btn_back_to_states"):
        st.session_state["_map_view_mode"] = "state"
        st.rerun(scope="app")

    st.markdown(f"**Selecting {geo_level.title()}s for: {', '.join(selected_states)}**")

    # Resolve state abbreviations → FIPS codes
    statefps = [
        _STATE_ABBR_TO_FIPS[s] for s in selected_states if s in _STATE_ABBR_TO_FIPS
    ]
    if not statefps:
        st.error("No valid states selected.")
        return

    # Session-state keys (county and puma tracked independently)
    sel_key = f"_map_{geo_level}_sel"
    click_key = f"_map_last_click_{geo_level}"

    selected_set: set[str] = st.session_state.get(sel_key, set())

    # Load and filter GeoJSON for all selected states
    if geo_level == "county":
        raw_geojson = _load_county_geojson()
        name_field = "NAME"
        name_alias = "County:"
        fips_key = "STATEFP"
        gisjoin_fn = _county_gisjoin
    else:  # puma
        raw_geojson = _load_puma_geojson()
        name_field = "NAME10"
        name_alias = "PUMA:"
        fips_key = "STATEFP10"
        gisjoin_fn = _puma_gisjoin

    filtered = _filter_features_by_states(raw_geojson, statefps, fips_key, gisjoin_fn)

    if not filtered["features"]:
        st.warning(f"No {geo_level} boundaries found for the selected states.")
        return

    # Compute bounding box for initial map viewport.
    # streamlit-folium uses zoom_start / location directly (fit_bounds JS is
    # ignored when feature_group_to_add is used), so compute both explicitly.
    all_coords: list[list[float]] = []
    for feat in filtered["features"]:
        all_coords.extend(_flatten_coords(feat["geometry"]))

    if all_coords:
        lats = [c[1] for c in all_coords]
        lngs = [c[0] for c in all_coords]
        center_lat = (min(lats) + max(lats)) / 2
        center_lng = (min(lngs) + max(lngs)) / 2
        zoom = _zoom_for_bbox(min(lats), max(lats), min(lngs), max(lngs))
        bounds = [[min(lats), min(lngs)], [max(lats), max(lngs)]]
    else:
        center_lat, center_lng = _bbox_center(filtered["features"])
        zoom = 6
        bounds = None

    m = folium.Map(
        location=[center_lat, center_lng], zoom_start=zoom, tiles=_BASEMAP_TILES
    )
    if bounds:
        m.fit_bounds(bounds)  # applied when streamlit-folium does execute JS

    # Split into two static-style layers — eliminates per-feature Python callbacks
    # across potentially thousands of county/PUMA features.
    _sel_feats = [
        f
        for f in filtered["features"]
        if f["properties"].get("_gisjoin", "") in selected_set
    ]
    _unsel_feats = [
        f
        for f in filtered["features"]
        if f["properties"].get("_gisjoin", "") not in selected_set
    ]
    fg = folium.FeatureGroup(name=geo_level.title())
    if _unsel_feats:
        folium.GeoJson(
            {"type": "FeatureCollection", "features": _unsel_feats},
            style_function=lambda _: _STYLE_UNSEL_SUB,
            tooltip=folium.GeoJsonTooltip(
                fields=[name_field, "_gisjoin"],
                aliases=[name_alias, "GISJOIN:"],
                sticky=False,
            ),
            smooth_factor=1.5,
        ).add_to(fg)
    if _sel_feats:
        folium.GeoJson(
            {"type": "FeatureCollection", "features": _sel_feats},
            style_function=lambda _: _STYLE_SEL_SUB,
            tooltip=folium.GeoJsonTooltip(
                fields=[name_field, "_gisjoin"],
                aliases=[name_alias, "GISJOIN:"],
                sticky=False,
            ),
            smooth_factor=1.5,
        ).add_to(fg)

    _map_version = st.session_state.get("_map_version", 0)
    map_data = st_folium(
        m,
        feature_group_to_add=fg,
        width="100%",
        height=500,
        key=f"sub_map_{geo_level}_v{_map_version}",
        returned_objects=["last_object_clicked", "last_object_clicked_tooltip"],
    )

    # ── Click-to-toggle ───────────────────────────────────────────────────────
    current_click = map_data.get("last_object_clicked") if map_data else None
    current_tooltip = map_data.get("last_object_clicked_tooltip") if map_data else None

    if (
        current_click
        and current_tooltip
        and current_click != st.session_state.get(click_key)
    ):
        st.session_state[click_key] = current_click
        match = re.search(r"(G\d{7,8})", str(current_tooltip))
        if match:
            clicked_id = match.group(1)
            new_set = set(selected_set)
            if clicked_id in new_set:
                new_set.discard(clicked_id)
            else:
                new_set.add(clicked_id)
            st.session_state[sel_key] = new_set
            st.rerun(scope="fragment")

    # ── Summary row + Clear button ────────────────────────────────────────────
    col1, col2 = st.columns([4, 1])
    with col1:
        if selected_set:
            preview = ", ".join(sorted(selected_set)[:5])
            suffix = "…" if len(selected_set) > 5 else ""
            st.caption(
                f"{len(selected_set)} {geo_level}(s) selected: {preview}{suffix}"
            )
        else:
            st.caption(f"Click {geo_level} regions on the map to select them.")
    with col2:
        if st.button("Clear", key=f"map_clear_{geo_level}"):
            st.session_state[sel_key] = set()
            st.session_state.pop(click_key, None)
            st.rerun(scope="fragment")


# ── Utility-based county selector ─────────────────────────────────────────────


def render_utility_map_selector() -> dict:
    """
    Render a utility name search with an interactive county map.

    Step 1 — the user types a query; the bundled utility-company list is
    filtered by substring match to populate a selectbox.

    Step 2 — upon selecting a utility, the matching EIA utility ID is looked up
    in ``eia_to_county.csv`` to identify county FIPS codes for that utility's
    service territory.  Those states' county boundaries are loaded and shown on
    a Folium map with the matched counties highlighted in blue.

    Step 3 — the user can click counties to add or remove them from the
    selection before clicking the **Save Selected** button (rendered by the
    caller in ``page_02_geography.py``).

    Returns:
        Dict with ``selected_county_ids`` — list of county GISJOIN strings
        representing the current (unsaved) selection.
    """
    if not _FOLIUM_AVAILABLE:
        st.error("streamlit-folium is not installed. Run: pixi install")
        return {"selected_county_ids": []}

    companies = _load_utility_companies_csv()

    search_query: str = st.text_input(
        "Search utility name or alias",
        placeholder="e.g. Xcel Energy, Rocky Mountain Power",
        key="utility_geo_search_query",
    )

    if not search_query:
        return {
            "selected_county_ids": list(
                st.session_state.get("_utility_map_county_sel", set())
            )
        }

    q = search_query.lower()
    matches = [c for c in companies if q in c.get("label", "").lower()]
    if not matches:
        words = q.split()
        matches = [
            c for c in companies if all(w in c.get("label", "").lower() for w in words)
        ]
    if not matches:
        st.caption("No matching utilities found. Try a different search term.")
        return {
            "selected_county_ids": list(
                st.session_state.get("_utility_map_county_sel", set())
            )
        }

    match_labels = [c["label"] for c in matches[:50]]
    selected_label: str = st.selectbox(
        "Select Utility",
        options=["— select a utility —"] + match_labels,
        key="utility_geo_selectbox",
    )

    if selected_label == "— select a utility —":
        return {
            "selected_county_ids": list(
                st.session_state.get("_utility_map_county_sel", set())
            )
        }

    selected_company = next((c for c in matches if c["label"] == selected_label), None)
    eia_id = (
        selected_company.get("eia_utility_id", "").strip() if selected_company else ""
    )

    if not eia_id:
        st.warning("No EIA utility ID found for this utility in the local dataset.")
        return {
            "selected_county_ids": list(
                st.session_state.get("_utility_map_county_sel", set())
            )
        }

    # Look up counties from eia_to_county.csv
    eia_county_rows = _load_eia_to_county_csv()
    matched_rows = [
        r for r in eia_county_rows if r.get("eia_utility_id", "").strip() == eia_id
    ]

    if not matched_rows:
        st.info(
            "No counties found for this utility in the EIA service territory dataset."
        )
        return {
            "selected_county_ids": list(
                st.session_state.get("_utility_map_county_sel", set())
            )
        }

    # Build GISJOIN IDs and collect state FIPS codes
    matched_gisjoins: set[str] = set()
    matched_statefps: set[str] = set()
    for row in matched_rows:
        statefp = str(row.get("STATEFP", "")).strip().zfill(2)
        countyfp = str(row.get("COUNTYFP", "")).strip().zfill(3)
        if statefp and countyfp:
            matched_gisjoins.add(f"G{statefp}0{countyfp}0")
            matched_statefps.add(statefp)

    if not matched_gisjoins:
        st.info("Could not resolve county boundaries for this utility.")
        return {
            "selected_county_ids": list(
                st.session_state.get("_utility_map_county_sel", set())
            )
        }

    # When the selected utility changes, reset the county selection to matched set
    sel_key = "_utility_map_county_sel"
    prev_key = "_utility_map_prev_label"
    if st.session_state.get(prev_key) != selected_label:
        st.session_state[sel_key] = set(matched_gisjoins)
        st.session_state[prev_key] = selected_label

    n_counties = len(matched_gisjoins)
    n_states = len(matched_statefps)
    st.caption(
        f"Utility serves {n_counties} county(ies) across {n_states} state(s). "
        "Click counties on the map to add or remove them."
    )

    _render_utility_county_map(sorted(matched_statefps), matched_gisjoins)

    return {"selected_county_ids": list(st.session_state.get(sel_key, set()))}


@st.fragment
def _render_utility_county_map(statefps: list[str], matched_gisjoins: set[str]) -> None:
    """Render county map for a utility's service territory with click-to-toggle."""
    sel_key = "_utility_map_county_sel"
    click_key = "_utility_map_last_click"

    if sel_key not in st.session_state:
        st.session_state[sel_key] = set(matched_gisjoins)

    selected_set: set[str] = st.session_state[sel_key]

    raw_geojson = _load_county_geojson()
    filtered = _filter_features_by_states(
        raw_geojson, statefps, "STATEFP", _county_gisjoin
    )

    if not filtered["features"]:
        st.warning("No county boundaries found for this utility's service territory.")
        return

    center_lat, center_lng = _bbox_center(filtered["features"])
    m = folium.Map(
        location=[center_lat, center_lng], zoom_start=5, tiles=_BASEMAP_TILES
    )

    all_coords: list[list[float]] = []
    for feat in filtered["features"]:
        all_coords.extend(_flatten_coords(feat["geometry"]))
    if all_coords:
        lats = [c[1] for c in all_coords]
        lngs = [c[0] for c in all_coords]
        m.fit_bounds([[min(lats), min(lngs)], [max(lats), max(lngs)]])

    def _style(feature):
        gj = feature["properties"].get("_gisjoin", "")
        if gj in selected_set:
            return {
                "fillColor": "#2196F3",
                "color": "#1565C0",
                "weight": 2,
                "fillOpacity": 0.6,
            }
        return {
            "fillColor": "#ECEFF1",
            "color": "#78909C",
            "weight": 1,
            "fillOpacity": 0.25,
        }

    fg = folium.FeatureGroup(name="Counties")
    folium.GeoJson(
        filtered,
        style_function=_style,
        tooltip=folium.GeoJsonTooltip(
            fields=["NAME", "_gisjoin"],
            aliases=["County:", "GISJOIN:"],
            sticky=False,
        ),
    ).add_to(fg)

    map_data = st_folium(
        m,
        feature_group_to_add=fg,
        width="100%",
        height=500,
        key="utility_county_map",
        returned_objects=["last_object_clicked", "last_object_clicked_tooltip"],
    )

    # Click-to-toggle
    current_click = map_data.get("last_object_clicked") if map_data else None
    current_tooltip = map_data.get("last_object_clicked_tooltip") if map_data else None

    if (
        current_click
        and current_tooltip
        and current_click != st.session_state.get(click_key)
    ):
        st.session_state[click_key] = current_click
        match = re.search(r"(G\d{7,8})", str(current_tooltip))
        if match:
            clicked_id = match.group(1)
            new_set = set(selected_set)
            if clicked_id in new_set:
                new_set.discard(clicked_id)
            else:
                new_set.add(clicked_id)
            st.session_state[sel_key] = new_set
            st.rerun(scope="fragment")

    col1, col2 = st.columns([4, 1])
    with col1:
        if selected_set:
            preview = ", ".join(sorted(selected_set)[:5])
            suffix = "…" if len(selected_set) > 5 else ""
            st.caption(f"{len(selected_set)} county(ies) selected: {preview}{suffix}")
        else:
            st.caption("Click counties on the map to select or deselect them.")
    with col2:
        if st.button("Clear", key="utility_map_clear"):
            st.session_state[sel_key] = set()
            st.session_state.pop(click_key, None)
            st.rerun(scope="fragment")
