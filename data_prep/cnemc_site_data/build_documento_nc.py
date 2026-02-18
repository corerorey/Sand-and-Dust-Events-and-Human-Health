import csv
import gc
import importlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
import zipfile
import xml.etree.ElementTree as ET
from datetime import timedelta

import numpy as np
import pandas as pd

def _load_google_translator():
    try:
        module = importlib.import_module("deep_translator")
        return getattr(module, "GoogleTranslator", None)
    except Exception:
        return None


GoogleTranslator = _load_google_translator()

try:
    from netCDF4 import Dataset, date2num
except Exception:
    Dataset = None
    date2num = None


INPUT_ROOT = r"C:\DOCUMENTO"
SITE_META_XLSX = os.path.join(INPUT_ROOT, "\u7ad9\u70b9\u5217\u8868-2022.02.13\u8d77.xlsx")
# Prefer ASCII output path on Windows to avoid occasional netCDF backend path issues.
OUTPUT_DIR = os.path.join(INPUT_ROOT, "nc_out")
README_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "README.md")
MANIFEST_PATH = os.path.join(OUTPUT_DIR, "manifest.csv")
TRANSLATION_CACHE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "translation_cache.json")

SHORT_GAP_LIMIT = 2
INVALID_MISSING_RATE = 0.95
INVALID_IF_MISSING_RATE_GE = True

COMPLEVEL = 4
TIME_CHUNK_MAX = 744
SITE_CHUNK_MAX = 256

DATA_DIR_RE = re.compile(r"^(?:\u7ad9\u70b9_)?\d{8}-\d{8}$")
DAILY_FILE_RE = re.compile(r"^china_sites_(\d{8})\.csv$")
SITE_COL_RE = re.compile(r"^\d{4}A$")

BASE_COLS = ["date", "hour", "type"]
BASE_COLS_SET = set(BASE_COLS)

XLSX_HEADER_CODE = "\u76d1\u6d4b\u70b9\u7f16\u7801"
XLSX_HEADER_NAME = "\u76d1\u6d4b\u70b9\u540d\u79f0"
XLSX_HEADER_CITY = "\u57ce\u5e02"
XLSX_HEADER_LON = "\u7ecf\u5ea6"
XLSX_HEADER_LAT = "\u7eac\u5ea6"
XLSX_HEADER_REF = "\u5bf9\u7167\u70b9"


def parse_mixed_date(series):
    s = series.astype(str).str.strip()
    dt = pd.to_datetime(s, format="%Y%m%d", errors="coerce")
    miss = dt.isna()
    if miss.any():
        dt2 = pd.to_datetime(s[miss], errors="coerce")
        dt.loc[miss] = dt2
    return dt


def normalize_text(value):
    if value is None:
        return ""
    return str(value).strip()


def safe_float(value):
    text = normalize_text(value)
    if not text:
        return np.nan
    try:
        return float(text)
    except Exception:
        return np.nan


def discover_data_dirs(input_root):
    if not os.path.isdir(input_root):
        return []

    root_name = os.path.basename(os.path.normpath(input_root))
    if DATA_DIR_RE.match(root_name):
        return [input_root]

    matched = []
    for name in sorted(os.listdir(input_root)):
        path = os.path.join(input_root, name)
        if os.path.isdir(path) and DATA_DIR_RE.match(name):
            matched.append(path)
    return matched


def path_has_data_segment(path):
    parts = os.path.normpath(path).split(os.sep)
    return any(DATA_DIR_RE.match(p) for p in parts)


def scan_files(input_root):
    matched_dirs = discover_data_dirs(input_root)
    raw_entries = []
    scanned_candidate_files = 0

    for data_dir in matched_dirs:
        for dirpath, _, filenames in os.walk(data_dir):
            for filename in filenames:
                match = DAILY_FILE_RE.match(filename)
                if not match:
                    continue

                file_path = os.path.join(dirpath, filename)
                if not path_has_data_segment(file_path):
                    continue

                scanned_candidate_files += 1
                date_text = match.group(1)
                dt = pd.to_datetime(date_text, format="%Y%m%d", errors="coerce")
                if pd.isna(dt):
                    continue
                raw_entries.append((dt.date(), file_path))

    raw_entries.sort(key=lambda x: (x[0], x[1]))

    deduped = {}
    duplicate_overrides = []
    for day, path in raw_entries:
        if day in deduped:
            duplicate_overrides.append((day, deduped[day], path))
        deduped[day] = path

    daily_files = [(day, deduped[day]) for day in sorted(deduped)]
    stats = {
        "matched_dirs": len(matched_dirs),
        "scanned_candidate_files": scanned_candidate_files,
        "raw_file_count": len(raw_entries),
        "unique_dates": len(daily_files),
        "duplicate_overrides": duplicate_overrides,
    }
    return daily_files, stats


def read_csv_header(file_path):
    with open(file_path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        return next(reader)


def collect_schema_union(daily_files):
    site_ids = set()
    variable_types = set()

    for i, (_, file_path) in enumerate(daily_files, start=1):
        header = read_csv_header(file_path)
        site_ids.update(col for col in header if SITE_COL_RE.match(col))

        with open(file_path, "r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.reader(fh)
            try:
                header_row = next(reader)
            except StopIteration:
                continue
            try:
                type_idx = header_row.index("type")
            except ValueError:
                continue
            for row in reader:
                if len(row) <= type_idx:
                    continue
                val = normalize_text(row[type_idx])
                if val:
                    variable_types.add(val)

        if i % 200 == 0 or i == len(daily_files):
            print(f"Schema scan progress: {i}/{len(daily_files)} files")

    return sorted(site_ids), sorted(variable_types)


def _xlsx_col_to_index(cell_ref):
    match = re.match(r"([A-Z]+)", cell_ref or "")
    if not match:
        return 0
    col = 0
    for ch in match.group(1):
        col = col * 26 + (ord(ch) - 64)
    return col


def parse_xlsx_first_sheet_rows(xlsx_path):
    ns = {
        "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    }
    with zipfile.ZipFile(xlsx_path) as zf:
        shared_strings = []
        if "xl/sharedStrings.xml" in zf.namelist():
            shared_root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in shared_root.findall("a:si", ns):
                parts = [t.text or "" for t in si.findall(".//a:t", ns)]
                shared_strings.append("".join(parts))

        workbook_root = ET.fromstring(zf.read("xl/workbook.xml"))
        rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels_root}

        first_sheet = workbook_root.find("a:sheets/a:sheet", ns)
        if first_sheet is None:
            raise ValueError("No sheet found in xlsx file")
        rel_id = first_sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        target = rel_map[rel_id]
        if not target.startswith("xl/"):
            target = "xl/" + target

        sheet_root = ET.fromstring(zf.read(target))
        rows = []
        for row in sheet_root.findall(".//a:sheetData/a:row", ns):
            vals = []
            last_col = 0
            for cell in row.findall("a:c", ns):
                col_idx = _xlsx_col_to_index(cell.attrib.get("r", ""))
                while last_col + 1 < col_idx:
                    vals.append("")
                    last_col += 1

                cell_type = cell.attrib.get("t")
                v_elem = cell.find("a:v", ns)
                is_elem = cell.find("a:is", ns)
                value = ""
                if cell_type == "s" and v_elem is not None and v_elem.text is not None:
                    idx = int(v_elem.text)
                    value = shared_strings[idx] if 0 <= idx < len(shared_strings) else ""
                elif cell_type == "inlineStr" and is_elem is not None:
                    value = "".join((t.text or "") for t in is_elem.findall(".//a:t", ns))
                elif v_elem is not None and v_elem.text is not None:
                    value = v_elem.text
                vals.append(value)
                last_col = col_idx
            rows.append(vals)

    if not rows:
        return []
    width = max(len(r) for r in rows)
    return [r + [""] * (width - len(r)) for r in rows]


def parse_is_reference(value):
    text = normalize_text(value).upper()
    if text in {"Y", "YES", "TRUE", "T", "1"}:
        return 1
    if text in {"N", "NO", "FALSE", "F", "0"}:
        return 0
    return -1


def load_site_metadata_from_xlsx(xlsx_path):
    rows = parse_xlsx_first_sheet_rows(xlsx_path)
    if not rows:
        raise ValueError(f"No rows parsed from xlsx: {xlsx_path}")

    header = [normalize_text(h) for h in rows[0]]
    header_index = {h: i for i, h in enumerate(header)}

    code_idx = header_index.get(XLSX_HEADER_CODE, 0)
    name_idx = header_index.get(XLSX_HEADER_NAME, 1)
    city_idx = header_index.get(XLSX_HEADER_CITY, 2)
    lon_idx = header_index.get(XLSX_HEADER_LON, 3)
    lat_idx = header_index.get(XLSX_HEADER_LAT, 4)
    ref_idx = header_index.get(XLSX_HEADER_REF, 5)

    meta = {}
    for row in rows[1:]:
        code = normalize_text(row[code_idx] if code_idx < len(row) else "")
        if not SITE_COL_RE.match(code):
            continue
        site_name = normalize_text(row[name_idx] if name_idx < len(row) else "")
        city = normalize_text(row[city_idx] if city_idx < len(row) else "")
        lon = safe_float(row[lon_idx] if lon_idx < len(row) else "")
        lat = safe_float(row[lat_idx] if lat_idx < len(row) else "")
        is_ref = parse_is_reference(row[ref_idx] if ref_idx < len(row) else "")
        meta[code] = {
            "site_name_zh": site_name,
            "city_zh": city,
            "lon": lon,
            "lat": lat,
            "is_reference": is_ref,
        }
    return meta


def load_translation_cache(cache_path):
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    else:
        data = {}
    data.setdefault("site_name", {})
    data.setdefault("city", {})
    return data


def save_translation_cache(cache_path, cache):
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=2)


def get_translator():
    if GoogleTranslator is None:
        print("Warning: deep-translator is unavailable; use direct-translation fallback.")
        return None
    try:
        return GoogleTranslator(source="zh-CN", target="en")
    except Exception as err:
        print(f"Warning: failed to initialize translator ({err}); use direct-translation fallback.")
        return None


def _translate_one(translator, text):
    return normalize_text(translator.translate(text))


def _online_direct_translate(text):
    query = urllib.parse.quote(text)
    url = (
        "https://translate.googleapis.com/translate_a/single"
        f"?client=gtx&sl=zh-CN&tl=en&dt=t&q={query}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        payload = resp.read().decode("utf-8", errors="ignore")
    data = json.loads(payload)
    if not isinstance(data, list) or not data:
        return ""
    segments = data[0]
    if not isinstance(segments, list):
        return ""
    out = []
    for seg in segments:
        if isinstance(seg, list) and seg:
            out.append(normalize_text(seg[0]))
    return normalize_text("".join(out))


def _local_literal_translate(text):
    src = normalize_text(text)
    if not src:
        return ""
    if src.isascii():
        return src

    # Rule-based literal replacements for common station/city terms.
    phrase_map = {
        "\u5170\u5dde\u65b0\u533a": "Lanzhou New Area",
        "\u5170\u5dde": "Lanzhou",
        "\u65b0\u533a": "New Area",
        "\u7ba1\u59d4\u4f1a": "Management Committee",
        "\u4e2d\u5b66": "Middle School",
        "\u533b\u9662": "Hospital",
        "\u5bbe\u9986": "Hotel",
        "\u6821\u533a": "Campus",
        "\u5bf9\u7167\u70b9": "Reference Site",
        "\u751f\u7269\u5236\u54c1\u6240": "Biological Products Institute",
        "\u94c1\u8def\u8bbe\u8ba1\u9662": "Railway Design Institute",
        "\u6559\u80b2\u6e2f": "Education Port",
        "\u767e\u5408\u516c\u56ed": "Lily Park",
        "\u548c\u5e73": "Heping",
        "\u804c\u5de5": "Workers",
        "\u505c\u8fd0": "Decommissioned",
        "\u6986\u4e2d": "Yuzhong",
        "\u5170\u5927": "LZU",
        "\u821f\u66f2": "Zhouqu",
        "\u6240": "Institute",
        "\u70b9": "Site",
        "\u516c\u56ed": "Park",
    }
    result = src
    for zh in sorted(phrase_map.keys(), key=len, reverse=True):
        result = result.replace(zh, f" {phrase_map[zh]} ")

    # Strip typical Chinese punctuation/brackets and normalize spaces.
    result = (
        result.replace("\uff08", " ")
        .replace("\uff09", " ")
        .replace("(", " ")
        .replace(")", " ")
        .replace("\u3001", " ")
        .replace("\uff0c", " ")
        .replace(",", " ")
    )
    result = re.sub(r"\s+", " ", result).strip()
    return result or src


def translate_text_with_fallback(text, translator):
    src = normalize_text(text)
    if not src:
        return ""

    if translator is not None:
        for attempt in range(3):
            try:
                direct = _translate_one(translator, src)
                if direct:
                    return direct
            except Exception:
                time.sleep(1 + attempt)

    # Fallback 1: direct web translate endpoint (no deep_translator dependency).
    for attempt in range(2):
        try:
            direct = _online_direct_translate(src)
            if direct:
                return direct
        except Exception:
            time.sleep(1 + attempt)

    # Fallback 2: local rule-based literal translation.
    return _local_literal_translate(src)


def translate_values(values, cache_section, translator, field_name):
    missing = [v for v in sorted(set(values)) if normalize_text(v) and v not in cache_section]
    if not missing:
        return

    print(f"Translating {len(missing)} {field_name} values...")
    unresolved = list(missing)

    # Best-effort batch path for deep_translator. Any misses will fallback below.
    if translator is not None and hasattr(translator, "translate_batch"):
        chunk_size = 50
        unresolved = []
        for i in range(0, len(missing), chunk_size):
            chunk = missing[i:i + chunk_size]
            try:
                result = translator.translate_batch(chunk)
                if isinstance(result, list) and len(result) == len(chunk):
                    for src, dst in zip(chunk, result):
                        text = normalize_text(dst)
                        if text:
                            cache_section[src] = text
                        else:
                            unresolved.append(src)
                else:
                    unresolved.extend(chunk)
            except Exception:
                unresolved.extend(chunk)

    for i, text in enumerate(unresolved, start=1):
        cache_section[text] = translate_text_with_fallback(text, translator) or text
        if i % 50 == 0 or i == len(unresolved):
            print(f"  {field_name}: {i}/{len(unresolved)} translated")


def prepare_site_metadata(site_ids, raw_meta, translation_cache):
    site_names_zh = []
    cities_zh = []
    missing_meta_ids = []

    for site_id in site_ids:
        meta = raw_meta.get(site_id, {})
        site_name = normalize_text(meta.get("site_name_zh", ""))
        city = normalize_text(meta.get("city_zh", ""))
        if not site_name:
            site_name = site_id
        site_names_zh.append(site_name)
        cities_zh.append(city)
        if site_id not in raw_meta:
            missing_meta_ids.append(site_id)

    translator = get_translator()
    translate_values(site_names_zh, translation_cache["site_name"], translator, "site_name")
    translate_values([c for c in cities_zh if c], translation_cache["city"], translator, "city")

    site_number = []
    site_name_en = []
    city_en = []
    lon = []
    lat = []
    is_reference = []

    for site_id, name_zh, city_zh in zip(site_ids, site_names_zh, cities_zh):
        meta = raw_meta.get(site_id, {})
        site_number.append(site_id)
        site_name_en.append(translation_cache["site_name"].get(name_zh, name_zh))
        city_en.append(translation_cache["city"].get(city_zh, city_zh) if city_zh else "")
        lon.append(safe_float(meta.get("lon", np.nan)))
        lat.append(safe_float(meta.get("lat", np.nan)))
        is_reference.append(int(meta.get("is_reference", -1)))

    prepared = {
        "site_number": site_number,
        "site_name_zh": site_names_zh,
        "site_name_en": site_name_en,
        "city_zh": cities_zh,
        "city_en": city_en,
        "lon": np.asarray(lon, dtype=np.float32),
        "lat": np.asarray(lat, dtype=np.float32),
        "is_reference": np.asarray(is_reference, dtype=np.int8),
    }
    return prepared, missing_meta_ids


def build_variable_name_map(variable_types):
    used = set()
    mapping = {}
    for source_name in variable_types:
        sanitized = re.sub(r"[^0-9A-Za-z_]", "_", source_name)
        sanitized = re.sub(r"_+", "_", sanitized).strip("_")
        if not sanitized:
            sanitized = "var"
        if re.match(r"^[0-9]", sanitized):
            sanitized = "v_" + sanitized
        base = sanitized
        suffix = 2
        while sanitized in used:
            sanitized = f"{base}_{suffix}"
            suffix += 1
        used.add(sanitized)
        mapping[source_name] = sanitized
    return mapping


def group_files_by_year(daily_files):
    grouped = {}
    for day, path in daily_files:
        grouped.setdefault(day.year, []).append((day, path))
    for year in grouped:
        grouped[year].sort(key=lambda x: x[0])
    return grouped


def read_daily_dataframe(file_path, site_ids_set):
    required_cols = BASE_COLS_SET | site_ids_set
    df = pd.read_csv(file_path, usecols=lambda c: c in required_cols, encoding="utf-8-sig")
    if not BASE_COLS_SET.issubset(df.columns):
        return pd.DataFrame(columns=["datetime", "type"])

    df["date"] = parse_mixed_date(df["date"])
    df["hour"] = pd.to_numeric(df["hour"], errors="coerce")
    df = df.dropna(subset=["date", "hour"]).copy()
    if df.empty:
        return pd.DataFrame(columns=["datetime", "type"])

    df["hour"] = df["hour"].astype(int)
    df["datetime"] = df["date"] + pd.to_timedelta(df["hour"], unit="h")
    df = df.dropna(subset=["datetime"]).copy()
    if df.empty:
        return pd.DataFrame(columns=["datetime", "type"])

    df["type"] = df["type"].astype(str).str.strip()
    site_cols_present = [c for c in df.columns if SITE_COL_RE.match(c)]
    if site_cols_present:
        df[site_cols_present] = df[site_cols_present].apply(pd.to_numeric, errors="coerce")

    keep_cols = ["datetime", "type"] + site_cols_present
    return df[keep_cols]


def create_nc_file(output_path, time_index, site_meta, var_name_map):
    if Dataset is None or date2num is None:
        raise RuntimeError("netCDF4 is required. Please install netCDF4 before running this script.")

    n_time = len(time_index)
    n_site = len(site_meta["site_number"])
    time_chunk = min(TIME_CHUNK_MAX, max(1, n_time))
    site_chunk = min(SITE_CHUNK_MAX, max(1, n_site))

    try:
        ds = Dataset(output_path, "w", format="NETCDF4")
    except PermissionError as err:
        raise PermissionError(
            f"Cannot create netCDF file: {output_path}. "
            "Check whether the file is open/locked by another process, "
            "or switch OUTPUT_DIR to a writable ASCII-only path."
        ) from err
    ds.createDimension("time", n_time)
    ds.createDimension("site", n_site)

    time_var = ds.createVariable("time", "f8", ("time",))
    time_var.units = "seconds since 1970-01-01 00:00:00"
    time_var.calendar = "standard"
    time_var[:] = date2num(time_index.to_pydatetime(), units=time_var.units, calendar=time_var.calendar)

    dt_str = ds.createVariable("datetime_str", str, ("time",))
    dt_str[:] = np.asarray([dt.strftime("%Y-%m-%d %H:%M:%S") for dt in time_index], dtype=object)

    for key in ["site_number", "site_name_zh", "site_name_en", "city_zh", "city_en"]:
        var = ds.createVariable(key, str, ("site",))
        var[:] = np.asarray(site_meta[key], dtype=object)

    lon_var = ds.createVariable("lon", "f4", ("site",), fill_value=np.nan)
    lat_var = ds.createVariable("lat", "f4", ("site",), fill_value=np.nan)
    ref_var = ds.createVariable("is_reference", "i1", ("site",))
    lon_var[:] = site_meta["lon"]
    lat_var[:] = site_meta["lat"]
    ref_var[:] = site_meta["is_reference"]

    data_vars = {}
    for source_name, nc_name in var_name_map.items():
        var = ds.createVariable(
            nc_name,
            "f4",
            ("time", "site"),
            zlib=True,
            shuffle=True,
            complevel=COMPLEVEL,
            fill_value=np.nan,
            chunksizes=(time_chunk, site_chunk),
        )
        var.coordinates = "time site"
        var.source_type_name = source_name
        data_vars[source_name] = var

    ds.featureType = "timeSeries"
    ds.Conventions = "CF-1.8"
    ds.history = f"Created by build_documento_nc.py at {pd.Timestamp.now()}"
    ds.description = "Hourly air-quality records organized as time x site with annual invalid/missing handling."
    return ds, data_vars


def is_invalid_missing_rate(missing_rate):
    if INVALID_IF_MISSING_RATE_GE:
        return missing_rate >= INVALID_MISSING_RATE
    return missing_rate > INVALID_MISSING_RATE


def apply_invalid_and_interpolation(arr, time_index):
    n_time, n_site = arr.shape
    invalid_site_count = 0

    for site_idx in range(n_site):
        col = arr[:, site_idx]
        missing_count = int(np.isnan(col).sum())
        missing_rate = missing_count / n_time
        if is_invalid_missing_rate(missing_rate):
            arr[:, site_idx] = np.nan
            invalid_site_count += 1
            continue

        if missing_count == 0:
            continue

        series = pd.Series(col, index=time_index)
        series = series.interpolate(
            method="time",
            limit=SHORT_GAP_LIMIT,
            limit_direction="both",
            limit_area="inside",
        )
        arr[:, site_idx] = series.to_numpy(dtype=np.float32)

    return invalid_site_count


def build_year_nc(year, entries, site_ids, variable_types, var_name_map, site_meta, output_dir):
    start_date = entries[0][0]
    end_date = entries[-1][0]
    start_dt = pd.Timestamp(start_date)
    end_dt = pd.Timestamp(end_date) + pd.Timedelta(hours=23)
    time_index = pd.date_range(start=start_dt, end=end_dt, freq="h")

    output_name = f"documento_all_sites_{start_dt:%Y%m%d}_{end_dt:%Y%m%d}.nc"
    output_path = os.path.join(output_dir, output_name)

    print(f"[{year}] building {output_name}")
    print(f"[{year}] time steps: {len(time_index)}, files: {len(entries)}")

    site_ids_set = set(site_ids)
    n_time = len(time_index)
    n_site = len(site_ids)
    time_start = time_index[0]

    tmp_dir = os.path.join(output_dir, f"_tmp_memmap_{year}")
    os.makedirs(tmp_dir, exist_ok=True)

    memmaps = {}
    memmap_paths = {}
    for var in variable_types:
        mm_path = os.path.join(tmp_dir, f"{var_name_map[var]}.dat")
        mem = np.memmap(mm_path, dtype=np.float32, mode="w+", shape=(n_time, n_site))
        mem[:] = np.nan
        memmaps[var] = mem
        memmap_paths[var] = mm_path

    for idx, (_, file_path) in enumerate(entries, start=1):
        df = read_daily_dataframe(file_path, site_ids_set)
        if df.empty:
            continue

        for var_name, sub in df.groupby("type", sort=False):
            if var_name not in memmaps:
                continue

            sub = sub.sort_values("datetime").drop_duplicates(subset=["datetime"], keep="last")
            offsets = ((sub["datetime"] - time_start) / pd.Timedelta(hours=1)).astype("int64").to_numpy()
            valid = (offsets >= 0) & (offsets < n_time)
            if not np.any(valid):
                continue

            if not np.all(valid):
                sub = sub.iloc[np.flatnonzero(valid)]
                offsets = offsets[valid]

            vals = sub.reindex(columns=site_ids).to_numpy(dtype=np.float32, copy=False)
            memmaps[var_name][offsets, :] = vals

        if idx % 50 == 0 or idx == len(entries):
            print(f"[{year}] filled raw data from {idx}/{len(entries)} files")

    ds, nc_vars = create_nc_file(output_path, time_index, site_meta, var_name_map)

    invalid_summary = {}
    for i, var_name in enumerate(variable_types, start=1):
        memmaps[var_name].flush()
        arr = np.array(memmaps[var_name], dtype=np.float32)
        invalid_count = apply_invalid_and_interpolation(arr, time_index)
        nc_var = nc_vars[var_name]
        nc_var[:, :] = arr
        nc_var.invalid_site_count = invalid_count
        nc_var.missing_rate_threshold = INVALID_MISSING_RATE
        nc_var.missing_rate_operator = ">=" if INVALID_IF_MISSING_RATE_GE else ">"
        invalid_summary[var_name] = invalid_count
        del arr
        gc.collect()
        print(f"[{year}] wrote variable {i}/{len(variable_types)}: {var_name}")

    ds.year = year
    ds.start_datetime = f"{start_dt:%Y-%m-%d %H:%M:%S}"
    ds.end_datetime = f"{end_dt:%Y-%m-%d %H:%M:%S}"
    ds.source_file_count = len(entries)
    ds.site_count = len(site_ids)
    ds.variable_count = len(variable_types)
    ds.close()

    for var_name, mem in memmaps.items():
        try:
            mem.flush()
            mem._mmap.close()
        except Exception:
            pass
        del mem
        try:
            os.remove(memmap_paths[var_name])
        except OSError:
            pass
    try:
        os.rmdir(tmp_dir)
    except OSError:
        pass

    summary = {
        "year": year,
        "start_date": f"{start_dt:%Y-%m-%d}",
        "end_date": f"{end_dt:%Y-%m-%d}",
        "input_days": len(entries),
        "time_points": len(time_index),
        "site_count": len(site_ids),
        "variable_count": len(variable_types),
        "output_file": output_name,
        "invalid_site_counts_json": json.dumps(invalid_summary, ensure_ascii=False),
    }
    return summary


def write_manifest(manifest_path, year_summaries):
    fields = [
        "year",
        "start_date",
        "end_date",
        "input_days",
        "time_points",
        "site_count",
        "variable_count",
        "output_file",
        "invalid_site_counts_json",
    ]
    with open(manifest_path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in year_summaries:
            writer.writerow(row)


def write_readme(
    readme_path,
    scan_stats,
    site_count,
    variable_types,
    var_name_map,
    missing_meta_ids,
    year_summaries,
):
    lines = []
    lines.append("# NetCDF Dataset Notes")
    lines.append("")
    lines.append("This document is generated by `build_documento_nc.py`.")
    lines.append("")
    lines.append("## Source")
    lines.append("")
    lines.append("- Input root: `C:\\DOCUMENTO`")
    lines.append("- Site metadata: `C:\\DOCUMENTO\\站点列表-2022.02.13起.xlsx`")
    lines.append("- Daily data pattern: `china_sites_YYYYMMDD.csv`")
    lines.append("")
    lines.append("## Scan Summary")
    lines.append("")
    lines.append(f"- Matched data directories: {scan_stats['matched_dirs']}")
    lines.append(f"- Candidate files scanned: {scan_stats['scanned_candidate_files']}")
    lines.append(f"- Raw files found: {scan_stats['raw_file_count']}")
    lines.append(f"- Unique dates after dedupe: {scan_stats['unique_dates']}")
    lines.append(f"- Duplicate overrides: {len(scan_stats['duplicate_overrides'])}")
    lines.append("")
    lines.append("## NetCDF Layout")
    lines.append("")
    lines.append("- Dimensions: `time`, `site`")
    lines.append("- Pollutants: each type is a variable with shape `(time, site)`")
    lines.append("- Site metadata variables:")
    lines.append("  - `site_number`, `site_name_zh`, `site_name_en`, `city_zh`, `city_en`, `lon`, `lat`, `is_reference`")
    lines.append("- Time variables:")
    lines.append("  - `time` (CF-compliant seconds since epoch)")
    lines.append("  - `datetime_str` (`YYYY-MM-DD HH:MM:SS`)")
    lines.append("")
    lines.append("## Data Handling Rules")
    lines.append("")
    lines.append(f"- Short-gap interpolation limit: {SHORT_GAP_LIMIT} hours")
    lines.append(f"- Invalid missing threshold: {INVALID_MISSING_RATE:.0%}")
    lines.append(f"- Invalid operator: {'>=' if INVALID_IF_MISSING_RATE_GE else '>'}")
    lines.append("- Missing-rate denominator is yearly coverage hours in each annual file.")
    lines.append("- Interpolation runs per year, per site, per variable (no cross-year fill).")
    lines.append("")
    lines.append("## Variables")
    lines.append("")
    lines.append(f"- Site count in output: {site_count}")
    lines.append(f"- Pollutant variable count: {len(variable_types)}")
    lines.append("")
    lines.append("| Source type | NC variable |")
    lines.append("|---|---|")
    for source_name in variable_types:
        lines.append(f"| `{source_name}` | `{var_name_map[source_name]}` |")
    lines.append("")
    lines.append("## Metadata Coverage")
    lines.append("")
    lines.append(f"- Sites missing metadata in xlsx: {len(missing_meta_ids)}")
    if missing_meta_ids:
        preview = ", ".join(missing_meta_ids[:30])
        lines.append(f"- Missing metadata site ids (preview): {preview}")
    lines.append("")
    lines.append("## Output Files")
    lines.append("")
    for summary in year_summaries:
        lines.append(
            f"- `{summary['output_file']}`: {summary['start_date']} to {summary['end_date']}, "
            f"time_points={summary['time_points']}, sites={summary['site_count']}, vars={summary['variable_count']}"
        )
    lines.append("")
    lines.append("## Read Example (Python)")
    lines.append("")
    lines.append("```python")
    lines.append("import xarray as xr")
    lines.append("ds = xr.open_dataset('nc_out/documento_all_sites_20150101_20151231.nc')")
    lines.append("print(ds)")
    lines.append("```")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- English fields are translated online and cached in `translation_cache.json`.")
    lines.append("- If translation fails, source Chinese text is used as fallback.")

    with open(readme_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main():
    if Dataset is None:
        raise RuntimeError("Missing dependency: netCDF4. Install with `pip install netCDF4`.")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    daily_files, scan_stats = scan_files(INPUT_ROOT)
    print("Scan summary:", {k: v for k, v in scan_stats.items() if k != "duplicate_overrides"})
    if scan_stats["duplicate_overrides"]:
        print(
            f"Duplicate day files detected: {len(scan_stats['duplicate_overrides'])}, "
            "keeping lexicographically last path."
        )

    if not daily_files:
        raise RuntimeError(f"No daily files found under {INPUT_ROOT}")

    site_ids, variable_types = collect_schema_union(daily_files)
    print(f"Schema union: sites={len(site_ids)}, variables={len(variable_types)}")

    raw_meta = load_site_metadata_from_xlsx(SITE_META_XLSX)
    translation_cache = load_translation_cache(TRANSLATION_CACHE_PATH)
    site_meta, missing_meta_ids = prepare_site_metadata(site_ids, raw_meta, translation_cache)
    save_translation_cache(TRANSLATION_CACHE_PATH, translation_cache)
    print(f"Metadata loaded: xlsx_sites={len(raw_meta)}, missing_meta_sites={len(missing_meta_ids)}")

    var_name_map = build_variable_name_map(variable_types)
    files_by_year = group_files_by_year(daily_files)
    year_summaries = []

    for year in sorted(files_by_year):
        summary = build_year_nc(
            year=year,
            entries=files_by_year[year],
            site_ids=site_ids,
            variable_types=variable_types,
            var_name_map=var_name_map,
            site_meta=site_meta,
            output_dir=OUTPUT_DIR,
        )
        year_summaries.append(summary)
        write_manifest(MANIFEST_PATH, year_summaries)

    write_readme(
        readme_path=README_PATH,
        scan_stats=scan_stats,
        site_count=len(site_ids),
        variable_types=variable_types,
        var_name_map=var_name_map,
        missing_meta_ids=missing_meta_ids,
        year_summaries=year_summaries,
    )

    print("Done.")
    print(f"NC files directory: {OUTPUT_DIR}")
    print(f"Manifest: {MANIFEST_PATH}")
    print(f"README: {README_PATH}")


if __name__ == "__main__":
    main()
