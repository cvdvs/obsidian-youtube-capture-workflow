import argparse
import html
import json
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
from youtube_transcript_api import YouTubeTranscriptApi

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


def load_simple_env(env_path: str):
    path = Path(env_path)
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


def parse_csv(text: str):
    return [x.strip() for x in (text or "").split(",") if x.strip()]


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def sanitize_filename(text: str) -> str:
    text = clean_text(text)
    text = re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .-_")
    return (text[:120] or "Untitled").strip()


def extract_video_id(value: str) -> str:
    value = value.strip()

    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value

    parsed = urlparse(value)
    host = parsed.netloc.lower()
    path = parsed.path

    if "youtu.be" in host:
        candidate = path.strip("/").split("/")[0]
        if candidate:
            return candidate

    if "youtube.com" in host or "m.youtube.com" in host:
        if path == "/watch":
            candidate = parse_qs(parsed.query).get("v", [""])[0]
            if candidate:
                return candidate

        for prefix in ("/shorts/", "/embed/", "/live/"):
            if path.startswith(prefix):
                candidate = path[len(prefix):].split("/")[0]
                if candidate:
                    return candidate

    raise ValueError("Could not extract a YouTube video ID from that URL.")


def fetch_oembed(url: str) -> dict:
    response = requests.get(
        "https://www.youtube.com/oembed",
        params={"url": url, "format": "json"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def download_thumbnail(video_id: str, asset_folder: str, safe_title: str) -> str:
    asset_dir = Path(asset_folder)
    asset_dir.mkdir(parents=True, exist_ok=True)

    filename = f"Youtube_{safe_title}_{video_id}.jpg"
    target = asset_dir / filename

    candidates = [
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/sddefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        f"https://i.ytimg.com/vi/{video_id}/default.jpg",
    ]

    for thumb_url in candidates:
        try:
            resp = requests.get(thumb_url, timeout=20)
            if (
                resp.status_code == 200
                and resp.headers.get("content-type", "").startswith("image/")
                and len(resp.content) > 1000
            ):
                target.write_bytes(resp.content)
                return str(target).replace("\\", "/")
        except requests.RequestException:
            continue

    return ""


def transcript_to_paragraphs(raw_items):
    paragraphs = []
    current = []
    current_chars = 0
    prev_end = None

    for item in raw_items:
        text = clean_text(item.get("text", ""))
        if not text:
            continue

        start = float(item.get("start", 0.0))
        duration = float(item.get("duration", 0.0))
        gap = None if prev_end is None else max(0.0, start - prev_end)

        should_break = bool(current) and (
            ((gap is not None) and (gap > 4.0))
            or (len(current) >= 4)
            or ((current_chars + len(text)) > 500)
        )

        if should_break:
            paragraphs.append(" ".join(current))
            current = []
            current_chars = 0

        current.append(text)
        current_chars += len(text) + 1
        prev_end = start + duration

    if current:
        paragraphs.append(" ".join(current))

    return paragraphs


def paragraphs_to_blockquote(paragraphs):
    if not paragraphs:
        return "> Transcript unavailable."

    lines = []

    for i, paragraph in enumerate(paragraphs):
        if i > 0:
            lines.append(">")

        lines.append(f"> {paragraph.strip()}")

    return "\n".join(lines)


def text_to_blockquote(text: str) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    if not text:
        return "> TODO"

    out = []

    for raw_line in text.split("\n"):
        line = raw_line.strip()

        if not line:
            out.append(">")
            continue

        out.append(f"> {line}")

    return "\n".join(out)


def fetch_transcript_data(video_id: str):
    try:
        api = YouTubeTranscriptApi()
        transcript_list = api.list(video_id)

        chosen = None
        preferred = ["en", "en-US", "en-GB"]

        for method_name in ("find_manually_created_transcript", "find_generated_transcript"):
            try:
                chosen = getattr(transcript_list, method_name)(preferred)
                break
            except Exception:
                pass

        if chosen is None:
            all_transcripts = list(transcript_list)
            if not all_transcripts:
                raise RuntimeError("No transcripts found for this video.")
            chosen = all_transcripts[0]

        fetched = chosen.fetch()
        raw = fetched.to_raw_data()
        paragraphs = transcript_to_paragraphs(raw)
        transcript_plain = "\n\n".join(paragraphs)

        return {
            "transcript_language": getattr(chosen, "language_code", ""),
            "transcript_source": "generated" if getattr(chosen, "is_generated", False) else "manual",
            "transcript_plain": transcript_plain,
            "transcript_blockquote": paragraphs_to_blockquote(paragraphs),
        }

    except Exception as e:
        message = clean_text(str(e)) or "Unknown transcript error."
        return {
            "transcript_language": "",
            "transcript_source": "none",
            "transcript_plain": "",
            "transcript_blockquote": f"> Transcript unavailable.\n>\n> {message}",
        }


def summarize_with_openai(title: str, creator: str, transcript_plain: str):
    model = os.environ.get("YOUTUBE_SUMMARY_MODEL", "gpt-5.4-mini").strip() or "gpt-5.4-mini"
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()

    if not transcript_plain:
        return {
            "summary_model": "",
            "summary_blockquote": "> Summary unavailable because the transcript is unavailable.",
        }

    if not api_key or OpenAI is None:
        return {
            "summary_model": "",
            "summary_blockquote": "> TODO",
        }

    try:
        client = OpenAI(api_key=api_key)

        response = client.responses.create(
            model=model,
            max_output_tokens=500,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You turn YouTube transcripts into useful Obsidian note summaries. "
                        "Be accurate, grounded, compact, and unsentimental. "
                        "Do not invent details. If the transcript is messy, summarize only what is clearly supported. "
                        "Output exactly two parts in markdown. "
                        "First, a concise summary in 2 short paragraphs. "
                        "Second, a section titled '### Key takeaways' with 3 to 5 bullet points. "
                        "Do not hard-wrap lines. "
                        "Each paragraph must be a single line. "
                        "Leave one blank line between paragraphs and sections. "
                        "Each bullet point must stay on a single line."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Video title: {title}\n"
                        f"Creator: {creator or 'Unknown'}\n\n"
                        f"Transcript:\n{transcript_plain}"
                    ),
                },
            ],
        )

        summary_text = (response.output_text or "").strip()

        if not summary_text:
            summary_text = "Summary unavailable."

        return {
            "summary_model": model,
            "summary_blockquote": text_to_blockquote(summary_text),
        }

    except Exception as e:
        message = clean_text(str(e)) or "Unknown summary error."
        return {
            "summary_model": model,
            "summary_blockquote": f"> Summary unavailable.\n>\n> {message}",
        }


def main():
    load_simple_env("scripts/.env")

    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--asset-folder", required=True)
    parser.add_argument("--topics", default="")
    parser.add_argument("--tags", default="")
    args = parser.parse_args()

    video_id = extract_video_id(args.url)
    topics = parse_csv(args.topics)
    tags = parse_csv(args.tags)

    if "youtube" not in [t.lower() for t in tags]:
        tags = ["youtube"] + tags

    title = video_id
    creator = ""

    try:
        meta = fetch_oembed(args.url)
        title = meta.get("title") or title
        creator = meta.get("author_name", "")
    except Exception:
        pass

    safe_title = sanitize_filename(title)
    note_filename = f"YouTube - {safe_title}"
    thumbnail_relative = download_thumbnail(video_id, args.asset_folder, safe_title)
    transcript = fetch_transcript_data(video_id)
    summary = summarize_with_openai(title, creator, transcript["transcript_plain"])

    payload = {
        "title": title,
        "safe_title": safe_title,
        "note_filename": note_filename,
        "url": args.url,
        "video_id": video_id,
        "creator": creator,
        "topics": topics,
        "tags": tags,
        "thumbnail_relative": thumbnail_relative,
        "summary_model": summary["summary_model"],
        "summary_blockquote": summary["summary_blockquote"],
        "transcript_language": transcript["transcript_language"],
        "transcript_source": transcript["transcript_source"],
        "transcript_blockquote": transcript["transcript_blockquote"],
    }

    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()
