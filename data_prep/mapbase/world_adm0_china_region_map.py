from pathlib import Path

import cartopy.crs as ccrs
import matplotlib.pyplot as plt
from cartopy.io.shapereader import Reader
from shapely.geometry import box as shapely_box
from shapely.ops import unary_union

from cnmap import (
    DEFAULT_WORLD_ADM0_SHP,
    DEFAULT_WORLD_ADM1_SHP,
    _adaptive_country_label_params,
    _lonlat_in_extent,
    _place_label_with_overlap_control,
    _skip_country_label,
    build_world_country_cells,
)

DEFAULT_CHINA_ADM0_SIMPLIFIED_SHP = "geoBoundaries-CHN-ADM0-all/geoBoundaries-CHN-ADM0_simplified.shp"


def _is_china_or_taiwan_adm0(attrs: dict) -> bool:
    values = [
        attrs.get("shapeGroup"),
        attrs.get("shapeISO"),
        attrs.get("shapeName"),
    ]
    tokens = []
    for value in values:
        if value is None:
            continue
        tokens.append(str(value).strip().lower())

    if any(t in {"chn", "cn", "china"} for t in tokens):
        return True
    if any(t in {"twn", "tw", "taiwan"} for t in tokens):
        return True
    return any(("china" in t) or ("taiwan" in t) for t in tokens)


def _add_grid(ax):
    gl = ax.gridlines(
        draw_labels=True,
        linestyle="-",
        color="#c8c8c8",
        alpha=0.75,
        linewidth=0.35,
    )
    gl.top_labels = False
    gl.right_labels = False


def _load_china_union_geometry(
    china_adm0_simplified_path: str,
    extent: tuple[float, float, float, float],
):
    lon_min, lon_max, lat_min, lat_max = extent
    bbox = shapely_box(lon_min, lat_min, lon_max, lat_max)
    chn_geoms = []

    for rec in Reader(str(Path(china_adm0_simplified_path).resolve())).records():
        attrs = rec.attributes
        if str(attrs.get("shapeGroup", "")).strip().upper() != "CHN":
            continue

        geom = rec.geometry
        if geom is None:
            continue
        try:
            if not geom.intersects(bbox):
                continue
        except Exception:
            continue
        chn_geoms.append(geom)

    if not chn_geoms:
        return None

    china_union = unary_union(chn_geoms)
    if china_union.is_empty:
        return None
    return china_union


def _draw_non_china_adm0_boundaries(
    ax,
    adm0_path: str,
    extent: tuple[float, float, float, float],
    linewidth: float = 0.42,
    china_geom=None,
    omit_shared_with_china: bool = True,
):
    lon_min, lon_max, lat_min, lat_max = extent
    bbox = shapely_box(lon_min, lat_min, lon_max, lat_max)
    geoms = []
    for rec in Reader(str(Path(adm0_path).resolve())).records():
        geom = rec.geometry
        if geom is None:
            continue
        try:
            if not geom.intersects(bbox):
                continue
        except Exception:
            continue
        if _is_china_or_taiwan_adm0(rec.attributes):
            continue
        line_geom = geom.boundary
        if omit_shared_with_china and china_geom is not None:
            try:
                # Remove China-shared segments from neighbor borders.
                line_geom = line_geom.difference(china_geom.buffer(0.02))
            except Exception:
                pass
        try:
            if line_geom.is_empty:
                continue
        except Exception:
            continue
        geoms.append(line_geom)

    if geoms:
        ax.add_geometries(
            geoms,
            crs=ccrs.PlateCarree(),
            facecolor="none",
            edgecolor="#000000",
            linewidth=linewidth,
            zorder=2,
        )


def _draw_replaced_china_boundary(
    ax,
    china_geom,
    linewidth: float = 1.15,
    edgecolor: str = "#5a5a5a",
    alpha: float = 0.72,
):
    if china_geom is None:
        return

    if china_geom.is_empty:
        return

    ax.add_geometries(
        [china_geom.boundary],
        crs=ccrs.PlateCarree(),
        facecolor="none",
        edgecolor=edgecolor,
        linewidth=linewidth,
        alpha=alpha,
        zorder=3,
    )


def _draw_country_cell_labels(
    ax,
    extent: tuple[float, float, float, float],
    subdivision_shape_path: str = DEFAULT_WORLD_ADM1_SHP,
    country_shape_path: str = DEFAULT_WORLD_ADM0_SHP,
    processing_extent: tuple[float, float, float, float] | None = None,
    avoid_overlap: bool = True,
):
    proj = ccrs.PlateCarree()
    effective_processing_extent = processing_extent if processing_extent is not None else extent

    cells = build_world_country_cells(
        subdivision_shape_path=subdivision_shape_path,
        country_shape_path=country_shape_path,
        processing_extent=effective_processing_extent,
    )

    labels = sorted(cells.values(), key=lambda c: c.area, reverse=True)
    placed_boxes = []
    sparse_offsets = [
        (0.0, 0.0),
        (0.7, 0.0),
        (-0.7, 0.0),
        (0.0, 0.55),
        (0.0, -0.55),
        (0.55, 0.45),
        (-0.55, 0.45),
        (0.55, -0.45),
        (-0.55, -0.45),
    ]
    shown = 0
    max_sparse_labels, min_sparse_area, dynamic_overlap_padding = _adaptive_country_label_params(
        effective_processing_extent
    )

    for cell in labels:
        text = cell.label
        if text == "Unknown" or _skip_country_label(text):
            continue
        lon, lat = cell.centroid
        if not _lonlat_in_extent(lon, lat, effective_processing_extent):
            continue
        area = cell.area
        fontsize = 6.5 if area > 500 else 5.8 if area > 120 else 5.0 if area > 25 else 4.2

        if avoid_overlap:
            if area < min_sparse_area:
                continue
            placed = _place_label_with_overlap_control(
                ax=ax,
                proj=proj,
                text=text,
                lon=lon,
                lat=lat,
                fontsize=fontsize,
                color="#2f2f2f",
                placed_boxes=placed_boxes,
                offsets=sparse_offsets,
                overlap_padding=dynamic_overlap_padding,
                label_extent=effective_processing_extent,
                clip_on=True,
                zorder=5,
            )
            if placed:
                shown += 1
            if shown >= max_sparse_labels:
                break
        else:
            ax.text(
                lon,
                lat,
                text,
                transform=proj,
                fontsize=fontsize,
                color="#2f2f2f",
                ha="center",
                va="center",
                clip_on=True,
                zorder=5,
            )


def draw_world_adm0_china_highlight_canvas(
    ax,
    extent: tuple[float, float, float, float] = (70, 140, 5, 55),
    draw_grid: bool = True,
    show_country_labels: bool = False,
    avoid_label_overlap: bool = True,
    processing_extent: tuple[float, float, float, float] | None = None,
    neighbor_linewidth: float = 0.42,
    china_linewidth: float = 1.15,
    china_edgecolor: str = "#5a5a5a",
    china_alpha: float = 0.72,
    omit_shared_with_china: bool = True,
):
    """
    White background canvas with ADM0 boundaries, but China border is replaced
    by CHN ADM0 simplified geometry and highlighted.
    """
    proj = ccrs.PlateCarree()
    ax.set_extent(extent, crs=proj)
    ax.set_facecolor("white")

    china_geom = _load_china_union_geometry(
        china_adm0_simplified_path=DEFAULT_CHINA_ADM0_SIMPLIFIED_SHP,
        extent=extent,
    )

    # Neighbor line layer is sourced from original geoBoundariesCGAZ ADM0.
    _draw_non_china_adm0_boundaries(
        ax=ax,
        adm0_path=DEFAULT_WORLD_ADM0_SHP,
        extent=extent,
        linewidth=neighbor_linewidth,
        china_geom=china_geom,
        omit_shared_with_china=omit_shared_with_china,
    )
    _draw_replaced_china_boundary(
        ax=ax,
        china_geom=china_geom,
        linewidth=china_linewidth,
        edgecolor=china_edgecolor,
        alpha=china_alpha,
    )

    if draw_grid:
        _add_grid(ax)

    if show_country_labels:
        _draw_country_cell_labels(
            ax=ax,
            extent=extent,
            subdivision_shape_path=DEFAULT_WORLD_ADM1_SHP,
            country_shape_path=DEFAULT_WORLD_ADM0_SHP,
            processing_extent=processing_extent if processing_extent is not None else extent,
            avoid_overlap=avoid_label_overlap,
        )

    return ax


def main():
    plt.rcParams.update(
        {
            "font.sans-serif": ["Times New Roman", "SimHei", "Microsoft YaHei", "Arial Unicode MS"],
            "axes.unicode_minus": False,
            "mathtext.default": "regular",
            "mathtext.fontset": "stix",
        }
    )

    china_extent = (70, 140, 5, 55)

    fig = plt.figure(figsize=(10.5, 7.0), dpi=300)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())
    draw_world_adm0_china_highlight_canvas(
        ax=ax,
        extent=china_extent,
        draw_grid=True,
        show_country_labels=False,
        avoid_label_overlap=True,
        processing_extent=china_extent,
        neighbor_linewidth=0.42,
        china_linewidth=1.15,
        china_edgecolor="#5a5a5a",
        china_alpha=0.72,
        omit_shared_with_china=True,
    )

    fig.savefig("world_adm0_china_region_map.png", dpi=350, bbox_inches="tight")
    plt.show()


if __name__ == "__main__":
    main()
