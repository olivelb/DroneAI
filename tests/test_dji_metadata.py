import pytest

from shared.dji_metadata import (
    image_sequence_number,
    load_dji_mrk_overrides,
    load_position_overrides,
    parse_aerial_xmp,
    parse_dji_mrk_file,
)


def _write_xmp_jpeg(path, *, latitude="47.64368732", rtk_flag="50"):
    xml = f"""<x:xmpmeta xmlns:x="adobe:ns:meta/">
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<rdf:Description
 xmlns:tiff="http://ns.adobe.com/tiff/1.0/"
 xmlns:drone="http://www.dji.com/drone-dji/1.0/"
 tiff:Make="Autel Robotics"
 tiff:Model="XT705"
 drone:AbsoluteAltitude="+513.00"
 drone:GpsLatitude="{latitude}"
 drone:GpsLongtitude="16.47592717"
 drone:CalibratedFocalLength="4404.166667"
 drone:FlightYawDegree="-148.96"
 drone:RtkFlag="{rtk_flag}"
 drone:RtkStdLon="0.01370"
 drone:RtkStdLat="0.01439"
 drone:RtkStdHgt="0.02969"/>
</rdf:RDF></x:xmpmeta>""".encode()
    payload = b"http://ns.adobe.com/xap/1.0/\x00" + xml
    path.write_bytes(
        b"\xff\xd8\xff\xe1"
        + (len(payload) + 2).to_bytes(2, "big")
        + payload
        + b"\xff\xd9"
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


def test_autel_sequence_and_xmp_rtk_covariance_are_recognized(tmp_path):
    image = tmp_path / "MAX_0002.JPG"
    _write_xmp_jpeg(image)

    metadata = parse_aerial_xmp(image)
    gps = metadata["gps"]

    assert image_sequence_number(image) == 2
    assert metadata["camera_make"] == "Autel Robotics"
    assert metadata["calibrated_focal_length_px"] == pytest.approx(4404.166667)
    assert metadata["flight_attitude_deg"]["yaw"] == pytest.approx(-148.96)
    assert gps["source"] == "xmp_rtk"
    assert gps["latitude"] == pytest.approx(47.64368732)
    assert gps["position_std_m"] == {
        "north_m": pytest.approx(0.01439),
        "east_m": pytest.approx(0.01370),
        "vertical_m": pytest.approx(0.02969),
    }


def test_mrk_has_priority_over_xmp_and_can_live_in_rtk_subdirectory(
    tmp_path,
):
    images = tmp_path / "images"
    rtk_data = tmp_path / "RTK_Data"
    images.mkdir()
    rtk_data.mkdir()
    image = images / "MAX_0002.JPG"
    _write_xmp_jpeg(image, latitude="47.0")
    (rtk_data / "flight_Timestamp.MRK").write_text(
        "2\t1.0\t[1]\t0,N\t0,E\t0,V\t48.0,Lat\t2.0,Lon\t"
        "514.0,Ellh\t0.01, 0.02, 0.03\t50,Q\n",
        encoding="utf-8",
    )

    overrides = load_position_overrides(tmp_path, [image])

    assert overrides["images/MAX_0002.JPG"]["source"] == "dji_mrk"
    assert overrides["images/MAX_0002.JPG"]["latitude"] == 48.0


def test_unscoped_mrk_refuses_ambiguous_duplicate_sequences(tmp_path):
    images_a = tmp_path / "flight-a"
    images_b = tmp_path / "flight-b"
    rtk_data = tmp_path / "RTK_Data"
    for directory in (images_a, images_b, rtk_data):
        directory.mkdir()
    first = images_a / "MAX_0002.JPG"
    second = images_b / "MAX_0002.JPG"
    first.write_bytes(b"jpeg")
    second.write_bytes(b"jpeg")
    (rtk_data / "flight_Timestamp.MRK").write_text(
        "2\t1.0\t[1]\t0,N\t0,E\t0,V\t48.0,Lat\t2.0,Lon\t"
        "514.0,Ellh\t0.01, 0.02, 0.03\t50,Q\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Ambiguous MRK sequence"):
        load_dji_mrk_overrides(tmp_path, [first, second])
