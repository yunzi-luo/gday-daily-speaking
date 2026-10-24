"""Generate natural-sounding MP3s for every English line in a lesson.

Usage:
  python tools/make_audio.py              # all lessons
  python tools/make_audio.py 2026-10-12   # one or more lessons
  python tools/make_audio.py --shrink-all # re-encode existing clips at BITRATE (skips ones already that small)

Output:
  lessons/audio/<id>/<hash>.mp3   one clip per line
  lessons/audio/<id>.json         {"files": {"<exact text>": "<hash>.mp3"}}

The page plays these clips when they exist and falls back to the browser's own voice otherwise.
Clips already on disk are reused, so re-running only fills in what's missing.

Engine:
  ENGINE = "edge"   free, no account (edge-tts: pip install edge-tts). Unofficial, may stop working.
  ENGINE = "azure"  official Azure Speech; set env vars AZURE_SPEECH_KEY and AZURE_SPEECH_REGION.
  Both use the same Microsoft neural voices, so switching does not change how lessons sound.

Size:
  New clips are re-encoded to BITRATE with ffmpeg (on PATH, or pip install imageio-ffmpeg), about half
  the size of the 48 kbps the voice service returns. Without ffmpeg they are kept as returned.
"""
import asyncio, hashlib, json, os, pathlib, re, shutil, subprocess, sys, urllib.request
from xml.sax.saxutils import escape

ENGINE = "edge"
VOICES = {"f": "en-AU-NatashaNeural", "m": "en-AU-WilliamMultilingualNeural"}
NARRATOR = "f"  # vocab and examples
BITRATE = 24  # kbps, mono 24 kHz; speech stays clear

ROOT = pathlib.Path(__file__).resolve().parent.parent
LESSONS = ROOT / "lessons"
AUDIO = LESSONS / "audio"

# must match the sentence split in index.html (runSpeech)
SENT_RE = re.compile(r'[^.!?]+[.!?]+["”\')]*\s*|[^.!?]+$')


def sentences(p):
    return [s.strip() for s in (SENT_RE.findall(p) or [p]) if s.strip()]


def lines_for(L):
    """(text, voice key) for every line the page can read aloud."""
    other = (L.get("dialogue") or {}).get("other") or {}
    them = other.get("voice", "f")
    me = "f" if them == "m" else "m"
    out = []
    for v in L.get("vocab", []):
        out.append((v["en"], NARRATOR))
        if v.get("ex"):
            out.append((v["ex"], NARRATOR))
    for x in L.get("listening", []):
        out.append((x["en"], them))
        if x.get("reply"):
            out.append((x["reply"], me))
    sp = L.get("speech") or {}
    for p in sp.get("paragraphs", []):
        out += [(s, me) for s in sentences(p)]
    for p in sp.get("patterns", []):
        out.append((p["en"], me))
    for ln in (L.get("dialogue") or {}).get("lines", []):
        out.append((ln["en"], them if ln["s"] == "o" else me))
    for r in L.get("rescue", []):
        out.append((r["en"], me))
    seen, uniq = set(), []
    for t, v in out:
        if t not in seen:
            seen.add(t)
            uniq.append((t, v))
    return uniq


def spoken(text):
    return re.sub(r"_{2,}", " blank ", text)


async def synth_edge(text, voice, path):
    import edge_tts
    await edge_tts.Communicate(spoken(text), voice).save(str(path))


async def synth_azure(text, voice, path):
    key, region = os.environ["AZURE_SPEECH_KEY"], os.environ["AZURE_SPEECH_REGION"]
    ssml = (f'<speak version="1.0" xml:lang="en-AU"><voice name="{voice}">'
            f"{escape(spoken(text))}</voice></speak>")
    req = urllib.request.Request(
        f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
        data=ssml.encode("utf-8"),
        headers={"Ocp-Apim-Subscription-Key": key, "Content-Type": "application/ssml+xml",
                 "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
                 "User-Agent": "gday-daily-speaking"})
    data = await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=60).read())
    path.write_bytes(data)


SYNTH = {"edge": synth_edge, "azure": synth_azure}[ENGINE]


def find_ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return None


FFMPEG = find_ffmpeg()

# kbps by bitrate index, for MPEG-1 and MPEG-2/2.5 Layer III
_KBPS = {3: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
         2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160]}


_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def bitrate(path):
    """Bitrate of the first MP3 audio frame in kbps (skipping a Xing/Info tag frame), or None."""
    b = path.read_bytes()[:16384]
    i = 0
    if b[:3] == b"ID3":
        i = 10 + ((b[6] & 0x7F) << 21 | (b[7] & 0x7F) << 14 | (b[8] & 0x7F) << 7 | (b[9] & 0x7F))
    while i + 3 < len(b):
        version, idx, sr = (b[i + 1] >> 3) & 3, b[i + 2] >> 4, (b[i + 2] >> 2) & 3
        if b[i] == 0xFF and b[i + 1] & 0xE0 == 0xE0 and version != 1 and 0 < idx < 15 and sr < 3:
            kbps = _KBPS[3 if version == 3 else 2][idx]
            if b"Xing" not in b[i:i + 64] and b"Info" not in b[i:i + 64]:
                return kbps
            rate = _RATES[version][sr]  # tag frame: step over it to the first audio frame
            i += (144 if version == 3 else 72) * kbps * 1000 // rate + ((b[i + 2] >> 1) & 1)
            continue
        i += 1
    return None


def shrink(src, dst):
    """Re-encode src to BITRATE at dst; without ffmpeg just move it. Returns True if re-encoded."""
    if FFMPEG:
        tmp = dst.with_suffix(".tmp")
        r = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                            "-ac", "1", "-ar", "24000", "-b:a", f"{BITRATE}k", "-f", "mp3", str(tmp)])
        if r.returncode == 0:
            tmp.replace(dst)
            if src != dst:
                src.unlink()
            return True
        tmp.unlink(missing_ok=True)
    if src != dst:
        src.replace(dst)
    return False


def shrink_all():
    if not FFMPEG:
        sys.exit("ffmpeg not found: install it or pip install imageio-ffmpeg")
    clips = sorted(AUDIO.glob("*/*.mp3"))
    before = sum(f.stat().st_size for f in clips)
    done = 0
    for f in clips:
        kbps = bitrate(f)
        if kbps is None or kbps > BITRATE:
            done += shrink(f, f)
    after = sum(f.stat().st_size for f in clips)
    print(f"{done} of {len(clips)} clips re-encoded; {before / 1e6:.1f} MB -> {after / 1e6:.1f} MB")


async def make(lesson_id):
    L = json.loads((LESSONS / f"{lesson_id}.json").read_text(encoding="utf-8"))
    out_dir = AUDIO / lesson_id
    out_dir.mkdir(parents=True, exist_ok=True)
    files, todo = {}, []
    for text, vk in lines_for(L):
        voice = VOICES[vk]
        name = hashlib.sha1(f"{voice}|{text}".encode("utf-8")).hexdigest()[:12] + ".mp3"
        files[text] = name
        if not (out_dir / name).exists():
            todo.append((text, voice, out_dir / name))

    sem = asyncio.Semaphore(4)

    async def one(text, voice, path):
        async with sem:
            for attempt in range(3):
                try:
                    tmp = path.with_suffix(".part")
                    await SYNTH(text, voice, tmp)
                    await asyncio.to_thread(shrink, tmp, path)
                    return True
                except Exception as e:
                    if attempt == 2:
                        print(f"  failed: {text[:50]!r}: {e}")
                    await asyncio.sleep(2 * (attempt + 1))
        return False

    ok = await asyncio.gather(*(one(*t) for t in todo))
    files = {t: n for t, n in files.items() if (out_dir / n).exists()}
    for f in out_dir.iterdir():  # drop clips for lines that were edited or removed
        if f.name not in files.values():
            f.unlink()
    (AUDIO / f"{lesson_id}.json").write_text(
        json.dumps({"engine": ENGINE, "files": files}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{lesson_id}: {len(files)} clips ({sum(ok)} new, {len(todo) - sum(ok)} failed)")


def main():
    if sys.argv[1:] == ["--shrink-all"]:
        return shrink_all()
    ids = sys.argv[1:] or sorted(p.stem for p in LESSONS.glob("????-??-??.json"))
    for i in ids:
        asyncio.run(make(i))


if __name__ == "__main__":
    main()
