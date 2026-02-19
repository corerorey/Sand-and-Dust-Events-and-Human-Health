from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
import numpy as np
from netCDF4 import Dataset
import cartopy.crs as ccrs
from cartopy.io.shapereader import Reader


DEFAULT_NC = Path(
    r"C:\DOCUMENTO\Sand-and-Dust-Storms-and-Human-Health\data_prep\noaagoes16"
    r"\OR_ABI-L2-CMIPC-M6C01_G16_s20210750411186_e20210750413559_c20210750414019.nc"
)
MAPBASE_DIR = Path(__file__).resolve().parents[1] / "mapbase"
WORLD_ADM0_SHP = MAPBASE_DIR / "geoBoundariesCGAZ_ADM0" / "geoBoundariesCGAZ_ADM0.shp"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate dust-event focused maps from GOES-16 CMIPC (C01)."
    )
    parser.add_argument("--nc", type=Path, default=DEFAULT_NC, help="Input NetCDF file.")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("data_prep/noaagoes16/dust_focus_plots"),
        help="Output directory for focused figures.",
    )
    parser.add_argument(
        "--step",
        type=int,
        default=6,
        help="Downsample step for plotting (higher = faster).",
    )
    parser.add_argument(
        "--gamma",
        type=float,
        default=0.7,
        help="Gamma for enhanced reflectance map.",
    )
    parser.add_argument(
        "--candidate-quantile",
        type=float,
        default=0.85,
        help="Quantile threshold (0-1) for high-reflectance candidate mask.",
    )
    return parser.parse_args()


def as_float_array(var_data: np.ma.MaskedArray | np.ndarray) -> np.ndarray:
    arr = np.ma.array(var_data, copy=False)
    data = np.asarray(arr.data, dtype=np.float32)
    mask = np.ma.getmaskarray(arr)
    if mask.shape == ():
        if bool(mask):
            return np.array(np.nan, dtype=np.float32)
        return data
    if mask.any():
        data = data.copy()
        data[mask] = np.nan
    return data


def extract_yx_2d(var) -> np.ndarray:
    dims = list(var.dimensions)
    if "x" not in dims or "y" not in dims:
        raise ValueError(f"{var.name}: no x/y dimensions")

    index = []
    kept_dims = []
    for dim in dims:
        if dim in ("y", "x"):
            index.append(slice(None))
            kept_dims.append(dim)
        else:
            index.append(0)

    data = as_float_array(var[tuple(index)])
    if data.ndim != 2:
        raise ValueError(f"{var.name}: cannot reduce to 2D y/x")
    if kept_dims == ["x", "y"]:
        data = data.T
    if kept_dims != ["y", "x"]:
        raise ValueError(f"{var.name}: unexpected dim order {kept_dims}")
    return data


def goes_xy_to_lonlat(
    x_rad: np.ndarray,
    y_rad: np.ndarray,
    lon_origin_deg: float,
    perspective_point_height: float,
    semi_major_axis: float,
    semi_minor_axis: float,
) -> tuple[np.ndarray, np.ndarray]:
    lon0 = np.deg2rad(lon_origin_deg).astype(np.float64)
    req = np.float64(semi_major_axis)
    rpol = np.float64(semi_minor_axis)
    h = np.float64(perspective_point_height)
    H = h + req

    x2d, y2d = np.meshgrid(x_rad.astype(np.float64), y_rad.astype(np.float64))
    cos_x = np.cos(x2d)
    sin_x = np.sin(x2d)
    cos_y = np.cos(y2d)
    sin_y = np.sin(y2d)

    with np.errstate(invalid="ignore"):
        a = sin_x**2 + cos_x**2 * (cos_y**2 + (req**2 / rpol**2) * sin_y**2)
        b = -2.0 * H * cos_x * cos_y
        c = H**2 - req**2
        disc = b**2 - 4.0 * a * c
        valid = disc >= 0

        rs = np.full(x2d.shape, np.nan, dtype=np.float64)
        rs[valid] = (-b[valid] - np.sqrt(disc[valid])) / (2.0 * a[valid])

        sx = rs * cos_x * cos_y
        sy = -rs * sin_x
        sz = rs * cos_x * sin_y

        lon = lon0 - np.arctan2(sy, H - sx)
        lat = np.arctan((req**2 / rpol**2) * (sz / np.sqrt((H - sx) ** 2 + sy**2)))

    lon = np.rad2deg(lon).astype(np.float32)
    lat = np.rad2deg(lat).astype(np.float32)
    lon[~valid] = np.nan
    lat[~valid] = np.nan
    return lon, lat


def scatter_map(
    lon: np.ndarray,
    lat: np.ndarray,
    value: np.ndarray,
    out_file: Path,
    title: str,
    cmap: str = "viridis",
    cbar_label: str = "",
    vmin: float | None = None,
    vmax: float | None = None,
    s: float = 1.0,
) -> None:
    finite = np.isfinite(lon) & np.isfinite(lat) & np.isfinite(value)
    if not finite.any():
        raise ValueError(f"{out_file.name}: no finite pixels to plot")

    lon_valid = lon[finite]
    lat_valid = lat[finite]
    val_valid = value[finite]
    extent = (
        float(np.nanmin(lon_valid) - 1.5),
        float(np.nanmax(lon_valid) + 1.5),
        float(np.nanmin(lat_valid) - 1.2),
        float(np.nanmax(lat_valid) + 1.2),
    )

    proj = ccrs.PlateCarree()
    fig = plt.figure(figsize=(10, 6), dpi=160)
    ax = fig.add_subplot(1, 1, 1, projection=proj)
    ax.set_extent(extent, crs=proj)
    ax.set_facecolor("white")

    # Same style intent as world_blank_canvas_map: white canvas + black boundaries + grid.
    if WORLD_ADM0_SHP.exists():
        geoms = list(Reader(str(WORLD_ADM0_SHP)).geometries())
        ax.add_geometries(
            geoms,
            crs=proj,
            facecolor="none",
            edgecolor="#000000",
            linewidth=0.22,
            zorder=2,
        )

    gl = ax.gridlines(
        draw_labels=True,
        linestyle="-",
        color="#c8c8c8",
        alpha=0.75,
        linewidth=0.35,
    )
    gl.top_labels = False
    gl.right_labels = False

    sc = ax.scatter(
        lon_valid,
        lat_valid,
        c=val_valid,
        transform=proj,
        s=s,
        cmap=cmap,
        vmin=vmin,
        vmax=vmax,
        linewidths=0,
        rasterized=True,
        zorder=6,
    )
    ax.set_title(title)
    cb = fig.colorbar(sc, ax=ax, shrink=0.85, pad=0.02)
    cb.set_label(cbar_label)
    fig.tight_layout()
    fig.savefig(out_file, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    args = parse_args()
    nc_path = args.nc.resolve()
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    step = max(1, int(args.step))
    gamma = float(args.gamma)
    q = float(np.clip(args.candidate_quantile, 0.0, 1.0))

    with Dataset(nc_path) as ds:
        cmi = extract_yx_2d(ds.variables["CMI"])
        dqf = extract_yx_2d(ds.variables["DQF"])

        cmi = cmi[::step, ::step]
        dqf = dqf[::step, ::step]

        x = as_float_array(ds.variables["x"][:])[::step]
        y = as_float_array(ds.variables["y"][:])[::step]
        proj = ds.variables["goes_imager_projection"]
        lon, lat = goes_xy_to_lonlat(
            x_rad=x,
            y_rad=y,
            lon_origin_deg=float(proj.longitude_of_projection_origin),
            perspective_point_height=float(proj.perspective_point_height),
            semi_major_axis=float(proj.semi_major_axis),
            semi_minor_axis=float(proj.semi_minor_axis),
        )

        finite = np.isfinite(cmi) & np.isfinite(lon) & np.isfinite(lat)
        if not finite.any():
            raise RuntimeError("CMI has no valid finite pixels.")
        p2 = float(np.nanpercentile(cmi[finite], 2))
        p98 = float(np.nanpercentile(cmi[finite], 98))
        if np.isclose(p2, p98):
            p98 = p2 + 1e-6

        cmi_norm = np.clip((cmi - p2) / (p98 - p2), 0.0, 1.0)
        cmi_enhanced = np.power(cmi_norm, gamma)
        gy, gx = np.gradient(cmi_enhanced)
        cmi_grad = np.sqrt(gx**2 + gy**2).astype(np.float32)

        good_dqf = (dqf == 0) & np.isfinite(dqf)
        threshold = float(np.nanquantile(cmi_enhanced[finite], q))
        candidate_mask = np.where(good_dqf & (cmi_enhanced >= threshold), 1.0, 0.0).astype(np.float32)

        scatter_map(
            lon,
            lat,
            cmi,
            out_dir / "01_cmi_reflectance_lonlat.png",
            "CMI Reflectance (C01) on Lon/Lat",
            cmap="gray",
            cbar_label="reflectance factor",
            vmin=p2,
            vmax=p98,
            s=1.0,
        )
        scatter_map(
            lon,
            lat,
            cmi_enhanced,
            out_dir / "02_cmi_enhanced_lonlat.png",
            f"CMI Enhanced (gamma={gamma:.2f}) on Lon/Lat",
            cmap="cividis",
            cbar_label="normalized reflectance",
            vmin=0.0,
            vmax=1.0,
            s=1.0,
        )
        scatter_map(
            lon,
            lat,
            cmi_grad,
            out_dir / "03_cmi_gradient_lonlat.png",
            "CMI Gradient Magnitude (Plume/Edge Contrast)",
            cmap="magma",
            cbar_label="gradient magnitude",
            vmin=float(np.nanpercentile(cmi_grad[np.isfinite(cmi_grad)], 2)),
            vmax=float(np.nanpercentile(cmi_grad[np.isfinite(cmi_grad)], 98)),
            s=1.0,
        )
        scatter_map(
            lon,
            lat,
            candidate_mask,
            out_dir / "04_high_reflectance_candidate_mask.png",
            f"High Reflectance Candidate Mask (DQF=0, q={q:.2f})",
            cmap=ListedColormap(["#1f2937", "#f59e0b"]),
            cbar_label="0=background, 1=candidate",
            vmin=0.0,
            vmax=1.0,
            s=1.0,
        )
        scatter_map(
            lon,
            lat,
            dqf,
            out_dir / "05_dqf_quality_lonlat.png",
            "DQF Quality Flags on Lon/Lat",
            cmap="tab10",
            cbar_label="DQF flag",
            vmin=float(np.nanmin(dqf[np.isfinite(dqf)])),
            vmax=float(np.nanmax(dqf[np.isfinite(dqf)])),
            s=1.0,
        )

    print(f"Done. Focused dust figures saved to: {out_dir}")
    print("Generated files:")
    print(" - 01_cmi_reflectance_lonlat.png")
    print(" - 02_cmi_enhanced_lonlat.png")
    print(" - 03_cmi_gradient_lonlat.png")
    print(" - 04_high_reflectance_candidate_mask.png")
    print(" - 05_dqf_quality_lonlat.png")


if __name__ == "__main__":
    main()
