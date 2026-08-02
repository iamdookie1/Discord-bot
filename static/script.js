// ---------- tab switching ----------

const tabs = document.querySelectorAll(".rail-tab");
const panels = document.querySelectorAll(".panel");

tabs.forEach((tab) => {
  tab.addEventListener("click", () => {
    tabs.forEach((t) => t.classList.remove("is-active"));
    panels.forEach((p) => p.classList.remove("is-active"));
    tab.classList.add("is-active");
    document.getElementById(`tab-${tab.dataset.tab}`).classList.add("is-active");
    if (tab.dataset.tab === "text") refreshGuilds();
    if (tab.dataset.tab === "bot") loadBotNamePlaceholder();
    if (tab.dataset.tab === "cmds") loadBuiltinCommands();
    if (tab.dataset.tab === "customcmds") loadCustomCommands();
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

serverSelect.addEventListener("change", () => refreshChannels(serverSelect.value));

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

// ---------- cmds tab ----------

const builtinCmdList = document.getElementById("builtinCmdList");

async function loadBuiltinCommands() {
  const cmds = await api("/api/commands/builtin");
  builtinCmdList.innerHTML = cmds.map((c) => `
    <div class="cmd-item">
      <span class="cmd-name mono">!${escapeHtml(c.name)}</span>
      <span class="cmd-desc">${escapeHtml(c.description)}</span>
    </div>
  `).join("");
}

// ---------- custom cmds tab ----------

const customCmdList = document.getElementById("customCmdList");
const customCmdEmpty = document.getElementById("customCmdEmpty");
const customCmdName = document.getElementById("customCmdName");
const customCmdDescription = document.getElementById("customCmdDescription");
const customCmdCode = document.getElementById("customCmdCode");
const createCustomCmdBtn = document.getElementById("createCustomCmdBtn");
const customCmdMsg = document.getElementById("customCmdMsg");

async function loadCustomCommands() {
  const cmds = await api("/api/commands/custom");
  customCmdEmpty.style.display = cmds.length ? "none" : "block";
  customCmdList.innerHTML = cmds.map((c) => `
    <div class="custom-cmd-item">
      <div class="custom-cmd-head">
        <span class="cmd-name mono">!${escapeHtml(c.name)}</span>
        <button type="button" class="btn-ghost btn-small custom-cmd-remove" data-name="${escapeHtml(c.name)}">Remove</button>
      </div>
      ${c.description ? `<span class="custom-cmd-desc">${escapeHtml(c.description)}</span>` : ""}
    </div>
  `).join("");

  customCmdList.querySelectorAll(".custom-cmd-remove").forEach((btn) => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      await api(`/api/commands/custom/${encodeURIComponent(btn.dataset.name)}`, { method: "DELETE" });
      loadCustomCommands();
    });
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
    customCmdName.value = "";
    customCmdDescription.value = "";
    customCmdCode.value = "";
    loadCustomCommands();
  } else {
    setMsg(customCmdMsg, data.error || "Couldn't save that command.", "error");
  }
});

// ---------- init ----------

loadTokenState();
