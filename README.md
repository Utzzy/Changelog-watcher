# Changelog Watcher (cadwork, Allplan, Rhino, Dlubal RFEM/RSTAB)

Prüft alle 6 Stunden per GitHub Actions, ob es bei den konfigurierten
Programmen eine neue Version/einen neuen Release-Post gibt. Bei einer
Änderung lässt das Skript **GitHub Copilot CLI** eine kurze deutsche
Zusammenfassung schreiben und verschickt sie per Mail. Ohne Änderung
passiert nichts (keine leeren Mails).

## 1. Repo einrichten

1. Neues **privates** Repo auf GitHub anlegen (persönlicher Account, keine
   Organisation).
2. Diese Dateien hochladen/pushen.

## 2. Copilot CLI-Zugriff (Personal Access Token)

Da es ein privates Repo unter deinem persönlichen Account ist (nicht
Organisation), läuft die Authentifizierung über ein **Fine-grained Personal
Access Token**:

1. Gehe zu `github.com/settings/personal-access-tokens/new`
2. **Resource owner**: dein persönlicher Account (NICHT eine Organisation)
3. Unter **Account permissions** → **Copilot Requests** → Zugriff aktivieren
4. Repository access kann auf "No access" bleiben (Copilot Requests ist eine
   Account-Berechtigung, keine Repo-Berechtigung)
5. Token generieren und kopieren

⚠️ Es muss ein **Fine-grained Token** sein. Klassische Tokens (`ghp_...`)
funktionieren mit Copilot CLI nicht.

## 3. Mail-Versand (SMTP)

Du brauchst ein Postfach, das SMTP mit App-Passwort erlaubt, z. B. Gmail:

1. Google-Konto → Sicherheit → 2-Faktor-Auth aktivieren (falls noch nicht)
2. "App-Passwörter" → neues Passwort für "Mail" erzeugen
3. Das erzeugte 16-stellige Passwort merken (nicht dein normales Gmail-Passwort!)

Gmail-Werte für die Secrets: `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`.

Andere Anbieter (Outlook, eigener Provider) funktionieren genauso, nur
Host/Port anpassen.

## 4. GitHub Secrets anlegen

Im Repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret          | Beispielwert                     |
|-----------------|-----------------------------------|
| `COPILOT_PAT`   | dein Fine-grained PAT aus Schritt 2 |
| `SMTP_HOST`     | `smtp.gmail.com`                  |
| `SMTP_PORT`     | `587`                             |
| `SMTP_USER`     | `deinname@gmail.com`              |
| `SMTP_PASS`     | das App-Passwort aus Schritt 3    |
| `MAIL_FROM`     | `deinname@gmail.com`              |
| `MAIL_TO`       | die Adresse, an die die Zusammenfassung gehen soll |

## 5. Quellen prüfen/anpassen

Öffne `scripts/sources.json`, falls du etwas anpassen willst. Bei allen
Quellen lohnt sich gelegentlich ein kurzer Blick, ob URLs/Regex noch zur
aktuellen Seitenstruktur passen — Hersteller ändern ihre Webseiten von Zeit
zu Zeit.

Jede Quelle hat ein `method`-Feld:
- `regex`: sucht eine Versionsnummer per Regex im Seitentext
- `rss`: nimmt den Titel des neuesten Feed-Eintrags
- `listing`: für Übersichtsseiten, die nur Titel/Links aber keine
  Änderungstexte enthalten (z. B. Allplan) — findet den neuesten Eintrag
  und lädt zusätzlich dessen Detailseite für die Zusammenfassung
- `hash`: Fallback, wenn es keine saubere Versionsnummer gibt — löst bei
  *jeder* Textänderung der Seite aus (weniger präzise)

**Allplan** ist bereits fertig konfiguriert: Die Erkennung ist bewusst auf
Einträge unter `/2025/` und `/2026/` begrenzt, damit alte 2024er-Releases
nie als "neu" durchgehen.

## 6. Testen

1. Im Repo unter **Actions** → "Changelog Watch" → **Run workflow** (manueller
   Trigger)
2. Der erste Lauf speichert nur den aktuellen Stand als Baseline (keine
   Mail) — das ist gewollt, sonst bekommst du beim ersten Mal 5 Mails auf
   einmal.
3. Ab dem zweiten Lauf bekommst du nur dann eine Mail, wenn sich seit dem
   letzten Lauf wirklich etwas geändert hat.

## 7. Zeitplan anpassen

In `.github/workflows/changelog-watch.yml` steht `cron: "0 */6 * * *"`
(alle 6 Stunden, UTC-Zeit). Für z. B. alle 3 Stunden: `"0 */3 * * *"`, für
einmal täglich morgens: `"0 6 * * *"`.

## Kosten

- **GitHub Actions**: bei einem privaten Repo sind ~2.000 Freiminuten/Monat
  enthalten (Free-Tier) — ein Lauf dauert typischerweise 1-2 Minuten, 4x
  täglich also weit im kostenlosen Rahmen.
- **Copilot CLI**: zieht seit Juni 2026 von deinem AI-Credits-Kontingent
  ab (tokenbasiert). Bei 5 kurzen Changelog-Texten, die nur bei tatsächlichen
  Releases zusammengefasst werden, ist das ein sehr kleiner Verbrauch.
