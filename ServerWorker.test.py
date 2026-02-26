from sys import orig_argv

from RtpPacket import RtpPacket


def test_fragmentation():
    # Mô phỏng 1 Frame MJPEG
    original_frame = b"X" * 5000
    MTU = 1400

    # Server: Cắt & Đóng gói
    packets = []
    mv = memoryview(original_frame)
    total = len(mv)
    pos = 0
    seq = 2

    while pos < total:
        chunk = bytes(mv[pos : pos + MTU])
        pos += MTU
        marker = 1 if pos >= total else 0

        pkt = RtpPacket()
        pkt.encode(2, 0, 0, 0, seq, marker, 26, 0, chunk)
        packets.append(pkt.getPacket())
        seq += 1

    # Kiểm tra: 5000 / 1400 = 4 pkts (1400 + 1400 + 1400 + 800)
    assert len(packets) == 4, f"Số packet sai: {len(packets)}"

    # Client: Nhận & Ghép nối lại
    buf = bytearray()
    final_frame = None

    for raw in packets:
        rtp_pkt = RtpPacket()
        rtp_pkt.decode(raw)
        buf += rtp_pkt.getPayload()

        if rtp_pkt.marker() == 1:  # Mảnh cuối cùng -> Ghép đã xong
            final_frame = bytes(buf)
            break

    # Kiểm tra tính toàn vẹn dữ liệu
    assert final_frame == original_frame, "Frame cuối cùng không giống Frame gốc!"

    # Kiểm tra seqCounter có tăng đúng không?
    pkt_list = [RtpPacket() for _ in range(4)]
    original_seq = 2
    for (
        i,
        raw,
    ) in enumerate(packets):
        pkt_list[i].decode(raw)
    assert all(pkt_list[i].seqNum() == original_seq + i for i in range(4)), (
        "SeqCounter không tăng đúng!"
    )

    # Kiểm tra frame cuối có marker=1
    assert (
        all(pkt_list[i].marker() == 0 for i in range(3)) and pkt_list[3].marker() == 1
    ), "Frame cuối không có marker=1!"

    print("[PASS] test_fragmentation chạy thành công!")


if __name__ == "__main__":
    print("Đang chạy ServerWorker Tests...")

    test_fragmentation()
