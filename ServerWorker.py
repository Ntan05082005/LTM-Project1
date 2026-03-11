import socket
import threading
import time
from random import randint

from RtpPacket import RtpPacket
from VideoStream import VideoStream

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
            print(f"[RTSP] Reply error: {e}")import socket
import threading
import time
from random import randint

from RtpPacket import RtpPacket
from VideoStream import VideoStream

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
