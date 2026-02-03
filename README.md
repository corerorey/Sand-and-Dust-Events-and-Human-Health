# Sand-and-Dust-Storms-and-Human-Health

**A methodological playbook for event-based exposure engineering, epidemiological/causal analysis, and ML-driven decision support for sand and dust storm (SDS) health risks.**

This repository is designed as a **research framework** rather than a single “one-click” model. It provides:

- A reproducible **data + alignment** blueprint (time/space harmonization).
- Standard **SDS event analytics** outputs (binary event label + footprint + intensity metrics).
- A menu of **health-risk estimation** methods (GAM/DLNM, causal inference, ML).
- Optional **decision-support** ideas based on rehearsal learning (Grad-RH, AUF-MICNS).

> Target use cases: daily/weekly health surveillance, early warning, mechanism-informed hypothesis testing, and policy-oriented scenario analysis.

---

## Table of contents

- [1. Repository goals](#1-repository-goals)
- [2. Data architecture](#2-data-architecture)
- [3. Exposure engineering for SDS](#3-exposure-engineering-for-sds)
- [4. Health-risk modeling](#4-health-risk-modeling)
- [5. Causal inference modules](#5-causal-inference-modules)
- [6. Machine learning modules](#6-machine-learning-modules)
- [7. Decision support: rehearsal learning](#7-decision-support-rehearsal-learning)
- [8. Recommended minimal variable set](#8-recommended-minimal-variable-set)
- [9. Evaluation, diagnostics, and reproducibility](#9-evaluation-diagnostics-and-reproducibility)
- [10. Data resources (links + formats)](#10-data-resources-links--formats)
- [11. References](#11-references)

---

## 1. Repository goals

### 1.1 Problem statement

We study how **SDS events** (and related dust exposure) affect human health.

We formalize a general mapping:

- **Inputs (X):** SDS exposures + meteorology + co-pollutants + population vulnerability + interventions.
- **Outputs (Y):** health outcomes (counts/rates, binary events, time-to-event), possibly subgroup-specific.
- **Constraints:** confounding, measurement error, lagged effects, spatial misalignment, non-stationarity.

### 1.2 Event-first philosophy

To improve comparability across studies and support downstream health models, we recommend an **event-first** workflow:

1. **Detect SDS events** (y/n label) using one or more data sources.
2. Delineate the **spatiotemporal footprint** and compute **affected-area concentration/intensity** metrics.
3. Align event metrics to health outcomes at compatible time scales (daily/weekly/monthly).
4. Estimate risks using statistical/causal/ML methods with uncertainty quantification.

---

## 2. Data architecture

### 2.1 Core datasets and typical formats

**Meteorology / reanalysis** *(NetCDF/GRIB)*
- ERA5 hourly meteorology: wind components, temperature, RH, pressure, boundary-layer variables.
- CAMS global reanalysis: aerosols (including dust-related fields).
- MERRA-2: aerosol diagnostics including dust proxies (NASA GES DISC).

**Remote sensing** *(HDF/GeoTIFF)*
- MODIS MAIAC AOD (daily, ~1 km) for plume/footprint detection and exposure proxies.
- AERONET: ground-truth optical properties for AOD validation.

**Air quality stations** *(CSV/JSON/Parquet)*
- PM10/PM2.5 from national networks and open platforms (e.g., OpenAQ).

**Health outcomes** *(CSV; frequently access-restricted)*
- Daily/weekly: mortality, hospital admissions/ER visits, ambulance calls.
- Notifiable infectious disease surveillance (e.g., measles cases).

### 2.2 Temporal alignment

**Recommended default:** daily.

- Convert hourly reanalysis → daily summaries (mean/max or event-window statistics).
- Convert event footprints → daily footprint metrics.
- Align to health time series with lag windows. If health data are weekly:
  - count SDS days per week,
  - compute weekly max/mean intensity,
  - compute cumulative intensity (sum over days).

### 2.3 Spatial alignment

Choose a spatial support consistent with health data (city/county/province).

- Grid → polygon aggregation using zonal statistics (area-weighted and/or population-weighted).
- Save both **mean** and **high-percentile** exposures within each administrative unit.

---

## 3. Exposure engineering for SDS

### 3.1 Event object (recommended data model)

For each SDS event **e**, store a structured “event object”:

- `start_time`, `end_time`
- `footprint` (polygon(s) per time step, e.g., GeoJSON)
- `intensity_metrics` (time series):
  - area-mean concentration/intensity
  - population-weighted mean (optional)
  - within-footprint percentile (e.g., P90 core)
  - footprint area
  - duration-integrated intensity (dose proxy)
- `detection_method` (visibility / PM threshold / AOD / model tracer / fusion)
- `uncertainty` (confidence score, threshold sensitivity range, source spread)

This makes SDS exposures **interoperable** with epidemiological models and supports explicit uncertainty propagation.

### 3.2 Dust vs. non-dust PM separation

Depending on data availability:

- Use dust tracers from CAMS/MERRA-2 (model-informed separation).
- Use dust fraction of AOD (algorithmic/model-based).
- Use coarse fraction proxy (PM10 − PM2.5) when both are available.

---

## 4. Health-risk modeling

### 4.1 Generalized Additive Models (GAM)

**Use case:** daily counts (mortality, admissions, disease cases).

**Canonical form** (Poisson / quasi-Poisson / negative binomial):

\[
\log(\mathbb{E}[Y_t]) = \alpha + \beta E_t + \sum_j s_j(M_{j,t}) + s_{time}(t) + \gamma^\top DOW_t + \log(pop_t)
\]

Where:
- `Y_t`: daily health outcome (count)
- `E_t`: SDS exposure (binary event, intensity, or both)
- `M_{j,t}`: meteorology and co-pollutants (smooth terms)
- `s_time(t)`: seasonality + long-term trend
- `DOW_t`: day-of-week, holiday indicators
- `log(pop_t)`: offset

**Key design parameters:**
- Spline basis, degrees of freedom for time trend & meteorology
- Overdispersion strategy
- Residual autocorrelation checks

### 4.2 Distributed Lag Nonlinear Models (DLNM)

**Use case:** SDS effects can extend across multiple days.

Design choices:
- lag window length `L` (outcome-dependent)
- exposure-response basis (linear vs spline)
- lag-response basis (spline; constrained if needed)

### 4.3 Multi-site analysis

For multi-city/province settings:
- Fit city-specific models → pool effects via random-effects meta-analysis.
- Explore heterogeneity by climate zone, dust regime, or vulnerability indices.

---

## 5. Causal inference modules

SDS–health studies are sensitive to confounding (seasonality, temperature, humidity, co-pollutants) and policy/behavior shifts.

Recommended causal tools:

- **DAG-driven adjustment sets** (structural causal reasoning).
- **Time-varying effect estimation** (seasonal regimes, policy changes).
- **Negative controls / placebo tests** to detect residual confounding.
- **Robustness/refutation tests** (when using ML-aided causal frameworks).

Practical note: causal claims require transparent assumptions. When assumptions are weak, report results as associations with careful sensitivity analyses.

---

## 6. Machine learning modules

### 6.1 Exposure modeling (high-resolution dust surfaces)

Borrow best practices from high-resolution PM modeling:

- Predict daily dust/coarse PM using remote sensing (AOD), reanalysis meteorology, CTM outputs, land-use, and surface properties.
- Use ensemble learners (e.g., random forest, gradient boosting) and validate against stations/AERONET.

### 6.2 Risk prediction and classification (TabPFN as a fast baseline)

TabPFN is a transformer trained for **small tabular classification** tasks and can serve as a fast benchmark for:

- “High-risk day” classification
- “Outbreak week” classification

Preprocessing is essential (numerical-only, missing values handled before inference).

---

## 7. Decision support: rehearsal learning

This repository also explores how **decision support** could be built on top of SDS–health models.

### 7.1 Grad-RH (gradient-based nonlinear rehearsal learning)

Conceptually:
- `X`: context (forecast SDS intensity, meteorology, mobility)
- `Z`: actionable interventions (warnings, masks, school closures, hospital staffing)
- `Y`: undesired outcomes (excess mortality, ER surge, outbreak intensity)

Grad-RH supports optimization over **multivariate interventions** in nonlinear settings.

### 7.2 AUF-MICNS (avoiding undesired future with minimal cost under non-stationarity)

Conceptually:
- sequentially updates influence relations in non-stationary environments
- selects minimal-cost interventions to keep outcomes within a desired region

**Important:** applying rehearsal learning in public health requires:
- a defensible influence graph (domain expertise)
- realistic action-cost definitions
- adequate variation in observed interventions to learn influence relations

---

## 8. Recommended minimal variable set

**Exposure (event-based):**
- SDS binary indicator
- intensity metrics (area-mean, pop-weighted mean, P90 core)
- footprint area, duration

**Air quality:**
- PM10, PM2.5, coarse fraction (PM10 − PM2.5)
- optional: O3, NO2 (mixture indicators)

**Meteorology:**
- temperature, RH, wind speed/gusts, pressure
- boundary-layer height (optional)

**Population & vulnerability:**
- age structure, SES proxies, baseline disease prevalence

**Healthcare capacity & behavior:**
- hospital beds per capita, baseline outpatient volume
- mobility proxies (optional)

**Infectious disease modifiers (if studying measles or similar):**
- vaccination coverage proxies
- school calendar, reporting changes

---

## 9. Evaluation, diagnostics, and reproducibility

**Exposure evaluation**
- Compare event detection across sources (PM vs AOD vs reanalysis) using hit rate / false alarm rate.
- Validate AOD-based products with AERONET where possible.

**Health model diagnostics**
- residual autocorrelation
- overdispersion and influential points
- sensitivity to df and lag length

**Causal robustness**
- negative controls
- alternative exposure definitions
- refutation tests (placebo treatments, randomized time shifts)

**Reproducibility**
- store event catalogs with metadata
- log preprocessing steps and QA/QC rules
- version dataset snapshots when permissible

---

## 10. Data resources (links + formats)

> The following URLs are included as practical entry points. Data access may require registration and/or acceptance of license terms.

```text
ERA5 (NetCDF/GRIB via Copernicus CDS)
https://cds.climate.copernicus.eu/datasets/reanalysis-era5-single-levels

CAMS global reanalysis (NetCDF)
https://www.ecmwf.int/en/forecasts/dataset/cams-global-reanalysis

MERRA-2 data access (NetCDF/HDF; NASA GES DISC)
https://gmao.gsfc.nasa.gov/gmao-products/merra-2/data-access_merra-2/

MODIS MAIAC AOD (HDF/GeoTIFF; also via Google Earth Engine)
https://ladsweb.modaps.eosdis.nasa.gov/missions-and-measurements/products/MCD19A2

AERONET (AOD/inversion; CSV/text)
https://aeronet.gsfc.nasa.gov/

OpenAQ API (air quality; JSON/CSV)
https://docs.openaq.org/

WHO: Sand and dust storms (health framing)
https://www.who.int/news-room/fact-sheets/detail/sand-and-dust-storms

WMO SDS-WAS (global SDS forecasting coordination)
https://wmo.int/site/knowledge-hub/governance/research-board/environmental-pollution-and-atmospheric-chemistry-scientific-steering-committee-ssc-epac/sand-and-dust-storms-warning-advisory-and-assessment-system
```

---

## 11. References

This repository builds on the environmental epidemiology and ML/causal inference literature, including (non-exhaustive):

- Manisalidis et al. (2020). *Environmental and Health Impacts of Air Pollution: A Review.*
- Wu et al. (2020). *Evaluating the impact of long-term exposure to fine particulate matter on mortality among the elderly.*
- Kang et al. (2021). *Machine learning-aided causal inference framework for environmental data analysis.*
- Han et al. (2024). *Interpretable AI-driven causal inference to uncover time-varying effects of PM2.5 and public health interventions.*
- Hollmann et al. (2023). *TabPFN: A Transformer That Solves Small Tabular Classification Problems in a Second.* arXiv:2207.01848.
- Qin, Tian, Wang, Zhou (2025). *Gradient-Based Nonlinear Rehearsal Learning with Multivariate Alterations.* (AAAI).
- Du, Qin, Wang, Zhou (2024). *Avoiding Undesired Future with Minimal Cost in Non-Stationary Environments.* (NeurIPS).

If you use this repository in academic work, please cite it and the relevant original papers.
