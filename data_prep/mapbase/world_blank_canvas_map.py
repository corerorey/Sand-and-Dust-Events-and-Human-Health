import matplotlib.pyplot as plt

from cnmap import DEFAULT_WORLD_ADM0_SHP, DEFAULT_WORLD_ADM1_SHP, create_world_blank_figure


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
    plot_extent = (20, 180, -30, 85)
    keep_internal_boundaries = False

    # White canvas + black boundary lines for large-scale lon/lat gridded overlays.
    fig, _ = create_world_blank_figure(
        world_adm1_shape_path=DEFAULT_WORLD_ADM1_SHP,
        world_adm0_shape_path=DEFAULT_WORLD_ADM0_SHP,
        extent=plot_extent,
        draw_grid=True,
        draw_boundaries=True,
        show_internal_boundaries=keep_internal_boundaries,
        processing_extent=plot_extent,
    )
    fig.savefig("world_blank_canvas.png", dpi=350, bbox_inches="tight")
    plt.show()


if __name__ == "__main__":
    main()
