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
