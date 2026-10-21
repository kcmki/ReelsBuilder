"""ffmpeg-only reel editor: picks highlight windows and renders 9:16 reels."""
import json
import logging
import shutil
import subprocess
import tempfile
import textwrap
from pathlib import Path
from typing import List, Optional

log = logging.getLogger("reels_manager")
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# Single source of truth for per-account editing options (the dashboard renders its form from this).
# (key, label, type, default, options/hint)
EDIT_FIELDS = [
    ("mode", "Reel type", "select", "single", ["single", "montage"]),
    ("layout", "Layout", "select", "blur", ["blur", "crop", "fit"]),
    ("clips_count", "Highlights per video", "int", 5, "single: reels per video · montage: segments in the reel"),
    ("clip_length", "Highlight length (s)", "float", 20, "montage: use 3-8s for punchy cuts"),
    ("max_duration", "Max reel length (s)", "float", 90, ""),
    ("transition", "Montage transition", "select", "fade",
     ["none", "fade", "fadeblack", "dissolve", "wipeleft", "slideleft", "slideup", "circleopen", "zoomin", "pixelize"]),
    ("transition_duration", "Transition length (s)", "float", 0.5, ""),
    ("zoom", "Foreground zoom", "float", 1.0, "blur layout: >1 fills more of the frame"),
    ("speed", "Playback speed", "float", 1.0, "0.5 - 2.0"),
    ("saturation", "Saturation", "float", 1.15, "1 = original"),
    ("contrast", "Contrast", "float", 1.05, "1 = original"),
    ("trim_right", "Trim right edge (%)", "float", 10, "hides channel watermarks"),
    ("trim_bottom", "Trim bottom edge (%)", "float", 10, ""),
    ("fade", "Fade in/out", "bool", True, ""),
    ("text", "Overlay text", "text", "", "supports {title}; empty = none"),
    ("text_position", "Text position", "select", "top", ["top", "center", "bottom"]),
    ("logo_position", "Logo position", "select", "bottom", ["none", "top", "bottom"]),
]
EDIT_DEFAULTS = {k: d for k, _, _, d, _ in EDIT_FIELDS}
W, H, FPS = 1080, 1920, 30


def coerce_edit(raw: Optional[dict]) -> dict:
    """Merge user values over defaults, casting to the declared type (input comes from the web form)."""
    out = dict(EDIT_DEFAULTS)
    for key, _, typ, default, opts in EDIT_FIELDS:
        if not raw or key not in raw or raw[key] is None:
            continue
        v = raw[key]
        try:
            if typ == "int":
                out[key] = int(float(v))
            elif typ == "float":
                out[key] = float(v)
            elif typ == "bool":
                out[key] = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "on", "yes")
            elif typ == "select":
                out[key] = v if v in opts else default
            else:
                out[key] = str(v)
        except (TypeError, ValueError):
            pass
    out["speed"] = min(max(out["speed"], 0.5), 2.0)  # atempo range
    out["clips_count"] = max(out["clips_count"], 1)
    return out


def probe(path: str) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(r.stdout)
    return {
        "duration": float(data["format"]["duration"]),
        "has_audio": any(s.get("codec_type") == "audio" for s in data.get("streams", [])),
    }


def _has_video(path: str) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=index",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return bool(r.stdout.strip())


def pick_windows(heatmap: Optional[list], duration: float, count: int, length: float) -> List[tuple]:
    """Return up to `count` non-overlapping (start, length) windows, chronological.

    heatmap: yt-dlp "most replayed" list [{start_time, end_time, value}]. Its peaks are taken first
    (opened slightly early so the moment has context); evenly spaced windows fill whatever is left.
    """
    length = min(length, duration)
    step = duration / (count + 1)
    candidates = [float(m["start_time"]) - length * 0.3
                  for m in sorted(heatmap or [], key=lambda m: m.get("value", 0), reverse=True)]
    candidates += [step * (i + 1) - length / 2 for i in range(count)]
    starts = []
    for s in candidates:
        s = max(0.0, min(s, duration - length))
        if all(abs(s - o) >= length for o in starts):
            starts.append(s)
        if len(starts) == count:
            break
    return [(round(s, 2), length) for s in sorted(starts)]


def _run(cmd: list):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-1500:]}")


ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
       "-c:a", "aac", "-b:a", "128k", "-ar", "48000", "-ac", "2"]


class ClipBuilder:
    def __init__(self, edit: Optional[dict] = None, logo_path: Optional[str] = None):
        self.e = coerce_edit(edit)
        self.logo = Path(logo_path) if logo_path and Path(logo_path).exists() else None

    # --- one highlight -> normalized 1080x1920 segment --------------------------------
    def _video_chain(self) -> str:
        e = self.e
        trim = f"crop=iw*{1 - e['trim_right'] / 100:.4f}:ih*{1 - e['trim_bottom'] / 100:.4f}:0:0"
        look = f"eq=saturation={e['saturation']}:contrast={e['contrast']},setpts=PTS/{e['speed']}"
        if e["layout"] == "crop":
            body = f"[0:v]{trim},{look},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}[v]"
        elif e["layout"] == "fit":
            body = (f"[0:v]{trim},{look},scale={W}:{H}:force_original_aspect_ratio=decrease,"
                    f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black[v]")
        else:  # blur: blurred fill behind a centered foreground
            fw, fh = int(W * e["zoom"]) // 2 * 2, int(H * e["zoom"]) // 2 * 2
            body = (f"[0:v]{trim},{look},split[a][b];"
                    f"[a]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,crop={W // 4}:{H // 4},"
                    f"boxblur=10:2,scale={W}:{H}[bg];"
                    f"[b]scale={fw}:{fh}:force_original_aspect_ratio=decrease[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]")
        return body + f";[v]setsar=1,fps={FPS},format=yuv420p[vout]"

    def render_segment(self, source: str, start: float, length: float, out: str, has_audio: bool):
        cmd = ["ffmpeg", "-y", "-ss", str(start), "-t", str(length), "-i", source]
        if has_audio:
            achain = f"[0:a]atempo={self.e['speed']},aresample=48000[aout]"
        else:
            cmd += ["-f", "lavfi", "-t", str(length / self.e["speed"]), "-i", "anullsrc=r=48000:cl=stereo"]
            achain = "[1:a]anull[aout]"
        cmd += ["-filter_complex", f"{self._video_chain()};{achain}", "-map", "[vout]", "-map", "[aout]",
                *ENC, out]
        _run(cmd)

    # --- segments -> one clip with transitions ---------------------------------------
    def join(self, segments: List[str], out: str):
        if len(segments) == 1:
            shutil.copy(segments[0], out)
            return
        t = self.e["transition_duration"]
        if self.e["transition"] == "none" or t <= 0:
            lst = Path(out).with_suffix(".txt")
            lst.write_text("".join(f"file '{Path(s).resolve().as_posix()}'\n" for s in segments))
            _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", out])
            lst.unlink()
            return
        durs = [probe(s)["duration"] for s in segments]
        t = min(t, min(durs) / 2)
        parts, v, a, offset = [], "[0:v]", "[0:a]", 0.0
        for i in range(1, len(segments)):
            offset += durs[i - 1] - t
            parts.append(f"{v}[{i}:v]xfade=transition={self.e['transition']}:duration={t}:offset={offset:.3f}[v{i}]")
            parts.append(f"{a}[{i}:a]acrossfade=d={t}[a{i}]")
            v, a = f"[v{i}]", f"[a{i}]"
        cmd = ["ffmpeg", "-y"]
        for s in segments:
            cmd += ["-i", s]
        _run(cmd + ["-filter_complex", ";".join(parts), "-map", v, "-map", a, *ENC, out])

    # --- logo, text, fades, length cap -----------------------------------------------
    def finish(self, src: str, out: str, title: str = ""):
        e = self.e
        dur = min(probe(src)["duration"], e["max_duration"])
        cmd = ["ffmpeg", "-y", "-i", src]
        chain, v = [], "[0:v]"
        if self.logo and e["logo_position"] != "none":
            cmd += ["-i", str(self.logo)]
            y = int(H * 0.04) if e["logo_position"] == "top" else f"H-h-{int(H * 0.04)}"
            chain.append(f"[1:v]scale=-1:{int(H * 0.06)}[lg];{v}[lg]overlay=(W-w)/2:{y}[vl]")
            v = "[vl]"
        text = e["text"].replace("{title}", title or "").strip()
        filters = []
        if text:
            tf = Path(out).with_suffix(".txt")
            tf.write_text("\n".join(textwrap.wrap(text, 24)[:4]), encoding="utf-8")
            y = {"top": "h*0.12", "center": "(h-text_h)/2", "bottom": "h*0.78-text_h"}[e["text_position"]]
            # ':' and '\' are drawtext separators; use forward slashes and escape the drive colon on Windows
            tpath = tf.resolve().as_posix().replace(":", "\\:")
            filters.append(f"drawtext=textfile='{tpath}':fontfile='{FONT if Path(FONT).exists() else ''}'"
                           f":fontsize={int(H * 0.035)}:fontcolor=white:borderw=5:bordercolor=black"
                           f":line_spacing=12:x=(w-text_w)/2:y={y}")
        afilter = "anull"
        if e["fade"] and dur > 2:
            filters.append(f"fade=t=in:st=0:d=0.4,fade=t=out:st={dur - 0.5:.2f}:d=0.5")
            afilter = f"afade=t=in:st=0:d=0.4,afade=t=out:st={dur - 0.5:.2f}:d=0.5"
        chain.append(f"{v}{','.join(filters) or 'null'}[vout]")
        chain.append(f"[0:a]{afilter}[aout]")
        cmd += ["-t", f"{dur:.2f}", "-filter_complex", ";".join(chain), "-map", "[vout]", "-map", "[aout]",
                *ENC, "-movflags", "+faststart", out]
        try:
            _run(cmd)
        finally:
            if text:
                Path(out).with_suffix(".txt").unlink(missing_ok=True)

    # --- full pipeline ---------------------------------------------------------------
    def build(self, source: str, heatmap: Optional[list], out_dir: str, title: str = "") -> List[str]:
        """Render reels for one downloaded video. Returns output paths."""
        e = self.e
        info = probe(source)
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        windows = pick_windows(heatmap, info["duration"], e["clips_count"], e["clip_length"] * e["speed"])
        outputs = []
        with tempfile.TemporaryDirectory(dir=out_dir) as tmp:
            segs = []
            for i, (start, length) in enumerate(windows):
                seg = f"{tmp}/seg_{i}.mp4"
                try:
                    self.render_segment(source, start, length, seg, info["has_audio"])
                    if probe(seg)["duration"] < 1:
                        raise RuntimeError("segment came out empty")
                    if not _has_video(seg):
                        raise RuntimeError("segment has no video frames")
                except Exception as err:
                    # one bad window (corrupt stretch of the source) shouldn't sink the whole video
                    log.warning("skipping window %s@%.1fs: %s", i, start, str(err)[-300:])
                    continue
                segs.append(seg)
            if not segs:
                raise RuntimeError(f"no usable segments out of {len(windows)} windows")
            groups = [segs] if e["mode"] == "montage" else [[s] for s in segs]
            for i, group in enumerate(groups):
                joined = f"{tmp}/joined_{i}.mp4"
                self.join(group, joined)
                out = out_dir / f"reel_{i + 1}.mp4"
                self.finish(joined, str(out), title)
                outputs.append(str(out))
        return outputs


if __name__ == "__main__":
    # self-check: window picking + a real render of both modes from a synthetic source
    hm = [{"start_time": t, "end_time": t + 1, "value": v} for t, v in [(10, 0.9), (11, 0.8), (50, 1.0), (90, 0.5)]]
    w = pick_windows(hm, 100, 3, 10)
    assert w == [(7.0, 10), (47.0, 10), (87.0, 10)], w  # 11s peak overlaps 10s peak -> skipped
    assert pick_windows(None, 100, 3, 10) == [(20.0, 10), (45.0, 10), (70.0, 10)]
    assert pick_windows(hm, 5, 2, 10) == [(0.0, 5)]  # short video clamps
    # sparse heatmap: the peak is kept, evenly spaced windows top it up
    assert pick_windows([{"start_time": 25, "value": 1}], 40, 4, 5) == [(5.5, 5), (13.5, 5), (23.5, 5), (29.5, 5)]
    assert coerce_edit({"speed": "9", "mode": "evil", "fade": "on"})["speed"] == 2.0
    assert coerce_edit({"mode": "evil"})["mode"] == "single"

    import sys
    with tempfile.TemporaryDirectory() as d:
        src = f"{d}/src.mp4"
        _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:d=30", "-f", "lavfi",
              "-i", "sine=d=30", "-shortest", *ENC, src])
        for mode in ("single", "montage"):
            cb = ClipBuilder({"mode": mode, "clips_count": 3, "clip_length": 4, "text": "{title}"})
            outs = cb.build(src, None, f"{d}/{mode}", title="Self check title that is long enough to wrap")
            assert len(outs) == (3 if mode == "single" else 1), outs
            p = probe(outs[0])
            assert p["has_audio"], p
            print(mode, [round(probe(o)["duration"], 1) for o in outs])
        # video stream ends at 10s but audio runs to 30s (the production failure): bad windows are skipped
        broken = f"{d}/broken.mp4"
        _run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:d=10", "-f", "lavfi", "-i", "sine=d=30",
              *ENC, broken])
        outs = ClipBuilder({"clips_count": 3, "clip_length": 4}).build(broken, None, f"{d}/broken")
        assert len(outs) == 1, outs
        print("broken source ->", len(outs), "reel")
        if "--keep" in sys.argv:
            shutil.copytree(d, sys.argv[-1], dirs_exist_ok=True)
    print("ClipBuilder self-check OK")
