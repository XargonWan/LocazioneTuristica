# LocazioneTuristica

LocazioneTuristica è una semplice ma completa applicazione web per gestire le entrate e le spese di locazioni turistiche (affitti brevi). È pensata per piccole realtà e per chi vuole tenere traccia dei flussi economici, delle spese, delle ripetizioni periodiche (recurrence) e degli allegati relativi a ogni voce.

## Caratteristiche principali ✅
- Gestione di entrate (incomes) e spese (expenses)
- Supporto per ricorrenze: crea serie ricorrenti (mensile/annuale) e materializza le occorrenze future
- Possibilità di convertire una voce singola in una serie ricorrente durante la modifica
- Gestione di appartamenti, property managers (PM), e piattaforme (per entrate)
- **Facile gestione dei Property Managers (PM)**: interfaccia semplice per aggiungere, modificare e assegnare PM alle unità.
- Allegati per ogni voce (PDF, immagini, fogli di calcolo, ecc.)
- Interfaccia con modali e conferme inline per un'esperienza utente fluida
- Filtri e vista mensile con riepilogo entrate/spese per mese

## Stack tecnologico 🔧
- Python 3.14
- FastAPI (server web)
- SQLAlchemy (ORM)
- Jinja2 (templating)
- Bootstrap (frontend, modali e layout)
- SQLite (DB locale di default)
- pytest per i test

## Installazione e avvio (sviluppo) 🛠️
Clona il repository e crea un virtual environment:

```bash
git clone <repo-url>
cd LocazioneTuristica
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Esegui l'app in modalità sviluppo con reload:

```bash
UVICORN_RELOAD=1 python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Oppure con Docker Compose:

```bash
docker compose up --build
```

Il volume `./data` viene usato per conservare il database SQLite e gli allegati caricati.

## Inizializzazione del DB e account admin 🧾
Se non sono presenti utenti, è disponibile uno script di seed che crea un amministratore con `must_change_password = true`.

```bash
./scripts/run.sh seed
# oppure
python scripts/seed.py
```

## Tipi di file e limiti upload 📎
- Tipi supportati: PDF, immagini (jpg/png/webp), ODT, XLS/XLSX
- Dimensione massima di upload predefinita: 10 MB (configurabile via tabella settings o variabile d'ambiente)

## Esempi d'uso principali ✨
- Aggiungere una spesa/entrata singola o ricorrente
- Modificare una voce e convertirla in ricorrente (creando la serie e materializzando le occorrenze future)
- Visualizzare riepiloghi mensili e dettaglio voce con allegati
- Eliminare singole occorrenze o intere serie ricorrenti

## Test 🧪
Esegui la suite di test con:

```bash
.venv/bin/python -m pytest -q
```

## Contribuire 🤝
- Apri issue per proposte o bug
- Per modifiche: crea una branch, implementa test quando opportuno e invia una pull request

## Licenza & contatti 📬
Questo progetto è rilasciato sotto la GNU General Public License v3 (GPL-3.0). Il testo completo della licenza è disponibile nel file `LICENSE` nella radice del repository.


