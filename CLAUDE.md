# linkedin-cli — Istruzioni specifiche

## Cosa fa
CLI per interagire con LinkedIn tramite la sua API interna (non ufficiale).
Non richiede API key. Permette a un agente AI di cercare profili, leggere
messaggi e gestire connessioni da terminale.

## ⚠️ Note legali
LinkedIn vieta l'uso automatizzato nei propri ToS. Questo tool è per uso
personale e di ricerca. Non usare per scraping massivo o spam.
Uso responsabile: simulare il comportamento di un utente normale.

## Come funziona tecnicamente
LinkedIn espone API REST/GraphQL interne che il sito usa per caricare i dati.
Non sono documentate ufficialmente ma sono stabili nel tempo.

**Autenticazione:**
LinkedIn usa cookie di sessione (`li_at`, `JSESSIONID`).
Il flusso di login usa Playwright per aprire un browser reale,
fare login, e catturare i cookie — stesso approccio di x-cli con Chrome.

**Header necessari in ogni richiesta:**
```
csrf-token: <dal cookie jsessionid>
x-li-lang: it_IT
x-li-track: {"clientVersion":"...","osName":"web"}
x-restli-protocol-version: 2.0.0
```

## Comandi da implementare (priorità)

### Profili
```bash
linkedin profile get <url-o-username>         # dettaglio profilo
linkedin profile search "Mario Rossi"         # ricerca persone
linkedin profile search "CTO" --company "MayAI"
```

### Connessioni
```bash
linkedin connections list                     # lista connessioni
linkedin connections pending                  # richieste in attesa
```

### Messaggi
```bash
linkedin messages list                        # lista conversazioni
linkedin messages get <conversation-id>       # leggi conversazione
linkedin messages send <profile-id> "Testo"  # invia messaggio
```

### Auth
```bash
linkedin auth login          # apre browser Playwright per login
linkedin auth status
linkedin auth logout
```

## Struttura file
```
linkedin-cli/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── Makefile
├── linkedin_cli/
│   ├── __init__.py
│   ├── main.py
│   ├── api/
│   │   ├── __init__.py
│   │   ├── client.py          # httpx client con cookie auth
│   │   └── endpoints.py       # URL interni LinkedIn
│   ├── auth/
│   │   ├── __init__.py
│   │   └── browser_login.py   # Playwright login flow
│   ├── models/
│   │   ├── __init__.py
│   │   └── profile.py         # dataclass Profilo
│   └── output/
│       ├── __init__.py
│       └── formatter.py
└── tests/
```

## Note importanti
- Gli endpoint LinkedIn cambiano periodicamente — documentare la versione testata
- Rate limiting aggressivo: aspettare 1-2 secondi tra le richieste
- I cookie scadono — implementare refresh automatico
- Playwright va installato separatamente: `playwright install chromium`
- Se LinkedIn blocca l'IP, aspettare qualche ora prima di riprovare
