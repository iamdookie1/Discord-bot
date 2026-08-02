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

// ---------- init ----------

loadTokenState();
