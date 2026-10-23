"""Build a server's channels from a `.layout` file (JSON).

This is the *import* side of the Layout tab: given a JSON description of a
server (categories/"tabs", text + voice channels, which channel sits under
which category, and which ones are locked), it creates all of that on a
real Discord server. The drag-and-drop editor in bot_layout.py rearranges
what already exists; this creates it from scratch.

Role support is intentionally NOT here — roles live in their own separate
`.roles` file/importer so a layout can be shared without carrying a
server's role setup with it.

-------------------------------------------------------------------------
.layout schema (only names are required; everything else has a default)
-------------------------------------------------------------------------

{
  "name": "My Server",            # optional label, shown back to you, not applied
  "categories": [
    {
      "name": "Information",
      "locked": true,             # @everyone can't talk in this category
      "private": false,           # @everyone can't even see it
      "channels": [
        { "name": "rules",        "type": "text",  "locked": true },
        { "name": "announcements","type": "text" },
        { "name": "General",      "type": "voice" },
        { "name": "Staff only",   "type": "voice", "private": true }
      ]
    }
  ],
  "uncategorized": [              # channels with no category ("channels" also works)
    { "name": "welcome", "type": "text" }
  ]
}

type     : text (default), voice, stage, forum, announcement/news (news falls
           back to text unless the server supports it).
locked   : deny @everyone Send Messages (text/forum) or Connect + Speak (voice).
private  : deny @everyone View Channel (the channel is hidden).
parenting: a channel is "parented" simply by being inside a category's
           "channels" list. A channel created in a locked/private category
           inherits (syncs to) that category's lock unless it sets its own.

The web Layout tab's "Replace everything" toggle deletes the server's
existing channels and categories first, then builds the file fresh.
"""
import discord

REASON = "Layout imported from Control Deck"

# Guard rails so a malformed or huge file can't hammer the API. Discord's own
# hard limits are 500 channels and 50 categories per server.
MAX_CATEGORIES = 50
MAX_CHANNELS = 300
_VALID_TYPES = {"text", "voice", "stage", "forum", "announcement", "news"}


def _channel_spec(raw, errors, where):
    """Validate one channel entry into a clean dict, or None (with an error)."""
    if isinstance(raw, str):
        raw = {"name": raw}
    if not isinstance(raw, dict):
        errors.append(f"{where}: each channel must be a name or an object")
        return None
    name = str(raw.get("name") or "").strip()
    if not name:
        errors.append(f"{where}: a channel is missing its name")
        return None
    ctype = str(raw.get("type") or "text").strip().lower()
    if ctype not in _VALID_TYPES:
        errors.append(f"{where}: channel '{name}' has unknown type '{ctype}' — using text")
        ctype = "text"
    return {
        "name": name[:100],
        "type": ctype,
        "locked": bool(raw.get("locked")),
        "private": bool(raw.get("private")),
    }


def parse_spec(text):
    """Turn raw .layout JSON text into a validated spec dict.

    Returns {"name", "categories": [...], "loose": [...], "warnings": [...]}.
    Raises ValueError with a friendly message on anything unusable.
    """
    import json
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"that isn't valid JSON ({exc})")
    if not isinstance(data, dict):
        raise ValueError("the file must be a JSON object with a \"categories\" list")

    warnings = []
    categories = []
    raw_cats = data.get("categories") or []
    if not isinstance(raw_cats, list):
        raise ValueError("\"categories\" must be a list")

    total_channels = 0
    for cat in raw_cats[:MAX_CATEGORIES]:
        if not isinstance(cat, dict):
            warnings.append("skipped a category that wasn't an object")
            continue
        cname = str(cat.get("name") or "").strip()
        if not cname:
            warnings.append("skipped a category with no name")
            continue
        chans = []
        for ch in (cat.get("channels") or []):
            spec = _channel_spec(ch, warnings, f"category '{cname}'")
            if spec:
                if total_channels >= MAX_CHANNELS:
                    warnings.append(f"hit the {MAX_CHANNELS}-channel limit; some were skipped")
                    break
                chans.append(spec)
                total_channels += 1
        categories.append({
            "name": cname[:100],
            "locked": bool(cat.get("locked")),
            "private": bool(cat.get("private")),
            "channels": chans,
        })
    if len(raw_cats) > MAX_CATEGORIES:
        warnings.append(f"only the first {MAX_CATEGORIES} categories were kept")

    loose = []
    for ch in (data.get("uncategorized") or data.get("channels") or []):
        spec = _channel_spec(ch, warnings, "uncategorized")
        if spec:
            if total_channels >= MAX_CHANNELS:
                warnings.append(f"hit the {MAX_CHANNELS}-channel limit; some were skipped")
                break
            loose.append(spec)
            total_channels += 1

    if not categories and not loose:
        raise ValueError("nothing to create — the file has no categories or channels")

    return {
        "name": str(data.get("name") or "").strip()[:100],
        "categories": categories,
        "loose": loose,
        "warnings": warnings,
    }


def _overwrite(guild, *, locked, private, voice):
    """Build the @everyone overwrite for a locked and/or private channel."""
    ow = discord.PermissionOverwrite()
    if private:
        ow.view_channel = False
    if locked:
        if voice:
            ow.connect = False
            ow.speak = False
        else:
            ow.send_messages = False
            ow.send_messages_in_threads = False
            ow.create_public_threads = False
            ow.create_private_threads = False
    return ow


async def _create_channel(guild, category, spec, reason):
    """Create one channel from a validated spec. Returns it, or raises."""
    name = spec["name"]
    ctype = spec["type"]
    voice = ctype in ("voice", "stage")
    kwargs = {"reason": reason}
    if category is not None:
        kwargs["category"] = category
    # Only pass overwrites when the channel sets its own lock/privacy;
    # otherwise it syncs to its category (so a locked category locks it too).
    if spec["locked"] or spec["private"]:
        kwargs["overwrites"] = {guild.default_role: _overwrite(guild, locked=spec["locked"], private=spec["private"], voice=voice)}

    if ctype == "voice":
        return await guild.create_voice_channel(name, **kwargs)
    if ctype == "stage":
        return await guild.create_stage_channel(name, **kwargs)
    if ctype == "forum":
        return await guild.create_forum(name, **kwargs)
    # announcement/news collapse to text unless the server has the feature;
    # creating them reliably needs Community, so keep it a plain text channel.
    return await guild.create_text_channel(name, **kwargs)


async def _delete_everything(guild, reason, errors):
    """For the 'replace everything' toggle: remove channels then categories."""
    deleted = 0
    # Non-category channels first, then the (now empty) categories.
    targets = [c for c in guild.channels if not isinstance(c, discord.CategoryChannel)]
    targets += [c for c in guild.channels if isinstance(c, discord.CategoryChannel)]
    for channel in targets:
        try:
            await channel.delete(reason=reason)
            deleted += 1
        except discord.HTTPException as exc:
            # Community servers won't let you delete the rules/updates channel;
            # note it and move on instead of aborting the whole import.
            errors.append(f"couldn't delete #{channel.name}: {exc.text or exc}")
    return deleted


async def apply_import(guild, spec, *, replace_all=False, reason=REASON):
    """Create everything in `spec` on `guild`. Returns a summary dict."""
    errors = list(spec.get("warnings") or [])
    deleted = 0

    if replace_all:
        deleted = await _delete_everything(guild, reason, errors)

    made_categories = 0
    made_channels = 0

    for cat in spec["categories"]:
        overwrites = None
        if cat["locked"] or cat["private"]:
            overwrites = {guild.default_role: _overwrite(guild, locked=cat["locked"], private=cat["private"], voice=False)}
        try:
            category = await guild.create_category(
                cat["name"], reason=reason,
                **({"overwrites": overwrites} if overwrites else {}),
            )
            made_categories += 1
        except discord.HTTPException as exc:
            errors.append(f"couldn't create category '{cat['name']}': {exc.text or exc}")
            category = None
        for ch in cat["channels"]:
            try:
                await _create_channel(guild, category, ch, reason)
                made_channels += 1
            except discord.HTTPException as exc:
                errors.append(f"couldn't create '{ch['name']}': {exc.text or exc}")

    for ch in spec["loose"]:
        try:
            await _create_channel(guild, None, ch, reason)
            made_channels += 1
        except discord.HTTPException as exc:
            errors.append(f"couldn't create '{ch['name']}': {exc.text or exc}")

    return {
        "ok": True,
        "deleted": deleted,
        "categories": made_categories,
        "channels": made_channels,
        "errors": errors,
    }
