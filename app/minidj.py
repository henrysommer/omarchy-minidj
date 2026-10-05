#!/usr/bin/env python3
"""MiniDJ – a minimal two-deck DJ controller for the terminal."""

import curses
import math
import os
import signal
import sys
import time

import fx
from engine import AUDIO_EXTS, EQ_MAX, EQ_MIN, SPEED_MAX, SPEED_MIN, Mixer, list_app_streams

TAU = 2 * math.pi
BUTTON5 = getattr(curses, "BUTTON5_PRESSED", 0x200000)
MIN_W, MIN_H = 116, 34
MIXER_W = 26
PANEL_H = 29

HELP = ("lowercase = Deck A · Shift = Deck B │ q/a HIGH  w/s MID  e/d LOW  r/f VOL  t/g SPEED  "
        "x PLAY  c CUE  b LOAD  v LIVE  n SOUNDS",
        "1-4 Sounds  ←/→ Crossfader  ↑/↓ Master  0 XF center │ Mouse: click · drag · scroll, right-click = reset │ Ctrl+C quit")


def fmt_time(s):
    s = max(0, int(s))
    return f"{s // 60:02d}:{s % 60:02d}"


def usb_roots():
    user = os.environ.get("USER", "")
    roots = []
    for base in (f"/run/media/{user}", "/media", "/mnt"):
        if os.path.isdir(base):
            roots += [os.path.join(base, d) for d in sorted(os.listdir(base))
                      if os.path.isdir(os.path.join(base, d))]
    return roots


# ---------------------------------------------------------------- overlays

class ListOverlay:
    title = ""

    def __init__(self, ui, deck_idx):
        self.ui, self.deck_idx = ui, deck_idx
        self.items, self.sel, self.top, self.msg = [], 0, 0, ""

    def label(self, item):
        return str(item)

    def activate(self, item):
        pass

    def extra_keys(self, ch):
        return False

    def header(self, y, x, w):
        pass

    def key(self, ch):
        n = len(self.items)
        if ch in (27, ord("q")):
            self.ui.overlay = None
        elif ch == curses.KEY_UP:
            self.sel = max(0, self.sel - 1)
        elif ch == curses.KEY_DOWN:
            self.sel = min(n - 1, self.sel + 1)
        elif ch == curses.KEY_PPAGE:
            self.sel = max(0, self.sel - 10)
        elif ch == curses.KEY_NPAGE:
            self.sel = min(n - 1, self.sel + 10)
        elif ch == curses.KEY_HOME:
            self.sel = 0
        elif ch == curses.KEY_END:
            self.sel = n - 1
        elif ch in (10, 13, curses.KEY_ENTER, curses.KEY_RIGHT) and self.items:
            self.activate(self.items[self.sel])
        else:
            self.extra_keys(ch)

    def draw(self):
        ui = self.ui
        H, W = ui.scr.getmaxyx()
        w, h = min(W - 6, 96), min(H - 4, 30)
        y0, x0 = (H - h) // 2, (W - w) // 2
        attr = ui.deck_attr(self.deck_idx)
        for yy in range(y0, y0 + h):
            ui.put(yy, x0, " " * w)
        ui.box(y0, x0, h, w, self.title, attr)
        self.header(y0 + 1, x0 + 2, w - 4)
        list_y, list_h = y0 + 4, h - 6
        if self.sel < self.top:
            self.top = self.sel
        if self.sel >= self.top + list_h:
            self.top = self.sel - list_h + 1
        for i in range(list_h):
            idx = self.top + i
            if idx >= len(self.items):
                break
            text = self.label(self.items[idx])[: w - 6].ljust(w - 6)
            a = curses.color_pair(7) | curses.A_BOLD if idx == self.sel else 0
            ui.put(list_y + i, x0 + 3, text, a)

            def hit(kind, _x, idx=idx):
                if kind == "click":
                    self.sel = idx
                    self.activate(self.items[idx])
            ui.hits.append((list_y + i, x0 + 2, x0 + w - 3, hit))
        if not self.items:
            ui.put(list_y, x0 + 3, "(nothing found)", curses.A_DIM)
        ui.put(y0 + h - 2, x0 + 2, self.msg[: w - 4], curses.color_pair(4))

    def scroll(self, d):
        self.sel = max(0, min(len(self.items) - 1, self.sel + d))


class FileBrowser(ListOverlay):
    def __init__(self, ui, deck_idx):
        super().__init__(ui, deck_idx)
        self.title = f" Load file → Deck {'AB'[deck_idx]} "
        self.cd(ui.browse_dir)

    def cd(self, path, select=None):
        try:
            names = sorted(os.listdir(path), key=str.lower)
        except OSError as e:
            self.msg = f"No access: {e.strerror}"
            return
        dirs = [n for n in names if not n.startswith(".") and os.path.isdir(os.path.join(path, n))]
        files = [n for n in names if not n.startswith(".") and os.path.splitext(n)[1].lower() in AUDIO_EXTS]
        self.path = os.path.abspath(path)
        self.items = ([("..", True)] if self.path != "/" else []) + [(d, True) for d in dirs] + [(f, False) for f in files]
        self.sel, self.top, self.msg = 0, 0, ""
        if select:
            for i, (n, _) in enumerate(self.items):
                if n == select:
                    self.sel = i
        self.ui.browse_dir = self.path

    def label(self, item):
        name, is_dir = item
        return f"▸ {name}/" if is_dir else f"♪ {name}"

    def activate(self, item):
        name, is_dir = item
        if name == "..":
            self.cd(os.path.dirname(self.path), select=os.path.basename(self.path))
        elif is_dir:
            self.cd(os.path.join(self.path, name))
        else:
            self.ui.m.decks[self.deck_idx].load_file(os.path.join(self.path, name))
            self.ui.overlay = None

    def go_usb(self):
        roots = usb_roots()
        if not roots:
            self.msg = "No USB stick mounted (/run/media/$USER is empty)"
        elif len(roots) == 1:
            self.cd(roots[0])
        else:
            self.cd(os.path.dirname(roots[0]))

    def extra_keys(self, ch):
        if ch in (curses.KEY_BACKSPACE, 127, 8, curses.KEY_LEFT):
            self.activate(("..", True))
        elif ch == ord("~"):
            self.cd(os.path.expanduser("~"))
        elif ch == ord("u"):
            self.go_usb()
        elif ch == ord("m"):
            self.cd(os.path.expanduser("~/Music"))

    def header(self, y, x, w):
        ui = self.ui
        ui.put(y, x, ("📁 " + self.path)[-w:], curses.A_BOLD)
        bx = x
        for text, fn in (("[~ Home]", lambda: self.cd(os.path.expanduser("~"))),
                         ("[u USB-Stick]", self.go_usb),
                         ("[m Music]", lambda: self.cd(os.path.expanduser("~/Music"))),
                         ("[⌫ back]", lambda: self.activate(("..", True))),
                         ("[Esc]", lambda: setattr(ui, "overlay", None))):
            bx = ui.button(y + 1, bx, text, fn, ui.deck_attr(self.deck_idx)) + 1


class AppPicker(ListOverlay):
    def __init__(self, ui, deck_idx):
        super().__init__(ui, deck_idx)
        self.title = f" Live audio from an app → Deck {'AB'[deck_idx]} "
        self.refresh()

    def refresh(self):
        items = []
        if self.ui.m.decks[self.deck_idx].live:
            items.append(None)
        try:
            items += list_app_streams()
            self.msg = "" if len(items) else "No app is playing audio – start e.g. Spotify or a browser and press r"
        except Exception as e:
            self.msg = f"pactl failed: {e}"
        self.items, self.sel = items, 0

    def label(self, item):
        if item is None:
            return "✕ Stop live (send the app back to normal output)"
        return f"♫ {item['app']}" + (f" – {item['media']}" if item["media"] else "")

    def activate(self, item):
        deck = self.ui.m.decks[self.deck_idx]
        if item is None:
            deck.stop_live()
        else:
            deck.start_live(item)
        self.ui.overlay = None

    def extra_keys(self, ch):
        if ch == ord("r"):
            self.refresh()

    def header(self, y, x, w):
        self.ui.put(y, x, "The app's audio then runs through this deck (EQ, volume, crossfader).", curses.A_DIM)
        bx = self.ui.button(y + 1, x, "[r Refresh]", self.refresh, self.ui.deck_attr(self.deck_idx))
        self.ui.button(y + 1, bx + 1, "[Esc]", lambda: setattr(self.ui, "overlay", None), self.ui.deck_attr(self.deck_idx))


class SoundPads:
    """Four big pads that fire one-shot sound effects."""

    def __init__(self, ui, deck_idx):
        self.ui, self.deck_idx = ui, deck_idx
        self.hit_at = {}

    def fire(self, name):
        self.ui.m.trigger_fx(name)
        self.hit_at[name] = time.time()

    def key(self, ch):
        if ch in (27, ord("q"), ord("n"), ord("N")):
            self.ui.overlay = None
        for name, key, _ in fx.EFFECTS:
            if ch == ord(key):
                self.fire(name)

    def scroll(self, _d):
        pass

    def draw(self):
        ui = self.ui
        H, W = ui.scr.getmaxyx()
        pw, ph, gap = 22, 7, 2
        w = len(fx.EFFECTS) * (pw + gap) - gap + 6
        h = ph + 7
        y0, x0 = (H - h) // 2, (W - w) // 2
        attr = ui.deck_attr(self.deck_idx)
        for yy in range(y0, y0 + h):
            ui.put(yy, x0, " " * w)
        ui.box(y0, x0, h, w, " SOUNDS ", attr)
        ui.put(y0 + 1, x0 + 3, "Click a pad or press 1–4 (the keys also work with this window closed).", curses.A_DIM)
        for n, (name, key, _) in enumerate(fx.EFFECTS):
            px, py = x0 + 3 + n * (pw + gap), y0 + 3
            lit = time.time() - self.hit_at.get(name, 0) < 0.15 or ui.m.fx_playing(name)
            fill = attr | curses.A_REVERSE | curses.A_BOLD if lit else attr
            ui.box(py, px, ph, pw, "", fill if lit else attr)
            for yy in range(py + 1, py + ph - 1):
                ui.put(yy, px + 1, " " * (pw - 2), fill if lit else 0)
                ui.hits.append((yy, px, px + pw - 1, lambda kind, _x, nm=name: self.fire(nm) if kind == "click" else None))
            ui.put(py + 2, px + 1, name.center(pw - 2), fill | curses.A_BOLD)
            ui.put(py + 4, px + 1, f"[{key}]".center(pw - 2), fill if lit else curses.A_DIM)
        bx = ui.button(y0 + h - 2, x0 + 3, "[Esc close]", lambda: setattr(ui, "overlay", None), attr)
        ui.slider(y0 + h - 2, bx + 3, 34, "VOL", lambda: ui.m.fx_volume,
                  lambda v: setattr(ui.m, "fx_volume", min(1.0, max(0.0, v))), 0.0, 1.0, 0.05, 0.7,
                  lambda v: f"{v * 100:.0f}%", attr, lw=5, tw=5)


# ---------------------------------------------------------------- main UI

class UI:
    def __init__(self, scr, mixer):
        self.scr, self.m = scr, mixer
        self.hits, self.drag, self.overlay = [], None, None
        music = os.path.expanduser("~/Music")
        self.browse_dir = music if os.path.isdir(music) else os.path.expanduser("~")
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        for i, c in enumerate((curses.COLOR_CYAN, curses.COLOR_MAGENTA, curses.COLOR_GREEN,
                               curses.COLOR_YELLOW, curses.COLOR_RED, curses.COLOR_WHITE), 1):
            curses.init_pair(i, c, -1)
        curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.mousemask(curses.ALL_MOUSE_EVENTS | curses.REPORT_MOUSE_POSITION)
        curses.mouseinterval(0)
        sys.stdout.write("\033[?1002h")  # report mouse motion while a button is held (dragging)
        sys.stdout.flush()
        scr.keypad(True)
        scr.timeout(40)

    # ---- primitives
    def put(self, y, x, s, attr=0):
        H, W = self.scr.getmaxyx()
        if y < 0 or y >= H or x < 0 or x >= W:
            return
        s = s[: W - x - (1 if y == H - 1 else 0)]
        try:
            self.scr.addstr(y, x, s, attr)
        except curses.error:
            pass

    def deck_attr(self, i):
        return curses.color_pair(1 + i)

    def box(self, y, x, h, w, title="", attr=0):
        self.put(y, x, "╭" + "─" * (w - 2) + "╮", attr)
        for yy in range(y + 1, y + h - 1):
            self.put(yy, x, "│", attr)
            self.put(yy, x + w - 1, "│", attr)
        self.put(y + h - 1, x, "╰" + "─" * (w - 2) + "╯", attr)
        if title:
            self.put(y, x + 2, title, attr | curses.A_BOLD)

    def button(self, y, x, text, fn, attr=0):
        self.put(y, x, text, attr | curses.A_BOLD)
        self.hits.append((y, x, x + len(text) - 1, lambda kind, _x: fn() if kind == "click" else None))
        return x + len(text)

    def slider(self, y, x, w, label, get, set_, lo, hi, step, default, text, attr, zero=None, lw=6, tw=8):
        bw = max(4, w - lw - tw)
        value = get()
        zero = lo if zero is None else zero
        cell = lambda v: round((v - lo) / (hi - lo) * (bw - 1))
        knob, zp = cell(value), cell(zero)
        self.put(y, x, label[: lw - 1].ljust(lw), curses.A_BOLD)
        bx = x + lw
        for i in range(bw):
            if i == knob:
                self.put(y, bx + i, "●", attr | curses.A_BOLD)
            elif min(zp, knob) <= i <= max(zp, knob):
                self.put(y, bx + i, "━", attr)
            elif i == zp:
                self.put(y, bx + i, "┼", curses.A_DIM)
            else:
                self.put(y, bx + i, "─", curses.A_DIM)
        self.put(y, bx + bw + 1, text(value).rjust(tw - 1))

        def hit(kind, mx):
            if kind in ("click", "drag"):
                c = max(0, min(bw - 1, mx - bx))
                set_(default if c == cell(default) else lo + c / (bw - 1) * (hi - lo))
            elif kind == "up":
                set_(get() + step)
            elif kind == "down":
                set_(get() - step)
            elif kind == "reset":
                set_(default)
        self.hits.append((y, x, x + w - 1, hit))

    def meter(self, y, x, h, level, label, attr):
        db = 20 * math.log10(max(level, 1e-6))
        frac = max(0.0, min(1.0, (db + 48) / 48))
        lit = round(frac * h)
        for i in range(h):
            yy = y + h - 1 - i
            f = i / h
            col = curses.color_pair(5) if f > 0.88 else curses.color_pair(4) if f > 0.7 else curses.color_pair(3)
            self.put(yy, x, "██" if i < lit else "░░", col if i < lit else curses.A_DIM)
        self.put(y + h, x, label, attr | curses.A_BOLD)

    def vinyl(self, cy, cx, rx, ry, spin, attr, active):
        rot = (spin % 1.0) * TAU
        for dy in range(-ry, ry + 1):
            for dx in range(-rx, rx + 1):
                nx, ny = dx / rx, dy / ry
                r = math.hypot(nx, ny)
                if r > 1.0:
                    continue
                rel = (math.atan2(ny, nx) - rot) % TAU
                if r < 0.1:
                    ch, a = "●", curses.A_BOLD
                elif r < 0.38:
                    marker = rel < 0.55 and r > 0.16
                    ch, a = ("█", curses.A_BOLD) if marker else ("▓", attr)
                elif r > 0.95:
                    ch, a = "░", curses.A_DIM
                else:
                    streak = rel < 0.22 or abs(rel - math.pi) < 0.12
                    if streak:
                        ch, a = "▓", (attr | curses.A_BOLD) if active else curses.A_BOLD
                    else:
                        ch, a = ("▒" if int(r * 16) % 2 else "░"), curses.A_DIM
                self.put(cy + dy, cx + dx, ch, a)

    # ---- panels
    def deck_panel(self, i, y, x, w):
        d = self.m.decks[i]
        attr = self.deck_attr(i)
        self.box(y, x, PANEL_H, w, f" DECK {d.letter} ", attr)
        iw, ix = w - 4, x + 2
        self.put(y + 1, ix, d.title[:iw].center(iw), curses.A_BOLD)
        rx = min(15, (iw - 2) // 2)
        self.vinyl(y + 9, x + w // 2, rx, 6, d.spin, attr, d.playing)

        if d.status:
            state = d.status
        elif d.live:
            state = "● LIVE" if d.playing else "❚❚ LIVE (muted)"
        elif d.audio is None:
            state = "empty – press b/B or [LOAD]"
        else:
            state = ("▶ PLAY" if d.playing else "❚❚ PAUSE") + \
                f"   {fmt_time(d.pos_s)} / {fmt_time(d.length_s)}   -{fmt_time(d.length_s - d.pos_s)}"
        self.put(y + 17, ix, state[:iw], attr | curses.A_BOLD)

        # progress / seek bar
        frac = d.pos / max(1, len(d.audio) - 1) if d.audio is not None else 0
        filled = round(frac * iw)
        self.put(y + 18, ix, "▬" * filled, attr | curses.A_BOLD)
        self.put(y + 18, ix + filled, "▬" * (iw - filled), curses.A_DIM)
        self.put(y + 18, ix + max(0, filled - 1), "▌", curses.A_BOLD)
        self.hits.append((y + 18, ix, ix + iw - 1,
                          lambda kind, mx: d.seek_frac((mx - ix) / max(1, iw - 1)) if kind in ("click", "drag") else None))

        db = lambda v: "KILL" if v <= EQ_MIN + 0.01 else f"{v:+.1f}dB"
        for row, band in ((20, "high"), (21, "mid"), (22, "low")):
            self.slider(y + row, ix, iw, band.upper(), lambda b=band: d.eq[b],
                        lambda v, b=band: d.set_eq(b, v), EQ_MIN, EQ_MAX, 1.5, 0.0, db, attr, zero=0.0)
        self.slider(y + 24, ix, iw, "VOL", lambda: d.volume, d.set_volume, 0.0, 1.0, 0.05, 0.8,
                    lambda v: f"{v * 100:.0f}%", attr)
        self.slider(y + 25, ix, iw, "SPEED", lambda: d.speed, d.set_speed, SPEED_MIN, SPEED_MAX, 0.005, 1.0,
                    lambda v: f"{(v - 1) * 100:+.1f}%", attr, zero=1.0)

        bx = ix
        bx = self.button(y + 27, bx, "[❚❚ PAUSE]" if d.playing else "[▶ PLAY]", d.toggle_play, attr) + 1
        bx = self.button(y + 27, bx, "[CUE]", d.cue_press, attr) + 1
        bx = self.button(y + 27, bx, "[LOAD]", lambda: self.open(FileBrowser, i), attr) + 1
        bx = self.button(y + 27, bx, "[LIVE]", lambda: self.open(AppPicker, i), attr) + 1
        self.button(y + 27, bx, "[SOUNDS]", lambda: self.open(SoundPads, i), attr)

    def mixer_panel(self, y, x, w):
        m = self.m
        self.box(y, x, PANEL_H, w, " MIXER ", curses.A_BOLD)
        mh = 19
        cx = x + w // 2
        self.meter(y + 2, cx - 9, mh, m.decks[0].level, " A", self.deck_attr(0))
        self.meter(y + 2, cx - 1, mh, m.level, "MST", curses.A_BOLD)
        self.meter(y + 2, cx + 7, mh, m.decks[1].level, " B", self.deck_attr(1))
        self.slider(y + 24, x + 2, w - 4, "MST", lambda: m.master, m.set_master, 0.0, 1.0, 0.05, 0.8,
                    lambda v: f"{v * 100:.0f}%", curses.color_pair(6), lw=4, tw=5)
        if m.error:
            self.put(y + 26, x + 2, m.error[: w - 4], curses.color_pair(5))

    def crossfader(self, y, W):
        w = min(W - 10, 64)
        x = (W - w) // 2
        self.put(y, x - 3, "A", self.deck_attr(0) | curses.A_BOLD)
        self.put(y, x + w + 2, "B", self.deck_attr(1) | curses.A_BOLD)
        knob = round(self.m.crossfader * (w - 1))
        for i in range(w):
            if i == knob:
                ch, a = "◆", curses.A_BOLD
            else:
                ch = "┼" if i == (w - 1) // 2 else "═"
                a = self.deck_attr(0 if i < knob else 1) | curses.A_DIM
            self.put(y, x + i, ch, a)
        self.put(y + 1, (W - 11) // 2, "CROSSFADER", curses.A_DIM)

        def hit(kind, mx):
            if kind in ("click", "drag"):
                c = max(0, min(w - 1, mx - x))
                self.m.set_crossfader(0.5 if c == (w - 1) // 2 else c / (w - 1))
            elif kind == "up":
                self.m.set_crossfader(self.m.crossfader + 0.05)
            elif kind == "down":
                self.m.set_crossfader(self.m.crossfader - 0.05)
            elif kind == "reset":
                self.m.set_crossfader(0.5)
        self.hits.append((y, x - 3, x + w + 2, hit))

    def draw(self):
        self.scr.erase()
        self.hits = []
        H, W = self.scr.getmaxyx()
        if W < MIN_W or H < MIN_H:
            self.put(0, 0, f"Window too small: {W}x{H}, need at least {MIN_W}x{MIN_H}.")
            self.scr.refresh()
            return
        top = max(0, (H - MIN_H) // 2)
        self.put(top, 2, "◉ MiniDJ", curses.A_BOLD)
        dw = (W - MIXER_W) // 2
        self.deck_panel(0, top + 1, 0, dw)
        self.mixer_panel(top + 1, dw, MIXER_W)
        self.deck_panel(1, top + 1, dw + MIXER_W, W - dw - MIXER_W)
        self.crossfader(top + 1 + PANEL_H, W)
        for n, line in enumerate(HELP):
            self.put(top + MIN_H - 2 + n, 1, line[: W - 2].center(W - 2), curses.A_DIM)
        if self.overlay:
            self.hits = []
            self.overlay.draw()
        self.scr.refresh()

    # ---- input
    def open(self, cls, i):
        self.overlay = cls(self, i)

    def mouse(self):
        try:
            _, mx, my, _, b = curses.getmouse()
        except curses.error:
            return
        if b & curses.BUTTON1_RELEASED:
            self.drag = None
            return
        if self.drag and b & curses.REPORT_MOUSE_POSITION:
            self.drag(("drag"), mx)
            return
        kind = ("click" if b & curses.BUTTON1_PRESSED else "reset" if b & curses.BUTTON3_PRESSED
                else "up" if b & curses.BUTTON4_PRESSED else "down" if b & BUTTON5 else None)
        if kind is None:
            return
        if self.overlay and kind in ("up", "down"):
            self.overlay.scroll(-1 if kind == "up" else 1)
            return
        for y, x0, x1, fn in reversed(self.hits):
            if fn and y == my and x0 <= mx <= x1:
                fn(kind, mx)
                if kind == "click":
                    self.drag = fn
                break

    def key(self, ch):
        if ch == curses.KEY_MOUSE:
            return self.mouse()
        if self.overlay:
            return self.overlay.key(ch)
        if ch == curses.KEY_LEFT:
            return self.m.set_crossfader(self.m.crossfader - 0.05)
        if ch == curses.KEY_RIGHT:
            return self.m.set_crossfader(self.m.crossfader + 0.05)
        if ch == curses.KEY_UP:
            return self.m.set_master(self.m.master + 0.05)
        if ch == curses.KEY_DOWN:
            return self.m.set_master(self.m.master - 0.05)
        for name, key, _ in fx.EFFECTS:
            if ch == ord(key):
                return self.m.trigger_fx(name)
        if ch == ord("0"):
            return self.m.set_crossfader(0.5)
        if ch == curses.KEY_F10:
            raise SystemExit
        if not 0 < ch < 256:
            return
        c = chr(ch)
        i = 1 if c.isupper() else 0
        d = self.m.decks[i]
        actions = {
            "q": lambda: d.set_eq("high", d.eq["high"] + 1.5), "a": lambda: d.set_eq("high", d.eq["high"] - 1.5),
            "w": lambda: d.set_eq("mid", d.eq["mid"] + 1.5), "s": lambda: d.set_eq("mid", d.eq["mid"] - 1.5),
            "e": lambda: d.set_eq("low", d.eq["low"] + 1.5), "d": lambda: d.set_eq("low", d.eq["low"] - 1.5),
            "r": lambda: d.set_volume(d.volume + 0.05), "f": lambda: d.set_volume(d.volume - 0.05),
            "t": lambda: d.set_speed(d.speed + 0.005), "g": lambda: d.set_speed(d.speed - 0.005),
            "x": d.toggle_play, "c": d.cue_press,
            "b": lambda: self.open(FileBrowser, i), "v": lambda: self.open(AppPicker, i),
            "n": lambda: self.open(SoundPads, i),
        }
        fn = actions.get(c.lower())
        if fn:
            fn()

    def run(self):
        while True:
            self.draw()
            ch = self.scr.getch()
            while ch != -1:
                self.key(ch)
                ch = self.scr.getch() if ch != curses.KEY_MOUSE else -1


def main(scr):
    mixer = Mixer()
    mixer.fx_samples = fx.build()
    try:
        mixer.start()
        for deck, path in zip(mixer.decks, sys.argv[1:3]):  # optional: minidj [trackA] [trackB]
            deck.load_file(path)
        UI(scr, mixer).run()
    finally:
        sys.stdout.write("\033[?1002l")
        sys.stdout.flush()
        mixer.close()


if __name__ == "__main__":
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: sys.exit(0))
    os.environ.setdefault("ESCDELAY", "25")
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
