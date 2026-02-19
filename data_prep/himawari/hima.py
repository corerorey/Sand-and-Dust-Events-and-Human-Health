from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import struct
import sys

import cartopy.crs as ccrs
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
import xarray as xr


MAPBASE_DIR = Path(__file__).resolve().parents[1] / "mapbase"
if str(MAPBASE_DIR) not in sys.path:
    sys.path.insert(0, str(MAPBASE_DIR))

import world_adm0_china_region_map as hlcn


DEFAULT_DAT_ROOT = Path(r"C:\DOCUMENTO\himawari")
DEFAULT_TIME = "20210316_0400"
DEFAULT_BANDS = ("B11", "B13", "B15")
DEFAULT_OUT_DIR = Path("data_prep/himawari/out_hima")
DEFAULT_EXTENT = (70.0, 145.0, 5.0, 60.0)
DEFAULT_STATION_NC_PATH = Path(r"C:\DOCUMENTO\nc_out\documento_all_sites_20210101_20211231.nc")
DEFAULT_STATION_VAR = "PM10"

RGB_R_RANGE = (-6.7, 2.6)
RGB_G_RANGE = (-0.5, 20.0)
RGB_B_RANGE = (261.2, 288.7)


CALIBRATION_HEAD_DTYPE = np.dtype(
    [
        ("hblock_number", "u1"),
        ("blocklength", "<u2"),
        ("band_number", "<u2"),
        ("central_wave_length", "f8"),
        ("valid_number_of_bits_per_pixel", "<u2"),
        ("count_value_error_pixels", "<u2"),
        ("count_value_outside_scan_pixels", "<u2"),
        ("gain_count2rad_conversion", "f8"),
        ("offset_count2rad_conversion", "f8"),
    ]
)

IR_CALIBRATION_DTYPE = np.dtype(
    [
        ("c0_rad2tb_conversion", "f8"),
        ("c1_rad2tb_conversion", "f8"),
        ("c2_rad2tb_conversion", "f8"),
        ("c0_tb2rad_conversion", "f8"),
        ("c1_tb2rad_conversion", "f8"),
        ("c2_tb2rad_conversion", "f8"),
        ("speed_of_light", "f8"),
        ("planck_constant", "f8"),
        ("boltzmann_constant", "f8"),
        ("spare", "S40"),
    ]
)


@dataclass(frozen=True)
class ProjectionParams:
    sub_lon_deg: float
    cfac: int
    lfac: int
    coff: float
    loff: float
    sat_dist_km: float
    eq_radius_km: float
    pol_radius_km: float


@dataclass(frozen=True)
class CalibrationParams:
    band_number: int
    central_wavelength_um: float
    error_count_value: int
    outside_count_value: int
    gain_count2rad: float
    offset_count2rad: float
    c0_rad2tb: float
    c1_rad2tb: float
    c2_rad2tb: float
    speed_of_light: float
    planck_constant: float
    boltzmann_constant: float


@dataclass(frozen=True)
class SegmentRecord:
    path: Path
    band: str
    segment_no: int
    total_segments: int
    lines: int
    cols: int
    data: np.ndarray
    projection: ProjectionParams
    calibration: CalibrationParams


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Himawari-8 HSD single-time workflow: DN->Radiance->BT->Dust RGB/BTD, "
            "conservative cloud mask, and China-wide PM10 station collocation."
        )
    )
    parser.add_argument(
        "--dat-root",
        type=Path,
        default=DEFAULT_DAT_ROOT,
        help="Root directory that contains HS_H08_*.DAT files.",
    )
    parser.add_argument(
        "--time-tag",
        type=str,
        default=DEFAULT_TIME,
        help="Time tag in filename, e.g. 20210316_0400.",
    )
    parser.add_argument(
        "--obs-time-tag-tz",
        type=str,
        default="UTC",
        choices=["UTC", "LOCAL", "utc", "local"],
        help="How to interpret --time-tag before converting to UTC.",
    )
    parser.add_argument(
        "--local-offset-hours",
        type=int,
        default=8,
        help="Local timezone offset from UTC for station-time alignment (e.g., UTC+8).",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="Output directory for figures and CSVs.",
    )
    parser.add_argument(
        "--step",
        type=int,
        default=8,
        help="Spatial downsample step for plotting and collocation.",
    )
    parser.add_argument(
        "--extent",
        type=float,
        nargs=4,
        default=list(DEFAULT_EXTENT),
        metavar=("LON_MIN", "LON_MAX", "LAT_MIN", "LAT_MAX"),
        help="Map extent for overlay.",
    )
    parser.add_argument(
        "--station-nc-path",
        type=Path,
        default=DEFAULT_STATION_NC_PATH,
        help="China station NetCDF path.",
    )
    parser.add_argument(
        "--station-var",
        type=str,
        default=DEFAULT_STATION_VAR,
        help="Station pollutant variable name in NetCDF, e.g. PM10.",
    )
    parser.add_argument(
        "--cloud-mask",
        type=str,
        default="conservative",
        choices=["conservative", "none"],
        help="Cloud handling strategy for dust diagnostics.",
    )
    return parser.parse_args()


def configure_mapbase_paths() -> None:
    hlcn.DEFAULT_WORLD_ADM0_SHP = str(
        MAPBASE_DIR / "geoBoundariesCGAZ_ADM0" / "geoBoundariesCGAZ_ADM0.shp"
    )
    hlcn.DEFAULT_WORLD_ADM1_SHP = str(
        MAPBASE_DIR / "geoBoundariesCGAZ_ADM1" / "geoBoundariesCGAZ_ADM1.shp"
    )
    hlcn.DEFAULT_CHINA_ADM0_SIMPLIFIED_SHP = str(
        MAPBASE_DIR
        / "geoBoundaries-CHN-ADM0-all"
        / "geoBoundaries-CHN-ADM0_simplified.shp"
    )


def infer_obs_time_utc(time_tag: str, time_tag_tz: str, local_offset_hours: int) -> pd.Timestamp:
    try:
        dt = datetime.strptime(time_tag, "%Y%m%d_%H%M")
    except ValueError as exc:
        raise ValueError(f"Invalid time-tag format: {time_tag}, expected YYYYMMDD_HHMM") from exc
    ts = pd.Timestamp(dt)
    if time_tag_tz.upper() == "UTC":
        return ts
    return ts - pd.Timedelta(hours=int(local_offset_hours))


def build_band_paths(dat_root: Path, time_tag: str, band: str) -> list[Path]:
    paths: list[Path] = []
    for seg in range(1, 11):
        name = f"HS_H08_{time_tag}_{band}_FLDK_R20_S{seg:02d}10.DAT"
        path = dat_root / name
        if not path.exists():
            raise FileNotFoundError(f"Missing DAT file: {path}")
        paths.append(path)
    return paths


def parse_header_blocks(blob: bytes) -> tuple[dict[int, bytes], int]:
    n_blocks = blob[3]
    blocks: dict[int, bytes] = {}
    offset = 0
    for _ in range(n_blocks):
        block_id = blob[offset]
        block_len = struct.unpack_from("<H", blob, offset + 1)[0]
        blocks[block_id] = blob[offset : offset + block_len]
        offset += block_len
    return blocks, offset


def parse_projection(block3: bytes) -> ProjectionParams:
    return ProjectionParams(
        sub_lon_deg=struct.unpack_from("<d", block3, 3)[0],
        cfac=struct.unpack_from("<i", block3, 11)[0],
        lfac=struct.unpack_from("<i", block3, 15)[0],
        coff=struct.unpack_from("<f", block3, 19)[0],
        loff=struct.unpack_from("<f", block3, 23)[0],
        sat_dist_km=struct.unpack_from("<d", block3, 27)[0],
        eq_radius_km=struct.unpack_from("<d", block3, 35)[0],
        pol_radius_km=struct.unpack_from("<d", block3, 43)[0],
    )


def parse_calibration(block5: bytes) -> CalibrationParams:
    expected = CALIBRATION_HEAD_DTYPE.itemsize + IR_CALIBRATION_DTYPE.itemsize
    if len(block5) != expected:
        raise ValueError(f"Unexpected block5 length: {len(block5)} (expected {expected}).")

    head = np.frombuffer(block5, dtype=CALIBRATION_HEAD_DTYPE, count=1)[0]
    ir = np.frombuffer(block5[CALIBRATION_HEAD_DTYPE.itemsize :], dtype=IR_CALIBRATION_DTYPE, count=1)[0]
    return CalibrationParams(
        band_number=int(head["band_number"]),
        central_wavelength_um=float(head["central_wave_length"]),
        error_count_value=int(head["count_value_error_pixels"]),
        outside_count_value=int(head["count_value_outside_scan_pixels"]),
        gain_count2rad=float(head["gain_count2rad_conversion"]),
        offset_count2rad=float(head["offset_count2rad_conversion"]),
        c0_rad2tb=float(ir["c0_rad2tb_conversion"]),
        c1_rad2tb=float(ir["c1_rad2tb_conversion"]),
        c2_rad2tb=float(ir["c2_rad2tb_conversion"]),
        speed_of_light=float(ir["speed_of_light"]),
        planck_constant=float(ir["planck_constant"]),
        boltzmann_constant=float(ir["boltzmann_constant"]),
    )

def read_segment(path: Path, band: str) -> SegmentRecord:
    blob = path.read_bytes()
    blocks, header_len = parse_header_blocks(blob)

    block2 = blocks[2]
    lines = int(struct.unpack_from("<H", block2, 7)[0])
    cols = int(struct.unpack_from("<H", block2, 5)[0])

    block7 = blocks[7]
    total_segments = int(block7[3])
    segment_no = int(block7[4])

    projection = parse_projection(blocks[3])
    calibration = parse_calibration(blocks[5])
    count = lines * cols
    data = np.frombuffer(blob, dtype="<u2", offset=header_len, count=count).reshape(lines, cols).copy()

    return SegmentRecord(
        path=path,
        band=band,
        segment_no=segment_no,
        total_segments=total_segments,
        lines=lines,
        cols=cols,
        data=data,
        projection=projection,
        calibration=calibration,
    )


def assemble_band(band: str, paths: list[Path]) -> tuple[np.ndarray, ProjectionParams, CalibrationParams]:
    records = [read_segment(p, band=band) for p in paths]
    records.sort(key=lambda r: r.segment_no)

    total_segments = records[0].total_segments
    lines = records[0].lines
    cols = records[0].cols
    expected = set(range(1, total_segments + 1))
    got = {r.segment_no for r in records}
    if got != expected:
        raise ValueError(f"{band}: segments mismatch, expected {sorted(expected)}, got {sorted(got)}")

    full = np.empty((lines * total_segments, cols), dtype=np.uint16)
    for rec in records:
        start = (rec.segment_no - 1) * lines
        full[start : start + lines, :] = rec.data

    return full, records[0].projection, records[0].calibration


def geos_to_lonlat(
    n_rows: int, n_cols: int, projection: ProjectionParams, step: int
) -> tuple[np.ndarray, np.ndarray]:
    rows = np.arange(1, n_rows + 1, step, dtype=np.float64)
    cols = np.arange(1, n_cols + 1, step, dtype=np.float64)

    x = np.deg2rad((cols - projection.coff) / (projection.cfac * (2.0**-16)))
    y = np.deg2rad(-(rows - projection.loff) / (projection.lfac * (2.0**-16)))
    x2d, y2d = np.meshgrid(x, y)

    req = projection.eq_radius_km * 1000.0
    rpol = projection.pol_radius_km * 1000.0
    H = projection.sat_dist_km * 1000.0
    lon0 = np.deg2rad(projection.sub_lon_deg)

    cos_x = np.cos(x2d)
    sin_x = np.sin(x2d)
    cos_y = np.cos(y2d)
    sin_y = np.sin(y2d)

    with np.errstate(invalid="ignore"):
        a = sin_x**2 + cos_x**2 * (cos_y**2 + (req**2 / rpol**2) * sin_y**2)
        b = -2.0 * H * cos_x * cos_y
        c = H**2 - req**2
        disc = b**2 - 4.0 * a * c
        valid = disc > 0

        rs = np.full_like(x2d, np.nan, dtype=np.float64)
        rs[valid] = (-b[valid] - np.sqrt(disc[valid])) / (2.0 * a[valid])

        sx = rs * cos_x * cos_y
        sy = -rs * sin_x
        sz = rs * cos_x * sin_y

        lon = lon0 - np.arctan2(sy, H - sx)
        lat = np.arctan((req**2 / rpol**2) * (sz / np.sqrt((H - sx) ** 2 + sy**2)))

    lon_deg = np.rad2deg(lon).astype(np.float32)
    lat_deg = np.rad2deg(lat).astype(np.float32)
    lon_deg[~valid] = np.nan
    lat_deg[~valid] = np.nan
    return lon_deg, lat_deg


def dn_to_bt(dn: np.ndarray, cal: CalibrationParams) -> tuple[np.ndarray, np.ndarray]:
    dn_f = dn.astype(np.float64)
    valid = (dn_f != cal.error_count_value) & (dn_f != cal.outside_count_value)

    radiance = cal.gain_count2rad * dn_f + cal.offset_count2rad
    valid &= np.isfinite(radiance) & (radiance > 0.0)

    wl_m = cal.central_wavelength_um * 1e-6
    c = cal.speed_of_light
    h = cal.planck_constant
    k = cal.boltzmann_constant

    with np.errstate(invalid="ignore", divide="ignore"):
        a = (h * c) / (k * wl_m)
        b = ((2.0 * h * c * c) / (radiance * 1e6 * (wl_m**5))) + 1.0
        te = a / np.log(b)
        bt = cal.c0_rad2tb + cal.c1_rad2tb * te + cal.c2_rad2tb * (te**2)

    bt = bt.astype(np.float32)
    bt[~valid] = np.nan

    radiance_f = radiance.astype(np.float32)
    radiance_f[~valid] = np.nan
    return bt, radiance_f


def normalize_fixed(data: np.ndarray, low: float, high: float) -> np.ndarray:
    if np.isclose(low, high):
        return np.zeros_like(data, dtype=np.float32)
    out = ((data - low) / (high - low)).astype(np.float32)
    out = np.clip(out, 0.0, 1.0, out=out)
    out[~np.isfinite(data)] = np.nan
    return out


def draw_hlcn_canvas_on_axis(ax: plt.Axes, extent: tuple[float, float, float, float]) -> None:
    hlcn.draw_world_adm0_china_highlight_canvas(
        ax=ax,
        extent=extent,
        draw_grid=True,
        show_country_labels=True,
        avoid_label_overlap=True,
        processing_extent=extent,
        neighbor_linewidth=0.35,
        china_linewidth=1.1,
        china_edgecolor="#5a5a5a",
        china_alpha=0.72,
        omit_shared_with_china=True,
    )


def create_hlcn_canvas(extent: tuple[float, float, float, float]) -> tuple[plt.Figure, plt.Axes]:
    fig = plt.figure(figsize=(12.0, 7.0), dpi=230)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    draw_hlcn_canvas_on_axis(ax, extent)
    return fig, ax


def extent_mask(
    lon: np.ndarray, lat: np.ndarray, extent: tuple[float, float, float, float]
) -> np.ndarray:
    lon_min, lon_max, lat_min, lat_max = extent
    return (
        np.isfinite(lon)
        & np.isfinite(lat)
        & (lon >= lon_min)
        & (lon <= lon_max)
        & (lat >= lat_min)
        & (lat <= lat_max)
    )


def plot_bt_band(
    lon: np.ndarray,
    lat: np.ndarray,
    bt: np.ndarray,
    extent: tuple[float, float, float, float],
    out_path: Path,
    title: str,
    cmap: str,
) -> None:
    fig, ax = create_hlcn_canvas(extent)
    m = extent_mask(lon, lat, extent) & np.isfinite(bt)
    if not m.any():
        raise RuntimeError(f"{out_path.name}: no valid BT pixels in extent.")

    vals = bt[m]
    vmin = float(np.nanpercentile(vals, 2))
    vmax = float(np.nanpercentile(vals, 98))
    if np.isclose(vmin, vmax):
        vmax = vmin + 1.0

    sc = ax.scatter(
        lon[m],
        lat[m],
        c=vals,
        s=0.9,
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        linewidths=0,
        rasterized=True,
        transform=ccrs.PlateCarree(),
        zorder=6,
    )
    ax.set_title(title)
    cbar = fig.colorbar(sc, ax=ax, shrink=0.86, pad=0.02)
    cbar.set_label("Brightness Temperature (K)")
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)

def build_dust_products(
    bt11: np.ndarray,
    bt13: np.ndarray,
    bt15: np.ndarray,
    cloud_mask_mode: str,
) -> dict[str, np.ndarray]:
    valid = np.isfinite(bt11) & np.isfinite(bt13) & np.isfinite(bt15)

    btd15_13 = bt15 - bt13
    btd13_11 = bt13 - bt11
    blue_bt13 = bt13

    r = normalize_fixed(btd15_13, RGB_R_RANGE[0], RGB_R_RANGE[1])
    g = normalize_fixed(btd13_11, RGB_G_RANGE[0], RGB_G_RANGE[1])
    b = normalize_fixed(blue_bt13, RGB_B_RANGE[0], RGB_B_RANGE[1])

    cloud_mask = np.zeros_like(valid, dtype=bool)
    if cloud_mask_mode == "conservative":
        cloud_mask = (bt13 < 273.15) | (btd15_13 < RGB_R_RANGE[0]) | (btd13_11 < RGB_G_RANGE[0])
        cloud_mask &= valid

    clear = valid & ~cloud_mask
    dust_candidate = clear & (btd15_13 > 0.0) & (btd13_11 > 0.0)

    dli = np.full_like(bt13, np.nan, dtype=np.float32)
    dli[clear] = 0.7 * r[clear] + 0.3 * g[clear]

    rgb = np.stack([r, g, b], axis=-1)
    return {
        "valid_mask": valid,
        "btd15_13": btd15_13.astype(np.float32),
        "btd13_11": btd13_11.astype(np.float32),
        "blue_bt13": blue_bt13.astype(np.float32),
        "r_norm": r,
        "g_norm": g,
        "b_norm": b,
        "rgb": rgb.astype(np.float32),
        "cloud_mask": cloud_mask,
        "clear_mask": clear,
        "dust_candidate": dust_candidate,
        "dli": dli,
    }


def plot_dust_rgb_fixed(
    lon: np.ndarray,
    lat: np.ndarray,
    products: dict[str, np.ndarray],
    extent: tuple[float, float, float, float],
    out_path: Path,
    obs_utc: pd.Timestamp,
) -> None:
    fig, ax = create_hlcn_canvas(extent)
    m = extent_mask(lon, lat, extent) & products["valid_mask"]
    if not m.any():
        raise RuntimeError(f"{out_path.name}: no valid Dust RGB pixels in extent.")

    ax.scatter(
        lon[m],
        lat[m],
        c=products["rgb"][m],
        s=0.9,
        linewidths=0,
        rasterized=True,
        transform=ccrs.PlateCarree(),
        zorder=6,
    )
    ax.set_title(f"Himawari Dust RGB (fixed ranges) | obs_utc={obs_utc}")
    fig.text(
        0.012,
        0.015,
        (
            f"R=B15-B13 in [{RGB_R_RANGE[0]}, {RGB_R_RANGE[1]}], "
            f"G=B13-B11 in [{RGB_G_RANGE[0]}, {RGB_G_RANGE[1]}], "
            f"B=B13 in [{RGB_B_RANGE[0]}, {RGB_B_RANGE[1]}] (K)"
        ),
        ha="left",
        va="bottom",
        fontsize=8,
    )
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)


def plot_binary_mask(
    lon: np.ndarray,
    lat: np.ndarray,
    base_valid: np.ndarray,
    target_mask: np.ndarray,
    extent: tuple[float, float, float, float],
    out_path: Path,
    title: str,
    ticklabels: tuple[str, str],
    colors: tuple[str, str],
) -> None:
    fig, ax = create_hlcn_canvas(extent)
    m = extent_mask(lon, lat, extent) & base_valid
    if not m.any():
        raise RuntimeError(f"{out_path.name}: no valid pixels in extent.")

    sc = ax.scatter(
        lon[m],
        lat[m],
        c=np.where(target_mask[m], 1.0, 0.0),
        s=0.9,
        cmap=ListedColormap([colors[0], colors[1]]),
        vmin=0.0,
        vmax=1.0,
        linewidths=0,
        rasterized=True,
        transform=ccrs.PlateCarree(),
        zorder=6,
    )
    cb = fig.colorbar(sc, ax=ax, shrink=0.86, pad=0.02)
    cb.set_ticks([0.0, 1.0])
    cb.set_ticklabels([ticklabels[0], ticklabels[1]])
    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)


def to_py_str(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore").strip()
    return str(value).strip()


def load_station_snapshot(
    nc_path: Path,
    station_var: str,
    target_local_time: pd.Timestamp,
    extent: tuple[float, float, float, float],
) -> tuple[pd.DataFrame, dict[str, object]]:
    if not nc_path.exists():
        raise FileNotFoundError(f"Station NC file not found: {nc_path}")

    ds = xr.open_dataset(nc_path)
    try:
        if station_var not in ds.data_vars:
            raise KeyError(f"Station variable not found in NetCDF: {station_var}")

        try:
            ds_t = ds.sel(time=target_local_time)
            matched_exact = True
        except Exception:
            ds_t = ds.sel(time=target_local_time, method="nearest")
            matched_exact = False

        matched_local = pd.Timestamp(ds_t["time"].values)
        lon = np.asarray(ds_t["lon"].values, dtype=float)
        lat = np.asarray(ds_t["lat"].values, dtype=float)
        pm = np.asarray(ds_t[station_var].values, dtype=float)

        n_sites_total = int(lon.size)
        valid = np.isfinite(lon) & np.isfinite(lat) & np.isfinite(pm)
        n_sites_valid_pm = int(valid.sum())

        ext = (
            (lon >= extent[0])
            & (lon <= extent[1])
            & (lat >= extent[2])
            & (lat <= extent[3])
        )
        sel = valid & ext

        if "site_number" in ds_t:
            site_number = [to_py_str(v) for v in ds_t["site_number"].values]
        else:
            site_number = [str(i) for i in range(n_sites_total)]

        if "site_name_zh" in ds_t:
            site_name_zh = [to_py_str(v) for v in ds_t["site_name_zh"].values]
        else:
            site_name_zh = [""] * n_sites_total

        if "city_zh" in ds_t:
            city_zh = [to_py_str(v) for v in ds_t["city_zh"].values]
        else:
            city_zh = [""] * n_sites_total

        raw_df = pd.DataFrame(
            {
                "site_index": np.arange(n_sites_total, dtype=int),
                "site_number": site_number,
                "site_name_zh": site_name_zh,
                "city_zh": city_zh,
                "lon": lon,
                "lat": lat,
                "pm10": pm,
            }
        )
        use_df = raw_df.loc[sel].reset_index(drop=True)
        meta: dict[str, object] = {
            "n_sites_total": n_sites_total,
            "n_sites_valid_pm10": n_sites_valid_pm,
            "n_sites_in_extent": int(sel.sum()),
            "target_local_time": str(target_local_time),
            "matched_local_time": str(matched_local),
            "matched_exact": matched_exact,
            "station_var": station_var,
            "station_nc_path": str(nc_path),
        }
        return use_df, meta
    finally:
        ds.close()

def haversine_km(
    lon1: np.ndarray, lat1: np.ndarray, lon2: np.ndarray, lat2: np.ndarray
) -> np.ndarray:
    lon1r = np.deg2rad(lon1)
    lat1r = np.deg2rad(lat1)
    lon2r = np.deg2rad(lon2)
    lat2r = np.deg2rad(lat2)
    dlon = lon2r - lon1r
    dlat = lat2r - lat1r
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat1r) * np.cos(lat2r) * np.sin(dlon / 2.0) ** 2
    c = 2.0 * np.arctan2(np.sqrt(a), np.sqrt(1.0 - a))
    return (6371.0 * c).astype(np.float32)


def collocate_stations(
    station_df: pd.DataFrame,
    lon: np.ndarray,
    lat: np.ndarray,
    bt11: np.ndarray,
    bt13: np.ndarray,
    bt15: np.ndarray,
    products: dict[str, np.ndarray],
) -> pd.DataFrame:
    if station_df.empty:
        return station_df.copy()

    sat_valid = np.isfinite(lon) & np.isfinite(lat) & products["valid_mask"]
    if not sat_valid.any():
        raise RuntimeError("No valid satellite points available for collocation.")

    sat_points = np.column_stack([lon[sat_valid], lat[sat_valid]])
    tree = cKDTree(sat_points)

    st_points = np.column_stack([station_df["lon"].to_numpy(), station_df["lat"].to_numpy()])
    _, nn_idx = tree.query(st_points, k=1)
    sat_lin_valid = np.flatnonzero(sat_valid)
    sat_lin = sat_lin_valid[nn_idx]
    sat_r, sat_c = np.unravel_index(sat_lin, lon.shape)

    out = station_df.copy()
    out["sat_row"] = sat_r.astype(int)
    out["sat_col"] = sat_c.astype(int)
    out["sat_lon"] = lon[sat_r, sat_c].astype(np.float32)
    out["sat_lat"] = lat[sat_r, sat_c].astype(np.float32)
    out["sat_distance_km"] = haversine_km(
        out["lon"].to_numpy(dtype=float),
        out["lat"].to_numpy(dtype=float),
        out["sat_lon"].to_numpy(dtype=float),
        out["sat_lat"].to_numpy(dtype=float),
    )
    out["sat_bt11_k"] = bt11[sat_r, sat_c].astype(np.float32)
    out["sat_bt13_k"] = bt13[sat_r, sat_c].astype(np.float32)
    out["sat_bt15_k"] = bt15[sat_r, sat_c].astype(np.float32)
    out["sat_btd15_13_k"] = products["btd15_13"][sat_r, sat_c].astype(np.float32)
    out["sat_btd13_11_k"] = products["btd13_11"][sat_r, sat_c].astype(np.float32)
    out["sat_rgb_r_norm"] = products["r_norm"][sat_r, sat_c].astype(np.float32)
    out["sat_rgb_g_norm"] = products["g_norm"][sat_r, sat_c].astype(np.float32)
    out["sat_rgb_b_norm"] = products["b_norm"][sat_r, sat_c].astype(np.float32)
    out["sat_cloud_flag"] = products["cloud_mask"][sat_r, sat_c].astype(np.int8)
    out["sat_dust_candidate_flag"] = products["dust_candidate"][sat_r, sat_c].astype(np.int8)
    out["sat_dli"] = products["dli"][sat_r, sat_c].astype(np.float32)
    return out


def _safe_spearman(x: pd.Series, y: pd.Series) -> float:
    xx = pd.to_numeric(x, errors="coerce")
    yy = pd.to_numeric(y, errors="coerce")
    m = np.isfinite(xx) & np.isfinite(yy)
    if int(m.sum()) < 2:
        return float("nan")
    xv = xx[m]
    yv = yy[m]
    if np.isclose(float(np.nanstd(xv)), 0.0) or np.isclose(float(np.nanstd(yv)), 0.0):
        return float("nan")
    return float(xv.corr(yv, method="spearman"))


def build_collocation_summary(
    collocated: pd.DataFrame,
    meta: dict[str, object],
    obs_utc: pd.Timestamp,
    obs_local: pd.Timestamp,
) -> pd.DataFrame:
    if collocated.empty:
        row = {
            "obs_utc": str(obs_utc),
            "obs_local": str(obs_local),
            "station_matched_local_time": meta.get("matched_local_time"),
            "n_sites_total": meta.get("n_sites_total"),
            "n_sites_valid_pm10": meta.get("n_sites_valid_pm10"),
            "n_sites_in_extent": meta.get("n_sites_in_extent"),
            "n_sites_collocated": 0,
            "n_sites_clear": 0,
            "n_sites_dust_candidate": 0,
            "spearman_pm10_vs_btd15_13": np.nan,
            "spearman_pm10_vs_dli": np.nan,
            "pm10_p90_threshold": np.nan,
            "high_pm10_p90_hit_rate": np.nan,
            "station_var": meta.get("station_var"),
            "station_nc_path": meta.get("station_nc_path"),
            "station_time_exact_match": meta.get("matched_exact"),
        }
        return pd.DataFrame([row])

    pm10 = pd.to_numeric(collocated["pm10"], errors="coerce")
    spearman_btd = _safe_spearman(pm10, collocated["sat_btd15_13_k"])
    spearman_dli = _safe_spearman(pm10, collocated["sat_dli"])

    p90 = float(pm10.quantile(0.9))
    high = pm10 >= p90
    if bool(high.any()):
        hit = float(collocated.loc[high, "sat_dust_candidate_flag"].mean())
    else:
        hit = float("nan")

    row = {
        "obs_utc": str(obs_utc),
        "obs_local": str(obs_local),
        "station_matched_local_time": meta.get("matched_local_time"),
        "n_sites_total": int(meta.get("n_sites_total", 0)),
        "n_sites_valid_pm10": int(meta.get("n_sites_valid_pm10", 0)),
        "n_sites_in_extent": int(meta.get("n_sites_in_extent", 0)),
        "n_sites_collocated": int(len(collocated)),
        "n_sites_clear": int((collocated["sat_cloud_flag"] == 0).sum()),
        "n_sites_dust_candidate": int((collocated["sat_dust_candidate_flag"] == 1).sum()),
        "spearman_pm10_vs_btd15_13": spearman_btd,
        "spearman_pm10_vs_dli": spearman_dli,
        "pm10_p90_threshold": p90,
        "high_pm10_p90_hit_rate": hit,
        "station_var": meta.get("station_var"),
        "station_nc_path": meta.get("station_nc_path"),
        "station_time_exact_match": bool(meta.get("matched_exact", False)),
    }
    return pd.DataFrame([row])


def plot_station_overlay(
    lon: np.ndarray,
    lat: np.ndarray,
    products: dict[str, np.ndarray],
    stations: pd.DataFrame,
    extent: tuple[float, float, float, float],
    out_path: Path,
    obs_utc: pd.Timestamp,
    obs_local: pd.Timestamp,
) -> None:
    fig, ax = create_hlcn_canvas(extent)
    sat_m = extent_mask(lon, lat, extent) & products["valid_mask"]
    if sat_m.any():
        ax.scatter(
            lon[sat_m],
            lat[sat_m],
            c=products["rgb"][sat_m],
            s=0.75,
            linewidths=0,
            rasterized=True,
            transform=ccrs.PlateCarree(),
            zorder=5,
            alpha=0.82,
        )

    if not stations.empty:
        sc = ax.scatter(
            stations["lon"].to_numpy(),
            stations["lat"].to_numpy(),
            c=stations["pm10"].to_numpy(),
            s=22,
            cmap="turbo",
            linewidths=0.25,
            edgecolors="#111111",
            transform=ccrs.PlateCarree(),
            zorder=9,
        )
        cbar = fig.colorbar(sc, ax=ax, shrink=0.86, pad=0.02)
        cbar.set_label("Station PM10")

    ax.set_title(
        "Dust RGB + Station PM10 Snapshot\n"
        f"obs_utc={obs_utc}, obs_local(UTC+8)={obs_local}"
    )
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)


def print_bt_quick_stats(name: str, arr: np.ndarray, valid_mask: np.ndarray) -> None:
    vals = arr[valid_mask & np.isfinite(arr)]
    if vals.size == 0:
        print(f"[WARN] {name}: no valid values")
        return
    p2, p50, p98 = np.percentile(vals, [2, 50, 98])
    print(f"[INFO] {name} p2/p50/p98 = {p2:.3f}/{p50:.3f}/{p98:.3f}")

def main() -> None:
    args = parse_args()
    configure_mapbase_paths()

    dat_root = args.dat_root.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    step = max(1, int(args.step))
    extent = tuple(float(x) for x in args.extent)
    obs_utc = infer_obs_time_utc(
        time_tag=args.time_tag,
        time_tag_tz=args.obs_time_tag_tz,
        local_offset_hours=args.local_offset_hours,
    )
    obs_local = obs_utc + pd.Timedelta(hours=args.local_offset_hours)

    print(
        f"[INFO] time_tag={args.time_tag} interpreted_as={args.obs_time_tag_tz.upper()} "
        f"-> obs_utc={obs_utc} obs_local(UTC+{args.local_offset_hours})={obs_local}"
    )

    band_full: dict[str, np.ndarray] = {}
    band_cal: dict[str, CalibrationParams] = {}
    projection: ProjectionParams | None = None

    for band in DEFAULT_BANDS:
        paths = build_band_paths(dat_root=dat_root, time_tag=args.time_tag, band=band)
        full, proj, cal = assemble_band(band=band, paths=paths)
        band_full[band] = full
        band_cal[band] = cal
        projection = proj
        print(
            f"[OK] {band}: shape={full.shape}, files={len(paths)}, "
            f"wl={cal.central_wavelength_um:.4f}um gain={cal.gain_count2rad:.6f} offset={cal.offset_count2rad:.6f}"
        )

    if projection is None:
        raise RuntimeError("Projection parameters were not loaded.")

    n_rows, n_cols = band_full["B13"].shape
    lon, lat = geos_to_lonlat(
        n_rows=n_rows,
        n_cols=n_cols,
        projection=projection,
        step=step,
    )

    dn11 = band_full["B11"][::step, ::step]
    dn13 = band_full["B13"][::step, ::step]
    dn15 = band_full["B15"][::step, ::step]
    bt11, _ = dn_to_bt(dn11, band_cal["B11"])
    bt13, _ = dn_to_bt(dn13, band_cal["B13"])
    bt15, _ = dn_to_bt(dn15, band_cal["B15"])

    plot_bt_band(
        lon=lon,
        lat=lat,
        bt=bt11,
        extent=extent,
        out_path=out_dir / "hima_b11_bt_map.png",
        title="Himawari-8 B11 Brightness Temperature (8.6 um)",
        cmap="magma",
    )
    print("[OK] hima_b11_bt_map.png")

    plot_bt_band(
        lon=lon,
        lat=lat,
        bt=bt13,
        extent=extent,
        out_path=out_dir / "hima_b13_bt_map.png",
        title="Himawari-8 B13 Brightness Temperature (10.4 um)",
        cmap="inferno",
    )
    print("[OK] hima_b13_bt_map.png")

    plot_bt_band(
        lon=lon,
        lat=lat,
        bt=bt15,
        extent=extent,
        out_path=out_dir / "hima_b15_bt_map.png",
        title="Himawari-8 B15 Brightness Temperature (12.4 um)",
        cmap="plasma",
    )
    print("[OK] hima_b15_bt_map.png")

    products = build_dust_products(
        bt11=bt11,
        bt13=bt13,
        bt15=bt15,
        cloud_mask_mode=args.cloud_mask,
    )
    ext_valid = extent_mask(lon, lat, extent)
    print_bt_quick_stats("BT11", bt11, ext_valid)
    print_bt_quick_stats("BT13", bt13, ext_valid)
    print_bt_quick_stats("BT15", bt15, ext_valid)

    plot_dust_rgb_fixed(
        lon=lon,
        lat=lat,
        products=products,
        extent=extent,
        out_path=out_dir / "hima_dust_rgb_paper_fixed_map.png",
        obs_utc=obs_utc,
    )
    print("[OK] hima_dust_rgb_paper_fixed_map.png")

    plot_binary_mask(
        lon=lon,
        lat=lat,
        base_valid=products["valid_mask"],
        target_mask=products["cloud_mask"],
        extent=extent,
        out_path=out_dir / "hima_cloud_mask_conservative_map.png",
        title=f"Cloud Mask ({args.cloud_mask})",
        ticklabels=("clear", "cloud"),
        colors=("#0b132b", "#9ca3af"),
    )
    print("[OK] hima_cloud_mask_conservative_map.png")

    plot_binary_mask(
        lon=lon,
        lat=lat,
        base_valid=products["valid_mask"],
        target_mask=products["dust_candidate"],
        extent=extent,
        out_path=out_dir / "hima_dust_candidate_mask_map.png",
        title="Dust Candidate Mask",
        ticklabels=("background", "dust-candidate"),
        colors=("#0b132b", "#f97316"),
    )
    print("[OK] hima_dust_candidate_mask_map.png")

    station_nc_path = args.station_nc_path.resolve()
    station_df, station_meta = load_station_snapshot(
        nc_path=station_nc_path,
        station_var=args.station_var,
        target_local_time=obs_local,
        extent=extent,
    )
    print(
        "[INFO] station snapshot: "
        f"total={station_meta['n_sites_total']} valid_pm10={station_meta['n_sites_valid_pm10']} "
        f"in_extent={station_meta['n_sites_in_extent']} matched_local={station_meta['matched_local_time']} "
        f"exact={station_meta['matched_exact']}"
    )

    collocated = collocate_stations(
        station_df=station_df,
        lon=lon,
        lat=lat,
        bt11=bt11,
        bt13=bt13,
        bt15=bt15,
        products=products,
    )
    collocation_csv = out_dir / "hima_station_collocation_snapshot.csv"
    collocated.to_csv(collocation_csv, index=False, encoding="utf-8-sig")
    print("[OK] hima_station_collocation_snapshot.csv")

    summary_df = build_collocation_summary(
        collocated=collocated,
        meta=station_meta,
        obs_utc=obs_utc,
        obs_local=obs_local,
    )
    summary_csv = out_dir / "hima_station_collocation_summary.csv"
    summary_df.to_csv(summary_csv, index=False, encoding="utf-8-sig")
    print("[OK] hima_station_collocation_summary.csv")

    plot_station_overlay(
        lon=lon,
        lat=lat,
        products=products,
        stations=collocated,
        extent=extent,
        out_path=out_dir / "hima_station_pm10_overlay_snapshot.png",
        obs_utc=obs_utc,
        obs_local=obs_local,
    )
    print("[OK] hima_station_pm10_overlay_snapshot.png")

    print(f"Done. Outputs in: {out_dir}")


if __name__ == "__main__":
    main()
