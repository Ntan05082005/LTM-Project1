import socket
<<<<<<< HEAD
import threading
import time
=======
import sys
import threading
import traceback
>>>>>>> e7f86b67fee3c32b1bfd96d3669f6f90b7113bb7
from random import randint

from RtpPacket import RtpPacket
from VideoStream import VideoStream

<<<<<<< HEAD
OK_200 = 0
FILE_NOT_FOUND_404 = 1
CON_ERR_500 = 2

INIT = 0
READY = 1
PLAYING = 2

TRANSPORT_UDP = "UDP"
TRANSPORT_TCP = "TCP"

MTU = 1400


class ServerWorker:
    SSRC = 12345678

    def __init__(self, clientInfo):
        self.clientInfo = clientInfo
        self.state = INIT
        self.sessionId = 0
        self.rtspSeq = 0
        self.videoStream = None
        self.transportMode = TRANSPORT_UDP
        self.clientRtpPort = None
        self.udpSocket = None
        self.tcpSocket = None
        self.sendRtpThread = None
        self.event = threading.Event()

    def run(self):
        threading.Thread(target=self.recvRtspRequest, daemon=True).start()

    def recvRtspRequest(self):
        connSocket = self.clientInfo['rtspSocket'][0]
        while True:
            try:
                data = connSocket.recv(4096).decode('utf-8', errors='ignore')
                if data:
                    print(f"[RTSP] Received:\n{data}")
                    self.processRtspRequest(data)
                else:
                    break
            except Exception as e:
                print(f"[RTSP] Connection closed: {e}")
                break

    def processRtspRequest(self, data):
        lines = data.strip().split('\n')
        if not lines:
            return
        requestType = lines[0].split(' ')[0]
        filename = lines[0].split(' ')[1]
        self.rtspSeq = 0
        for line in lines:
            if 'CSeq' in line:
                self.rtspSeq = int(line.split(':')[1].strip())
                break
        if requestType == 'SETUP':
            self.handleSetup(filename, lines)
        elif requestType == 'PLAY':
            self.handlePlay()
        elif requestType == 'PAUSE':
            self.handlePause()
        elif requestType == 'TEARDOWN':
            self.handleTeardown()

    def handleSetup(self, filename, lines):
        if self.state == INIT:
            self._stopStreaming()
            self._closeSockets()
            self.event = threading.Event()

            transportLine = ""
            for line in lines:
                if 'Transport' in line:
                    transportLine = line
                    break

            self.transportMode = TRANSPORT_TCP if 'TCP' in transportLine.upper() else TRANSPORT_UDP

            self.clientRtpPort = None
            for part in transportLine.split(';'):
                if 'client_port' in part:
                    try:
                        self.clientRtpPort = int(part.split('=')[1].strip())
                    except Exception:
                        pass
                    break

            try:
                self.videoStream = VideoStream(filename)
                self.sessionId = randint(10000, 99999)
                self.state = READY
                self.sendRtspReply(OK_200)
                print(f"[SETUP] File: {filename}, Transport: {self.transportMode}, Port: {self.clientRtpPort}")
            except IOError:
                self.sendRtspReply(FILE_NOT_FOUND_404)

    def handlePlay(self):
        if self.state == READY:
            self.state = PLAYING
            self.sendRtspReply(OK_200)
            self.event.clear()
            if self._openRtpSocket():
                self.sendRtpThread = threading.Thread(target=self.sendRtp, daemon=True)
                self.sendRtpThread.start()
            else:
                print("[PLAY] Failed to open RTP socket")
                self.state = READY

    def handlePause(self):
        if self.state == PLAYING:
            self.state = READY
            self.event.set()
            self.sendRtspReply(OK_200)

    def handleTeardown(self):
        self.event.set()
        self.state = INIT
        self.sendRtspReply(OK_200)
        self._closeSockets()

    def _openRtpSocket(self):
        clientAddr = self.clientInfo['rtspSocket'][1][0]
        if self.transportMode == TRANSPORT_TCP:
            for attempt in range(10):
                try:
                    self.tcpSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    self.tcpSocket.settimeout(2.0)
                    self.tcpSocket.connect((clientAddr, self.clientRtpPort))
                    self.tcpSocket.settimeout(None)
                    print(f"[RTP] TCP connected to {clientAddr}:{self.clientRtpPort}")
                    return True
                except Exception as e:
                    print(f"[RTP] TCP attempt {attempt+1} failed: {e}")
                    try:
                        self.tcpSocket.close()
                    except Exception:
                        pass
                    self.tcpSocket = None
                    time.sleep(0.3)
            return False
        else:
            self.udpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            print(f"[RTP] UDP ready, sending to {clientAddr}:{self.clientRtpPort}")
            return True

    def _stopStreaming(self):
        self.event.set()
        if self.sendRtpThread and self.sendRtpThread.is_alive():
            self.sendRtpThread.join(timeout=1.0)
        self.sendRtpThread = None

    def _closeSockets(self):
        for s in (self.udpSocket, self.tcpSocket):
            if s:
                try:
                    s.close()
                except Exception:
                    pass
        self.udpSocket = None
        self.tcpSocket = None

    def sendRtp(self):
        clientAddr = self.clientInfo['rtspSocket'][1][0]
        while True:
            self.event.wait(0.05)
            if self.event.is_set():
                break
            if self.state != PLAYING:
                break
            data = self.videoStream.nextFrame()
            if data is None:
                print("[RTP] End of video stream.")
                break
            frameNbr = self.videoStream.frameNbr()
            self._sendFrame(data, frameNbr, clientAddr)

    def _sendFrame(self, data, frameNbr, clientAddr):
        mv = memoryview(data)
        total = len(mv)
        pos = 0
        while pos < total:
            chunk = bytes(mv[pos:pos + MTU])
            pos += MTU
            marker = 1 if pos >= total else 0
            pkt = RtpPacket()
            pkt.encode(
                version=2, padding=0, extension=0, cc=0,
                seqnum=frameNbr, marker=marker,
                pt=26, ssrc=self.SSRC, payload=chunk
            )
            raw = pkt.getPacket()
            try:
                if self.transportMode == TRANSPORT_TCP and self.tcpSocket:
                    self.tcpSocket.sendall(len(raw).to_bytes(4, 'big') + raw)
                elif self.transportMode == TRANSPORT_UDP and self.udpSocket:
                    self.udpSocket.sendto(raw, (clientAddr, self.clientRtpPort))
            except Exception as e:
                print(f"[RTP] Send error: {e}")
                break

    def sendRtspReply(self, code):
        reply = ''
        if code == OK_200:
            reply = f"RTSP/1.0 200 OK\nCSeq: {self.rtspSeq}\nSession: {self.sessionId}\n"
        elif code == FILE_NOT_FOUND_404:
            reply = f"RTSP/1.0 404 File Not Found\nCSeq: {self.rtspSeq}\n"
        elif code == CON_ERR_500:
            reply = f"RTSP/1.0 500 Connection Error\nCSeq: {self.rtspSeq}\n"
        print(f"[RTSP] Sending reply:\n{reply}")
        try:
            self.clientInfo['rtspSocket'][0].send(reply.encode())
        except Exception as e:
            print(f"[RTSP] Reply error: {e}")
=======

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
>>>>>>> e7f86b67fee3c32b1bfd96d3669f6f90b7113bb7
