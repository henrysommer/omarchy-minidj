# MiniDJ for Omarchy

A minimal two-deck DJ controller that lives in your Omarchy bar. Click the
record icon next to Bluetooth and Wi‑Fi and a floating terminal opens with two
spinning ASCII vinyls, a 3-band EQ per deck, speed, a crossfader and sound-effect
pads. Load tracks from any folder or USB stick, or pull the audio of another app
(Spotify, a browser tab, …) into a deck and mix it live.

![MiniDJ](preview.png)

## Features

- **Two decks:** play/pause, CUE, seek bar, spinning vinyl
- **Per deck:** HIGH / MID / LOW EQ (+6 dB down to kill), volume, speed ±50 % (pitch follows, like vinyl)
- **Mixer:** level meters, master volume, constant-power crossfader
- **File browser:** shows only audio files and jumps straight to USB sticks (`/run/media/$USER`), `~` or `~/Music`
- **Live mode:** reroutes another app's playback stream through a deck via PipeWire, so EQ, volume and crossfader apply to it; the app is routed back when you stop or quit
- **Sound pads:** air horn, dub siren, laser, scratch (synthesized, no sample files)
- **Mouse and keyboard:** click or drag sliders, scroll to fine-tune, right-click to reset

## Install

```bash
omarchy plugin add https://github.com/henrysommer/omarchy-minidj.git --enable
```

Pick the `right` section when asked, then move the icon next to Bluetooth:

```bash
omarchy bar move henrysommer.minidj --before omarchy.bluetooth
```

The first click opens a terminal that sets up a private Python environment in
`~/.local/share/minidj` (about a minute; later starts are instant). It runs
entirely as your user and installs nothing system-wide.

MiniDJ needs a window of about 116×34 characters. On first launch it **asks**
whether it may add this rule to `~/.config/hypr/hyprland.lua` so it opens as a
centered floating window:

```lua
o.window("org.omarchy.minidj", { float = true, center = true, size = { 1240, 820 } })
```

If you answer no, your config stays untouched and MiniDJ won't ask again; you can
paste the line above into `hyprland.lua` yourself at any time.

### Dependencies

- **System** (preinstalled on Omarchy): PipeWire with `pipewire-pulse`, `python`,
  `ffmpeg` (decodes tracks) and `libpulse` (`pactl`/`parec` for live mode)
- **Python** (installed by `pip` from PyPI into `~/.local/share/minidj/venv`):
  `numpy`, `scipy`, `sounddevice`, see [`app/requirements.txt`](app/requirements.txt)

You can also start it from a terminal: `~/.config/omarchy/plugins/henrysommer.minidj/bin/minidj [trackA] [trackB]`.

## Controls

Lowercase keys control deck A, the same keys with Shift control deck B.

| Key | Action | Key | Action |
|---|---|---|---|
| `q` / `a` | HIGH up / down | `x` | play / pause |
| `w` / `s` | MID up / down | `c` | CUE |
| `e` / `d` | LOW up / down | `b` | load file |
| `r` / `f` | volume up / down | `v` | live app audio |
| `t` / `g` | speed up / down | `n` | sound pads |
| `1`–`4` | fire sound effect | `←` / `→` | crossfader |
| `0` | center crossfader | `↑` / `↓` | master volume |

Quit with `Ctrl+C` or `F10`.

## Uninstall

```bash
omarchy plugin remove henrysommer.minidj
rm -rf ~/.local/share/minidj
```

If you let MiniDJ add its window rule, delete the two `MiniDJ` lines at the end
of `~/.config/hypr/hyprland.lua`.

## Development

```bash
scripts/dev-sync.sh   # validate, copy into ~/.config/omarchy/plugins and link launchers
```

The audio engine is in `app/engine.py`, the curses UI in `app/minidj.py` and
the sound effects in `app/fx.py`.

## License

MIT
