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



