# Control Deck — a Discord bot + local UI for Termux

A minimal Discord bot you run on your phone with Termux, controlled from a
local web UI at `http://127.0.0.1:5000` instead of the command line.

## What's in here

- `setup.sh` — Termux system-package check (installs anything missing), then launches the app
- `run.py` — checks/installs the required **Python** packages, then starts the server
- `app.py` — the Flask app: all the `/api/...` routes the UI talks to
- `bot_manager.py` — runs the actual discord.py client in the background
- `bot_commands.py` — the built-in `!commands` and the custom-command sandbox
- `templates/`, `static/` — the UI (Home, Text, Bot, Cmds, Custom tabs)
- `config.json` — created automatically the first time you save a token (kept only on your device)
- `custom_commands.json` — created automatically the first time you save a custom command (kept only on your device)

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
- Type a message and hit **Send message**, and/or fill in the **Embed** box below it (title, description, color, author, footer, thumbnail, image, timestamp, and repeatable fields) and hit **Send embed** — the two send independently

**Bot tab**
- Type a new name and hit **Update** to rename the bot on Discord directly — this fixes a bot name that looks "stuck" after being renamed in the Developer Portal, since it forces a real update instead of relying on a cached one
- Choose an image and hit **Update profile picture** to change the bot's avatar

**Cmds tab**
- Reference list of the built-in commands anyone can type in a channel the bot can see: `!ping`, `!cmds`/`!help`, `!uptime`, `!avatar`, `!userinfo`, `!serverinfo`, `!say`, `!coinflip`, `!roll`, `!8ball`, `!time`
- **Requires "Message Content Intent" turned on** for your bot in the Developer Portal (**Bot** page) — without it, discord.py can't read what people type, so no `!command` will ever trigger. This is separate from the token and has to be flipped on manually per-bot.

**Custom tab**
- Create your own `!command` in Python. The code you write runs as the body of `async def run(ctx): ...`, where `ctx` gives you `ctx.send(...)` to reply, `ctx.args` (the words after the command), `ctx.content` (the raw text after it), and `ctx.message` / `ctx.author` / `ctx.channel` / `ctx.guild` as normal discord.py objects.
- A wide set of modules — `discord`, `random`, `requests`, `datetime`, `json`, `re`, `os`, and more — are already imported, so you don't need to install anything to use them.
- This code runs with full access on the device the bot is on — only add commands you wrote (or trust) yourself.

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
