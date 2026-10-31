"""폰 알림과 원격 제어 (텔레그램 / 디스코드 웹훅). config.yaml 의 notify: 에 토큰을 넣어야 동작한다.

  telegram: 봇 토큰 + 내 chat_id. 사진 전송과 원격 명령(/상태 /화면 /일시정지 /재개 /정지)을 지원한다.
            명령은 chat_id가 일치하는 대화에서 온 것만 받는다.
  discord:  웹훅 URL. 알림(사진 포함)만 지원한다.

네트워크 오류가 나도 매크로는 멈추지 않는다 (로그만 남김).
"""
import io
import json
import logging
import threading
import time
import urllib.parse
import urllib.request
import uuid

import cv2

log = logging.getLogger(__name__)


def _post_multipart(url, fields, files, timeout=15):
    boundary = uuid.uuid4().hex
    body = io.BytesIO()
    for k, v in fields.items():
        body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
    for k, (name, data, ctype) in files.items():
        body.write(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{name}\"\r\n"
                   f"Content-Type: {ctype}\r\n\r\n".encode())
        body.write(data)
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(url, data=body.getvalue(),
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _jpeg(frame, width=960):
    h, w = frame.shape[:2]
    if w > width:
        frame = cv2.resize(frame, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return buf.tobytes() if ok else None


class Notifier:
    def __init__(self, cfg):
        cfg = cfg or {}
        self.enabled = bool(cfg.get("enabled"))
        tg = cfg.get("telegram") or {}
        self.tg_token = tg.get("token") or ""
        self.tg_chat = str(tg.get("chat_id") or "")
        self.dc_url = (cfg.get("discord") or {}).get("webhook") or ""
        self.min_gap = float(cfg.get("min_gap", 20))       # 같은 알림 연타 방지 (초)
        self._last = {}
        self._offset = None
        self._thread = None
        self._stop = threading.Event()

    @property
    def ready(self):
        return self.enabled and ((self.tg_token and self.tg_chat) or self.dc_url)

    # ---------- 보내기 ----------
    def send(self, text, frame=None, key=None):
        """알림 전송 (백그라운드). key가 같으면 min_gap 안에 다시 보내지 않는다."""
        if not self.ready:
            return
        k = key or text
        now = time.monotonic()
        if now - self._last.get(k, -1e9) < self.min_gap:
            return
        self._last[k] = now
        img = _jpeg(frame) if frame is not None else None
        threading.Thread(target=self._send, args=(text, img), daemon=True).start()

    def _send(self, text, img):
        text = "[도깨비 매크로] " + text
        try:
            if self.tg_token and self.tg_chat:
                base = f"https://api.telegram.org/bot{self.tg_token}"
                if img:
                    _post_multipart(base + "/sendPhoto", {"chat_id": self.tg_chat, "caption": text[:1000]},
                                    {"photo": ("screen.jpg", img, "image/jpeg")})
                else:
                    data = urllib.parse.urlencode({"chat_id": self.tg_chat, "text": text}).encode()
                    with urllib.request.urlopen(base + "/sendMessage", data=data, timeout=15) as response:
                        response.read()
            if self.dc_url:
                if img:
                    _post_multipart(self.dc_url, {"content": text[:1900]}, {"file": ("screen.jpg", img, "image/jpeg")})
                else:
                    req = urllib.request.Request(self.dc_url, data=json.dumps({"content": text[:1900]}).encode(),
                                                 headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=15) as response:
                        response.read()
        except Exception as e:  # noqa: BLE001
            log.warning("알림 전송 실패: %s", e)

    # ---------- 원격 명령 (텔레그램) ----------
    def start_commands(self, handler):
        """handler(cmd: str) -> (답장 텍스트, frame 또는 None). 텔레그램 설정이 있을 때만 동작."""
        if not (self.enabled and self.tg_token and self.tg_chat) or self._thread:
            return
        self._thread = threading.Thread(target=self._poll, args=(handler,), daemon=True)
        self._thread.start()
        log.info("텔레그램 원격 명령 대기 시작 (/상태 /화면 /일시정지 /재개 /정지)")

    def stop(self):
        self._stop.set()

    def _poll(self, handler):
        base = f"https://api.telegram.org/bot{self.tg_token}"
        while not self._stop.is_set():
            try:
                q = {"timeout": 25}
                if self._offset is not None:
                    q["offset"] = self._offset
                with urllib.request.urlopen(base + "/getUpdates?" + urllib.parse.urlencode(q), timeout=35) as r:
                    data = json.loads(r.read())
                for u in data.get("result", []):
                    self._offset = u["update_id"] + 1
                    msg = u.get("message") or {}
                    if str((msg.get("chat") or {}).get("id")) != self.tg_chat:
                        continue                      # 내 대화가 아닌 곳의 명령은 무시
                    text = (msg.get("text") or "").strip()
                    if not text.startswith("/"):
                        continue
                    try:
                        reply, frame = handler(text.split()[0].lstrip("/").split("@")[0])
                    except Exception as e:  # noqa: BLE001
                        reply, frame = f"명령 처리 오류: {e}", None
                    self._last.pop(reply, None)
                    self._send(reply, _jpeg(frame) if frame is not None else None)
            except Exception as e:  # noqa: BLE001
                log.debug("텔레그램 폴링 오류: %s", e)
                self._stop.wait(10)
