# LocazioneTuristica

> [!WARNING]
> L'applicazione è stata sviluppata usando la tecnica del vibe coding, tuttavia, io lo sviluppatore, la uso come daily driver e al momento non ho trovato bug critici ne data loss.

LocazioneTuristica è una semplice ma completa applicazione web per gestire le entrate e le spese di locazioni turistiche (affitti brevi). È pensata per piccole realtà e per chi vuole tenere traccia dei flussi economici, delle spese, delle ripetizioni periodiche (recurrence) e degli allegati relativi a ogni voce.

## Caratteristiche principali ✅
- Gestione di entrate (incomes) e spese (expenses)
- Supporto per ricorrenze: crea serie ricorrenti (mensile/annuale) e materializza le occorrenze future; il form richiede l'intervallo "da"/"fino a" (mese/anno o anno) e permette di iniziare la serie prima della singola voce modificata
- Possibilità di convertire una voce singola in una serie ricorrente durante la modifica
  * quando modifichi una voce già ricorrente, il form pre‑popola tipo, data inizio e data fine;
  * se una ricorrenza esiste ma la voce è stata in precedenza scollegata dalla serie (ad es. modificata singolarmente), il sistema *inferrerà* comunque i dettagli basandosi sulla serie esistente e ti proporrà di #riattaccarla#;
  * cambiando il periodo (spostando indietro o avanti il campo data) la serie viene ricreata e le occorrenze fuori range vengono rimosse
- Gestione di appartamenti, property managers (PM), e piattaforme (per entrate)
- **Facile gestione dei Property Managers (PM)**: interfaccia semplice per aggiungere, modificare e assegnare PM alle unità.
  - Quando si modifica la percentuale di un PM, se esistono entrate o spese già associate al PM che utilizzano la vecchia percentuale, il sistema chiede conferma prima di aggiornare automaticamente quelle voci.
 - Modalità di selezione multipla per modificare o eliminare voci in blocco (multi-select). È disponibile nelle pagine Entrate/Spese e anche nel Rendiconto (vista mensile).
  - Quando si salva una modifica in blocco, viene mostrata una conferma con il messaggio: "Stai modificando X voci, vuoi davvero continuare?".
  - Nota: nella vista Rendiconto le selezioni devono essere tutte dello stesso tipo (solo entrate o solo spese) prima di poter applicare modifiche o eliminazioni in blocco.
- Allegati per ogni voce (PDF, immagini, fogli di calcolo, ecc.)
- **Supporto pulizie**: registrazione di interventi di pulizia associati ad appartamenti. Ogni pulizia genera automaticamente una spesa contrassegnata (🧹) e può essere legata a una ditta/servizio personalizzabile; i costi compaiono nelle statistiche mensili.
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

## Installazione e avvio
L'applicazione è stata sviluppata per essere deployata su un home server con docker compose, non è stata esplorata la possibilità di essere utilizzata standalone ma potrebbe funzionare comunque.
Basterà aggiungere (o lanciare) il `docker-compose.yml` al (o dal) tuo stack.


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

Commenta `image` e decommenta `build` in `docker-compose.yml` per eseguire il codice direttamente presente sulla repo, poi:

```bash
docker compose up -d --build
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


