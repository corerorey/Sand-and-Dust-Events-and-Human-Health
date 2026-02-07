import matplotlib.pyplot as plt
import matplotlib.cm as cm
import matplotlib.colors as mcolors

from cnmap import (
    DEFAULT_WORLD_ADM0_SHP,
    DEFAULT_WORLD_ADM1_SHP,
    build_world_country_cells,
    create_world_country_cell_figure,
)


def main():
    plt.rcParams.update(
        {
            "font.sans-serif": ["Times New Roman", "SimHei", "Microsoft YaHei", "Arial Unicode MS"],
            "axes.unicode_minus": False,
            "mathtext.default": "regular",
            "mathtext.fontset": "stix",
        }
    )

    # Directly set map window: (lon_min, lon_max, lat_min, lat_max)
    plot_extent = (60, 160, 20, 65)
    keep_internal_boundaries = False

    # Build only within the selected extent to avoid global pre-processing.
    cells = build_world_country_cells(
        subdivision_shape_path=DEFAULT_WORLD_ADM1_SHP,
        country_shape_path=DEFAULT_WORLD_ADM0_SHP,
        processing_extent=plot_extent,
    )

    # Example heatmap values (replace with your own country-level values).
    country_values = {k: float(v.area) for k, v in cells.items()}
    value_cmap = "YlOrRd"
    valid_values = list(country_values.values())
    vmin = min(valid_values) if valid_values else 0.0
    vmax = max(valid_values) if valid_values else 1.0
    if vmax <= vmin:
        vmax = vmin + 1e-9

    fig, ax = create_world_country_cell_figure(
        world_adm1_shape_path=DEFAULT_WORLD_ADM1_SHP,
        world_adm0_shape_path=DEFAULT_WORLD_ADM0_SHP,
        extent=plot_extent,
        draw_grid=True,
        country_colors=None,
        country_values=country_values,
        value_cmap=value_cmap,
        draw_labels=True,
        avoid_overlap=True,
        processing_extent=plot_extent,
        show_internal_boundaries=keep_internal_boundaries,
    )

    sm = cm.ScalarMappable(norm=mcolors.Normalize(vmin=vmin, vmax=vmax), cmap=value_cmap)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, shrink=0.76, pad=0.02)
    cbar.set_label("Country Cell Value")

    fig.savefig("world_country_cell_default.png", dpi=350, bbox_inches="tight")
    plt.show()


if __name__ == "__main__":
    main()
