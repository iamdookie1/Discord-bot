// ---------- tab switching ----------

const tabs = document.querySelectorAll(".rail-tab");
const panels = document.querySelectorAll(".panel");

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((t) => t.classList.remove("is-active"));
    panels.forEach((p) => p.classList.remove("is-active"));
    tab.classList.add("is-active");
    document.getElementById(`tab-${tab.dataset.tab}`).classList.add("is-active");
    if (tab.dataset.tab === "text") {
      refreshGuilds();
      startMusicPolling();
    } else {
      stopMusicPolling();
    }
    if (tab.dataset.tab === "bot") {
      loadBotNamePlaceholder();
      loadPresence();
    }
    if (tab.dataset.tab === "cmds") loadBuiltinCommands();
    if (tab.dataset.tab === "customcmds") loadCustomCommands();
    if (tab.dataset.tab === "rp") loadRpCommands();
    if (tab.dataset.tab === "backup") {
      refreshBackupServers();
      loadBackupList();
    }
    if (tab.dataset.tab === "mod") refreshModServers();
  });
});

// ---------- helpers ----------

async function api(path, opts) {
  const res = await fetch(path, opts);
  return res.json();
}

function setMsg(el, text, kind) {
  el.textContent = text;
  el.className = "form-msg" + (kind ? ` is-${kind}` : "");
}

// ---------- home tab: token ----------

const tokenInput = document.getElementById("tokenInput");
const toggleTokenVisibility = document.getElementById("toggleTokenVisibility");
const saveTokenBtn = document.getElementById("saveTokenBtn");
const disconnectBtn = document.getElementById("disconnectBtn");
const tokenMsg = document.getElementById("tokenMsg");

toggleTokenVisibility.addEventListener("click", () => {
  tokenInput.type = tokenInput.type === "password" ? "text" : "password";
});

async function loadTokenState() {
  const data = await api("/api/token");
  if (data.has_token) {
    tokenInput.placeholder = data.masked_token;
  }
}

saveTokenBtn.addEventListener("click", async () => {
  const token = tokenInput.value.trim();
  if (!token) {
    setMsg(tokenMsg, "Paste a token first.", "error");
    return;
  }
  saveTokenBtn.disabled = true;
  setMsg(tokenMsg, "Saving and connecting...", "");
  const data = await api("/api/token", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });
  saveTokenBtn.disabled = false;
  if (data.ok) {
    setMsg(tokenMsg, "Token saved. Connecting to Discord...", "success");
    tokenInput.value = "";
    tokenInput.placeholder = data.masked_token;
  } else {
    setMsg(tokenMsg, data.error || "Something went wrong.", "error");
  }
});

disconnectBtn.addEventListener("click", async () => {
  await api("/api/disconnect", { method: "POST" });
  setMsg(tokenMsg, "Disconnected.", "");
});

// ---------- live status readout ----------

const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");
const statusUser = document.getElementById("statusUser");

async function pollStatus() {
  try {
    const data = await api("/api/status");
    statusDot.className = "readout-dot";
    if (data.status === "online") {
      statusDot.classList.add("is-online");
      statusText.textContent = "ONLINE";
      statusUser.textContent = data.user_tag || "\u2014";
    } else if (data.status === "connecting") {
      statusDot.classList.add("is-connecting");
      statusText.textContent = "CONNECTING";
      statusUser.textContent = "\u2014";
    } else if (data.status === "error") {
      statusText.textContent = "ERROR";
      statusUser.textContent = data.error ? data.error.slice(0, 40) : "\u2014";
    } else {
      statusText.textContent = "OFFLINE";
      statusUser.textContent = "\u2014";
    }
  } catch (e) {
    statusText.textContent = "UNREACHABLE";
  }
}

setInterval(pollStatus, 2500);
pollStatus();

// ---------- text tab: server / channel / send ----------

const serverSelect = document.getElementById("serverSelect");
const channelSelect = document.getElementById("channelSelect");
const messageInput = document.getElementById("messageInput");
const sendBtn = document.getElementById("sendBtn");
const sendMsg = document.getElementById("sendMsg");
const charCount = document.getElementById("charCount");

async function refreshGuilds() {
  const guilds = await api("/api/guilds");
  if (!guilds.length) {
    serverSelect.innerHTML = '<option value="">No servers found (is the bot online + invited?)</option>';
    channelSelect.innerHTML = '<option value="">Pick a server first&hellip;</option>';
    return;
  }
  serverSelect.innerHTML =
    '<option value="">Choose a server&hellip;</option>' +
    guilds.map((g) => `<option value="${g.id}">${escapeHtml(g.name)}</option>`).join("");
}

async function refreshChannels(guildId) {
  if (!guildId) {
    channelSelect.innerHTML = '<option value="">Pick a server first&hellip;</option>';
    return;
  }
  channelSelect.innerHTML = '<option value="">Loading channels&hellip;</option>';
  const channels = await api(`/api/channels?guild_id=${encodeURIComponent(guildId)}`);
  if (!channels.length) {
    channelSelect.innerHTML = '<option value="">No text channels found</option>';
    return;
  }
  channelSelect.innerHTML =
    '<option value="">Choose a channel&hellip;</option>' +
    channels.map((c) => `<option value="${c.id}">#${escapeHtml(c.name)}</option>`).join("");
}

serverSelect.addEventListener("change", () => {
  refreshChannels(serverSelect.value);
  pollMusicState();
});

messageInput.addEventListener("input", () => {
  charCount.textContent = `${messageInput.value.length} characters`;
});

sendBtn.addEventListener("click", async () => {
  const guild_id = serverSelect.value;
  const channel_id = channelSelect.value;
  const message = messageInput.value.trim();

  if (!guild_id || !channel_id) {
    setMsg(sendMsg, "Pick a server and a channel first.", "error");
    return;
  }
  if (!message) {
    setMsg(sendMsg, "Write a message first.", "error");
    return;
  }

  sendBtn.disabled = true;
  setMsg(sendMsg, "Sending...", "");
  const data = await api("/api/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guild_id, channel_id, message }),
  });
  sendBtn.disabled = false;

  if (data.ok) {
    setMsg(sendMsg, "Sent.", "success");
    messageInput.value = "";
    charCount.textContent = "0 characters";
  } else {
    setMsg(sendMsg, data.error || "Couldn't send that message.", "error");
  }
});

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// ---------- text tab: embed ----------

const embedTitle = document.getElementById("embedTitle");
const embedUrl = document.getElementById("embedUrl");
const embedDescription = document.getElementById("embedDescription");
const embedColor = document.getElementById("embedColor");
const embedColorPicker = document.getElementById("embedColorPicker");
const embedTimestamp = document.getElementById("embedTimestamp");
const embedAuthorName = document.getElementById("embedAuthorName");
const embedAuthorUrl = document.getElementById("embedAuthorUrl");
const embedAuthorIcon = document.getElementById("embedAuthorIcon");
const embedThumbnail = document.getElementById("embedThumbnail");
const embedImage = document.getElementById("embedImage");
const embedFooterText = document.getElementById("embedFooterText");
const embedFooterIcon = document.getElementById("embedFooterIcon");
const embedFieldsList = document.getElementById("embedFieldsList");
const addEmbedFieldBtn = document.getElementById("addEmbedFieldBtn");
const sendEmbedBtn = document.getElementById("sendEmbedBtn");
const clearEmbedBtn = document.getElementById("clearEmbedBtn");
const embedMsg = document.getElementById("embedMsg");

const EMBED_TEXT_FIELDS = [
  embedTitle, embedUrl, embedDescription, embedColor,
  embedAuthorName, embedAuthorUrl, embedAuthorIcon,
  embedThumbnail, embedImage, embedFooterText, embedFooterIcon,
];

const MAX_EMBED_FIELDS = 25;

function addEmbedFieldRow() {
  if (embedFieldsList.children.length >= MAX_EMBED_FIELDS) return;

  const row = document.createElement("div");
  row.className = "embed-field-row";

  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.className = "field-input embed-field-name";
  nameInput.placeholder = "Field name";

  const valueInput = document.createElement("input");
  valueInput.type = "text";
  valueInput.className = "field-input embed-field-value";
  valueInput.placeholder = "Field value";

  const inlineLabel = document.createElement("label");
  inlineLabel.className = "checkbox-row";
  const inlineCheckbox = document.createElement("input");
  inlineCheckbox.type = "checkbox";
  inlineCheckbox.className = "embed-field-inline";
  inlineCheckbox.checked = true;
  const inlineSpan = document.createElement("span");
  inlineSpan.textContent = "Inline";
  inlineLabel.append(inlineCheckbox, inlineSpan);

  const removeBtn = document.createElement("button");
  removeBtn.type = "button";
  removeBtn.className = "btn-ghost embed-field-remove";
  removeBtn.title = "Remove field";
  removeBtn.textContent = "×";
  removeBtn.addEventListener("click", () => row.remove());

  row.append(nameInput, valueInput, inlineLabel, removeBtn);
  embedFieldsList.appendChild(row);
}

function collectEmbedFields() {
  return Array.from(embedFieldsList.querySelectorAll(".embed-field-row"))
    .map((row) => ({
      name: row.querySelector(".embed-field-name").value.trim(),
      value: row.querySelector(".embed-field-value").value.trim(),
      inline: row.querySelector(".embed-field-inline").checked,
    }))
    .filter((f) => f.name && f.value);
}

addEmbedFieldBtn.addEventListener("click", () => addEmbedFieldRow());

embedColorPicker.addEventListener("input", () => {
  embedColor.value = embedColorPicker.value;
});

embedColor.addEventListener("input", () => {
  const v = embedColor.value.trim();
  if (/^#?[0-9a-fA-F]{6}$/.test(v)) {
    embedColorPicker.value = v.startsWith("#") ? v : `#${v}`;
  }
});

sendEmbedBtn.addEventListener("click", async () => {
  const guild_id = serverSelect.value;
  const channel_id = channelSelect.value;

  if (!guild_id || !channel_id) {
    setMsg(embedMsg, "Pick a server and a channel first.", "error");
    return;
  }

  const embed = {
    title: embedTitle.value.trim(),
    description: embedDescription.value.trim(),
    url: embedUrl.value.trim(),
    color: embedColor.value.trim() || embedColorPicker.value,
    timestamp: embedTimestamp.checked,
    author_name: embedAuthorName.value.trim(),
    author_url: embedAuthorUrl.value.trim(),
    author_icon_url: embedAuthorIcon.value.trim(),
    thumbnail_url: embedThumbnail.value.trim(),
    image_url: embedImage.value.trim(),
    footer_text: embedFooterText.value.trim(),
    footer_icon_url: embedFooterIcon.value.trim(),
    fields: collectEmbedFields(),
  };

  if (!(embed.title || embed.description || embed.fields.length)) {
    setMsg(embedMsg, "Add at least a title, description, or a field.", "error");
    return;
  }

  sendEmbedBtn.disabled = true;
  setMsg(embedMsg, "Sending...", "");
  const data = await api("/api/send_embed", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guild_id, channel_id, embed }),
  });
  sendEmbedBtn.disabled = false;

  if (data.ok) {
    setMsg(embedMsg, "Sent.", "success");
  } else {
    setMsg(embedMsg, data.error || "Couldn't send that embed.", "error");
  }
});

clearEmbedBtn.addEventListener("click", () => {
  EMBED_TEXT_FIELDS.forEach((el) => (el.value = ""));
  embedColorPicker.value = "#ffb454";
  embedTimestamp.checked = false;
  embedFieldsList.innerHTML = "";
  addEmbedFieldRow();
  setMsg(embedMsg, "", "");
});

addEmbedFieldRow();

// ---------- text tab: music ----------

const musicCard = document.getElementById("musicCard");
const musicTrackTitle = document.getElementById("musicTrackTitle");
const musicProgressFill = document.getElementById("musicProgressFill");
const musicElapsed = document.getElementById("musicElapsed");
const musicDuration = document.getElementById("musicDuration");
const musicPauseBtn = document.getElementById("musicPauseBtn");
const musicSkipBtn = document.getElementById("musicSkipBtn");
const musicStopBtn = document.getElementById("musicStopBtn");
const musicVolDownBtn = document.getElementById("musicVolDownBtn");
const musicVolUpBtn = document.getElementById("musicVolUpBtn");
const musicVolumeLabel = document.getElementById("musicVolumeLabel");
const musicLoopBtn = document.getElementById("musicLoopBtn");
const musicQueueHint = document.getElementById("musicQueueHint");
const musicMsg = document.getElementById("musicMsg");

const MUSIC_POLL_MS = 1000;
let musicPollTimer = null;

function fmtMusicTime(seconds) {
  seconds = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = h ? String(m).padStart(2, "0") : m;
  const ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function renderMusicState(data) {
  if (!data || !data.available) {
    musicCard.style.display = "none";
    return;
  }
  musicCard.style.display = "block";

  if (!data.connected || !data.title) {
    musicTrackTitle.textContent = "Nothing playing";
    musicProgressFill.style.width = "0%";
    musicElapsed.textContent = "0:00";
    musicDuration.textContent = "—";
    musicVolumeLabel.textContent = `${data.volume ?? 100}%`;
    musicLoopBtn.textContent = `🔁 Loop: ${(data.loop_mode || "off").replace(/^\w/, (c) => c.toUpperCase())}`;
    musicPauseBtn.textContent = "⏸️ Pause";
    musicQueueHint.textContent = "";
    return;
  }

  musicTrackTitle.textContent = data.title;
  musicElapsed.textContent = fmtMusicTime(data.elapsed);
  musicPauseBtn.textContent = data.paused ? "▶️ Resume" : "⏸️ Pause";
  musicVolumeLabel.textContent = `${data.volume}%`;
  musicLoopBtn.textContent = `🔁 Loop: ${data.loop_mode.replace(/^\w/, (c) => c.toUpperCase())}`;

  if (data.duration) {
    musicDuration.textContent = fmtMusicTime(data.duration);
    musicProgressFill.style.width = `${Math.min(100, (data.elapsed / data.duration) * 100)}%`;
  } else {
    musicDuration.textContent = "—";
    musicProgressFill.style.width = "0%";
  }

  musicQueueHint.textContent = data.queue_length
    ? `${data.queue_length} more in queue: ${data.queue.slice(0, 3).join(", ")}${data.queue_length > 3 ? "…" : ""}`
    : "";
}

async function pollMusicState() {
  const guildId = serverSelect.value;
  if (!guildId) {
    musicCard.style.display = "none";
    return;
  }
  try {
    const data = await api(`/api/music/state?guild_id=${encodeURIComponent(guildId)}`);
    renderMusicState(data);
  } catch (e) {
    // transient fetch failure — leave the card as-is, next poll will retry
  }
}

function startMusicPolling() {
  stopMusicPolling();
  pollMusicState();
  musicPollTimer = setInterval(pollMusicState, MUSIC_POLL_MS);
}

function stopMusicPolling() {
  if (musicPollTimer) {
    clearInterval(musicPollTimer);
    musicPollTimer = null;
  }
}

async function musicAction(action) {
  const guildId = serverSelect.value;
  if (!guildId) return;
  const data = await api("/api/music/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guild_id: guildId, action }),
  });
  if (!data.ok) {
    setMsg(musicMsg, data.error || "Couldn't do that.", "error");
  } else {
    setMsg(musicMsg, "", "");
  }
  pollMusicState();
}

musicPauseBtn.addEventListener("click", () => {
  musicAction(musicPauseBtn.textContent.includes("Resume") ? "resume" : "pause");
});
musicSkipBtn.addEventListener("click", () => musicAction("skip"));
musicStopBtn.addEventListener("click", () => musicAction("stop"));
musicLoopBtn.addEventListener("click", () => musicAction("loop"));

async function musicVolume(delta) {
  const guildId = serverSelect.value;
  if (!guildId) return;
  const data = await api("/api/music/volume", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guild_id: guildId, delta }),
  });
  if (!data.ok) {
    setMsg(musicMsg, data.error || "Couldn't do that.", "error");
  }
  pollMusicState();
}

musicVolDownBtn.addEventListener("click", () => musicVolume(-10));
musicVolUpBtn.addEventListener("click", () => musicVolume(10));

// ---------- bot tab: name / avatar ----------

const botNameInput = document.getElementById("botNameInput");
const updateNameBtn = document.getElementById("updateNameBtn");
const nameMsg = document.getElementById("nameMsg");
const botAvatarInput = document.getElementById("botAvatarInput");
const updateAvatarBtn = document.getElementById("updateAvatarBtn");
const avatarMsg = document.getElementById("avatarMsg");

async function loadBotNamePlaceholder() {
  const profile = await api("/api/bot/profile");
  botNameInput.placeholder = profile && profile.name
    ? `Current: ${profile.name}`
    : "Connect the bot first…";
}

updateNameBtn.addEventListener("click", async () => {
  const name = botNameInput.value.trim();
  if (!name) {
    setMsg(nameMsg, "Type a new name first.", "error");
    return;
  }
  updateNameBtn.disabled = true;
  setMsg(nameMsg, "Updating on Discord...", "");
  const data = await api("/api/bot/name", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  updateNameBtn.disabled = false;

  if (data.ok) {
    setMsg(nameMsg, "Name updated.", "success");
    botNameInput.value = "";
    loadBotNamePlaceholder();
  } else {
    setMsg(nameMsg, data.error || "Couldn't update the name.", "error");
  }
});

updateAvatarBtn.addEventListener("click", async () => {
  const file = botAvatarInput.files[0];
  if (!file) {
    setMsg(avatarMsg, "Choose an image first.", "error");
    return;
  }
  updateAvatarBtn.disabled = true;
  setMsg(avatarMsg, "Uploading to Discord...", "");
  const formData = new FormData();
  formData.append("avatar", file);
  const data = await api("/api/bot/avatar", { method: "POST", body: formData });
  updateAvatarBtn.disabled = false;

  if (data.ok) {
    setMsg(avatarMsg, "Profile picture updated.", "success");
    botAvatarInput.value = "";
  } else {
    setMsg(avatarMsg, data.error || "Couldn't update the picture.", "error");
  }
});

// ---------- bot tab: presence ----------

const presenceType = document.getElementById("presenceType");
const presenceText = document.getElementById("presenceText");
const setPresenceBtn = document.getElementById("setPresenceBtn");
const clearPresenceBtn = document.getElementById("clearPresenceBtn");
const presenceMsg = document.getElementById("presenceMsg");

async function loadPresence() {
  const data = await api("/api/bot/presence");
  presenceType.value = data.type || "playing";
  presenceText.value = data.text || "";
}

async function savePresence(type, text) {
  setPresenceBtn.disabled = true;
  setMsg(presenceMsg, "Saving...", "");
  const data = await api("/api/bot/presence", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type, text }),
  });
  setPresenceBtn.disabled = false;

  if (data.ok) {
    setMsg(presenceMsg, data.applied_live ? "Updated." : "Saved — will apply once the bot connects.", "success");
  } else {
    setMsg(presenceMsg, data.error || "Couldn't set that.", "error");
  }
}

setPresenceBtn.addEventListener("click", () => {
  const text = presenceText.value.trim();
  if (!text) {
    setMsg(presenceMsg, "Type something first.", "error");
    return;
  }
  savePresence(presenceType.value, text);
});

clearPresenceBtn.addEventListener("click", () => {
  presenceText.value = "";
  savePresence(presenceType.value, "");
});

// ---------- cmds tab ----------

const utilityCmdList = document.getElementById("utilityCmdList");
const moderationCmdList = document.getElementById("moderationCmdList");
const musicCmdList = document.getElementById("musicCmdList");

function renderToggle(name, enabled, onToggle) {
  const label = document.createElement("label");
  label.className = "toggle-switch";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.checked = enabled;
  const slider = document.createElement("span");
  slider.className = "toggle-slider";
  label.append(input, slider);
  input.addEventListener("change", () => onToggle(input.checked));
  return label;
}

function renderBuiltinCmdItem(c) {
  const item = document.createElement("div");
  item.className = "cmd-item";
  item.dataset.search = `${c.name} ${c.description}`.toLowerCase();

  item.appendChild(renderToggle(c.name, c.enabled, async (checked) => {
    await api("/api/commands/builtin/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: c.name, enabled: checked }),
    });
  }));

  const info = document.createElement("div");
  info.className = "cmd-info";
  info.innerHTML = `
    <span class="cmd-name mono">!${escapeHtml(c.name)}</span>
    <span class="cmd-desc">${escapeHtml(c.description)}</span>
  `;
  item.appendChild(info);

  if (c.required_perm_label) {
    const badge = document.createElement("span");
    badge.className = "perm-badge";
    badge.textContent = c.required_perm_label;
    item.appendChild(badge);
  }

  return item;
}

async function loadBuiltinCommands() {
  const cmds = await api("/api/commands/builtin");
  const byCategory = { utility: [], moderation: [], music: [] };
  cmds.forEach((c) => { (byCategory[c.category] || byCategory.utility).push(c); });

  const fill = (el, list) => {
    el.innerHTML = "";
    list.sort((a, b) => a.name.localeCompare(b.name)).forEach((c) => el.appendChild(renderBuiltinCmdItem(c)));
  };
  fill(utilityCmdList, byCategory.utility);
  fill(moderationCmdList, byCategory.moderation);
  fill(musicCmdList, byCategory.music);
  filterBuiltinCommands();
}

const cmdSearchInput = document.getElementById("cmdSearchInput");

function filterBuiltinCommands() {
  const query = cmdSearchInput.value.trim().toLowerCase();
  document.querySelectorAll("#tab-cmds .cmd-item").forEach((item) => {
    item.style.display = !query || item.dataset.search.includes(query) ? "" : "none";
  });
}

cmdSearchInput.addEventListener("input", filterBuiltinCommands);

// ---------- custom cmds tab ----------

const customCmdList = document.getElementById("customCmdList");
const customCmdEmpty = document.getElementById("customCmdEmpty");
const customCmdFormTitle = document.getElementById("customCmdFormTitle");
const customCmdName = document.getElementById("customCmdName");
const customCmdDescription = document.getElementById("customCmdDescription");
const customCmdCode = document.getElementById("customCmdCode");
const createCustomCmdBtn = document.getElementById("createCustomCmdBtn");
const cancelEditCustomCmdBtn = document.getElementById("cancelEditCustomCmdBtn");
const customCmdMsg = document.getElementById("customCmdMsg");

let editingCustomCmd = null;

function resetCustomCmdForm() {
  editingCustomCmd = null;
  customCmdFormTitle.textContent = "New custom command";
  customCmdName.value = "";
  customCmdName.disabled = false;
  customCmdDescription.value = "";
  customCmdCode.value = "";
  createCustomCmdBtn.textContent = "Create command";
  cancelEditCustomCmdBtn.style.display = "none";
  setMsg(customCmdMsg, "", "");
}

function beginEditCustomCmd(cmd) {
  editingCustomCmd = cmd.name;
  customCmdFormTitle.textContent = `Editing !${cmd.name}`;
  customCmdName.value = cmd.name;
  customCmdName.disabled = true;
  customCmdDescription.value = cmd.description || "";
  customCmdCode.value = cmd.code || "";
  createCustomCmdBtn.textContent = "Save changes";
  cancelEditCustomCmdBtn.style.display = "";
  setMsg(customCmdMsg, "", "");
  customCmdCode.scrollIntoView({ behavior: "smooth", block: "center" });
}

cancelEditCustomCmdBtn.addEventListener("click", resetCustomCmdForm);

async function loadCustomCommands() {
  const cmds = await api("/api/commands/custom");
  customCmdEmpty.style.display = cmds.length ? "none" : "block";
  customCmdList.innerHTML = "";

  cmds.forEach((c) => {
    const item = document.createElement("div");
    item.className = "custom-cmd-item";

    item.appendChild(renderToggle(c.name, c.enabled, async (checked) => {
      await api(`/api/commands/custom/${encodeURIComponent(c.name)}/toggle`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: checked }),
      });
    }));

    const info = document.createElement("div");
    info.className = "cmd-info";
    info.innerHTML = `
      <span class="cmd-name mono">!${escapeHtml(c.name)}</span>
      <span class="cmd-desc">${escapeHtml(c.description || "")}</span>
    `;
    item.appendChild(info);

    const actions = document.createElement("div");
    actions.className = "custom-cmd-actions";

    const editBtn = document.createElement("button");
    editBtn.type = "button";
    editBtn.className = "btn-outline btn-small";
    editBtn.textContent = "Edit";
    editBtn.addEventListener("click", () => beginEditCustomCmd(c));

    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.className = "btn-ghost btn-small";
    removeBtn.textContent = "Remove";
    removeBtn.addEventListener("click", async () => {
      removeBtn.disabled = true;
      await api(`/api/commands/custom/${encodeURIComponent(c.name)}`, { method: "DELETE" });
      if (editingCustomCmd === c.name) resetCustomCmdForm();
      loadCustomCommands();
    });

    actions.append(editBtn, removeBtn);
    item.appendChild(actions);
    customCmdList.appendChild(item);
  });
}

createCustomCmdBtn.addEventListener("click", async () => {
  const name = customCmdName.value.trim().toLowerCase();
  const description = customCmdDescription.value.trim();
  const code = customCmdCode.value;

  if (!name) {
    setMsg(customCmdMsg, "Give the command a name.", "error");
    return;
  }
  if (!code.trim()) {
    setMsg(customCmdMsg, "Write some code for it to run.", "error");
    return;
  }

  createCustomCmdBtn.disabled = true;
  setMsg(customCmdMsg, "Saving...", "");
  const data = await api("/api/commands/custom", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description, code }),
  });
  createCustomCmdBtn.disabled = false;

  if (data.ok) {
    setMsg(customCmdMsg, "Command saved. Try it in Discord.", "success");
    resetCustomCmdForm();
    loadCustomCommands();
  } else {
    setMsg(customCmdMsg, data.error || "Couldn't save that command.", "error");
  }
});

// ---------- rp tab ----------

const rpCmdList = document.getElementById("rpCmdList");
const rpCmdName = document.getElementById("rpCmdName");
const rpCmdDescription = document.getElementById("rpCmdDescription");
const createRpCmdBtn = document.getElementById("createRpCmdBtn");
const rpCreateMsg = document.getElementById("rpCreateMsg");

const MAX_RP_GIFS = 10;
const MAX_RP_MESSAGES = 10;

function renderRpCmdItem(c) {
  const item = document.createElement("div");
  item.className = "rp-cmd-item";

  const row = document.createElement("div");
  row.className = "rp-cmd-row";

  row.appendChild(renderToggle(c.name, c.enabled, async (checked) => {
    await api(`/api/rp/commands/${encodeURIComponent(c.name)}/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: checked }),
    });
  }));

  const info = document.createElement("div");
  info.className = "cmd-info";
  const gifCount = c.gifs.length ? `${c.gifs.length} gif${c.gifs.length === 1 ? "" : "s"}` : "no gifs yet";
  const msgCount = c.messages.length ? `${c.messages.length} msg${c.messages.length === 1 ? "" : "s"}` : "default msg";
  info.innerHTML = `
    <span class="cmd-name mono">!${escapeHtml(c.name)}</span>
    <span class="cmd-desc">${escapeHtml(c.description || "")} &middot; ${gifCount} &middot; ${msgCount}</span>
  `;
  row.appendChild(info);

  const actions = document.createElement("div");
  actions.className = "custom-cmd-actions";

  const editBtn = document.createElement("button");
  editBtn.type = "button";
  editBtn.className = "btn-outline btn-small";
  editBtn.textContent = "Edit";
  actions.appendChild(editBtn);

  if (c.custom) {
    const deleteBtn = document.createElement("button");
    deleteBtn.type = "button";
    deleteBtn.className = "btn-ghost btn-small";
    deleteBtn.textContent = "Delete";
    deleteBtn.addEventListener("click", async () => {
      deleteBtn.disabled = true;
      await api(`/api/rp/commands/${encodeURIComponent(c.name)}`, { method: "DELETE" });
      loadRpCommands();
    });
    actions.appendChild(deleteBtn);
  }

  row.appendChild(actions);
  item.appendChild(row);

  const editor = document.createElement("div");
  editor.className = "rp-gif-editor";
  editor.style.display = "none";

  const gifLabel = document.createElement("label");
  gifLabel.className = "field-label";
  gifLabel.textContent = `GIFs (up to ${MAX_RP_GIFS})`;
  editor.appendChild(gifLabel);

  function refreshSlotPreview(slot) {
    const val = slot.input.value.trim();
    if (val.startsWith("local:")) {
      slot.preview.src = `/rp_media/${encodeURIComponent(val.slice("local:".length))}`;
      slot.preview.style.display = "inline-block";
    } else {
      slot.preview.removeAttribute("src");
      slot.preview.style.display = "none";
    }
  }

  const gifSlots = [];
  for (let i = 0; i < MAX_RP_GIFS; i++) {
    const wrapper = document.createElement("div");
    wrapper.className = "rp-gif-slot";

    const input = document.createElement("input");
    input.type = "text";
    input.className = "field-input mono rp-gif-input";
    input.placeholder = `GIF URL ${i + 1}, or upload below`;
    input.value = c.gifs[i] || "";

    const preview = document.createElement("img");
    preview.className = "rp-gif-preview";
    preview.alt = "";

    const slot = { input, preview };
    input.addEventListener("input", () => refreshSlotPreview(slot));
    refreshSlotPreview(slot);

    wrapper.append(input, preview);
    editor.appendChild(wrapper);
    gifSlots.push(slot);
  }

  const uploadRow = document.createElement("div");
  uploadRow.className = "rp-upload-row";
  const uploadInput = document.createElement("input");
  uploadInput.type = "file";
  uploadInput.accept = "image/*,video/*";
  uploadInput.className = "field-input rp-upload-input";
  const uploadBtn = document.createElement("button");
  uploadBtn.type = "button";
  uploadBtn.className = "btn-outline btn-small";
  uploadBtn.textContent = "Upload";
  uploadRow.append(uploadInput, uploadBtn);
  editor.appendChild(uploadRow);

  const uploadHint = document.createElement("p");
  uploadHint.className = "field-hint";
  uploadHint.style.marginTop = "0";
  uploadHint.textContent = "Images/GIFs are used as-is; videos are converted to a GIF automatically (first 8s, needs ffmpeg). Max 30MB — fills the next empty slot above.";
  editor.appendChild(uploadHint);

  const uploadMsg = document.createElement("p");
  uploadMsg.className = "form-msg";
  editor.appendChild(uploadMsg);

  uploadBtn.addEventListener("click", async () => {
    const file = uploadInput.files[0];
    if (!file) {
      setMsg(uploadMsg, "Choose a file first.", "error");
      return;
    }
    const emptySlot = gifSlots.find((s) => !s.input.value.trim());
    if (!emptySlot) {
      setMsg(uploadMsg, "All 10 GIF slots are full — clear one first.", "error");
      return;
    }
    uploadBtn.disabled = true;
    setMsg(uploadMsg, file.type.startsWith("video") ? "Uploading and converting to a GIF…" : "Uploading…", "");
    const formData = new FormData();
    formData.append("file", file);
    const data = await api(`/api/rp/commands/${encodeURIComponent(c.name)}/upload`, { method: "POST", body: formData });
    uploadBtn.disabled = false;
    if (data.ok) {
      emptySlot.input.value = data.value;
      refreshSlotPreview(emptySlot);
      uploadInput.value = "";
      setMsg(uploadMsg, "Uploaded — click Save to add it to the command.", "success");
    } else {
      setMsg(uploadMsg, data.error || "Couldn't upload that file.", "error");
    }
  });

  const msgLabel = document.createElement("label");
  msgLabel.className = "field-label";
  msgLabel.textContent = `Messages (up to ${MAX_RP_MESSAGES}, optional)`;
  editor.appendChild(msgLabel);

  const msgHint = document.createElement("p");
  msgHint.className = "field-hint";
  msgHint.style.marginTop = "0";
  msgHint.textContent = "Use {author} and {target} as placeholders. Leave all blank to use the default phrasing.";
  editor.appendChild(msgHint);

  const messageInputs = [];
  for (let i = 0; i < MAX_RP_MESSAGES; i++) {
    const input = document.createElement("input");
    input.type = "text";
    input.className = "field-input rp-gif-input";
    input.placeholder = `Message ${i + 1}`;
    input.value = c.messages[i] || "";
    messageInputs.push(input);
    editor.appendChild(input);
  }

  const editorMsg = document.createElement("p");
  editorMsg.className = "form-msg";

  const saveRow = document.createElement("div");
  saveRow.className = "btn-row";
  const saveBtn = document.createElement("button");
  saveBtn.type = "button";
  saveBtn.className = "btn-primary btn-small";
  saveBtn.textContent = "Save";
  saveBtn.addEventListener("click", async () => {
    saveBtn.disabled = true;
    const gifs = gifSlots.map((s) => s.input.value.trim());
    const messages = messageInputs.map((inp) => inp.value.trim());
    const data = await api(`/api/rp/commands/${encodeURIComponent(c.name)}/content`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ gifs, messages }),
    });
    saveBtn.disabled = false;
    if (data.ok) {
      setMsg(editorMsg, "Saved.", "success");
      loadRpCommands();
    } else {
      setMsg(editorMsg, data.error || "Couldn't save.", "error");
    }
  });
  saveRow.appendChild(saveBtn);
  editor.append(saveRow, editorMsg);

  editBtn.addEventListener("click", () => {
    editor.style.display = editor.style.display === "none" ? "flex" : "none";
  });

  item.appendChild(editor);
  return item;
}

async function loadRpCommands() {
  const cmds = await api("/api/rp/commands");
  rpCmdList.innerHTML = "";
  cmds.forEach((c) => rpCmdList.appendChild(renderRpCmdItem(c)));
}

createRpCmdBtn.addEventListener("click", async () => {
  const name = rpCmdName.value.trim().toLowerCase();
  const description = rpCmdDescription.value.trim();

  if (!name) {
    setMsg(rpCreateMsg, "Give the command a name.", "error");
    return;
  }

  createRpCmdBtn.disabled = true;
  setMsg(rpCreateMsg, "Saving...", "");
  const data = await api("/api/rp/commands", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, description, gifs: [] }),
  });
  createRpCmdBtn.disabled = false;

  if (data.ok) {
    setMsg(rpCreateMsg, "Created — add GIFs to it below.", "success");
    rpCmdName.value = "";
    rpCmdDescription.value = "";
    loadRpCommands();
  } else {
    setMsg(rpCreateMsg, data.error || "Couldn't create that command.", "error");
  }
});

// ---------- backup tab (web UI only) ----------

const backupServerSelect = document.getElementById("backupServerSelect");
const saveBackupBtn = document.getElementById("saveBackupBtn");
const saveBackupMsg = document.getElementById("saveBackupMsg");
const backupSelect = document.getElementById("backupSelect");
const loadModeSelect = document.getElementById("loadModeSelect");
const loadBackupBtn = document.getElementById("loadBackupBtn");
const loadBackupMsg = document.getElementById("loadBackupMsg");
const backupList = document.getElementById("backupList");
const backupListEmpty = document.getElementById("backupListEmpty");

async function refreshBackupServers() {
  const guilds = await api("/api/guilds");
  const current = backupServerSelect.value;
  if (!guilds.length) {
    backupServerSelect.innerHTML = '<option value="">No servers found (is the bot online + invited?)</option>';
    return;
  }
  backupServerSelect.innerHTML =
    '<option value="">Choose a server&hellip;</option>' +
    guilds.map((g) => `<option value="${g.id}">${escapeHtml(g.name)}</option>`).join("");
  if (current) backupServerSelect.value = current;
}

async function loadBackupList() {
  const backups = await api("/api/backups");
  backupListEmpty.style.display = backups.length ? "none" : "block";

  const currentSelection = backupSelect.value;
  backupSelect.innerHTML = backups.length
    ? backups.map((b) => `<option value="${b.id}">${escapeHtml(b.name)} (${b.role_count} roles, ${b.channel_count} channels)</option>`).join("")
    : '<option value="">No backups saved yet&hellip;</option>';
  if (currentSelection) backupSelect.value = currentSelection;

  backupList.innerHTML = "";
  backups.forEach((b) => {
    const item = document.createElement("div");
    item.className = "custom-cmd-item";

    const info = document.createElement("div");
    info.className = "cmd-info";
    info.innerHTML = `
      <span class="cmd-name mono">${escapeHtml(b.name)}</span>
      <span class="cmd-desc">from ${escapeHtml(b.guild_name)} &middot; ${b.role_count} roles, ${b.category_count} categories, ${b.channel_count} channels</span>
    `;
    item.appendChild(info);

    const actions = document.createElement("div");
    actions.className = "custom-cmd-actions";
    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn-ghost btn-small";
    delBtn.textContent = "Delete";
    delBtn.addEventListener("click", async () => {
      delBtn.disabled = true;
      await api(`/api/backups/${encodeURIComponent(b.id)}`, { method: "DELETE" });
      loadBackupList();
    });
    actions.appendChild(delBtn);
    item.appendChild(actions);

    backupList.appendChild(item);
  });
}

saveBackupBtn.addEventListener("click", async () => {
  const guildId = backupServerSelect.value;
  if (!guildId) {
    setMsg(saveBackupMsg, "Pick a server first.", "error");
    return;
  }
  saveBackupBtn.disabled = true;
  setMsg(saveBackupMsg, "Saving — this can take a moment for large servers...", "");
  const data = await api("/api/backups", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guild_id: guildId }),
  });
  saveBackupBtn.disabled = false;

  if (data.ok) {
    setMsg(saveBackupMsg, `Saved: ${data.roles} roles, ${data.categories} categories, ${data.channels} channels.`, "success");
    loadBackupList();
  } else {
    setMsg(saveBackupMsg, data.error || "Couldn't save that backup.", "error");
  }
});

loadBackupBtn.addEventListener("click", async () => {
  const guildId = backupServerSelect.value;
  const backupId = backupSelect.value;
  const mode = loadModeSelect.value;

  if (!guildId) {
    setMsg(loadBackupMsg, "Pick a server first.", "error");
    return;
  }
  if (!backupId) {
    setMsg(loadBackupMsg, "Pick a backup to load.", "error");
    return;
  }

  if (mode === "replace") {
    const sure = window.confirm(
      "This deletes EVERY channel and role currently in the selected server, then recreates the backup. " +
      "This cannot be undone. Continue?"
    );
    if (!sure) return;
  }

  loadBackupBtn.disabled = true;
  setMsg(loadBackupMsg, "Loading — this can take a while for large backups...", "");
  const data = await api(`/api/backups/${encodeURIComponent(backupId)}/load`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guild_id: guildId, mode, confirm: mode === "replace" }),
  });
  loadBackupBtn.disabled = false;

  if (data.ok) {
    let msg = `Created ${data.created_roles} roles, ${data.created_categories} categories, ${data.created_channels} channels.`;
    if (data.deleted_roles || data.deleted_channels) {
      msg += ` Deleted ${data.deleted_roles} roles and ${data.deleted_channels} channels first.`;
    }
    if (data.errors && data.errors.length) {
      msg += ` ${data.errors.length} error(s) — check the bot's Manage Roles/Manage Channels permission.`;
    }
    setMsg(loadBackupMsg, msg, data.errors && data.errors.length ? "error" : "success");
  } else {
    setMsg(loadBackupMsg, data.error || "Couldn't load that backup.", "error");
  }
});

// ---------- mod tab ----------

const modServerSelect = document.getElementById("modServerSelect");
const modMusicChannelSelect = document.getElementById("modMusicChannelSelect");
const saveMusicChannelBtn = document.getElementById("saveMusicChannelBtn");
const musicChannelMsg = document.getElementById("musicChannelMsg");
const modLogChannelSelect = document.getElementById("modLogChannelSelect");
const saveModLogChannelBtn = document.getElementById("saveModLogChannelBtn");
const modLogChannelMsg = document.getElementById("modLogChannelMsg");
const modUserIdInput = document.getElementById("modUserIdInput");
const modReasonInput = document.getElementById("modReasonInput");
const modMinutesInput = document.getElementById("modMinutesInput");
const modActionMsg = document.getElementById("modActionMsg");
const modWarningsList = document.getElementById("modWarningsList");

async function refreshModServers() {
  const guilds = await api("/api/guilds");
  const current = modServerSelect.value;
  if (!guilds.length) {
    modServerSelect.innerHTML = '<option value="">No servers found (is the bot online + invited?)</option>';
    return;
  }
  modServerSelect.innerHTML =
    '<option value="">Choose a server&hellip;</option>' +
    guilds.map((g) => `<option value="${g.id}">${escapeHtml(g.name)}</option>`).join("");
  if (current) modServerSelect.value = current;
  await refreshModChannels();
  await loadGuildSettings();
}

async function refreshModChannels() {
  const guildId = modServerSelect.value;
  if (!guildId) {
    modMusicChannelSelect.innerHTML = '<option value="">Pick a server first&hellip;</option>';
    modLogChannelSelect.innerHTML = '<option value="">Pick a server first&hellip;</option>';
    return;
  }
  const channels = await api(`/api/channels?guild_id=${encodeURIComponent(guildId)}`);
  const options = '<option value="">None</option>' +
    channels.map((c) => `<option value="${c.id}">#${escapeHtml(c.name)}</option>`).join("");
  modMusicChannelSelect.innerHTML = options;
  modLogChannelSelect.innerHTML = options;
}

async function loadGuildSettings() {
  const guildId = modServerSelect.value;
  if (!guildId) return;
  const s = await api(`/api/guild_settings?guild_id=${encodeURIComponent(guildId)}`);
  if (s.music_channel) modMusicChannelSelect.value = String(s.music_channel);
  if (s.modlog_channel) modLogChannelSelect.value = String(s.modlog_channel);
}

modServerSelect.addEventListener("change", async () => {
  await refreshModChannels();
  await loadGuildSettings();
});

saveMusicChannelBtn.addEventListener("click", async () => {
  const guildId = modServerSelect.value;
  if (!guildId) {
    setMsg(musicChannelMsg, "Pick a server first.", "error");
    return;
  }
  const data = await api("/api/guild_settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guild_id: guildId, music_channel: modMusicChannelSelect.value || null }),
  });
  setMsg(musicChannelMsg, data.ok ? "Saved." : (data.error || "Couldn't save."), data.ok ? "success" : "error");
});

saveModLogChannelBtn.addEventListener("click", async () => {
  const guildId = modServerSelect.value;
  if (!guildId) {
    setMsg(modLogChannelMsg, "Pick a server first.", "error");
    return;
  }
  const data = await api("/api/guild_settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guild_id: guildId, modlog_channel: modLogChannelSelect.value || null }),
  });
  setMsg(modLogChannelMsg, data.ok ? "Saved." : (data.error || "Couldn't save."), data.ok ? "success" : "error");
});

async function refreshModWarnings() {
  const guildId = modServerSelect.value;
  const userId = modUserIdInput.value.trim();
  if (!guildId || !userId) {
    modWarningsList.textContent = "Enter a user ID above to check.";
    return;
  }
  const entries = await api(`/api/moderation/warnings?guild_id=${encodeURIComponent(guildId)}&user_id=${encodeURIComponent(userId)}`);
  if (!entries.length) {
    modWarningsList.textContent = "No warnings.";
    return;
  }
  modWarningsList.innerHTML = entries.map((e, i) => `${i + 1}. ${escapeHtml(e.reason)} (by ${escapeHtml(e.by)})`).join("<br>");
}

modUserIdInput.addEventListener("blur", refreshModWarnings);

async function runModAction(action, { confirmText, needsMinutes } = {}) {
  const guildId = modServerSelect.value;
  const userId = modUserIdInput.value.trim();
  if (!guildId || !userId) {
    setMsg(modActionMsg, "Pick a server and enter a user ID first.", "error");
    return;
  }
  if (confirmText && !confirm(confirmText)) return;

  const body = { guild_id: guildId, user_id: userId, action, reason: modReasonInput.value.trim() };
  if (needsMinutes) body.minutes = parseInt(modMinutesInput.value, 10) || 10;

  setMsg(modActionMsg, "Working...", "");
  const data = await api("/api/moderation/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  setMsg(modActionMsg, data.ok ? "Done." : (data.error || "Couldn't do that."), data.ok ? "success" : "error");
  if (data.ok && (action === "warn" || action === "clearwarnings")) refreshModWarnings();
}

document.getElementById("modKickBtn").addEventListener("click", () =>
  runModAction("kick", { confirmText: "Kick this member?" }));
document.getElementById("modBanBtn").addEventListener("click", () =>
  runModAction("ban", { confirmText: "Ban this member? This can't be easily undone." }));
document.getElementById("modTimeoutBtn").addEventListener("click", () =>
  runModAction("timeout", { needsMinutes: true }));
document.getElementById("modWarnBtn").addEventListener("click", () =>
  runModAction("warn"));
document.getElementById("modClearWarningsBtn").addEventListener("click", () =>
  runModAction("clearwarnings", { confirmText: "Clear all warnings for this member?" }));
document.getElementById("modClearNickBtn").addEventListener("click", () =>
  runModAction("clearnick"));

// ---------- init ----------

loadTokenState();
