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
quando la fonte dichiara di essere localizzata. Dopo due tentativi falliti viene
mostrato un avviso in italiano, senza pubblicare il testo originale inglese.
Le traduzioni riuscite vengono conservate in una cache limitata in memoria.

Per ogni gioco il bot prova prima il video Steam e poi YouTube, anche se Telegram
rifiuta il primo video. Se nessun video è inviabile, pubblica il testo con il link
al trailer quando disponibile, senza foto. Non è garantita la disponibilità di
un video per ogni gioco. Le descrizioni lunghe vengono inviate separatamente dal
video per rispettare il limite della didascalia.

La scheda mostra titolo, piattaforma, descrizione italiana (massimo tre righe
logiche da 42 caratteri), scadenza se nota e link testuale "Scarica da".
Telegram può andare ulteriormente a capo in base allo schermo e alla dimensione
del font. Sono rimossi hashtag, prezzo originale e pulsanti del trailer.
Quando il video non è inviabile viene inserito un link nel testo; se non è stato
trovato alcun trailer, il link è esplicitamente una ricerca YouTube.
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
