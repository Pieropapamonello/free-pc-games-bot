# Bot Telegram Giochi PC Gratis

Deploy webhook su Render.

## Comandi e destinatari

- `/start` iscrive la chat, apre il menu piattaforme e mostra fino a 12 giochi
  disponibili solo nella chat che invia il comando, usando i suoi filtri.
- `/piattaforme` apre il menu senza inviare giochi.
- Le richieste manuali non modificano lo storico globale delle notifiche.
- Il controllo periodico e `/ping` inviano le novità a tutte le chat iscritte,
  rispettando i filtri di ciascuna chat; `/stop` disattiva l'iscrizione della chat.

Test di isolamento delle chat (senza inviare messaggi Telegram):
`python -m unittest discover -s tests -v`.

## Descrizioni e video

Le descrizioni di tutte le fonti passano dalla traduzione in italiano, anche
quando la fonte dichiara di essere localizzata. Dopo due tentativi falliti la
scheda usa una breve alternativa italiana basata sui metadati disponibili,
senza pubblicare il testo originale inglese.
Le traduzioni riuscite vengono conservate in una cache limitata in memoria.

Ogni scheda viene pubblicata con un trailer ufficiale allegato: italiano prima,
inglese come alternativa, durata effettiva massima 180 secondi. I trailer più
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

Se non esiste un trailer verificabile e inviabile, la scheda viene saltata e
resta pendente per i controlli successivi. Nessun ripiego con immagini, pulsanti
o link al video. Le richieste manuali senza risultati inviabili ricevono un
messaggio di stato. Le ricerche senza esito sono memorizzate per 10 minuti;
gli allegati già caricati riusano il file_id Telegram per 6 ore.

Gli invii automatici salvano una ricevuta per gioco e chat in
`delivery_receipts.json` e, se configurato, nel nodo Firebase `deliveries`.
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

La scheda mostra titolo in grassetto, icone per piattaforma e disponibilità,
descrizione italiana in corsivo (massimo 150 caratteri), scadenza se nota e link
testuale "Scarica da", con spaziatura tra intestazione, descrizione e link.
Se la traduzione fallisce, la scheda usa i generi noti e un breve rimando ai
dettagli del gioco: non mostra messaggi tecnici sulla traduzione.
Telegram può andare ulteriormente a capo in base allo schermo e alla dimensione
del font. Sono rimossi hashtag, prezzo originale e pulsanti del trailer.
Le condizioni di abbonamento e la distinzione DLC/free-to-play restano visibili.

## Fonti

- Epic Games: promozioni e prossime uscite gratuite.
- [GamerPower](https://www.gamerpower.com/api-read): giochi di tutte le piattaforme
  disponibili, inclusi Steam, GOG, itch.io, console e mobile, più DLC/loot.
  Si usa una richiesta globale per i giochi invece di una richiesta per store.
- Reddit FreeGameFindings: console/mobile/Prime e segnalazioni PC contrassegnate
  come giochi con link a Steam, Epic, GOG, itch.io, IndieGala o Ubisoft.
- Prime Gaming: raccolta esistente dal canale freegamesnot; richiede abbonamento.
- [FreeToGame](https://www.freetogame.com/api-doc): catalogo free-to-play per PC
  e browser. Disponibile con `/giochi` (massimo 12 risultati) e `/cerca`.
  Al primo controllo automatico i titoli esistenti vengono registrati senza
  notifiche di massa; i successivi ingressi nel catalogo generano avvisi.
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
