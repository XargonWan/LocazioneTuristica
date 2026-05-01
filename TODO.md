Vorrei che tu mi facessi un software da hostarmi via docker compose, il software è per gestire le locazioni turistiche (apartamenti in italia adibiti tipo a bnb o affitti brevi), il software dovrà avere le seguenti caratteristiche.
- scritto un python
- avviabile via docker compose
- db su sqlite montabile del quale la cartella dovrà essere montata su data
- modalità giorno e notte

Il software presenta una barra superiore con i vari menu: Overview, Anagrafiche, Statistiche, Impostazioni

## Anagarafiche

Il software permette di creare una o più profili di property manager (persone addette agli appartamenti), uno o più appartamenti e una o più ditte.
Queste sono chiamate anagrafiche.

Property Manager:
- nome, cognome, ragione fiscale, data di nascita, indirizzo, partita iva o codice fiscale, iban, percentuale (la percentuale che s trattiene per ogni prenotazione)
- appartamento: può essere associato a uno o più appartamenti

Appartamenti:
- nome
- indirizzo
- codice locker
- può essere associato a un property manager

Ditte (ad esempio di pulizia, commercialista, fornitore acqua luce e gase...), puoi trovare un ome più consono a questo elemento.
- anagrafica simile a property manager

Piattaforme
- nome (eg: Airbnb)
- link

## Overview, puoi trovare un ome più consono a questo elemento.

Per ogni appartamento è possibile aggiungere una spesa, le spese vengono raggruppate mensilmente e annualmente nell'overview.
L'overviews presenta in formato verticiale, tutti i mesi, e in una barra sotto abbiamo le schede divise per anno.
Premendo un pulsante [+] si potrà inserire una spesa o un entrata.

### Aggiunta spese
Per semplicità le spese sono solo in euro al momento.
causale
Importo lordo
Importo netto -> se è presente un lordo, calcolato: lordo-22%, sennò campo libero. (l'iva è settabile nelle impostazioni)
associata a... (campo opzionalem può essere associata al PM di riferimento o ad una ditta)
ricorrenza: mai (default), mensile, annuale
Note: campo libero di testo
allegati: (è possibile allegare documenti tipo la fattura)

### Aggiunta entrate
Per semplicità le entrate sono solo in euro al momento.
importo lordo
Importo netto -> se è presente un lordo, calcolato: lordo-22%, sennò campo libero. (l'iva è settabile nelle impostazioni)
importo netto al netto del property manager, dato editabile ma impostato al lordo-percentuale del PM (property Manager)
causale
piattaforma o anagrafica (non obbligatorio)

## Statistiche
Una pagina che mostra varie statistiche con grafici e dati.
Divisa in schede sotto per argomento.

### Per anno e per mese
lordo guadagnato
netto guadagnato
PM saldato/totale

## Per "anagrafica"
Ovvero quando guadagnato o pagato da e verso una singola entità, tipo il property manager, booking.com, commercialista, etc.

## Autenticazione e utenze

- Tipologie utente: Admin (full access) e Read-only (solo visualizzazione)
- Schermata di login con password; se l'utente ha il flag `must_change_password` o la password è assente, verrà richiesto di impostarne una nuova alla prima autentificazione

## Allegati

- Tipi file supportati: PDF, immagini (jpg/jpeg, png), ODT, Excel (xls, xlsx)
- Dimensione massima default: 10MB, impostabile nelle impostazioni
- I file vengono salvati sul filesystem in `./data/attachments` con metadati nel DB

## Ricorrenze

- Non vengono materializzate voci future automaticamente (no job pianificato per ora)
- Quando si crea una spesa/entrata con ricorrenza, la voce viene salvata immediatamente e il flag di ricorrenza resta nel record per future elaborazioni manuali o job futuri

## Lingua e compatibilità

- Lingua principale: Italiano (prevedere i18n in futuro)
- UI mobile/desktop compatibile con browser moderni (Chrome/Firefox/Safari/Edge)

## Requisiti aggiuntivi MVP

- Single-tenant
- Login con admin seed che obbliga a cambiare password al primo accesso
- Giorno/Notte toggle persistente via cookie

## MVP - Implementazioni fatte (progress)

Le seguenti funzionalità sono state implementate nell'MVP:
- Skeleton dell'app FastAPI + templates Jinja2 e Bootstrap
- SQLite DB con SQLAlchemy, file DB e allegati montati in `./data`
- Auth: login, set-password se flag `must_change_password` true; Admin/Read-only roles
- CRUD base per: Property Manager, Appartamenti, Ditte (Company), Piattaforme
- CRUD per Entrate (Incomes) e Spese (Expenses) con calcolo automatico net/gross e PM amount
- Upload e download allegati, link a spese/entrate, limite di upload e controllo MIME
- Overview di base con riepilogo mensile per anno
- Pagine impostazioni con update di default IVA e max_upload_size

## Next steps proposti
- Migliorare UI (responsive, mobile-first), aggiungere grafici con Chart.js
- Implementare export CSV/PDF
- Aggiungere tests e Docker Compose di produzione
- Implementare job pianificati o gestione ricorrenze (cron/celery)
 - Implementare l'edit scoping per voci ricorrenti (già implementato: ora è possibile scegliere se modificare solo la singola occorrenza o tutta la serie quando si modifica un'entrata/spesa con ricorrenza)
 
## Bug fixes (Dec 2025)
- Corretto il problema per cui i PM non venivano mostrati nelle Entrate: i template ora usano i campi serializzati (`associated_pm_name`, `pm_percent`, `pm_amount`) passati dal router invece di accedere a relazioni ORM non disponibili.
- Ripristinata la formattazione della vista mensile (rimosse chiusure HTML duplicate e riportate le classi `table table-sm` per ripristinare i bordi dei mesi dopo Gennaio).
- Fixata la visualizzazione delle modali di conferma eliminazione (spostate fuori dalle modali figlie per evitare overlay/backdrop che oscuravano la conferma).
- Titoli dei dettagli (Entrata/Spesa) ora mostrano la causale/note se presente: `Dettagli Spesa <note>` (fallback a `#id`).
- Aggiunti test `pytest` che verificano il rendering dei template per prevenire regressioni su questi punti.



