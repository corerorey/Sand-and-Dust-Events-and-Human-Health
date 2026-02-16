import os
import re
import csv
import stat
import math
import urllib.parse
from pathlib import Path
from datetime import timedelta

import numpy as np
import pandas as pd
import xarray as xr

# =========================
# 0) 你需要改的配置（只改这里）
# =========================
EARTHDATA_USERNAME = "correr27890"
EARTHDATA_PASSWORD = "AQN/RZ2Y&S5Rb+j"


# 你这次上传的 linkslist（txt）
LINKLIST_PATH = r"C:\Users\Cory Kong\Downloads\subset_M2T1NXAER_5.12.4_20260216_082525_.txt"

# 你本地兰州的 csv（Windows 路径）
LANZHOU_CSV_PATH = r"C:\DOCUMENTO\Sand-and-Dust-Storms-and-Human-Health\data_prep\webcrawler\lanzhou_201101_202602.csv"

OUT_DIR = str(Path(__file__).resolve().parent / "out_merra2_dust")
TARGET_YEAR = 2021
os.makedirs(OUT_DIR, exist_ok=True)

# 目标城市（兰州）坐标：你可改成别的城市
CITY_NAME = "lanzhou"
ALIGNED_DAILY_CSV = f"{CITY_NAME}_aq_merra_daily_aligned_{TARGET_YEAR}.csv"
CITY_LAT = 36.0611
CITY_LON = 103.8343

# 取城市周围一个“面积窗口”做平均（单位：度）
# 例如 1.0 表示 lat/lon 各 ±1° 的盒子（大概 200 km 量级）
WINDOW_DEG = 1.0

# 你要作为“critical criteria”的变量名（优先 DUSMASS；没有就用 DUCMASS / DUEXTTAU）
CRITICAL_VAR = "DUSMASS"

# 事件阈值：用该变量时间序列的百分位做阈值（推荐先用 90 或 95 试）
THRESH_PERCENTILE = 90

# 最短持续时长（小时）：小于这个的高值段不算 event
MIN_EVENT_HOURS = 3

# MERRA-2 time 通常按 UTC；你若要对齐中国本地日（UTC+8），这里设 8
LOCAL_TZ_OFFSET_HOURS = 8

# =========================
# 1) 认证：写 netrc（让 Hyrax/OPeNDAP 自动带账号密码）
# =========================
def ensure_netrc(username: str, password: str, host: str = "goldsmr4.gesdisc.eosdis.nasa.gov"):
    """
    Create ~/.netrc (Linux/macOS) or ~/_netrc (Windows) for Earthdata auth.
    """
    home = Path.home()
    netrc_path = home / ( "_netrc" if os.name == "nt" else ".netrc" )

    content = f"machine {host} login {username} password {password}\n"
    # 追加/覆盖：为简单起见直接写入（你若已有 netrc，可以自行改成“追加不覆盖”）
    netrc_path.write_text(content, encoding="utf-8")

    # *nix 需要 600 权限
    if os.name != "nt":
        netrc_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

    return str(netrc_path)

# =========================
# 2) 从 linkslist 解析出：Hyrax OPeNDAP URL + 变量列表 + BBOX
# =========================
def parse_linkslist_to_opendap(txt_path: str):
    """
    Your txt contains many lines like:
    https://goldsmr4.../HTTP_services.cgi?FILENAME=...&BBOX=...&VARIABLES=...&LABEL=...SUB.nc

    We extract:
    - decoded FILENAME path: /data/MERRA2/.../MERRA2_###....nc4
    - VARIABLES (comma-separated)
    - BBOX: south,west,north,east
    then build Hyrax OPeNDAP URL:
    https://goldsmr4.gesdisc.eosdis.nasa.gov/opendap/hyrax + FILENAME
    """
    opendap_urls = []
    vars_set = None
    bbox = None

    with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line.startswith("http"):
                continue

            u = urllib.parse.urlparse(line)
            q = urllib.parse.parse_qs(u.query)

            if "FILENAME" not in q:
                continue

            filename = urllib.parse.unquote(q["FILENAME"][0])  # like /data/MERRA2/...
            # record BBOX / VARIABLES (all links typically share the same)
            if bbox is None and "BBOX" in q:
                bbox_vals = q["BBOX"][0].split(",")
                if len(bbox_vals) == 4:
                    bbox = tuple(map(float, bbox_vals))  # (south, west, north, east)

            if vars_set is None and "VARIABLES" in q:
                # VARIABLES are comma-separated, sometimes URL-encoded
                vars_set = q["VARIABLES"][0].split(",")
                vars_set = [v.strip() for v in vars_set if v.strip()]

            # build hyrax opendap url
            hyrax = "https://goldsmr4.gesdisc.eosdis.nasa.gov/opendap/hyrax"
            opendap_urls.append(hyrax + filename)

    if not opendap_urls:
        raise RuntimeError("No usable HTTP_services links found in your txt.")

    # 去重但保持顺序
    seen = set()
    opendap_urls_unique = []
    for x in opendap_urls:
        if x not in seen:
            seen.add(x)
            opendap_urls_unique.append(x)

    return opendap_urls_unique, vars_set, bbox

# =========================
# 3) 坐标/区域选择 + 面积加权平均
# =========================
def normalize_lon_for_merra(lon):
    """
    MERRA-2 lon often in [0, 360). If user gives [-180,180], convert.
    """
    lon = float(lon)
    if lon < 0:
        lon = lon % 360.0
    return lon

def spatial_subset_and_weighted_mean(ds: xr.Dataset, varnames, lat0, lon0, window_deg):
    """
    Subset around (lat0, lon0) with a box, then area-weighted mean using cos(lat).
    """
    lon0 = normalize_lon_for_merra(lon0)

    # ds lon could be 0..360; ensure selection matches
    # Use slice; but if lon dims not monotonic, we fallback to nearest selection window
    lat_min, lat_max = lat0 - window_deg, lat0 + window_deg
    lon_min, lon_max = lon0 - window_deg, lon0 + window_deg

    # handle wrap-around near 0/360
    if lon_min < 0:
        lon_min += 360
    if lon_max >= 360:
        lon_max -= 360

    # subset lat
    ds2 = ds.sel(lat=slice(lat_min, lat_max))

    # subset lon with possible wrap
    if lon_min <= lon_max:
        ds2 = ds2.sel(lon=slice(lon_min, lon_max))
    else:
        # window crosses 0 meridian in 0..360 system: concat two slices
        a = ds2.sel(lon=slice(lon_min, 359.999))
        b = ds2.sel(lon=slice(0.0, lon_max))
        ds2 = xr.concat([a, b], dim="lon")

    # weights
    weights = np.cos(np.deg2rad(ds2["lat"]))
    # xarray weighted requires weights aligned to dims; broadcast over lon
    w = weights / weights.mean()

    out = {}
    for v in varnames:
        if v not in ds2:
            continue
        da = ds2[v]
        # average over lat/lon (if present)
        if set(["lat", "lon"]).issubset(set(da.dims)):
            out[v] = da.weighted(w).mean(dim=("lat", "lon"), skipna=True)
        else:
            out[v] = da

    return xr.Dataset(out)

# =========================
# 4) 事件识别：超过阈值的连续时段
# =========================
def detect_events(time_index: pd.DatetimeIndex, values: np.ndarray, thr: float, min_hours: int):
    """
    Return list of events:
    start_time, end_time, duration_hours, mean_value, max_value
    """
    is_high = values >= thr
    events = []
    n = len(values)
    i = 0
    while i < n:
        if not is_high[i]:
            i += 1
            continue
        j = i
        while j < n and is_high[j]:
            j += 1
        # segment [i, j)
        start = time_index[i]
        end = time_index[j - 1]
        duration = (j - i)  # in hours (assuming hourly data)
        if duration >= min_hours:
            seg = values[i:j]
            events.append({
                "start_datetime": start,
                "end_datetime": end,
                "duration_hours": int(duration),
                "mean_critical": float(np.nanmean(seg)),
                "max_critical": float(np.nanmax(seg)),
                "threshold": float(thr),
            })
        i = j
    return events

# =========================
# 5) 读 Lanzhou CSV 并按日 merge
# =========================
def sniff_delimiter(path: str):
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        sample = f.read(4096)
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", "\t", ";"])
        return dialect.delimiter
    except Exception:
        # fallback: if tabs appear a lot, use tab
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


def read_lanzhou_aq(path: str, target_year: int) -> pd.DataFrame:
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


def build_dust_daily_table(ts: pd.DataFrame, crit_var: str, threshold: float, target_year: int | None = None) -> pd.DataFrame:
    daily = (
        ts.assign(date=pd.to_datetime(ts["datetime_local"]).dt.date)
        .groupby("date", as_index=False)[crit_var]
        .agg(dust_mean="mean", dust_max="max")
    )
    daily["dust_flag"] = (daily["dust_max"] >= threshold).astype(int)
    if target_year is not None:
        daily = daily[daily["date"].map(lambda d: d.year == target_year)].copy()
    return daily


def main():
    netrc_path = ensure_netrc(EARTHDATA_USERNAME, EARTHDATA_PASSWORD)
    print(f"[OK] netrc written: {netrc_path}")

    opendap_urls, selected_vars, bbox = parse_linkslist_to_opendap(LINKLIST_PATH)

    print(f"[OK] parsed {len(opendap_urls)} Hyrax OPeNDAP URLs from linklist.")
    if selected_vars:
        print(f"[OK] variables in linklist: {selected_vars}")
    if bbox:
        print(f"[OK] bbox in linklist (south,west,north,east): {bbox}")

    # 如果 linkslist 里没解析到 VARIABLES，就至少保证 critical 在列表里
    if not selected_vars:
        selected_vars = [CRITICAL_VAR]
    else:
        if CRITICAL_VAR not in selected_vars:
            selected_vars = [CRITICAL_VAR] + selected_vars

    # 逐文件读取（每个 url 通常是一“天”的 nc4）
    rows = []
    for k, url in enumerate(opendap_urls, start=1):
        print(f"[{k}/{len(opendap_urls)}] OPeNDAP open -> {url}")

        # xarray + netCDF4 打开 OPeNDAP；chunks={} 让它懒加载
        ds = xr.open_dataset(url, engine="netcdf4", chunks={})

        # 只保留需要的变量 + 坐标
        keep_vars = [v for v in selected_vars if v in ds.variables]
        if not keep_vars:
            print("  [WARN] none of selected vars found, skip.")
            ds.close()
            continue

        ds_sub = ds[keep_vars]

        # 城市周围区域平均
        ds_mean = spatial_subset_and_weighted_mean(
            ds_sub, keep_vars, CITY_LAT, CITY_LON, WINDOW_DEG
        )

        # 转成表：每个 time 一行
        # time 可能是 (time,) 或 (time, something)；我们只处理 time 维
        if "time" not in ds_mean.dims and "time" not in ds_mean.coords:
            print("  [WARN] no time coord, skip.")
            ds.close()
            continue

        t = pd.to_datetime(ds_mean["time"].values)

        # 本地时区偏移（对齐中国按日统计）
        if LOCAL_TZ_OFFSET_HOURS != 0:
            t_local = t + pd.to_timedelta(LOCAL_TZ_OFFSET_HOURS, unit="h")
        else:
            t_local = t

        data = {"datetime_utc": t, "datetime_local": t_local}
        for v in keep_vars:
            # 确保是一维 time
            arr = ds_mean[v].values
            arr = np.asarray(arr).reshape(-1)
            data[v] = arr

        df_day = pd.DataFrame(data)
        rows.append(df_day)

        ds.close()

    if not rows:
        raise RuntimeError("No data extracted. Check auth / URLs / variables.")

    ts = pd.concat(rows, ignore_index=True).sort_values("datetime_local")
    if CRITICAL_VAR not in ts.columns:
        raise RuntimeError(f"Critical var {CRITICAL_VAR} not found in extracted columns: {ts.columns.tolist()}")

    # Event detection
    crit = ts[CRITICAL_VAR].astype(float).to_numpy()
    thr = float(np.nanpercentile(crit, THRESH_PERCENTILE))
    time_local = pd.to_datetime(ts["datetime_local"])
    events = detect_events(time_local, crit, thr, MIN_EVENT_HOURS)
    ev_df = pd.DataFrame(events)
    if ev_df.empty:
        ev_df = pd.DataFrame(columns=[
            "start_datetime", "end_datetime", "duration_hours",
            "mean_critical", "max_critical", "threshold"
        ])

    # Mark hourly spans by event list
    ts_marked = ts.copy()
    ts_marked["event_id"] = 0
    ts_marked["event_flag"] = 0
    ts_marked["threshold_used"] = thr
    ts_marked["exceed_threshold"] = (ts_marked[CRITICAL_VAR] > thr).astype(int)

    for eid, row in enumerate(events, start=1):
        s = pd.to_datetime(row["start_datetime"])
        e = pd.to_datetime(row["end_datetime"])
        m = (pd.to_datetime(ts_marked["datetime_local"]) >= s) & (pd.to_datetime(ts_marked["datetime_local"]) <= e)
        ts_marked.loc[m, "event_id"] = eid
        ts_marked.loc[m, "event_flag"] = 1

    # Fetch2-compatible summary
    local_to_utc = pd.Series(pd.to_datetime(ts_marked["datetime_utc"]).values, index=pd.to_datetime(ts_marked["datetime_local"]))
    summary_rows = []
    for eid, row in enumerate(events, start=1):
        s = pd.to_datetime(row["start_datetime"])
        e = pd.to_datetime(row["end_datetime"])
        summary_rows.append({
            "event_id": eid,
            "start_local": s,
            "end_local": e,
            "start_utc": pd.to_datetime(local_to_utc.get(s, pd.NaT)),
            "end_utc": pd.to_datetime(local_to_utc.get(e, pd.NaT)),
            "duration_hours": int(row["duration_hours"]),
            "mean_crit_span": float(row["mean_critical"]),
            "mean_crit_exceed": float(row["mean_critical"]),
            "max_crit": float(row["max_critical"]),
            "threshold": float(row["threshold"]),
            "exceed_fraction": 1.0,
        })
    events_summary = pd.DataFrame(summary_rows)
    if events_summary.empty:
        events_summary = pd.DataFrame(columns=[
            "event_id", "start_local", "end_local", "start_utc", "end_utc",
            "duration_hours", "mean_crit_span", "mean_crit_exceed", "max_crit",
            "threshold", "exceed_fraction"
        ])

    # Daily outputs
    dust_daily = build_dust_daily_table(ts_marked, CRITICAL_VAR, thr, TARGET_YEAR)
    daily_mean = build_daily_mean_table(ts, TARGET_YEAR)
    lanzhou_aq = read_lanzhou_aq(LANZHOU_CSV_PATH, TARGET_YEAR)
    if lanzhou_aq.empty:
        raise RuntimeError(f"No Lanzhou AQ rows found for {TARGET_YEAR}.")
    aligned = lanzhou_aq.merge(daily_mean, on="date", how="inner").sort_values("date")

    # Complete output set
    hourly_mark_path = os.path.join(OUT_DIR, "hourly_timeseries_with_event_mark.csv")
    events_summary_path = os.path.join(OUT_DIR, "dust_events_summary.csv")
    city_hourly_path = os.path.join(OUT_DIR, f"{CITY_NAME}_merra2_timeseries_hourly.csv")
    city_events_path = os.path.join(OUT_DIR, f"{CITY_NAME}_dust_events.csv")
    city_daily_path = os.path.join(OUT_DIR, f"{CITY_NAME}_dust_daily.csv")
    aligned_path = os.path.join(OUT_DIR, ALIGNED_DAILY_CSV)

    ts_marked.to_csv(hourly_mark_path, index=False, encoding="utf-8-sig")
    events_summary.to_csv(events_summary_path, index=False, encoding="utf-8-sig")
    ts_marked.to_csv(city_hourly_path, index=False, encoding="utf-8-sig")
    ev_df.to_csv(city_events_path, index=False, encoding="utf-8-sig")
    dust_daily.to_csv(city_daily_path, index=False, encoding="utf-8-sig")
    aligned.to_csv(aligned_path, index=False, encoding="utf-8-sig")

    print("\nDONE.")
    print("Outputs:")
    print(" - hourly mark:", hourly_mark_path)
    print(" - events sum :", events_summary_path)
    print(" - city hourly:", city_hourly_path)
    print(" - city events:", city_events_path)
    print(" - city daily :", city_daily_path)
    print(" - aligned    :", aligned_path)

if __name__ == "__main__":
    main()
