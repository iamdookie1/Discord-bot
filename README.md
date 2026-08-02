# Control Deck — a Discord bot + local UI for Termux

A minimal Discord bot you run on your phone with Termux, controlled from a
local web UI at `http://127.0.0.1:5000` instead of the command line.

## What's in here

- `setup.sh` — Termux system-package check (installs anything missing), then launches the app
- `run.py` — checks/installs the required **Python** packages (Flask, discord.py), then starts the server
- `app.py` — the Flask app: routes for saving the token, listing servers/channels, and sending messages
- `bot_manager.py` — runs the actual discord.py client in the background
- `templates/`, `static/` — the UI (Home tab, Text tab)
- `config.json` — created automatically the first time you save a token (kept only on your device)

## First-time setup (in Termux)

Install by cloning the repo with git — this is what makes auto-update work,
since `setup.sh` pulls the latest version from GitHub every time it runs.

```bash
pkg install -y python git         # if you don't have these yet
git clone https://github.com/iamdookie1/discord-bot.git
cd discord-bot
bash setup.sh
```

`setup.sh` will:
1. Pull the latest changes from GitHub (`git pull`) — skipped automatically if you're offline or aren't running from a git checkout
2. Make sure the Termux system packages `python`, `git`, `libffi`, `openssl` are present (installs any that are missing)
3. Run `run.py`, which checks whether `flask` and `discord.py` are installed in Python and installs whichever are missing
4. Start the server and print `http://127.0.0.1:5000`

Every time after that, just run:

```bash
bash setup.sh
```

and it'll auto-update itself before starting. If you ever want to update
without launching the app, `git pull` in the project folder does the same
thing `setup.sh` does automatically.

## Using the UI

Open `http://127.0.0.1:5000` in your phone's browser.

**Home tab**
- Paste your bot token and hit **Save & connect**. It's written to `config.json` on your device and the bot connects immediately.
- The strip at the top of the screen shows live connection status (`OFFLINE` / `CONNECTING` / `ONLINE`) and the bot's username once connected.
- **Disconnect** logs the bot out without deleting the saved token.

**Text tab**
- Pick a **server** from the dropdown (populated from the servers your bot is actually in)
- Pick a **channel** in that server
- Type a message and hit **Send message**

## Getting a bot token

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**
2. Open the **Bot** tab → **Reset Token** → copy it
3. Under **OAuth2 → URL Generator**, check `bot`, then under bot permissions check at least `Send Messages`, and use the generated link to invite the bot to your server
4. Paste the token into the Home tab

Keep the token private — anyone with it can control your bot.

## Troubleshooting

- **pip fails to build discord.py / aiohttp**: run `pkg install -y rust binutils` in Termux, then re-run `setup.sh`. Some Termux versions need Rust to build one of discord.py's dependencies.
- **"No servers found"** on the Text tab: make sure the status strip says `ONLINE` first, and that the bot has actually been invited to a server.
- **Login failed**: the token was rejected — copy it again from the Developer Portal (resetting the token invalidates the old one).
