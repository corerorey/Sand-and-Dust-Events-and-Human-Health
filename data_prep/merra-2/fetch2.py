# -*- coding: utf-8 -*-
"""
MERRA-2 local subset pipeline (click-run, VSCode/PyCharm friendly)

Inputs:
  - Local `.SUB.nc` files (already downloaded)
Process:
  1) For each daily .SUB.nc:
       read selected MERRA variables on gridded lat/lon
       compute ROI area-mean over BOX (cos(lat) weighted)
       build hourly time series (UTC + local time)
  2) Detect dust events using DUCMASS as primary criterion and DUSMASS as auxiliary criterion
     and output event-marked hourly/event summary CSVs
  3) Build daily dust metrics and city-compatible CSVs
  4) Build 2021 aligned daily CSV (AQ columns + MERRA daily means)

Notes:
  - "minute precision": timestamps come from dataset, usually xx:30 for hourly time-averaged products.
"""

import os
import time
import csv
from glob import glob
from pathlib import Path
from typing import List, Tuple, Dict
from urllib.parse import urlparse, parse_qs, unquote

import numpy as np
import pandas as pd
import requests
import xarray as xr


# =========================
# 0) Configuration (EDIT ME)
# =========================
EARTHDATA_USERNAME = "correr27890"
EARTHDATA_PASSWORD = "AQN/RZ2Y&S5Rb+j"


# Local file glob pattern (already-downloaded SUB.nc files)
LOCAL_NC_GLOB = (
    r"C:\DOCUMENTO\Sand-and-Dust-Storms-and-Human-Health\downloads_merra2_subset"
    r"\MERRA2_4*.tavg1_2d_aer_Nx.2021*.SUB.nc"
)

# Kept for backward compatibility; not used in local-only mode
DOWNLOAD_DIR = r"./downloads_merra2_subset"  # kept for backward compatibility; not used in local-only mode

# Output directory (fixed to script location, independent of current working directory)
OUT_DIR = Path(__file__).resolve().parent / "out_dust_events"

# Openfet-compatible output settings
CITY_NAME = "lanzhou"
LANZHOU_CSV_PATH = (
    Path(__file__).resolve().parents[1] / "webcrawler" / "lanzhou_201101_202602.csv"
)
TARGET_YEAR = 2021
ALIGNED_DAILY_CSV = f"{CITY_NAME}_aq_merra_daily_aligned_{TARGET_YEAR}.csv"

# ROI box (W, S, E, N)
BOX = (102.0, 35.0, 104.5, 37.0)

# Event-detection criteria:
# - primary: DUCMASS
# - auxiliary: DUSMASS
PRIMARY_CRIT_VAR = "DUCMASS"
SECONDARY_CRIT_VAR = "DUSMASS"

# Variables exported in hourly output CSVs (kept compact but include both criteria)
EXTRACT_VARS = [PRIMARY_CRIT_VAR, SECONDARY_CRIT_VAR]

# Extraction mode over ROI
EXTRACT_MODE = "area_mean"   # "area_mean" / "area_max" / "nearest"

# UTC+8
LOCAL_TZ_OFFSET_HOURS = 8

# Event detection parameters
# Primary threshold (high quantile)
Q_PRIMARY = 0.95
# Lower primary threshold used with auxiliary criterion support
Q_PRIMARY_LOW = 0.90
# Auxiliary threshold for SECONDARY_CRIT_VAR
Q_SECONDARY = 0.90
MIN_EVENT_HOURS = 6
MERGE_GAP_HOURS = 2

# Skip README PDF links in txt
SKIP_PDF = True

# Download retry settings (used by downloader helpers)
RETRIES = 5
SLEEP_BETWEEN = 0.15
# =========================


def is_likely_netcdf_file(path: Path) -> bool:
    """
    Quick signature check:
      - NetCDF classic: starts with b"CDF"
      - NetCDF4/HDF5: starts with b"\\x89HDF\\r\\n\\x1a\\n"
    """
    try:
        with open(path, "rb") as f:
            head = f.read(8)
        return head.startswith(b"CDF") or head.startswith(b"\x89HDF\r\n\x1a\n")
    except Exception:
        return False


# Download helpers (handle URS 401 redirect chain)
def is_url(s: str) -> bool:
    return s.startswith("http://") or s.startswith("https://")


def filename_from_url(url: str) -> str:
    """
    Prefer LABEL=... (subset output, usually ends with .SUB.nc)
    Fallback to FILENAME=... (original path)
    """
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    if "LABEL" in qs and qs["LABEL"]:
        return qs["LABEL"][0]

    if "FILENAME" in qs and qs["FILENAME"]:
        fn_path = unquote(qs["FILENAME"][0])
        return os.path.basename(fn_path)

    base = os.path.basename(parsed.path)
    return base if base else "download.bin"


def read_urls_from_txt(txt_path: Path) -> List[str]:
    urls = []
    with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            u = line.strip()
            if not u or not is_url(u):
                continue
            if SKIP_PDF and u.lower().endswith(".pdf"):
                continue
            urls.append(u)

    # Deduplicate while preserving order
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            out.append(u)
            seen.add(u)
    return out


def get_follow_redirects_with_urs_auth(session: requests.Session, url: str, auth, max_hops: int = 10):
    """
    Manually follow redirects.
    Key point: when redirect target is urs.earthdata.nasa.gov, attach BasicAuth.
    """
    cur = url
    for _ in range(max_hops):
        r = session.get(cur, stream=True, allow_redirects=False, timeout=(30, 600))

        # final response (not a redirect)
        if r.status_code < 300 or r.status_code >= 400:
            return r

        loc = r.headers.get("Location")
        if not loc:
            return r

        # relative redirect
        if loc.startswith("/"):
            p = urlparse(cur)
            loc = f"{p.scheme}://{p.netloc}{loc}"

        # redirect to URS -> attach auth
        if "urs.earthdata.nasa.gov" in loc:
            r.close()
            r2 = session.get(loc, stream=True, allow_redirects=False, timeout=(30, 600), auth=auth)
            next_loc = r2.headers.get("Location", loc)
            r2.close()
            cur = next_loc
        else:
            r.close()
            cur = loc

    raise RuntimeError("Too many redirects: possible EULA issue or auth loop.")


def download_one(session: requests.Session, url: str, out_path: Path, auth, retries: int) -> bool:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".part")

    for attempt in range(1, retries + 1):
        try:
            r = get_follow_redirects_with_urs_auth(session, url, auth)

            if r.status_code in (401, 403):
                raise RuntimeError(
                    f"AUTH {r.status_code}. Common causes:\n"
                    f"1) Invalid Earthdata username/password\n"
                    f"2) EULAs not accepted in Earthdata profile\n"
                    f"3) Authorization changes not propagated yet (wait a few minutes)"
                )

            r.raise_for_status()

            total = int(r.headers.get("Content-Length", "0"))
            got = 0
            with open(tmp_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
                        got += len(chunk)
            r.close()

            if total > 0 and got < total:
                raise IOError(f"Incomplete download: {got}/{total}")

            tmp_path.replace(out_path)
            return True

        except Exception as e:
            print(f"[RETRY {attempt}/{retries}] {out_path.name} -> {e}")
            time.sleep(2 * attempt)

    try:
        if tmp_path.exists():
            tmp_path.unlink()
    except Exception:
        pass

    return False


def ensure_download_all(txt_path: Path, download_dir: Path, username: str, password: str) -> List[Path]:
    urls = read_urls_from_txt(txt_path)
    if not urls:
        raise RuntimeError("No URLs found in TXT file.")

    session = requests.Session()
    session.headers.update({"User-Agent": "merra2-dust-events/1.0"})
    auth = (username, password)

    files = []
    for i, url in enumerate(urls, 1):
        fname = filename_from_url(url)
        out_path = download_dir / fname
        files.append(out_path)

        if out_path.exists() and out_path.stat().st_size > 0:
            print(f"[SKIP] ({i}/{len(urls)}) {fname}")
            continue

        print(f"[GET ] ({i}/{len(urls)}) {fname}")
        ok = download_one(session, url, out_path, auth, retries=RETRIES)
        if ok:
            print(f"[OK  ] {fname}")
        else:
            print(f"[FAIL] {fname}")

        time.sleep(SLEEP_BETWEEN)

    return files


# Data extraction over ROI (area mean over BOX)
def guess_latlon_names(ds: xr.Dataset) -> Tuple[str, str]:
    lat = "lat" if "lat" in ds.coords else ("latitude" if "latitude" in ds.coords else None)
    lon = "lon" if "lon" in ds.coords else ("longitude" if "longitude" in ds.coords else None)
    if lat is None or lon is None:
        raise RuntimeError("Cannot find lat/lon coordinate names.")
    return lat, lon


def normalize_lon_for_ds(lon: float, lon_coord: xr.DataArray) -> float:
    lonmin = float(lon_coord.min())
    lonmax = float(lon_coord.max())
    if lonmin >= 0 and lon < 0:
        return lon % 360
    if lonmax <= 180 and lon > 180:
        return ((lon + 180) % 360) - 180
    return lon


def subset_box(ds: xr.Dataset, box: Tuple[float, float, float, float]) -> xr.Dataset:
    """Subset dataset to the target ROI box."""
    w, s, e, n = box
    lat_name, lon_name = guess_latlon_names(ds)

    # Normalize longitude convention to match dataset coordinates
    w2 = normalize_lon_for_ds(w, ds[lon_name])
    e2 = normalize_lon_for_ds(e, ds[lon_name])

    # Assume latitude/longitude are ascending
    return ds.sel({lat_name: slice(s, n), lon_name: slice(w2, e2)})


def _reduce_roi_series(
    da: xr.DataArray,
    ds: xr.Dataset,
    box: Tuple[float, float, float, float],
    lat_name: str,
    lon_name: str,
    extract_mode: str,
) -> np.ndarray:
    """Reduce one variable over ROI to a 1D time series."""
    if extract_mode == "nearest":
        # Use ROI center point
        w, s, e, n = box
        lat0 = (s + n) / 2
        lon0 = (w + e) / 2
        lon0 = normalize_lon_for_ds(lon0, ds[lon_name])
        sub = da.sel({lat_name: lat0, lon_name: lon0}, method="nearest")
        return sub.values

    if extract_mode == "area_max":
        sub = da.max(dim=(lat_name, lon_name), skipna=True)
        return sub.values

    if extract_mode == "area_mean":
        weights = np.cos(np.deg2rad(ds[lat_name]))
        weights.name = "weights"
        sub = da.weighted(weights).mean(dim=(lat_name, lon_name), skipna=True)
        return sub.values

    raise RuntimeError(f"Unknown EXTRACT_MODE={extract_mode}")


def extract_hourly_roi_from_file(
    nc_path: Path,
    extract_vars: List[str],
    box: Tuple[float, float, float, float],
    extract_mode: str,
) -> pd.DataFrame:
    ds = xr.open_dataset(nc_path, engine="netcdf4")
    try:
        if "time" not in ds.coords:
            raise RuntimeError(f"{nc_path.name} has no time coordinate.")

        ds = subset_box(ds, box)
        lat_name, lon_name = guess_latlon_names(ds)

        t_utc = pd.to_datetime(ds["time"].values)
        t_local = t_utc + pd.Timedelta(hours=LOCAL_TZ_OFFSET_HOURS)

        out = pd.DataFrame({
            "datetime_utc": t_utc,
            "datetime_local": t_local,
        })
        for var in extract_vars:
            if var not in ds.variables:
                out[var] = np.nan
                continue
            da = ds[var]  # dims time, lat, lon (usually)
            out[var] = _reduce_roi_series(da, ds, box, lat_name, lon_name, extract_mode)
        return out

    finally:
        ds.close()


def build_hourly_timeseries(
    nc_files: List[Path],
    extract_vars: List[str],
    box: Tuple[float, float, float, float],
    extract_mode: str,
) -> pd.DataFrame:
    frames = []
    bad_files = []
    for i, fp in enumerate(sorted(nc_files), 1):
        if not fp.exists():
            continue

        if not is_likely_netcdf_file(fp):
            bad_files.append(fp)
            print(f"[SKIP] ({i}/{len(nc_files)}) {fp.name} is not a valid NetCDF file")
            continue

        print(f"[READ] ({i}/{len(nc_files)}) {fp.name}")
        try:
            frames.append(extract_hourly_roi_from_file(fp, extract_vars, box, extract_mode))
        except Exception as e:
            bad_files.append(fp)
            print(f"[SKIP] ({i}/{len(nc_files)}) {fp.name} failed to parse: {e}")

    if not frames:
        hint = ""
        if bad_files:
            hint = (
                " All candidate files are invalid/unreadable. "
                "If the file starts with '<!DOCTYPE html>', it is an auth/error page, not NetCDF."
            )
        raise RuntimeError(f"No usable NetCDF files found.{hint}")

    ts = pd.concat(frames, axis=0)
    ts = ts.sort_values("datetime_local").drop_duplicates("datetime_local").reset_index(drop=True)
    return ts


# Event detection (DUCMASS primary, DUSMASS auxiliary)
def detect_events(
    ts: pd.DataFrame,
    primary_var: str,
    secondary_var: str,
    q_primary: float,
    q_primary_low: float,
    q_secondary: float,
    min_hours: int,
    merge_gap_hours: int,
) -> Tuple[pd.DataFrame, Dict[str, float], pd.Series, pd.Series]:
    """Detect dust events and return event summary plus hourly labels.

    Composite exceedance rule:
      exceed = (primary > thr_primary) OR ((primary > thr_primary_low) AND (secondary > thr_secondary))
    This keeps primary criterion dominant and uses secondary criterion as support.
    """
    full_index = pd.DatetimeIndex(pd.to_datetime(ts["datetime_local"]).to_numpy())
    s_primary = pd.Series(ts[primary_var].to_numpy(), index=full_index).sort_index()
    has_secondary = secondary_var in ts.columns and pd.api.types.is_numeric_dtype(ts[secondary_var])
    s_secondary = pd.Series(ts[secondary_var].to_numpy(), index=full_index).sort_index() if has_secondary else None

    s_valid = s_primary.dropna()
    if s_valid.empty:
        thresholds = {"primary": np.nan, "primary_low": np.nan, "secondary": np.nan}
        events = pd.DataFrame(columns=[
            "event_id", "start_local", "end_local", "start_utc", "end_utc",
            "duration_hours", "mean_crit_span", "mean_crit_exceed", "max_crit",
            "threshold", "exceed_fraction",
            "primary_var", "secondary_var",
            "threshold_primary", "threshold_primary_low", "threshold_secondary",
            "mean_secondary_span", "mean_secondary_exceed", "max_secondary",
            "exceed_primary_fraction", "exceed_support_fraction", "exceed_composite_fraction",
        ])
        return events, thresholds, pd.Series(0, index=full_index), pd.Series(0, index=full_index)

    thr_primary = float(s_valid.quantile(q_primary))
    thr_primary_low = float(s_valid.quantile(q_primary_low))
    if has_secondary:
        s2_valid = s_secondary.dropna()
        thr_secondary = float(s2_valid.quantile(q_secondary)) if len(s2_valid) else np.nan
    else:
        thr_secondary = np.nan

    exceed_primary = s_primary > thr_primary
    if has_secondary and np.isfinite(thr_secondary):
        exceed_support = (s_primary > thr_primary_low) & (s_secondary > thr_secondary)
    else:
        exceed_support = pd.Series(False, index=full_index)
    exceed = exceed_primary | exceed_support

    # Segment extraction using exceed=True timestamps.
    t_ex = exceed[exceed].index
    segments = []
    if len(t_ex) > 0:
        st = t_ex[0]
        prev = t_ex[0]
        for t in t_ex[1:]:
            if (t - prev) / pd.Timedelta(hours=1) <= 1.01:
                prev = t
            else:
                segments.append((st, prev))
                st = t
                prev = t
        segments.append((st, prev))

    # Filter segments by minimum duration.
    segs = []
    for a, b in segments:
        dur = int(round(((b - a) / pd.Timedelta(hours=1)) + 1))
        if dur >= min_hours:
            segs.append((a, b))

    if not segs:
        thresholds = {"primary": thr_primary, "primary_low": thr_primary_low, "secondary": thr_secondary}
        events = pd.DataFrame(columns=[
            "event_id", "start_local", "end_local", "start_utc", "end_utc",
            "duration_hours", "mean_crit_span", "mean_crit_exceed", "max_crit",
            "threshold", "exceed_fraction",
            "primary_var", "secondary_var",
            "threshold_primary", "threshold_primary_low", "threshold_secondary",
            "mean_secondary_span", "mean_secondary_exceed", "max_secondary",
            "exceed_primary_fraction", "exceed_support_fraction", "exceed_composite_fraction",
        ])
        return events, thresholds, pd.Series(0, index=full_index), pd.Series(0, index=full_index)

    # Merge close segments; gap = next_start - current_end - 1 hour.
    merged = [segs[0]]
    for a, b in segs[1:]:
        la, lb = merged[-1]
        gap = (a - lb) / pd.Timedelta(hours=1) - 1
        if gap <= merge_gap_hours:
            merged[-1] = (la, b)
        else:
            merged.append((a, b))

    event_id_full = pd.Series(0, index=full_index)
    flag_full = pd.Series(0, index=full_index)

    map_local_to_utc = pd.Series(pd.to_datetime(ts["datetime_utc"]).to_numpy(), index=full_index)
    rows = []
    for eid, (a, b) in enumerate(merged, 1):
        span_mask = (full_index >= a) & (full_index <= b)
        if not span_mask.any():
            continue

        span_primary = pd.Series(ts.loc[span_mask, primary_var].to_numpy(), index=full_index[span_mask])
        span_secondary = (
            pd.Series(ts.loc[span_mask, secondary_var].to_numpy(), index=full_index[span_mask])
            if has_secondary else pd.Series(np.nan, index=full_index[span_mask])
        )

        span_exceed_primary = exceed_primary.reindex(full_index[span_mask]).fillna(False)
        span_exceed_support = exceed_support.reindex(full_index[span_mask]).fillna(False)
        span_exceed_composite = exceed.reindex(full_index[span_mask]).fillna(False)

        primary_exceed_vals = span_primary[span_exceed_primary]
        secondary_exceed_vals = span_secondary[span_exceed_support]

        duration_hours = int(span_mask.sum())
        mean_primary_span = float(span_primary.mean()) if len(span_primary) else np.nan
        mean_primary_exceed = float(primary_exceed_vals.mean()) if len(primary_exceed_vals) else np.nan
        max_primary = float(span_primary.max()) if len(span_primary) else np.nan

        mean_secondary_span = float(span_secondary.mean()) if len(span_secondary) else np.nan
        mean_secondary_exceed = float(secondary_exceed_vals.mean()) if len(secondary_exceed_vals) else np.nan
        max_secondary = float(span_secondary.max()) if len(span_secondary) else np.nan

        frac_primary = float(span_exceed_primary.mean()) if len(span_primary) else 0.0
        frac_support = float(span_exceed_support.mean()) if len(span_primary) else 0.0
        frac_composite = float(span_exceed_composite.mean()) if len(span_primary) else 0.0

        event_id_full.iloc[span_mask] = eid
        flag_full.iloc[span_mask] = 1

        start_utc = pd.to_datetime(map_local_to_utc.loc[a])
        end_utc = pd.to_datetime(map_local_to_utc.loc[b])

        rows.append({
            "event_id": eid,
            "start_local": a,
            "end_local": b,
            "start_utc": start_utc,
            "end_utc": end_utc,
            "duration_hours": duration_hours,
            # Backward-compatible primary fields.
            "mean_crit_span": mean_primary_span,
            "mean_crit_exceed": mean_primary_exceed,
            "max_crit": max_primary,
            "threshold": thr_primary,
            "exceed_fraction": frac_composite,
            # Explicit multi-criteria fields.
            "primary_var": primary_var,
            "secondary_var": secondary_var if has_secondary else "",
            "threshold_primary": thr_primary,
            "threshold_primary_low": thr_primary_low,
            "threshold_secondary": thr_secondary,
            "mean_secondary_span": mean_secondary_span,
            "mean_secondary_exceed": mean_secondary_exceed,
            "max_secondary": max_secondary,
            "exceed_primary_fraction": frac_primary,
            "exceed_support_fraction": frac_support,
            "exceed_composite_fraction": frac_composite,
        })

    events = pd.DataFrame(rows)
    thresholds = {"primary": thr_primary, "primary_low": thr_primary_low, "secondary": thr_secondary}
    return events, thresholds, event_id_full, flag_full


def sniff_delimiter(path: Path) -> str:
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        sample = f.read(4096)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", "\t", ";"])
        return dialect.delimiter
    except Exception:
        return "\t" if sample.count("\t") > 0 else ","


def _parse_lanzhou_date(date_series: pd.Series) -> pd.Series:
    raw = date_series.astype(str).str.strip()
    cleaned = (
        raw
        .str.replace("\u5e74", "-", regex=False)
        .str.replace("\u6708", "-", regex=False)
        .str.replace("\u65e5", "", regex=False)
        .str.replace("/", "-", regex=False)
        .str.replace(".", "-", regex=False)
    )
    dt_raw = pd.to_datetime(raw, format="mixed", errors="coerce")
    dt_clean = pd.to_datetime(cleaned, format="mixed", errors="coerce")
    return dt_raw.fillna(dt_clean)


def read_lanzhou_aq(path: Path, target_year: int) -> pd.DataFrame:
    """Read Lanzhou CSV and keep only date + AQ-related columns."""
    delim = sniff_delimiter(path)
    df = pd.read_csv(path, sep=delim, engine="python")
    if df.empty:
        return pd.DataFrame(columns=["date"])

    if "date" in df.columns:
        date_src = df["date"]
    else:
        date_src = df.iloc[:, 0]

    df = df.copy()
    df["date"] = _parse_lanzhou_date(date_src).dt.date
    df = df.dropna(subset=["date"]).copy()
    df = df[df["date"].map(lambda d: d.year == target_year)].copy()

    aq_cols = [c for c in df.columns if str(c).lower().startswith("aqi")]
    return df[["date"] + aq_cols].reset_index(drop=True)


def build_daily_mean_table(ts: pd.DataFrame, target_year: int) -> pd.DataFrame:
    """Build daily means for all numeric MERRA columns."""
    work = ts.copy()
    work["date"] = pd.to_datetime(work["datetime_local"]).dt.date

    value_cols = [
        c for c in work.columns
        if c not in {"datetime_utc", "datetime_local"} and pd.api.types.is_numeric_dtype(work[c])
    ]
    if not value_cols:
        raise RuntimeError("No numeric MERRA columns found to aggregate.")

    daily = work.groupby("date", as_index=False)[value_cols].mean()
    daily = daily.rename(columns={c: f"{c}_daily_mean" for c in value_cols})
    daily = daily[daily["date"].map(lambda d: d.year == target_year)].copy()
    return daily


def build_dust_daily_table(
    ts: pd.DataFrame,
    primary_var: str,
    thresholds: Dict[str, float],
    secondary_var: str | None = None,
    target_year: int | None = None,
) -> pd.DataFrame:
    """Build daily dust stats from hourly criteria variables.

    `dust_*` columns remain backward-compatible and map to primary criterion.
    """
    work = ts.assign(date=pd.to_datetime(ts["datetime_local"]).dt.date)
    daily_primary = work.groupby("date", as_index=False)[primary_var].agg(primary_mean="mean", primary_max="max")
    daily = daily_primary.rename(columns={"primary_mean": "dust_mean", "primary_max": "dust_max"})
    daily[f"{primary_var.lower()}_mean"] = daily["dust_mean"]
    daily[f"{primary_var.lower()}_max"] = daily["dust_max"]

    has_secondary = secondary_var is not None and secondary_var in ts.columns
    if has_secondary:
        daily_secondary = work.groupby("date", as_index=False)[secondary_var].agg(sec_mean="mean", sec_max="max")
        daily = daily.merge(daily_secondary, on="date", how="left")
        daily[f"{secondary_var.lower()}_mean"] = daily["sec_mean"]
        daily[f"{secondary_var.lower()}_max"] = daily["sec_max"]
        daily = daily.drop(columns=["sec_mean", "sec_max"])

    thr_primary = thresholds.get("primary", np.nan)
    thr_primary_low = thresholds.get("primary_low", np.nan)
    thr_secondary = thresholds.get("secondary", np.nan)

    if has_secondary and np.isfinite(thr_secondary):
        daily["dust_flag"] = (
            (daily["dust_max"] >= thr_primary)
            | ((daily["dust_max"] >= thr_primary_low) & (daily[f"{secondary_var.lower()}_max"] >= thr_secondary))
        ).astype(int)
    else:
        daily["dust_flag"] = (daily["dust_max"] >= thr_primary).astype(int)

    daily["threshold_primary"] = thr_primary
    daily["threshold_primary_low"] = thr_primary_low
    daily["threshold_secondary"] = thr_secondary
    if target_year is not None:
        daily = daily[daily["date"].map(lambda d: d.year == target_year)].copy()
    return daily


def to_city_event_table(events: pd.DataFrame) -> pd.DataFrame:
    """Convert internal event summary to city-style event output columns."""
    if events.empty:
        return pd.DataFrame(columns=[
            "start_datetime", "end_datetime", "duration_hours",
            "mean_critical", "max_critical", "threshold",
            "mean_secondary", "max_secondary"
        ])
    out = pd.DataFrame({
        "start_datetime": events["start_local"],
        "end_datetime": events["end_local"],
        "duration_hours": events["duration_hours"],
        "mean_critical": events["mean_crit_exceed"],
        "max_critical": events["max_crit"],
        "threshold": events["threshold"],
    })
    if "mean_secondary_exceed" in events.columns:
        out["mean_secondary"] = events["mean_secondary_exceed"]
    if "max_secondary" in events.columns:
        out["max_secondary"] = events["max_secondary"]
    return out


def main():
    out_dir = Path(OUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) Read local .SUB.nc files (no downloading)
    print("\n=== Step 1: Read local .SUB.nc files ===")
    nc_files = [Path(p) for p in sorted(glob(LOCAL_NC_GLOB))]
    nc_files = [p for p in nc_files if p.exists() and p.suffix.lower() in (".nc", ".nc4")]

    if not nc_files:
        raise RuntimeError(f"No local .nc files matched LOCAL_NC_GLOB: {LOCAL_NC_GLOB}")

    print(f"Matched local files: {len(nc_files)}")

    # 2) Build hourly ROI time series
    print("\n=== Step 2: Build hourly ROI time series ===")
    extract_vars = list(dict.fromkeys(EXTRACT_VARS))
    ts = build_hourly_timeseries(nc_files, extract_vars, BOX, EXTRACT_MODE)
    if PRIMARY_CRIT_VAR not in ts.columns:
        raise RuntimeError(f"Primary criterion variable missing in hourly series: {PRIMARY_CRIT_VAR}")

    # 3) Detect events and mark hourly series
    print("\n=== Step 3: Detect events and mark hourly series ===")
    events, thresholds, event_id_series, flag_series = detect_events(
        ts=ts,
        primary_var=PRIMARY_CRIT_VAR,
        secondary_var=SECONDARY_CRIT_VAR,
        q_primary=Q_PRIMARY,
        q_primary_low=Q_PRIMARY_LOW,
        q_secondary=Q_SECONDARY,
        min_hours=MIN_EVENT_HOURS,
        merge_gap_hours=MERGE_GAP_HOURS,
    )

    idx_full = pd.to_datetime(ts["datetime_local"])
    ts_marked = ts.copy()
    ts_marked["event_id"] = event_id_series.reindex(idx_full).fillna(0).astype(int).values
    ts_marked["event_flag"] = flag_series.reindex(idx_full).fillna(0).astype(int).values
    ts_marked["threshold_used"] = thresholds.get("primary", np.nan)  # backward compatibility
    ts_marked["threshold_primary"] = thresholds.get("primary", np.nan)
    ts_marked["threshold_primary_low"] = thresholds.get("primary_low", np.nan)
    ts_marked["threshold_secondary"] = thresholds.get("secondary", np.nan)

    ts_marked["exceed_primary"] = (ts_marked[PRIMARY_CRIT_VAR] > thresholds.get("primary", np.nan)).astype(int)
    if SECONDARY_CRIT_VAR in ts_marked.columns and np.isfinite(thresholds.get("secondary", np.nan)):
        ts_marked["exceed_support"] = (
            (ts_marked[PRIMARY_CRIT_VAR] > thresholds.get("primary_low", np.nan))
            & (ts_marked[SECONDARY_CRIT_VAR] > thresholds.get("secondary", np.nan))
        ).astype(int)
    else:
        ts_marked["exceed_support"] = 0
    # Composite exceedance (kept in legacy column name too).
    ts_marked["exceed_threshold"] = ((ts_marked["exceed_primary"] == 1) | (ts_marked["exceed_support"] == 1)).astype(int)

    # 4) Output complete CSV set (keep all commonly-used files)
    print("\n=== Step 4: Write complete CSV outputs ===")
    hourly_mark_csv = out_dir / "hourly_timeseries_with_event_mark.csv"
    events_summary_csv = out_dir / "dust_events_summary.csv"
    city_hourly_csv = out_dir / f"{CITY_NAME}_merra2_timeseries_hourly.csv"
    city_events_csv = out_dir / f"{CITY_NAME}_dust_events.csv"
    city_daily_csv = out_dir / f"{CITY_NAME}_dust_daily.csv"
    aligned_csv = out_dir / ALIGNED_DAILY_CSV

    ts_marked.to_csv(hourly_mark_csv, index=False, encoding="utf-8-sig")
    events.to_csv(events_summary_csv, index=False, encoding="utf-8-sig")
    ts_marked.to_csv(city_hourly_csv, index=False, encoding="utf-8-sig")
    to_city_event_table(events).to_csv(city_events_csv, index=False, encoding="utf-8-sig")

    dust_daily = build_dust_daily_table(
        ts_marked,
        primary_var=PRIMARY_CRIT_VAR,
        thresholds=thresholds,
        secondary_var=SECONDARY_CRIT_VAR,
        target_year=TARGET_YEAR,
    )
    dust_daily.to_csv(city_daily_csv, index=False, encoding="utf-8-sig")

    # 5) Build 2021 aligned AQ + daily-mean MERRA output
    print(f"\n=== Step 5: Build aligned AQ + MERRA daily means ({TARGET_YEAR}) ===")
    daily_mean = build_daily_mean_table(ts, TARGET_YEAR)

    lanzhou_path = Path(LANZHOU_CSV_PATH)
    if not lanzhou_path.exists():
        raise RuntimeError(f"Lanzhou CSV not found: {lanzhou_path}")

    lanzhou_aq = read_lanzhou_aq(lanzhou_path, TARGET_YEAR)
    if lanzhou_aq.empty:
        raise RuntimeError(f"No Lanzhou AQ rows found for {TARGET_YEAR}")

    aligned = lanzhou_aq.merge(daily_mean, on="date", how="inner").sort_values("date")
    aligned.to_csv(aligned_csv, index=False, encoding="utf-8-sig")

    print("\n=== DONE ===")
    print(f"Hourly marked     -> {hourly_mark_csv.resolve()}")
    print(f"Events summary    -> {events_summary_csv.resolve()}")
    print(f"City hourly       -> {city_hourly_csv.resolve()}")
    print(f"City events       -> {city_events_csv.resolve()}")
    print(f"City dust daily   -> {city_daily_csv.resolve()}")
    print(f"Aligned daily     -> {aligned_csv.resolve()}")
    print(f"Aligned rows      -> {len(aligned)}")


if __name__ == "__main__":
    main()
