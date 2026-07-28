from shared.dji_metadata import (
    image_sequence_number,
    load_dji_mrk_overrides,
    parse_dji_mrk_file,
)


def test_mrk_parser_reads_coordinates_and_standard_deviations(tmp_path):
    sidecar = tmp_path / "flight_Timestamp.MRK"
    sidecar.write_text(
        "1\t400373.488877\t[2264]\t-40,N\t0,E\t86,V\t"
        "44.42824637,Lat\t1.26196331,Lon\t359.494,Ellh\t"
        "0.028, 0.031, 0.055\t16,Q\n",
        encoding="utf-8",
    )

    marks = parse_dji_mrk_file(sidecar)

    assert marks[1]["latitude"] == 44.42824637
    assert marks[1]["longitude"] == 1.26196331
    assert marks[1]["altitude_m"] == 359.494
    assert marks[1]["horizontal_error_m"] == 0.031
    assert marks[1]["position_std_m"]["vertical_m"] == 0.055
    assert marks[1]["vertical_reference"] == "ellipsoidal"
    assert marks[1]["vertical_reference_source"] == "dji_mrk_ellh"


def test_mrk_records_are_mapped_to_images_in_the_same_flight(tmp_path):
    flight = tmp_path / "flight"
    flight.mkdir()
    image = flight / "DJI_20230601171232_0001_V.JPG"
    image.write_bytes(b"jpeg")
    (flight / "flight_Timestamp.MRK").write_text(
        "1\t1.0\t[1]\t0,N\t0,E\t0,V\t44.0,Lat\t1.0,Lon\t"
        "100.0,Ellh\t1.0, 2.0, 3.0\t16,Q\n",
        encoding="utf-8",
    )

    overrides = load_dji_mrk_overrides(tmp_path, [image])

    assert image_sequence_number(image) == 1
    assert overrides["flight/DJI_20230601171232_0001_V.JPG"]["source"] == "dji_mrk"
