from __future__ import annotations

import math
import sys
from pathlib import Path

import cartopy.crs as ccrs
import matplotlib.colors as mcolors
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xarray as xr


# -----------------------------
# Config
# -----------------------------
EVENT_ID = 16
UTC_FALLBACK_START = "2021-06-05 23:30:00"
UTC_FALLBACK_END = "2021-06-07 02:30:00"
LOCAL_TZ_OFFSET_HOURS = 8

SUMMARY_CSV = Path(__file__).resolve().parent / "out_dust_events" / "dust_events_summary.csv"
NC_DIR_CANDIDATES = [
    Path(__file__).resolve().parents[2] / "downloads_merra2_subset",
    Path(__file__).resolve().parent / "downloads_merra2_subset",
]
OUT_DIR = Path(__file__).resolve().parent / "out_dust_events" / "event16_spatial_maps"

PLOT_VARS = ["DUSMASS", "DUCMASS", "DUEXTTAU", "DUSCATAU", "DUFLUX_MAG"]
BASE_VARS_REQUIRED = ["DUSMASS", "DUCMASS", "DUEXTTAU", "DUSCATAU", "DUFLUXU", "DUFLUXV"]
PEAK_TIME_VAR = "DUSMASS"
PEAK_TIME_CMAP = "YlGnBu"  # Early = lighter, late = darker.

LANZHOU_LON = 103.8343
LANZHOU_LAT = 36.0611
LANZHOU_BOX = (103.0, 35.5, 104.5, 36.8)  # (W, S, E, N), consistent with fetch2.py
SHOW_LANZHOU_MARKER = True
SHOW_LANZHOU_BOX = True


def _setup_mapbase_import():
    mapbase_dir = Path(__file__).resolve().parents[1] / "mapbase"
    if str(mapbase_dir) not in sys.path:
        sys.path.insert(0, str(mapbase_dir))
    import world_adm0_china_region_map as wadm

    # Force absolute paths for map resources to avoid CWD-dependent failures.
    wadm.DEFAULT_WORLD_ADM0_SHP = str((mapbase_dir / "geoBoundariesCGAZ_ADM0" / "geoBoundariesCGAZ_ADM0.shp").resolve())
    wadm.DEFAULT_WORLD_ADM1_SHP = str((mapbase_dir / "geoBoundariesCGAZ_ADM1" / "geoBoundariesCGAZ_ADM1.shp").resolve())
    wadm.DEFAULT_CHINA_ADM0_SIMPLIFIED_SHP = str(
        (mapbase_dir / "geoBoundaries-CHN-ADM0-all" / "geoBoundaries-CHN-ADM0_simplified.shp").resolve()
    )

    return wadm.draw_world_adm0_china_highlight_canvas


def _pick_nc_dir() -> Path:
    for nc_dir in NC_DIR_CANDIDATES:
        if nc_dir.exists():
            return nc_dir
    raise FileNotFoundError(f"No nc directory found among: {NC_DIR_CANDIDATES}")


def _get_event_window_utc(summary_csv: Path, event_id: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    if summary_csv.exists():
        df = pd.read_csv(summary_csv)
        if "event_id" in df.columns and "start_utc" in df.columns and "end_utc" in df.columns:
            row = df.loc[df["event_id"] == event_id]
            if len(row) == 1:
                start_utc = pd.to_datetime(row.iloc[0]["start_utc"])
                end_utc = pd.to_datetime(row.iloc[0]["end_utc"])
                if pd.notna(start_utc) and pd.notna(end_utc):
                    return start_utc, end_utc

    # Fallback to user-specified window.
    return pd.to_datetime(UTC_FALLBACK_START), pd.to_datetime(UTC_FALLBACK_END)


def _collect_event_files(nc_dir: Path, start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> list[Path]:
    files = []
    days = pd.date_range(start=start_utc.floor("D"), end=end_utc.floor("D"), freq="D")
    for day in days:
        ymd = day.strftime("%Y%m%d")
        matched = sorted(nc_dir.glob(f"MERRA2_4*.tavg1_2d_aer_Nx.{ymd}.SUB.nc"))
        files.extend(matched)
    uniq = sorted({p.resolve() for p in files})
    return [Path(p) for p in uniq]


def _load_event_dataset(nc_files: list[Path], start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> xr.Dataset:
    if not nc_files:
        raise FileNotFoundError("No .SUB.nc files found for event time window.")

    datasets = []
    for fp in nc_files:
        ds = xr.open_dataset(fp, engine="netcdf4")
        keep = [v for v in BASE_VARS_REQUIRED if v in ds.data_vars]
        if not keep:
            ds.close()
            continue
        datasets.append(ds[keep])

    if not datasets:
        raise RuntimeError("No required variables found in selected event files.")

    ds_all = xr.concat(datasets, dim="time").sortby("time")
    for ds in datasets:
        ds.close()

    ds_evt = ds_all.sel(time=slice(start_utc, end_utc))
    if ds_evt.sizes.get("time", 0) == 0:
        raise RuntimeError("Selected event window has zero timesteps after slicing.")

    # Derived variable: vector magnitude of dust flux.
    if "DUFLUXU" in ds_evt and "DUFLUXV" in ds_evt:
        ds_evt["DUFLUX_MAG"] = np.hypot(ds_evt["DUFLUXU"], ds_evt["DUFLUXV"])
        ds_evt["DUFLUX_MAG"].attrs["long_name"] = "Dust column horizontal mass flux magnitude"
        ds_evt["DUFLUX_MAG"].attrs["units"] = ds_evt["DUFLUXU"].attrs.get("units", "")

    return ds_evt


def _infer_extent(ds: xr.Dataset) -> tuple[float, float, float, float]:
    lon_min = float(ds["lon"].min())
    lon_max = float(ds["lon"].max())
    lat_min = float(ds["lat"].min())
    lat_max = float(ds["lat"].max())
    # Use exact nc subset bounds (no expansion).
    return (lon_min, lon_max, lat_min, lat_max)


def _panel_norm(values: np.ndarray, metric: str) -> mcolors.Normalize:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return mcolors.Normalize(vmin=0.0, vmax=1.0)
    if metric == "mean":
        lo, hi = np.nanpercentile(finite, [2, 98])
    else:
        lo, hi = np.nanpercentile(finite, [5, 99])
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        lo = float(np.nanmin(finite))
        hi = float(np.nanmax(finite))
        if hi <= lo:
            hi = lo + 1e-12
    return mcolors.Normalize(vmin=lo, vmax=hi)


def _var_meta(ds: xr.Dataset, var: str) -> tuple[str, str, str]:
    cmap = {
        "DUSMASS": "YlOrRd",
        "DUCMASS": "YlOrRd",
        "DUEXTTAU": "magma",
        "DUSCATAU": "magma",
        "DUFLUX_MAG": "viridis",
    }.get(var, "YlOrRd")

    if var in ds:
        long_name = ds[var].attrs.get("long_name", var)
        unit = ds[var].attrs.get("units", "")
    else:
        long_name = var
        unit = ""
    return long_name, unit, cmap


def _overlay_lanzhou(ax):
    if SHOW_LANZHOU_MARKER:
        ax.scatter(
            [LANZHOU_LON],
            [LANZHOU_LAT],
            transform=ccrs.PlateCarree(),
            s=24,
            marker="o",
            facecolor="none",
            edgecolor="black",
            linewidth=1.0,
            zorder=6,
        )
    if SHOW_LANZHOU_BOX:
        w, s, e, n = LANZHOU_BOX
        rect = mpatches.Rectangle(
            (w, s),
            e - w,
            n - s,
            linewidth=1.2,
            edgecolor="#1f77b4",
            facecolor="none",
            linestyle="-",
            transform=ccrs.PlateCarree(),
            zorder=6,
        )
        ax.add_patch(rect)


def _peak_time_hours(ds_evt: xr.Dataset, var: str) -> xr.DataArray:
    peak_idx = ds_evt[var].argmax(dim="time", skipna=True)
    peak_t = ds_evt["time"].isel(time=peak_idx)
    valid = ds_evt[var].count(dim="time") > 0
    start_t = ds_evt["time"].min()
    peak_hours = ((peak_t - start_t) / np.timedelta64(1, "h")).where(valid)
    peak_hours.attrs["units"] = "hour since event start (UTC)"
    return peak_hours


def _plot_metric_panels(
    ds_evt: xr.Dataset,
    plot_vars: list[str],
    metric: str,
    extent: tuple[float, float, float, float],
    out_png: Path,
    event_id: int,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
):
    draw_world_adm0_china_highlight_canvas = _setup_mapbase_import()

    valid_vars = [v for v in plot_vars if v in ds_evt.data_vars]
    if not valid_vars:
        raise RuntimeError("No plotting variables available in event dataset.")

    add_peak_panel = metric == "mean" and PEAK_TIME_VAR in valid_vars
    n = len(valid_vars) + (1 if add_peak_panel else 0)
    ncols = 3
    nrows = int(math.ceil(n / ncols))
    fig, axes = plt.subplots(
        nrows=nrows,
        ncols=ncols,
        figsize=(5.6 * ncols, 4.6 * nrows),
        subplot_kw={"projection": ccrs.PlateCarree()},
        dpi=320,
        constrained_layout=True,
    )
    axes = np.atleast_1d(axes).ravel()

    for i, var in enumerate(valid_vars):
        ax = axes[i]
        draw_world_adm0_china_highlight_canvas(
            ax=ax,
            extent=extent,
            draw_grid=True,
            show_country_labels=False,
            processing_extent=extent,
            neighbor_linewidth=0.36,
            china_linewidth=1.1,
            china_edgecolor="#5a5a5a",
            china_alpha=0.72,
            omit_shared_with_china=True,
        )

        field = ds_evt[var].mean(dim="time", skipna=True) if metric == "mean" else ds_evt[var].max(dim="time", skipna=True)
        lon = ds_evt["lon"].values
        lat = ds_evt["lat"].values
        vals = field.values
        norm = _panel_norm(vals, metric)
        long_name, unit, cmap = _var_meta(ds_evt, var)

        mesh = ax.pcolormesh(
            lon,
            lat,
            vals,
            transform=ccrs.PlateCarree(),
            cmap=cmap,
            shading="auto",
            norm=norm,
            zorder=1,
        )

        _overlay_lanzhou(ax)

        cbar = fig.colorbar(mesh, ax=ax, shrink=0.84, pad=0.02)
        cbar.set_label(unit if unit else var)
        ax.set_title(f"{long_name}\n{metric.upper()} over event window", fontsize=10)

    if add_peak_panel:
        ax = axes[len(valid_vars)]
        draw_world_adm0_china_highlight_canvas(
            ax=ax,
            extent=extent,
            draw_grid=True,
            show_country_labels=False,
            processing_extent=extent,
            neighbor_linewidth=0.36,
            china_linewidth=1.1,
            china_edgecolor="#5a5a5a",
            china_alpha=0.72,
            omit_shared_with_china=True,
        )

        peak_hours = _peak_time_hours(ds_evt, PEAK_TIME_VAR)
        vals = peak_hours.values
        max_hours = float(((ds_evt["time"].max() - ds_evt["time"].min()) / np.timedelta64(1, "h")).item())
        if not np.isfinite(max_hours) or max_hours <= 0:
            max_hours = 1.0
        norm = mcolors.Normalize(vmin=0.0, vmax=max_hours)
        mesh = ax.pcolormesh(
            ds_evt["lon"].values,
            ds_evt["lat"].values,
            vals,
            transform=ccrs.PlateCarree(),
            cmap=PEAK_TIME_CMAP,
            shading="auto",
            norm=norm,
            zorder=1,
        )
        _overlay_lanzhou(ax)
        cbar = fig.colorbar(mesh, ax=ax, shrink=0.84, pad=0.02)
        start_local = start_utc + pd.Timedelta(hours=LOCAL_TZ_OFFSET_HOURS)
        ticks = np.linspace(0.0, max_hours, 6)
        cbar.set_ticks(ticks)
        cbar.set_ticklabels([(start_local + pd.Timedelta(hours=float(t))).strftime("%m-%d %H:%M") for t in ticks])
        cbar.set_label("Local time of peak (UTC+8)")
        ax.set_title(f"Peak-Time Map ({PEAK_TIME_VAR})\nTime of maximum over event window", fontsize=10)

    used_panels = len(valid_vars) + (1 if add_peak_panel else 0)
    for j in range(used_panels, len(axes)):
        axes[j].set_visible(False)

    start_local = start_utc + pd.Timedelta(hours=LOCAL_TZ_OFFSET_HOURS)
    end_local = end_utc + pd.Timedelta(hours=LOCAL_TZ_OFFSET_HOURS)
    figure_title = f"MERRA-2 Event {event_id} Spatial Heatmaps ({metric.upper()})"
    if add_peak_panel:
        figure_title = f"MERRA-2 Event {event_id} Spatial Heatmaps (MEAN + TIME-OF-PEAK)"
    fig.suptitle(
        f"{figure_title}\nUTC: {start_utc} to {end_utc} | Local(UTC+8): {start_local} to {end_local}",
        fontsize=13,
        y=1.02,
    )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=350, bbox_inches="tight")
    plt.close(fig)


def _plot_focus_duflux_peak_1x2(
    ds_evt: xr.Dataset,
    extent: tuple[float, float, float, float],
    out_png: Path,
    event_id: int,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
):
    draw_world_adm0_china_highlight_canvas = _setup_mapbase_import()
    mean_var = "DUFLUX_MAG"
    peak_var = PEAK_TIME_VAR
    if mean_var not in ds_evt.data_vars:
        raise RuntimeError(f"Required variable not found for 1x2 output: {mean_var}")
    if peak_var not in ds_evt.data_vars:
        raise RuntimeError(f"Required variable not found for 1x2 output: {peak_var}")

    fig, axes = plt.subplots(
        nrows=1,
        ncols=2,
        figsize=(12.6, 5.8),
        subplot_kw={"projection": ccrs.PlateCarree()},
        dpi=320,
        constrained_layout=True,
    )

    for ax in axes:
        draw_world_adm0_china_highlight_canvas(
            ax=ax,
            extent=extent,
            draw_grid=True,
            show_country_labels=False,
            processing_extent=extent,
            neighbor_linewidth=0.36,
            china_linewidth=1.1,
            china_edgecolor="#5a5a5a",
            china_alpha=0.72,
            omit_shared_with_china=True,
        )

    # Left panel: mean DUFLUX_MAG
    ax = axes[0]
    mean_field = ds_evt[mean_var].mean(dim="time", skipna=True)
    mean_vals = mean_field.values
    mean_norm = _panel_norm(mean_vals, "mean")
    long_name, unit, cmap = _var_meta(ds_evt, mean_var)
    mesh = ax.pcolormesh(
        ds_evt["lon"].values,
        ds_evt["lat"].values,
        mean_vals,
        transform=ccrs.PlateCarree(),
        cmap=cmap,
        shading="auto",
        norm=mean_norm,
        zorder=1,
    )
    _overlay_lanzhou(ax)
    cbar = fig.colorbar(mesh, ax=ax, shrink=0.88, pad=0.02)
    cbar.set_label(unit if unit else mean_var)
    ax.set_title(f"{long_name}\nMEAN over event window", fontsize=11)

    # Right panel: peak time map of DUSMASS
    ax = axes[1]
    peak_hours = _peak_time_hours(ds_evt, peak_var)
    peak_vals = peak_hours.values
    max_hours = float(((ds_evt["time"].max() - ds_evt["time"].min()) / np.timedelta64(1, "h")).item())
    if not np.isfinite(max_hours) or max_hours <= 0:
        max_hours = 1.0
    peak_norm = mcolors.Normalize(vmin=0.0, vmax=max_hours)
    mesh = ax.pcolormesh(
        ds_evt["lon"].values,
        ds_evt["lat"].values,
        peak_vals,
        transform=ccrs.PlateCarree(),
        cmap=PEAK_TIME_CMAP,
        shading="auto",
        norm=peak_norm,
        zorder=1,
    )
    _overlay_lanzhou(ax)
    cbar = fig.colorbar(mesh, ax=ax, shrink=0.88, pad=0.02)
    start_local = start_utc + pd.Timedelta(hours=LOCAL_TZ_OFFSET_HOURS)
    ticks = np.linspace(0.0, max_hours, 6)
    cbar.set_ticks(ticks)
    cbar.set_ticklabels([(start_local + pd.Timedelta(hours=float(t))).strftime("%m-%d %H:%M") for t in ticks])
    cbar.set_label("Local time of peak (UTC+8)")
    ax.set_title(f"Peak-Time Map ({peak_var})\nTime of maximum over event window", fontsize=11)

    start_local = start_utc + pd.Timedelta(hours=LOCAL_TZ_OFFSET_HOURS)
    end_local = end_utc + pd.Timedelta(hours=LOCAL_TZ_OFFSET_HOURS)
    fig.suptitle(
        (
            f"MERRA-2 Event {event_id} Spatial Focus Maps "
            f"(1x2: {mean_var} MEAN + {peak_var} PEAK-TIME)\n"
            f"UTC: {start_utc} to {end_utc} | Local(UTC+8): {start_local} to {end_local}"
        ),
        fontsize=13,
        y=1.02,
    )

    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=350, bbox_inches="tight")
    plt.close(fig)


def main():
    plt.rcParams.update(
        {
            "font.sans-serif": ["Times New Roman", "SimHei", "Microsoft YaHei", "Arial Unicode MS"],
            "axes.unicode_minus": False,
            "mathtext.default": "regular",
            "mathtext.fontset": "stix",
        }
    )

    start_utc, end_utc = _get_event_window_utc(SUMMARY_CSV, EVENT_ID)
    nc_dir = _pick_nc_dir()
    event_files = _collect_event_files(nc_dir, start_utc, end_utc)
    ds_evt = _load_event_dataset(event_files, start_utc, end_utc)
    extent = _infer_extent(ds_evt)

    out_focus = OUT_DIR / f"event{EVENT_ID}_spatial_mean_heatmaps.png"
    # Legacy full-panel outputs are retained via _plot_metric_panels(), but are not called for now:
    # out_max = OUT_DIR / f"event{EVENT_ID}_spatial_max_heatmaps.png"
    # _plot_metric_panels(ds_evt, PLOT_VARS, "mean", extent, out_focus, EVENT_ID, start_utc, end_utc)
    # _plot_metric_panels(ds_evt, PLOT_VARS, "max", extent, out_max, EVENT_ID, start_utc, end_utc)
    _plot_focus_duflux_peak_1x2(ds_evt, extent, out_focus, EVENT_ID, start_utc, end_utc)

    print("\nDONE")
    print(f"Event ID         : {EVENT_ID}")
    print(f"UTC window       : {start_utc} -> {end_utc}")
    print(f"Local window+8   : {start_utc + pd.Timedelta(hours=8)} -> {end_utc + pd.Timedelta(hours=8)}")
    print(f"NC directory     : {nc_dir}")
    print(f"Files used       : {len(event_files)}")
    for f in event_files:
        print(f"  - {f.name}")
    print(f"Output focus map : {out_focus.resolve()}")


if __name__ == "__main__":
    main()
