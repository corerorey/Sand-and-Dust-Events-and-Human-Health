import matplotlib.pyplot as plt
import matplotlib.cm as cm
import matplotlib.colors as mcolors

from cnmap import (
    DEFAULT_CHINA_ADM1_SHP,
    build_china_province_cells,
    create_china_province_figure,
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

    # Province cells: base structure for follow-up heatmaps/choropleths.
    cells = build_china_province_cells(DEFAULT_CHINA_ADM1_SHP)

    # Example heatmap values (replace with your own province-level values).
    province_values = {k: float(v.area) for k, v in cells.items()}
    value_cmap = "YlOrRd"
    valid_values = list(province_values.values())
    vmin = min(valid_values) if valid_values else 0.0
    vmax = max(valid_values) if valid_values else 1.0
    if vmax <= vmin:
        vmax = vmin + 1e-9

    fig, ax, _ = create_china_province_figure(
        shape_path=DEFAULT_CHINA_ADM1_SHP,
        include_south_china_sea_inset=True,
        province_colors=None,
        province_values=province_values,
        value_cmap=value_cmap,
        label_overlap_padding=0.03,
        min_label_fontsize=4.2,
        knn_neighbors=12,
    )

    sm = cm.ScalarMappable(norm=mcolors.Normalize(vmin=vmin, vmax=vmax), cmap=value_cmap)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, shrink=0.78, pad=0.02)
    cbar.set_label("Province Cell Value")

    fig.savefig("china_province_cell_default.png", dpi=350, bbox_inches="tight")
    plt.show()


if __name__ == "__main__":
    main()
