import socket
import sys
import threading
import traceback
from random import randint

from RtpPacket import RtpPacket
from VideoStream import VideoStream


class ServerWorker:
    SETUP = "SETUP"
    PLAY = "PLAY"
    PAUSE = "PAUSE"
    TEARDOWN = "TEARDOWN"

    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    OK_200 = 0
    FILE_NOT_FOUND_404 = 1
    CON_ERR_500 = 2

    """
    MTU_PAYLOAD_SIZE = 1400 là do Ethernet MTU = 1500 bytes, trong đó:
        - IP Header = 20 bytes
        - UDP Header = 8 bytes
        - RTP Header = 12 bytes
    => Nên tối đa payload chỉ 1460 bytes nên an toàn thì lấy 1400 bytes (chừa lại làm preverse).
    """
    MTU_PAYLOAD_SIZE = 1400

    # clientInfo = {} # Đây là Class Attribute nên nếu dùng ServerWorker.clientInfo thay vì sử dụng self.clientInfo thì tất cả User sẽ dùng chung 1 clientInfo dẫn đến thông tin bị loạn. Nên không dùng, chỉ dùng self.clientInfo (Instance Attribute đã khai báo trong __init__)

    def __init__(self, clientInfo):
        """
        Giải thích:
            - seqCounter là biến đếm tăng dần cho mỗi RTP Packet được gửi đi, không phải theo Frame. Lý do: Frame sẽ bị cắt thành các chunk và đóng gói thành Packet (ví dụ: 1 Frame có 3 mảnh) nếu lúc này tính theo Frame thì 3 mảnh đều sẽ có seqNum=1 -> Client không biết mảnh nào đến trước/sau.
        """
        self.clientInfo = clientInfo
        self.seqCounter = 0

    def run(self):
        threading.Thread(target=self.recvRtspRequest).start()

    def recvRtspRequest(self):
        """Receive RTSP request from the client."""
        connSocket = self.clientInfo["rtspSocket"][0]
        while True:
            data = connSocket.recv(256)
            if data:
                print("Data received:\n" + data.decode("utf-8"))
                self.processRtspRequest(data.decode("utf-8"))

    def processRtspRequest(self, data):
        """Process RTSP request sent from the client."""
        # Get the request type
        request = data.split("\n")
        line1 = request[0].split(" ")
        requestType = line1[0]

        # Get the media file name
        filename = line1[1]

        # Get the RTSP sequence number
        seq = request[1].split(" ")

        # Process SETUP request
        if requestType == self.SETUP:
            if self.state == self.INIT:
                # Update state
                print("processing SETUP\n")

                try:
                    self.clientInfo["videoStream"] = VideoStream(filename)
                    self.state = self.READY
                except IOError:
                    self.replyRtsp(self.FILE_NOT_FOUND_404, seq[1])

                # Generate a randomized RTSP session ID
                self.clientInfo["session"] = randint(100000, 999999)

                # Send RTSP reply
                self.replyRtsp(self.OK_200, seq[1])

                # Get the RTP/UDP port from the last line
                self.clientInfo["rtpPort"] = request[2].split(" ")[3]

        # Process PLAY request
        elif requestType == self.PLAY:
            if self.state == self.READY:
                print("processing PLAY\n")
                self.state = self.PLAYING

                # Create a new socket for RTP/UDP
                self.clientInfo["rtpSocket"] = socket.socket(
                    socket.AF_INET, socket.SOCK_DGRAM
                )

                self.replyRtsp(self.OK_200, seq[1])

                # Create a new thread and start sending RTP packets
                self.clientInfo["event"] = threading.Event()
                self.clientInfo["worker"] = threading.Thread(target=self.sendRtp)
                self.clientInfo["worker"].start()

        # Process PAUSE request
        elif requestType == self.PAUSE:
            if self.state == self.PLAYING:
                print("processing PAUSE\n")
                self.state = self.READY

                self.clientInfo["event"].set()

                self.replyRtsp(self.OK_200, seq[1])

        # Process TEARDOWN request
        elif requestType == self.TEARDOWN:
            print("processing TEARDOWN\n")

            self.clientInfo["event"].set()

            self.replyRtsp(self.OK_200, seq[1])

            # Close the RTP socket
            self.clientInfo["rtpSocket"].close()

    def sendRtp(self):
        """Send RTP packets over UDP."""
        while True:
            self.clientInfo["event"].wait(0.05)

            # Stop sending if request is PAUSE or TEARDOWN
            if self.clientInfo["event"].isSet():
                break

            data = self.clientInfo["videoStream"].nextFrame()

            if data:
                try:
                    address = self.clientInfo["rtspSocket"][1][0]
                    port = int(self.clientInfo["rtpPort"])

                    # Fragmentation
                    mv = memoryview(data)  # Zero-copy, không cấp phát bộ nhớ mới
                    total = len(mv)
                    pos = 0

                    while pos < total:
                        # Cắt chunk <= MTU_PAYLOAD_SIZE
                        chunk = bytes(mv[pos : pos + self.MTU_PAYLOAD_SIZE])
                        pos += self.MTU_PAYLOAD_SIZE

                        # Mảnh cuối cùng của frame thì marker=1
                        marker = 1 if pos >= total else 0

                        # Gửi Packet với seqCounter++
                        packet = self.makeRtp(chunk, self.seqCounter, marker)
                        self.clientInfo["rtpSocket"].sendto(packet, (address, port))
                        self.seqCounter += 1

                except Exception as _:
                    print("Connection Error")
                    print("-" * 60)
                    traceback.print_exc(file=sys.stdout)
                    print("-" * 60)
            else:
                print("End of video stream.")
                self.clientInfo["event"].set()  # Ngắt luồng
                break

    def makeRtp(self, payload, frameNbr, marker=0):
        """RTP-packetize the video data."""
        version = 2
        padding = 0
        extension = 0
        cc = 0
        pt = 26  # MJPEG type
        seqnum = frameNbr
        ssrc = 0

        rtpPacket = RtpPacket()

        rtpPacket.encode(
            version, padding, extension, cc, seqnum, marker, pt, ssrc, payload
        )

        return rtpPacket.getPacket()

    def replyRtsp(self, code, seq):
        """Send RTSP reply to the client."""
        if code == self.OK_200:
            # print("200 OK")
            reply = (
                "RTSP/1.0 200 OK\nCSeq: "
                + seq
                + "\nSession: "
                + str(self.clientInfo["session"])
            )
            connSocket = self.clientInfo["rtspSocket"][0]
            connSocket.send(reply.encode())

        # Error messages
        elif code == self.FILE_NOT_FOUND_404:
            print("404 NOT FOUND")
        elif code == self.CON_ERR_500:
            print("500 CONNECTION ERROR")
