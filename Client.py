from tkinter import *
import tkinter.messagebox
from PIL import Image, ImageTk
<< << << < HEAD
import socket, threading, os, io, time
from collections import deque
== == == =
import socket, threading, sys, traceback, os
>> >> >> > e7f86b67fee3c32b1bfd96d3669f6f90b7113bb7

from RtpPacket import RtpPacket

CACHE_FILE_NAME = "cache-"
CACHE_FILE_EXT = ".jpg"
<< << << < HEAD
BUFFER_SIZE = 10  # Số frame pre-buffer trước khi phát


class Client:
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3

    def __init__(self, master, serveraddr, serverport, rtpport, filename):
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.fileName = filename
        self.rtspSeq = 0
        self.sessionId = 0
        self.requestSent = -1
        self.teardownAcked = 0
        self.frameNbr = -1

        # HD flag – mặc định SD (UDP)
        self.useHD = False

        # Frame buffer cho client-side caching
        self.frameBuffer = deque()
        self.bufferLock = threading.Lock()
        self.bufferReady = threading.Event()
        self.bufferFilled = False

        # Sockets
        self.rtpSocket = None  # UDP socket
        self.rtpTcpServer = None  # TCP listen socket
        self.rtpTcpConn = None  # TCP accepted connection

        # Fragment reassembly buffer
        self.fragmentBuffer = bytearray()

        self.connectToServer()
        self.createWidgets()

    # ──────────────────────────── GUI ────────────────────────────

    def createWidgets(self):
        """Tạo giao diện."""
        self.label = Label(self.master, height=19, bg="black")
        self.label.grid(row=0, column=0, columnspan=5, sticky=W + E + N + S, padx=5, pady=5)

        btn_cfg = dict(width=14, padx=3, pady=3)

        self.setupBtn = Button(self.master, text="Setup", command=self.setupMovie, **btn_cfg)
        self.setupBtn.grid(row=1, column=0, padx=2, pady=2)

        self.playBtn = Button(self.master, text="Play", command=self.playMovie, **btn_cfg)
        self.playBtn.grid(row=1, column=1, padx=2, pady=2)

        self.pauseBtn = Button(self.master, text="Pause", command=self.pauseMovie, **btn_cfg)
        self.pauseBtn.grid(row=1, column=2, padx=2, pady=2)

        self.teardownBtn = Button(self.master, text="Teardown", command=self.exitClient, **btn_cfg)
        self.teardownBtn.grid(row=1, column=3, padx=2, pady=2)

        # Radio buttons chọn chất lượng
        self.hdVar = IntVar(value=0)
        qf = Frame(self.master)
        qf.grid(row=1, column=4, padx=6)
        Radiobutton(qf, text="SD-540", variable=self.hdVar, value=0, command=self.onQualityChange).pack(anchor=W)
        Radiobutton(qf, text="HD-720", variable=self.hdVar, value=1, command=self.onQualityChange).pack(anchor=W)
        Radiobutton(qf, text="HD-1080", variable=self.hdVar, value=2, command=self.onQualityChange).pack(anchor=W)

        self.statusVar = StringVar(value="Connected")
        Label(self.master, textvariable=self.statusVar, relief=SUNKEN, anchor=W).grid(
            row=2, column=0, columnspan=5, sticky=W + E, padx=5, pady=2)

    def onQualityChange(self):
        """Xử lý khi đổi quality."""
        self.useHD = self.hdVar.get() > 0
        labels = ["SD-540 (UDP)", "HD-720 (TCP)", "HD-1080 (TCP)"]
        self.statusVar.set(f"Quality: {labels[self.hdVar.get()]}")

    # ─────────────────── Button Handlers ───────────────────

    def setupMovie(self):
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)

    def exitClient(self):
        """Teardown: kết thúc session, reset về INIT để Setup lại."""
        if self.state != self.INIT:
            if hasattr(self, 'playEvent'):
                self.playEvent.set()
            self.sendRtspRequest(self.TEARDOWN)
            time.sleep(0.2)  # Chờ server nhận TEARDOWN
        # Đóng RTSP socket cũ
        try:
            self.rtspSocket.close()
        except Exception:
            pass
        # Reset state
        self.state = self.INIT
        self.sessionId = 0
        self.rtspSeq = 0
        self.frameNbr = -1
        self.fragmentBuffer = bytearray()
        self.frameBuffer.clear()
        self.bufferFilled = False
        self._closeRtpSockets()
        # Kết nối lại RTSP socket mới cho session tiếp theo
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
            self.statusVar.set("Session ended. Select quality and press Setup.")
        except Exception:
            self.statusVar.set("Reconnect failed. Restart server.")

    def closeApp(self):
        """Đóng hoàn toàn app — gọi khi người dùng đóng cửa sổ."""
        if self.state != self.INIT:
            if hasattr(self, 'playEvent'):
                self.playEvent.set()
            self.sendRtspRequest(self.TEARDOWN)
        self.master.destroy()

    def pauseMovie(self):
        if self.state == self.PLAYING:
            self.sendRtspRequest(self.PAUSE)

    def playMovie(self):
        if self.state == self.READY:
            self.playEvent = threading.Event()
            self.playEvent.clear()
            self.bufferFilled = False
            self.bufferReady.clear()
            # Start listen thread TRƯỚC khi gửi PLAY
            threading.Thread(target=self.listenRtp, daemon=True).start()
            threading.Thread(target=self.playFromBuffer, daemon=True).start()
            self.sendRtspRequest(self.PLAY)

    # ─────────────────── RTP Receiving ───────────────────

    def listenRtp(self):
        """Nhận RTP packets và đẩy vào frame buffer."""
        # Chờ socket sẵn sàng (tránh race condition với openRtpPort)
        for _ in range(40):  # chờ tối đa 2 giây
            if self.rtpSocket or self.rtpTcpConn:
                break
            time.sleep(0.05)
        else:
            print("[listenRtp] Timeout waiting for RTP socket!")
            return

        # Reset fragment buffer khi bắt đầu session mới
        self.fragmentBuffer = bytearray()
        print(f"[listenRtp] Socket ready, starting receive loop")
        while True:
            try:
                data = self._recvRtpPacket()
                if not data:
                    continue

                rtpPacket = RtpPacket()
                rtpPacket.decode(data)

                # Ghép mảnh (fragmentation reassembly)
                self.fragmentBuffer += rtpPacket.getPayload()

                if rtpPacket.marker() == 1:
                    # Marker=1 → mảnh cuối → frame hoàn chỉnh
                    frame = bytes(self.fragmentBuffer)
                    self.fragmentBuffer = bytearray()

                    currSeq = rtpPacket.seqNum()
                    if currSeq > self.frameNbr:
                        self.frameNbr = currSeq
                        with self.bufferLock:
                            self.frameBuffer.append(frame)
                            if not self.bufferFilled and len(self.frameBuffer) >= BUFFER_SIZE:
                                self.bufferFilled = True
                                self.bufferReady.set()

            except Exception:
                if self.playEvent.isSet():
                    break
                if self.teardownAcked == 1:
                    self._closeRtpSockets()
                    break

    def _recvRtpPacket(self):
        """Đọc 1 RTP packet thô (UDP hoặc TCP)."""
        if self.useHD and self.rtpTcpConn:
            try:
                raw_len = self._tcpRecvAll(self.rtpTcpConn, 4)
                if not raw_len:
                    return None
                length = int.from_bytes(raw_len, "big")
                return self._tcpRecvAll(self.rtpTcpConn, length)
            except Exception:
                return None
        elif self.rtpSocket:
            return self.rtpSocket.recv(65535)
        return None

    def _tcpRecvAll(self, sock, n):
        """Đọc đúng n bytes từ TCP socket."""
        data = b""
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    # ─────────────────── Buffer & Display ───────────────────

    def playFromBuffer(self):
        """Chờ đủ buffer rồi phát frame liên tục."""
        self.statusVar.set(f"Buffering... 0/{BUFFER_SIZE} frames")

        # Poll buffer count trong khi chờ
        while not self.bufferFilled and not self.playEvent.isSet():
            with self.bufferLock:
                count = len(self.frameBuffer)
            self.statusVar.set(f"Buffering... {count}/{BUFFER_SIZE} frames")
            time.sleep(0.1)

        if self.playEvent.isSet():
            return

        self.statusVar.set("Playing")

        while not self.playEvent.isSet():
            frame_data = None
            with self.bufferLock:
                if self.frameBuffer:
                    frame_data = self.frameBuffer.popleft()

            if frame_data:
                self._showFrame(frame_data)
                time.sleep(0.04)  # ~25 fps
            else:
                time.sleep(0.01)

    def _showFrame(self, data):
        """Decode JPEG bytes và cập nhật GUI."""
        try:
            img = Image.open(io.BytesIO(data))
            photo = ImageTk.PhotoImage(img)
            self.label.configure(image=photo, height=288)
            self.label.image = photo
        except Exception as e:
            print(f"Frame display error: {e}")

    def writeFrame(self, data):
        cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
        with open(cachename, "wb") as f:
            f.write(data)
        return cachename

    def updateMovie(self, imageFile):
        photo = ImageTk.PhotoImage(Image.open(imageFile))
        self.label.configure(image=photo, height=288)
        self.label.image = photo

    # ─────────────────── RTSP ───────────────────

    def connectToServer(self):
        """Mở kết nối TCP đến RTSP server."""
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
        except Exception:
            tkinter.messagebox.showwarning(
                "Connection Failed", f"Connection to '{self.serverAddr}' failed.")

    def sendRtspRequest(self, requestCode):
        """Build và gửi RTSP request."""
        if requestCode == self.SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply, daemon=True).start()
            self.rtspSeq += 1
            transport = "RTP/TCP" if self.useHD else "RTP/UDP"
            request = (f"SETUP {self.fileName} RTSP/1.0\n"
                       f"CSeq: {self.rtspSeq}\n"
                       f"Transport: {transport}; client_port= {self.rtpPort}")
            self.requestSent = self.SETUP

        elif requestCode == self.PLAY and self.state == self.READY:
            self.rtspSeq += 1
            request = (f"PLAY {self.fileName} RTSP/1.0\n"
                       f"CSeq: {self.rtspSeq}\n"
                       f"Session: {self.sessionId}")
            self.requestSent = self.PLAY

        elif requestCode == self.PAUSE and self.state == self.PLAYING:
            self.rtspSeq += 1
            request = (f"PAUSE {self.fileName} RTSP/1.0\n"
                       f"CSeq: {self.rtspSeq}\n"
                       f"Session: {self.sessionId}")
            self.requestSent = self.PAUSE

        elif requestCode == self.TEARDOWN and self.state != self.INIT:
            self.rtspSeq += 1
            request = (f"TEARDOWN {self.fileName} RTSP/1.0\n"
                       f"CSeq: {self.rtspSeq}\n"
                       f"Session: {self.sessionId}")
            self.requestSent = self.TEARDOWN
        else:
            return

        self.rtspSocket.send(request.encode())
        print(f"\nData sent:\n{request}")

    def recvRtspReply(self):
        """Nhận RTSP reply liên tục trong thread riêng."""
        while True:
            try:
                reply = self.rtspSocket.recv(1024)
                if reply:
                    self.parseRtspReply(reply.decode("utf-8"))
                if self.requestSent == self.TEARDOWN:
                    break
            except Exception:
                break

    def parseRtspReply(self, data):
        """Parse RTSP reply và cập nhật state machine."""
        lines = data.split("\n")
        seqNum = int(lines[1].split(" ")[1])

        if seqNum == self.rtspSeq:
            session = int(lines[2].split(" ")[1])
            if self.sessionId == 0:
                self.sessionId = session

            if self.sessionId == session:
                if int(lines[0].split(" ")[1]) == 200:
                    if self.requestSent == self.SETUP:
                        self.state = self.READY
                        self.statusVar.set(f"SETUP OK  |  Session: {self.sessionId}")
                        self.openRtpPort()
                        time.sleep(0.1)  # Đợi socket bind xong

                    elif self.requestSent == self.PLAY:
                        self.state = self.PLAYING

                    elif self.requestSent == self.PAUSE:
                        self.state = self.READY
                        self.playEvent.set()

                    elif self.requestSent == self.TEARDOWN:
                        self.state = self.INIT
                        self.teardownAcked = 1

    # ─────────────────── RTP Socket Management ───────────────────

    def openRtpPort(self):
        """Mở RTP socket phù hợp với transport mode."""
        self._closeRtpSockets()

        if self.useHD:
            # TCP mode: mở server socket, đợi server connect vào
            self.rtpTcpServer = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.rtpTcpServer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                self.rtpTcpServer.bind(("", self.rtpPort))
                self.rtpTcpServer.listen(1)
                threading.Thread(target=self._acceptTcpRtp, daemon=True).start()
                print(f"TCP RTP listening on port {self.rtpPort}")
            except Exception as e:
                tkinter.messagebox.showwarning("Unable to Bind",
                                               f"Unable to bind TCP PORT={self.rtpPort}\n{e}")
        else:
            # UDP mode
            self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.rtpSocket.settimeout(0.5)
            try:
                self.rtpSocket.bind(("", self.rtpPort))
                print(f"UDP RTP bound on port {self.rtpPort}")
            except Exception:
                tkinter.messagebox.showwarning("Unable to Bind",
                                               f"Unable to bind UDP PORT={self.rtpPort}")

    def _acceptTcpRtp(self):
        """Chấp nhận kết nối TCP RTP từ server."""
        try:
            conn, addr = self.rtpTcpServer.accept()
            self.rtpTcpConn = conn
            self.rtpTcpConn.settimeout(0.5)
            print(f"TCP RTP accepted from {addr}")
        except Exception as e:
            print(f"TCP RTP accept error: {e}")

    def _closeRtpSockets(self):
        for s in (self.rtpSocket, self.rtpTcpConn, self.rtpTcpServer):
            if s:
                try:
                    s.close()
                except Exception:
                    pass
        self.rtpSocket = None
        self.rtpTcpConn = None
        self.rtpTcpServer = None

    def handler(self):
        """Handler khi đóng cửa sổ GUI."""
        self.pauseMovie()
        if tkinter.messagebox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.closeApp()
        else:
            self.playMovie()

== == == =

class Client:
    INIT = 0
    READY = 1
    PLAYING = 2
    state = INIT

    SETUP = 0
    PLAY = 1
    PAUSE = 2
    TEARDOWN = 3

    # Initiation..
    def __init__(self, master, serveraddr, serverport, rtpport, filename):
        self.master = master
        self.master.protocol("WM_DELETE_WINDOW", self.handler)
        self.createWidgets()
        self.serverAddr = serveraddr
        self.serverPort = int(serverport)
        self.rtpPort = int(rtpport)
        self.fileName = filename
        self.rtspSeq = 0
        self.sessionId = 0
        self.requestSent = -1
        self.teardownAcked = 0
        self.connectToServer()
        self.frameNbr = 0

    def createWidgets(self):
        """Build GUI."""
        # Create Setup button
        self.setup = Button(self.master, width=20, padx=3, pady=3)
        self.setup["text"] = "Setup"
        self.setup["command"] = self.setupMovie
        self.setup.grid(row=1, column=0, padx=2, pady=2)

        # Create Play button		
        self.start = Button(self.master, width=20, padx=3, pady=3)
        self.start["text"] = "Play"
        self.start["command"] = self.playMovie
        self.start.grid(row=1, column=1, padx=2, pady=2)

        # Create Pause button			
        self.pause = Button(self.master, width=20, padx=3, pady=3)
        self.pause["text"] = "Pause"
        self.pause["command"] = self.pauseMovie
        self.pause.grid(row=1, column=2, padx=2, pady=2)

        # Create Teardown button
        self.teardown = Button(self.master, width=20, padx=3, pady=3)
        self.teardown["text"] = "Teardown"
        self.teardown["command"] = self.exitClient
        self.teardown.grid(row=1, column=3, padx=2, pady=2)

        # Create a label to display the movie
        self.label = Label(self.master, height=19)
        self.label.grid(row=0, column=0, columnspan=4, sticky=W + E + N + S, padx=5, pady=5)

    def setupMovie(self):
        """Setup button handler."""
        if self.state == self.INIT:
            self.sendRtspRequest(self.SETUP)

    def exitClient(self):
        """Teardown button handler."""
        self.sendRtspRequest(self.TEARDOWN)
        self.master.destroy()  # Close the gui window
        os.remove(CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT)  # Delete the cache image from video

    def pauseMovie(self):
        """Pause button handler."""
        if self.state == self.PLAYING:
            self.sendRtspRequest(self.PAUSE)

    def playMovie(self):
        """Play button handler."""
        if self.state == self.READY:
            # Create a new thread to listen for RTP packets
            threading.Thread(target=self.listenRtp).start()
            self.playEvent = threading.Event()
            self.playEvent.clear()
            self.sendRtspRequest(self.PLAY)

    def listenRtp(self):
        """Listen for RTP packets."""
        while True:
            try:
                data = self.rtpSocket.recv(20480)
                if data:
                    rtpPacket = RtpPacket()
                    rtpPacket.decode(data)

                    currFrameNbr = rtpPacket.seqNum()
                    print("Current Seq Num: " + str(currFrameNbr))

                    if currFrameNbr > self.frameNbr:  # Discard the late packet
                        self.frameNbr = currFrameNbr
                        self.updateMovie(self.writeFrame(rtpPacket.getPayload()))
            except:
                # Stop listening upon requesting PAUSE or TEARDOWN
                if self.playEvent.isSet():
                    break

                # Upon receiving ACK for TEARDOWN request,
                # close the RTP socket
                if self.teardownAcked == 1:
                    self.rtpSocket.shutdown(socket.SHUT_RDWR)
                    self.rtpSocket.close()
                    break

    def writeFrame(self, data):
        """Write the received frame to a temp image file. Return the image file."""
        cachename = CACHE_FILE_NAME + str(self.sessionId) + CACHE_FILE_EXT
        file = open(cachename, "wb")
        file.write(data)
        file.close()

        return cachename

    def updateMovie(self, imageFile):
        """Update the image file as video frame in the GUI."""
        photo = ImageTk.PhotoImage(Image.open(imageFile))
        self.label.configure(image=photo, height=288)
        self.label.image = photo

    def connectToServer(self):
        """Connect to the Server. Start a new RTSP/TCP session."""
        self.rtspSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.rtspSocket.connect((self.serverAddr, self.serverPort))
        except:
            tkMessageBox.showwarning('Connection Failed', 'Connection to \'%s\' failed.' % self.serverAddr)

    def sendRtspRequest(self, requestCode):
        """Send RTSP request to the server."""
        # -------------
        # TO COMPLETE
        # -------------

        # Setup request
        if requestCode == self.SETUP and self.state == self.INIT:
            threading.Thread(target=self.recvRtspReply).start()
            # Update RTSP sequence number.
            self.rtspSeq += 1

            if self.useHD:
                transport = "RTP/TCP"  # TCP for HD streaming
            else:
                transport = "RTP/UDP"  # UDP for SD streaming

            # Write the RTSP request to be sent.
            request = "SETUP " + self.fileName + " RTSP/1.0\nCSeq: " + str(
                self.rtspSeq) + "\nTransport: " + transport + "; client_port= " + str(self.rtpPort)

            # Keep track of the sent request.
            self.requestSent = self.SETUP

        # Play request
        elif requestCode == self.PLAY and self.state == self.READY:
            # Update RTSP sequence number.
            self.rtspSeq += 1

            # Write the RTSP request to be sent.
            request = "PLAY " + self.fileName + " RTSP/1.0\nCSeq: " + str(self.rtspSeq) + "\nSession: " + str(
                self.sessionId)

            # Keep track of the sent request.
            self.requestSent = self.PLAY


        # Pause request
        elif requestCode == self.PAUSE and self.state == self.PLAYING:
            # Update RTSP sequence number.
            self.rtspSeq += 1

            # Write the RTSP request to be sent.
            request = "PAUSE " + self.fileName + " RTSP/1.0\nCSeq: " + str(self.rtspSeq) + "\nSession: " + str(
                self.sessionId)

            # Keep track of the sent request.
            self.requestSent = self.PAUSE
        # self.requestSent = ...

        # Teardown request
        elif requestCode == self.TEARDOWN and not self.state == self.INIT:
            # Update RTSP sequence number.
            self.rtspSeq += 1

            # Write the RTSP request to be sent.
            request = "TEARDOWN " + self.fileName + " RTSP/1.0\nCSeq: " + str(self.rtspSeq) + "\nSession: " + str(
                self.sessionId)

            # Keep track of the sent request.
            self.requestSent = self.TEARDOWN
        else:
            return

        # Send the RTSP request using rtspSocket.
        self.rtspSocket.send(request.encode())

        print('\nData sent:\n' + request)

    def recvRtspReply(self):
        """Receive RTSP reply from the server."""
        while True:
            reply = self.rtspSocket.recv(1024)

            if reply:
                self.parseRtspReply(reply.decode("utf-8"))

            # Close the RTSP socket upon requesting Teardown
            if self.requestSent == self.TEARDOWN:
                self.rtspSocket.shutdown(socket.SHUT_RDWR)
                self.rtspSocket.close()
                break

    def parseRtspReply(self, data):
        """Parse the RTSP reply from the server."""
        lines = data.split('\n')
        seqNum = int(lines[1].split(' ')[1])

        # Process only if the server reply's sequence number is the same as the request's
        if seqNum == self.rtspSeq:
            session = int(lines[2].split(' ')[1])
            # New RTSP session ID
            if self.sessionId == 0:
                self.sessionId = session

            # Process only if the session ID is the same
            if self.sessionId == session:
                if int(lines[0].split(' ')[1]) == 200:
                    if self.requestSent == self.SETUP:
                        # -------------
                        # TO COMPLETE
                        # -------------
                        # Update RTSP state.
                        self.state = self.READY

                        # Open RTP port.
                        self.openRtpPort()
                    elif self.requestSent == self.PLAY:
                        self.state = self.PLAYING
                    elif self.requestSent == self.PAUSE:
                        self.state = self.READY

                        # The play thread exits. A new thread is created on resume.
                        self.playEvent.set()
                    elif self.requestSent == self.TEARDOWN:
                        self.state = self.INIT

                        # Flag the teardownAcked to close the socket.
                        self.teardownAcked = 1

    def openRtpPort(self):
        """Open RTP socket binded to a specified port."""
        # -------------
        # TO COMPLETE
        # -------------

        # Switch to TCP socket for HD streaming
        if self.useHD:
            self.rtpTcpSocket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.rtpTcpSocket.settimeout(0.5)
            try:
                self.rtpTcpSocket.connect((self.serverAddr, self.rtpPort))
            except Exception as e:
                print("TCP connection failed, falling back to UDP: " + str(e))
                self.useHD = False
                self.hdVar.set(0)

        # Create a new datagram socket to receive RTP packets from the server
        self.rtpSocket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Set the timeout value of the socket to 0.5sec
        self.rtpSocket.settimeout(0.5)

        try:
            # Bind the socket to the address using the RTP port given by the client user
            self.rtpSocket.bind(('', self.rtpPort if not self.useHD else self.rtpPort + 1))
        except:
            tkMessageBox.showwarning('Unable to Bind', 'Unable to bind PORT=%d' % self.rtpPort)

    def handler(self):
        """Handler on explicitly closing the GUI window."""
        self.pauseMovie()
        if tkMessageBox.askokcancel("Quit?", "Are you sure you want to quit?"):
            self.exitClient()
        else:  # When the user presses cancel, resume playing.
            self.playMovie()

>> >> >> > e7f86b67fee3c32b1bfd96d3669f6f90b7113bb7
