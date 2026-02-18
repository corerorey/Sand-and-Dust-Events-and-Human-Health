# %% 
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xarray as xr


NC_PATH = r"C:\DOCUMENTO\nc_out\documento_all_sites_20210101_20211231.nc"
SITE_ID = "1477A"
START_TIME = "2021-03-10 00:00:00"
END_TIME = "2021-03-25 23:00:00"
OUTPUT_PNG = "1477A_20210310_20210325_valid_variables_longline.png"


def _as_str(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)


def _load_time_slice(ds, start_time, end_time):
    try:
        return ds.sel(time=slice(pd.Timestamp(start_time), pd.Timestamp(end_time)))
    except Exception:
        if "datetime_str" not in ds:
            raise
        dt = pd.to_datetime(ds["datetime_str"].values, errors="coerce")
        mask = (dt >= pd.Timestamp(start_time)) & (dt <= pd.Timestamp(end_time))
        idx = np.where(mask)[0]
        if len(idx) == 0:
            return ds.isel(time=slice(0, 0))
        return ds.isel(time=slice(int(idx.min()), int(idx.max()) + 1))


def main():
    ds = xr.open_dataset(NC_PATH)

    site_numbers = [_as_str(v).strip() for v in ds["site_number"].values]
    if SITE_ID not in site_numbers:
        raise ValueError(f"Site {SITE_ID} not found in dataset site_number.")
    site_idx = site_numbers.index(SITE_ID)

    ds_sel = _load_time_slice(ds, START_TIME, END_TIME).isel(site=site_idx)
    if ds_sel.sizes.get("time", 0) == 0:
        raise ValueError(f"No rows in selected time range: {START_TIME} to {END_TIME}")

    candidate_vars = [
        name
        for name, da in ds_sel.data_vars.items()
        if da.dims == ("time",)
    ]

    valid_vars = []
    for name in candidate_vars:
        arr = ds_sel[name].values
        if np.issubdtype(np.asarray(arr).dtype, np.number) and np.isfinite(arr).any():
            valid_vars.append(name)

    if not valid_vars:
        raise ValueError(f"No valid numeric variables for {SITE_ID} in selected range.")

    time_values = pd.to_datetime(ds_sel["time"].values)
    fig_h = max(1.7 * len(valid_vars), 6)
    fig, axes = plt.subplots(len(valid_vars), 1, figsize=(24, fig_h), sharex=True)
    if len(valid_vars) == 1:
        axes = [axes]

    for ax, var_name in zip(axes, valid_vars):
        values = ds_sel[var_name].values
        ax.plot(time_values, values, linewidth=1.0, color="#1f77b4")
        ax.set_ylabel(var_name, rotation=0, ha="right", va="center")
        ax.grid(alpha=0.25, linestyle="--", linewidth=0.6)

    title = (
        f"{SITE_ID} valid variables | {START_TIME} to {END_TIME} | "
        f"count={len(valid_vars)}"
    )
    axes[0].set_title(title)
    axes[-1].set_xlabel("Datetime")
    plt.tight_layout()
    plt.savefig(OUTPUT_PNG, dpi=180)
    plt.show()
    print(f"Saved plot: {os.path.abspath(OUTPUT_PNG)}")


# if __name__ == "__main__":
#     main()
# %%

# %%
import sys
from pathlib import Path

import cartopy.crs as ccrs
import matplotlib.colors as mcolors


NC_MAP_PATH = r"C:\DOCUMENTO\nc_out\documento_all_sites_20210101_20211231.nc"
MAP_TIME = pd.Timestamp("2021-03-16 05:00:00")
MAP_OUTPUT_PNG = "china_sites_pm10_pm25_20210316_0500.png"
MAPBASE_DIR = Path(r"C:\DOCUMENTO\Sand-and-Dust-Storms-and-Human-Health\data_prep\mapbase")


def _setup_mapbase_drawer(mapbase_dir: Path):
    if str(mapbase_dir) not in sys.path:
        sys.path.insert(0, str(mapbase_dir))
    import world_adm0_china_region_map as wadm

    wadm.DEFAULT_WORLD_ADM0_SHP = str((mapbase_dir / "geoBoundariesCGAZ_ADM0" / "geoBoundariesCGAZ_ADM0.shp").resolve())
    wadm.DEFAULT_WORLD_ADM1_SHP = str((mapbase_dir / "geoBoundariesCGAZ_ADM1" / "geoBoundariesCGAZ_ADM1.shp").resolve())
    wadm.DEFAULT_CHINA_ADM0_SIMPLIFIED_SHP = str(
        (mapbase_dir / "geoBoundaries-CHN-ADM0-all" / "geoBoundaries-CHN-ADM0_simplified.shp").resolve()
    )
    return wadm.draw_world_adm0_china_highlight_canvas


def _get_var_name(ds, source_name):
    for name, da in ds.data_vars.items():
        if da.attrs.get("source_type_name") == source_name:
            return name
    candidates = [source_name, source_name.replace(".", "_"), source_name.replace(".", "")]
    for c in candidates:
        if c in ds.data_vars:
            return c
    return None


def _minmax_norm(arr):
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return mcolors.Normalize(vmin=0.0, vmax=1.0), 0.0, 1.0
    vmin = float(np.nanmin(finite))
    vmax = float(np.nanmax(finite))
    if vmax <= vmin:
        vmax = vmin + 1e-12
    return mcolors.Normalize(vmin=vmin, vmax=vmax), vmin, vmax


def _draw_site_map(ax, draw_bg, extent, lon, lat, val, title, cmap, norm):
    draw_bg(
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
    order = np.argsort(val)
    lon = lon[order]
    lat = lat[order]
    val = val[order]

    sc = ax.scatter(
        lon,
        lat,
        c=val,
        s=26,
        cmap=cmap,
        norm=norm,
        transform=ccrs.PlateCarree(),
        zorder=6,
    )
    ax.set_title(title)
    return sc


def _print_max_site_info(ds_t, var_name, values):
    finite_mask = np.isfinite(values)
    if not finite_mask.any():
        print(f"[{var_name}] no finite values at selected time.")
        return
    idx = int(np.nanargmax(values))
    max_val = float(values[idx])
    site_number = _as_str(ds_t["site_number"].values[idx]).strip() if "site_number" in ds_t else ""
    site_name_zh = _as_str(ds_t["site_name_zh"].values[idx]).strip() if "site_name_zh" in ds_t else ""
    city_zh = _as_str(ds_t["city_zh"].values[idx]).strip() if "city_zh" in ds_t else ""
    lon = float(ds_t["lon"].values[idx]) if "lon" in ds_t else float("nan")
    lat = float(ds_t["lat"].values[idx]) if "lat" in ds_t else float("nan")
    print(
        f"[{var_name}] max={max_val} | site={site_number} | name={site_name_zh} | "
        f"city={city_zh} | lon={lon}, lat={lat}"
    )


def _unit_text(unit):
    u = _as_str(unit).strip() if unit is not None else ""
    return f" ({u})" if u else ""


def _resolve_unit_text(source_name, unit_from_nc):
    nc_unit = _as_str(unit_from_nc).strip() if unit_from_nc is not None else ""
    if nc_unit:
        return nc_unit
    fallback_units = {
        "PM10": "µg/m³",
        "PM2.5": "µg/m³",
    }
    return fallback_units.get(source_name, "")


def plot_pm10_pm25_sites_at_hour():
    ds = xr.open_dataset(NC_MAP_PATH)
    draw_bg = _setup_mapbase_drawer(MAPBASE_DIR)

    pm10_var = _get_var_name(ds, "PM10")
    pm25_var = _get_var_name(ds, "PM2.5")
    if pm10_var is None or pm25_var is None:
        raise ValueError(f"Cannot find PM10/PM2.5 variables in dataset. pm10={pm10_var}, pm25={pm25_var}")

    try:
        ds_t = ds.sel(time=MAP_TIME)
    except Exception:
        ds_t = ds.sel(time=MAP_TIME, method="nearest")
        matched_time = pd.to_datetime(ds_t["time"].values)
        print(f"Exact time not found, using nearest: {matched_time}")

    lon = np.asarray(ds_t["lon"].values, dtype=float)
    lat = np.asarray(ds_t["lat"].values, dtype=float)
    pm10 = np.asarray(ds_t[pm10_var].values, dtype=float)
    pm25 = np.asarray(ds_t[pm25_var].values, dtype=float)
    pm10_unit = _resolve_unit_text("PM10", ds[pm10_var].attrs.get("units", ""))
    pm25_unit = _resolve_unit_text("PM2.5", ds[pm25_var].attrs.get("units", ""))

    _print_max_site_info(ds_t, "PM10", pm10)
    _print_max_site_info(ds_t, "PM2.5", pm25)

    valid_pm10 = np.isfinite(lon) & np.isfinite(lat) & np.isfinite(pm10)
    valid_pm25 = np.isfinite(lon) & np.isfinite(lat) & np.isfinite(pm25)
    if not valid_pm10.any() and not valid_pm25.any():
        raise ValueError("No valid site values for PM10/PM2.5 at selected time.")

    lon_all = np.concatenate([lon[valid_pm10], lon[valid_pm25]]) if valid_pm10.any() or valid_pm25.any() else lon
    lat_all = np.concatenate([lat[valid_pm10], lat[valid_pm25]]) if valid_pm10.any() or valid_pm25.any() else lat
    extent = (
        float(np.nanmin(lon_all) - 2.5),
        float(np.nanmax(lon_all) + 2.5),
        float(np.nanmin(lat_all) - 2.5),
        float(np.nanmax(lat_all) + 2.5),
    )

    fig, axes = plt.subplots(
        nrows=1,
        ncols=2,
        figsize=(16, 6),
        subplot_kw={"projection": ccrs.PlateCarree()},
        dpi=220,
    )

    pm10_threshold = 6000.0
    pm10_low_mask = valid_pm10 & (pm10 <= pm10_threshold)
    pm10_high_mask = valid_pm10 & (pm10 > pm10_threshold)

    if pm10_low_mask.any():
        norm1, vmin1, vmax1 = _minmax_norm(pm10[pm10_low_mask])
        sc1 = _draw_site_map(
            axes[0],
            draw_bg,
            extent,
            lon[pm10_low_mask],
            lat[pm10_low_mask],
            pm10[pm10_low_mask],
            f"PM10 at {MAP_TIME:%Y-%m-%d %H:%M}{_unit_text(pm10_unit)}",
            "YlOrRd",
            norm1,
        )
        cbar1 = fig.colorbar(sc1, ax=axes[0], shrink=0.85, pad=0.02)
        cbar1.set_label(f"PM10{_unit_text(pm10_unit)}")
        if vmax1 > vmin1:
            cbar1.set_ticks([vmin1, (vmin1 + vmax1) / 2.0, vmax1])
        else:
            cbar1.set_ticks([vmin1, vmax1])
    else:
        # Draw basemap even if all points are above threshold.
        _draw_site_map(
            axes[0],
            draw_bg,
            extent,
            np.array([], dtype=float),
            np.array([], dtype=float),
            np.array([], dtype=float),
            f"PM10 at {MAP_TIME:%Y-%m-%d %H:%M}{_unit_text(pm10_unit)}",
            "YlOrRd",
            mcolors.Normalize(vmin=0.0, vmax=1.0),
        )

    if pm10_high_mask.any():
        axes[0].scatter(
            lon[pm10_high_mask],
            lat[pm10_high_mask],
            s=26,
            c="black",
            transform=ccrs.PlateCarree(),
            zorder=8,
            label=f"PM10 > {pm10_threshold:.0f}",
        )
        axes[0].legend(loc="lower left", frameon=True, framealpha=0.9, fontsize=8)

    norm2, vmin2, vmax2 = _minmax_norm(pm25[valid_pm25])
    sc2 = _draw_site_map(
        axes[1],
        draw_bg,
        extent,
        lon[valid_pm25],
        lat[valid_pm25],
        pm25[valid_pm25],
        f"PM2.5 at {MAP_TIME:%Y-%m-%d %H:%M}{_unit_text(pm25_unit)}",
        "Blues",
        norm2,
    )
    cbar2 = fig.colorbar(sc2, ax=axes[1], shrink=0.85, pad=0.02)
    cbar2.set_label(f"PM2.5{_unit_text(pm25_unit)}")
    if vmax2 > vmin2:
        cbar2.set_ticks([vmin2, (vmin2 + vmax2) / 2.0, vmax2])
    else:
        cbar2.set_ticks([vmin2, vmax2])

    fig.suptitle("China Sites with Valid Data (PM10 / PM2.5)")
    fig.tight_layout()
    fig.savefig(MAP_OUTPUT_PNG, dpi=220, bbox_inches="tight")
    plt.show()
    print(f"Saved map: {os.path.abspath(MAP_OUTPUT_PNG)}")


if __name__ == "__main__":
    plot_pm10_pm25_sites_at_hour()

# Call manually in this cell:
# plot_pm10_pm25_sites_at_hour()
# %%
