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
