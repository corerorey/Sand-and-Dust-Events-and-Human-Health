from __future__ import annotations

import re, json, time, zipfile
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
from urllib.parse import parse_qs, unquote, urlparse

import certifi
import urllib3
import requests
import pandas as pd
import numpy as np
import xarray as xr

# =========================
# 0) 你需要改的参数（EDIT ME）
# =========================
EARTHDATA_USERNAME = "correr27890"
EARTHDATA_PASSWORD = "AQN/RZ2Y&S5Rb+j"


# 你的 webcrawler CSV 文件夹（你给的路径）
# WEBCRAWLER_DIR = r"C:\DOCUMENTO\Sand-and-Dust-Storms-and-Human-Health\data_prep\webcrawler\lanzhou_201101_202602.csv"
WEBCRAWLER_DIR = r"C:\DOCUMENTO\Sand-and-Dust-Storms-and-Human-Health\data_prep\webcrawler"


# 兰州坐标（可改）
SITE_LAT = 36.0611
SITE_LON = 103.8343  # East positive

# 选择提取方式：建议 area_mean（区域平均更稳）
EXTRACT_MODE = "area_mean"   # "nearest" / "area_mean" / "area_max"

# 子集 BOX（W,S,E,N），建议覆盖兰州附近
BOX = (103.0, 35.5, 104.5, 36.8)

# 数据集：小时平均 aerosol
DATASET_ID = "M2T1NXAER_5.12.4"
VARS = ["DUEXTTAU", "DUCMASS", "DUSMASS"]

# 中国本地时间：UTC+8
LOCAL_TZ_OFFSET_HOURS = 8

# Optional hard overrides for download range (YYYY-MM-DD). Set to None to auto-detect from CSVs.
FORCE_START_DATE = "2015-01-01"
FORCE_END_DATE = "2025-12-31"

# If a monthly request fails, retry by splitting into single-day requests.
FALLBACK_TO_DAILY_ON_FAILURE = True
REQUEST_RETRIES = 2

# Alternative mode: use a pre-generated OTF URL list file (HTTP_services.cgi links).
USE_OTF_URL_LIST = True
OTF_URL_LIST_FILE = Path(r"C:\Users\Cory Kong\Downloads\subset_M2T1NXAER_5.12.4_20260216_074723_.txt")
DELETE_TEMP_AFTER_EXTRACT = True

OUTDIR = Path("./out_merra2_lanzhou")
ORIG_DIR = OUTDIR / "01_original_subsets"
OUTDIR.mkdir(parents=True, exist_ok=True)
ORIG_DIR.mkdir(parents=True, exist_ok=True)

# —— 事件识别（小时级）——
EVENT_METRIC = "DUEXTTAU"   # 用哪个变量定义事件
EVENT_Q = 0.95             # 阈值=该变量在整个时间段的分位数
MIN_EVENT_HOURS = 6        # 至少连续多少小时才算事件
MERGE_GAP_HOURS = 2        # 两段事件间隔 <=2小时 就合并（可改 0 表示不合并）

EVENTS_HOURLY_CSV = OUTDIR / "02_dust_events_hourly.csv"
TS_HOURLY_CSV = OUTDIR / "03_merra2_hourly_timeseries.csv"

# —— 对齐（按“日”）——
MERGED_DIR = OUTDIR / "04_merged_csvs"
MERGED_DIR.mkdir(parents=True, exist_ok=True)
# =========================


# =========================
# 1) GES DISC Subsetting API
# =========================
JSONWSP_URL = "https://disc.gsfc.nasa.gov/service/subset/jsonwsp"

http = urllib3.PoolManager(
    cert_reqs="CERT_REQUIRED",
    ca_certs=certifi.where(),
    timeout=urllib3.Timeout(connect=30.0, read=120.0),
)

def post_jsonwsp(payload: Dict[str, Any]) -> Dict[str, Any]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    r = http.request("POST", JSONWSP_URL, body=json.dumps(payload), headers=headers)
    data = json.loads(r.data.decode("utf-8"))
    if data.get("type") == "jsonwsp/fault":
        raise RuntimeError(f"API fault: {data.get('fault', data)}")
    return data

def month_chunks(start: pd.Timestamp, end: pd.Timestamp) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
    chunks = []
    cur = pd.Timestamp(year=start.year, month=start.month, day=1)
    while cur <= end:
        nxt = (cur + pd.offsets.MonthBegin(1)).normalize()
        s = max(start, cur)
        e = min(end, nxt - pd.Timedelta(days=1))
        chunks.append((s, e))
        cur = nxt
    return chunks


def day_chunks(start: pd.Timestamp, end: pd.Timestamp) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
    chunks = []
    cur = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    while cur <= end:
        chunks.append((cur, cur))
        cur = cur + pd.Timedelta(days=1)
    return chunks


def _parse_yyyy_mm_dd(s: Optional[str]) -> Optional[pd.Timestamp]:
    if not s:
        return None
    return pd.Timestamp(str(s)).normalize()


def resolve_date_range(min_date: pd.Timestamp, max_date: pd.Timestamp) -> Tuple[pd.Timestamp, pd.Timestamp]:
    start = _parse_yyyy_mm_dd(FORCE_START_DATE) or pd.Timestamp(min_date).normalize()
    end = _parse_yyyy_mm_dd(FORCE_END_DATE) or pd.Timestamp(max_date).normalize()
    if start > end:
        raise RuntimeError(f"Invalid date range: start {start.date()} > end {end.date()}")
    return start, end


def submit_subset_job(dataset_id: str, vars_: List[str], box: Tuple[float, float, float, float],
                     start_date: str, end_date: str) -> str:
    minlon, minlat, maxlon, maxlat = box
    payload = {
        "methodname": "subset",
        "type": "jsonwsp/request",
        "version": "1.0",
        "args": {
            "role": "subset",
            "start": start_date,
            "end": end_date,
            "box": [minlon, minlat, maxlon, maxlat],
            "crop": True,
            "data": [{"datasetId": dataset_id, "variable": v} for v in vars_],
        },
    }
    resp = post_jsonwsp(payload)
    return resp["result"]["jobId"]

def wait_for_job(job_id: str, poll_seconds: int = 5) -> None:
    payload = {"methodname": "GetStatus", "version": "1.0", "type": "jsonwsp/request", "args": {"jobId": job_id}}
    while True:
        resp = post_jsonwsp(payload)
        status = resp["result"]["Status"]
        pct = resp["result"].get("PercentCompleted", 0)
        if status in ("Accepted", "Running"):
            print(f"  Job {job_id}: {status} ({pct}%)")
            time.sleep(poll_seconds)
            continue
        if status == "Succeeded":
            print(f"  Job {job_id}: Succeeded")
            return
        raise RuntimeError(f"Job {job_id} failed: {resp}")

def get_job_results(job_id: str, batchsize: int = 200) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    start_index = 0
    while True:
        payload = {
            "methodname": "GetResult",
            "version": "1.0",
            "type": "jsonwsp/request",
            "args": {"jobId": job_id, "count": batchsize, "startIndex": start_index},
        }
        resp = post_jsonwsp(payload)
        res = resp["result"]
        page_items = res["items"]
        items.extend(page_items)
        start_index += len(page_items)
        if start_index >= res["totalResults"]:
            break
    out = [it for it in items if isinstance(it, dict) and "link" in it]
    return out

def download_items(items: List[Dict[str, Any]], outdir: Path, session: requests.Session) -> List[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    paths: List[Path] = []
    for it in items:
        url = it["link"]
        label = it.get("label") or f"subset_{it.get('start','')}_{it.get('end','')}.zip"
        safe = "".join(c if c.isalnum() or c in ("-", "_", ".", "+") else "_" for c in label)
        fp = outdir / safe
        if fp.exists() and fp.stat().st_size > 0:
            paths.append(fp)
            continue
        print(f"    downloading -> {fp.name}")
        r = session.get(url, stream=True, allow_redirects=True, timeout=240)
        r.raise_for_status()
        with open(fp, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
        paths.append(fp)
        time.sleep(0.2)
    return paths

def unzip_to_nc(files: List[Path], outdir: Path) -> List[Path]:
    outdir.mkdir(parents=True, exist_ok=True)
    nc_paths: List[Path] = []
    for fp in files:
        if fp.suffix.lower() == ".zip":
            with zipfile.ZipFile(fp, "r") as z:
                for name in z.namelist():
                    if name.lower().endswith((".nc", ".nc4", ".netcdf")):
                        out_path = outdir / Path(name).name
                        if not out_path.exists():
                            z.extract(name, outdir)
                            extracted = outdir / name
                            if extracted.exists() and extracted != out_path:
                                extracted.replace(out_path)
                                try:
                                    extracted.parent.rmdir()
                                except Exception:
                                    pass
                        nc_paths.append(out_path)
        else:
            if fp.suffix.lower() in (".nc", ".nc4"):
                nc_paths.append(fp)
    return sorted(list({p.resolve() for p in nc_paths}))


def fetch_subset_range(session: requests.Session, start_dt: pd.Timestamp, end_dt: pd.Timestamp) -> List[Path]:
    s_str = pd.Timestamp(start_dt).strftime("%Y-%m-%d")
    e_str = pd.Timestamp(end_dt).strftime("%Y-%m-%d")
    last_err = None
    for attempt in range(1, REQUEST_RETRIES + 1):
        try:
            job_id = submit_subset_job(DATASET_ID, VARS, BOX, s_str, e_str)
            print(f"  Job ID: {job_id}")
            wait_for_job(job_id, poll_seconds=5)
            items = get_job_results(job_id)
            if not items:
                print(f"  WARNING: {s_str} to {e_str} returned 0 files")
                return []
            range_dir = ORIG_DIR / f"{s_str}_to_{e_str}"
            return download_items(items, range_dir, session)
        except Exception as ex:
            last_err = ex
            if attempt < REQUEST_RETRIES:
                print(f"  WARNING: {s_str} to {e_str} attempt {attempt}/{REQUEST_RETRIES} failed, retrying...")
                time.sleep(2 * attempt)
            else:
                raise last_err
    return []


def _extract_date_from_otf_url(url: str) -> Optional[pd.Timestamp]:
    try:
        query = parse_qs(urlparse(url).query)
        filename = unquote(query.get("FILENAME", [""])[0])
        m = re.search(r"\.(\d{8})\.nc4$", filename)
        if not m:
            return None
        return pd.to_datetime(m.group(1), format="%Y%m%d").normalize()
    except Exception:
        return None


def load_otf_urls(url_list_file: Path, start_dt: pd.Timestamp, end_dt: pd.Timestamp) -> List[Tuple[pd.Timestamp, str]]:
    if not url_list_file.exists():
        raise RuntimeError(f"OTF URL list file does not exist: {url_list_file}")

    start_dt = pd.Timestamp(start_dt).normalize()
    end_dt = pd.Timestamp(end_dt).normalize()
    out: List[Tuple[pd.Timestamp, str]] = []
    seen: set[str] = set()

    with open(url_list_file, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line.startswith("http"):
                continue
            if "HTTP_services.cgi" not in line:
                continue
            dt = _extract_date_from_otf_url(line)
            if dt is None or dt < start_dt or dt > end_dt:
                continue
            if line in seen:
                continue
            seen.add(line)
            out.append((dt, line))

    out.sort(key=lambda x: x[0])
    return out


def download_otf_nc(url: str, out_file: Path, session: requests.Session, retries: int = 3) -> None:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, stream=True, allow_redirects=True, timeout=300)
            if r.status_code == 401:
                raise RuntimeError("401 Unauthorized from Earthdata. Check account authorization/cookies.")
            r.raise_for_status()
            with open(out_file, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)
            return
        except Exception as ex:
            last_err = ex
            if attempt < retries:
                time.sleep(2 * attempt)
            else:
                raise last_err


def build_hourly_from_otf_urls(
    session: requests.Session, url_list_file: Path, start_dt: pd.Timestamp, end_dt: pd.Timestamp
) -> pd.DataFrame:
    pairs = load_otf_urls(url_list_file, start_dt, end_dt)
    if not pairs:
        raise RuntimeError(f"No matching OTF URLs found in {url_list_file} for {start_dt.date()} to {end_dt.date()}.")

    tmp_dir = OUTDIR / "_tmp_otf_nc"
    rows: List[pd.DataFrame] = []
    total = len(pairs)
    failed = 0

    for i, (dt, url) in enumerate(pairs, start=1):
        ymd = dt.strftime("%Y%m%d")
        nc_fp = tmp_dir / f"{ymd}.SUB.nc"
        print(f"  OTF {i}/{total}: {ymd}")
        try:
            download_otf_nc(url, nc_fp, session, retries=REQUEST_RETRIES + 1)
            one = extract_hourly_timeseries([nc_fp])
            rows.append(one)
        except Exception as ex:
            failed += 1
            print(f"    WARNING: OTF {ymd} failed: {ex}")
        finally:
            if DELETE_TEMP_AFTER_EXTRACT:
                try:
                    if nc_fp.exists():
                        nc_fp.unlink()
                except Exception:
                    pass

    if failed:
        print(f"OTF failures: {failed}/{total}")
    if not rows:
        raise RuntimeError("No hourly records extracted from OTF URL list.")

    out = pd.concat(rows, axis=0).sort_values("datetime_local").reset_index(drop=True)
    out = out.drop_duplicates(subset=["datetime_local"], keep="first")
    return out


# =========================
# 2) 浠?netCDF 鎻愬彇灏忔椂搴忓垪锛堟湰鍦版椂闂达級
# =========================
def get_latlon_names(ds: xr.Dataset) -> Tuple[str, str]:
    lat_name = "lat" if "lat" in ds.coords else ("latitude" if "latitude" in ds.coords else None)
    lon_name = "lon" if "lon" in ds.coords else ("longitude" if "longitude" in ds.coords else None)
    if lat_name is None or lon_name is None:
        raise RuntimeError("找不到 lat/lon 坐标名。")
    return lat_name, lon_name

def normalize_lon_for_ds(lon: float, lon_coord: xr.DataArray) -> float:
    lonmin = float(lon_coord.min())
    lonmax = float(lon_coord.max())
    if lonmin >= 0 and lon < 0:
        return lon % 360
    if lonmax <= 180 and lon > 180:
        return ((lon + 180) % 360) - 180
    return lon

def extract_hourly_timeseries(nc_files: List[Path]) -> pd.DataFrame:
    rows = []
    for fp in sorted(nc_files):
        try:
            ds = xr.open_dataset(fp, engine="netcdf4")
        except Exception as e:
            print(f"WARNING: 打不开 {fp.name}: {e}")
            continue

        lat_name, lon_name = get_latlon_names(ds)
        if "time" not in ds.coords:
            ds.close()
            continue

        # 转为本地时间（UTC+8）
        t_utc = pd.to_datetime(ds["time"].values)
        t_local = t_utc + pd.Timedelta(hours=LOCAL_TZ_OFFSET_HOURS)

        if EXTRACT_MODE == "nearest":
            target_lon = normalize_lon_for_ds(SITE_LON, ds[lon_name])
            sub = ds.sel({lat_name: SITE_LAT, lon_name: target_lon}, method="nearest")
            rec = {"datetime_local": t_local, "datetime_utc": t_utc}
            for v in VARS:
                if v in sub:
                    rec[v] = sub[v].values
            df = pd.DataFrame(rec)
            rows.append(df)

        else:
            # 区域统计：area_mean or area_max
            weights = np.cos(np.deg2rad(ds[lat_name]))
            weights.name = "weights"

            rec = {"datetime_local": t_local, "datetime_utc": t_utc}
            for v in VARS:
                if v not in ds:
                    continue
                field = ds[v]
                if EXTRACT_MODE == "area_mean":
                    ts = field.weighted(weights).mean(dim=(lat_name, lon_name), skipna=True)
                    rec[v] = ts.values
                elif EXTRACT_MODE == "area_max":
                    ts = field.max(dim=(lat_name, lon_name), skipna=True)
                    rec[v] = ts.values
                else:
                    ds.close()
                    raise RuntimeError(f"未知 EXTRACT_MODE={EXTRACT_MODE}")

            df = pd.DataFrame(rec)
            rows.append(df)

        ds.close()

    if not rows:
        raise RuntimeError("没有提取到小时序列（检查下载/变量/BOX）。")

    out = pd.concat(rows, axis=0).sort_values("datetime_local").reset_index(drop=True)
    # 去重
    out = out.drop_duplicates(subset=["datetime_local"], keep="first")
    return out


# =========================
# 3) 小时级事件识别（输出 start/end datetime）
# =========================
def detect_events_hourly(ts: pd.Series, q: float, min_hours: int, merge_gap_hours: int) -> pd.DataFrame:
    s = ts.dropna().sort_index()
    if s.empty:
        return pd.DataFrame(columns=["start_local", "end_local", "duration_hours", "threshold", "max_value"])

    thr = float(s.quantile(q))
    flag = s > thr

    # 找连续段
    starts, ends = [], []
    prev = False
    st = None
    for t, f in flag.items():
        if f and not prev:
            st = t
        if (not f) and prev:
            starts.append(st)
            ends.append(t)  # end is exclusive
        prev = f
    if prev and st is not None:
        starts.append(st)
        ends.append(s.index[-1] + pd.Timedelta(hours=1))

    # 先按 min_hours 过滤
    segs = []
    for a, b in zip(starts, ends):
        dur = (b - a) / pd.Timedelta(hours=1)
        if dur >= min_hours:
            segs.append((a, b))

    if not segs:
        return pd.DataFrame(columns=["start_local", "end_local", "duration_hours", "threshold", "max_value"])

    # 合并间隔很小的事件段
    merged = [segs[0]]
    for a, b in segs[1:]:
        last_a, last_b = merged[-1]
        gap = (a - last_b) / pd.Timedelta(hours=1)
        if gap <= merge_gap_hours:
            merged[-1] = (last_a, b)
        else:
            merged.append((a, b))

    out_rows = []
    for a, b in merged:
        seg = s.loc[(s.index >= a) & (s.index < b)]
        out_rows.append({
            "start_local": a,
            "end_local": b,
            "duration_hours": float((b - a) / pd.Timedelta(hours=1)),
            "threshold": thr,
            "max_value": float(seg.max()),
        })
    return pd.DataFrame(out_rows)


# =========================
# 4) webcrawler CSV 读取 & 按“日”对齐（可选）
# =========================
def parse_cn_date(s: str) -> Optional[pd.Timestamp]:
    m = re.search(r"(\d{4})年(\d{2})月(\d{2})日", str(s))
    if not m:
        return None
    y, mo, d = map(int, m.groups())
    return pd.Timestamp(year=y, month=mo, day=d)

def read_any_csv(path: Path) -> pd.DataFrame:
    # 兼容 tab/逗号；gbk/utf8
    for enc in ["utf-8-sig", "utf-8", "gbk", "gb18030"]:
        for sep in ["\t", ",", None]:
            try:
                df = pd.read_csv(path, header=None, sep=sep, engine="python", encoding=enc)
                return df
            except Exception:
                pass
    raise RuntimeError(f"读不了文件：{path}")

def add_date_col(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["_date"] = out.iloc[:, 0].apply(parse_cn_date)
    out = out.dropna(subset=["_date"])
    out["_date"] = pd.to_datetime(out["_date"]).dt.normalize()
    return out

def make_daily_from_hourly(df_hourly: pd.DataFrame) -> pd.DataFrame:
    # 用本地时间切“本地日”
    df = df_hourly.copy()
    df["date"] = pd.to_datetime(df["datetime_local"]).dt.normalize()
    agg = {}
    for v in VARS:
        if v in df.columns:
            agg[v] = "mean"
    daily = df.groupby("date", as_index=False).agg(agg)
    # 重命名更清晰
    daily = daily.rename(columns={v: f"{v}_daily_mean" for v in agg.keys()})
    return daily


# =========================
# MAIN
# =========================
def main():
    if "PUT_YOURS_HERE" in (EARTHDATA_USERNAME, EARTHDATA_PASSWORD):
        raise RuntimeError("请先在脚本里填写 EARTHDATA_USERNAME / EARTHDATA_PASSWORD。")

    # 0) 扫描 webcrawler 目录里所有 csv，确定总体时间范围（避免你手写开始结束）
    csv_dir = Path(WEBCRAWLER_DIR)
    csv_files = sorted(list(csv_dir.glob("*.csv")))
    if not csv_files:
        raise RuntimeError(f"在目录里没找到 csv：{WEBCRAWLER_DIR}")

    # 用所有 CSV 的日期范围取并集（最早->最晚）
    min_date, max_date = None, None
    for fp in csv_files[:50]:  # 前50个先抽样读一下，够用（你要全读也行）
        df = read_any_csv(fp)
        df = add_date_col(df)
        if df.empty:
            continue
        a, b = df["_date"].min(), df["_date"].max()
        min_date = a if min_date is None else min(min_date, a)
        max_date = b if max_date is None else max(max_date, b)

    if min_date is None or max_date is None:
        raise RuntimeError("这些 csv 里没解析出日期（第一列必须包含 'YYYY年MM月DD日'）。")

    print(f"从 webcrawler 目录推断日期范围：{min_date.date()} -> {max_date.date()} (本地日)")
    effective_start, effective_end = resolve_date_range(min_date, max_date)
    if FORCE_START_DATE or FORCE_END_DATE:
        print(f"Using overridden range: {effective_start.date()} -> {effective_end.date()}")

    # 1) 涓嬭浇鍘熷 subset锛堟寜鏈堬級
    sess = requests.Session()
    sess.auth = (EARTHDATA_USERNAME, EARTHDATA_PASSWORD)
    sess.headers.update({"User-Agent": "merra2-hourly-events/1.0"})

    if USE_OTF_URL_LIST:
        print(f"\nUsing OTF URL list mode: {OTF_URL_LIST_FILE}")
        df_hourly = build_hourly_from_otf_urls(
            session=sess,
            url_list_file=OTF_URL_LIST_FILE,
            start_dt=effective_start,
            end_dt=effective_end,
        )
    else:
        downloaded = []
        failed_ranges = []
        for s, e in month_chunks(effective_start, effective_end):
            s_str, e_str = s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d")
            print(f"\n== Request subset: {s_str} to {e_str} ==")
            try:
                files = fetch_subset_range(sess, s, e)
                downloaded.extend(files)
            except Exception as ex:
                print(f"  WARNING: {s_str} to {e_str} failed: {ex}")
                failed_ranges.append((s_str, e_str, str(ex)))
                if not FALLBACK_TO_DAILY_ON_FAILURE:
                    continue

                print(f"  Fallback: retrying day-by-day for {s_str} to {e_str}")
                for ds, de in day_chunks(s, e):
                    ds_str = ds.strftime("%Y-%m-%d")
                    print(f"    day request: {ds_str}")
                    try:
                        files = fetch_subset_range(sess, ds, de)
                        downloaded.extend(files)
                    except Exception as day_ex:
                        print(f"    WARNING: {ds_str} failed: {day_ex}")
                        failed_ranges.append((ds_str, ds_str, str(day_ex)))
                        continue

        if failed_ranges:
            print(f"Failed ranges count: {len(failed_ranges)}")

        if not downloaded:
            raise RuntimeError("No subset files downloaded. Check credentials, BOX, and dataset settings."
)

        # 2) unzip and parse netCDF
        nc_dir = ORIG_DIR / "_nc_extracted"
        nc_files = unzip_to_nc(downloaded, nc_dir)
        if not nc_files:
            raise RuntimeError("No netCDF files found after subset download/extraction."
)

        # 3) extract hourly series
        print("\nExtracting hourly series...")
        df_hourly = extract_hourly_timeseries(nc_files)
    df_hourly.to_csv(TS_HOURLY_CSV, index=False, encoding="utf-8-sig")
    print(f"已保存小时序列：{TS_HOURLY_CSV}")

    # 4) 小时级事件识别（输出 start/end datetime，含分钟）
    if EVENT_METRIC not in df_hourly.columns:
        raise RuntimeError(f"EVENT_METRIC={EVENT_METRIC} 不在数据列里（现有列：{list(df_hourly.columns)}）")

    s = pd.Series(df_hourly[EVENT_METRIC].values, index=pd.to_datetime(df_hourly["datetime_local"]))
    events = detect_events_hourly(s, q=EVENT_Q, min_hours=MIN_EVENT_HOURS, merge_gap_hours=MERGE_GAP_HOURS)

    # 注意：分钟精度来自原始时间戳（通常是 :30）
    events.to_csv(EVENTS_HOURLY_CSV, index=False, encoding="utf-8-sig")
    print(f"已保存小时级尘事件：{EVENTS_HOURLY_CSV}")
    print(events.head(10))

    # 5) （可选）按日聚合，用于和 webcrawler CSV 对齐
    daily = make_daily_from_hourly(df_hourly)

    print("\n开始把每日 MERRA-2 指标对齐到每个 webcrawler CSV（按日期）...")
    for fp in csv_files:
        try:
            df = read_any_csv(fp)
            df = add_date_col(df)
            merged = df.merge(daily, left_on="_date", right_on="date", how="left")

            # 保留原始列风格：去掉辅助列
            merged = merged.drop(columns=["_date", "date"], errors="ignore")

            out_fp = MERGED_DIR / fp.name
            merged.to_csv(out_fp, index=False, header=False, encoding="utf-8-sig")
        except Exception as ex:
            print(f"  WARNING: 合并失败 {fp.name}: {ex}")
            continue

    print(f"\n对齐完成，输出目录：{MERGED_DIR}")
    print("DONE.")


if __name__ == "__main__":
    main()
