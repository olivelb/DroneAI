from gaussian_ortho.lichtfeld_trainer import (
    LichtFeldTrainConfig,
    build_lichtfeld_command,
)


def test_command_forwards_image_scaling_and_tile_mode():
    config = LichtFeldTrainConfig(
        iterations=500,
        sh_degree=1,
        cap_max=100_000,
        data_path="/data",
        output_path="/output",
        data_factor=8,
        max_width=1024,
        tile_mode=4,
    )

    command = build_lichtfeld_command("/opt/lichtfeld/LichtFeld-Studio", config)

    assert command[command.index("--resize_factor") + 1] == "8"
    assert command[command.index("--max-width") + 1] == "1024"
    assert command[command.index("--tile-mode") + 1] == "4"
