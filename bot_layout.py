"""
Server layout editing: where channels sit, which category they're under,
and what order categories come in. Shared by three front-ends:

- the web UI's Layout tab (drag-and-drop tree → bot_manager.apply_layout)
- the interactive `!layout` editor posted in Discord (select menus + buttons)
- quick one-shot chat commands (`!movech`, `!setcat`, `!swapch`, ...)

Everything works on a plain "layout" model — an ordered list of
[category_id | None, [channel_id, ...]] groups, with the uncategorized
group always first (that's where Discord draws them). Moves are pure list
edits on that model, and the whole thing is written back to Discord with
ONE bulk position update (PATCH /guilds/{id}/channels) instead of one edit
per channel, which is what makes rearranging fast and rate-limit friendly.

Inside a category Discord always draws text-style channels (text, news,
forum) above voice-style ones (voice, stage) no matter their position
numbers, so each group is kept normalized that way and up/down moves only
swap a channel with its neighbours of the same style — a move that would
cross that line couldn't show up in the client anyway.
"""
import asyncio
import re

import discord

from theme import EMBED_COLOR

REASON = "Layout edited from Control Deck"

_MENTION_RE = re.compile(r"^<#(\d{15,25})>$")
_ID_RE = re.compile(r"^\d{15,25}$")

# One bulk update per guild at a time — two rapid button presses otherwise
# race each other and the second one's positions win.
_locks: dict[int, asyncio.Lock] = {}


def _lock(guild_id: int) -> asyncio.Lock:
    lock = _locks.get(guild_id)
    if lock is None:
        lock = _locks[guild_id] = asyncio.Lock()
    return lock


# ==================== model ====================

def is_voice_like(channel) -> bool:
    return isinstance(channel, (discord.VoiceChannel, discord.StageChannel))


def kind_of(guild: discord.Guild, channel_id: int) -> int:
    """0 = drawn in the text block of a category, 1 = the voice block."""
    return 1 if is_voice_like(guild.get_channel(channel_id)) else 0


def icon_for(channel) -> str:
    if isinstance(channel, discord.CategoryChannel):
        return "📁"
    if isinstance(channel, discord.StageChannel):
        return "🎙️"
    if isinstance(channel, discord.VoiceChannel):
        return "🔊"
    if isinstance(channel, discord.ForumChannel):
        return "💬"
    if getattr(channel, "is_news", lambda: False)():
        return "📢"
    return "#"


def type_name(channel) -> str:
    if isinstance(channel, discord.CategoryChannel):
        return "category"
    if isinstance(channel, discord.StageChannel):
        return "stage"
    if isinstance(channel, discord.VoiceChannel):
        return "voice"
    if isinstance(channel, discord.ForumChannel):
        return "forum"
    if getattr(channel, "is_news", lambda: False)():
        return "news"
    return "text"


def _sort_key(channel):
    return (1 if is_voice_like(channel) else 0, channel.position, channel.id)


def snapshot(guild: discord.Guild) -> list:
    """The guild's current layout, as Discord's client would draw it."""
    loose = [c for c in guild.channels if not isinstance(c, discord.CategoryChannel) and c.category_id is None]
    layout = [[None, [c.id for c in sorted(loose, key=_sort_key)]]]
    for cat in sorted(guild.categories, key=lambda c: (c.position, c.id)):
        layout.append([cat.id, [c.id for c in sorted(cat.channels, key=_sort_key)]])
    return layout


def normalize(guild: discord.Guild, layout: list) -> list:
    """Drop channels/categories that no longer exist and re-stack each
    group text-first (stable, so relative order inside each block holds)."""
    out = []
    for cat_id, ids in layout:
        if cat_id is not None and not isinstance(guild.get_channel(cat_id), discord.CategoryChannel):
            continue
        ids = [i for i in ids if guild.get_channel(i) is not None]
        ids.sort(key=lambda i: kind_of(guild, i))
        out.append([cat_id, ids])
    if not out or out[0][0] is not None:
        out.insert(0, [None, []])
    return out


def locate(layout: list, channel_id: int):
    """(group index, index within group) of a channel, or None."""
    for gi, (_, ids) in enumerate(layout):
        if channel_id in ids:
            return gi, ids.index(channel_id)
    return None


def group_index(layout: list, cat_id) -> int | None:
    for gi, (cid, _) in enumerate(layout):
        if cid == cat_id:
            return gi
    return None


def _reorder(items: list, index: int, where) -> list:
    """Move items[index] per `where`: 'up'/'down' (by `where[1]` steps if a
    tuple), 'top', 'bottom', or an int (1-based target slot)."""
    item = items.pop(index)
    steps = 1
    if isinstance(where, tuple):
        where, steps = where
    if where == "up":
        new = max(0, index - steps)
    elif where == "down":
        new = min(len(items), index + steps)
    elif where == "top":
        new = 0
    elif where == "bottom":
        new = len(items)
    else:
        new = max(0, min(len(items), int(where) - 1))
    items.insert(new, item)
    return items


def move_channel(guild, layout: list, channel_id: int, where) -> bool:
    """Reorder a channel among same-style siblings in its category.
    Returns False if it didn't actually move (already at the edge)."""
    loc = locate(layout, channel_id)
    if loc is None:
        return False
    gi, _ = loc
    ids = layout[gi][1]
    kind = kind_of(guild, channel_id)
    block = [i for i in ids if kind_of(guild, i) == kind]
    before = list(block)
    _reorder(block, block.index(channel_id), where)
    if block == before:
        return False
    others = [i for i in ids if kind_of(guild, i) != kind]
    layout[gi][1] = block + others if kind == 0 else others + block
    return True


def set_category(guild, layout: list, channel_id: int, cat_id, where="bottom") -> bool:
    loc = locate(layout, channel_id)
    target = group_index(layout, cat_id)
    if loc is None or target is None:
        return False
    gi, idx = loc
    if gi == target:
        return False
    layout[gi][1].pop(idx)
    layout[target][1].append(channel_id)
    layout[target][1].sort(key=lambda i: kind_of(guild, i))  # stable: lands at the end of its block
    if where != "bottom":
        move_channel(guild, layout, channel_id, where)
    return True


def move_category(layout: list, cat_id: int, where) -> bool:
    gi = group_index(layout, cat_id)
    if gi is None or cat_id is None:
        return False
    cats = layout[1:]
    before = [c[0] for c in cats]
    _reorder(cats, gi - 1, where)
    if [c[0] for c in cats] == before:
        return False
    layout[1:] = cats
    return True


def swap_channels(guild, layout: list, a: int, b: int) -> bool:
    la, lb = locate(layout, a), locate(layout, b)
    if la is None or lb is None or a == b:
        return False
    layout[la[0]][1][la[1]] = b
    layout[lb[0]][1][lb[1]] = a
    for gi in {la[0], lb[0]}:
        layout[gi][1].sort(key=lambda i: kind_of(guild, i))
    return True


def sort_alpha(guild, layout: list, cat_id="all") -> int:
    """A→Z inside each block of one category (or every group). Returns
    how many groups were touched."""
    touched = 0
    for group in layout:
        if cat_id != "all" and group[0] != cat_id:
            continue
        group[1].sort(key=lambda i: (kind_of(guild, i), guild.get_channel(i).name.lower()))
        touched += 1
    return touched


def build_payload(layout: list, sync_ids=()) -> list:
    payload = []
    for pos, (cat_id, _) in enumerate(g for g in layout if g[0] is not None):
        payload.append({"id": cat_id, "position": pos})
    pos = 0
    for cat_id, ids in layout:
        for cid in ids:
            entry = {"id": cid, "position": pos, "parent_id": cat_id}
            if cid in sync_ids:
                entry["lock_permissions"] = True
            payload.append(entry)
            pos += 1
    return payload


async def apply(guild: discord.Guild, layout: list, *, sync_ids=(), reason: str = REASON):
    """Write the whole layout back in one request. Raises discord.HTTPException."""
    payload = build_payload(layout, sync_ids)
    async with _lock(guild.id):
        await guild._state.http.bulk_channel_update(guild.id, payload, reason=reason)


def layout_from_web(guild: discord.Guild, groups: list) -> list:
    """Validate a layout the web UI sent back. Unknown IDs are dropped, and
    any channel the browser didn't know about (created since the page
    loaded) keeps its current category, appended to the end."""
    layout = [[None, []]]
    seen = set()
    for group in groups or []:
        raw_cat = group.get("id")
        cat_id = int(raw_cat) if raw_cat else None
        if cat_id is not None:
            if not isinstance(guild.get_channel(cat_id), discord.CategoryChannel) or cat_id in seen:
                continue
            seen.add(cat_id)
            layout.append([cat_id, []])
        target = layout[-1] if cat_id is not None else layout[0]
        for raw in group.get("channels") or []:
            cid = int(raw)
            ch = guild.get_channel(cid)
            if ch is None or isinstance(ch, discord.CategoryChannel) or cid in seen:
                continue
            seen.add(cid)
            target[1].append(cid)
    for cat in guild.categories:
        if cat.id not in seen:
            seen.add(cat.id)
            layout.append([cat.id, []])
    for ch in guild.channels:
        if isinstance(ch, discord.CategoryChannel) or ch.id in seen:
            continue
        gi = group_index(layout, ch.category_id)
        layout[gi if gi is not None else 0][1].append(ch.id)
    return normalize(guild, layout)


def to_web(guild: discord.Guild) -> dict:
    """The layout tree as JSON for the web UI."""
    groups = []
    for cat_id, ids in normalize(guild, snapshot(guild)):
        cat = guild.get_channel(cat_id) if cat_id else None
        groups.append({
            "id": str(cat_id) if cat_id else None,
            "name": cat.name if cat else None,
            "channels": [
                {"id": str(i), "name": guild.get_channel(i).name, "type": type_name(guild.get_channel(i))}
                for i in ids
            ],
        })
    return {"groups": groups}


# ==================== rendering ====================

def _chan_line(channel, *, selected=False) -> str:
    name = discord.utils.escape_markdown(channel.name)
    icon = icon_for(channel)
    label = f"{icon} {name}" if icon != "#" else f"# {name}"
    return f"➤ **{label}**" if selected else f" {label}"


def render_tree(guild: discord.Guild, layout: list, *, focus_cat="all", selected=None, limit=3900) -> str:
    """Markdown tree. With a focused category, only that one is expanded
    and the rest collapse to one line each, so a big server still fits."""
    lines = []
    for cat_id, ids in layout:
        cat = guild.get_channel(cat_id) if cat_id else None
        expanded = focus_cat == "all" or focus_cat == cat_id
        if cat_id is None:
            if not ids and focus_cat is not None:
                continue
            head = "▾ *no category*" if expanded else f"▸ *no category* · {len(ids)}"
        else:
            name = discord.utils.escape_markdown(cat.name.upper())
            marker = "▾" if expanded else "▸"
            head = f"{marker} 📁 **{name}**" if expanded else f"{marker} 📁 {name} · {len(ids)}"
        lines.append(head)
        if expanded:
            if not ids:
                lines.append(" *empty*")
            for cid in ids:
                lines.append(_chan_line(guild.get_channel(cid), selected=(cid == selected)))
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[: limit - 2].rsplit("\n", 1)[0] + "\n…"
    return text or "*This server has no channels.*"


# ==================== argument parsing ====================

def resolve_channel(guild: discord.Guild, token: str, *, allow_category=False):
    token = (token or "").strip()
    if not token:
        return None
    m = _MENTION_RE.match(token)
    if m or _ID_RE.match(token):
        ch = guild.get_channel(int(m.group(1) if m else token))
    else:
        wanted = token.lstrip("#").lower()
        dashed = wanted.replace(" ", "-")
        ch = discord.utils.find(
            lambda c: not isinstance(c, discord.CategoryChannel) and c.name.lower() in (wanted, dashed),
            guild.channels,
        )
    if isinstance(ch, discord.CategoryChannel) and not allow_category:
        return None
    return ch


def resolve_category(guild: discord.Guild, token: str):
    """A CategoryChannel, the string "none" for uncategorized, or None."""
    token = (token or "").strip()
    if token.lower() in ("none", "no", "nocategory", "no-category", "-"):
        return "none"
    if _ID_RE.match(token):
        ch = guild.get_channel(int(token))
        return ch if isinstance(ch, discord.CategoryChannel) else None
    wanted = token.lower()
    return discord.utils.find(lambda c: c.name.lower() == wanted, guild.categories) or discord.utils.find(
        lambda c: c.name.lower().startswith(wanted), guild.categories)


_DIRECTIONS = {"up": "up", "u": "up", "down": "down", "d": "down", "top": "top", "first": "top",
               "bottom": "bottom", "last": "bottom", "bot": "bottom"}


def parse_where(args: list):
    """['up'] / ['up', '3'] / ['top'] / ['4'] → a `where` for _reorder, or None."""
    if not args:
        return None
    word = args[0].lower()
    if word in _DIRECTIONS:
        where = _DIRECTIONS[word]
        if where in ("up", "down") and len(args) > 1 and args[1].isdigit():
            return (where, max(1, int(args[1])))
        return where
    if word.isdigit() and int(word) >= 1:
        return int(word)
    return None


def _describe_where(where) -> str:
    if isinstance(where, tuple):
        return f"{where[0]} {where[1]}"
    if isinstance(where, int):
        return f"to slot {where}"
    return where


# ==================== the interactive !layout editor ====================

class _RenameModal(discord.ui.Modal):
    def __init__(self, view: "LayoutView", channel):
        super().__init__(title=f"Rename {channel.name}"[:45])
        self.view_ref = view
        self.channel = channel
        self.new_name = discord.ui.TextInput(label="New name", default=channel.name, max_length=100)
        self.add_item(self.new_name)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await self.channel.edit(name=str(self.new_name.value), reason=REASON)
            self.view_ref.status = f"✏️ Renamed to **{discord.utils.escape_markdown(self.channel.name)}**"
        except discord.HTTPException as exc:
            self.view_ref.status = f"⚠️ Couldn't rename: {exc.text}"
        await self.view_ref.refresh(interaction)


class LayoutView(discord.ui.View):
    """One message that drives the whole layout: pick a category, pick a
    channel, then nudge it around with buttons — every press is applied to
    Discord immediately with a single bulk update."""

    def __init__(self, guild: discord.Guild, author_id: int):
        super().__init__(timeout=600)
        self.guild = guild
        self.author_id = author_id
        self.layout = normalize(guild, snapshot(guild))
        self.focus = self.layout[0][0] if self.layout[0][1] else (self.layout[1][0] if len(self.layout) > 1 else None)
        group = self.layout[group_index(self.layout, self.focus) or 0][1]
        self.selected = group[0] if group else None
        self.status = "Pick a category, pick a channel, then move it."
        self.confirm_delete = False
        self.message: discord.Message | None = None
        self._build()

    # ----- helpers -----

    def _group(self) -> list:
        gi = group_index(self.layout, self.focus)
        return self.layout[gi][1] if gi is not None else []

    def _channel(self):
        return self.guild.get_channel(self.selected) if self.selected else None

    def embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=f"◼ Layout · {self.guild.name}"[:256],
            description=render_tree(self.guild, self.layout, focus_cat=self.focus, selected=self.selected),
            color=EMBED_COLOR,
        )
        embed.set_footer(text=self.status[:200].replace("**", ""))
        return embed

    def _build(self):
        self.clear_items()
        self.layout = normalize(self.guild, self.layout)
        if group_index(self.layout, self.focus) is None:
            self.focus = None
        if self.selected is not None and locate(self.layout, self.selected) is None:
            self.selected = None

        # row 0 — category picker
        cat_opts = [discord.SelectOption(label="No category", value="none", emoji="▫️", default=self.focus is None)]
        for cat_id, ids in self.layout[1:24]:
            cat = self.guild.get_channel(cat_id)
            cat_opts.append(discord.SelectOption(
                label=cat.name[:100], value=str(cat_id), emoji="📁",
                description=f"{len(ids)} channel{'s' if len(ids) != 1 else ''}", default=self.focus == cat_id))
        cat_select = discord.ui.Select(placeholder="📁 Category…", options=cat_opts, row=0)
        cat_select.callback = self._on_pick_category
        self.add_item(cat_select)

        # row 1 — channel picker (a 25-wide window around the selection)
        ids = self._group()
        if ids:
            start = 0
            if self.selected in ids and len(ids) > 25:
                start = max(0, min(ids.index(self.selected) - 12, len(ids) - 25))
            chan_opts = []
            for cid in ids[start:start + 25]:
                ch = self.guild.get_channel(cid)
                chan_opts.append(discord.SelectOption(
                    label=ch.name[:100], value=str(cid), emoji=icon_for(ch) if icon_for(ch) != "#" else "#️⃣",
                    default=cid == self.selected))
            chan_select = discord.ui.Select(placeholder="Channel…", options=chan_opts, row=1)
        else:
            chan_select = discord.ui.Select(placeholder="This category is empty",
                                            options=[discord.SelectOption(label="—", value="0")],
                                            row=1, disabled=True)
        chan_select.callback = self._on_pick_channel
        self.add_item(chan_select)

        has_sel = self.selected is not None
        # row 2 — move buttons
        for label, where, emoji in (("Top", "top", "⏫"), ("Up", "up", "🔼"), ("Down", "down", "🔽"), ("Bottom", "bottom", "⏬")):
            btn = discord.ui.Button(label=label, emoji=emoji, style=discord.ButtonStyle.primary, row=2, disabled=not has_sel)
            btn.callback = self._mover(where)
            self.add_item(btn)
        rename = discord.ui.Button(label="Rename", emoji="✏️", style=discord.ButtonStyle.secondary, row=2, disabled=not has_sel)
        rename.callback = self._on_rename
        self.add_item(rename)

        # row 3 — move to another category
        dest_opts = []
        if self.focus is not None:
            dest_opts.append(discord.SelectOption(label="No category", value="none", emoji="▫️"))
        for cat_id, _ in self.layout[1:25]:
            if cat_id != self.focus and len(dest_opts) < 25:
                dest_opts.append(discord.SelectOption(label=self.guild.get_channel(cat_id).name[:100], value=str(cat_id), emoji="📁"))
        if has_sel and dest_opts:
            dest = discord.ui.Select(placeholder="↪ Move selected channel into…", options=dest_opts, row=3)
        else:
            dest = discord.ui.Select(placeholder="↪ Move selected channel into…",
                                     options=[discord.SelectOption(label="—", value="0")], row=3, disabled=True)
        dest.callback = self._on_move_to
        self.add_item(dest)

        # row 4 — category order, sync, delete, close
        is_cat = self.focus is not None
        for label, where in (("Category ▲", "up"), ("Category ▼", "down")):
            btn = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, row=4, disabled=not is_cat)
            btn.callback = self._cat_mover(where)
            self.add_item(btn)
        sync = discord.ui.Button(label="Sync perms", emoji="🔒", style=discord.ButtonStyle.secondary, row=4,
                                 disabled=not (has_sel and is_cat))
        sync.callback = self._on_sync
        self.add_item(sync)
        delete = discord.ui.Button(label="Confirm delete" if self.confirm_delete else "Delete", emoji="🗑️",
                                   style=discord.ButtonStyle.danger, row=4, disabled=not has_sel)
        delete.callback = self._on_delete
        self.add_item(delete)
        close = discord.ui.Button(label="Done", emoji="✅", style=discord.ButtonStyle.success, row=4)
        close.callback = self._on_close
        self.add_item(close)

    async def refresh(self, interaction: discord.Interaction):
        self._build()
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=self.embed(), view=self)
        else:
            await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _commit(self, interaction: discord.Interaction, ok_status: str, sync_ids=()):
        self.confirm_delete = False
        # the bulk update can sit behind a rate limit for a few seconds;
        # defer so the interaction doesn't expire while it waits
        await interaction.response.defer()
        try:
            await apply(self.guild, self.layout, sync_ids=sync_ids)
            self.status = ok_status
        except discord.HTTPException as exc:
            self.status = f"⚠️ Discord refused that: {exc.text or exc}"
            self.layout = normalize(self.guild, snapshot(self.guild))
        await self.refresh(interaction)

    # ----- access -----

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This layout editor belongs to someone else — run `!layout` for your own.", ephemeral=True)
            return False
        return True

    async def on_timeout(self):
        if self.message:
            try:
                self.status = "Editor closed (timed out). Run !layout to open it again."
                await self.message.edit(embed=self.embed(), view=None)
            except discord.HTTPException:
                pass

    # ----- callbacks -----

    async def _on_pick_category(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]
        self.focus = None if value == "none" else int(value)
        ids = self._group()
        self.selected = ids[0] if ids else None
        self.confirm_delete = False
        self.status = "Category selected."
        await self.refresh(interaction)

    async def _on_pick_channel(self, interaction: discord.Interaction):
        self.selected = int(interaction.data["values"][0])
        self.confirm_delete = False
        self.status = f"Selected {self._channel().name}."
        await self.refresh(interaction)

    def _mover(self, where):
        async def callback(interaction: discord.Interaction):
            ch = self._channel()
            if not ch:
                return await self.refresh(interaction)
            if not move_channel(self.guild, self.layout, ch.id, where):
                self.status = f"{ch.name} is already at the {'top' if where in ('up', 'top') else 'bottom'}."
                return await self.refresh(interaction)
            await self._commit(interaction, f"Moved {ch.name} {where}.")
        return callback

    def _cat_mover(self, where):
        async def callback(interaction: discord.Interaction):
            cat = self.guild.get_channel(self.focus) if self.focus else None
            if not cat:
                return await self.refresh(interaction)
            if not move_category(self.layout, cat.id, where):
                self.status = f"{cat.name} is already at the {'top' if where == 'up' else 'bottom'}."
                return await self.refresh(interaction)
            await self._commit(interaction, f"Moved category {cat.name} {where}.")
        return callback

    async def _on_move_to(self, interaction: discord.Interaction):
        ch = self._channel()
        value = interaction.data["values"][0]
        target = None if value == "none" else int(value)
        if not ch or not set_category(self.guild, self.layout, ch.id, target):
            return await self.refresh(interaction)
        self.focus = target
        dest = self.guild.get_channel(target).name if target else "no category"
        await self._commit(interaction, f"Moved {ch.name} into {dest}.")

    async def _on_rename(self, interaction: discord.Interaction):
        ch = self._channel()
        if ch:
            await interaction.response.send_modal(_RenameModal(self, ch))

    async def _on_sync(self, interaction: discord.Interaction):
        ch = self._channel()
        if not ch:
            return await self.refresh(interaction)
        await self._commit(interaction, f"🔒 {ch.name} now uses its category's permissions.", sync_ids={ch.id})

    async def _on_delete(self, interaction: discord.Interaction):
        ch = self._channel()
        if not ch:
            return await self.refresh(interaction)
        if not self.confirm_delete:
            self.confirm_delete = True
            self.status = f"Press Confirm delete to permanently delete {ch.name}."
            return await self.refresh(interaction)
        self.confirm_delete = False
        try:
            await ch.delete(reason=REASON)
            loc = locate(self.layout, ch.id)
            if loc:
                self.layout[loc[0]][1].remove(ch.id)
            ids = self._group()
            self.selected = ids[0] if ids else None
            self.status = f"🗑️ Deleted {ch.name}."
        except discord.HTTPException as exc:
            self.status = f"⚠️ Couldn't delete: {exc.text}"
        await self.refresh(interaction)

    async def _on_close(self, interaction: discord.Interaction):
        self.stop()
        self.status = "Layout saved. Run !layout to edit again."
        self.focus = "all"
        await interaction.response.edit_message(embed=self.embed(), view=None)


# ==================== chat commands ====================

async def _need_guild(ctx) -> bool:
    if not ctx.guild:
        await ctx.send("This only works in a server.")
        return False
    return True


def _ok(text: str) -> discord.Embed:
    return discord.Embed(description=text, color=EMBED_COLOR)


async def _apply_or_report(ctx, layout, done_text: str, sync_ids=()):
    try:
        await apply(ctx.guild, layout, sync_ids=sync_ids)
    except discord.HTTPException as exc:
        await ctx.send(f"⚠️ Discord refused that: {exc.text or exc}")
        return
    await ctx.send(embed=_ok(done_text))


def _split_channel_arg(ctx, args):
    """First arg is the channel if it resolves to one; otherwise the
    command applies to the channel it was typed in."""
    if args:
        ch = resolve_channel(ctx.guild, args[0])
        if ch is not None:
            return ch, args[1:]
    return ctx.channel, args


async def cmd_layout(ctx):
    if not await _need_guild(ctx):
        return
    view = LayoutView(ctx.guild, ctx.author.id)
    view.message = await ctx.send(embed=view.embed(), view=view)


async def cmd_tree(ctx):
    if not await _need_guild(ctx):
        return
    layout = normalize(ctx.guild, snapshot(ctx.guild))
    text = render_tree(ctx.guild, layout, selected=getattr(ctx.channel, "id", None))
    embed = discord.Embed(title=f"◼ {ctx.guild.name}"[:256], description=text, color=EMBED_COLOR)
    embed.set_footer(text=f"{len(layout) - 1} categories · {sum(len(g[1]) for g in layout)} channels · !layout to edit")
    await ctx.send(embed=embed)


async def cmd_movech(ctx):
    if not await _need_guild(ctx):
        return
    if parse_where(ctx.args) is not None and len(ctx.args) <= 2:
        ch, rest = ctx.channel, ctx.args  # "!movech up 2" → this channel
    else:
        ch, rest = _split_channel_arg(ctx, ctx.args)
    where = parse_where(rest)
    if where is None:
        await ctx.send("Usage: `!movech [#channel] <up|down|top|bottom|N> [steps]` — e.g. `!movech #rules top`, `!movech up 2`")
        return
    layout = normalize(ctx.guild, snapshot(ctx.guild))
    if not move_channel(ctx.guild, layout, ch.id, where):
        await ctx.send(f"{ch.mention} is already there.")
        return
    await _apply_or_report(ctx, layout, f"↕️ Moved {ch.mention} {_describe_where(where)}.")


async def cmd_setcat(ctx):
    if not await _need_guild(ctx):
        return
    ch, rest = _split_channel_arg(ctx, ctx.args)
    if not rest:
        await ctx.send("Usage: `!setcat [#channel] <category name|none> [--sync]`")
        return
    sync = rest[-1].lower() in ("--sync", "sync")
    if sync:
        rest = rest[:-1]
    target = resolve_category(ctx.guild, " ".join(rest))
    if target is None:
        await ctx.send(f"No category called **{discord.utils.escape_markdown(' '.join(rest))}**.")
        return
    cat_id = None if target == "none" else target.id
    layout = normalize(ctx.guild, snapshot(ctx.guild))
    if not set_category(ctx.guild, layout, ch.id, cat_id):
        await ctx.send(f"{ch.mention} is already there.")
        return
    dest = "no category" if cat_id is None else f"**{discord.utils.escape_markdown(target.name)}**"
    await _apply_or_report(ctx, layout, f"📁 Moved {ch.mention} into {dest}." + (" Permissions synced." if sync and cat_id else ""),
                           sync_ids={ch.id} if sync and cat_id else ())


async def cmd_swapch(ctx):
    if not await _need_guild(ctx):
        return
    if len(ctx.args) < 2:
        await ctx.send("Usage: `!swapch #channel-a #channel-b` — swaps their spots (and categories).")
        return
    a, b = resolve_channel(ctx.guild, ctx.args[0]), resolve_channel(ctx.guild, ctx.args[1])
    if not a or not b:
        await ctx.send("Couldn't find both channels — mention them or use their IDs.")
        return
    layout = normalize(ctx.guild, snapshot(ctx.guild))
    if not swap_channels(ctx.guild, layout, a.id, b.id):
        await ctx.send("Those are the same channel.")
        return
    await _apply_or_report(ctx, layout, f"🔀 Swapped {a.mention} and {b.mention}.")


async def cmd_movecat(ctx):
    if not await _need_guild(ctx):
        return
    if len(ctx.args) < 2:
        await ctx.send("Usage: `!movecat <category> <up|down|top|bottom|N> [steps]`")
        return
    # direction goes last so category names can have spaces
    args = ctx.args
    if len(args) >= 3 and args[-1].isdigit() and args[-2].lower() in ("up", "u", "down", "d"):
        split = len(args) - 2
    else:
        split = len(args) - 1
    where = parse_where(args[split:])
    cat = resolve_category(ctx.guild, " ".join(args[:split])) if where is not None else None
    if not isinstance(cat, discord.CategoryChannel):
        await ctx.send("Couldn't find that category. Usage: `!movecat <category> <up|down|top|bottom|N>`")
        return
    layout = normalize(ctx.guild, snapshot(ctx.guild))
    if not move_category(layout, cat.id, where):
        await ctx.send(f"**{discord.utils.escape_markdown(cat.name)}** is already there.")
        return
    await _apply_or_report(ctx, layout, f"📁 Moved **{discord.utils.escape_markdown(cat.name)}** {_describe_where(where)}.")


async def cmd_sortch(ctx):
    if not await _need_guild(ctx):
        return
    layout = normalize(ctx.guild, snapshot(ctx.guild))
    if not ctx.content or ctx.content.lower() == "all":
        sort_alpha(ctx.guild, layout, "all")
        label = "every category"
    else:
        cat = resolve_category(ctx.guild, ctx.content)
        if cat is None:
            await ctx.send("Couldn't find that category. Usage: `!sortch [category|none|all]`")
            return
        cat_id = None if cat == "none" else cat.id
        sort_alpha(ctx.guild, layout, cat_id)
        label = "uncategorized channels" if cat_id is None else f"**{discord.utils.escape_markdown(cat.name)}**"
    await _apply_or_report(ctx, layout, f"🔤 Sorted {label} A→Z.")


async def cmd_renamech(ctx):
    if not await _need_guild(ctx):
        return
    ch, rest = _split_channel_arg(ctx, ctx.args)
    if not rest:
        await ctx.send("Usage: `!renamech [#channel] <new name>`")
        return
    old = ch.name
    try:
        await ch.edit(name=" ".join(rest)[:100], reason=REASON)
    except discord.HTTPException as exc:
        await ctx.send(f"⚠️ Couldn't rename: {exc.text}")
        return
    await ctx.send(embed=_ok(f"✏️ **{discord.utils.escape_markdown(old)}** → {ch.mention}"))


async def cmd_createch(ctx):
    if not await _need_guild(ctx):
        return
    if len(ctx.args) < 2 or ctx.args[0].lower() not in ("text", "voice", "stage", "forum", "news"):
        await ctx.send("Usage: `!createch <text|voice> <name> [in <category>]` — defaults to this channel's category.")
        return
    kind = ctx.args[0].lower()
    words = ctx.args[1:]
    category = getattr(ctx.channel, "category", None)
    lowered = [w.lower() for w in words]
    if "in" in lowered[1:]:
        cut = len(lowered) - 1 - lowered[::-1].index("in")
        found = resolve_category(ctx.guild, " ".join(words[cut + 1:]))
        if found is not None:
            category = None if found == "none" else found
            words = words[:cut]
    name = " ".join(words)[:100]
    try:
        if kind == "voice":
            ch = await ctx.guild.create_voice_channel(name, category=category, reason=REASON)
        elif kind == "stage":
            ch = await ctx.guild.create_stage_channel(name, category=category, reason=REASON)
        elif kind == "forum":
            ch = await ctx.guild.create_forum(name, category=category, reason=REASON)
        else:
            ch = await ctx.guild.create_text_channel(name, category=category, news=kind == "news", reason=REASON)
    except discord.HTTPException as exc:
        await ctx.send(f"⚠️ Couldn't create that: {exc.text}")
        return
    where = f" in **{discord.utils.escape_markdown(category.name)}**" if category else ""
    await ctx.send(embed=_ok(f"✨ Created {ch.mention}{where}."))


async def cmd_createcat(ctx):
    if not await _need_guild(ctx):
        return
    if not ctx.content:
        await ctx.send("Usage: `!createcat <name>`")
        return
    try:
        cat = await ctx.guild.create_category(ctx.content[:100], reason=REASON)
    except discord.HTTPException as exc:
        await ctx.send(f"⚠️ Couldn't create that: {exc.text}")
        return
    await ctx.send(embed=_ok(f"📁 Created category **{discord.utils.escape_markdown(cat.name)}** at the bottom. `!movecat {cat.name} top` to move it."))


async def cmd_clonech(ctx):
    if not await _need_guild(ctx):
        return
    ch, rest = _split_channel_arg(ctx, ctx.args)
    try:
        clone = await ch.clone(name=(" ".join(rest) or ch.name)[:100], reason=REASON)
        for _ in range(12):  # the cache only learns about it from the gateway event
            if ctx.guild.get_channel(clone.id):
                break
            await asyncio.sleep(0.25)
        layout = normalize(ctx.guild, snapshot(ctx.guild))
        # drop the copy right under the original instead of wherever Discord put it
        loc, src = locate(layout, clone.id), locate(layout, ch.id)
        if loc and src and loc[0] == src[0]:
            ids = layout[loc[0]][1]
            ids.remove(clone.id)
            ids.insert(ids.index(ch.id) + 1, clone.id)
            await apply(ctx.guild, layout)
    except discord.HTTPException as exc:
        await ctx.send(f"⚠️ Couldn't clone: {exc.text}")
        return
    await ctx.send(embed=_ok(f"🧬 Cloned {ch.mention} → {clone.mention} (same permissions & settings)."))


async def cmd_syncch(ctx):
    if not await _need_guild(ctx):
        return
    ch, _ = _split_channel_arg(ctx, ctx.args)
    if not ch.category:
        await ctx.send(f"{ch.mention} isn't in a category, so there's nothing to sync with.")
        return
    try:
        await ch.edit(sync_permissions=True, reason=REASON)
    except discord.HTTPException as exc:
        await ctx.send(f"⚠️ Couldn't sync: {exc.text}")
        return
    await ctx.send(embed=_ok(f"🔒 {ch.mention} now uses **{discord.utils.escape_markdown(ch.category.name)}**'s permissions."))


class _ConfirmDelete(discord.ui.View):
    def __init__(self, channel, author_id: int):
        super().__init__(timeout=30)
        self.channel = channel
        self.author_id = author_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Not your confirmation.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Delete it", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def yes(self, interaction: discord.Interaction, _button):
        self.stop()
        name = self.channel.name
        try:
            await self.channel.delete(reason=REASON)
            text = f"🗑️ Deleted **{discord.utils.escape_markdown(name)}**."
        except discord.HTTPException as exc:
            text = f"⚠️ Couldn't delete: {exc.text}"
        try:
            await interaction.response.edit_message(embed=_ok(text), view=None)
        except discord.HTTPException:
            pass  # the confirmation lived in the channel we just deleted

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def no(self, interaction: discord.Interaction, _button):
        self.stop()
        await interaction.response.edit_message(embed=_ok("Cancelled."), view=None)


async def cmd_deletech(ctx):
    if not await _need_guild(ctx):
        return
    ch = resolve_channel(ctx.guild, ctx.args[0], allow_category=True) if ctx.args else None
    if ch is None:
        await ctx.send("Usage: `!deletech <#channel|category name|ID>` — asks before deleting.")
        return
    what = "category (its channels stay, uncategorized)" if isinstance(ch, discord.CategoryChannel) else "channel"
    await ctx.send(embed=_ok(f"Delete the {what} **{discord.utils.escape_markdown(ch.name)}**? This can't be undone."),
                   view=_ConfirmDelete(ch, ctx.author.id))


# name: (description, handler, required permission)
LAYOUT_COMMANDS = {
    "layout": ("Opens an interactive layout editor — move, rename, and re-categorize channels with buttons.", cmd_layout, "manage_channels"),
    "tree": ("Shows the server's full channel layout.", cmd_tree, None),
    "movech": ("Moves a channel up/down/top/bottom/to slot N within its category.", cmd_movech, "manage_channels"),
    "setcat": ("Moves a channel into another category (or none).", cmd_setcat, "manage_channels"),
    "swapch": ("Swaps the positions of two channels.", cmd_swapch, "manage_channels"),
    "movecat": ("Moves a whole category up/down/top/bottom.", cmd_movecat, "manage_channels"),
    "sortch": ("Sorts a category's channels (or all) A→Z.", cmd_sortch, "manage_channels"),
    "renamech": ("Renames a channel.", cmd_renamech, "manage_channels"),
    "createch": ("Creates a text/voice/stage/forum/news channel.", cmd_createch, "manage_channels"),
    "createcat": ("Creates a new category.", cmd_createcat, "manage_channels"),
    "clonech": ("Duplicates a channel (settings + permissions) right below it.", cmd_clonech, "manage_channels"),
    "syncch": ("Syncs a channel's permissions with its category.", cmd_syncch, "manage_channels"),
    "deletech": ("Deletes a channel or category (asks first).", cmd_deletech, "manage_channels"),
}
