"""Audio engine for MiniDJ: two decks, 3-band EQ, speed, crossfader, live app capture."""

import json
import math
import os
import subprocess
import threading
import time
from collections import deque

import numpy as np

# Name our own playback stream so it shows up as "MiniDJ" and can be excluded from the live-app list.
os.environ.setdefault("PIPEWIRE_PROPS", '{ node.name = "minidj-output" application.name = "MiniDJ" }')
import sounddevice as sd  # noqa: E402
from scipy.signal import sosfilt

SR = 44100
BLOCK = 512
EQ_MIN, EQ_MAX = -26.0, 6.0  # -26 dB acts as a "kill"
SPEED_MIN, SPEED_MAX = 0.5, 1.5
AUDIO_EXTS = {".mp3", ".flac", ".wav", ".ogg", ".oga", ".opus", ".m4a", ".aac", ".aif", ".aiff", ".wma", ".webm", ".mp4"}


# ---------------------------------------------------------------- EQ (RBJ biquads)

def _biquad(kind, f0, gain_db, q=0.707):
    A = 10 ** (gain_db / 40)
    w0 = 2 * math.pi * f0 / SR
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2 * q)
    sa = 2 * math.sqrt(A) * alpha
    if kind == "lowshelf":
        b = [A * ((A + 1) - (A - 1) * cw + sa), 2 * A * ((A - 1) - (A + 1) * cw), A * ((A + 1) - (A - 1) * cw - sa)]
        a = [(A + 1) + (A - 1) * cw + sa, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - sa]
    elif kind == "highshelf":
        b = [A * ((A + 1) + (A - 1) * cw + sa), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - sa)]
        a = [(A + 1) - (A - 1) * cw + sa, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - sa]
    else:  # peaking
        b = [1 + alpha * A, -2 * cw, 1 - alpha * A]
        a = [1 + alpha / A, -2 * cw, 1 - alpha / A]
    return [b[0] / a[0], b[1] / a[0], b[2] / a[0], 1.0, a[1] / a[0], a[2] / a[0]]


def eq_sos(low, mid, high):
    return np.array([
        _biquad("lowshelf", 250, low),
        _biquad("peak", 1000, mid, q=0.6),
        _biquad("highshelf", 3500, high),
    ])


# ---------------------------------------------------------------- live capture of app audio

def _pactl_json(*args):
    out = subprocess.run(["pactl", "-f", "json", *args], capture_output=True, text=True, timeout=3).stdout
    return json.loads(out or "[]")


def list_app_streams():
    """Playback streams of other applications (PipeWire/Pulse sink inputs)."""
    streams = []
    me = str(os.getpid())
    for si in _pactl_json("list", "sink-inputs"):
        props = si.get("properties", {})
        if props.get("application.process.id") == me:
            continue
        if props.get("node.name", "").startswith("minidj"):
            continue
        app = props.get("application.name") or props.get("node.name") or "?"
        media = props.get("media.name", "")
        streams.append({"id": si["index"], "app": app, "media": media, "sink": si.get("sink")})
    return streams


class LiveSource:
    """Reroutes an app's output into a private null sink and captures it for a deck."""

    def __init__(self, stream, letter):
        self.stream = stream
        self.sink_name = f"minidj_deck_{letter.lower()}"
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.max_bytes = SR * 8 // 2  # ~0.5 s of float32 stereo
        self.module = subprocess.run(
            ["pactl", "load-module", "module-null-sink", f"sink_name={self.sink_name}",
             f"sink_properties=device.description=MiniDJ-Deck-{letter}"],
            capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["pactl", "move-sink-input", str(stream["id"]), self.sink_name], check=True)
        self.proc = subprocess.Popen(
            ["parec", "-d", f"{self.sink_name}.monitor", "--format=float32le", f"--rate={SR}",
             "--channels=2", "--raw", "--latency-msec=20"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.alive = True
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self):
        while self.alive:
            chunk = self.proc.stdout.read1(4096) if self.proc.stdout else b""
            if not chunk:
                break
            with self.lock:
                self.buf += chunk
                if len(self.buf) > self.max_bytes:
                    del self.buf[: len(self.buf) - self.max_bytes]

    def read(self, n):
        need = n * 8
        with self.lock:
            take = min(len(self.buf), need) // 8 * 8
            data = bytes(self.buf[:take])
            del self.buf[:take]
        out = np.zeros((n, 2), dtype=np.float32)
        if take:
            out[: take // 8] = np.frombuffer(data, dtype=np.float32).reshape(-1, 2)
        return out

    def close(self):
        self.alive = False
        try:
            self.proc.terminate()
        except Exception:
            pass
        target = str(self.stream["sink"]) if self.stream.get("sink") is not None else "@DEFAULT_SINK@"
        for sink in (target, "@DEFAULT_SINK@"):
            if subprocess.run(["pactl", "move-sink-input", str(self.stream["id"]), sink],
                              capture_output=True).returncode == 0:
                break
        subprocess.run(["pactl", "unload-module", self.module], capture_output=True)


# ---------------------------------------------------------------- deck

class Deck:
    def __init__(self, letter):
        self.letter = letter
        self.audio = None
        self.title = "— empty —"
        self.status = ""
        self.pos = 0.0
        self.cue = 0.0
        self.playing = False
        self.loading = False
        self.volume = 0.8
        self.speed = 1.0
        self.eq = {"high": 0.0, "mid": 0.0, "low": 0.0}
        self.live = None
        self.level = 0.0
        self.spin = 0.0  # rotation in revolutions, drives the vinyl drawing
        self._sos = eq_sos(0, 0, 0)
        self._zi = np.zeros((3, 2, 2))
        self._eq_dirty = False

    # ---- controls
    def set_eq(self, band, db):
        self.eq[band] = float(min(EQ_MAX, max(EQ_MIN, db)))
        self._eq_dirty = True

    def set_volume(self, v):
        self.volume = float(min(1.0, max(0.0, v)))

    def set_speed(self, s):
        self.speed = float(min(SPEED_MAX, max(SPEED_MIN, s)))

    def toggle_play(self):
        if self.live:
            self.playing = not self.playing
        elif self.audio is not None:
            if not self.playing and self.pos >= len(self.audio) - 2:
                self.pos = self.cue
            self.playing = not self.playing

    def cue_press(self):
        if self.playing:
            self.playing = False
            self.pos = self.cue
        else:  # paused: set the cue point at the current position (CDJ style)
            self.cue = self.pos

    def seek_frac(self, frac):
        if self.audio is not None:
            self.pos = max(0.0, min(1.0, frac)) * (len(self.audio) - 1)

    @property
    def length_s(self):
        return len(self.audio) / SR if self.audio is not None else 0.0

    @property
    def pos_s(self):
        return self.pos / SR

    # ---- sources
    def stop_live(self):
        if self.live:
            live, self.live = self.live, None
            live.close()
            self.playing = False
            self.title = "— empty —"

    def start_live(self, stream):
        self.stop_live()
        self.audio = None
        try:
            self.live = LiveSource(stream, self.letter)
        except Exception as e:
            self.status = f"Live failed: {e}"
            return
        self.title = f"LIVE · {stream['app']}" + (f" – {stream['media']}" if stream["media"] else "")
        self.status = ""
        self.playing = True

    def load_file(self, path):
        self.stop_live()
        self.playing = False
        self.loading = True
        self.status = "loading …"
        self.title = os.path.basename(path)

        def work():
            try:
                raw = subprocess.run(
                    ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-vn", "-f", "f32le",
                     "-ac", "2", "-ar", str(SR), "-"],
                    capture_output=True, check=True).stdout
                audio = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).copy()
                if len(audio) < 2:
                    raise ValueError("no audio data")
                self.audio, self.pos, self.cue = audio, 0.0, 0.0
                self.status = ""
            except Exception as e:
                self.audio = None
                self.status = f"Error: {getattr(e, 'stderr', b'') and e.stderr.decode()[:60] or e}"
            finally:
                self.loading = False

        threading.Thread(target=work, daemon=True).start()

    # ---- audio thread
    def render(self, n):
        out = None
        if self.live:
            data = self.live.read(n)  # always drain so latency stays low
            if self.playing:
                out = data
                self.spin += n / SR * 0.5556
        elif self.playing and self.audio is not None:
            a = self.audio
            idx = self.pos + np.arange(n) * self.speed
            i0 = idx.astype(np.int64)
            valid = i0 < len(a) - 1
            i0c = np.minimum(i0, len(a) - 2)
            frac = (idx - i0)[:, None].astype(np.float32)
            out = a[i0c] * (1 - frac) + a[i0c + 1] * frac
            out[~valid] = 0
            self.pos += n * self.speed
            self.spin = self.pos / SR * 0.5556  # 33⅓ rpm
            if self.pos >= len(a) - 1:
                self.pos = len(a) - 1
                self.playing = False
        if out is None:
            self.level *= 0.8
            return None
        if self._eq_dirty:
            self._sos = eq_sos(self.eq["low"], self.eq["mid"], self.eq["high"])
            self._eq_dirty = False
        if any(self.eq.values()):
            out, self._zi = sosfilt(self._sos, out, axis=0, zi=self._zi)
        out = out * (self.volume ** 1.5)
        self.level = max(float(np.abs(out).max()), self.level * 0.85)
        return out


# ---------------------------------------------------------------- mixer

class Mixer:
    def __init__(self):
        self.decks = [Deck("A"), Deck("B")]
        self.crossfader = 0.5  # 0 = only A, 1 = only B
        self.master = 0.8
        self.level = 0.0
        self.error = ""
        self.fx_samples = {}  # name -> (n, 2) float32 one-shot, filled by fx.build()
        self.fx_volume = 0.7
        self._pending = deque()  # triggered from the UI thread, picked up by the audio thread
        self._voices = []  # [name, sample, position]; only the audio thread modifies this
        self.stream = sd.OutputStream(samplerate=SR, blocksize=BLOCK, channels=2,
                                      dtype="float32", callback=self._callback, latency="low")

    def start(self):
        self.stream.start()

    def close(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass
        for d in self.decks:
            d.stop_live()

    def set_crossfader(self, v):
        self.crossfader = float(min(1.0, max(0.0, v)))

    def set_master(self, v):
        self.master = float(min(1.0, max(0.0, v)))

    def trigger_fx(self, name):
        sample = self.fx_samples.get(name)
        if sample is not None:
            self._pending.append([name, sample, 0])

    def fx_playing(self, name):
        return any(v[0] == name for v in self._voices)

    def _mix_fx(self, mix, frames):
        while self._pending:
            self._voices.append(self._pending.popleft())
        alive = []
        for voice in self._voices:
            _, sample, pos = voice
            chunk = sample[pos: pos + frames]
            mix[: len(chunk)] += chunk * self.fx_volume
            voice[2] = pos + frames
            if voice[2] < len(sample):
                alive.append(voice)
        self._voices = alive

    def _callback(self, outdata, frames, _time, _status):
        try:
            mix = np.zeros((frames, 2), dtype=np.float32)
            gains = (math.cos(self.crossfader * math.pi / 2), math.sin(self.crossfader * math.pi / 2))
            for deck, g in zip(self.decks, gains):
                sig = deck.render(frames)
                if sig is not None:
                    mix += sig * g
            if self._voices or self._pending:  # sound effects bypass the crossfader
                self._mix_fx(mix, frames)
            mix *= self.master ** 1.5
            np.clip(mix, -1.0, 1.0, out=mix)
            self.level = max(float(np.abs(mix).max()), self.level * 0.85)
            outdata[:] = mix
        except Exception as e:  # never let the audio thread die
            self.error = str(e)
            outdata.fill(0)


def wait_loaded(deck, timeout=30):
    t = time.time()
    while deck.loading and time.time() - t < timeout:
        time.sleep(0.05)
