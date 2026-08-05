# Control Deck — a Discord bot + local UI for Termux

A minimal Discord bot you run on your phone with Termux, controlled from a
local web UI at `http://127.0.0.1:5000` instead of the command line.
.
## What's in here

- `setup.sh` — Termux system-package check (installs anything missing), then launches the app
- `run.py` — checks/installs the required **Python** packages, then starts the server
- `app.py` — the Flask app: all the `/api/...` routes the UI talks to
- `bot_manager.py` — runs the actual discord.py client in the background
- `bot_commands.py` — utility + moderation `!commands`, on/off toggle storage, per-user command cooldowns, and the custom-command sandbox
- `bot_music.py` — the `!join`/`!play`/`!menu`/... voice commands, the interactive now-playing menu, and playback state
- `bot_rp.py` — the `!kiss`/`!hug`/... roleplay commands and their GIF storage
- `bot_backup.py` — server structure snapshot/restore for the Backup tab (web UI only, no chat command)
- `templates/`, `static/` — the UI (Home, Text, Bot, Cmds, Custom, RP, Backup tabs)
- `config.json` — created automatically the first time you save a token or set a presence (kept only on your device)
- `custom_commands.json`, `command_settings.json`, `rp_commands.json`, `warnings.json`, `server_backups.json` — created automatically as you use the Custom/Cmds/RP/Backup tabs (all kept only on your device, none of it committed to git)

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
- If something's playing in that server's voice channel, a **Music** card appears with the same controls as the Discord menu (pause/resume, skip, stop, volume, loop) — it polls every second instead of Discord's 5, so it feels a lot snappier

**Bot tab**
- Type a new name and hit **Update** to rename the bot on Discord directly — this fixes a bot name that looks "stuck" after being renamed in the Developer Portal, since it forces a real update instead of relying on a cached one
- Choose an image and hit **Update profile picture** to change the bot's avatar
- **Presence**: set what shows under the bot's name in the member list (Playing/Watching/Listening to/Competing in + text). Saved and reapplied automatically every time the bot connects.

**Cmds tab** — 50+ built-in commands across three categories, each with an on/off toggle, plus a search box to find one quickly:
- **Utility** (16): `!ping`, `!cmds`/`!help`, `!uptime`, `!avatar`, `!userinfo`, `!serverinfo`, `!say` (also deletes your original message), `!coinflip`, `!roll`, `!8ball`, `!time`, `!calc`, `!choose`, `!reverse`, `!remind`
- **Moderation** (16): `!kick`, `!ban`, `!softban`, `!unban`, `!timeout`, `!untimeout`, `!warn`, `!warnings`, `!clearwarnings`, `!purge`, `!slowmode`, `!lock`, `!unlock`, `!nick`, `!addrole`, `!removerole` — every one of these checks the caller has the matching Discord permission (and that the bot does too) before running anything, and refuses with a clear message if not
- **Music** (9): `!join`, `!leave`, `!play`, `!menu`, `!pause`, `!resume`, `!skip`, `!stop`, `!queue` — needs the `ffmpeg` binary, `PyNaCl` (voice encryption), and `davey` (Discord's now-mandatory DAVE end-to-end voice encryption, required since March 2026); `setup.sh` tries to install all of it automatically on Termux, but if any piece is missing `!play` tells you instead of failing silently. `!play` (and `!menu`) show an interactive now-playing menu — see below.
- **Requires "Message Content Intent" turned on** for your bot in the Developer Portal (**Bot** page) — without it, discord.py can't read what people type, so no `!command` will ever trigger. This is separate from the token and has to be flipped on manually per-bot.
- Every command, of every kind (built-in, RP, custom), has a 3-second per-user cooldown — spamming one just gets silently ignored until the cooldown clears.

**The music menu** — `!play` posts (and reuses) one message per voice session with:
- A progress bar, elapsed/total time, and volume/loop status, refreshed live
- Buttons: Pause (turns into Resume while paused), Skip, Stop, Volume −/+, Loop (cycles Off → Track → Queue), and a Queue button that lists what's up next
- Only works for whoever's in the same voice channel as the bot, to stop randoms in other channels from taking over
- The embed itself only re-renders every 5 seconds (Discord rate-limits message edits harder than that), but any button press updates it immediately regardless of that timer
- The bot auto-disconnects after 5 minutes with nothing playing

**Custom tab**
- Create your own `!command` in Python. The code you write runs as the body of `async def run(ctx): ...`, where `ctx` gives you `ctx.send(...)` to reply, `ctx.args` (the words after the command), `ctx.content` (the raw text after it), and `ctx.message` / `ctx.author` / `ctx.channel` / `ctx.guild` as normal discord.py objects.
- A wide set of modules — `discord`, `random`, `requests`, `datetime`, `json`, `re`, `os`, and more — are already imported, so you don't need to install anything to use them.
- **Edit** re-opens a saved command for editing (name is locked; description/code aren't) and re-saves over the same command. Each one also has its own on/off toggle, separate from deleting it.
- This code runs with full access on the device the bot is on — only add commands you wrote (or trust) yourself.

**RP tab**
- Action commands like `!kiss @user` and `!hug @user` — ten are built in (`kiss`, `hug`, `slap`, `pat`, `cuddle`, `poke`, `bonk`, `highfive`, `tickle`, `wave`), and **New custom RP command** lets you add more by name.
- Every one of them, built-in or custom, needs GIFs added before it'll do anything — hit **Edit gifs** on any command (including the built-in ones) to set up to 5 GIF URLs. Empty slots are ignored, extras past 5 are ignored, and the bot picks one at random each time the command runs. If none are set yet, using the command sends an error telling you to add some instead of failing silently.
- Each RP command has its own on/off toggle too; only custom ones can be deleted outright (built-ins can only be toggled off).

**Backup tab** — web UI only, nothing here is a chat command:
- Pick a **server**, hit **Save backup** to snapshot its roles, categories, and channels (names, colors/permissions, per-role permission overwrites, channel type/topic/slowmode/bitrate/etc). Message history, pins, threads, and anything else *inside* a channel is never captured.
- Pick a saved backup from the **Backup to load** dropdown, pick a **server** (can be the same one or a totally different one), and hit **Load into selected server**. Two load modes:
  - **Additive** (default) — only creates roles/categories/channels from the backup, never touches or deletes anything already in the target server. Loading the same backup twice will create duplicates.
  - **Full wipe and replace** — deletes every existing channel and role in the target server first, then recreates the backup exactly. Irreversible, so the button asks for an explicit confirmation before doing anything.
- The bot needs **Manage Roles** and **Manage Channels** permission in the target server for either mode to work.
- Large servers can take a while to save/restore — Discord rate-limits how fast channels and roles can be created, so this isn't instant.

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
