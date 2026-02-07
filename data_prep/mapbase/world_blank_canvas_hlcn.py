import cartopy.crs as ccrs
import matplotlib.pyplot as plt

from world_adm0_china_region_map import draw_world_adm0_china_highlight_canvas


def main():
    plt.rcParams.update(
        {
            "font.sans-serif": ["Times New Roman", "SimHei", "Microsoft YaHei", "Arial Unicode MS"],
            "axes.unicode_minus": False,
            "mathtext.default": "regular",
            "mathtext.fontset": "stix",
        }
    )

    # Background extent for gridded lon/lat overlays.
    plot_extent = (70, 145, 5, 60)
    show_country_labels = True

    fig = plt.figure(figsize=(12.0, 7.0), dpi=300)
    ax = fig.add_subplot(1, 1, 1, projection=ccrs.PlateCarree())

    draw_world_adm0_china_highlight_canvas(
        ax=ax,
        extent=plot_extent,
        draw_grid=True,
        show_country_labels=show_country_labels,
        avoid_label_overlap=True,
        processing_extent=plot_extent,
        neighbor_linewidth=0.35,
        china_linewidth=1.1,
        china_edgecolor="#5a5a5a",
        china_alpha=0.72,
        omit_shared_with_china=True,
    )

    fig.savefig("world_blank_canvas_hlcn.png", dpi=350, bbox_inches="tight")
    plt.show()


if __name__ == "__main__":
    main()
