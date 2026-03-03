#%%
import os
import re
import pandas as pd

DEFAULT_INPUT_ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SCAN_ROOT = r"C:\DOCUMENTO"
DEFAULT_OUTPUT_START = "2025-01-01 00:00:00"
DEFAULT_OUTPUT_END = "2026-02-28 23:00:00"

# IDE-friendly runtime config:
# 1) click Run
# 2) type custom range in the Run console (or press Enter to use defaults)
INPUT_ROOT = DEFAULT_SCAN_ROOT
OUTPUT_DIR = DEFAULT_INPUT_ROOT
PROMPT_FOR_RANGE = True
OUTPUT_START = DEFAULT_OUTPUT_START
OUTPUT_END = DEFAULT_OUTPUT_END

SITE_ALIAS = {
    "1475A": "workers_hospital_decommissioned_20181115",
    "1476A": "lanlian_hotel",
    "1477A": "yuzhong_lzu_campus_control",
    "1478A": "biological_products_institute",
    "1479A": "railway_design_institute",
    "3186A": "education_port",
    "3241A": "lily_park",
    "3242A": "heping",
    "3245A": "new_district_management_committee",
    "3246A": "zhouqu_middle_school",
}

# Scientific-conservative interpolation rule:
# only fill short internal gaps (<=2 consecutive hours), keep long gaps as NaN.
SHORT_GAP_LIMIT = 2
INVALID_MISSING_RATE = 0.95
INVALID_IF_MISSING_RATE_GE = True

# Use unicode escapes to avoid IDE/terminal encoding corruption for "站点_".
DATA_DIR_RE = re.compile(r"^(?:\u7ad9\u70b9_)?\d{8}-\d{8}$")
DAILY_FILE_RE = re.compile(r"^china_sites_(\d{8})\.csv$")
LAST_OUTPUT_TAG = None


def parse_mixed_date(series):
    s = series.astype(str).str.strip()
    dt = pd.to_datetime(s, format="%Y%m%d", errors="coerce")
    miss = dt.isna()
    if miss.any():
        dt2 = pd.to_datetime(s[miss], errors="coerce")
        dt.loc[miss] = dt2
    return dt


def validate_output_range(output_start, output_end):
    start_ts = pd.Timestamp(output_start).floor("h")
    end_ts = pd.Timestamp(output_end).floor("h")
    if start_ts > end_ts:
        raise ValueError(f"Invalid output range: start {start_ts} is after end {end_ts}")
    return start_ts, end_ts


def resolve_output_range_from_ide():
    start_text = OUTPUT_START
    end_text = OUTPUT_END

    if PROMPT_FOR_RANGE:
        print("Input output datetime range (press Enter to keep default values).")
        try:
            user_start = input(f"Output start [{OUTPUT_START}]: ").strip()
            user_end = input(f"Output end   [{OUTPUT_END}]: ").strip()
            if user_start:
                start_text = user_start
            if user_end:
                end_text = user_end
        except EOFError:
            print("No interactive input detected, using default output range values.")

    return validate_output_range(start_text, end_text)


def discover_data_dirs(input_root):
    dirs = []
    if not os.path.isdir(input_root):
        return dirs

    root_name = os.path.basename(os.path.normpath(input_root))
    if DATA_DIR_RE.match(root_name):
        return [input_root]

    for name in sorted(os.listdir(input_root)):
        path = os.path.join(input_root, name)
        if os.path.isdir(path) and DATA_DIR_RE.match(name):
            dirs.append(path)
    return dirs


def path_has_data_segment(path):
    parts = os.path.normpath(path).split(os.sep)
    return any(DATA_DIR_RE.match(p) for p in parts)


def collect_daily_files(input_root, start_date, end_date):
    raw_daily_files = []
    matched_dirs = discover_data_dirs(input_root)

    scanned_candidate_count = 0
    for data_dir in matched_dirs:
        for dirpath, _, filenames in os.walk(data_dir):
            for name in filenames:
                match = DAILY_FILE_RE.match(name)
                if not match:
                    continue

                file_path = os.path.join(dirpath, name)
                if not path_has_data_segment(file_path):
                    continue

                scanned_candidate_count += 1
                day = pd.to_datetime(match.group(1), format="%Y%m%d", errors="coerce")
                if pd.isna(day):
                    continue
                day_date = day.date()
                if start_date <= day_date <= end_date:
                    raw_daily_files.append((day_date, file_path))

    raw_daily_files.sort(key=lambda x: (x[0], x[1]))

    deduped = {}
    duplicate_overrides = []
    for day_date, file_path in raw_daily_files:
        if day_date in deduped:
            duplicate_overrides.append((day_date, deduped[day_date], file_path))
        deduped[day_date] = file_path

    daily_files = [deduped[d] for d in sorted(deduped)]
    stats = {
        "matched_dirs": len(matched_dirs),
        "scanned_candidate_files": scanned_candidate_count,
        "in_range_files": len(raw_daily_files),
        "unique_dates": len(daily_files),
        "duplicate_overrides": duplicate_overrides,
    }
    return daily_files, stats


def interpolate_by_year(series):
    return series.groupby(series.index.year, group_keys=False).apply(
        lambda s: s.interpolate(
            method="time",
            limit=SHORT_GAP_LIMIT,
            limit_direction="both",
            limit_area="inside",
        )
    )


def annual_invalidate_columns(wide_df, value_cols):
    invalid_by_year = {}
    years = sorted(wide_df["datetime"].dt.year.unique().tolist())

    for year in years:
        year_mask = wide_df["datetime"].dt.year == year
        hours_in_year = int(year_mask.sum())
        if hours_in_year == 0:
            continue

        invalid_for_year = {}
        for col in value_cols:
            missing_rate = float(wide_df.loc[year_mask, col].isna().sum()) / hours_in_year
            if INVALID_IF_MISSING_RATE_GE:
                is_invalid = missing_rate >= INVALID_MISSING_RATE
            else:
                is_invalid = missing_rate > INVALID_MISSING_RATE

            if is_invalid:
                wide_df.loc[year_mask, col] = pd.NA
                invalid_for_year[col] = round(missing_rate, 4)

        if invalid_for_year:
            invalid_by_year[year] = invalid_for_year

    return invalid_by_year


def process_site(site_id, site_alias, frames, full_index, output_dir, output_tag):
    if not frames:
        print(f"No valid rows found for {site_id}")
        return

    long_df = pd.concat(frames, ignore_index=True)
    wide_df = (
        long_df.pivot_table(
            index=["date", "hour"],
            columns="type",
            values=site_id,
            aggfunc="first",
        )
        .reset_index()
        .sort_values(["date", "hour"])
    )
    wide_df.columns.name = None

    wide_df["date"] = parse_mixed_date(wide_df["date"])
    wide_df["hour"] = pd.to_numeric(wide_df["hour"], errors="coerce")
    wide_df = wide_df.dropna(subset=["date", "hour"]).copy()
    if wide_df.empty:
        print(f"Skip {site_id}: no valid date/hour rows after parsing")
        return

    wide_df["hour"] = wide_df["hour"].astype(int)
    wide_df["datetime"] = wide_df["date"] + pd.to_timedelta(wide_df["hour"], unit="h")
    wide_df = wide_df.dropna(subset=["datetime"]).sort_values("datetime").drop_duplicates(subset=["datetime"]).copy()
    if wide_df.empty:
        print(f"Skip {site_id}: datetime is empty after cleanup")
        return

    wide_df = (
        wide_df.set_index("datetime")
        .reindex(full_index)
        .rename_axis("datetime")
        .reset_index()
    )

    value_cols = [c for c in wide_df.columns if c not in ["datetime", "date", "hour"]]
    if not value_cols:
        print(f"Skip {site_id}: no value columns found after pivot")
        return

    for col in value_cols:
        wide_df[col] = pd.to_numeric(wide_df[col], errors="coerce")

    invalid_by_year = annual_invalidate_columns(wide_df, value_cols)
    if invalid_by_year:
        threshold_op = ">=" if INVALID_IF_MISSING_RATE_GE else ">"
        print(
            f"{site_id}: annual invalid columns "
            f"(missing_rate {threshold_op} {INVALID_MISSING_RATE:.0%}) -> {invalid_by_year}"
        )

    missing_before = {col: int(wide_df[col].isna().sum()) for col in value_cols}

    wide_df = wide_df.set_index("datetime")
    for col in value_cols:
        wide_df[col] = interpolate_by_year(wide_df[col])
    wide_df = wide_df.reset_index()

    wide_df["date"] = wide_df["datetime"].dt.strftime("%Y%m%d").astype(int)
    wide_df["hour"] = wide_df["datetime"].dt.hour.astype(int)
    wide_df = wide_df[["date", "hour"] + value_cols].sort_values(["date", "hour"]).reset_index(drop=True)

    missing_after = {col: int(wide_df[col].isna().sum()) for col in value_cols}
    filled_short = {col: missing_before[col] - missing_after[col] for col in value_cols}

    valid_counts = wide_df[value_cols].notna().sum()
    if not (valid_counts > 0).any():
        print(f"Skip {site_id}: all value columns are NaN after invalidation/interpolation, no CSV output")
        return

    output_name = f"{site_id}_{site_alias[site_id]}_{output_tag}_wide.csv"
    output_path = os.path.join(output_dir, output_name)
    wide_df.to_csv(output_path, index=False, encoding="utf-8-sig")

    print(f"Saved: {output_path}")
    print(f"  Interp summary (limit={SHORT_GAP_LIMIT}h, yearly grouped):")
    print("  ", {k: v for k, v in filled_short.items() if v > 0})


def main():
    global LAST_OUTPUT_TAG
    input_root = INPUT_ROOT
    output_dir = OUTPUT_DIR
    output_start, output_end = resolve_output_range_from_ide()
    output_tag = f"{output_start:%Y%m%d}_{output_end:%Y%m%d}"
    LAST_OUTPUT_TAG = output_tag
    full_index = pd.date_range(start=output_start, end=output_end, freq="h")

    if len(full_index) == 0:
        raise ValueError(f"Empty date range from {output_start} to {output_end}")

    daily_files, scan_stats = collect_daily_files(input_root, output_start.date(), output_end.date())
    print(
        "Scan summary:",
        {
            "matched_dirs": scan_stats["matched_dirs"],
            "scanned_candidate_files": scan_stats["scanned_candidate_files"],
            "in_range_files": scan_stats["in_range_files"],
            "unique_dates": scan_stats["unique_dates"],
        },
    )
    if scan_stats["duplicate_overrides"]:
        print(
            f"Duplicate day files detected: {len(scan_stats['duplicate_overrides'])}, "
            "keeping lexicographically last path"
        )
        preview = scan_stats["duplicate_overrides"][:20]
        for day_date, old_path, new_path in preview:
            print(f"  {day_date}: {old_path} -> {new_path}")
        if len(scan_stats["duplicate_overrides"]) > len(preview):
            print(f"  ... and {len(scan_stats['duplicate_overrides']) - len(preview)} more")

    if not daily_files:
        print(
            f"No daily files found in {input_root} for date range "
            f"{output_start.date()} to {output_end.date()}"
        )
        return

    reference_file = daily_files[0]
    header_cols = pd.read_csv(reference_file, nrows=0).columns
    target_sites = [site_id for site_id in SITE_ALIAS if site_id in header_cols]
    missing_sites = [site_id for site_id in SITE_ALIAS if site_id not in header_cols]

    if missing_sites:
        print("Missing site columns in reference file:", missing_sites)
    if not target_sites:
        print(f"No target sites found in reference file: {reference_file}")
        return

    site_frames = {site_id: [] for site_id in target_sites}
    base_cols = ["date", "hour", "type"]
    required_base_cols = set(base_cols)
    required_cols = set(base_cols + target_sites)

    for file_path in daily_files:
        try:
            df = pd.read_csv(file_path, usecols=lambda c: c in required_cols)
        except Exception as err:
            print(f"Skip file (read error): {file_path} -> {err}")
            continue

        if not required_base_cols.issubset(df.columns):
            print(f"Skip file (missing base columns): {file_path}")
            continue

        for site_id in target_sites:
            if site_id in df.columns:
                site_frames[site_id].append(df[base_cols + [site_id]])

    os.makedirs(output_dir, exist_ok=True)
    for site_id in target_sites:
        process_site(
            site_id=site_id,
            site_alias=SITE_ALIAS,
            frames=site_frames[site_id],
            full_index=full_index,
            output_dir=output_dir,
            output_tag=output_tag,
        )


if __name__ == "__main__":
    main()

#%%
import os
import pandas as pd
import matplotlib.pyplot as plt

plot_site_id = "3241A"
plot_output_start, plot_output_end = validate_output_range(OUTPUT_START, OUTPUT_END)
plot_output_tag = LAST_OUTPUT_TAG or f"{plot_output_start:%Y%m%d}_{plot_output_end:%Y%m%d}"
folder = OUTPUT_DIR
site_file = os.path.join(
    folder,
    f"{plot_site_id}_{SITE_ALIAS[plot_site_id]}_{plot_output_tag}_wide.csv",
)

df = pd.read_csv(site_file)
df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
df["datetime"] = df["date"] + pd.to_timedelta(df["hour"], unit="h")

start = pd.Timestamp("2025-01-01 00:00:00")
end = pd.Timestamp("2026-02-28 23:00:00")
plot_df = df[(df["datetime"] >= start) & (df["datetime"] <= end)].copy()

metrics = ["AQI", "PM2.5", "PM10", "NO2", "SO2", "O3", "CO"]
metrics = [m for m in metrics if m in plot_df.columns]

# Only short-gap interpolation for each variable (scientific conservative):
# fill at most 2 consecutive missing points; long gaps remain NaN.
plot_df = plot_df.sort_values("datetime").set_index("datetime")
for metric in metrics:
    plot_df[metric] = pd.to_numeric(plot_df[metric], errors="coerce")
    plot_df[metric] = plot_df[metric].interpolate(
        method="time",
        limit=2,
        limit_direction="both",
        limit_area="inside",
    )
plot_df = plot_df.reset_index()

plt.figure(figsize=(16, 6))
for metric in metrics:
    plt.plot(plot_df["datetime"], plot_df[metric], label=metric, linewidth=1.5)

plt.title("3241A Hourly Metrics (2025-01-01 to 2026-02-28)")
plt.xlabel("Datetime")
plt.ylabel("Value")
plt.legend(ncol=4)
plt.grid(alpha=0.3)
plt.tight_layout()
plt.savefig(os.path.join(folder, f"{plot_site_id}_metrics_{plot_output_tag}.png"), dpi=300)
plt.show()



# %%
