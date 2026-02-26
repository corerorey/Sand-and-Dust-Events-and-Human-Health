# Code Review: Sand-and-Dust-Storms-and-Human-Health

**Reviewed:** 20+ Python source files, full README  
**Date:** 2026-02-26

---

## Overall Assessment

This is a **well-structured research project** with a clear scientific focus: linking sand/dust storm events to human health outcomes. The codebase covers data acquisition, preprocessing, event detection, satellite collocation, and spatial visualization — a solid foundation for epidemiological analysis.

**Strengths:**
- Clear scientific methodology documented in the README
- Thorough data pipeline from raw data → cleaned NetCDF → event detection → aligned outputs
- Good use of numpy memmap for memory-efficient NetCDF construction
- Well-designed satellite processing pipeline (Himawari HSD → BT → Dust RGB)
- Robust error handling in data downloaders with retry logic

**Areas for improvement** are detailed below by severity.

---

## 🔴 Critical Issues

### 1. Hardcoded Credentials in Source Code

> **CAUTION:** Earthdata credentials are hardcoded in plain text across **4 files** and committed to the Git repository.

| File | Location |
|------|----------|
| [data_prep/merra-2/fetch2.py](data_prep/merra-2/fetch2.py#L38-L39) | `EARTHDATA_USERNAME` / `EARTHDATA_PASSWORD` (lines 38–39) |
| [data_prep/merra-2/fetch_data.py](data_prep/merra-2/fetch_data.py#L18-L19) | Same credentials |
| [data_prep/merra-2/fetchch.py](data_prep/merra-2/fetchch.py#L18-L19) | Same credentials |
| [data_prep/merra-2/openfet.py](data_prep/merra-2/openfet.py#L17-L18) | Same credentials |

**Recommendation:**
- **Immediately rotate the password** on your Earthdata account
- Use environment variables (`os.environ["EARTHDATA_USERNAME"]`) or a `.env` file (with `.gitignore`)
- The `ensure_netrc()` approach in [openfet.py](data_prep/merra-2/openfet.py) already points in the right direction — don't hardcode credentials there either
- Add `*.env` and `_netrc` to `.gitignore`

### 2. No `.gitignore` File

There is no `.gitignore` preventing sensitive files, output artifacts, or IDE metadata from being committed. This directly compounds the credential leak above.

**Recommended `.gitignore` contents:**

```gitignore
# Credentials & secrets
.env
_netrc
.netrc

# Python cache
__pycache__/
*.pyc
*.pyo

# IDE
.idea/
.vscode/

# Partial downloads
*.part

# Generated data outputs
_tmp_*/
out_*/
nc_out/
downloads_*/
```

---

## 🟡 Significant Issues

### 3. Massive Code Duplication Across MERRA-2 Scripts

Four scripts ([fetch2.py](data_prep/merra-2/fetch2.py), [fetch_data.py](data_prep/merra-2/fetch_data.py), [fetchch.py](data_prep/merra-2/fetchch.py), [openfet.py](data_prep/merra-2/openfet.py)) contain **heavily duplicated logic**:

| Duplicated Component | Files |
|---------------------|-------|
| URL redirect + auth handler | `fetch2.py`, `fetchch.py`, `fetch_data.py` |
| `filename_from_url()` | `fetch2.py`, `fetchch.py` |
| `read_lanzhou_aq()` + `_parse_lanzhou_date()` | `fetch2.py`, `openfet.py` |
| `build_daily_mean_table()` | `fetch2.py`, `openfet.py` |
| `sniff_delimiter()` | `fetch2.py`, `openfet.py` |
| `normalize_lon_for_ds()` | `fetch2.py`, `fetch_data.py`, `openfet.py` |
| Event detection logic | All three — with **3 different implementations** |

**Impact:** Bug fixes must be applied in 3–4 places; divergent behavior between scripts is easy to miss.

**Recommendation:** Extract shared utilities into a `data_prep/merra-2/merra2_utils/` package:
- `auth.py` — credential loading, redirect handler, download functions
- `event_detection.py` — unified event detection with configurable thresholds
- `io_utils.py` — CSV sniffing, date parsing, Lanzhou AQ reading

### 4. Functions Duplicated Between CNEMC Scripts

[prepro.py](data_prep/cnemc_site_data/prepro.py) and [build_documento_nc.py](data_prep/cnemc_site_data/build_documento_nc.py) share nearly identical versions of `parse_mixed_date()`, `discover_data_dirs()`, `path_has_data_segment()`, and the same regex patterns (`DATA_DIR_RE`, `DAILY_FILE_RE`).

**Recommendation:** Extract shared CNEMC utilities into a `data_prep/cnemc_site_data/cnemc_common.py`.

### 5. `sys.path` Manipulation for Mapbase Imports

Multiple files insert the `mapbase` directory into `sys.path` at runtime:
- [data_prep/cnemc_site_data/vis.py](data_prep/cnemc_site_data/vis.py) (line 108)
- [data_prep/himawari/hima.py](data_prep/himawari/hima.py) (line 23)
- [data_prep/merra-2/plot_event16_mean_integral_2x3.py](data_prep/merra-2/plot_event16_mean_integral_2x3.py) (line 49)
- [data_prep/merra-2/plot_event16_spatial_heatmaps.py](data_prep/merra-2/plot_event16_spatial_heatmaps.py) (line 46)

**Recommendation:** Convert `data_prep/mapbase/` into a proper importable package by adding an `__init__.py`, or add a root-level `pyproject.toml` so the package can be installed with `pip install -e .`.

---

## 🟠 Moderate Issues

### 6. Hardcoded Absolute Windows Paths

Nearly every script uses hardcoded `C:\DOCUMENTO\...` paths:

| File | Hardcoded Path Variable |
|------|------------------------|
| `build_documento_nc.py` | `INPUT_ROOT` |
| `prepro.py` | `DEFAULT_SCAN_ROOT` |
| `vis.py` | `NC_PATH` |
| `hima.py` | `DEFAULT_DAT_ROOT` |
| `fetch2.py` | `LOCAL_NC_GLOB` |
| `fetch_data.py` | `WEBCRAWLER_DIR`, `OTF_URL_LIST_FILE` |
| `fetchch.py` | `TXT_PATH` |
| `openfet.py` | `LINKLIST_PATH`, `LANZHOU_CSV_PATH` |

**Impact:** The project cannot be run on another machine without editing every script individually.

**Recommendation:**
- Use a central `config.yaml` or `.env` file for all data root paths
- Or use relative paths computed via `Path(__file__).resolve().parents[N]` where paths are predictable

### 7. Three Different Event Detection Implementations

| File | Function | Approach |
|------|----------|----------|
| `fetch2.py` | `detect_events()` | Dual-criteria (primary + secondary), segment-based with gap-merge |
| `fetch_data.py` | `detect_events_hourly()` | Single-criterion, flag-based with exclusive end timestamp |
| `openfet.py` | `detect_events()` | Single-criterion, `while`-loop scanner with inclusive end |

These use **different boundary conventions** (inclusive vs. exclusive end) and **different merge strategies**. Running `fetch_data.py` vs `fetch2.py` on the same data can produce different event counts, which is a reproducibility problem.

**Recommendation:** Consolidate into one parameterized `detect_events()` function with an optional dual-criteria mode.

### 8. Mixed Language in Code Comments

Comments and error messages are a mix of English and Chinese throughout:
- `fetch_data.py`: `# 事件识别（小时级）`, `raise RuntimeError("没有提取到小时序列")`
- `fetchch.py`: `# 去重但保序`, Chinese error messages
- `openfet.py`: `# 你需要改的配置（只改这里）`, `# 兰州坐标`

**Recommendation:** Standardize to English for all comments, docstrings, and error messages in a codebase intended for publication or collaboration.

### 9. No Dependency Management

There is no `requirements.txt`, `pyproject.toml`, or `environment.yml`. The project depends on a significant number of external packages:

```
numpy, pandas, xarray, netCDF4, matplotlib, cartopy, scipy,
shapely, requests, beautifulsoup4, lxml, deep_translator (optional),
certifi, urllib3
```

**Recommendation:** Create a `requirements.txt` pinning at least major versions, or a `pyproject.toml` for full reproducibility.

---

## 🔵 Minor Issues

### 10. Debug Script Left in Repository

[data_prep/himawari/_tmp_bt_check.py](data_prep/himawari/_tmp_bt_check.py) is a throwaway debug script with minimal variable names (`b`, `L`, `off`) and no docstrings. Not harmful, but it clutters the repository.

### 11. `prepro.py` Mixes Script and Notebook Cell Patterns

Lines 355–409 of [prepro.py](data_prep/cnemc_site_data/prepro.py) contain a `#%%` cell block that runs plotting code **outside** any `if __name__ == "__main__"` guard. If this file is ever imported as a module, that code will execute unintentionally.

### 12. Bare `except Exception` Blocks

Several files catch exceptions too broadly:
- `build_documento_nc.py` line 751 — `mem._mmap.close()` wrapped in a bare `except`  
- `fetch_data.py` lines 517–519 — `read_any_csv()` silently swallows all errors across 12 encoding/delimiter combinations

**Recommendation:** Catch specific exception types and use `logging.warning()` rather than silent suppression.

### 13. `openfet.py` Overwrites `~/_netrc` Without Warning

The `ensure_netrc()` function (line 56) unconditionally **overwrites** `~/_netrc` with a single machine entry. If the file already contains credentials for other services, they will be silently lost.

### 14. Output File Name Collisions

Both `fetch2.py` and `openfet.py` write identically named output files (`hourly_timeseries_with_event_mark.csv`, `dust_events_summary.csv`) to different output directories. This can cause confusion about which "events summary" is the authoritative one.

### 15. No Unit Tests

The project has no test suite. Given the complexity of event detection, interpolation logic, and coordinate transformations, even a small set of unit tests would significantly improve confidence during refactoring.

---

## 📋 Summary Table

| Severity | Issue | Estimated Effort |
|----------|-------|-----------------|
| 🔴 Critical | Hardcoded credentials in 4 files | Low — use env vars |
| 🔴 Critical | No `.gitignore` | Low — create file |
| 🟡 Significant | MERRA-2 code duplication (4 scripts) | Medium — extract utils module |
| 🟡 Significant | Duplicate code in CNEMC scripts | Medium — `cnemc_common.py` |
| 🟡 Significant | `sys.path` hacking for mapbase | Medium — package properly |
| 🟠 Moderate | Hardcoded absolute Windows paths | Medium — centralize config |
| 🟠 Moderate | 3 divergent event detection engines | Medium — unify |
| 🟠 Moderate | Mixed-language comments | Low — translate to English |
| 🟠 Moderate | No dependency management | Low — `requirements.txt` |
| 🔵 Minor | Debug script in repo | Trivial — delete or move |
| 🔵 Minor | Notebook cell outside `__main__` guard | Low — wrap in function |
| 🔵 Minor | Bare `except` blocks | Low — narrow exception types |
| 🔵 Minor | `_netrc` overwrite risk | Low — check before write |
| 🔵 Minor | Output filename collisions | Low — namespace outputs |
| 🔵 Minor | No unit tests | High effort, high value |

---

## 👍 Positive Highlights

| Aspect | Details |
|--------|---------|
| **Scientific rigor** | Annual invalidation + conservative short-gap interpolation in `build_documento_nc.py` and `prepro.py` is methodologically sound |
| **Satellite processing** | `hima.py` has a clean, well-typed pipeline: HSD binary → BT → Dust RGB → cloud mask → station collocation |
| **Memory management** | `build_documento_nc.py` uses `numpy.memmap` for memory-efficient year-long NetCDF construction |
| **Mapbase library** | `cnmap.py` (~1900 lines) is a comprehensive cartographic toolkit including DSATUR graph-coloring for province maps |
| **Robust downloading** | `fetchch.py` / `fetch2.py` handle URS redirect chains, partial download recovery, and HTML-response detection |
| **Translation pipeline** | `build_documento_nc.py` has a 3-tier translation fallback (deep_translator → direct API → rule-based) for Chinese station names |
| **Web crawler** | `zonghe.py` has clean exponential-backoff retry and dual-source (weather + AQI) per-month merge by normalized date key |

---

## Recommended Priority Actions

1. **Immediately** rotate the Earthdata password and remove credentials from all scripts
2. Add `.gitignore` (prevent future leaks) and `requirements.txt` (reproducibility)
3. Extract shared MERRA-2 utilities into a single module to eliminate 4-way duplication
4. Unify the three event detection implementations into one parameterized function
5. Move all hardcoded data paths to a central configuration file
