"""Automated YouTube Shorts pipeline.

script (Claude API) -> voice (edge-tts) -> word-timed captions (ASS)
-> vertical 1080x1920 video (ffmpeg) -> upload (YouTube Data API v3).

Env vars: GEMINI_API_KEY (free) or ANTHROPIC_API_KEY (paid), YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
Optional: DRY_RUN=1 (render only, no upload)
"""
import asyncio
import datetime as dt
import json
import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
OUT = ROOT / "out"
HISTORY = ROOT / "history.json"
W, H, FPS = 1080, 1920, 30


# ----------------------------------------------------------------- script ---
def load_history():
    try:
        return json.loads(HISTORY.read_text(encoding="utf-8"))
    except Exception:
        return []


def _gemini(prompt):
    """Free tier via Google AI Studio key. Tries each model until one works."""
    key = os.environ["GEMINI_API_KEY"]
    last = None
    for model in CFG["gemini_models"]:
        for attempt in range(2):
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key, "content-type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}],
                      "generationConfig": {"responseMimeType": "application/json",
                                           "temperature": 0.9, "maxOutputTokens": 2048}},
                timeout=90,
            )
            if r.status_code == 200:
                parts = r.json()["candidates"][0]["content"]["parts"]
                out = "".join(p.get("text", "") for p in parts)
                if out.strip():
                    print("llm model:", model)
                    return out
                last = f"{model}: empty answer"
                break
            last = f"{model}: HTTP {r.status_code} {r.text[:200]}"
            if r.status_code == 429 and attempt == 0:
                time.sleep(20)  # per-minute limit: wait once, retry same model
                continue
            break  # 404 / quota 0 / other: try next model
    raise RuntimeError(f"all Gemini models failed. last: {last}")


def _anthropic(prompt):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01", "content-type": "application/json"},
        json={"model": CFG["claude_model"], "max_tokens": 1200,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=90,
    )
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"])


def call_llm(prompt):
    return (_anthropic if CFG.get("llm_provider") == "anthropic" else _gemini)(prompt)


def generate_script(history):
    recent = [h["title"] for h in history[-60:]]
    prompt = f"""You write scripts for YouTube Shorts voice-overs.
Niche: {CFG['niche']}
Language: {CFG['language']}

Rules:
- {CFG['words_min']}-{CFG['words_max']} words, spoken style, short sentences.
- First sentence is a hook that creates curiosity or surprise (no greetings, no "did you know").
- Only include claims you are highly confident are true and widely documented.
  If a number is approximate, say "about". No medical, financial or legal advice.
  No claims about named living people. No hate, no graphic content.
- End with a one-line payoff or open loop. No "like and subscribe".
- Do NOT repeat or closely resemble these earlier topics: {recent}

Return ONLY a JSON object, no markdown fences:
{{"title": "<max 80 chars, curiosity-driven, no hashtags>",
  "script": "<the full voice-over text, plain sentences, no stage directions>",
  "description": "<1-2 sentences>",
  "tags": ["5-8 short keywords"]}}"""
    text = call_llm(prompt)
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    data = json.loads(text[text.index("{"): text.rindex("}") + 1])
    for k in ("title", "script", "description", "tags"):
        if k not in data:
            raise ValueError(f"model output missing '{k}'")
    n = len(data["script"].split())
    if not (CFG["words_min"] - 25 <= n <= CFG["words_max"] + 40):
        raise ValueError(f"script length {n} words is out of range")
    return data


# ------------------------------------------------------------------ voice ---
def probe_duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)], text=True)
    return float(out.strip())


async def _tts(text, audio_path):
    import edge_tts
    try:
        comm = edge_tts.Communicate(text, CFG["voice"], rate=CFG["voice_rate"],
                                    boundary="WordBoundary")
    except TypeError:  # older edge-tts without the boundary argument
        comm = edge_tts.Communicate(text, CFG["voice"], rate=CFG["voice_rate"])
    words = []
    with open(audio_path, "wb") as f:
        async for ch in comm.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                words.append((ch["text"], ch["offset"] / 1e7, ch["duration"] / 1e7))
    return words


def make_voice(text, audio_path):
    words = asyncio.run(_tts(text, audio_path))
    dur = probe_duration(audio_path)
    if len(words) >= max(5, len(text.split()) // 2):
        # real word timings from the TTS service
        timed = [(w, s, min(s + d, dur)) for w, s, d in words]
    else:
        # fallback: spread words over the audio proportionally to length
        toks = text.split()
        total = sum(len(t) + 1 for t in toks)
        t, timed = 0.0, []
        for tok in toks:
            d = dur * (len(tok) + 1) / total
            timed.append((tok, t, t + d))
            t += d
    return timed, dur


# --------------------------------------------------------------- captions ---
def _ts(sec):
    cs = int(round(sec * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def make_ass(timed, path, chunk=3):
    """Word-by-word captions: chunks of N words, current word highlighted."""
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {W}\nPlayResY: {H}\nWrapStyle: 0\n\n"
        "[V4+ Styles]\n"
        "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,"
        "BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,"
        "BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
        "Style: Default,DejaVu Sans,84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
        "-1,0,0,0,100,100,0,0,1,8,2,5,60,60,0,1\n\n"
        "[Events]\nFormat: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
    )
    lines = []
    for i in range(0, len(timed), chunk):
        grp = timed[i:i + chunk]
        for j, (_, s, e) in enumerate(grp):
            end = grp[j + 1][1] if j + 1 < len(grp) else (
                timed[i + chunk][1] if i + chunk < len(timed) else e + 0.25)
            parts = []
            for k, (w, _, _) in enumerate(grp):
                w = w.replace("{", "(").replace("}", ")").upper()
                parts.append(r"{\c&H0000E5FF&}" + w + r"{\c&H00FFFFFF&}" if k == j else w)
            lines.append(f"Dialogue: 0,{_ts(s)},{_ts(end)},Default,,0,0,0,,{' '.join(parts)}")
    Path(path).write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------------------- background ---
def make_gradient(path, seed):
    from PIL import Image
    rnd = random.Random(seed)
    palettes = [((15, 12, 41), (48, 43, 99), (36, 36, 62)),
                ((10, 25, 47), (23, 87, 130), (8, 60, 90)),
                ((30, 10, 40), (120, 30, 90), (60, 20, 70)),
                ((8, 30, 25), (20, 110, 90), (10, 60, 55))]
    a, b, c = rnd.choice(palettes)
    w, h = W + 220, H + 380
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / (h - 1)
        base = [a[i] + (b[i] - a[i]) * (t / .6) for i in range(3)] if t < .6 else \
               [b[i] + (c[i] - b[i]) * (t - .6) / .4 for i in range(3)]
        for x in range(w):
            g = 1 + 0.12 * (x / w - 0.5)
            px[x, y] = tuple(max(0, min(255, int(v * g))) for v in base)
    img.save(path)


def pick_background(dur, seed):
    vids = sorted((ROOT / "assets" / "backgrounds").glob("*.mp4"))
    if vids:
        return "video", random.Random(seed).choice(vids)
    png = OUT / "bg.png"
    make_gradient(png, seed)
    return "image", png


# ----------------------------------------------------------------- render ---
def render(audio, ass, dur, out_mp4, seed):
    kind, bg = pick_background(dur, seed)
    total = dur + 0.6
    music = sorted((ROOT / "assets" / "music").glob("*.mp3"))
    if kind == "video":
        inputs = ["-stream_loop", "-1", "-i", str(bg)]
        vf = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,"
              f"crop={W}:{H},setsar=1,fps={FPS},subtitles={ass.name}[v]")
    else:
        inputs = ["-loop", "1", "-framerate", str(FPS), "-i", str(bg)]
        vf = (f"[0:v]crop={W}:{H}:x='(iw-{W})*0.5*(1+sin(t/6))':"
              f"y='(ih-{H})*t/{total:.2f}',setsar=1,subtitles={ass.name}[v]")
    inputs += ["-i", str(audio)]
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(random.Random(seed).choice(music))]
        af = "[1:a]apad[a0];[2:a]volume=0.10[a1];[a0][a1]amix=inputs=2:duration=first:normalize=0[a]"
    else:
        af = "[1:a]apad[a]"
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs,
           "-filter_complex", f"{vf};{af}", "-map", "[v]", "-map", "[a]",
           "-t", f"{total:.2f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
           str(out_mp4.name)]
    subprocess.run(cmd, check=True, cwd=OUT)  # cwd=OUT so subtitles path needs no escaping


# ----------------------------------------------------------------- upload ---
def upload(mp4, data):
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        scopes=["https://www.googleapis.com/auth/youtube.upload"],
    )
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    tags = [t.lstrip("#") for t in data["tags"]][:10]
    desc = data["description"].strip() + "\n\n" + " ".join(CFG["hashtags"])
    body = {
        "snippet": {"title": data["title"][:95], "description": desc, "tags": tags,
                    "categoryId": CFG["category_id"]},
        "status": {"privacyStatus": CFG["privacy"], "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": True},  # AI-voice disclosure
    }
    media = MediaFileUpload(str(mp4), mimetype="video/mp4", resumable=True)
    try:
        req = yt.videos().insert(part="snippet,status", body=body, media_body=media)
        resp = None
        while resp is None:
            _, resp = req.next_chunk()
    except HttpError as e:
        if e.resp.status == 400 and "containsSyntheticMedia" in str(e):
            body["status"].pop("containsSyntheticMedia")
            media = MediaFileUpload(str(mp4), mimetype="video/mp4", resumable=True)
            resp = yt.videos().insert(part="snippet,status", body=body,
                                      media_body=media).execute()
        else:
            raise
    return resp["id"]


# ------------------------------------------------------------------- main ---
def one_video(history, dry):
    OUT.mkdir(exist_ok=True)
    data = generate_script(history)
    print("TITLE:", data["title"])
    stamp = dt.datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    audio, ass, mp4 = OUT / f"{stamp}.mp3", OUT / f"{stamp}.ass", OUT / f"{stamp}.mp4"
    timed, dur = make_voice(data["script"], audio)
    print(f"voice: {dur:.1f}s, {len(timed)} words")
    make_ass(timed, ass)
    render(audio, ass, dur, mp4, seed=stamp)
    vid = None
    if dry:
        print("DRY_RUN: skipped upload ->", mp4)
    else:
        vid = upload(mp4, data)
        print("uploaded:", f"https://youtube.com/shorts/{vid}")
    history.append({"date": stamp, "title": data["title"], "video_id": vid})
    HISTORY.write_text(json.dumps(history, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    dry = os.environ.get("DRY_RUN") == "1"
    hist = load_history()
    for _ in range(int(CFG["videos_per_run"])):
        one_video(hist, dry)
    sys.exit(0)
