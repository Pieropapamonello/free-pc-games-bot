# Bot Telegram Giochi PC Gratis

Deploy webhook su Render.

## Comandi e destinatari

- `/formato` permette di scegliere tra schede singole (impostazione iniziale)
  ed elenco per dispositivo. La preferenza riguarda solo la chat corrente,
  viene conservata in Firebase e in `display_prefs.json`, e vale per `/start`,
  `/giochi`, `/cerca` e avvisi automatici. Il riepilogo contiene titoli cliccabili,
  scadenze e condizioni DLC/abbonamento, senza immagini o video.
  I titoli multipiattaforma vengono inseriti una volta nel primo dispositivo
  scelto (PC, Console, Android/iOS), senza un ulteriore gruppo combinato.
  Gli elenchi si dividono solo oltre 4000 unita' UTF-16 di testo visibile:
  URL nascosti e tag HTML non contribuiscono al conteggio. I titoli omettono
  parentesi di store/piattaforma, conservando quelle proprie del nome del gioco.
  Se Telegram rifiuta l'elenco con errore 400 (ad esempio `ENTITIES_TOO_LONG`
  per il volume dei collegamenti), il bot divide il gruppo e ritenta parti piu'
  piccole. Le ricevute degli avvisi vengono salvate per ogni parte riuscita,
  evitando duplicazioni se una parte successiva fallisce. `/status` mostra
  piattaforme, generi e contenuti effettivamente selezionati nella chat.
  Gli elenchi sono ordinati per store (con intestazioni) e poi per scadenza
  crescente; le scadenze sconosciute sono in fondo allo store. Le date mostrate
  omettono l'anno, conservato per ordinamento e verifica delle offerte scadute.
  Amazon Prime compare nell'intestazione dello store senza ripetersi nelle righe.
  In formato elenco le richieste manuali mostrano tutti i risultati;
  nelle schede singole restano i limiti di 12 giochi e 8 risultati di ricerca.
- `/start` iscrive la chat, apre il menu piattaforme e mostra fino a 12 giochi
  disponibili solo nella chat che invia il comando, usando i suoi filtri.
- `/piattaforme` apre il menu senza inviare giochi.
- Le richieste manuali non modificano lo storico globale delle notifiche.
- Il controllo periodico e `/ping` inviano le novità a tutte le chat iscritte,
  rispettando i filtri di ciascuna chat; `/stop` disattiva l'iscrizione della chat.

Test di isolamento delle chat (senza inviare messaggi Telegram):
`python -m unittest discover -s tests -v`.

## Descrizioni e video

Gli elenchi mostrano numeri cliccabili, senza una griglia di pulsanti.
Toccando il numero si apre WhatsApp con nome, piattaforma, scadenza se nota e
link del gioco precompilati. Prime/abbonamento e DLC sono indicati quando
necessari; il link non viene troncato. L'invio resta a scelta dell'utente.
Telegram non permette di associare al numero una copia di testo nascosto:
la pressione prolungata sul numero non copia il messaggio completo.
L'intestazione itch.io viene mostrata come "itch" per evitare un collegamento
automatico alla homepage; i titoli mantengono i link alle pagine dei giochi.

Le descrizioni di tutte le fonti passano dalla traduzione in italiano. I testi
gia' italiani vengono conservati. Si usa la risposta JSON del servizio pubblico
Google Translate, con MyMemory come alternativa e un ultimo tentativo Google.
Le richieste hanno timeout di connessione e lettura; i risultati vuoti, invariati
in inglese o non italiani vengono scartati, considerando l'incertezza del
rilevamento per frasi brevi. MyMemory riceve al massimo 480 byte di testo.
La cache contiene solo traduzioni riuscite, con massimo 2048 elementi in memoria
e nel file `translations_it.json`; Firebase conserva le traduzioni nel nodo
`translations_it`, ricaricato all'avvio. Le traduzioni si riusano tra chat.
Se tutti i servizi falliscono, la scheda resta visibile con un avviso in italiano,
senza pubblicare il testo originale in inglese. Il bot ritenta dopo 15, 45 e 90
secondi e aggiorna lo stesso messaggio se recupera la traduzione. Il recupero
condivide il limite di 5 minuti delle attivita' media. Un guasto prolungato dei
servizi puo' lasciare l'avviso fino alla successiva richiesta del gioco.
I titoli ufficiali dei giochi conservano il nome originale.

Ogni gioco viene mostrato con il banner originale della fonte, descrizione e link
di riscatto. Se il banner manca o Telegram lo rifiuta, viene mostrata la scheda
testuale. La ricerca del trailer avviene in background e non blocca gli altri
risultati. Quando disponibile, il trailer viene allegato alla stessa scheda
tramite `editMessageMedia`, senza inviare un secondo messaggio.
I trailer devono essere ufficiali: italiano prima, inglese come alternativa,
durata effettiva massima 180 secondi. I trailer più
lunghi vengono esclusi, non tagliati. Il file viene scaricato, verificato con
ffprobe, convertito in MP4 H.264/AAC e caricato su Telegram (massimo 45 MB).
Il Dockerfile installa ffmpeg/ffprobe e Node.js (richiesto da yt-dlp per YouTube);
per l'avvio locale devono essere nel PATH. Installare `requirements.txt` in un
ambiente virtuale Python.

Fonti ammesse: video etichettati come trailer nella pagina Steam del titolo
corrispondente; su YouTube, canali verificati il cui nome corrisponde esattamente
a sviluppatore/editore dichiarato su Steam, con titolo del gioco e durata
verificabili. La lingua deve risultare dai metadati del video, dai tag audio o
da un'indicazione esplicita nel titolo. Il semplice parametro `l=italian` della
pagina Steam non prova la lingua del video. Giochi senza corrispondenza Steam,
canali con alias diversi e video senza lingua dichiarata possono essere esclusi.

Se non esiste un trailer verificabile e inviabile, oppure la ricerca fallisce,
la scheda con il banner resta visibile. Non vengono aggiunti pulsanti al video.
Le attività media hanno un limite di 5 minuti e una
coda massima di 256 attività; vengono annullate alla chiusura del bot senza
rimuovere le schede già inviate. Le ricerche senza esito sono memorizzate per 10 minuti;
gli allegati già caricati riusano il file_id Telegram per 6 ore.

Gli invii automatici salvano una ricevuta per gioco e chat in
`delivery_receipts.json` e, se configurato, nel nodo Firebase `deliveries`.
Le ricevute riguardano l'invio della scheda, indipendentemente dal trailer.
Un invio parziale viene ritentato soltanto per le chat ancora da raggiungere;
il gioco viene segnato come completato dopo la consegna a tutti i destinatari
interessati. Le ricevute vengono ricaricate al riavvio. Come per qualunque invio
esterno, un arresto tra la consegna Telegram e il salvataggio della ricevuta può
ancora causare una ripetizione; il lock tra istanze Firebase resta best-effort.

Errori Telegram della singola chat e rate limit non invalidano la cache video
delle altre chat. Due giochi possono essere preparati contemporaneamente;
le richieste per lo stesso titolo condividono il lavoro. Sono supportati video
e audio YouTube separati, e la durata viene confrontata prima e dopo la
conversione per scartare download incompleti. La cache Steam scade dopo un'ora
per i risultati riusciti e dopo un minuto per gli errori temporanei.

La scheda riprende il layout con banner in alto, intestazione "GRATIS SU…",
titolo in grassetto maiuscolo e descrizione leggibile (massimo 280 caratteri),
scadenza se nota e link "Scarica da", separati da spaziatura.
Quando la traduzione non riesce, mostra un breve avviso in italiano.
Telegram può andare ulteriormente a capo in base allo schermo e alla dimensione
del font. Sono rimossi hashtag, prezzo originale e pulsanti del trailer.
Le condizioni di abbonamento e la distinzione DLC restano visibili.

## Fonti

- itch.io diretto: prime tre pagine dei giochi in offerta, soltanto prezzo zero
  e sconto 100%, con piattaforme riconoscibili. Esclude demo/prologhi/playtest,
  soundtrack e DLC; legge le scadenze dalle pagine ufficiali delle promozioni.
- IndieGala Freebies diretto: verifica il riscatto sulla pagina del gioco e
  richiede una corrispondenza Steam esatta con prezzo normale positivo;
  esclude demo/prologhi/playtest e titoli sempre gratuiti o non verificabili.
- Epic Games: promozioni e prossime uscite gratuite.
- [GamerPower](https://www.gamerpower.com/api-read): giochi di tutte le piattaforme
  disponibili, inclusi Steam, GOG, itch.io, console e mobile, più DLC/loot.
  Si usa una richiesta globale per i giochi invece di una richiesta per store.
- Reddit FreeGameFindings: console/mobile/Prime e segnalazioni PC contrassegnate
  come giochi con link a Steam, Epic, GOG, itch.io, IndieGala o Ubisoft.
- Prime Gaming: raccolta esistente dal canale freegamesnot; richiede abbonamento.
- [MMOBomb](https://www.mmobomb.com/api): giveaway identificati come pacchetti
  e ricompense, visibili attivando DLC/contenuti. Beta, giveaway ambigui e chiavi
  esplicitamente esaurite vengono esclusi.
- [CheapShark](https://apidocs.cheapshark.com/): offerte con prezzo attuale zero
  e prezzo originale maggiore di zero; link di riscatto tramite CheapShark.
  Fino a cinque pagine da 60 offerte, ordinate per prezzo, fermandosi alle offerte
  a pagamento. Descrizione arricchita da Steam/RAWG quando disponibile.

I titoli duplicati vengono unificati per nome e tipo di contenuto, dando priorità
alle promozioni rispetto al catalogo permanente. Gli errori di una fonte non
impediscono di consultare le altre. La disponibilità effettiva va confermata
nella pagina dello store.

I giochi free-to-play permanenti sono esclusi da liste, ricerche e avvisi.
La deduplicazione usa nome normalizzato e tipo di contenuto. Uno stesso gioco
presente in piu' fonti compare una sola volta. Per gli avvisi e le ricevute si
salva anche un identificatore indipendente dalla fonte, evitando ripetizioni
quando una fonte scompare e un'altra trova lo stesso titolo. Vengono riconosciuti
anche i vecchi identificatori GamerPower e Prime. Giochi e DLC/abbonamenti
restano distinti per non nascondere condizioni diverse di riscatto.
Le promozioni con una data di fine gia' passata sono escluse. Quando la fonte
non comunica una data, la scadenza viene omessa dalla scheda e dall'elenco.
I nomi ricavati dagli URL Prime Gaming vengono capitalizzati per la lettura.
Il catalogo FreeToGame non viene piu' consultato. Restano le promozioni gratuite
di giochi normalmente a pagamento e i giveaway di DLC/contenuti, se abilitati.
