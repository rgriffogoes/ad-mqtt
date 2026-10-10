import logging

from ad_mqtt.Client import Client

MSG_A = (b'[00000001100000000A--],022,'
         b'[f70000851022000028020000000000],"FAULT 22 BEDROOMFRONT WIND"')
MSG_B = (b'[00000001100000000A--],015,'
         b'[f70000851015000028020000000000],'
         b'"FAULT 15 DINING SLIDING DOOR    "')


def make_client():
    # No connection is made in the constructor.
    return Client("127.0.0.1", 10000)


def collect(client):
    lines = []
    client.on_read += lambda dev, data: lines.append(data)
    return lines


def test_cr_separated_messages_are_split():
    client = make_client()
    lines = collect(client)

    client._read_buf = MSG_A + b"\r" + MSG_B + b"\n"
    client.parse_read_buf()

    assert lines == [MSG_A, MSG_B]
    assert client._read_buf == b""


def test_lf_separated_messages_are_split():
    client = make_client()
    lines = collect(client)

    client._read_buf = MSG_A + b"\n" + MSG_B + b"\n"
    client.parse_read_buf()

    assert lines == [MSG_A, MSG_B]


def test_crlf_separated_messages_are_split():
    client = make_client()
    lines = collect(client)

    client._read_buf = MSG_A + b"\r\n" + MSG_B + b"\r\n"
    client.parse_read_buf()

    assert lines == [MSG_A, MSG_B]


def test_partial_message_is_buffered_until_complete():
    client = make_client()
    lines = collect(client)

    client._read_buf = MSG_A[:20]
    client.parse_read_buf()
    assert lines == []
    assert client._read_buf == MSG_A[:20]

    client._read_buf += MSG_A[20:] + b"\r\n"
    client.parse_read_buf()
    assert lines == [MSG_A]


def test_stray_empty_lines_are_ignored():
    client = make_client()
    lines = collect(client)

    client._read_buf = b"\r\n\r\n" + MSG_A + b"\n"
    client.parse_read_buf()

    assert lines == [MSG_A]


def test_unterminated_text_field_is_repaired():
    from alarmdecoder.messages.panel_message import Message

    client = make_client()
    parsed = []

    def handler(dev, data):
        parsed.append(Message(data.decode("utf-8")))

    client.on_read += handler

    # First frame is missing its closing quote, as happens when frames are
    # emitted back-to-back.
    truncated = MSG_A[:-1]
    client._read_buf = truncated + b"\r" + MSG_B + b"\r"
    client.parse_read_buf()

    assert [m.text for m in parsed] == [
        "FAULT 22 BEDROOMFRONT WIND",
        "FAULT 15 DINING SLIDING DOOR    ",
    ]


def test_glued_frames_are_split():
    client = make_client()
    lines = collect(client)

    # Two frames emitted back-to-back with no CR/LF between them, and the
    # first frame is missing the closing quote on its text field.
    glued_a = (b'[00000001100000000A--],022,'
               b'[f70000851022000028020000000000],"FAULT 22 BEDROFONTINW')
    glued_b = (b'[00000001100000000A--],015,'
               b'[f70000851015000028020000000000],"FAULT 15 DINING SLIDING OR')

    client._read_buf = glued_a + glued_b + b"\r\n"
    client.parse_read_buf()

    assert lines == [glued_a, glued_b]
    assert client._read_buf == b""


def test_glued_frames_are_parsed_individually():
    from alarmdecoder.messages.panel_message import Message

    client = make_client()
    parsed = []

    def handler(dev, data):
        parsed.append(Message(data.decode("utf-8")))

    client.on_read += handler

    glued_a = (b'[00000001100000000A--],022,'
               b'[f70000851022000028020000000000],"FAULT 22 BEDROFONTINW')
    glued_b = (b'[00000001100000000A--],015,'
               b'[f70000851015000028020000000000],"FAULT 15 DINING SLIDING OR')

    client._read_buf = glued_a + glued_b + b"\r\n"
    client.parse_read_buf()

    assert [m.text for m in parsed] == [
        "FAULT 22 BEDROFONTINW",
        "FAULT 15 DINING SLIDING OR",
    ]
    assert [m.numeric_code for m in parsed] == ["022", "015"]


def test_single_frame_is_not_split():
    client = make_client()
    lines = collect(client)

    client._read_buf = MSG_A + b"\r\n"
    client.parse_read_buf()

    assert lines == [MSG_A]


def test_bad_message_does_not_propagate(caplog):
    client = make_client()
    lines = []

    def boom(dev, data):
        lines.append(data)
        raise ValueError("bad message")

    client.on_read += boom

    client._read_buf = MSG_A + b"\r" + MSG_B + b"\n"
    with caplog.at_level(logging.ERROR):
        client.parse_read_buf()

    # Both messages are dispatched and the buffer is fully consumed even
    # though the handler raised.
    assert lines == [MSG_A, MSG_B]
    assert client._read_buf == b""
    assert "Error processing line" in caplog.text
