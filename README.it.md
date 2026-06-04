# linkedin-cli

Client da riga di comando per **LinkedIn**, pensato sia per agenti AI sia per
sviluppatori. Pilota l'API interna Voyager come fa il sito: nessuna API key,
solo i cookie di sessione catturati da un browser reale.

> English: [README.md](README.md) — versione completa.

> [!WARNING]
> LinkedIn vieta l'uso automatizzato nei propri Termini di Servizio
> (sezione 8.2). Questo strumento è per uso personale e di ricerca: comportati
> come un utente normale, rispetta i rate limit, non fare scraping di massa né
> spam. Usalo a tuo rischio, preferibilmente su un account non principale.

## Requisiti

- Python 3.11+
- Un account LinkedIn
- Chromium (installato in automatico da `make install` via Playwright)

## Installazione

```bash
pip install mayai-linkedin-cli
playwright install chromium
```

Installa il comando `linkedin` (e `linkedin-mcp` per il server MCP).

## Quick start

```bash
# 1. Autenticazione — apre una finestra Chromium per fare login normalmente.
linkedin auth login

# 2. Verifica
linkedin auth status

# 3. Cerca una persona (NDJSON con --json)
linkedin --json search people "Mario Rossi"

# 4. Leggi un profilo per public id o URL completo
linkedin --json profile get mario-rossi-9558832a

# 5. Manda una richiesta di connessione (prima in dry-run, se vuoi)
linkedin connections send mario-rossi-9558832a --dry-run
linkedin connections send mario-rossi-9558832a

# 6. Ultime conversazioni in arrivo
linkedin --json messages list
```

## Server MCP

`linkedin-cli` include un server MCP nativo: gli agenti AI (come Claude
Desktop) usano LinkedIn direttamente come tool, senza sottoprocessi né
parsing. Aggiungi a `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "linkedin": { "command": "/percorso/di/linkedin-mcp" }
  }
}
```

Trova il percorso con `which linkedin-mcp`. Dettagli e lista dei tool nel
[README.md](README.md#mcp-server).

## Sicurezza

Le azioni di scrittura sono protette: alla CLI `connections send` e
`messages send` accettano `--dry-run`; i tool MCP corrispondenti richiedono
`confirm=True` esplicito e sono rate-limited per sessione. La CLI rispetta
inoltre quote giornaliere per account e un ritardo casuale tra le richieste,
per non farsi notare dall'antiabuso di LinkedIn.

I cookie stanno cifrati con Fernet in `~/.config/mayai-cli/linkedin/`
(`credentials.json` + `key.bin`, permessi `0600`). Mai password come argomento,
mai cookie in chiaro. Dettagli: [docs/AUTHENTICATION.md](docs/AUTHENTICATION.md).

## Altro

Per il riferimento completo (tutti i comandi, flag, parser Voyager, MCP,
quote, contributi): vedi [README.md](README.md) e [docs/](docs/).

## Licenza

MIT — vedi [LICENSE](./LICENSE).
