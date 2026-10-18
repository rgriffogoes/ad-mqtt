import json
import os

from unittest.mock import MagicMock

from ad_mqtt.Bridge import Bridge
from ad_mqtt.Devices import Rf, RfZone, Zone, init_devices, normalize_serial
from ad_mqtt.RfLog import RfLog


def make_msg(serial="24E5C8", value=0x21, raw="RF:24E5C8,21"):
    msg = MagicMock()
    msg.serial_number = serial
    msg.value = value
    msg.battery = bool(value & 0x02)
    msg.supervision = bool(value & 0x04)
    # Bit 8 = loop 1, bit 6 = loop 2, bit 5 = loop 3, bit 7 = loop 4
    msg.loop = [bool(value & (1 << (b - 1))) for b in (8, 6, 5, 7)]
    msg.raw = raw
    return msg


def make_bridge(rf_devices=None, rf_log=None, zones=None):
    return Bridge(MagicMock(), MagicMock(), "1234",
                  zones=zones if zones is not None else {},
                  rf_devices=rf_devices if rf_devices is not None else {},
                  rf_log=rf_log)


def read_lines(path):
    with open(path) as f:
        return [json.loads(line) for line in f]


# ---------------------------------------------------------------- RfLog


def test_rf_log_writes_json_line(tmp_path):
    path = str(tmp_path / "rf.jsonl")
    log = RfLog(path)
    log.write({"serial": "AAA", "loop": [True, False, False, False]})

    assert read_lines(path) == [{"serial": "AAA",
                                 "loop": [True, False, False, False]}]


def test_rf_log_appends_multiple_lines(tmp_path):
    path = str(tmp_path / "rf.jsonl")
    log = RfLog(path)
    log.write({"serial": "AAA"})
    log.write({"serial": "BBB"})

    assert [r["serial"] for r in read_lines(path)] == ["AAA", "BBB"]


def test_rf_log_rotates_at_size_cap(tmp_path):
    path = str(tmp_path / "rf.jsonl")
    record = {"serial": "A" * 90, "loop": [False] * 4}
    # Cap allows ~2 records per file.
    log = RfLog(path, max_bytes=250)

    for _ in range(6):
        log.write(record)

    assert os.path.getsize(path) <= 250
    assert os.path.exists(path + ".1")
    # Old records went to the backup, newest record is in the active file.
    assert read_lines(path) == [record]


def test_rf_log_rotates_only_when_file_has_content(tmp_path):
    path = str(tmp_path / "rf.jsonl")
    # A single record larger than the cap is still written (no rotation
    # loop on an empty file).
    record = {"serial": "A" * 300}
    log = RfLog(path, max_bytes=100)
    log.write(record)

    assert read_lines(path) == [record]
    assert not os.path.exists(path + ".1")


def test_rf_log_missing_file_size_is_zero(tmp_path):
    log = RfLog(str(tmp_path / "nope.jsonl"))
    assert log._size() == 0


# --------------------------------------------------------------- Bridge


def test_unknown_rf_serial_is_logged(tmp_path):
    path = str(tmp_path / "rf.jsonl")
    bridge = make_bridge(rf_log=RfLog(path))

    # Matches alarmdecoder bit mapping: bit 8 = loop 1, bit 6 = loop 2,
    # bit 5 = loop 3, bit 7 = loop 4.  0x81 = bits 8+1 -> loop 1 open.
    bridge.on_rfx_message(None, make_msg(serial="24E5C8", value=0x81))

    lines = read_lines(path)
    assert len(lines) == 1
    rec = lines[0]
    assert rec["serial"] == "24E5C8"
    assert rec["value"] == 0x81
    assert rec["loop"] == [True, False, False, False]
    assert rec["open_loops"] == [1]
    assert rec["battery"] is False
    assert rec["supervision"] is False
    assert rec["raw"] == "RF:24E5C8,21"
    assert "time" in rec


def test_unknown_rf_serial_matches_real_decoder_parsing(tmp_path):
    from alarmdecoder.messages.rf_message import RFMessage

    path = str(tmp_path / "rf.jsonl")
    bridge = make_bridge(rf_log=RfLog(path))

    bridge.on_rfx_message(None, RFMessage("RF:24E5C8,21"))

    rec = read_lines(path)[0]
    # 0x21 = bit 6 -> loop 2 open (same as the real decoder reports)
    assert rec["loop"] == [False, True, False, False]
    assert rec["open_loops"] == [2]


def test_unknown_rf_serial_battery_flag(tmp_path):
    path = str(tmp_path / "rf.jsonl")
    bridge = make_bridge(rf_log=RfLog(path))

    bridge.on_rfx_message(None, make_msg(serial="24E5C8", value=0x23))

    rec = read_lines(path)[0]
    assert rec["battery"] is True


def test_known_rf_serial_is_not_logged(tmp_path):
    path = str(tmp_path / "rf.jsonl")
    rf = Rf("24E5C8", [Zone(5, "rf_front_door", "Front door")])
    bridge = make_bridge(rf_devices={"24E5C8": rf}, rf_log=RfLog(path))

    bridge.on_rfx_message(None, make_msg(serial="24E5C8", value=0x21))

    assert not os.path.exists(path)


def test_unknown_rf_serial_without_rf_log_is_fine():
    bridge = make_bridge(rf_log=None)
    bridge.on_rfx_message(None, make_msg())


def test_bridge_defaults_rf_log_to_none():
    bridge = make_bridge()
    assert bridge.rf_log is None


# ------------------------------------------- leading-zero serial matching


def test_leading_zero_panel_serial_matches_int_config():
    # The panel zero-pads serials to 7 digits (e.g. '0789186') but
    # devices.py typically writes them as ints (789186).
    zones, rf_devices = init_devices(
        [RfZone(789186, 510, "master_bedroom_right_window",
                "Master Bedroom Right Window")])

    bridge = make_bridge(rf_devices=rf_devices, rf_log=None, zones=zones)
    # Real panel-style message with the leading-zero serial.  0x80 = bit 8
    # -> loop 1 open, which is the loop RfZone configures.
    bridge.on_rfx_message(None, make_msg(serial="0789186", value=0x80))

    # Device matched: loop state was applied to the configured zone.
    assert zones[510].faulted is True


def test_leading_zero_serial_in_config_matches_panel():
    # Config written with a leading-zero string should also match.
    _, rf_devices = init_devices(
        [RfZone("0789186", 510, "master_bedroom_right_window",
                "Master Bedroom Right Window")])

    assert "789186" in rf_devices


def test_normalize_serial():
    assert normalize_serial("0789186") == "789186"
    assert normalize_serial(789186) == "789186"
    assert normalize_serial("789186") == "789186"
    assert normalize_serial("000") == "0"
