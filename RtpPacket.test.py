from RtpPacket import RtpPacket


def test_basic_encode_decode():
    pkt = RtpPacket()
    dummy_payload = b"Hello, this is dummy payload"

    pkt.encode(
        version=2,
        padding=0,
        extension=0,
        cc=0,
        seqnum=42,
        marker=0,
        pt=26,  # MJPEG
        ssrc=12345,
        payload=dummy_payload,
    )

    # Mô phỏng gửi qua mạng -> nhận -> decode
    raw_bytes = pkt.getPacket()
    pkt2 = RtpPacket()
    pkt2.decode(raw_bytes)

    # Assertions
    assert pkt2.version() == 2, f"Version sai: {pkt2.version()}"
    assert pkt2.seqNum() == 42, f"Seqnum sai: {pkt2.seqNum()}"
    assert pkt2.payloadType() == 26, f"PT sai: {pkt2.payloadType()}"
    assert pkt2.getPayload() == dummy_payload, f"Payload sai: {pkt2.getPayload()}"
    print("[PASS] test_basic_encode_decode")


def test_packet_size():
    pkt = RtpPacket()
    payload = b"X" * 1000  # 1000 bytes

    pkt.encode(2, 0, 0, 0, 1, 0, 26, 0, payload)

    raw = pkt.getPacket()
    assert len(raw) == 12 + len(payload), f"Packet size sai: {len(raw)}"
    print("[PASS] test_packet_size")


def test_marker_bit():
    pkt = RtpPacket()
    payload = b"end_of_frame"

    pkt.encode(2, 0, 0, 0, 99, marker=1, pt=26, ssrc=0, payload=payload)

    raw = pkt.getPacket()
    pkt2 = RtpPacket()
    pkt2.decode(raw)

    # Bit cao nhất của byte 1 là Marker
    marker_bit = (pkt2.header[1] >> 7) & 0x01
    assert marker_bit == 1, f"Marker bit sai: {marker_bit}"
    print("[PASS] test_marker_bit")


def test_seqnum_boundary():
    # Kiểm tra giá trị của seqnum khi vượt quá giới hạn 16 bit
    pkt = RtpPacket()
    pkt.encode(2, 0, 0, 0, seqnum=65535, marker=0, pt=26, ssrc=0, payload=b"seqnum_max")

    raw = pkt.getPacket()
    pkt2 = RtpPacket()
    pkt2.decode(raw)

    assert pkt2.seqNum() == 65535, f"Seqnum sai: {pkt2.seqNum()}"
    print("[PASS] test_seqnum_boundary")


def test_byte0():
    # Ví dụ V=2, P=0, X=0, CC=3 -> Byte 0 phải = 0b10000011 = 0x83 = 131
    pkt = RtpPacket()
    pkt.encode(
        version=2,
        padding=0,
        extension=0,
        cc=3,
        seqnum=0,
        marker=0,
        pt=26,
        ssrc=0,
        payload=b"",
    )

    assert pkt.header[0] == 0b10000011, (
        f"Byte 0 sai: {bin(pkt.header[0])} (mong đợi 0b10000011)"
    )
    print("[PASS] test_byte0")


# Chạy tất cả các test
if __name__ == "__main__":
    print("Starting RtpPacket tests...")

    test_basic_encode_decode()
    test_packet_size()
    test_marker_bit()
    test_seqnum_boundary()
    test_byte0()

    print("All tests passed!")
