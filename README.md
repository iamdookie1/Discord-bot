# Control Deck — a Discord bot + local UI for Termux

A minimal Discord bot you run on your phone with Termux, controlled from a
local web UI at `http://127.0.0.1:5000` instead of the command line.
.
## What's in here

- `setup.sh` — Termux system-package check (installs anything missing), then launches the app
- `run.py` — checks/installs the required **Python** packages, then starts the server
- `app.py` — the Flask app: all the `/api/...` routes the UI talks to
- `bot_manager.py` — runs the actual discord.py client in the background, plus the web moderation panel's actions (kick/ban/timeout/warn/etc., mirroring the chat commands)
- `bot_commands.py` — utility + moderation `!commands`, on/off toggle storage, per-user command cooldowns, and the custom-command sandbox
- `bot_music.py` — the `!join`/`!play`/`!menu`/... voice commands, the interactive now-playing menu, and playback state
- `bot_rp.py` — the `!kiss`/`!hug`/... roleplay commands, their GIF storage, and the owner-gated channel lockdown
- `bot_tts.py` — `!tts`, which reads a text channel's messages aloud in voice via espeak-ng, plus 11 owner-only sound controls (`!tone`, `!pitch`, `!onlytm`, `!voiceselection`, `!volume`, `!ttsrate`, `!myvoice`, `!myvolume`, `!ttsstatus`, `!ttsreset`, `!ttstest`)
- `owner.py` — the one hardcoded Discord user ID allowed to use owner-only commands (RP's channel lockdown, TTS's sound controls), shared so there's a single source of truth for it
- `voice_owner.py` — tiny shared registry so music and TTS (only one voice connection per server) take turns instead of colliding
- `guild_settings.py` — per-server settings (RP-allowed channel, music channel, mod-log channel, mute role), keyed by guild ID
- `bot_backup.py` — server structure snapshot/restore for the Backup tab (web UI only, no chat command)
- `templates/`, `static/` — the UI (Home, Text, Bot, Music, Cmds, Mod, Channels, Categories, Fonts, Custom, RP, Backup tabs)
- `config.json` — created automatically the first time you save a token or set a presence (kept only on your device)
- `custom_commands.json`, `command_settings.json`, `rp_commands.json`, `warnings.json`, `server_backups.json`, `guild_settings.json`, `tts_settings.json` — created automatically as you use the app (all kept only on your device, none of it committed to git)
- `rp_media/` — GIFs/images/converted videos uploaded from the RP tab, created automatically (kept only on your device, never committed to git)

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

Open `http://127.0.0.1:5000` in your phone's browser. On a narrow screen the sidebar becomes a hamburger menu (top-left) instead of the full icon rail — tap it to open an off-canvas drawer with the full tab list, which closes automatically once you pick one.

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

**Music tab** — a full-size player, separate from the compact card on the Text tab, with its own server picker so it doesn't have to match whatever server you're texting from:
- **Now playing**: title, who requested it, a progress bar, elapsed/total time, and the full control row (Pause/Resume, Skip, Stop, Volume −/+, Loop) — polls once a second, same as the Text tab card
- **Add a song**: type a name, YouTube link, or Spotify track link and hit Play/Queue — same resolve-then-play-or-queue behavior as `!play`, just from the browser. Plays immediately if nothing's going, otherwise joins the end of the queue.
- **Effects**: Off, Nightcore, Vaporwave, Chipmunk, Slowed + Reverb, Reverb, Echo, Bass Boost, 8D Audio, Muffled, Radio, Karaoke (vocal reduction), Mono, and Custom — all applied live with ffmpeg audio filters. Picking one hot-swaps a freshly-filtered source into the currently playing track at the exact same position, with no re-fetch, stop, or restart — the old audio keeps playing right up until a brief (~200ms) blend into the new one, the same hot-swap trick real crossfades use, so nothing ever pauses. The choice sticks for every track after that until changed again. The reverb-based effects use several short (40-250ms), decaying echo taps rather than one long one, since ffmpeg has no dedicated reverb filter in most builds and a single ~1s echo just sounds like a discrete repeat rather than an actual reverb tail. Most modes show their own tunable sliders right under the effect picker — e.g. Slowed + Reverb gets separate "how slowed" and "how much reverb" sliders, Nightcore gets an amount slider — remembered per-mode so switching back and forth doesn't lose your settings. **Custom** is a fully manual mode: independent Speed and Pitch sliders, plus a "pitch tied to speed" toggle — on, pitch rides along with speed like a real turntable (the nightcore/chipmunk effect); off, speed and pitch are changed completely independently via ffmpeg's `atempo` filter. Adjusting any slider hot-swaps it in the same seamless way as switching effect modes.
- **Crossfade**: a 0-10 second slider that blends the tail of a track into the start of the next one instead of a hard cut. 0 (the default) disables it. Dragging the slider live-updates its own label without fighting the poll loop; releasing it applies the new value immediately, rescheduling the crossfade for whatever's currently playing too. Implemented as a genuine audio mix (not a fade-to-silence-and-back) — the outgoing track keeps decoding uninterrupted while the incoming one starts early and the two are blended sample-by-sample for the overlap window. Loop mode is respected across crossfades the same way it is for a hard cut. **Track** loop repeats the current song directly and never touches the queue — the song already playing never shows up as "up next," even briefly, whether other songs are queued behind it or not. **Queue** loop is the one that genuinely puts the just-finished track back into rotation, appended to the end of the queue so it comes around again after everything else. Every re-fetch of an already-known track (a loop repeat, an effect/slider change, a crossfade, the background prefetch) resolves the exact same video it played before rather than re-searching by name, so loop mode can't drift onto a different upload that happens to share the same title.
- **Queue**: the full up-next list (not just the first few names truncated into a hint like the Text tab card), each with its own remove button
- Shows a plain "not available" message instead of the player if `ffmpeg`/`PyNaCl` aren't installed, same underlying check as the Text tab card and the Cmds tab's Music section

**Cmds tab** — 76 built-in commands across four categories, each with an on/off toggle, plus a search box to find one quickly. Info-style commands (`!userinfo`, `!serverinfo`, `!roleinfo`, `!permissions`, `!channelinfo`, `!warnings`, `!banlist`, `!avatar`, `!poll`) reply with an embed in the app's own accent color rather than plain text:
- **Utility** (35): `!ping`, `!cmds`/`!help`, `!uptime`, `!avatar`, `!userinfo`, `!serverinfo`, `!say` (also deletes your original message), `!coinflip`, `!roll`, `!8ball`, `!time`, `!calc`, `!choose`, `!reverse`, `!remind`, `!remindlist`, `!remindcancel`, `!password`, `!uuid`, `!base64`, `!hash`, `!color`, `!timestamp`, `!invite`, `!poll`, `!channelinfo`, `!roleinfo`, `!permissions`, `!snowflake` (decode a Discord ID's timestamp), `!membercount`, `!servericon`, `!emojis`, `!qr` (text-rendered QR code), `!ascii` (text banner, needs `pyfiglet`)
- **Moderation** (31): `!kick`, `!ban`, `!softban`, `!unban`, `!timeout`, `!untimeout`, `!warn`, `!warnings`, `!clearwarnings`, `!warnremove`, `!purge`, `!slowmode`, `!lock`, `!unlock`, `!nick`, `!addrole`, `!removerole`, `!createrole`, `!deleterole`, `!purgeuser`, `!banid`, `!announce`, `!pin`, `!unpin`, `!clearnick`, `!banlist`, `!setmodlog`, `!muterole`, `!mute`, `!unmute`, `!tempban` — every one of these checks the caller has the matching Discord permission (and that the bot does too) before running anything, and refuses with a clear message if not. Kicks, bans, softbans, timeouts, warns, mutes, and tempbans also get posted as an embed to the server's mod-log channel (`!setmodlog #channel`), if one's been set — same for the equivalent actions run from the **Mod** tab.
- **Music** (9): `!join`, `!leave`, `!play`, `!menu`, `!pause`, `!resume`, `!skip`, `!stop`, `!queue` — needs the `ffmpeg` binary, `PyNaCl` (voice encryption), and `davey` (Discord's now-mandatory DAVE end-to-end voice encryption, required since March 2026); `setup.sh` tries to install all of it automatically on Termux, but if any piece is missing `!play` tells you instead of failing silently. `!play` (and `!menu`) show an interactive now-playing menu — see below. `!join`/`!play` always connect to the server's configured music voice channel (set on the **Mod** tab), not wherever the caller is sitting — with none configured, music commands are fully blocked, not just unrestricted. `!play` accepts a search term, a YouTube link (any format — full, `youtu.be`, Shorts, etc.), or a single Spotify track link (resolved to its YouTube equivalent via Spotify's own public oEmbed lookup — Spotify's actual audio is DRM'd and can't be streamed directly by any bot; album/playlist links aren't supported, since that would need Spotify API credentials). A queued track's stream is re-fetched fresh right before it actually plays rather than reusing whatever was found when it was queued, since YouTube stream links expire and can otherwise 403; `run.py` also re-upgrades `yt-dlp` on every launch, since an outdated version is the most common cause of playback breaking against YouTube. The next queued track's stream is also pre-fetched quietly in the background — starting a few seconds after the current track begins, and at a deliberately lowered thread priority — so there's normally no lookup delay between songs without that background lookup competing with the actual audio playback for CPU (an earlier version started it immediately at full priority, which could audibly stutter the current song on a phone). You can say who a song is by directly in the query, e.g. `!play Blinding Lights by The Weeknd` — a search with no matches (a typo, nothing found) now fails with a plain "couldn't find that" instead of crashing. A free-text search pulls back several candidates and reranks them itself by title/artist similarity, rather than trusting whichever result YouTube's own (popularity-weighted) search ranks first — a short or common query word can otherwise surface an unrelated viral video over the actual song being asked for; a "Title by Artist" query also checks the artist against each candidate's channel/uploader name, since that's normally a much stronger signal than title text alone. Songs can also be queued straight from the **Music tab** in the web UI, and audio effects/crossfade are controlled there too, not via chat commands — see below.
- **TTS** (1): `!tts` — see below.
- **Requires "Message Content Intent" turned on** for your bot in the Developer Portal (**Bot** page) — without it, discord.py can't read what people type, so no `!command` will ever trigger. This is separate from the token and has to be flipped on manually per-bot.
- Every command, of every kind (built-in, RP, custom), has a 3-second per-user cooldown — spamming one just gets silently ignored until the cooldown clears.

**The music menu** — `!play` posts (and reuses) one message per voice session with:
- A progress bar, elapsed/total time, and volume/loop status, refreshed live
- Buttons: Pause (turns into Resume while paused), Skip, Stop, Volume −/+, Loop (cycles Off → Track → Queue), and a Queue button that lists what's up next
- Only works for whoever's in the same voice channel as the bot, to stop randoms in other channels from taking over
- The embed itself only re-renders every 5 seconds (Discord rate-limits message edits harder than that), but any button press updates it immediately regardless of that timer
- The bot auto-disconnects after 5 minutes with nothing playing

**`!tts`** — reads a text channel's messages aloud in voice, for anyone who'd rather type than talk:
- Run `!tts` in any text channel while you're in a voice channel to turn it on — the bot joins your voice channel and links it to that text channel. Run `!tts` again (in that same text channel) to turn it off and leave.
- Only reads messages posted in the linked text channel by people currently sitting in the linked voice channel — not everyone in the server, and not the message author's actual voice, just their typed words read aloud.
- Strips before speaking: links, custom/unicode emoji, spoiler-tagged text (not read at all), markdown symbols, and mentions (replaced with the person's display name so it still reads naturally). A message with nothing left to say after that — just a GIF/link/emoji — is silently skipped, as is anything over 300 characters, except for the owner's own messages, which have no length cap at all. Short (1-2 letter) all-caps words like "IT" or "HI" are also lowercased before speaking — espeak-ng otherwise spells a handful of these out letter-by-letter ("I. T.") as if they were abbreviations, while leaving genuine acronyms (FBI, NASA, ...) alone.
- Uses `espeak-ng` (installed via `setup.sh`) — fully offline, no external API, so it can't go down the way an unofficial web TTS service could.
- Music and TTS share the bot's one voice connection per server, so only one can run at a time: `!tts` refuses to start while music is playing ("Sorry, music's playing right now"), and music commands refuse to start while TTS is on ("Sorry, TTS is on right now") — turn one off to use the other.
- Messages queue up and get read one at a time, in order — except the owner's own, which jump straight to the front and interrupt whatever's currently being read, so they're heard right away instead of waiting behind everyone else.
- A stack of extra sound controls exist as chat commands but are hidden: `!tone`, `!pitch`, `!onlytm`, `!voiceselection`, `!volume`, `!ttsrate`, `!myvoice`, `!myvolume`, `!ttsstatus`, `!ttsreset`, `!ttstest`. Same lockdown as `!allowchannelrp` above — silent no-op for anyone but Discord user ID `1409771422011887678`, left out of `!cmds`/`!help` entirely, chat-only. `!voiceselection <1-20>`, `!volume <0-500>`, and `!ttsrate <80-400>` pick which of espeak-ng's built-in voices to use, how loud it is, and how fast it talks — apply to everyone TTS reads (volume above 200 gets loud/distorted fast, but espeak-ng doesn't reject it). `!tone <1-10>` and `!pitch <-100 to 100>` only change how *the owner's own* messages sound (everyone else is unaffected) — a personal flourish rather than a server-wide setting. `!onlytm` toggles reading only the owner's own messages, ignoring everyone else in the linked voice channel. `!myvoice <1-20|off>` and `!myvolume <0-500|off>` go further than `!tone`/`!pitch` — a completely different voice or volume, just for the owner, on top of the tone/pitch personalization. `!ttsstatus` shows every current setting at once instead of checking each one individually; `!ttsreset` puts everything back to default; `!ttstest` speaks a short preview phrase right now (interrupting whatever's currently playing, same as any other owner message) so a change can be heard immediately without waiting for a real message to trigger it.
- These same settings, plus a new **Speed** slider (80-400 words/minute, espeak-ng's own `-s` rate flag), are also editable from a **TTS voice** card on the **Bot** tab — voice dropdown, only-me toggle, and sliders for volume/speed/tone/pitch, saved instantly as you let go of each control. Unlike the chat commands, the web card has no owner check: the web UI is a single-operator surface already, so anything reachable there is implicitly trusted.
- The same card also has two more owner-only overrides, web-only (no chat command): a toggle + dropdown to use a **different voice** just for the owner's own messages, and a toggle + slider for a **different volume**, both layered on top of the tone/pitch personalization above — everyone else keeps hearing the base voice/volume either way.

**Mod tab** — every moderation command has a web equivalent here now, not just the six original ones:
- **Server settings**: music channel, mod-log channel (optional — kicks/bans/timeouts/warns/mutes/tempbans post there as an embed), and mute role (the role `Mute`/`Unmute` below add/remove — set once here, same as `!muterole` in chat).
- **Moderate a member**: paste a user ID (not a picker — the bot doesn't request the privileged Members intent, so a full member list isn't reliably available). Grouped into **Access** (Kick, Ban, Softban, Temp-ban, Ban by ID, Unban — the last two work even if that person isn't currently a member), **Timeout & mute** (Timeout, Remove timeout, Mute, Unmute — mute needs the mute role set above first), **Warnings** (Warn, Remove warning #, Clear warnings — a user's current warnings list itself automatically once you tab out of the User ID field), and **Identity & roles** (set/reset nickname, Add/Remove role from a dropdown of the server's roles). Kick/Ban/Softban/Temp-ban/Ban-by-ID ask for confirmation first.
- **Moderate a channel**: pick a text channel, then Purge (bulk-delete N messages), set Slowmode, Lock/Unlock (blocks/restores `@everyone`'s Send Messages), send an Announcement, Pin/Unpin a message by ID, or purge just one user's messages out of it.
- **Roles**: create a new role by name, or delete an existing one from the list — same permission rules as Discord (the bot can't touch a role above its own).
- **Ban list**: shows up to 100 current bans with a one-click Unban per entry.
- All of it is the same underlying logic as the equivalent chat commands — just triggerable from the browser instead of Discord, and logged to the mod-log channel the same way.

**Channels tab** — create, rename, move, and delete text/voice channels for a server:
- Pick a server, then **Create a channel**: name, type (Text/Voice), and an optional category to drop it into.
- **Existing channels** lists everything with inline controls per row: a rename field + button, a category dropdown (change it and hit Move), and Delete (asks for confirmation).

**Categories tab** — same idea, for categories:
- **Create a category** by name.
- **Existing categories** lists each with rename and delete controls. Deleting a category doesn't delete the channels inside it — same as deleting one manually in Discord, they just become uncategorized.

**Fonts tab** — a text-styling toy, entirely client-side (no Discord API involved, works even while offline from the bot):
- Pick a style from the dropdown (Bold, Italic, Bold Italic, Script, Bold Script, Fraktur, Bold Fraktur, Double-Struck, Sans-Serif ×4, Monospace, Vaporwave/Fullwidth, Small Caps, Circled, Squared ×2, Strikethrough, Underline, Upside Down, Reversed, Wide Spacing, Zalgo — 24 in total), type text, and the converted result appears live — hit **Copy result** to grab it. Most styles use the real Unicode "Mathematical Alphanumeric Symbols" block (the same trick most "fancy text" generators use), so the result is genuine text, not an image — pastes anywhere Discord (or anywhere else) accepts Unicode.
- **Invisible characters**: zero-width and blank-rendering Unicode (zero-width space, ZWNJ, ZWJ, word joiner, BOM, Hangul filler, Braille blank, no-break/en/em space) — tap one to copy it. Useful anywhere a truly empty string gets rejected but a character that just *looks* empty doesn't.
- **Arrows** and **Symbols & stars**: two grids of common Unicode arrows/symbols — tap any to copy it instantly.

**Custom tab**
- Create your own `!command` in Python. The code you write runs as the body of `async def run(ctx): ...`, where `ctx` gives you `ctx.send(...)` to reply, `ctx.args` (the words after the command), `ctx.content` (the raw text after it), and `ctx.message` / `ctx.author` / `ctx.channel` / `ctx.guild` as normal discord.py objects.
- A wide set of modules — `discord`, `random`, `requests`, `datetime`, `json`, `re`, `os`, and more — are already imported, so you don't need to install anything to use them.
- **Edit** re-opens a saved command for editing (name is locked; description/code aren't) and re-saves over the same command. Each one also has its own on/off toggle, separate from deleting it.
- This code runs with full access on the device the bot is on — only add commands you wrote (or trust) yourself.

**RP tab**
- RP is hidden by default: it's left out of `!cmds`/`!help`, and every RP command is a silent no-op — no reply, no hint it exists — outside one allowed channel per server. Only Discord user ID `1409771422011887678` can set that channel, by running `!allowchannelrp` inside it (replaces any previous channel for that server — one at a time). `!rpcmds`, run in that same channel, is the only way to see what's available. There's no web UI control for any of this on purpose — it's chat-only, gated to that one account, no other way in.
- Action commands like `!kiss @user` and `!hug @user` — ten are built in (`kiss`, `hug`, `slap`, `pat`, `cuddle`, `poke`, `bonk`, `highfive`, `tickle`, `wave`), and **New custom RP command** lets you add more by name.
- Every one of them, built-in or custom, needs GIFs added before it'll do anything — hit **Edit** on any command (including the built-in ones) to set up to 10 of them. Each slot takes either a pasted URL or a file uploaded straight from your device (images/GIFs are kept as-is; a video gets its first 8 seconds converted to a GIF automatically, which needs the `ffmpeg` binary — `setup.sh` installs it for you). Uploaded files are stored locally in `rp_media/` and sent to Discord as a real file attachment (not a URL), since Discord's servers can't reach back into your phone to fetch one. Empty slots are ignored, extras past 10 are ignored, and the bot picks one at random each time the command runs. If none are set yet, using the command sends an error telling you to add some instead of failing silently.
- The same **Edit** screen also lets you add up to 10 custom message templates, using `{author}` and `{target}` as placeholders (e.g. `{author} tackles {target} into a pile of leaves!`) — one is picked at random alongside the GIF. Leave them all blank to fall back to the default "`{author} verbs {target}!`" phrasing.
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
