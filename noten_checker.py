#!/usr/bin/env python3
"""
CAS Noten-Checker
=================
Loggt sich alle 30 Minuten (per Cron) ins CAS-System der Uni ein, liest die
Notenseite aus und schickt eine E-Mail, sobald sich etwas aendert (neue Note,
geaenderte Note, neuer Eintrag).

Konfiguration komplett ueber eine .env-Datei -- es stehen KEINE Zugangsdaten
im Code. Siehe .env.example und README.md.

Abhaengigkeiten:  pip install requests beautifulsoup4 python-dotenv
"""

import hashlib
import json
import logging
import os
import smtplib
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import requests
from bs4 import BeautifulSoup

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv optional; Variablen koennen auch direkt gesetzt sein


# --------------------------------------------------------------------------- #
# Konfiguration (aus Umgebungsvariablen / .env)
# --------------------------------------------------------------------------- #
def cfg(key: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(key, default)
    if required and not val:
        sys.exit(f"FEHLER: Umgebungsvariable {key} ist nicht gesetzt (siehe .env.example)")
    return val


# --- CAS-Zugang ---
CAS_LOGIN_URL   = cfg("CAS_LOGIN_URL", required=True)     # Seite mit dem Login-Formular
CAS_GRADES_URL  = cfg("CAS_GRADES_URL", required=True)    # Seite, auf der die Noten stehen
CAS_USERNAME    = cfg("CAS_USERNAME", required=True)
CAS_PASSWORD    = cfg("CAS_PASSWORD", required=True)

# Namen der Formularfelder im Login-Formular (im Browser per "Element untersuchen" pruefen)
FIELD_USER      = cfg("CAS_FIELD_USER", "username")
FIELD_PASS      = cfg("CAS_FIELD_PASS", "password")

# CSS-Selektor des Bereichs/der Tabelle mit den Noten.
# Leer lassen -> es wird der gesamte sichtbare Text der Notenseite verglichen.
GRADES_SELECTOR = cfg("CAS_GRADES_SELECTOR", "")

# --- E-Mail (GMX als Standard) ---
SMTP_HOST       = cfg("SMTP_HOST", "mail.gmx.net")
SMTP_PORT       = int(cfg("SMTP_PORT", "587"))
SMTP_USER       = cfg("SMTP_USER", required=True)         # deine Mail-Adresse
SMTP_PASSWORD   = cfg("SMTP_PASSWORD", required=True)     # Mail-Passwort / App-Passwort
MAIL_TO         = cfg("MAIL_TO", SMTP_USER)               # Empfaenger (Standard: du selbst)

# --- Monitoring (optional) ---
# Healthchecks.io Ping-URL. Leer lassen -> Monitoring aus.
HEALTHCHECK_URL = cfg("HEALTHCHECK_URL", "")

# --- Dateien ---
BASE_DIR        = Path(__file__).resolve().parent
STATE_FILE      = Path(cfg("STATE_FILE", str(BASE_DIR / "noten_state.json")))
LOG_FILE        = Path(cfg("LOG_FILE", str(BASE_DIR / "noten_checker.log")))
# Zeitstempel des letzten erfolgreichen Laufs (fuer den Waechter / watchdog.py)
HEARTBEAT_FILE  = Path(cfg("HEARTBEAT_FILE", str(BASE_DIR / "last_success.txt")))

USER_AGENT = "Mozilla/5.0 (compatible; NotenChecker/1.0)"


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("noten")


# --------------------------------------------------------------------------- #
# Login + Scraping
# --------------------------------------------------------------------------- #
def login_and_fetch_grades() -> str:
    """Loggt ein und gibt den relevanten Notenseiten-Inhalt als Text zurueck."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    # 1) Login-Seite holen, um versteckte Felder/CSRF-Token mitzunehmen
    resp = session.get(CAS_LOGIN_URL, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Es kann mehrere Formulare geben -> das mit dem Passwort-Feld waehlen
    payload: dict[str, str] = {}
    form = None
    for f in soup.find_all("form"):
        if f.find("input", {"type": "password"}):
            form = f
            break
    if form is None:
        form = soup.find("form")
    if form:
        for inp in form.find_all("input"):
            name = inp.get("name")
            if name:
                payload[name] = inp.get("value", "")
        # Action-URL des Formulars bestimmen (relativ -> absolut)
        action = form.get("action") or CAS_LOGIN_URL
        post_url = requests.compat.urljoin(CAS_LOGIN_URL, action)
    else:
        post_url = CAS_LOGIN_URL

    # Zugangsdaten setzen
    payload[FIELD_USER] = CAS_USERNAME
    payload[FIELD_PASS] = CAS_PASSWORD

    # 2) Login absenden
    resp = session.post(post_url, data=payload, timeout=30)
    resp.raise_for_status()

    # 3) Notenseite abrufen
    resp = session.get(CAS_GRADES_URL, timeout=30)
    resp.raise_for_status()
    page = BeautifulSoup(resp.text, "html.parser")

    # Plausibilitaetscheck: sieht es nach einem fehlgeschlagenen Login aus?
    lowered = resp.text.lower()
    if any(w in lowered for w in ("login fehlgeschlagen", "anmeldung fehlgeschlagen",
                                  "incorrect", "ungueltige", "ungültige")):
        raise RuntimeError("Login scheint fehlgeschlagen zu sein (Fehlermeldung auf der Seite).")

    # Relevanten Bereich extrahieren -- ZEILENWEISE (jede Tabellenzeile = eine Zeile),
    # damit eine Aenderung nur die betroffene Zeile zeigt statt der ganzen Tabelle.
    if GRADES_SELECTOR:
        nodes = page.select(GRADES_SELECTOR)
        if not nodes:
            raise RuntimeError(
                f"Selektor '{GRADES_SELECTOR}' liefert nichts. "
                "Bist du eingeloggt? Selektor pruefen."
            )
        rows: list[str] = []
        for n in nodes:
            trs = n.find_all("tr")
            if trs:                                  # Tabelle -> jede Zeile einzeln
                rows.extend(tr.get_text(" ", strip=True) for tr in trs)
            else:                                    # kein <tr> -> ganzer Knoten
                rows.append(n.get_text(" ", strip=True))
        text = "\n".join(r for r in rows if r.strip())
    else:
        text = page.get_text("\n", strip=True)

    return text


# --------------------------------------------------------------------------- #
# Zustand laden/speichern
# --------------------------------------------------------------------------- #
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            log.warning("State-Datei unlesbar, wird neu angelegt.")
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize(text: str) -> list[str]:
    """In vergleichbare, stabile Zeilen umwandeln (Whitespace egalisiert)."""
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


# --------------------------------------------------------------------------- #
# E-Mail
# --------------------------------------------------------------------------- #
def send_email(subject: str, body: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_USER
    msg["To"] = MAIL_TO
    msg.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.send_message(msg)
    log.info("E-Mail an %s gesendet.", MAIL_TO)


# --------------------------------------------------------------------------- #
# Monitoring (Healthchecks.io)
# --------------------------------------------------------------------------- #
def ping(suffix: str = "") -> None:
    """Pingt Healthchecks.io an (optional). Fehler hier brechen den Job NIE ab."""
    if not HEALTHCHECK_URL:
        return
    try:
        requests.get(HEALTHCHECK_URL + suffix, timeout=10)
    except Exception as exc:
        log.warning("Healthcheck-Ping fehlgeschlagen: %s", exc)


# --------------------------------------------------------------------------- #
# Hauptlogik
# --------------------------------------------------------------------------- #
def run_check() -> None:
    """Eigentliche Pruefung. Wirft bei Fehlern eine Exception (siehe main)."""
    text = login_and_fetch_grades()

    lines = normalize(text)
    # Sortiert hashen -> reine Umsortierung der Zeilen loest KEINEN Alarm aus.
    digest = hashlib.sha256("\n".join(sorted(lines)).encode("utf-8")).hexdigest()

    state = load_state()
    old_digest = state.get("digest")
    old_lines = state.get("lines", [])

    if old_digest is None:
        # Erster Lauf: nur Ausgangszustand speichern, keine Mail
        log.info("Erster Lauf -- Ausgangszustand gespeichert (%d Zeilen).", len(lines))
        save_state({"digest": digest, "lines": lines,
                    "updated": datetime.now().isoformat(timespec="seconds")})
        return

    if digest == old_digest:
        log.info("Keine Aenderung.")
        return

    # Aenderung erkannt -> Unterschiede ermitteln
    old_set, new_set = set(old_lines), set(lines)
    added = [l for l in lines if l not in old_set]
    removed = [l for l in old_lines if l not in new_set]

    log.info("AENDERUNG erkannt: +%d / -%d Zeilen.", len(added), len(removed))

    parts = ["Im CAS-System hat sich etwas geaendert.\n"]
    if added:
        parts.append("Neu / hinzugekommen:\n" + "\n".join(f"  + {l}" for l in added))
    if removed:
        parts.append("\nEntfernt / geaendert (alter Stand):\n" + "\n".join(f"  - {l}" for l in removed))
    parts.append(f"\nNotenseite: {CAS_GRADES_URL}")
    parts.append(f"Zeitpunkt: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
    body = "\n".join(parts)

    send_email("Neue Note im CAS-System!", body)

    save_state({"digest": digest, "lines": lines,
                "updated": datetime.now().isoformat(timespec="seconds")})


def main() -> None:
    ping("/start")
    try:
        run_check()
    except Exception as exc:
        log.error("Lauf fehlgeschlagen: %s", exc)
        ping("/fail")          # Healthchecks sofort ueber Fehler informieren
        sys.exit(1)
    ping()                     # Erfolg -> "ich lebe und habe sauber durchlaufen"
    # Zeitstempel fuer den lokalen Waechter setzen (watchdog.py prueft ihn)
    HEARTBEAT_FILE.write_text(datetime.now().isoformat(timespec="seconds"), encoding="utf-8")


if __name__ == "__main__":
    main()
