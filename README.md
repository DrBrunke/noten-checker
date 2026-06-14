# CAS Noten-Checker

Ein kleines Python-Tool, das automatisch prüft, ob im CAS-/CampusOffice-System
der Uni eine neue Note eingetragen wurde, und bei einer Änderung eine E-Mail
verschickt. Läuft per Cron im Hintergrund (z. B. alle 30 Minuten).

> **Nur für den Eigengebrauch:** Das Tool liest ausschließlich deine eigenen
> Daten mit deinem eigenen Login – es verändert im CAS-System nichts.

## Wie es funktioniert

Das Skript loggt sich mit deinen Zugangsdaten ein, liest die Notenseite,
vergleicht sie mit dem zuletzt gespeicherten Stand und schickt nur dann eine
Mail, wenn sich etwas geändert hat. Beim ersten Lauf wird nur der Ausgangs-
zustand gespeichert (keine Mail). Alle Zugangsdaten liegen in einer `.env` –
im Code steht nichts Geheimes.

## Installation

```bash
git clone https://github.com/DEIN-NUTZER/noten-checker.git
cd noten-checker
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env        # mit deinen Werten füllen
chmod 600 .env
```

## Konfiguration (`.env`)

| Variable | Bedeutung |
|---|---|
| `CAS_LOGIN_URL` | Seite mit dem Login-Formular |
| `CAS_GRADES_URL` | URL der Notenseite (nach dem Login aus der Adresszeile) |
| `CAS_USERNAME` / `CAS_PASSWORD` | deine Uni-Zugangsdaten |
| `CAS_FIELD_USER` / `CAS_FIELD_PASS` | `name`-Attribute der Login-Felder (bei CampusOffice oft `u` / `p`) |
| `CAS_GRADES_SELECTOR` | CSS-Selektor der Notentabelle (z. B. `table.hierarchy`); leer = ganze Seite |
| `SMTP_*` / `MAIL_TO` | Mail-Versand (GMX voreingestellt) |

**Login-Feldnamen finden:** Login-Seite im Browser öffnen, Rechtsklick auf das
Feld → „Untersuchen" → `name="..."` ablesen.

**Notentabelle eingrenzen:** Ohne Selektor wird die ganze Seite verglichen, was
zu Fehlalarmen durch wechselnde Menü-/Datumselemente führen kann. Besser einen
Selektor setzen, der nur die Notentabelle trifft (bei CampusOffice
`table.hierarchy`).

## Test

```bash
source venv/bin/activate
python3 noten_checker.py
```

Beim ersten Lauf erscheint „Erster Lauf – Ausgangszustand gespeichert". Mehrfach
ausführen sollte „Keine Aenderung." zeigen. Den Mailversand kannst du testen,
indem du in `noten_state.json` eine Zeile entfernst und erneut startest.

## Automatisierung (Cron, alle 30 Minuten)

```bash
crontab -e
```

```
*/30 * * * * cd $HOME/noten-checker && $HOME/noten-checker/venv/bin/python $HOME/noten-checker/noten_checker.py >> $HOME/noten-checker/cron.log 2>&1
```

## Sicherheit

- Zugangsdaten stehen **nur** in der `.env` (nie im Code). `chmod 600 .env`.
- `.env`, `noten_state.json` und `page.html` sind in `.gitignore` – damit landen
  weder Passwörter noch persönliche Notendaten im Repository.

## Lizenz

MIT – siehe [LICENSE](LICENSE).
