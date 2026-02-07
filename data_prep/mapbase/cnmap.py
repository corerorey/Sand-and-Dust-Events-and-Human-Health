from functools import lru_cache
from pathlib import Path
import hashlib
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Optional

import cartopy.crs as ccrs
import cartopy.feature as cfeature
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from cartopy.io.shapereader import Reader
from shapely.geometry import Point, box as shapely_box, shape as shapely_shape
from shapely.ops import unary_union

# Default shapefiles
DEFAULT_CHINA_SHP = "china-myclass.shp"
DEFAULT_CHINA_ADM1_SHP = "geoBoundaries-CHN-ADM1-all/geoBoundaries-CHN-ADM1.shp"
DEFAULT_WORLD_ADM0_SHP = "geoBoundariesCGAZ_ADM0/geoBoundariesCGAZ_ADM0.shp"
DEFAULT_WORLD_ADM1_SHP = "geoBoundariesCGAZ_ADM1/geoBoundariesCGAZ_ADM1.shp"

# Default extents
MAIN_EXTENT = (73, 136, 17, 54)
SOUTH_CHINA_SEA_EXTENT = (105, 125, 2, 25)
WORLD_EXTENT = (-180, 180, -89.5, 89.5)
WORLD_PACIFIC_CENTRIC_PROJ = ccrs.PlateCarree(central_longitude=150)


@dataclass
class ProvinceCell:
    name: str
    geometry: object
    centroid: tuple[float, float]
    area: float
    neighbors: set[str]


@dataclass
class CountryCell:
    key: str
    label: str
    geometry: object
    centroid: tuple[float, float]
    area: float
    neighbors: set[str]


@lru_cache(maxsize=16)
def _read_geometries(shape_path: str):
    shp = Path(shape_path)
    if not shp.exists():
        raise FileNotFoundError(f"Shapefile not found: {shape_path}")
    return tuple(Reader(str(shp)).geometries())


@lru_cache(maxsize=8)
def _read_records(shape_path: str):
    shp = Path(shape_path)
    if not shp.exists():
        raise FileNotFoundError(f"Shapefile not found: {shape_path}")
    try:
        return tuple(Reader(str(shp)).records())
    except UnicodeDecodeError:
        # Some DBF files are not UTF-8; fall back to pyshp with common encodings.
        import shapefile

        encodings = ("utf-8", "latin1", "cp1252", "gbk", "gb18030")
        last_exc = None
        for enc in encodings:
            try:
                sf = shapefile.Reader(str(shp), encoding=enc, encodingErrors="replace")
                field_names = [f[0] for f in sf.fields[1:]]
                rows = []
                for sr in sf.iterShapeRecords():
                    attrs = dict(zip(field_names, sr.record))
                    geom = shapely_shape(sr.shape.__geo_interface__)
                    rows.append(SimpleNamespace(attributes=attrs, geometry=geom))
                return tuple(rows)
            except Exception as exc:
                last_exc = exc
        raise RuntimeError(f"Failed to read DBF records for {shape_path}") from last_exc


def _build_shape_feature(
    shape_path: str,
    proj: ccrs.CRS,
    edgecolor: str = "k",
    facecolor: str = "none",
) -> cfeature.ShapelyFeature:
    abs_path = str(Path(shape_path).resolve())
    return cfeature.ShapelyFeature(
        _read_geometries(abs_path),
        proj,
        edgecolor=edgecolor,
        facecolor=facecolor,
    )


def _country_name_from_attrs(attrs: dict) -> str:
    candidates = [
        "shapeName",
        "ADM0_NAME",
        "NAME_0",
        "COUNTRY",
        "NAME",
        "ADMIN",
        "SOVEREIGNT",
        "GID_0",
    ]
    for key in candidates:
        if key in attrs and attrs[key]:
            return str(attrs[key])
    for key, value in attrs.items():
        if value and ("name" in str(key).lower() or "country" in str(key).lower()):
            return str(value)
    return "Unknown"


def _country_group_name_from_attrs(attrs: dict) -> str:
    """
    Prefer country-level fields for grouping ADM1 rows into countries.
    """
    candidates = [
        "ADM0_NAME",
        "NAME_0",
        "COUNTRY",
        "ADMIN",
        "SOVEREIGNT",
        "shapeGroup",
        "GID_0",
        "ISO_A3",
        "shapeISO",
    ]
    for key in candidates:
        if key in attrs and attrs[key]:
            return str(attrs[key])
    return _country_name_from_attrs(attrs)


def _looks_like_country_code(text: str) -> bool:
    s = str(text).strip()
    if not s:
        return False
    s2 = s.replace("-", "")
    return len(s2) <= 4 and s2.isalnum() and s2.upper() == s2


def _country_display_name_from_attrs(attrs: dict) -> str:
    """
    Prefer human-readable country names for map labels.
    """
    name_candidates = [
        "ADM0_NAME",
        "NAME_0",
        "COUNTRY",
        "ADMIN",
        "SOVEREIGNT",
        "shapeName",
        "NAME",
        "FORMAL_EN",
        "NAME_EN",
    ]
    for key in name_candidates:
        value = attrs.get(key)
        if value and not _looks_like_country_code(str(value)):
            return str(value)

    for key, value in attrs.items():
        if not value:
            continue
        key_l = str(key).lower()
        if ("name" in key_l or "country" in key_l or "admin" in key_l) and not _looks_like_country_code(str(value)):
            return str(value)

    return _country_group_name_from_attrs(attrs)


def _prefer_display_name(old_name: Optional[str], new_name: str) -> str:
    if not old_name:
        return new_name
    old_is_code = _looks_like_country_code(old_name)
    new_is_code = _looks_like_country_code(new_name)
    if old_is_code and not new_is_code:
        return new_name
    if new_is_code and not old_is_code:
        return old_name
    return new_name if len(new_name) > len(old_name) else old_name


def _country_color_key(name: str) -> str:
    """
    Normalize names so China and Taiwan share one color.
    """
    n = str(name).strip().lower()
    if n in {
        "chn",
        "china",
        "people's republic of china",
        "people s republic of china",
        "prc",
        "cn",
        "156",
    }:
        return "CHN"
    if "aksai chin" in n or "demchok" in n or n == "113":
        return "CHN"
    if n in {
        "taiwan",
        "taiwan, province of china",
        "taiwan province of china",
        "taiwan province",
        "taiwan (province of china)",
        "twn",
        "tw",
        "taiwan, china",
    }:
        return "CHN"
    return str(name)


def _country_group_merge_key(group_key: str, display_name: str = "") -> str:
    """
    Merge-rule key for country cells.
    China and Taiwan are treated as one country cell key: CHN.
    """
    if _country_color_key(group_key) == "CHN" or _country_color_key(display_name) == "CHN":
        return "CHN"
    return str(group_key)


def _skip_country_label(name: str) -> bool:
    raw = str(name).strip()
    n = raw.lower()
    if "taiwan" in n or n in {"tw", "twn"}:
        return True

    # Disputed island labels to hide.
    if any(k in n for k in ("senkaku", "diaoyu", "paracel", "spratly")):
        return True

    return False


def _stable_color(name: str, palette):
    digest = hashlib.md5(name.encode("utf-8")).hexdigest()
    idx = int(digest[:8], 16) % len(palette)
    return palette[idx]


def _province_name_from_attrs(attrs: dict) -> str:
    candidates = [
        "name",
        "NAME",
        "NAME_1",
        "shapeName",
        "prov_name",
        "province",
        "PROVINCE",
        "ADM1_NAME",
    ]
    for key in candidates:
        if key in attrs and attrs[key]:
            return str(attrs[key])
    for key, value in attrs.items():
        key_l = str(key).lower()
        if value and ("name" in key_l or "prov" in key_l or "adm1" in key_l):
            return str(value)
    return "Unknown"


def _format_china_province_label(name: str) -> str:
    """
    Normalize China ADM1 label text for display.
    - Canonical short names for municipalities and autonomous regions
    - Fix known source-name issue: Guangzhou -> Guangdong
    - Remove trailing 'Province'
    - Use 'S.A.R.' suffix for Hong Kong / Macau
    """
    text = str(name).strip()
    low = text.lower()

    # Fix known source issue.
    if "guangzhou" in low:
        return "Guangdong"

    # Municipalities: keep city names only.
    if "beijing" in low:
        return "Beijing"
    if "tianjin" in low:
        return "Tianjin"
    if "chongqing" in low:
        return "Chongqing"
    if "shanghai" in low:
        return "Shanghai"

    # Autonomous regions: keep the first leading term.
    if "inner mongolia" in low:
        return "Inner Mongolia"
    if "xinjiang" in low:
        return "Xinjiang"
    if "tibet" in low or "xizang" in low:
        return "Tibet"
    if "guangxi" in low:
        return "Guangxi"
    if "ningxia" in low:
        return "Ningxia"

    if low.endswith(" province"):
        text = text[: -len(" province")].strip()
        low = text.lower()

    if "hong kong" in low:
        return "Hong Kong S.A.R."
    if "macao" in low or "macau" in low:
        return "Macau S.A.R."

    return text


def _load_china_province_groups(shape_path: str):
    abs_path = str(Path(shape_path).resolve())
    records = _read_records(abs_path)
    grouped = {}
    line_like_geoms = []
    for rec in records:
        pname = _province_name_from_attrs(rec.attributes)
        geom = rec.geometry
        gtype = getattr(geom, "geom_type", "")
        if "Polygon" in gtype:
            grouped.setdefault(pname, []).append(geom)
        else:
            line_like_geoms.append(geom)
    return grouped, line_like_geoms


def _are_province_neighbors(geom_a, geom_b) -> bool:
    ax0, ay0, ax1, ay1 = geom_a.bounds
    bx0, by0, bx1, by1 = geom_b.bounds
    if ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0:
        return False
    try:
        inter = geom_a.boundary.intersection(geom_b.boundary)
        return (not inter.is_empty) and getattr(inter, "length", 0.0) > 1e-6
    except Exception:
        return False


def _build_china_province_cells_from_grouped(grouped: dict[str, list]):
    cells = {}
    for pname, geoms in grouped.items():
        try:
            merged = unary_union(geoms)
            rep = merged.representative_point()
            cells[pname] = ProvinceCell(
                name=pname,
                geometry=merged,
                centroid=(rep.x, rep.y),
                area=float(merged.area),
                neighbors=set(),
            )
        except Exception:
            continue

    names = list(cells.keys())
    for i, n1 in enumerate(names):
        g1 = cells[n1].geometry
        for j in range(i + 1, len(names)):
            n2 = names[j]
            g2 = cells[n2].geometry
            if _are_province_neighbors(g1, g2):
                cells[n1].neighbors.add(n2)
                cells[n2].neighbors.add(n1)
    return cells


def build_china_province_cells(shape_path: str = DEFAULT_CHINA_ADM1_SHP) -> dict[str, ProvinceCell]:
    """
    Build province cells for downstream plotting (heatmaps, choropleths, etc.).
    """
    grouped, _ = _load_china_province_groups(shape_path)
    return _build_china_province_cells_from_grouped(grouped)


def color_china_province_cells(
    cells: dict[str, ProvinceCell],
    palette: Optional[list[str]] = None,
) -> dict[str, str]:
    """
    Color provinces with a low number of colors while ensuring neighboring
    provinces have different colors (greedy DSATUR-style coloring).
    """
    if palette is None:
        palette = ["#f2dc7f", "#b9d7ea", "#cabfd9", "#b7d6c4", "#f3c9bf", "#d9d2c1"]
    else:
        palette = list(palette)

    neighbors = {k: set(v.neighbors) for k, v in cells.items()}
    uncolored = set(cells.keys())
    color_idx = {}

    while uncolored:
        def sat_degree(name: str):
            neighbor_colors = {color_idx[n] for n in neighbors[name] if n in color_idx}
            return (len(neighbor_colors), len(neighbors[name]))

        node = max(uncolored, key=sat_degree)
        used = {color_idx[n] for n in neighbors[node] if n in color_idx}
        idx = 0
        while idx in used:
            idx += 1

        if idx >= len(palette):
            hue = (idx * 0.61803398875) % 1.0
            extra = mcolors.hsv_to_rgb((hue, 0.35, 0.92))
            palette.append(mcolors.to_hex(extra))

        color_idx[node] = idx
        uncolored.remove(node)

    return {k: palette[i] for k, i in color_idx.items()}


def _color_china_province_cells_by_values(
    cells: dict[str, ProvinceCell],
    values: dict[str, float],
    cmap: str = "YlOrRd",
    missing_color: str = "#ece8e2",
) -> dict[str, str]:
    valid = [values[name] for name in cells if name in values and values[name] is not None]
    if not valid:
        return {name: missing_color for name in cells}
    vmin = min(valid)
    vmax = max(valid)
    if vmax <= vmin:
        vmax = vmin + 1e-9
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
    cm = plt.get_cmap(cmap)

    out = {}
    for name in cells:
        v = values.get(name)
        if v is None:
            out[name] = missing_color
        else:
            out[name] = mcolors.to_hex(cm(norm(v)))
    return out


def _load_world_country_groups(
    subdivision_shape_path: str = DEFAULT_WORLD_ADM1_SHP,
    country_shape_path: Optional[str] = DEFAULT_WORLD_ADM0_SHP,
    processing_extent: Optional[tuple[float, float, float, float]] = None,
):
    abs_adm1_path = str(Path(subdivision_shape_path).resolve()) if subdivision_shape_path else None
    abs_adm0_path = str(Path(country_shape_path).resolve()) if country_shape_path else None

    grouped = {}
    display_names = {}

    if abs_adm0_path and Path(abs_adm0_path).exists():
        records = _read_records(abs_adm0_path)
    else:
        if not abs_adm1_path:
            raise ValueError("At least one of country_shape_path or subdivision_shape_path must be set.")
        records = _read_records(abs_adm1_path)

    def _need_filter(ext):
        return ext is not None and ext != WORLD_EXTENT

    def _extent_polygons(ext):
        lon_min, lon_max, lat_min, lat_max = ext
        if lon_min <= lon_max:
            return [shapely_box(lon_min, lat_min, lon_max, lat_max)]
        # Dateline-crossing extent.
        return [
            shapely_box(lon_min, lat_min, 180.0, lat_max),
            shapely_box(-180.0, lat_min, lon_max, lat_max),
        ]

    def _collect_polygonal(g, out):
        gtype = getattr(g, "geom_type", "")
        if "Polygon" in gtype:
            out.append(g)
            return
        if hasattr(g, "geoms"):
            for gg in g.geoms:
                _collect_polygonal(gg, out)

    def _clip_to_extent_polygonal(geom, ext):
        clipped_parts = []
        for ep in _extent_polygons(ext):
            try:
                inter = geom.intersection(ep)
            except Exception:
                continue
            if inter.is_empty:
                continue
            polys = []
            _collect_polygonal(inter, polys)
            if polys:
                clipped_parts.append(unary_union(polys))
        if not clipped_parts:
            return None
        if len(clipped_parts) == 1:
            return clipped_parts[0]
        return unary_union(clipped_parts)

    for rec in records:
        key = _country_group_name_from_attrs(rec.attributes)
        label = _country_display_name_from_attrs(rec.attributes)
        merge_key = _country_group_merge_key(key, label)
        geom = rec.geometry
        gtype = getattr(geom, "geom_type", "")
        if "Polygon" not in gtype:
            continue
        if _need_filter(processing_extent):
            clipped = _clip_to_extent_polygonal(geom, processing_extent)
            if clipped is None or getattr(clipped, "is_empty", True):
                continue
            geom = clipped
        grouped.setdefault(merge_key, []).append(geom)
        if merge_key == "CHN":
            display_names[merge_key] = "China"
        else:
            display_names[merge_key] = _prefer_display_name(display_names.get(merge_key), label)

    return grouped, display_names


def _build_world_country_cells_from_groups(
    grouped: dict[str, list],
    display_names: dict[str, str],
) -> dict[str, CountryCell]:
    cells = {}
    for key, geoms in grouped.items():
        try:
            merged = unary_union(geoms)
            rep = merged.representative_point()
            cells[key] = CountryCell(
                key=key,
                label=display_names.get(key, key),
                geometry=merged,
                centroid=(rep.x, rep.y),
                area=float(merged.area),
                neighbors=set(),
            )
        except Exception:
            continue

    names = list(cells.keys())
    for i, n1 in enumerate(names):
        g1 = cells[n1].geometry
        for j in range(i + 1, len(names)):
            n2 = names[j]
            g2 = cells[n2].geometry
            if _are_province_neighbors(g1, g2):
                cells[n1].neighbors.add(n2)
                cells[n2].neighbors.add(n1)
    return cells


def build_world_country_cells(
    subdivision_shape_path: str = DEFAULT_WORLD_ADM1_SHP,
    country_shape_path: Optional[str] = DEFAULT_WORLD_ADM0_SHP,
    processing_extent: Optional[tuple[float, float, float, float]] = None,
) -> dict[str, CountryCell]:
    """
    Build world country cells for downstream plotting.
    """
    grouped, display_names = _load_world_country_groups(
        subdivision_shape_path=subdivision_shape_path,
        country_shape_path=country_shape_path,
        processing_extent=processing_extent,
    )
    return _build_world_country_cells_from_groups(grouped, display_names)


def _add_world_boundaries_filtered(
    ax,
    shape_path: str,
    proj,
    extent: tuple[float, float, float, float],
    edgecolor: str,
    linewidth: float,
    alpha: float = 1.0,
    zorder: int = 3,
):
    abs_path = str(Path(shape_path).resolve())
    if extent == WORLD_EXTENT:
        border = _build_shape_feature(abs_path, proj, edgecolor=edgecolor)
        ax.add_feature(border, facecolor="none", lw=linewidth, alpha=alpha, zorder=zorder)
        return

    lon_min, lon_max, lat_min, lat_max = extent
    if lon_min <= lon_max:
        extent_polys = [shapely_box(lon_min, lat_min, lon_max, lat_max)]
    else:
        extent_polys = [
            shapely_box(lon_min, lat_min, 180.0, lat_max),
            shapely_box(-180.0, lat_min, lon_max, lat_max),
        ]

    geoms = []
    for g in _read_geometries(abs_path):
        try:
            if any(g.intersects(ep) for ep in extent_polys):
                geoms.append(g)
        except Exception:
            continue

    if geoms:
        ax.add_geometries(
            geoms,
            crs=proj,
            facecolor="none",
            edgecolor=edgecolor,
            linewidth=linewidth,
            alpha=alpha,
            zorder=zorder,
        )


def _add_country_boundaries_from_geoms(
    ax,
    geoms: list,
    proj,
    extent: tuple[float, float, float, float],
    edgecolor: str,
    linewidth: float,
    alpha: float = 1.0,
    zorder: int = 4,
):
    if not geoms:
        return

    if extent == WORLD_EXTENT:
        draw_geoms = geoms
    else:
        lon_min, lon_max, lat_min, lat_max = extent
        if lon_min <= lon_max:
            extent_polys = [shapely_box(lon_min, lat_min, lon_max, lat_max)]
        else:
            extent_polys = [
                shapely_box(lon_min, lat_min, 180.0, lat_max),
                shapely_box(-180.0, lat_min, lon_max, lat_max),
            ]
        draw_geoms = []
        for g in geoms:
            try:
                if any(g.intersects(ep) for ep in extent_polys):
                    draw_geoms.append(g)
            except Exception:
                continue

    if draw_geoms:
        ax.add_geometries(
            draw_geoms,
            crs=proj,
            facecolor="none",
            edgecolor=edgecolor,
            linewidth=linewidth,
            alpha=alpha,
            zorder=zorder,
        )


def color_world_country_cells(
    cells: dict[str, CountryCell],
    palette: Optional[list[str]] = None,
) -> dict[str, str]:
    """
    Color countries with a low number of colors while ensuring neighboring
    countries have different colors.
    """
    if palette is None:
        palette = ["#f2dc7f", "#b9d7ea", "#cabfd9", "#b7d6c4", "#f3c9bf", "#d9d2c1", "#c8d9a0"]
    else:
        palette = list(palette)

    neighbors = {k: set(v.neighbors) for k, v in cells.items()}
    uncolored = set(cells.keys())
    color_idx = {}

    while uncolored:
        def sat_degree(name: str):
            neighbor_colors = {color_idx[n] for n in neighbors[name] if n in color_idx}
            return (len(neighbor_colors), len(neighbors[name]))

        node = max(uncolored, key=sat_degree)
        used = {color_idx[n] for n in neighbors[node] if n in color_idx}
        idx = 0
        while idx in used:
            idx += 1

        if idx >= len(palette):
            hue = (idx * 0.61803398875) % 1.0
            extra = mcolors.hsv_to_rgb((hue, 0.35, 0.92))
            palette.append(mcolors.to_hex(extra))

        color_idx[node] = idx
        uncolored.remove(node)

    return {k: palette[i] for k, i in color_idx.items()}


def _color_world_country_cells_by_values(
    cells: dict[str, CountryCell],
    values: dict[str, float],
    cmap: str = "YlOrRd",
    missing_color: str = "#ece8e2",
) -> dict[str, str]:
    valid = [values[k] for k in cells if k in values and values[k] is not None]
    if not valid:
        return {k: missing_color for k in cells}
    vmin = min(valid)
    vmax = max(valid)
    if vmax <= vmin:
        vmax = vmin + 1e-9
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax)
    cm = plt.get_cmap(cmap)

    out = {}
    for k in cells:
        v = values.get(k)
        if v is None:
            out[k] = missing_color
        else:
            out[k] = mcolors.to_hex(cm(norm(v)))
    return out


def _boxes_overlap(a, b, padding=0.0):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return not (ax1 + padding < bx0 or bx1 + padding < ax0 or ay1 + padding < by0 or by1 + padding < ay0)


def _estimate_label_box(lon: float, lat: float, text: str, fontsize: float):
    width = max(1.6, len(text) * fontsize * 0.17)
    height = max(0.9, fontsize * 0.33)
    return (lon - width / 2, lat - height / 2, lon + width / 2, lat + height / 2)


def _label_box_inside_geometry(geom, box):
    x0, y0, x1, y1 = box
    points = (
        Point(x0, y0),
        Point(x0, y1),
        Point(x1, y0),
        Point(x1, y1),
        Point((x0 + x1) * 0.5, (y0 + y1) * 0.5),
    )
    try:
        return all(geom.covers(p) for p in points)
    except Exception:
        return False


def _label_center_inside_geometry(geom, lon: float, lat: float):
    try:
        return geom.covers(Point(lon, lat))
    except Exception:
        return False


def _box_center(box):
    x0, y0, x1, y1 = box
    return ((x0 + x1) * 0.5, (y0 + y1) * 0.5)


def _overlap_area(a, b, padding=0.0):
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    if padding != 0.0:
        ax0 -= padding
        ay0 -= padding
        ax1 += padding
        ay1 += padding
        bx0 -= padding
        by0 -= padding
        bx1 += padding
        by1 += padding
    ix = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    iy = max(0.0, min(ay1, by1) - max(ay0, by0))
    return ix * iy


def _box_inside_extent(box, extent, margin=0.0):
    x0, y0, x1, y1 = box
    lon_min, lon_max, lat_min, lat_max = extent
    return x0 >= lon_min + margin and x1 <= lon_max - margin and y0 >= lat_min + margin and y1 <= lat_max - margin


def _lonlat_in_extent(lon, lat, ext):
    if ext is None:
        return True
    lon_min, lon_max, lat_min, lat_max = ext
    if lat < lat_min or lat > lat_max:
        return False
    if lon_min <= lon_max:
        return lon_min <= lon <= lon_max
    # Dateline-crossing extent.
    return lon >= lon_min or lon <= lon_max


def _extent_area_fraction(ext: Optional[tuple[float, float, float, float]]) -> float:
    """
    Approximate visible area fraction against full lon/lat domain (360x180).
    Smaller extent -> smaller fraction.
    """
    if ext is None or ext == WORLD_EXTENT:
        return 1.0
    lon_min, lon_max, lat_min, lat_max = ext
    lon_span = (lon_max - lon_min) if lon_min <= lon_max else (360.0 - (lon_min - lon_max))
    lat_span = max(0.0, lat_max - lat_min)
    frac = (max(0.0, lon_span) * max(0.0, lat_span)) / (360.0 * 180.0)
    return max(0.01, min(1.0, frac))


def _adaptive_country_label_params(ext: Optional[tuple[float, float, float, float]]):
    """
    Dynamic label density:
    - smaller extent => more labels
    - smaller extent => lower area threshold
    - smaller extent => allow tighter packing
    """
    frac = _extent_area_fraction(ext)
    max_sparse_labels = int(min(260, max(95, round(95 + (1.0 - frac) * 230))))
    min_sparse_area = max(1.2, 18.0 * (frac ** 0.55))
    overlap_padding = max(0.16, 0.6 * (frac ** 0.5))
    return max_sparse_labels, min_sparse_area, overlap_padding


def _build_label_offsets():
    offsets = [(0.0, 0.0)]
    for r in (0.55, 0.8, 1.1, 1.4, 1.8, 2.2, 2.7, 3.2, 3.8, 4.4):
        offsets.extend(
            [
                (r, 0.0),
                (-r, 0.0),
                (0.0, r),
                (0.0, -r),
                (0.7 * r, 0.7 * r),
                (-0.7 * r, 0.7 * r),
                (0.7 * r, -0.7 * r),
                (-0.7 * r, -0.7 * r),
            ]
        )
    return offsets


def _select_label_position_knn(
    lon: float,
    lat: float,
    text: str,
    font_candidates: list[float],
    offsets: list[tuple[float, float]],
    placed_boxes: list,
    extent: tuple[float, float, float, float],
    overlap_padding: float,
    knn_neighbors: int = 10,
):
    relax_steps = [overlap_padding, overlap_padding * 0.5, 0.0, -0.02, -0.05]

    # First pass: strict/relaxed collision-free placement inside extent.
    for pad in relax_steps:
        for fs in font_candidates:
            for dx, dy in offsets:
                lx, ly = lon + dx, lat + dy
                box = _estimate_label_box(lx, ly, text, fs)
                if not _box_inside_extent(box, extent, margin=0.06):
                    continue
                if any(_boxes_overlap(box, prev, padding=pad) for prev in placed_boxes):
                    continue
                return lx, ly, fs, box

    # KNN-inspired fallback: choose minimum-cost in-map candidate.
    best = None
    best_score = float("inf")
    for fs in font_candidates:
        for dx, dy in offsets:
            lx, ly = lon + dx, lat + dy
            box = _estimate_label_box(lx, ly, text, fs)
            if not _box_inside_extent(box, extent, margin=0.02):
                continue

            if placed_boxes:
                nearest = sorted(
                    placed_boxes,
                    key=lambda b: (_box_center(b)[0] - lx) ** 2 + (_box_center(b)[1] - ly) ** 2,
                )[:knn_neighbors]
            else:
                nearest = []

            overlap_cost = sum(_overlap_area(box, prev, padding=-0.02) for prev in nearest)
            displacement_cost = ((lx - lon) ** 2 + ((ly - lat) * 1.12) ** 2) ** 0.5
            score = overlap_cost * 180.0 + displacement_cost * 0.7

            if score < best_score:
                best_score = score
                best = (lx, ly, fs, box)

    return best


def _place_label_with_overlap_control(
    ax,
    proj,
    text: str,
    lon: float,
    lat: float,
    fontsize: float,
    color: str,
    placed_boxes: list,
    offsets: Optional[list[tuple[float, float]]] = None,
    overlap_padding: float = 0.05,
    label_extent: Optional[tuple[float, float, float, float]] = None,
    clip_on: bool = True,
    zorder: int = 5,
):
    if offsets is None:
        offsets = [
            (0.0, 0.0),
            (1.2, 0.0),
            (-1.2, 0.0),
            (0.0, 0.9),
            (0.0, -0.9),
            (1.0, 0.8),
            (-1.0, 0.8),
            (1.0, -0.8),
            (-1.0, -0.8),
        ]
    for dx, dy in offsets:
        lx, ly = lon + dx, lat + dy
        if not _lonlat_in_extent(lx, ly, label_extent):
            continue
        box = _estimate_label_box(lx, ly, text, fontsize)
        if any(_boxes_overlap(box, prev, padding=overlap_padding) for prev in placed_boxes):
            continue
        ax.text(
            lx,
            ly,
            text,
            transform=proj,
            fontsize=fontsize,
            color=color,
            ha="center",
            va="center",
            clip_on=clip_on,
            zorder=zorder,
        )
        placed_boxes.append(box)
        return True
    return False


def draw_china_basemap(
    ax,
    shape_path: str = DEFAULT_CHINA_SHP,
    extent: tuple[float, float, float, float] = MAIN_EXTENT,
    draw_grid: bool = True,
    border_lw: float = 0.6,
):
    """
    Draw a China basemap on an existing cartopy axis.
    """
    proj = ccrs.PlateCarree()
    china_border = _build_shape_feature(shape_path, proj, edgecolor="k")

    ax.set_extent(extent, crs=proj)
    ax.add_feature(cfeature.OCEAN.with_scale("50m"), facecolor="#8fabd4", zorder=0)
    ax.add_feature(cfeature.LAND.with_scale("50m"), facecolor="#d8d8c7", zorder=1)
    ax.add_feature(china_border, lw=border_lw, zorder=2)

    if draw_grid:
        gl = ax.gridlines(
            draw_labels=True,
            linestyle="--",
            color="gray",
            alpha=0.5,
            linewidth=0.5,
        )
        gl.top_labels = False
        gl.right_labels = False

    return ax


def draw_china_province_labeled_map(
    ax,
    shape_path: str = DEFAULT_CHINA_ADM1_SHP,
    extent: tuple[float, float, float, float] = MAIN_EXTENT,
    draw_grid: bool = True,
    draw_labels: bool = True,
    avoid_overlap: bool = True,
    province_colors: Optional[dict[str, str]] = None,
    province_values: Optional[dict[str, float]] = None,
    value_cmap: str = "YlOrRd",
    missing_color: str = "#ece8e2",
    label_overlap_padding: float = 0.05,
    min_label_fontsize: float = 4.5,
    knn_neighbors: int = 10,
):
    """
    Draw China map with province-level color fills and labels from shapefile attributes.
    """
    proj = ccrs.PlateCarree()
    abs_path = str(Path(shape_path).resolve())

    ax.set_extent(extent, crs=proj)
    ax.add_feature(cfeature.OCEAN.with_scale("50m"), facecolor="#8fabd4", zorder=0)
    ax.add_feature(cfeature.LAND.with_scale("50m"), facecolor="#d8d8c7", zorder=1)

    grouped, line_like_geoms = _load_china_province_groups(shape_path)
    cells = _build_china_province_cells_from_grouped(grouped)

    if province_colors is None:
        if province_values is not None:
            province_colors = _color_china_province_cells_by_values(
                cells,
                province_values,
                cmap=value_cmap,
                missing_color=missing_color,
            )
        else:
            province_colors = color_china_province_cells(cells)

    for pname, cell in cells.items():
        ax.add_geometries(
            [cell.geometry],
            crs=proj,
            facecolor=province_colors.get(pname, missing_color),
            edgecolor="#f7f5f2",
            linewidth=0.45,
            zorder=2,
        )

    # Draw non-polygon features as lines only to avoid invalid face fills.
    if line_like_geoms:
        ax.add_geometries(
            line_like_geoms,
            crs=proj,
            facecolor="none",
            edgecolor="#5b4b7a",
            linewidth=0.45,
            zorder=3,
        )

    border = _build_shape_feature(abs_path, proj, edgecolor="#dbd8d8")
    ax.add_feature(border, facecolor="none", lw=0.45, zorder=3)

    if draw_grid:
        gl = ax.gridlines(
            draw_labels=True,
            linestyle="--",
            color="gray",
            alpha=0.5,
            linewidth=0.5,
        )
        gl.top_labels = False
        gl.right_labels = False

    if draw_labels:
        merged_by_prov = []
        for pname, cell in cells.items():
            if pname == "Unknown":
                continue
            merged_by_prov.append((pname, cell.area, cell.centroid[0], cell.centroid[1]))

        merged_by_prov.sort(key=lambda x: x[1], reverse=True)
        for pname, area, lon, lat in merged_by_prov:
            label_text = _format_china_province_label(pname)
            fontsize = 8 if area > 8 else 7 if area > 3 else 6
            cell_geom = cells[pname].geometry

            # Province labels: no anti-overlap displacement logic.
            # Priority is to keep each label center inside province boundary.
            # Font is not allowed to become too small.
            font_candidates = [fontsize, fontsize - 0.4, fontsize - 0.8, fontsize - 1.2, fontsize - 1.6]
            font_candidates = [fs for fs in font_candidates if fs >= min_label_fontsize]
            if not font_candidates:
                font_candidates = [min_label_fontsize]

            in_province_offsets = [
                (0.0, 0.0),
                (0.22, 0.0),
                (-0.22, 0.0),
                (0.0, 0.22),
                (0.0, -0.22),
                (0.18, 0.18),
                (-0.18, 0.18),
                (0.18, -0.18),
                (-0.18, -0.18),
                (0.35, 0.0),
                (-0.35, 0.0),
                (0.0, 0.35),
                (0.0, -0.35),
                (0.28, 0.28),
                (-0.28, 0.28),
                (0.28, -0.28),
                (-0.28, -0.28),
                (0.55, 0.0),
                (-0.55, 0.0),
                (0.0, 0.55),
                (0.0, -0.55),
                (0.45, 0.45),
                (-0.45, 0.45),
                (0.45, -0.45),
                (-0.45, -0.45),
            ]

            chosen = None
            allow_outside_center = label_text in {"Hong Kong S.A.R.", "Macau S.A.R."}

            if allow_outside_center:
                # Special-case tiny coastal regions: allow label center outside boundary.
                outside_offsets = [
                    (0.75, -0.15),
                    (0.95, -0.10),
                    (0.65, -0.30),
                    (1.10, -0.20),
                    (0.45, -0.45),
                    (0.15, -0.55),
                ]
                for fs in font_candidates:
                    for dx, dy in outside_offsets + in_province_offsets:
                        lx, ly = lon + dx, lat + dy
                        chosen = (lx, ly, fs)
                        break
                    if chosen:
                        break
            else:
                for fs in font_candidates:
                    for dx, dy in in_province_offsets:
                        lx, ly = lon + dx, lat + dy
                        if _label_center_inside_geometry(cell_geom, lx, ly):
                            chosen = (lx, ly, fs)
                            break
                    if chosen:
                        break

            if chosen is None:
                # Force draw near representative point with minimum size.
                chosen = (lon, lat, font_candidates[-1])

            ax.text(
                chosen[0],
                chosen[1],
                label_text,
                transform=proj,
                fontsize=chosen[2],
                color="#2f2f2f",
                ha="center",
                va="center",
                zorder=5,
            )

    return ax


def draw_world_adm1_basemap(
    ax,
    shape_path: str = DEFAULT_WORLD_ADM1_SHP,
    extent: tuple[float, float, float, float] = WORLD_EXTENT,
    draw_grid: bool = True,
    border_lw: float = 0.18,
):
    """
    Draw a world basemap based on geoBoundaries CGAZ ADM1 boundaries.
    """
    proj = ccrs.PlateCarree()
    world_adm1 = _build_shape_feature(shape_path, proj, edgecolor="#2f2f2f")

    if extent == WORLD_EXTENT:
        ax.set_global()
    else:
        ax.set_extent(extent, crs=proj)
    ax.add_feature(cfeature.OCEAN.with_scale("110m"), facecolor="#8fabd4", zorder=0)
    ax.add_feature(cfeature.LAND.with_scale("110m"), facecolor="#d8d8c7", zorder=1)
    ax.add_feature(world_adm1, lw=border_lw, zorder=2)

    if draw_grid:
        gl = ax.gridlines(
            draw_labels=True,
            linestyle="--",
            color="gray",
            alpha=0.45,
            linewidth=0.4,
        )
        gl.top_labels = False
        gl.right_labels = False

    return ax


def draw_world_blank_basemap(
    ax,
    shape_path: str = DEFAULT_WORLD_ADM1_SHP,
    country_shape_path: Optional[str] = DEFAULT_WORLD_ADM0_SHP,
    extent: tuple[float, float, float, float] = WORLD_EXTENT,
    draw_grid: bool = True,
    draw_boundaries: bool = True,
    show_internal_boundaries: bool = False,
    processing_extent: Optional[tuple[float, float, float, float]] = None,
    boundary_lw: float = 0.18,
):
    """
    Draw a white world canvas with configurable boundary granularity.
    - show_internal_boundaries=True: draw ADM1 boundaries
    - show_internal_boundaries=False: draw country-cell boundaries only
    Designed as a lightweight background for gridded lon/lat overlays.
    """
    proj = ccrs.PlateCarree()

    if extent == WORLD_EXTENT:
        ax.set_global()
    else:
        ax.set_extent(extent, crs=proj)
    ax.set_facecolor("white")

    if draw_boundaries:
        effective_processing_extent = processing_extent if processing_extent is not None else (None if extent == WORLD_EXTENT else extent)
        if show_internal_boundaries:
            _add_world_boundaries_filtered(
                ax=ax,
                shape_path=shape_path,
                proj=proj,
                extent=extent,
                edgecolor="#000000",
                linewidth=boundary_lw,
                alpha=1.0,
                zorder=2,
            )
        else:
            country_path = country_shape_path if country_shape_path and Path(country_shape_path).exists() else None
            cells = build_world_country_cells(
                subdivision_shape_path=shape_path,
                country_shape_path=country_path,
                processing_extent=effective_processing_extent,
            )
            _add_country_boundaries_from_geoms(
                ax=ax,
                geoms=[c.geometry for c in cells.values()],
                proj=proj,
                extent=extent,
                edgecolor="#000000",
                linewidth=boundary_lw,
                alpha=1.0,
                zorder=2,
            )

    if draw_grid:
        gl = ax.gridlines(
            draw_labels=True,
            linestyle="-",
            color="#c8c8c8",
            alpha=0.75,
            linewidth=0.35,
        )
        gl.top_labels = False
        gl.right_labels = False

    return ax


def add_lonlat_grid_layer(
    ax,
    lons,
    lats,
    values,
    method: str = "pcolormesh",
    cmap: str = "viridis",
    vmin: Optional[float] = None,
    vmax: Optional[float] = None,
    alpha: float = 1.0,
    zorder: int = 6,
    **kwargs,
):
    """
    Overlay lon/lat gridded data onto an existing world axis.

    Parameters
    ----------
    ax : matplotlib axis with cartopy projection
    lons, lats : 1D/2D longitude and latitude arrays
    values : 2D data array
    method : {"pcolormesh", "contourf"}
    """
    proj = ccrs.PlateCarree()
    method_l = str(method).strip().lower()

    if method_l == "pcolormesh":
        shading = kwargs.pop("shading", "auto")
        return ax.pcolormesh(
            lons,
            lats,
            values,
            transform=proj,
            shading=shading,
            cmap=cmap,
            vmin=vmin,
            vmax=vmax,
            alpha=alpha,
            zorder=zorder,
            **kwargs,
        )

    if method_l == "contourf":
        levels = kwargs.pop("levels", 15)
        return ax.contourf(
            lons,
            lats,
            values,
            levels=levels,
            transform=proj,
            cmap=cmap,
            vmin=vmin,
            vmax=vmax,
            alpha=alpha,
            zorder=zorder,
            **kwargs,
        )

    raise ValueError("method must be 'pcolormesh' or 'contourf'.")


def draw_world_country_cell_map(
    ax,
    subdivision_shape_path: str = DEFAULT_WORLD_ADM1_SHP,
    country_shape_path: Optional[str] = DEFAULT_WORLD_ADM0_SHP,
    extent: tuple[float, float, float, float] = WORLD_EXTENT,
    draw_grid: bool = True,
    draw_labels: bool = True,
    avoid_overlap: bool = True,
    country_colors: Optional[dict[str, str]] = None,
    country_values: Optional[dict[str, float]] = None,
    value_cmap: str = "YlOrRd",
    missing_color: str = "#ece8e2",
    show_internal_boundaries: bool = True,
    processing_extent: Optional[tuple[float, float, float, float]] = None,
):
    """
    Draw world map by country cells for downstream country-level visualizations.
    """
    proj = ccrs.PlateCarree()
    abs_adm1_path = str(Path(subdivision_shape_path).resolve()) if subdivision_shape_path else None
    abs_adm0_path = str(Path(country_shape_path).resolve()) if country_shape_path else None

    ax.set_facecolor("#ece8e2")
    if extent == WORLD_EXTENT:
        ax.set_global()
    else:
        ax.set_extent(extent, crs=proj)
    ax.add_feature(cfeature.OCEAN.with_scale("110m"), facecolor="#c6d2dd", zorder=0)
    ax.add_feature(cfeature.LAND.with_scale("110m"), facecolor="#ece8e2", zorder=1)

    effective_processing_extent = processing_extent if processing_extent is not None else (None if extent == WORLD_EXTENT else extent)

    cells = build_world_country_cells(
        subdivision_shape_path=subdivision_shape_path,
        country_shape_path=country_shape_path,
        processing_extent=effective_processing_extent,
    )

    if country_colors is None:
        if country_values is not None:
            country_colors = _color_world_country_cells_by_values(
                cells,
                values=country_values,
                cmap=value_cmap,
                missing_color=missing_color,
            )
        else:
            country_colors = color_world_country_cells(cells)

    for key, cell in cells.items():
        ax.add_geometries(
            [cell.geometry],
            crs=proj,
            facecolor=country_colors.get(key, missing_color),
            edgecolor="#f7f5f2",
            linewidth=0.2,
            zorder=2,
        )

    if show_internal_boundaries and abs_adm1_path:
        _add_world_boundaries_filtered(
            ax=ax,
            shape_path=abs_adm1_path,
            proj=proj,
            extent=extent,
            edgecolor="#f7f4ee",
            linewidth=0.12,
            alpha=0.95,
            zorder=3,
        )

    # Draw national borders from merged country-cell geometries, so merged areas
    # (e.g., CHN + Taiwan + configured regions) do not show internal borders.
    _add_country_boundaries_from_geoms(
        ax=ax,
        geoms=[c.geometry for c in cells.values()],
        proj=proj,
        extent=extent,
        edgecolor="#ede7dd",
        linewidth=0.36,
        alpha=1.0,
        zorder=4,
    )

    if draw_grid:
        gl = ax.gridlines(
            draw_labels=True,
            linestyle="-",
            color="#b3ada4",
            alpha=0.8,
            linewidth=0.4,
        )
        gl.top_labels = False
        gl.right_labels = False

    if draw_labels:
        labels = sorted(cells.values(), key=lambda c: c.area, reverse=True)
        placed_boxes = []
        # Country-cell labels: sparse labeling for crowded regions.
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
            effective_processing_extent if effective_processing_extent is not None else extent
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
                # First priority is displacement; second policy is sparse omission.
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

    return ax


def draw_world_partition_labeled_map(
    ax,
    subdivision_shape_path: str = DEFAULT_WORLD_ADM1_SHP,
    country_shape_path: Optional[str] = None,
    extent: tuple[float, float, float, float] = WORLD_EXTENT,
    draw_grid: bool = True,
    avoid_overlap: bool = True,
):
    """
    Draw a styled world map: country-level color partitions + labels.
    Keep ADM1 internal boundaries if subdivision_shape_path is provided.

    Notes
    -----
    - If country_shape_path is None, countries are derived by grouping ADM1 records.
    - China and Taiwan share the same fill color.
    - Taiwan label is suppressed.
    """
    proj = ccrs.PlateCarree()
    abs_adm1_path = str(Path(subdivision_shape_path).resolve()) if subdivision_shape_path else None
    abs_adm0_path = str(Path(country_shape_path).resolve()) if country_shape_path else None

    ax.set_facecolor("#ece8e2")
    if extent == WORLD_EXTENT:
        ax.set_global()
    else:
        ax.set_extent(extent, crs=proj)
    ax.add_feature(cfeature.OCEAN.with_scale("110m"), facecolor="#c6d2dd", zorder=0)
    ax.add_feature(cfeature.LAND.with_scale("110m"), facecolor="#ece8e2", zorder=1)

    palette = [
        "#f2dc7f",
        "#d1e5a6",
        "#b9d7ea",
        "#e7b58a",
        "#cabfd9",
        "#b7d6c4",
        "#f3c9bf",
        "#d9d2c1",
    ]

    grouped = {}
    display_names = {}
    if abs_adm0_path:
        country_records = _read_records(abs_adm0_path)
        for rec in country_records:
            group_key = _country_group_name_from_attrs(rec.attributes)
            display_name = _country_display_name_from_attrs(rec.attributes)
            merge_key = _country_group_merge_key(group_key, display_name)
            grouped.setdefault(merge_key, []).append(rec.geometry)
            if merge_key == "CHN":
                display_names[merge_key] = "China"
            else:
                display_names[merge_key] = _prefer_display_name(display_names.get(merge_key), display_name)
    else:
        if not abs_adm1_path:
            raise ValueError("At least one of country_shape_path or subdivision_shape_path must be set.")
        adm1_records = _read_records(abs_adm1_path)
        for rec in adm1_records:
            group_key = _country_group_name_from_attrs(rec.attributes)
            display_name = _country_display_name_from_attrs(rec.attributes)
            merge_key = _country_group_merge_key(group_key, display_name)
            grouped.setdefault(merge_key, []).append(rec.geometry)
            if merge_key == "CHN":
                display_names[merge_key] = "China"
            else:
                display_names[merge_key] = _prefer_display_name(display_names.get(merge_key), display_name)

    for group_key, geoms in grouped.items():
        ax.add_geometries(
            geoms,
            crs=proj,
            facecolor=_stable_color(_country_color_key(group_key), palette),
            edgecolor="#f7f5f2",
            linewidth=0.2,
            zorder=2,
        )

    if abs_adm1_path:
        adm1_border = _build_shape_feature(abs_adm1_path, proj, edgecolor="#f7f4ee")
        ax.add_feature(adm1_border, facecolor="none", lw=0.12, alpha=0.95, zorder=3)

    # National border overlay will be drawn from merged country geometries below.

    if draw_grid:
        gl = ax.gridlines(
            draw_labels=True,
            linestyle="-",
            color="#b3ada4",
            alpha=0.8,
            linewidth=0.4,
        )
        gl.top_labels = False
        gl.right_labels = False

    merged_by_country = []
    for group_key, geoms in grouped.items():
        try:
            merged = unary_union(geoms)
            rep = merged.representative_point()
            label_text = display_names.get(group_key, group_key)
            merged_by_country.append((group_key, label_text, merged.area, rep.x, rep.y, merged))
        except Exception:
            continue

    merged_by_country.sort(key=lambda x: x[2], reverse=True)
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
    max_sparse_labels, min_sparse_area, dynamic_overlap_padding = _adaptive_country_label_params(extent)
    _add_country_boundaries_from_geoms(
        ax=ax,
        geoms=[x[5] for x in merged_by_country],
        proj=proj,
        extent=extent,
        edgecolor="#ede7dd",
        linewidth=0.36,
        alpha=1.0,
        zorder=4,
    )

    for _, text, area, lon, lat, _ in merged_by_country:
        if text == "Unknown" or _skip_country_label(text):
            continue
        fontsize = 7 if area > 500 else 6 if area > 120 else 5 if area > 25 else 4.5
        if avoid_overlap:
            if area < min_sparse_area:
                continue
            # First priority is displacement; second policy is sparse omission.
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
                label_extent=extent,
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
                zorder=5,
            )

    ocean_labels = [
        ("Pacific\nOcean", -150, 0),
        ("Atlantic\nOcean", -35, 5),
        ("Indian\nOcean", 75, -15),
        ("Arctic\nOcean", 20, 74),
    ]
    for text, lon, lat in ocean_labels:
        ax.text(
            lon,
            lat,
            text,
            transform=proj,
            fontsize=20,
            color="#7f7a72",
            alpha=0.55,
            ha="center",
            va="center",
            style="italic",
            zorder=1,
        )

    return ax


def create_china_figure(
    shape_path: str = DEFAULT_CHINA_SHP,
    include_south_china_sea_inset: bool = True,
    figsize: tuple[float, float] = (10.5, 7.5),
    dpi: int = 300,
):
    """
    Create a ready-to-use China basemap figure.
    """
    proj = ccrs.PlateCarree()
    fig = plt.figure(figsize=figsize, dpi=dpi)
    ax = fig.add_subplot(1, 1, 1, projection=proj)

    draw_china_basemap(ax=ax, shape_path=shape_path, extent=MAIN_EXTENT, draw_grid=True)

    ax_inset = None
    if include_south_china_sea_inset:
        ax_inset = fig.add_axes([0.78, 0.12, 0.24, 0.28], projection=proj)
        draw_china_basemap(
            ax=ax_inset,
            shape_path=shape_path,
            extent=SOUTH_CHINA_SEA_EXTENT,
            draw_grid=False,
        )
        gl_nh = ax_inset.gridlines(
            draw_labels=True,
            linestyle="--",
            color="gray",
            alpha=0.5,
            linewidth=0.4,
        )
        gl_nh.top_labels = False
        gl_nh.right_labels = False
        gl_nh.left_labels = False

    return fig, ax, ax_inset


def create_china_province_figure(
    shape_path: str = DEFAULT_CHINA_ADM1_SHP,
    include_south_china_sea_inset: bool = True,
    figsize: tuple[float, float] = (10.5, 7.5),
    dpi: int = 300,
    province_colors: Optional[dict[str, str]] = None,
    province_values: Optional[dict[str, float]] = None,
    value_cmap: str = "YlOrRd",
    missing_color: str = "#ece8e2",
    label_overlap_padding: float = 0.05,
    min_label_fontsize: float = 4.5,
    knn_neighbors: int = 10,
):
    """
    Create China province-level colored map with labels.
    """
    proj = ccrs.PlateCarree()
    fig = plt.figure(figsize=figsize, dpi=dpi)
    ax = fig.add_subplot(1, 1, 1, projection=proj)

    draw_china_province_labeled_map(
        ax=ax,
        shape_path=shape_path,
        extent=MAIN_EXTENT,
        draw_grid=True,
        draw_labels=True,
        avoid_overlap=True,
        province_colors=province_colors,
        province_values=province_values,
        value_cmap=value_cmap,
        missing_color=missing_color,
        label_overlap_padding=label_overlap_padding,
        min_label_fontsize=min_label_fontsize,
        knn_neighbors=knn_neighbors,
    )

    ax_inset = None
    if include_south_china_sea_inset:
        ax_inset = fig.add_axes([0.78, 0.12, 0.24, 0.28], projection=proj)
        draw_china_province_labeled_map(
            ax=ax_inset,
            shape_path=shape_path,
            extent=SOUTH_CHINA_SEA_EXTENT,
            draw_grid=False,
            draw_labels=False,
            avoid_overlap=False,
            province_colors=province_colors,
            province_values=province_values,
            value_cmap=value_cmap,
            missing_color=missing_color,
            label_overlap_padding=label_overlap_padding,
            min_label_fontsize=min_label_fontsize,
            knn_neighbors=knn_neighbors,
        )
        ax_inset.set_xticks([])
        ax_inset.set_yticks([])

    return fig, ax, ax_inset


def create_world_figure(
    world_adm1_shape_path: str = DEFAULT_WORLD_ADM1_SHP,
    extent: tuple[float, float, float, float] = WORLD_EXTENT,
    draw_grid: bool = True,
    figsize: tuple[float, float] = (12, 6.6),
    dpi: int = 300,
):
    """
    Create a standalone world ADM1 basemap figure.
    """
    proj = WORLD_PACIFIC_CENTRIC_PROJ
    fig = plt.figure(figsize=figsize, dpi=dpi)
    ax_world = fig.add_subplot(1, 1, 1, projection=proj)
    draw_world_adm1_basemap(
        ax=ax_world,
        shape_path=world_adm1_shape_path,
        extent=extent,
        draw_grid=draw_grid,
    )
    return fig, ax_world


def create_world_blank_figure(
    world_adm1_shape_path: str = DEFAULT_WORLD_ADM1_SHP,
    world_adm0_shape_path: Optional[str] = DEFAULT_WORLD_ADM0_SHP,
    extent: tuple[float, float, float, float] = WORLD_EXTENT,
    figsize: tuple[float, float] = (16, 8.6),
    dpi: int = 300,
    draw_grid: bool = True,
    draw_boundaries: bool = True,
    show_internal_boundaries: bool = False,
    processing_extent: Optional[tuple[float, float, float, float]] = None,
    boundary_lw: float = 0.18,
):
    """
    Create a blank world figure for large-scale gridded overlays.
    """
    proj = WORLD_PACIFIC_CENTRIC_PROJ
    fig = plt.figure(figsize=figsize, dpi=dpi)
    ax_world = fig.add_subplot(1, 1, 1, projection=proj)
    draw_world_blank_basemap(
        ax=ax_world,
        shape_path=world_adm1_shape_path,
        country_shape_path=world_adm0_shape_path,
        extent=extent,
        draw_grid=draw_grid,
        draw_boundaries=draw_boundaries,
        show_internal_boundaries=show_internal_boundaries,
        processing_extent=processing_extent,
        boundary_lw=boundary_lw,
    )
    return fig, ax_world


def create_world_country_cell_figure(
    world_adm1_shape_path: str = DEFAULT_WORLD_ADM1_SHP,
    world_adm0_shape_path: Optional[str] = DEFAULT_WORLD_ADM0_SHP,
    extent: tuple[float, float, float, float] = WORLD_EXTENT,
    draw_grid: bool = True,
    figsize: tuple[float, float] = (16, 8.6),
    dpi: int = 300,
    country_colors: Optional[dict[str, str]] = None,
    country_values: Optional[dict[str, float]] = None,
    value_cmap: str = "YlOrRd",
    missing_color: str = "#ece8e2",
    draw_labels: bool = True,
    avoid_overlap: bool = True,
    processing_extent: Optional[tuple[float, float, float, float]] = None,
    show_internal_boundaries: bool = True,
):
    """
    Create a world country-cell figure for country-level plotting workflows.
    """
    proj = WORLD_PACIFIC_CENTRIC_PROJ
    fig = plt.figure(figsize=figsize, dpi=dpi)
    ax_world = fig.add_subplot(1, 1, 1, projection=proj)
    country_path = world_adm0_shape_path if world_adm0_shape_path and Path(world_adm0_shape_path).exists() else None
    draw_world_country_cell_map(
        ax=ax_world,
        subdivision_shape_path=world_adm1_shape_path,
        country_shape_path=country_path,
        extent=extent,
        draw_grid=draw_grid,
        draw_labels=draw_labels,
        avoid_overlap=avoid_overlap,
        country_colors=country_colors,
        country_values=country_values,
        value_cmap=value_cmap,
        missing_color=missing_color,
        show_internal_boundaries=show_internal_boundaries,
        processing_extent=processing_extent,
    )
    return fig, ax_world


def create_world_partition_labeled_figure(
    world_adm1_shape_path: str = DEFAULT_WORLD_ADM1_SHP,
    world_adm0_shape_path: Optional[str] = DEFAULT_WORLD_ADM0_SHP,
    extent: tuple[float, float, float, float] = WORLD_EXTENT,
    draw_grid: bool = True,
    figsize: tuple[float, float] = (16, 8.6),
    dpi: int = 300,
):
    """
    Create a stylized world partition map with labels.
    """
    proj = WORLD_PACIFIC_CENTRIC_PROJ
    fig = plt.figure(figsize=figsize, dpi=dpi)
    ax_world = fig.add_subplot(1, 1, 1, projection=proj)
    country_path = world_adm0_shape_path if world_adm0_shape_path and Path(world_adm0_shape_path).exists() else None
    draw_world_partition_labeled_map(
        ax=ax_world,
        subdivision_shape_path=world_adm1_shape_path,
        country_shape_path=country_path,
        extent=extent,
        draw_grid=draw_grid,
        avoid_overlap=True,
    )
    return fig, ax_world


if __name__ == "__main__":
    plt.rcParams.update(
        {
            "font.sans-serif": ["Times New Roman", "SimHei", "Microsoft YaHei", "Arial Unicode MS"],
            "axes.unicode_minus": False,
            "mathtext.default": "regular",
            "mathtext.fontset": "stix",
        }
    )

    # fig_cn, _, _ = create_china_figure()
    # fig_cn.savefig("china_map_with_south_china_sea_inset.png", dpi=350, bbox_inches="tight")

    fig_cn_prov, _, _ = create_china_province_figure()
    fig_cn_prov.savefig("china_province_labeled_map.png", dpi=350, bbox_inches="tight")

    # fig_world, _ = create_world_figure()
    # fig_world.savefig("world_adm1_basemap.png", dpi=350, bbox_inches="tight")

    # fig_world_partition, _ = create_world_partition_labeled_figure()
    # fig_world_partition.savefig("world_partition_labeled_map.png", dpi=350, bbox_inches="tight")
    plt.show()
