"""A tiny SMTP server on 127.0.0.1 for mailer tests. Scriptable failures; records what it received. No real SMTP."""
from __future__ import annotations

import base64
import socketserver
import threading
import time
from email import message_from_bytes, policy


class Sink:
    def __init__(self) -> None:
        self.messages: list[tuple[str, list[str], bytes]] = []
        self.logins: list[tuple[str, str]] = []
        self.auth_ok = True
        self.mail_reply = "250 OK"
        self.rcpt_reply: dict[str, str] = {}
        self.data_cmd_reply = "354 go ahead"
        self.final_reply = "250 2.0.0 OK queued"
        self.drop_after_data = False  # close the socket instead of answering the final reply
        self.hang_after_data = 0.0  # seconds to stay silent after the message text
        self._srv: socketserver.ThreadingTCPServer | None = None

    def start(self) -> "Sink":
        sink = self

        class H(socketserver.StreamRequestHandler):
            def reply(self, line: str) -> None:
                self.wfile.write((line + "\r\n").encode())
                self.wfile.flush()

            def handle(self) -> None:
                self.reply("220 sink ESMTP")
                sender, rcpts = "", []
                while True:
                    raw = self.rfile.readline()
                    if not raw:
                        return
                    line = raw.decode(errors="replace").strip()
                    cmd = line.upper()
                    if cmd.startswith("EHLO") or cmd.startswith("HELO"):
                        self.wfile.write(b"250-sink\r\n250 AUTH PLAIN LOGIN\r\n")
                        self.wfile.flush()
                    elif cmd.startswith("AUTH PLAIN"):
                        parts = base64.b64decode(line.split()[2]).split(b"\0")
                        sink.logins.append((parts[1].decode(), parts[2].decode()))
                        self.reply("235 2.7.0 Accepted" if sink.auth_ok else "535 5.7.8 Username and Password not accepted")
                    elif cmd.startswith("AUTH LOGIN"):
                        self.reply("334 VXNlcm5hbWU6")
                        user = base64.b64decode(self.rfile.readline().strip()).decode()
                        self.reply("334 UGFzc3dvcmQ6")
                        pw = base64.b64decode(self.rfile.readline().strip()).decode()
                        sink.logins.append((user, pw))
                        self.reply("235 2.7.0 Accepted" if sink.auth_ok else "535 5.7.8 Username and Password not accepted")
                    elif cmd.startswith("MAIL FROM"):
                        sender = line.split(":", 1)[1].strip()
                        self.reply(sink.mail_reply)
                    elif cmd.startswith("RCPT TO"):
                        addr = line.split(":", 1)[1].strip().strip("<>")
                        rep = sink.rcpt_reply.get(addr, "250 OK")
                        if rep.startswith("25"):
                            rcpts.append(addr)
                        self.reply(rep)
                    elif cmd == "DATA":
                        self.reply(sink.data_cmd_reply)
                        if not sink.data_cmd_reply.startswith("354"):
                            continue
                        buf = b""
                        while True:
                            ln = self.rfile.readline()
                            if ln in (b".\r\n", b""):
                                break
                            buf += ln[1:] if ln.startswith(b"..") else ln
                        if sink.drop_after_data:
                            return
                        if sink.hang_after_data:
                            time.sleep(sink.hang_after_data)
                            return
                        if sink.final_reply.startswith("250"):
                            sink.messages.append((sender, list(rcpts), buf))
                        self.reply(sink.final_reply)
                    elif cmd == "QUIT":
                        self.reply("221 bye")
                        return
                    else:
                        self.reply("250 OK")

        self._srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), H)
        self._srv.daemon_threads = True
        threading.Thread(target=self._srv.serve_forever, daemon=True).start()
        return self

    @property
    def port(self) -> int:
        return self._srv.server_address[1]  # type: ignore[union-attr]

    def stop(self) -> None:
        if self._srv:
            self._srv.shutdown()
            self._srv.server_close()

    def parsed(self, i: int = -1):
        return message_from_bytes(self.messages[i][2], policy=policy.default)
