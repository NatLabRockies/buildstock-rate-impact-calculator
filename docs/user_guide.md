# BuildStock Rate Impact Calculator — User Guide

The BuildStock Rate Impact Calculator estimates how utility rate structures
and building efficiency upgrades interact to affect customer energy bills
across a region. It combines NLR's ResStock (residential) and ComStock
(commercial) building-stock simulation datasets with PySAM's utility-rate
engine to bill thousands of simulated buildings under real or custom
tariffs — before and after an upgrade, and optionally under several
alternative rate designs at once.

This guide covers the workflow, explains how sampling **weights** work in
each dataset (essential for reading the numbers correctly), and walks
through every section of the Results page with guidance on interpretation.

- [1. Concepts](#1-concepts)
- [2. Workflow walkthrough](#2-workflow-walkthrough)
- [3. Understanding weights](#3-understanding-weights)
- [4. Interpreting results](#4-interpreting-results)
- [5. Units cheat sheet](#5-units-cheat-sheet)
- [6. Modeling notes and caveats](#6-modeling-notes-and-caveats)

---

## 1. Concepts

**Datasets.** ResStock and ComStock are NLR simulations of the U.S.
residential and commercial building stock. Each dataset release contains
tens of thousands of *building models* — statistically sampled combinations
of building characteristics — with simulated energy use at 15-minute
resolution for a 2018 actual-meteorological-year weather file. Supported
releases: ResStock 2024.2 and 2025.1, ComStock 2024.2 and 2025.3.

**Upgrades (scenarios).** Each release includes a baseline (upgrade 0) and a
set of efficiency-measure packages (upgrades 1–16 for ResStock, 1–39 for
ComStock). Every scenario you select is billed independently. Not every
upgrade applies to every building — see
[applicability](#upgrade-vs-baseline-comparison).

**Rates.** Electricity rates are URDB-format tariffs — fetched from the
[OpenEI Utility Rate Database](https://openei.org/wiki/Utility_Rate_Database)
or built in the app — and are evaluated by PySAM, so time-of-use periods,
tiers, demand charges, fixed charges, and minimum charges are all honored
against each building's 8,760-hour load profile. Fuel rates (natural gas,
propane, fuel oil) are simpler custom flat or block rates applied to annual
(or monthly, for block rates) consumption; when no fuel rate is assigned,
the dataset's own default fuel bill column is used where available. A fuel
rate's monthly fixed charge accrues only for buildings that actually consume
that fuel — a building with zero consumption of a fuel is not billed its
fixed charge.

**Rate assignment vs. rate sweep.** You assign one or more rates to each
scenario. The **first** rate assigned to a scenario is its *primary* rate —
the one the savings, distribution, and summary views report. Any *additional*
rates assigned to a scenario bill it a second time (a rate sweep), enabling
the scenario × rate matrix and the multi-rate charts. A scenario can be
assigned **All Configured Rates** at once.

**Weights.** Every building model stands in for many real buildings. The
`weight` column says how many. Section 3 explains how the two datasets
differ and exactly where weights enter the math.

---

## 2. Workflow walkthrough

The app is a six-step workflow shown as a stepper across the top of the
page: **1 Dataset → 2 Geography → 3 Building Filters → 4 Energy Rates →
5 Run → 6 Results**. Completed steps show a ✓ in the stepper. Use the
**Next →** and **← Back** buttons at the bottom of each page to move through
the workflow — Next is disabled until the current step has what it needs —
or click any step in the stepper (or in the sidebar's **Workflow** panel) to
jump directly to it. Selections on most steps apply immediately; the
Geography step's selection is applied (and building metadata downloaded) when
you click **Next** or the page's **Apply selections** button. Progress is
saved into a *project* (a folder under your output directory containing
`config.json` and the rate definitions), which can be reloaded from the
sidebar later — loading a project jumps you to the first unfinished step.

### Step 1 — Dataset
Pick the dataset type (ResStock/ComStock) and release, and select the
baseline plus the upgrade scenarios to analyze. Scenario names are fetched
from the release's measure documentation.

### Step 2 — Geography
Select states, counties, or PUMAs on the map. **ComStock requires a
county-level selection** — its usable metadata is published per county
(PUMA selections are ignored for ComStock), and every load profile download
is scoped to your selection. The selection is applied — and the building
metadata downloaded — when you click **Next** (or the page's **Apply
selections** button, which lets you preview the applicability table without
leaving the step).

### Step 3 — Building Filters
Filter the building stock by characteristics — building type, heating fuel,
vintage, floor area, HVAC type, income bin (ResStock), annual electricity
consumption, and any additional `in.*` metadata columns you add. The
**Matching Buildings** metric shows how many building models survive your
filters; a warning appears below 30 models (see
[guardrails](#small-sample-and-preview-guardrails)).

### Step 4 — Rates
Build the rate library (fetch URDB tariffs by utility/label, or construct
flat/TOU/tiered/demand rates in the editor), then assign one **or more**
electricity rates to each scenario with the per-scenario multiselect. The
first rate you pick for a scenario is its primary rate; any additional rates
bill that scenario again (a rate sweep) and populate the Rate Comparison
matrix — runtime scales with the total number of rate assignments. Pick
**✨ All Configured Rates** to bill a scenario under every rate at once.
Optionally assign fuel rates per scenario and fuel. At least one scenario
must have an assigned electricity rate before **Next** unlocks; the Run step
warns when some scenarios are still unassigned. Note: in-progress entries in
the rate editors are not preserved if you navigate to another step before
adding the rate.

### Step 5 — Run
Review the configuration summary and upgrade applicability, optionally
enable **⚡ Fast preview** (a stratified sample — see
[preview sampling](#preview-sampling)), and start the run. The pipeline:

1. loads and filters metadata for your selection;
2. downloads the needed 15-minute load profiles from OEDI (cached on disk —
   reruns only fetch what's missing);
3. bills every building × scenario × rate through PySAM (electricity) and
   the fuel-rate expressions;
4. flags rate eligibility and joins building characteristics onto the
   results.

Runs are cancelable; progress, per-phase timing, and an activity log are
shown live. When the run finishes, a **View Results →** button takes you
straight to the Results step.

### Step 6 — Results
Interactive charts, tables, and exports — covered in detail in
[Section 4](#4-interpreting-results). Results are also written to
`<project>/results/bill_results.csv` automatically.

### The sidebar
The sidebar shows a **Workflow** panel — one line per step with a completion
mark and a summary of your selections; click any line to jump to that step.
Below it, **⚙ Setup** holds the data directory and OpenEI API key (expanded
automatically until a key is stored), and **Project** holds the project name
and the load-existing-project controls.

---

## 3. Understanding weights

Both datasets are *samples*: each building model represents a slice of the
real stock, and the `weight` column is the number of real-world
buildings (or dwelling units) that model stands for. The two datasets
handle weights differently, and the app's math follows suit. Getting this
right is the difference between reading a number as "a typical customer's
bill" versus "the whole region's bill."

### ResStock: one scalar weight, per-building bills

ResStock samples the residential stock at a fixed rate, so **every row
carries the same weight** — a single dwelling-units-per-model value fixed
by the release. The app reads this scalar once from the metadata.

- **Bills are computed per building.** Each model's unscaled 8,760-hour
  load goes through PySAM, so `annual_bill` is what *one household* of that
  type would pay. Tier boundaries, demand charges, and fixed charges apply
  at true household scale.
- **Weights enter only at aggregation:**
  - totals: `total_annual_bill = Σ (weight × annual_bill)` — dollars across
    the represented stock;
  - `units = Σ weight` — dwelling units represented;
  - means and medians are simple per-model statistics (each model counts
    once), representing the *typical modeled household*, not a
    weight-blended average.

**Interpretation:** ResStock means/medians/distributions answer "what does
a household pay?" Totals answer "what does the selected stock pay in
aggregate?"

### ComStock: per-row weights, pre-weighted bills

ComStock weights vary by building and by geography, and **the same
`bldg_id` can appear on several rows** — one per county instance, each with
its own weight. (Internally every metadata row gets a unique `_row_uid` so
these instances never collide.)

- **Loads are multiplied by weight *before* billing.** Each row's load
  profile is scaled by its weight before PySAM runs, so `annual_bill`,
  `annual_energy_kwh`, and `peak_demand_kw` on a ComStock results row are
  **weighted, stock-level quantities** — real-world dollars/kWh/kW for all
  the buildings that row represents, not one building's bill.
- **Per-building figures are recovered by dividing the weight back out.**
  The app does this wherever a per-building statistic is shown:
  - cost intensity: `$/ft² = annual_bill / (weight × sqft)`;
  - peak intensity: `W/ft² = peak_demand_kw × 1000 / (weight × sqft)`;
  - eligibility screening compares `annual_energy_kwh / weight` and
    `peak_demand_kw / weight` against a rate's window.
- **Totals are direct sums** (the weighting is already baked in):
  `total_annual_bill = Σ annual_bill`, and floor area is
  `total_sqft = Σ (weight × sqft)`.
- **Tiny-weight rows are excluded up front.** Rows whose weighted floor
  area is ≤ 333 ft² are dropped before billing — their minuscule weights
  otherwise produce absurd $/ft² intensities. The run log reports how many
  were excluded.

**Interpretation:** ComStock raw dollar figures are *stock* quantities; the
comparable per-building lens is always the **$/ft² intensity**, which is
what the app reports as means and medians. Never read a ComStock
`annual_bill` as one building's bill.

> **Caveat — nonlinear rate features at weighted scale.** Because ComStock
> loads are weight-scaled before billing, tier thresholds, demand charges,
> and fixed charges in the tariff are evaluated against the *aggregate*
> load of the represented buildings, not each building individually. For
> flat and TOU energy charges (linear in load) this is exact; for strongly
> tiered rates or per-meter fixed charges, per-building $/ft² intensities
> derived from weighted bills are approximations. Rows with weights near
> 1.0 are unaffected; be more cautious with high-weight rows under
> block-heavy tariffs.

### Preview sampling

**⚡ Fast preview** bills a stratified sample instead of the full selection
(N models per building-type stratum; configurable). To keep the numbers
unbiased, each sampled row's weight is multiplied by its stratum's scale
factor `original stratum weight ÷ sampled stratum weight`, so:

- **weighted totals remain unbiased estimates** of the full-run totals;
- **means and medians are sample estimates** — expect them to wiggle
  relative to a full run, especially in small strata;
- fully-kept strata get a scale of exactly 1.0 (no distortion).

Preview results are bannered on the Results page and flagged in exports.
Use preview to iterate on rate designs, then re-run without it for final
numbers.

---

## 4. Interpreting results

All statistics on the Results page respect three global controls at the
top:

- **Energy Sources pills** — which bill components (Electricity, Natural
  Gas, Propane, Fuel Oil) feed the summary, distribution, and savings
  statistics. Electricity-specific sections (components, monthly profile,
  peak, rate comparison) always use electricity regardless.
- **Rate eligibility toggle** — when the assigned rate declares a
  peak-demand (kW) or monthly-consumption (kWh) applicability window and
  some buildings fall outside it, a warning reports the weighted share and
  a toggle lets you exclude them from every statistic. Buildings are
  *flagged, not silently dropped* — you decide.
- **Guardrail banners** — preview-run notice and small-sample warning
  (below 30 building models, weighted statistics are unreliable).

With a rate sweep configured, **every section except Rate Comparison shows
the primary (assigned) rate only** — the multi-rate views are the Mean
Annual Electricity Bill chart, the Electricity Bill Distribution, the
Monthly Electricity Bill Profile, and the Rate Comparison matrix.

### Bill Summary

Three views:

- **Total bill** — per-scenario table over the selected energy sources.
  ResStock: mean/median per-household bill, weighted total, dwelling units.
  ComStock: mean/median $/ft², weighted total dollars, total ft².
- **By fuel** — stacked bar of per-source mean bills, showing how much of
  the bill each fuel contributes per scenario.
- **By charge component** — stacked bar decomposing the *electricity* bill
  into Fixed / Energy / Demand / Minimum charges (from PySAM). Use this to
  see *why* a rate design changes bills — e.g. a TOU rate shifting dollars
  from energy to demand charges. Fuel bills are not decomposed.

### Rate Comparison (rate sweep only)

The scenario × rate matrix, in two views:

- **Mean bill** — mean electricity bill ($ or $/ft²) for each scenario
  under each rate. Compare *across a row* to see rate-design impact on the
  same building stock and measure.
- **Mean savings** — **same-rate-baseline** savings: baseline under rate R
  minus upgrade under rate R. This answers *"what does this measure save
  under this rate design?"* It deliberately does **not** mix rates — the
  cross-rate question ("what happens if customers also switch rates?") is
  read from the Mean bill view instead.

### Mean Annual Electricity Bill (bar chart)

Bars grouped by scenario, colored by rate — every rate a scenario was
billed under appears. Electricity only.

### Electricity Bill Distribution

Box-and-whisker plots per scenario (and per rate, when a sweep is
configured), on per-building values — $/ft² for ComStock, $ for ResStock.

- Boxes span Q1–Q3 with the median tick; whiskers extend to the most
  extreme values within **1.5 × IQR** of the quartiles (Tukey convention).
- **Outliers are hidden by default** for rendering speed — enable *Show
  outliers* to draw the individual points beyond the whiskers.
- A wide box or long whiskers mean the rate/measure affects customers very
  unevenly — check the savings histogram and segment breakout to find out
  who sits where.

### Monthly Electricity Bill Profile

Mean monthly electricity bill per scenario (and per scenario–rate
combination under a sweep; use the multiselect to declutter). Seasonal
shape reveals rate–load interactions — e.g. a demand-heavy tariff spiking
in cooling months, or an envelope measure flattening winter bills. Fuel
bills are annual only and never appear here.

### Bill Savings

Shown when a baseline and at least one upgrade are present. **Sign
convention: positive savings = the upgrade is cheaper than the baseline.**
Savings compare each scenario *under its own assigned rate* — if you
assigned different rates to baseline and upgrade, the savings include both
the measure effect and the rate change (that's the point of the tool; use
the Rate Comparison matrix to isolate the measure effect under a fixed
rate).

- **Winners/losers line** — weighted shares of buildings that *save*
  (> $1/yr), *increase* (< −$1/yr), or are *neutral* (within ±$1). A
  measure with positive mean savings can still leave 30% of customers
  paying more — this line is the fastest equity check on the page.
- **Average / Total toggle** — Average shows per-building mean/median
  savings ($/ft² for ComStock) plus mean/median **percent savings**
  (relative to each building's own baseline bill). Total shows weighted
  aggregate dollar savings across the represented stock.
- **Savings histogram** — the full distribution, weighted by buildings
  represented. Look for mass below zero (bill increases) and long tails.
- **Break out savings by** — group savings by a categorical building
  characteristic (vintage, building type, heating fuel, income bin, …) to
  find which segments a program should target. Segments with fewer than 30
  models are flagged as unreliable.

### Peak Demand

Mean electricity peak demand per scenario with change vs. baseline —
**W/ft²** for ComStock (per-building intensity), **kW per dwelling** for
ResStock. This is the max of PySAM's monthly peaks, i.e. the quantity
demand charges are billed on — useful alongside the "By charge component"
view when evaluating demand-based tariffs.

### Energy Burden (ResStock only)

Household energy bills as a share of income, using the selected energy
sources and the dataset's binned income characteristic
(`in.income_recs_2020`). Each bin maps to a point estimate — the bin
midpoint; half the bound for "<20000"; 1.25 × the bound for "150000+";
"Not Available" households are excluded. Thresholds follow DOE LEAD
conventions: **high burden ≥ 6%**, **severe ≥ 10%** of income. The table
reports mean/median burden and the weighted share of households above each
threshold, per scenario, with the change vs. baseline.

Interpretation notes: burden is a *ratio of two estimates* (modeled bills ÷
binned-income midpoints), so treat threshold shares as indicative, not
precise; the measure of interest is usually the *change* in high/severe
shares between scenarios.

### Upgrade vs Baseline Comparison

Per-upgrade expanders comparing against the baseline, with an
**applicability** filter. Upgrades don't apply to every building (e.g. a
heat-pump measure doesn't apply to buildings that already have one).
Non-applicable buildings are billed using their **baseline load profile
under the upgrade scenario's rate** — so for them, any bill change is pure
rate effect. Toggle "Applicable buildings only" to isolate the measure's
effect on the buildings it actually touches; the box plots split
applicable vs. not.

### Export

- **Download Results CSV** — the full per-building × scenario × rate frame
  (see the cheat sheet below for column semantics).
- **Download HTML Report** — a self-contained shareable report with the run
  configuration, headline tables, and interactive charts (needs internet
  when opened, for the chart runtime CDN).
- **Save to Output Directory** — writes results and the rate JSONs into the
  project folder.

---

## 5. Units cheat sheet

Electricity result columns carry an `electricity_` prefix to distinguish
them from the fuel bill columns (`natural_gas_bill`, `propane_bill`,
`fuel_oil_bill`). Results saved by older app versions used unprefixed names
(`annual_bill`, `monthly_bills`, …) and are renamed automatically when the
project is loaded.

| Quantity | ResStock | ComStock |
|---|---|---|
| `weight` column | Same scalar on every row (dwelling units per model) | Per-row; one `bldg_id` may repeat across counties |
| `electricity_annual_bill`, `electricity_annual_energy_kwh`, `electricity_peak_demand_kw` (per row) | Per building (one household) | **Weighted** stock totals for that row |
| Per-building bill lens | `electricity_annual_bill` ($/household) | `cost_per_sqft` = bill ÷ (weight × ft²) |
| Mean / median statistics | $ per household (unweighted across models) | $/ft² (per-building intensity) |
| `total_annual_bill` | Σ weight × bill | Σ bill (already weighted) |
| Stock size metric | `units` = Σ weight (dwelling units) | `total_sqft` = Σ weight × ft² |
| Peak demand reporting | kW per dwelling | W/ft² |
| Savings in "Average" view | $ per household | $/ft² |
| Savings in "Total" view | Σ weight × savings | Σ savings (already weighted) |

Other result columns worth knowing: `scenario` (label), `rate_name` /
`is_primary` (rate-sweep variant identity), `applicable` (upgrade applies
to this building), `rate_eligible` (inside the rate's kW/kWh window),
`bill_savings_pct` (fraction of own baseline bill), `electricity_monthly_bills`
(12 electricity values as a JSON list), the four charge components
(`electricity_annual_fixed/energy/demand/min_charge`), `error` (non-null when
PySAM failed for the row — such rows are excluded from statistics), and any
`in.*` characteristic columns joined for filtering/segmenting.

---

## 6. Modeling notes and caveats

- **Single-year, load-only analysis.** Bills are computed for one year of
  simulated load (AMY 2018 weather) with no on-site generation, net
  metering, escalation, or multi-year effects.
- **Simulated buildings, statistical weights.** Results describe the
  *modeled stock*. Individual real buildings will differ; small filtered
  samples (< 30 models) make even stock-level statistics unreliable — the
  app warns when this happens.
- **ComStock weighted-load billing.** Nonlinear tariff features are
  evaluated at aggregate scale for ComStock (see the caveat in Section 3).
- **Electricity-only decomposition.** Charge components, monthly profiles,
  peak demand, and the rate sweep are electricity-only; fuel bills are
  annual and rate-independent of the electricity tariff.
- **Fuel fixed charges require consumption.** A fuel rate's monthly fixed
  charge is billed only to buildings with non-zero consumption of that fuel;
  all-electric buildings pay nothing under an assigned gas/propane/oil rate.
- **Rate eligibility windows** use URDB's declared limits: peak-kW bounds
  compare against the building's maximum monthly peak; monthly-kWh bounds
  compare against annual consumption ÷ 12. Buildings with missing data are
  treated as eligible.
- **Energy burden** relies on binned-income midpoints (documented above)
  and modeled bills — read shares as indicative.
- **Non-applicable buildings** keep their baseline load under upgrade
  scenarios; when comparing "all filtered buildings," upgrade-scenario
  statistics blend measure effects with pure-rate effects for those
  buildings.
- **Error rows.** Occasional PySAM failures (malformed profiles, degenerate
  rates) produce rows with a non-null `error`; they're counted in the
  Calculation Errors metric and excluded from all statistics.
- **Caching.** Metadata and load profiles are cached under the output
  directory; reruns reuse them. Delete the cache folder to force fresh
  downloads after a dataset release changes.
