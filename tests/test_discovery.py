import json

from unittest.mock import MagicMock

from ad_mqtt.Bridge import Bridge
from ad_mqtt.Devices import RfZone, init_devices
from ad_mqtt.Discovery import Discovery

from test_rf_log import make_msg

BAT_ID = "master_bedroom_right_window_battery"


def make_zone_bridge():
    zones, rf_devices = init_devices(
        [RfZone(789186, 510, "master_bedroom_right_window",
                "Master Bedroom Right Window", loop=2)])
    bridge = Bridge(MagicMock(), MagicMock(), "1234",
                    zones=zones, rf_devices=rf_devices)
    return bridge, zones


def config_payloads(bridge, zones):
    disc = Discovery(MagicMock(), bridge, zones)
    return disc.messages


def payload_by_object_id(bridge, zones, object_id):
    return next(p for _, p in config_payloads(bridge, zones)
                if p.get("object_id") == object_id)


def test_battery_state_topic_matches_bridge_publish():
    bridge, zones = make_zone_bridge()

    payload = payload_by_object_id(bridge, zones, BAT_ID)
    state_topic = payload["state_topic"]
    assert state_topic == "alarm/sensor/master_bedroom_right_window/battery"
    assert payload["json_attributes_topic"] == state_topic

    # Bridge publishes battery status on an RF message with the battery
    # bit set (0x02).  The discovery config must point at that topic or
    # HA shows the battery as Unknown forever.
    bridge.mqtt.publish.reset_mock()
    bridge.on_rfx_message(None, make_msg(serial="0789186", value=0x23))
    published = [c.args[0] for c in bridge.mqtt.publish.call_args_list]

    assert state_topic in published


def test_zone_state_topic_matches_bridge_publish():
    bridge, zones = make_zone_bridge()

    payload = payload_by_object_id(
        bridge, zones, "master_bedroom_right_window")
    state_topic = payload["state_topic"]
    assert state_topic == "alarm/sensor/master_bedroom_right_window/state"

    bridge.mqtt.publish.reset_mock()
    bridge.on_rfx_message(None, make_msg(serial="0789186", value=0xa0))
    published = [c.args[0] for c in bridge.mqtt.publish.call_args_list]

    assert state_topic in published


def test_zone_battery_entity_ids_are_suffixed():
    bridge, zones = make_zone_bridge()

    payload = payload_by_object_id(bridge, zones, BAT_ID)
    assert payload["unique_id"] == \
        "admqtt_master_bedroom_right_window_battery"
    assert payload["object_id"] == BAT_ID


def test_battery_payload_is_valid_json():
    bridge, zones = make_zone_bridge()

    payload = payload_by_object_id(bridge, zones, BAT_ID)
    # Discovery messages are serializable (used by mqtt_connected).
    json.dumps(payload)
