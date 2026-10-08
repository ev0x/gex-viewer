#!/usr/bin/env python3
"""Fetch /daily?format=txt and post it to a Discord channel via webhook.

Standalone and dependency-free (stdlib only) so it runs under cron with the
system python3 — no venv, no pip. Config comes from the environment; a local
`.env` next to this file is loaded automatically if present:

  DISCORD_WEBHOOK_URL   (required) the Discord channel webhook URL
  DAILY_URL             (optional) default http://localhost:5181/daily?format=txt
  DISCORD_USERNAME      (optional) override the webhook's display name

Running the script *is* the manual test — it posts immediately. Cron runs the
exact same command on schedule. Exit code is 0 on success, non-zero on any
failure so cron/`MAILTO` surfaces the problem.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DAILY_URL = "http://localhost:5181/daily?format=txt"
DISCORD_LIMIT = 2000
# Leave headroom for the ``` code-fence wrapper (7 chars) + newlines.
CHUNK_BUDGET = DISCORD_LIMIT - 12


def load_dotenv(path):
    """Minimal .env loader (KEY=VALUE lines). Doesn't override real env vars."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


def fetch_daily(url):
    req = urllib.request.Request(url, headers={"Accept": "text/plain"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8").strip()
    if not text:
        raise RuntimeError("empty response from daily endpoint")
    if "MENTHORQ_API_KEY env var is not set" in text:
        raise RuntimeError("server reports MENTHORQ_API_KEY is not set")
    return text


def chunk_lines(text, budget):
    """Split text into <=budget-char pieces on line boundaries.

    A single line longer than budget is hard-split as a last resort, but the
    /daily output lines are ~330 chars so that path is not normally hit.
    """
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > budget:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:budget])
            line = line[budget:]
        piece = line if not cur else cur + "\n" + line
        if len(piece) > budget:
            chunks.append(cur)
            cur = line
        else:
            cur = piece
    if cur:
        chunks.append(cur)
    return chunks


def post_chunk(webhook, content, username):
    payload = {"content": content, "allowed_mentions": {"parse": []}}
    if username:
        payload["username"] = username
    data = json.dumps(payload).encode("utf-8")
    # Discord/Cloudflare 403s (error 1010) the default Python-urllib UA — send a
    # descriptive one, as Discord's API requires.
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "gex-viewer-post-daily/1.0 (+https://localhost)",
    }
    for _ in range(4):
        req = urllib.request.Request(webhook, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30):
                return
        except urllib.error.HTTPError as e:
            if e.code == 429:  # rate limited — honor Discord's retry_after
                try:
                    wait = json.loads(e.read().decode("utf-8")).get("retry_after", 1.0)
                except Exception:  # noqa: BLE001
                    wait = 1.0
                time.sleep(float(wait) + 0.25)
                continue
            body = e.read().decode("utf-8", "replace")[:200]
            raise RuntimeError(f"Discord returned HTTP {e.code}: {body}") from None
    raise RuntimeError("rate limited by Discord after 4 attempts")


def main():
    load_dotenv(os.path.join(HERE, ".env"))

    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    if not webhook:
        sys.stderr.write("error: DISCORD_WEBHOOK_URL is not set\n")
        return 2
    daily_url = os.environ.get("DAILY_URL", DEFAULT_DAILY_URL).strip()
    username = os.environ.get("DISCORD_USERNAME", "").strip()

    try:
        text = fetch_daily(daily_url)
    except Exception as e:  # noqa: BLE001 — cron wants a clear stderr line
        sys.stderr.write(f"error: fetching {daily_url}: {e}\n")
        return 1

    header = f"**GEX daily levels — {date.today():%a %d %b %Y}**"
    chunks = chunk_lines(text, CHUNK_BUDGET)

    try:
        post_chunk(webhook, header, username)
        for i, chunk in enumerate(chunks):
            post_chunk(webhook, f"```\n{chunk}\n```", username)
            if i < len(chunks) - 1:
                time.sleep(0.5)  # stay clear of the webhook rate limit
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"error: posting to Discord: {e}\n")
        return 1

    print(f"posted {len(chunks)} message(s) ({len(text)} chars) to Discord")
    return 0


if __name__ == "__main__":
    sys.exit(main())
