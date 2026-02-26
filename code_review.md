# Code Review: Sand-and-Dust-Storms-and-Human-Health

**Reviewed:** 20+ Python source files, 1 Jupyter notebook reference, full README  
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

> [!CAUTION]
> **Earthdata credentials are hardcoded in plain text** across **4 files** and committed to the Git repository.

| File | Line |
|------|------|
| [fetch2.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch2.py#L38-L39) | `EARTHDATA_USERNAME = "correr27890"` / `EARTHDATA_PASSWORD = "AQN/RZ2Y&S5Rb+j"` |
| [fetch_data.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch_data.py#L18-L19) | Same credentials |
| [fetchch.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetchch.py#L18-L19) | Same credentials |
| [openfet.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/openfet.py#L17-L18) | Same credentials |

**Recommendation:**
- **Immediately rotate the password** on your Earthdata account
- Use environment variables (`os.environ["EARTHDATA_USERNAME"]`) or a `.env` file (with `.gitignore`)
- Add a [_netrc](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/openfet.py#56-72) / `.netrc` approach as [openfet.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/openfet.py) already demonstrates, but don't hardcode it
- Add `*.env` and [_netrc](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/openfet.py#56-72) to `.gitignore`

### 2. No `.gitignore` File

There is no `.gitignore` preventing sensitive files, output artifacts, or IDE metadata from being committed. This compounds the credential leak above.

**Recommended `.gitignore` contents:**
```
*.env
_netrc
.netrc
__pycache__/
*.pyc
.idea/
.vscode/
*.part
_tmp_*/
out_*/
nc_out/
downloads_*/
```

---

## 🟡 Significant Issues

### 3. Massive Code Duplication Across MERRA-2 Scripts

Four scripts ([fetch2.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch2.py), [fetch_data.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch_data.py), [fetchch.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetchch.py), [openfet.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/openfet.py)) contain **heavily duplicated logic**:

| Duplicated Component | Files |
|---------------------|-------|
| URL redirect auth handler | [fetch2.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch2.py), [fetchch.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetchch.py), [fetch_data.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch_data.py) |
| [filename_from_url()](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch2.py#118-135) | [fetch2.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch2.py), [fetchch.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetchch.py) |
| [read_lanzhou_aq()](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/openfet.py#256-274) + [_parse_lanzhou_date()](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/openfet.py#241-254) | [fetch2.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch2.py), [openfet.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/openfet.py) |
| [build_daily_mean_table()](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch2.py#621-637) | [fetch2.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch2.py), [openfet.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/openfet.py) |
| [sniff_delimiter()](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch2.py#575-583) | [fetch2.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch2.py), [openfet.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/openfet.py) |
| [normalize_lon_for_ds()](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch_data.py#371-379) | [fetch2.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch2.py), [fetch_data.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch_data.py), [openfet.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/openfet.py) |
| Event detection logic | [fetch2.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch2.py), [fetch_data.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/fetch_data.py), [openfet.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/openfet.py) (3 different implementations!) |

**Impact:** Bug fixes need to be applied in 3-4 places; divergent behavior between scripts is easy to miss.

**Recommendation:** Extract shared utilities into a `merra2_utils.py` module:
- `auth.py` — credential loading, redirect handler, download functions
- `event_detection.py` — unified event detection with configurable thresholds
- `io_utils.py` — CSV sniffing, date parsing, Lanzhou AQ reading

### 4. Functions Duplicated Between [prepro.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/cnemc_site_data/prepro.py) and [build_documento_nc.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/cnemc_site_data/build_documento_nc.py)

Both share nearly identical versions of: [parse_mixed_date()](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/cnemc_site_data/build_documento_nc.py#65-73), [discover_data_dirs()](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/cnemc_site_data/prepro.py#82-96), [path_has_data_segment()](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/cnemc_site_data/prepro.py#98-101), and the same regex patterns (`DATA_DIR_RE`, `DAILY_FILE_RE`).

**Recommendation:** Extract shared CNEMC utilities into a `cnemc_common.py`.

### 5. `sys.path` Manipulation for Mapbase Imports

Multiple files insert mapbase directory into `sys.path` at runtime:
- [vis.py:108](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/cnemc_site_data/vis.py#L108)
- [hima.py:23](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/himawari/hima.py#L23)
- [plot_event16_mean_integral_2x3.py:49](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/plot_event16_mean_integral_2x3.py#L49)
- [plot_event16_spatial_heatmaps.py:46](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/plot_event16_spatial_heatmaps.py#L46)

**Recommendation:** Convert [mapbase](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/merra-2/plot_event16_spatial_heatmaps.py#43-57) into a proper importable package by adding an `__init__.py`, or structure the project with a top-level `pyproject.toml` so relative imports work naturally.

---

## 🟠 Moderate Issues

### 6. Hardcoded Absolute Windows Paths

Nearly every script uses hardcoded `C:\DOCUMENTO\...` paths:

| File | Example |
|------|---------|
| `build_documento_nc.py` | `INPUT_ROOT = r"C:\DOCUMENTO"` |
| `prepro.py` | `DEFAULT_SCAN_ROOT = r"C:\DOCUMENTO"` |
| `vis.py` | `NC_PATH = r"C:\DOCUMENTO\nc_out\..."` |
| `hima.py` | `DEFAULT_DAT_ROOT = Path(r"C:\DOCUMENTO\himawari")` |
| `fetch2.py` | `LOCAL_NC_GLOB` with full path |
| `fetch_data.py` | `WEBCRAWLER_DIR`, `OTF_URL_LIST_FILE` |
| `fetchch.py` | `TXT_PATH` |
| `openfet.py` | `LINKLIST_PATH`, `LANZHOU_CSV_PATH` |

**Impact:** The project is not portable or reproducible on another machine without editing every script.

**Recommendation:** 
- Use a central config file (`config.yaml` or `.env`) for all data root paths 
- Or use relative paths from the project root via `Path(__file__).resolve().parents[N]`

### 7. Three Different Event Detection Implementations

| File | Function | Approach |
|------|----------|----------|
| `fetch2.py` | `detect_events()` | Dual-criteria (primary + secondary), segment-based with merge |
| `fetch_data.py` | `detect_events_hourly()` | Single-criterion, flag-based with exclusive end |
| `openfet.py` | `detect_events()` | Single-criterion, `while`-loop scanner |

These use **different boundary conventions** (inclusive vs exclusive end timestamps) and **different merge strategies**. A researcher running `fetch_data.py` vs `fetch2.py` could get different event counts from the same data.

**Recommendation:** Consolidate into one parameterized `detect_events()` with an option for dual-criteria support.

### 8. Mixed Language in Code Comments

Comments are a mix of English and Chinese across the codebase:
- `fetch_data.py`: `# 事件识别（小时级）`, `# 去重`, `raise RuntimeError("没有提取到小时序列")`
- `fetchch.py`: `# 去重但保序`, error messages in Chinese
- `openfet.py`: `# 你需要改的配置`, `# 兰州坐标`

**Recommendation:** Standardize to English for comments, error messages, and variable names in a research codebase intended for publication/sharing.

### 9. No Dependency Management

There's no `requirements.txt`, `pyproject.toml`, or `environment.yml`. The project depends on:
```
numpy, pandas, xarray, netCDF4, matplotlib, cartopy, scipy, 
shapely, requests, beautifulsoup4, lxml, deep_translator (optional),
certifi, urllib3
```

**Recommendation:** Create a `requirements.txt` or `pyproject.toml` for reproducibility.

---

## 🔵 Minor Issues

### 10. `_tmp_bt_check.py` — Debug Script Left in Repo

[_tmp_bt_check.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/himawari/_tmp_bt_check.py) is a throwaway debug script with minimal variable names (`b`, `L`, `off`) and no docstrings. Not harmful, but clutters the repo.

### 11. `prepro.py` Mixes Script and Notebook Cell Patterns

Lines 355-409 of [prepro.py](file:///c:/DOCUMENTO/Sand-and-Dust-Storms-and-Human-Health/data_prep/cnemc_site_data/prepro.py#L355-L409) contain a `#%%` cell block that runs plotting code **outside** `if __name__ == "__main__"`. If imported as a module, this code would execute unintentionally.

### 12. Bare `except Exception` Blocks

Several files catch broad exceptions silently:
- `build_documento_nc.py:751` — `mem._mmap.close()` in bare except
- `fetch_data.py:517-519` — `read_any_csv()` swallows all errors across 12 encoding/delimiter combinations

**Recommendation:** Catch specific exceptions and log warnings.

### 13. `openfet.py` Writes to User Home `.netrc` Without Warning

The `ensure_netrc()` function at line 56 **overwrites** `~/_netrc` without checking if it already exists with other credentials. This could break other tools.

### 14. Output File Collisions

Both `fetch2.py` and `openfet.py` write to identically named output files (`hourly_timeseries_with_event_mark.csv`, `dust_events_summary.csv`) but in different output directories. This can cause confusion about which "events summary" is authoritative.

### 15. No Unit Tests or Automated Tests

The project has no test suite. Given the complexity of event detection, interpolation logic, and coordinate transformations, these would be high-value investments.

---

## 📋 Summary Table

| Severity | Issue | Effort to Fix |
|----------|-------|---------------|
| 🔴 Critical | Hardcoded credentials in 4 files | Low — move to env vars |
| 🔴 Critical | No `.gitignore` | Low — create file |
| 🟡 Significant | Massive MERRA-2 code duplication | Medium — extract shared utils |
| 🟡 Significant | Duplicate code in CNEMC scripts | Medium — extract common module |
| 🟡 Significant | `sys.path` hacking for mapbase | Medium — package properly |
| 🟠 Moderate | Hardcoded absolute paths | Medium — centralize config |
| 🟠 Moderate | 3 divergent event detection engines | Medium — unify |
| 🟠 Moderate | Mixed-language comments | Low — translate |
| 🟠 Moderate | No dependency management | Low — create requirements.txt |
| 🔵 Minor | Debug script in repo | Trivial — delete or move |
| 🔵 Minor | Cell code outside `__main__` guard | Low — wrap in function |
| 🔵 Minor | Bare except blocks | Low — narrow exceptions |
| 🔵 Minor | `.netrc` overwrite risk | Low — check before write |
| 🔵 Minor | Output file name collisions | Low — namespace or rename |
| 🔵 Minor | No tests | High (but high-value) |

---

## Positive Highlights 👍

| Aspect | Details |
|--------|---------|
| **Scientific rigor** | Annual invalidation + conservative short-gap interpolation in `build_documento_nc.py` and `prepro.py` is methodologically sound |
| **Satellite processing** | `hima.py` has a clean, well-typed pipeline from raw HSD binary → BT → Dust RGB → cloud mask → station collocation |
| **Memory management** | `build_documento_nc.py` uses `numpy.memmap` to handle large year-long NetCDF builds without blowing memory |
| **Mapbase library** | `cnmap.py` (1926 lines) is a comprehensive, self-contained cartographic toolkit with DSATUR graph coloring for province maps — impressive |
| **Robust downloading** | `fetchch.py`/`fetch2.py` handle URS redirect chains, partial download recovery, and HTML-instead-of-NetCDF detection |
| **Translation pipeline** | `build_documento_nc.py` has a 3-tier translation fallback (deep_translator → direct API → rule-based) for Chinese station metadata |
| **Web crawler** | `zonghe.py` has clean exponential-backoff retry logic and dual-source (weather + AQI) per-month merge by normalized date keys |

---

## Recommended Priority Actions

1. **Immediately** rotate Earthdata password and remove credentials from code
2. Add `.gitignore` and `requirements.txt`
3. Extract shared MERRA-2 utilities to eliminate 4-way duplication  
4. Unify event detection into a single parameterized function
5. Move hardcoded paths to a central configuration mechanism
