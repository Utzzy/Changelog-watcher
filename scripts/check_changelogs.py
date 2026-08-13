#!/usr/bin/env python3
"""
Checks configured changelog sources for new versions/posts.
If something changed since the last run, it asks GitHub Copilot CLI
to write a short German summary and emails it.

Env vars required:
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, MAIL_FROM, MAIL_TO
  COPILOT_GITHUB_TOKEN  (fine-grained PAT with "Copilot Requests" permission)

Exit code 0 always (errors are logged, not fatal) except for config errors.
"""
import json
import os
import re
import smtplib
import subprocess
import sys
import hashlib
from email.mime.text import MIMEText
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
SOURCES_FILE = ROOT / "scripts" / "sources.json"
STATE_FILE = ROOT / "state" / "versions.json"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; changelog-watcher/1.0; "
                  "+https://github.com/) requests"
}
TIMEOUT = 20


def load_json(path, default):
    if not path.exists():
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.text


def strip_html(html):
    text = re.sub(r"<script.*?</script>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_marker(source, raw):
    """Returns a short string identifying the 'current state' of this source
    (a version number, or a content hash as fallback)."""
    method = source["method"]
    if method == "regex":
        match = re.search(source["version_regex"], raw)
        return match.group(1) if match else None
    if method == "rss":
        # Take the title of the first <item>/<entry> as the marker
        match = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                           raw, flags=re.S)
        # skip the feed's own title (first match is usually the feed title)
        titles = re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>",
                             raw, flags=re.S)
        return titles[1].strip() if len(titles) > 1 else (
            titles[0].strip() if titles else None)
    if method == "hash":
        text = strip_html(raw)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    raise ValueError(f"Unknown method: {method}")


def excerpt_for_summary(source, raw, max_chars=6000):
    """Grabs the readable text that should go into the LLM prompt."""
    if source["method"] == "rss":
        # Use the first item's title + description
        items = re.findall(r"<item>(.*?)</item>", raw, flags=re.S)
        if not items:
            items = re.findall(r"<entry>(.*?)</entry>", raw, flags=re.S)
        text = strip_html(items[0]) if items else strip_html(raw)
    else:
        text = strip_html(raw)
    return text[:max_chars]


def call_copilot(prompt):
    """Runs Copilot CLI in non-interactive mode and returns the response text."""
    env = os.environ.copy()
    result = subprocess.run(
        [
            "copilot",
            "-p", prompt,
            "-s",                 # quiet/pipe-friendly output
            "--no-ask-user",      # never pause for clarification
            "--allow-all",        # no filesystem/network actions needed, just text-gen
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"copilot CLI failed (exit {result.returncode}): {result.stderr[:2000]}"
        )
    return result.stdout.strip()


def send_mail(subject, body):
    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    pw = os.environ["SMTP_PASS"]
    mail_from = os.environ.get("MAIL_FROM", user)
    mail_to = os.environ["MAIL_TO"]

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = mail_from
    msg["To"] = mail_to

    with smtplib.SMTP(host, port, timeout=30) as server:
        server.starttls()
        server.login(user, pw)
        server.sendmail(mail_from, [mail_to], msg.as_string())


def main():
    sources = load_json(SOURCES_FILE, [])
    state = load_json(STATE_FILE, {})

    changed = []  # list of dicts: id, name, old, new, excerpt, url
    errors = []

    for source in sources:
        sid = source["id"]
        try:
            if source["method"] == "listing":
                # Overview page only lists titles/links, not the actual change
                # text. Find the newest matching entry, then fetch that
                # specific detail page separately for the excerpt.
                overview_raw = fetch(source["url"])
                match = re.search(source["listing_regex"], overview_raw)
                if not match:
                    errors.append(
                        f"{source['name']}: kein Eintrag über die Listing-Regex "
                        f"gefunden (Seitenstruktur evtl. geändert?)"
                    )
                    continue
                captured = match.group(1)
                if captured.startswith("http"):
                    detail_url = captured
                else:
                    base = source.get("base_url", "").rstrip("/")
                    detail_url = base + "/" + captured.lstrip("/")
                marker = detail_url  # each release has its own URL -> unique
                old = state.get(sid)
                if old != marker:
                    detail_raw = fetch(detail_url)
                    changed.append({
                        "id": sid,
                        "name": source["name"],
                        "old": old,
                        "new": marker,
                        "url": detail_url,
                        "excerpt": strip_html(detail_raw)[:6000],
                    })
                    state[sid] = marker
                continue

            if source["method"] == "discourse_topic":
                # Tracks a single growing forum thread (e.g. McNeel's
                # "Rhino 8 Service Release Available") via Discourse's public
                # JSON API instead of scraping JS-rendered HTML.
                topic_json_url = source["topic_url"].rstrip("/") + ".json"
                topic_data = json.loads(fetch(topic_json_url))
                posts_count = topic_data.get("posts_count")
                if posts_count is None:
                    errors.append(
                        f"{source['name']}: 'posts_count' nicht in der "
                        f"Discourse-API-Antwort gefunden (Forum umgebaut?)"
                    )
                    continue
                marker = str(posts_count)
                old = state.get(sid)
                if old != marker:
                    highest = topic_data.get("highest_post_number", posts_count)
                    topic_id = source["topic_id"]
                    post_json_url = (
                        f"https://discourse.mcneel.com/posts/by_number/"
                        f"{topic_id}/{highest}.json"
                    )
                    post_data = json.loads(fetch(post_json_url))
                    cooked_html = post_data.get("cooked", "")
                    changed.append({
                        "id": sid,
                        "name": source["name"],
                        "old": old,
                        "new": marker,
                        "url": f"{source['topic_url']}/{highest}",
                        "excerpt": strip_html(cooked_html)[:6000],
                    })
                    state[sid] = marker
                continue

            raw = fetch(source["url"])
            marker = extract_marker(source, raw)
            if marker is None:
                errors.append(f"{source['name']}: konnte keine Version/Marker "
                               f"extrahieren (Seitenstruktur evtl. geändert?)")
                continue

            old = state.get(sid)
            if old != marker:
                changed.append({
                    "id": sid,
                    "name": source["name"],
                    "old": old,
                    "new": marker,
                    "url": source["url"],
                    "excerpt": excerpt_for_summary(source, raw),
                })
                state[sid] = marker
        except Exception as e:  # noqa: BLE001
            errors.append(f"{source['name']}: Fehler beim Abrufen ({e})")

    # First run: everything looks "new" because state was empty.
    # Store the baseline silently, don't spam a mail with 5 items on day one.
    first_run = not STATE_FILE.exists() or load_json(STATE_FILE, {}) == {}
    if first_run:
        print("Erster Lauf: Baseline wird gespeichert, keine Mail wird verschickt.")
        save_json(STATE_FILE, state)
        if errors:
            print("Hinweise:\n" + "\n".join(errors), file=sys.stderr)
        return

    if not changed:
        print("Keine Änderungen gefunden.")
        if errors:
            print("Hinweise:\n" + "\n".join(errors), file=sys.stderr)
        return

    # Build one combined prompt for Copilot CLI so we only make one call.
    sections = []
    for c in changed:
        old_txt = c["old"] if c["old"] else "(unbekannt / erster erkannter Stand)"
        sections.append(
            f"### {c['name']}\n"
            f"Alter Stand: {old_txt}\n"
            f"Neuer Stand: {c['new']}\n"
            f"Quelle: {c['url']}\n"
            f"Rohtext (Auszug):\n{c['excerpt']}\n"
        )

    prompt = (
        "Du bekommst Rohtext-Auszüge von Release-/Changelog-Seiten mehrerer "
        "CAD-/Statik-Programme, bei denen sich seit der letzten Prüfung etwas "
        "geändert hat. Schreibe eine kurze, klare Zusammenfassung auf Deutsch "
        "für einen Anwender dieser Programme. Für jedes Programm: 2-5 "
        "Stichpunkte mit den wichtigsten neuen Funktionen/Änderungen/Bugfixes. "
        "Keine Einleitung, kein Fazit, keine Werbesprache, nur die Fakten. "
        "Nutze Markdown-Überschriften pro Programm.\n\n"
        + "\n\n".join(sections)
    )

    try:
        summary = call_copilot(prompt)
    except Exception as e:  # noqa: BLE001
        # Fall back to sending the raw excerpts if Copilot CLI failed,
        # so you still get notified instead of silently missing the update.
        summary = (
            f"[Automatische Zusammenfassung fehlgeschlagen: {e}]\n\n"
            + "\n\n".join(sections)
        )

    names = ", ".join(c["name"] for c in changed)
    subject = f"Neue Version(en): {names}"
    body = summary
    if errors:
        body += "\n\n---\nHinweise/Fehler bei anderen Quellen:\n" + "\n".join(errors)

    send_mail(subject, body)
    save_json(STATE_FILE, state)
    print(f"Mail verschickt für: {names}")


if __name__ == "__main__":
    main()
