// ---------- mobile nav (hamburger + off-canvas drawer) ----------

const railEl = document.getElementById("rail");
const railBackdrop = document.getElementById("railBackdrop");
const navToggleBtn = document.getElementById("navToggleBtn");

function openMobileNav() {
  railEl.classList.add("is-open");
  railBackdrop.classList.add("is-open");
}

function closeMobileNav() {
  railEl.classList.remove("is-open");
  railBackdrop.classList.remove("is-open");
}

navToggleBtn.addEventListener("click", () => {
  railEl.classList.contains("is-open") ? closeMobileNav() : openMobileNav();
});
railBackdrop.addEventListener("click", closeMobileNav);

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
      textMusicController.start();
    } else {
      textMusicController.stop();
    }
    if (tab.dataset.tab === "music") {
      refreshMusicTabServers();
      musicTabController.start();
    } else {
      musicTabController.stop();
    }
    if (tab.dataset.tab === "bot") {
      loadBotNamePlaceholder();
      loadPresence();
      loadTtsSettings();
      refreshLeaveServerList();
    }
    if (tab.dataset.tab === "cmds") loadBuiltinCommands();
    if (tab.dataset.tab === "customcmds") loadCustomCommands();
    if (tab.dataset.tab === "rp") loadRpCommands();
    if (tab.dataset.tab === "backup") {
      refreshBackupServers();
      loadBackupList();
    }
    if (tab.dataset.tab === "mod") refreshModServers();
    if (tab.dataset.tab === "layout") layoutEditor.open();
    if (tab.dataset.tab === "fonts" && !fontsInitialized) initFontsTab();
    if (tab.dataset.tab === "servers") loadServerList();
    closeMobileNav();
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
  textMusicController.poll();
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

// ---------- music: shared controller (used by the Text tab's compact card and the Music tab) ----------

const MUSIC_POLL_MS = 1000;

function fmtMusicTime(seconds) {
  seconds = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const mm = h ? String(m).padStart(2, "0") : m;
  const ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

// `els.card` (optional): a single container hidden/shown wholesale when music is unavailable.
// `els.content`/`els.unavailableEl` (optional): used instead of `els.card` when the tab shows
// an explicit "not available" message alongside other always-visible chrome (e.g. the server picker).
function createMusicController(getGuildId, els) {
  let pollTimer = null;
  let crossfadeSliderDirty = false;   // true while the user's actively dragging it — don't fight them mid-poll
  let effectSlidersDirty = false;     // same idea, for whichever effect's parameter sliders are showing
  let lastSliderMode = null;          // which effect the sliders were last built for — rebuild only on change

  function render(data) {
    const available = !!(data && data.available);

    if (els.unavailableEl) els.unavailableEl.style.display = data && !available ? "block" : "none";
    if (els.content) els.content.style.display = available ? "block" : "none";
    if (els.card) els.card.style.display = available ? "block" : "none";
    if (!available) return;

    if (!data.connected || !data.title) {
      els.title.textContent = "Nothing playing";
      els.progressFill.style.width = "0%";
      els.elapsed.textContent = "0:00";
      els.duration.textContent = "—";
      els.volumeLabel.textContent = `${data.volume ?? 100}%`;
      els.loopBtn.textContent = `🔁 Loop: ${(data.loop_mode || "off").replace(/^\w/, (c) => c.toUpperCase())}`;
      els.pauseBtn.textContent = "⏸️ Pause";
      if (els.queueHint) els.queueHint.textContent = "";
      if (els.queueList) els.queueList.innerHTML = '<p class="field-hint">Queue is empty.</p>';
      if (els.requester) els.requester.textContent = "";
      setEffectRadios(data.effect_mode);
      setCrossfade(data.crossfade_seconds);
      renderEffectSliders(data);
      return;
    }

    els.title.textContent = data.title;
    els.elapsed.textContent = fmtMusicTime(data.elapsed);
    els.pauseBtn.textContent = data.paused ? "▶️ Resume" : "⏸️ Pause";
    els.volumeLabel.textContent = `${data.volume}%`;
    els.loopBtn.textContent = `🔁 Loop: ${data.loop_mode.replace(/^\w/, (c) => c.toUpperCase())}`;
    if (els.requester) els.requester.textContent = data.requester ? `Requested by ${data.requester}` : "";

    if (data.duration) {
      els.duration.textContent = fmtMusicTime(data.duration);
      els.progressFill.style.width = `${Math.min(100, (data.elapsed / data.duration) * 100)}%`;
    } else {
      els.duration.textContent = "—";
      els.progressFill.style.width = "0%";
    }

    if (els.queueHint) {
      els.queueHint.textContent = data.queue_length
        ? `${data.queue_length} more in queue: ${data.queue.slice(0, 3).join(", ")}${data.queue_length > 3 ? "…" : ""}`
        : "";
    }
    if (els.queueList) {
      els.queueList.innerHTML = data.queue_length
        ? data.queue
            .map((t, i) => `
              <div class="music-queue-item">
                <span class="music-queue-text"><span class="music-queue-index">${i + 1}.</span> ${escapeHtml(t)}</span>
                <button type="button" class="music-queue-remove" data-index="${i + 1}" title="Remove from queue">&times;</button>
              </div>`)
            .join("")
        : '<p class="field-hint">Queue is empty.</p>';
      if (data.queue_length) {
        els.queueList.querySelectorAll(".music-queue-remove").forEach((btn) => {
          btn.addEventListener("click", () => removeFromQueue(Number(btn.dataset.index)));
        });
      }
    }

    setEffectRadios(data.effect_mode);
    setCrossfade(data.crossfade_seconds);
    renderEffectSliders(data);
  }

  function setEffectRadios(mode) {
    if (!els.effectRadios) return;
    els.effectRadios().forEach((r) => {
      r.checked = r.value === (mode || "off");
    });
  }

  function setCrossfade(seconds) {
    if (!els.crossfadeSlider || crossfadeSliderDirty) return;
    const value = seconds ?? 0;
    els.crossfadeSlider.value = value;
    if (els.crossfadeValueLabel) els.crossfadeValueLabel.textContent = value > 0 ? `${value}s` : "Off";
  }

  function renderEffectSliders(data) {
    if (!els.effectSlidersContainer) return;
    const mode = data.effect_mode || "off";
    const specs = (data.effect_param_specs && data.effect_param_specs[mode]) || [];

    if (!specs.length) {
      els.effectSlidersContainer.style.display = "none";
      els.effectSlidersContainer.innerHTML = "";
      lastSliderMode = mode;
      return;
    }

    els.effectSlidersContainer.style.display = "block";

    if (mode !== lastSliderMode) {
      const tied = (data.effect_tied_modes || []).includes(mode);
      const values = data.effect_params || {};
      els.effectSlidersContainer.innerHTML = specs.map((spec) => `
        <div class="music-effect-slider-row" data-param-id="${spec.id}">
          <label class="field-label">
            <span>${escapeHtml(spec.label)}</span>
            <span class="mono effect-slider-value">${values[spec.id] ?? spec.default}${spec.unit || ""}</span>
          </label>
          <input type="range" class="field-range effect-slider-input" min="${spec.min}" max="${spec.max}" step="${spec.step}" value="${values[spec.id] ?? spec.default}">
        </div>
      `).join("") + (tied ? `
        <label class="checkbox-row">
          <input type="checkbox" id="effectTiedCheckbox" ${data.custom_tied !== false ? "checked" : ""}>
          <span>Pitch tied to speed</span>
        </label>
      ` : "");

      els.effectSlidersContainer.querySelectorAll(".effect-slider-input").forEach((input) => {
        input.addEventListener("pointerdown", () => { effectSlidersDirty = true; });
        input.addEventListener("input", () => {
          const row = input.closest(".music-effect-slider-row");
          const spec = specs.find((s) => s.id === row.dataset.paramId);
          row.querySelector(".effect-slider-value").textContent = `${input.value}${spec.unit || ""}`;
        });
        input.addEventListener("change", () => {
          effectSlidersDirty = false;
          submitEffectParams();
        });
      });
      const tiedCheckbox = els.effectSlidersContainer.querySelector("#effectTiedCheckbox");
      if (tiedCheckbox) {
        tiedCheckbox.addEventListener("change", () => submitEffectParams());
      }
      lastSliderMode = mode;
    } else if (!effectSlidersDirty) {
      const values = data.effect_params || {};
      els.effectSlidersContainer.querySelectorAll(".music-effect-slider-row").forEach((row) => {
        const spec = specs.find((s) => s.id === row.dataset.paramId);
        if (!spec) return;
        const value = values[spec.id] ?? spec.default;
        row.querySelector(".effect-slider-input").value = value;
        row.querySelector(".effect-slider-value").textContent = `${value}${spec.unit || ""}`;
      });
      const tiedCheckbox = els.effectSlidersContainer.querySelector("#effectTiedCheckbox");
      if (tiedCheckbox) tiedCheckbox.checked = data.custom_tied !== false;
    }
  }

  async function submitEffectParams() {
    const guildId = getGuildId();
    if (!guildId || !els.effectSlidersContainer) return;
    const params = {};
    els.effectSlidersContainer.querySelectorAll(".music-effect-slider-row").forEach((row) => {
      params[row.dataset.paramId] = Number(row.querySelector(".effect-slider-input").value);
    });
    const tiedCheckbox = els.effectSlidersContainer.querySelector("#effectTiedCheckbox");
    const body = { guild_id: guildId, params };
    if (tiedCheckbox) body.tied = tiedCheckbox.checked;
    const data = await api("/api/music/effect_params", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!data.ok) setMsg(els.msg, data.error || "Couldn't update that.", "error");
    poll();
  }

  async function poll() {
    const guildId = getGuildId();
    if (!guildId) {
      render(null);
      return;
    }
    try {
      const data = await api(`/api/music/state?guild_id=${encodeURIComponent(guildId)}`);
      render(data);
    } catch (e) {
      // transient fetch failure — leave the display as-is, next poll will retry
    }
  }

  function start() {
    stop();
    poll();
    pollTimer = setInterval(poll, MUSIC_POLL_MS);
  }

  function stop() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function action(name) {
    const guildId = getGuildId();
    if (!guildId) return;
    const data = await api("/api/music/action", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ guild_id: guildId, action: name }),
    });
    setMsg(els.msg, data.ok ? "" : data.error || "Couldn't do that.", data.ok ? "" : "error");
    poll();
  }

  async function volume(delta) {
    const guildId = getGuildId();
    if (!guildId) return;
    const data = await api("/api/music/volume", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ guild_id: guildId, delta }),
    });
    if (!data.ok) setMsg(els.msg, data.error || "Couldn't do that.", "error");
    poll();
  }

  async function setEffect(mode) {
    const guildId = getGuildId();
    if (!guildId) return;
    const data = await api("/api/music/effect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ guild_id: guildId, mode }),
    });
    if (!data.ok) setMsg(els.msg, data.error || "Couldn't set that effect.", "error");
    poll();
  }

  async function setCrossfadeSeconds(seconds) {
    const guildId = getGuildId();
    if (!guildId) return;
    const data = await api("/api/music/crossfade", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ guild_id: guildId, seconds }),
    });
    if (!data.ok) setMsg(els.msg, data.error || "Couldn't set that.", "error");
  }

  function beginCrossfadeDrag() {
    crossfadeSliderDirty = true;
  }

  function endCrossfadeDrag() {
    crossfadeSliderDirty = false;
  }

  async function playSong(query, msgEl) {
    const guildId = getGuildId();
    const target = msgEl || els.msg;
    if (!guildId) {
      setMsg(target, "Pick a server first.", "error");
      return;
    }
    if (!query || !query.trim()) {
      setMsg(target, "Type a song name or link first.", "error");
      return;
    }
    setMsg(target, "Looking that up...", "");
    const data = await api("/api/music/play", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ guild_id: guildId, query }),
    });
    if (data.ok) {
      setMsg(target, data.queued ? `Queued "${data.title}".` : `Now playing "${data.title}".`, "success");
    } else {
      setMsg(target, data.error || "Couldn't find that.", "error");
    }
    poll();
    return data;
  }

  async function removeFromQueue(index) {
    const guildId = getGuildId();
    if (!guildId) return;
    const data = await api("/api/music/queue/remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ guild_id: guildId, index }),
    });
    if (!data.ok) setMsg(els.msg, data.error || "Couldn't remove that.", "error");
    poll();
  }

  return {
    render, poll, start, stop, action, volume, setEffect,
    setCrossfadeSeconds, beginCrossfadeDrag, endCrossfadeDrag,
    playSong, removeFromQueue,
  };
}

// ---------- text tab: compact music card ----------

const musicPauseBtn = document.getElementById("musicPauseBtn");
const musicSkipBtn = document.getElementById("musicSkipBtn");
const musicStopBtn = document.getElementById("musicStopBtn");
const musicVolDownBtn = document.getElementById("musicVolDownBtn");
const musicVolUpBtn = document.getElementById("musicVolUpBtn");
const musicLoopBtn = document.getElementById("musicLoopBtn");

const textMusicController = createMusicController(() => serverSelect.value, {
  card: document.getElementById("musicCard"),
  title: document.getElementById("musicTrackTitle"),
  progressFill: document.getElementById("musicProgressFill"),
  elapsed: document.getElementById("musicElapsed"),
  duration: document.getElementById("musicDuration"),
  pauseBtn: musicPauseBtn,
  volumeLabel: document.getElementById("musicVolumeLabel"),
  loopBtn: musicLoopBtn,
  queueHint: document.getElementById("musicQueueHint"),
  msg: document.getElementById("musicMsg"),
});

musicPauseBtn.addEventListener("click", () => {
  textMusicController.action(musicPauseBtn.textContent.includes("Resume") ? "resume" : "pause");
});
musicSkipBtn.addEventListener("click", () => textMusicController.action("skip"));
musicStopBtn.addEventListener("click", () => textMusicController.action("stop"));
musicLoopBtn.addEventListener("click", () => textMusicController.action("loop"));
musicVolDownBtn.addEventListener("click", () => textMusicController.volume(-10));
musicVolUpBtn.addEventListener("click", () => textMusicController.volume(10));

// ---------- music tab: full controls ----------

const musicTabServerSelect = document.getElementById("musicTabServerSelect");
const mtPauseBtn = document.getElementById("mtPauseBtn");
const mtSkipBtn = document.getElementById("mtSkipBtn");
const mtStopBtn = document.getElementById("mtStopBtn");
const mtVolDownBtn = document.getElementById("mtVolDownBtn");
const mtVolUpBtn = document.getElementById("mtVolUpBtn");
const mtLoopBtn = document.getElementById("mtLoopBtn");

const musicTabController = createMusicController(() => musicTabServerSelect.value, {
  content: document.getElementById("musicTabContent"),
  unavailableEl: document.getElementById("musicTabUnavailable"),
  title: document.getElementById("mtTrackTitle"),
  requester: document.getElementById("mtRequester"),
  progressFill: document.getElementById("mtProgressFill"),
  elapsed: document.getElementById("mtElapsed"),
  duration: document.getElementById("mtDuration"),
  pauseBtn: mtPauseBtn,
  volumeLabel: document.getElementById("mtVolumeLabel"),
  loopBtn: mtLoopBtn,
  queueList: document.getElementById("mtQueueList"),
  msg: document.getElementById("mtMsg"),
  effectRadios: () => document.querySelectorAll('input[name="mtEffect"]'),
  effectSlidersContainer: document.getElementById("mtEffectSliders"),
  crossfadeSlider: document.getElementById("mtCrossfadeSlider"),
  crossfadeValueLabel: document.getElementById("mtCrossfadeValue"),
});

mtPauseBtn.addEventListener("click", () => {
  musicTabController.action(mtPauseBtn.textContent.includes("Resume") ? "resume" : "pause");
});
mtSkipBtn.addEventListener("click", () => musicTabController.action("skip"));
mtStopBtn.addEventListener("click", () => musicTabController.action("stop"));
mtLoopBtn.addEventListener("click", () => musicTabController.action("loop"));
mtVolDownBtn.addEventListener("click", () => musicTabController.volume(-10));
mtVolUpBtn.addEventListener("click", () => musicTabController.volume(10));

document.querySelectorAll('input[name="mtEffect"]').forEach((radio) => {
  radio.addEventListener("change", () => {
    if (radio.checked) musicTabController.setEffect(radio.value);
  });
});

const mtPlayInput = document.getElementById("mtPlayInput");
const mtPlayBtn = document.getElementById("mtPlayBtn");
const mtPlayMsg = document.getElementById("mtPlayMsg");

mtPlayBtn.addEventListener("click", async () => {
  mtPlayBtn.disabled = true;
  await musicTabController.playSong(mtPlayInput.value, mtPlayMsg);
  mtPlayBtn.disabled = false;
  mtPlayInput.value = "";
});
mtPlayInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") mtPlayBtn.click();
});

const mtCrossfadeSlider = document.getElementById("mtCrossfadeSlider");
const mtCrossfadeValue = document.getElementById("mtCrossfadeValue");

mtCrossfadeSlider.addEventListener("input", () => {
  const value = Number(mtCrossfadeSlider.value);
  mtCrossfadeValue.textContent = value > 0 ? `${value}s` : "Off";
});
mtCrossfadeSlider.addEventListener("pointerdown", () => musicTabController.beginCrossfadeDrag());
mtCrossfadeSlider.addEventListener("change", () => {
  musicTabController.setCrossfadeSeconds(Number(mtCrossfadeSlider.value));
  musicTabController.endCrossfadeDrag();
});

async function refreshMusicTabServers() {
  const guilds = await api("/api/guilds");
  const current = musicTabServerSelect.value;
  if (!guilds.length) {
    musicTabServerSelect.innerHTML = '<option value="">No servers found (is the bot online + invited?)</option>';
    return;
  }
  musicTabServerSelect.innerHTML =
    '<option value="">Choose a server&hellip;</option>' +
    guilds.map((g) => `<option value="${g.id}">${escapeHtml(g.name)}</option>`).join("");
  if (current) musicTabServerSelect.value = current;
}

musicTabServerSelect.addEventListener("change", () => musicTabController.poll());

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

// ---------- bot tab: leave a server ----------

const leaveServerSelect = document.getElementById("leaveServerSelect");
const leaveServerBtn = document.getElementById("leaveServerBtn");
const leaveServerMsg = document.getElementById("leaveServerMsg");

async function refreshLeaveServerList() {
  const guilds = await api("/api/guilds");
  const current = leaveServerSelect.value;
  if (!guilds.length) {
    leaveServerSelect.innerHTML = '<option value="">No servers found (is the bot online + invited?)</option>';
    return;
  }
  leaveServerSelect.innerHTML =
    '<option value="">Choose a server&hellip;</option>' +
    guilds.map((g) => `<option value="${g.id}">${escapeHtml(g.name)}</option>`).join("");
  if (current) leaveServerSelect.value = current;
}

leaveServerBtn.addEventListener("click", async () => {
  const guildId = leaveServerSelect.value;
  if (!guildId) {
    setMsg(leaveServerMsg, "Pick a server first.", "error");
    return;
  }
  const serverName = leaveServerSelect.options[leaveServerSelect.selectedIndex].textContent;
  if (!confirm(`Leave "${serverName}"? The bot will need to be re-invited to come back.`)) return;

  leaveServerBtn.disabled = true;
  const data = await api("/api/guilds/leave", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guild_id: guildId }),
  });
  leaveServerBtn.disabled = false;

  if (data.ok) {
    setMsg(leaveServerMsg, `Left "${data.name}".`, "success");
    refreshLeaveServerList();
  } else {
    setMsg(leaveServerMsg, data.error || "Couldn't leave that server.", "error");
  }
});

// ---------- bot tab: tts voice settings ----------

const ttsSettingsCard = document.getElementById("ttsSettingsCard");
const ttsVoiceSelect = document.getElementById("ttsVoiceSelect");
const ttsOnlyMeCheckbox = document.getElementById("ttsOnlyMeCheckbox");
const ttsSliders = document.getElementById("ttsSliders");
const ttsSettingsMsg = document.getElementById("ttsSettingsMsg");
const ownerVoiceSelect = document.getElementById("ownerVoiceSelect");
const ownerVoiceOverrideCheckbox = document.getElementById("ownerVoiceOverrideCheckbox");
const ownerVolumeOverrideCheckbox = document.getElementById("ownerVolumeOverrideCheckbox");
const ownerRateOverrideCheckbox = document.getElementById("ownerRateOverrideCheckbox");
const ownerTtsSliders = document.getElementById("ownerTtsSliders");
const ownerEffectSelect = document.getElementById("ownerEffectSelect");
const ownerEffectSliders = document.getElementById("ownerEffectSliders");
const ownerEffectUnavailable = document.getElementById("ownerEffectUnavailable");

let ttsSlidersDirty = false;
let ttsSlidersBuilt = false;
let ownerTtsSlidersDirty = false;
let ownerTtsSlidersBuilt = false;
let ownerEffectSlidersDirty = false;
let ownerEffectLastMode = null;

function buildTtsSliderGroup(container, specs, settings, dirty) {
  container.innerHTML = specs.map((spec) => `
    <div class="music-effect-slider-row" data-param-id="${spec.id}">
      <label class="field-label">
        <span>${escapeHtml(spec.label)}</span>
        <span class="mono tts-slider-value">${settings[spec.id]}${spec.unit || ""}</span>
      </label>
      <input type="range" class="field-range tts-slider-input" min="${spec.min}" max="${spec.max}" step="${spec.step}" value="${settings[spec.id]}">
    </div>
  `).join("");
  container.querySelectorAll(".tts-slider-input").forEach((input) => {
    const specId = input.closest(".music-effect-slider-row").dataset.paramId;
    const spec = specs.find((s) => s.id === specId);
    input.addEventListener("pointerdown", () => { dirty.value = true; });
    input.addEventListener("input", () => {
      input.closest(".music-effect-slider-row").querySelector(".tts-slider-value").textContent = `${input.value}${spec.unit || ""}`;
    });
    input.addEventListener("change", () => {
      dirty.value = false;
      saveTtsSettings({ [specId]: Number(input.value) });
    });
  });
}

function refreshTtsSliderGroup(container, specs, settings) {
  container.querySelectorAll(".music-effect-slider-row").forEach((row) => {
    const spec = specs.find((s) => s.id === row.dataset.paramId);
    if (!spec) return;
    row.querySelector(".tts-slider-input").value = settings[spec.id];
    row.querySelector(".tts-slider-value").textContent = `${settings[spec.id]}${spec.unit || ""}`;
  });
}

function renderOwnerEffectSliders(data) {
  const mode = ownerEffectSelect.value;
  const specs = (data.owner_effect_param_specs && data.owner_effect_param_specs[mode]) || [];

  if (!specs.length) {
    ownerEffectSliders.style.display = "none";
    ownerEffectSliders.innerHTML = "";
    ownerEffectLastMode = mode;
    return;
  }
  ownerEffectSliders.style.display = "flex";

  if (mode !== ownerEffectLastMode) {
    const values = data.owner_effect_params || {};
    ownerEffectSliders.innerHTML = specs.map((spec) => `
      <div class="music-effect-slider-row" data-param-id="${spec.id}">
        <label class="field-label">
          <span>${escapeHtml(spec.label)}</span>
          <span class="mono owner-effect-slider-value">${values[spec.id] ?? spec.default}${spec.unit || ""}</span>
        </label>
        <input type="range" class="field-range owner-effect-slider-input" min="${spec.min}" max="${spec.max}" step="${spec.step}" value="${values[spec.id] ?? spec.default}">
      </div>
    `).join("");
    ownerEffectSliders.querySelectorAll(".owner-effect-slider-input").forEach((input) => {
      const row = input.closest(".music-effect-slider-row");
      const spec = specs.find((s) => s.id === row.dataset.paramId);
      input.addEventListener("pointerdown", () => { ownerEffectSlidersDirty = true; });
      input.addEventListener("input", () => {
        row.querySelector(".owner-effect-slider-value").textContent = `${input.value}${spec.unit || ""}`;
      });
      input.addEventListener("change", () => {
        ownerEffectSlidersDirty = false;
        submitOwnerEffectParams();
      });
    });
    ownerEffectLastMode = mode;
  } else if (!ownerEffectSlidersDirty) {
    const values = data.owner_effect_params || {};
    ownerEffectSliders.querySelectorAll(".music-effect-slider-row").forEach((row) => {
      const spec = specs.find((s) => s.id === row.dataset.paramId);
      if (!spec) return;
      const v = values[spec.id] ?? spec.default;
      row.querySelector(".owner-effect-slider-input").value = v;
      row.querySelector(".owner-effect-slider-value").textContent = `${v}${spec.unit || ""}`;
    });
  }
}

function submitOwnerEffectParams() {
  const params = {};
  ownerEffectSliders.querySelectorAll(".music-effect-slider-row").forEach((row) => {
    params[row.dataset.paramId] = Number(row.querySelector(".owner-effect-slider-input").value);
  });
  saveTtsSettings({ owner_effect_params: params });
}

async function loadTtsSettings() {
  const data = await api("/api/tts/settings");
  if (!data.available) {
    ttsSettingsCard.style.display = "none";
    return;
  }
  ttsSettingsCard.style.display = "block";

  if (ttsVoiceSelect.options.length !== (data.voices || []).length) {
    const optionsHtml = data.voices.map((v) => `<option value="${v.slot}">${escapeHtml(v.label)}</option>`).join("");
    ttsVoiceSelect.innerHTML = optionsHtml;
    ownerVoiceSelect.innerHTML = optionsHtml;
  }
  ttsVoiceSelect.value = data.settings.voice_slot;
  ttsOnlyMeCheckbox.checked = !!data.settings.only_me;
  ownerVoiceSelect.value = data.settings.owner_voice_slot;
  ownerVoiceOverrideCheckbox.checked = !!data.settings.owner_voice_override;
  ownerVolumeOverrideCheckbox.checked = !!data.settings.owner_volume_override;
  ownerRateOverrideCheckbox.checked = !!data.settings.owner_rate_override;

  if (!ttsSlidersBuilt) {
    buildTtsSliderGroup(ttsSliders, data.specs, data.settings, {
      set value(v) { ttsSlidersDirty = v; }, get value() { return ttsSlidersDirty; },
    });
    ttsSlidersBuilt = true;
  } else if (!ttsSlidersDirty) {
    refreshTtsSliderGroup(ttsSliders, data.specs, data.settings);
  }

  if (!ownerTtsSlidersBuilt) {
    buildTtsSliderGroup(ownerTtsSliders, data.owner_specs, data.settings, {
      set value(v) { ownerTtsSlidersDirty = v; }, get value() { return ownerTtsSlidersDirty; },
    });
    ownerTtsSlidersBuilt = true;
  } else if (!ownerTtsSlidersDirty) {
    refreshTtsSliderGroup(ownerTtsSliders, data.owner_specs, data.settings);
  }

  if (ownerEffectSelect.options.length !== (data.owner_effect_modes || []).length) {
    ownerEffectSelect.innerHTML = data.owner_effect_modes
      .map((m) => `<option value="${m}">${escapeHtml(data.owner_effect_labels[m] || m)}</option>`)
      .join("");
  }
  ownerEffectSelect.value = data.settings.owner_effect_mode;
  ownerEffectUnavailable.style.display = data.effects_available ? "none" : "block";
  renderOwnerEffectSliders(data);
}

async function saveTtsSettings(updates) {
  const data = await api("/api/tts/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!data.ok) setMsg(ttsSettingsMsg, data.error || "Couldn't save that.", "error");
  else setMsg(ttsSettingsMsg, "Saved.", "success");
  return data;
}

ttsVoiceSelect.addEventListener("change", () => saveTtsSettings({ voice_slot: Number(ttsVoiceSelect.value) }));
ttsOnlyMeCheckbox.addEventListener("change", () => saveTtsSettings({ only_me: ttsOnlyMeCheckbox.checked }));
ownerVoiceSelect.addEventListener("change", () => saveTtsSettings({ owner_voice_slot: Number(ownerVoiceSelect.value) }));
ownerVoiceOverrideCheckbox.addEventListener("change", () => saveTtsSettings({ owner_voice_override: ownerVoiceOverrideCheckbox.checked }));
ownerVolumeOverrideCheckbox.addEventListener("change", () => saveTtsSettings({ owner_volume_override: ownerVolumeOverrideCheckbox.checked }));
ownerRateOverrideCheckbox.addEventListener("change", () => saveTtsSettings({ owner_rate_override: ownerRateOverrideCheckbox.checked }));
ownerEffectSelect.addEventListener("change", async () => {
  await saveTtsSettings({ owner_effect_mode: ownerEffectSelect.value });
  loadTtsSettings();
});

// ---------- cmds tab ----------

const utilityCmdList = document.getElementById("utilityCmdList");
const moderationCmdList = document.getElementById("moderationCmdList");
const ttsCmdList = document.getElementById("ttsCmdList");
const layoutCmdList = document.getElementById("layoutCmdList");
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
  const byCategory = { utility: [], moderation: [], layout: [], music: [], tts: [] };
  cmds.forEach((c) => { (byCategory[c.category] || byCategory.utility).push(c); });

  const fill = (el, list) => {
    el.innerHTML = "";
    list.sort((a, b) => a.name.localeCompare(b.name)).forEach((c) => el.appendChild(renderBuiltinCmdItem(c)));
  };
  fill(utilityCmdList, byCategory.utility);
  fill(moderationCmdList, byCategory.moderation);
  fill(musicCmdList, byCategory.music);
  fill(ttsCmdList, byCategory.tts);
  fill(layoutCmdList, byCategory.layout);
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
const modMuteRoleSelect = document.getElementById("modMuteRoleSelect");
const saveMuteRoleBtn = document.getElementById("saveMuteRoleBtn");
const muteRoleMsg = document.getElementById("muteRoleMsg");
const modUserIdInput = document.getElementById("modUserIdInput");
const modReasonInput = document.getElementById("modReasonInput");
const modMinutesInput = document.getElementById("modMinutesInput");
const modWarnIndexInput = document.getElementById("modWarnIndexInput");
const modNickInput = document.getElementById("modNickInput");
const modRoleSelect = document.getElementById("modRoleSelect");
const modActionMsg = document.getElementById("modActionMsg");
const modWarningsList = document.getElementById("modWarningsList");
const modChannelSelect = document.getElementById("modChannelSelect");
const modPurgeCountInput = document.getElementById("modPurgeCountInput");
const modSlowmodeInput = document.getElementById("modSlowmodeInput");
const modAnnounceInput = document.getElementById("modAnnounceInput");
const modMessageIdInput = document.getElementById("modMessageIdInput");
const modPurgeUserIdInput = document.getElementById("modPurgeUserIdInput");
const modChannelMsg = document.getElementById("modChannelMsg");
const modNewRoleInput = document.getElementById("modNewRoleInput");
const modRoleCreateMsg = document.getElementById("modRoleCreateMsg");
const modRoleList = document.getElementById("modRoleList");
const modBanList = document.getElementById("modBanList");

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
  await refreshModRoles();
  await loadGuildSettings();
  await refreshModBanList();
}

async function refreshModChannels() {
  const guildId = modServerSelect.value;
  if (!guildId) {
    modMusicChannelSelect.innerHTML = '<option value="">Pick a server first&hellip;</option>';
    modLogChannelSelect.innerHTML = '<option value="">Pick a server first&hellip;</option>';
    modChannelSelect.innerHTML = '<option value="">Pick a server first&hellip;</option>';
    return;
  }
  const [voiceChannels, textChannels] = await Promise.all([
    api(`/api/channels?guild_id=${encodeURIComponent(guildId)}&type=voice`),
    api(`/api/channels?guild_id=${encodeURIComponent(guildId)}`),
  ]);
  modMusicChannelSelect.innerHTML = '<option value="">None</option>' +
    voiceChannels.map((c) => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
  modLogChannelSelect.innerHTML = '<option value="">None</option>' +
    textChannels.map((c) => `<option value="${c.id}">#${escapeHtml(c.name)}</option>`).join("");
  modChannelSelect.innerHTML = '<option value="">Choose a channel&hellip;</option>' +
    textChannels.map((c) => `<option value="${c.id}">#${escapeHtml(c.name)}</option>`).join("");
}

async function refreshModRoles() {
  const guildId = modServerSelect.value;
  if (!guildId) {
    modMuteRoleSelect.innerHTML = '<option value="">Pick a server first&hellip;</option>';
    modRoleSelect.innerHTML = '<option value="">Pick a server first&hellip;</option>';
    modRoleList.innerHTML = "";
    return;
  }
  const roles = await api(`/api/roles?guild_id=${encodeURIComponent(guildId)}`);
  modMuteRoleSelect.innerHTML = '<option value="">None</option>' +
    roles.map((r) => `<option value="${r.id}">${escapeHtml(r.name)}</option>`).join("");
  modRoleSelect.innerHTML = '<option value="">Choose a role&hellip;</option>' +
    roles.map((r) => `<option value="${r.id}">${escapeHtml(r.name)}</option>`).join("");

  modRoleList.innerHTML = roles.length
    ? roles.map((r) => `
        <div class="mod-list-item" data-role-id="${r.id}">
          <div class="mod-list-info"><span class="mod-list-name">${escapeHtml(r.name)}</span></div>
          <div class="mod-list-actions">
            <button class="btn-outline btn-small mod-role-delete-btn">Delete</button>
          </div>
        </div>`).join("")
    : '<p class="mod-list-empty">No roles found (or the bot has no manageable roles below it).</p>';

  modRoleList.querySelectorAll(".mod-role-delete-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const row = btn.closest(".mod-list-item");
      const roleId = row.dataset.roleId;
      const roleName = row.querySelector(".mod-list-name").textContent;
      if (!confirm(`Delete role "${roleName}"? This can't be undone.`)) return;
      const data = await api("/api/roles", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ guild_id: guildId, role_id: roleId }),
      });
      if (data.ok) {
        await refreshModRoles();
      } else {
        alert(data.error || "Couldn't delete that role.");
      }
    });
  });
}

document.getElementById("modCreateRoleBtn").addEventListener("click", async () => {
  const guildId = modServerSelect.value;
  const name = modNewRoleInput.value.trim();
  if (!guildId || !name) {
    setMsg(modRoleCreateMsg, "Pick a server and enter a role name first.", "error");
    return;
  }
  const data = await api("/api/roles", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guild_id: guildId, name }),
  });
  setMsg(modRoleCreateMsg, data.ok ? "Created." : (data.error || "Couldn't create that role."), data.ok ? "success" : "error");
  if (data.ok) {
    modNewRoleInput.value = "";
    await refreshModRoles();
  }
});

async function refreshModBanList() {
  const guildId = modServerSelect.value;
  if (!guildId) {
    modBanList.innerHTML = "";
    return;
  }
  const data = await api(`/api/moderation/banlist?guild_id=${encodeURIComponent(guildId)}`);
  if (!data.ok) {
    modBanList.innerHTML = `<p class="mod-list-empty">${escapeHtml(data.error || "Couldn't load the ban list.")}</p>`;
    return;
  }
  if (!data.bans.length) {
    modBanList.innerHTML = '<p class="mod-list-empty">No bans in this server.</p>';
    return;
  }
  modBanList.innerHTML = data.bans.map((b) => `
    <div class="mod-list-item" data-user-id="${b.id}">
      <div class="mod-list-info">
        <span class="mod-list-name">${escapeHtml(b.name)}</span>
        <span class="mod-list-sub">${b.id}</span>
      </div>
      <div class="mod-list-actions">
        <button class="btn-outline btn-small mod-unban-btn">Unban</button>
      </div>
    </div>`).join("");

  modBanList.querySelectorAll(".mod-unban-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const userId = btn.closest(".mod-list-item").dataset.userId;
      const data2 = await api("/api/moderation/user_action", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ guild_id: guildId, user_id: userId, action: "unban" }),
      });
      if (data2.ok) {
        await refreshModBanList();
      } else {
        alert(data2.error || "Couldn't unban that user.");
      }
    });
  });
}

document.getElementById("modRefreshBansBtn").addEventListener("click", refreshModBanList);

async function loadGuildSettings() {
  const guildId = modServerSelect.value;
  if (!guildId) return;
  const s = await api(`/api/guild_settings?guild_id=${encodeURIComponent(guildId)}`);
  if (s.music_channel) modMusicChannelSelect.value = String(s.music_channel);
  if (s.modlog_channel) modLogChannelSelect.value = String(s.modlog_channel);
  if (s.mute_role) modMuteRoleSelect.value = String(s.mute_role);
}

modServerSelect.addEventListener("change", async () => {
  await refreshModChannels();
  await refreshModRoles();
  await loadGuildSettings();
  await refreshModBanList();
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

saveMuteRoleBtn.addEventListener("click", async () => {
  const guildId = modServerSelect.value;
  if (!guildId) {
    setMsg(muteRoleMsg, "Pick a server first.", "error");
    return;
  }
  const data = await api("/api/guild_settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guild_id: guildId, mute_role: modMuteRoleSelect.value || null }),
  });
  setMsg(muteRoleMsg, data.ok ? "Saved." : (data.error || "Couldn't save."), data.ok ? "success" : "error");
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

// ---- member actions (act on modUserIdInput's user, via /api/moderation/action) ----

async function runModAction(action, { confirmText, needsMinutes, extra } = {}) {
  const guildId = modServerSelect.value;
  const userId = modUserIdInput.value.trim();
  if (!guildId || !userId) {
    setMsg(modActionMsg, "Pick a server and enter a user ID first.", "error");
    return;
  }
  if (confirmText && !confirm(confirmText)) return;

  const body = { guild_id: guildId, user_id: userId, action, reason: modReasonInput.value.trim() };
  if (needsMinutes) body.minutes = parseFloat(modMinutesInput.value) || 10;
  if (extra !== undefined) body.extra = extra;

  setMsg(modActionMsg, "Working...", "");
  const data = await api("/api/moderation/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  setMsg(modActionMsg, data.ok ? "Done." : (data.error || "Couldn't do that."), data.ok ? "success" : "error");
  if (data.ok && ["warn", "clearwarnings", "warnremove"].includes(action)) refreshModWarnings();
}

// ---- member actions that work on a raw user ID (no membership needed) ----

async function runModUserAction(action, { confirmText } = {}) {
  const guildId = modServerSelect.value;
  const userId = modUserIdInput.value.trim();
  if (!guildId || !userId) {
    setMsg(modActionMsg, "Pick a server and enter a user ID first.", "error");
    return;
  }
  if (confirmText && !confirm(confirmText)) return;

  setMsg(modActionMsg, "Working...", "");
  const data = await api("/api/moderation/user_action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guild_id: guildId, user_id: userId, action, reason: modReasonInput.value.trim() }),
  });
  setMsg(modActionMsg, data.ok ? "Done." : (data.error || "Couldn't do that."), data.ok ? "success" : "error");
  if (data.ok) refreshModBanList();
}

document.getElementById("modKickBtn").addEventListener("click", () =>
  runModAction("kick", { confirmText: "Kick this member?" }));
document.getElementById("modBanBtn").addEventListener("click", () =>
  runModAction("ban", { confirmText: "Ban this member? This can't be easily undone." }));
document.getElementById("modSoftbanBtn").addEventListener("click", () =>
  runModAction("softban", { confirmText: "Softban this member (ban+unban to purge recent messages)?" }));
document.getElementById("modTempbanBtn").addEventListener("click", () =>
  runModAction("tempban", { needsMinutes: true, confirmText: "Temp-ban this member for the given number of minutes?" }));
document.getElementById("modBanIdBtn").addEventListener("click", () =>
  runModUserAction("banid", { confirmText: "Ban this user ID? They don't need to be a member." }));
document.getElementById("modUnbanBtn").addEventListener("click", () =>
  runModUserAction("unban"));
document.getElementById("modTimeoutBtn").addEventListener("click", () =>
  runModAction("timeout", { needsMinutes: true }));
document.getElementById("modUntimeoutBtn").addEventListener("click", () =>
  runModAction("untimeout"));
document.getElementById("modMuteBtn").addEventListener("click", () =>
  runModAction("mute"));
document.getElementById("modUnmuteBtn").addEventListener("click", () =>
  runModAction("unmute"));
document.getElementById("modWarnBtn").addEventListener("click", () =>
  runModAction("warn"));
document.getElementById("modWarnRemoveBtn").addEventListener("click", () =>
  runModAction("warnremove", { extra: modWarnIndexInput.value.trim() }));
document.getElementById("modClearWarningsBtn").addEventListener("click", () =>
  runModAction("clearwarnings", { confirmText: "Clear all warnings for this member?" }));
document.getElementById("modNickBtn").addEventListener("click", () =>
  runModAction("nick", { extra: modNickInput.value.trim() }));
document.getElementById("modClearNickBtn").addEventListener("click", () =>
  runModAction("clearnick"));
document.getElementById("modAddRoleBtn").addEventListener("click", () =>
  runModAction("addrole", { extra: modRoleSelect.value }));
document.getElementById("modRemoveRoleBtn").addEventListener("click", () =>
  runModAction("removerole", { extra: modRoleSelect.value }));

// ---- channel moderation ----

async function runModChannelAction(action, extra) {
  const guildId = modServerSelect.value;
  const channelId = modChannelSelect.value;
  if (!guildId || !channelId) {
    setMsg(modChannelMsg, "Pick a server and a channel first.", "error");
    return;
  }
  setMsg(modChannelMsg, "Working...", "");
  const data = await api("/api/moderation/channel_action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guild_id: guildId, channel_id: channelId, action, extra }),
  });
  setMsg(
    modChannelMsg,
    data.ok ? ("deleted" in data ? `Deleted ${data.deleted} message(s).` : "Done.") : (data.error || "Couldn't do that."),
    data.ok ? "success" : "error"
  );
}

document.getElementById("modPurgeBtn").addEventListener("click", () =>
  runModChannelAction("purge", modPurgeCountInput.value.trim()));
document.getElementById("modSlowmodeBtn").addEventListener("click", () =>
  runModChannelAction("slowmode", modSlowmodeInput.value.trim()));
document.getElementById("modLockBtn").addEventListener("click", () => runModChannelAction("lock"));
document.getElementById("modUnlockBtn").addEventListener("click", () => runModChannelAction("unlock"));
document.getElementById("modAnnounceBtn").addEventListener("click", () =>
  runModChannelAction("announce", modAnnounceInput.value));

document.getElementById("modPinBtn").addEventListener("click", async () => {
  const guildId = modServerSelect.value;
  const channelId = modChannelSelect.value;
  const messageId = modMessageIdInput.value.trim();
  if (!guildId || !channelId || !messageId) {
    setMsg(modChannelMsg, "Pick a server, a channel, and a message ID first.", "error");
    return;
  }
  const data = await api("/api/moderation/pin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guild_id: guildId, channel_id: channelId, message_id: messageId, pin: true }),
  });
  setMsg(modChannelMsg, data.ok ? "Pinned." : (data.error || "Couldn't pin that."), data.ok ? "success" : "error");
});

document.getElementById("modUnpinBtn").addEventListener("click", async () => {
  const guildId = modServerSelect.value;
  const channelId = modChannelSelect.value;
  const messageId = modMessageIdInput.value.trim();
  if (!guildId || !channelId || !messageId) {
    setMsg(modChannelMsg, "Pick a server, a channel, and a message ID first.", "error");
    return;
  }
  const data = await api("/api/moderation/pin", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guild_id: guildId, channel_id: channelId, message_id: messageId, pin: false }),
  });
  setMsg(modChannelMsg, data.ok ? "Unpinned." : (data.error || "Couldn't unpin that."), data.ok ? "success" : "error");
});

document.getElementById("modPurgeUserBtn").addEventListener("click", async () => {
  const guildId = modServerSelect.value;
  const channelId = modChannelSelect.value;
  const userId = modPurgeUserIdInput.value.trim();
  if (!guildId || !channelId || !userId) {
    setMsg(modChannelMsg, "Pick a server, a channel, and a user ID first.", "error");
    return;
  }
  const data = await api("/api/moderation/purge_user", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ guild_id: guildId, channel_id: channelId, user_id: userId, count: parseInt(modPurgeCountInput.value, 10) || 10 }),
  });
  setMsg(modChannelMsg, data.ok ? `Deleted ${data.deleted} message(s).` : (data.error || "Couldn't do that."), data.ok ? "success" : "error");
});

// ---------- toasts ----------

const toastStack = document.getElementById("toastStack");

function toast(text, kind) {
  const el = document.createElement("div");
  el.className = "toast" + (kind ? ` is-${kind}` : "");
  el.textContent = text;
  toastStack.appendChild(el);
  setTimeout(() => el.remove(), kind === "error" ? 6000 : 2600);
}

function prefGet(key, fallback) {
  try {
    const v = localStorage.getItem(`cd.${key}`);
    return v === null ? fallback : JSON.parse(v);
  } catch (_) {
    return fallback;
  }
}

function prefSet(key, value) {
  try { localStorage.setItem(`cd.${key}`, JSON.stringify(value)); } catch (_) { /* private mode */ }
}

// ---------- layout tab (channels + categories) ----------
//
// Keeps the server's whole layout as a local model:
//   groups = [{ id: null, channels: [...] }, { id, name, channels: [...] }, ...]
// (uncategorized first, like Discord draws it). Every move is a plain edit
// of that model followed by a re-render; saving sends the whole thing to
// /api/layout, which writes it back in ONE bulk request.

const layoutEditor = (() => {
  const $ = (id) => document.getElementById(id);
  const el = {
    server: $("layoutServerSelect"),
    reload: $("layoutReloadBtn"),
    tree: $("layoutTree"),
    newName: $("layoutNewName"),
    newType: $("layoutNewType"),
    newCat: $("layoutNewCategory"),
    createBtn: $("layoutCreateBtn"),
    filter: $("layoutFilter"),
    autoSave: $("layoutAutoSave"),
    collapseBtn: $("layoutCollapseBtn"),
    actionBar: $("layoutActionBar"),
    selLabel: $("layoutSelLabel"),
    moveTo: $("layoutMoveTo"),
    syncBtn: $("layoutSyncBtn"),
    renameBtn: $("layoutRenameBtn"),
    cloneBtn: $("layoutCloneBtn"),
    deleteBtn: $("layoutDeleteBtn"),
    saveBar: $("layoutSaveBar"),
    saveLabel: $("layoutSaveLabel"),
    sortBtn: $("layoutSortBtn"),
    discardBtn: $("layoutDiscardBtn"),
    saveBtn: $("layoutSaveBtn"),
  };

  const svg = (d, size = 16) => `<svg viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true"><path fill="currentColor" d="${d}"/></svg>`;
  const ICONS = {
    text: svg("M10 3 9 8H5v2h3.6l-.8 4H4v2h3.4l-1 5h2l1-5h4l-1 5h2l1-5H20v-2h-4.2l.8-4H20V8h-3.6l1-5h-2l-1 5h-4l1-5h-2Zm.6 7h4l-.8 4h-4l.8-4Z"),
    voice: svg("M4 9h4l5-4v14l-5-4H4V9Zm12.5 3a4.5 4.5 0 0 0-2-3.7v7.4a4.5 4.5 0 0 0 2-3.7Z"),
    stage: svg("M12 2a3 3 0 0 1 3 3v6a3 3 0 1 1-6 0V5a3 3 0 0 1 3-3Zm-7 9h2a5 5 0 0 0 10 0h2a7 7 0 0 1-6 6.9V21h-2v-3.1A7 7 0 0 1 5 11Z"),
    forum: svg("M3 4h13a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1H8l-4 3v-3H3a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Zm16 5h2a1 1 0 0 1 1 1v8a1 1 0 0 1-1 1h-1v3l-4-3h-6a1 1 0 0 1-1-1v-2h8a2 2 0 0 0 2-2V9Z"),
    news: svg("M3 10v4h3l6 4V6l-6 4H3Zm12-3v10a5 5 0 0 0 0-10Z"),
  };
  const GRIP = svg("M9 5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0Zm0 7a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0Zm0 7a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0Zm9-14a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0Zm0 7a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0Zm0 7a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0Z");
  const CHEVRON = svg("M7 10l5 5 5-5z", 18);
  const VOICE_TYPES = new Set(["voice", "stage"]);

  let guildId = "";
  let groups = [];
  let saved = "";            // order() as of the last successful save/load
  let selected = null;       // { kind: "chan" | "cat", id }
  let collapsed = new Set(prefGet("layoutCollapsed", []));
  let pendingSync = new Set();
  let saveTimer = null;
  let saving = false;
  let saveAgain = false;
  let saveError = "";
  let drag = null;

  el.autoSave.checked = prefGet("layoutAutoSave", true);

  // ----- model -----

  const isVoice = (c) => VOICE_TYPES.has(c.type);
  const order = () => JSON.stringify(groups.map((g) => [g.id, g.channels.map((c) => c.id)]));
  const isDirty = () => order() !== saved;

  function normalize() {
    groups.forEach((g) => {
      g.channels = g.channels.filter((c) => !isVoice(c)).concat(g.channels.filter(isVoice));
    });
  }

  function findChan(id) {
    for (const g of groups) {
      const i = g.channels.findIndex((c) => c.id === id);
      if (i >= 0) return { g, i, c: g.channels[i] };
    }
    return null;
  }

  const findGroup = (id) => groups.find((g) => g.id === id);

  function changeCount() {
    let before;
    try { before = JSON.parse(saved); } catch (_) { return 0; }
    const pos = new Map();
    before.forEach(([gid, ids], gi) => {
      if (gid) pos.set(gid, `c${gi}`);
      ids.forEach((id, i) => pos.set(id, `${gid}:${i}`));
    });
    let n = 0;
    groups.forEach((g, gi) => {
      if (g.id && pos.get(g.id) !== `c${gi}`) n++;
      g.channels.forEach((c, i) => { if (pos.get(c.id) !== `${g.id}:${i}`) n++; });
    });
    return n;
  }

  function reorder(list, index, where) {
    let target = where === "up" ? index - 1 : where === "down" ? index + 1 : where === "top" ? 0 : list.length - 1;
    target = Math.max(0, Math.min(list.length - 1, target));
    if (target === index) return false;
    const [item] = list.splice(index, 1);
    list.splice(target, 0, item);
    return true;
  }

  function moveChan(id, where) {
    const f = findChan(id);
    if (!f) return false;
    const voice = isVoice(f.c);
    const block = f.g.channels.filter((c) => isVoice(c) === voice);
    if (!reorder(block, block.indexOf(f.c), where)) return false;
    const others = f.g.channels.filter((c) => isVoice(c) !== voice);
    f.g.channels = voice ? others.concat(block) : block.concat(others);
    return true;
  }

  function moveCat(id, where) {
    const cats = groups.slice(1);
    const i = cats.findIndex((g) => g.id === id);
    if (i < 0 || !reorder(cats, i, where)) return false;
    groups = [groups[0]].concat(cats);
    return true;
  }

  function setCat(id, catId) {
    const f = findChan(id);
    const target = findGroup(catId);
    if (!f || !target || f.g === target) return false;
    f.g.channels.splice(f.i, 1);
    target.channels.push(f.c);
    collapsed.delete(String(catId));
    normalize();
    return true;
  }

  // Mutations that already happened on Discord (create/delete) shouldn't
  // count as unsaved layout changes.
  function mutateSynced(fn) {
    const wasClean = !isDirty();
    fn();
    if (wasClean) saved = order();
  }

  // ----- loading -----

  async function open() {
    const guilds = await api("/api/guilds");
    const current = el.server.value || prefGet("layoutGuild", "");
    if (!guilds.length) {
      el.server.innerHTML = '<option value="">No servers found (is the bot online + invited?)</option>';
      guildId = "";
      render();
      return;
    }
    el.server.innerHTML =
      '<option value="">Choose a server&hellip;</option>' +
      guilds.map((g) => `<option value="${g.id}">${escapeHtml(g.name)}</option>`).join("");
    if (current && guilds.some((g) => g.id === current)) el.server.value = current;
    else if (guilds.length === 1) el.server.value = guilds[0].id;
    if (el.server.value !== guildId || !groups.length) await load();
    else render();
  }

  async function load() {
    clearTimeout(saveTimer);
    guildId = el.server.value;
    prefSet("layoutGuild", guildId);
    selected = null;
    pendingSync.clear();
    saveError = "";
    if (!guildId) {
      groups = [];
      render();
      return;
    }
    el.tree.innerHTML = '<div class="lt-empty">Loading layout&hellip;</div>';
    let data;
    try {
      data = await api(`/api/layout?guild_id=${encodeURIComponent(guildId)}`);
    } catch (_) {
      data = { groups: [] };
    }
    groups = data.groups || [];
    saved = order();
    render();
  }

  // ----- rendering -----

  function chanHtml(c, q) {
    const sel = selected && selected.kind === "chan" && selected.id === c.id;
    const hidden = q && !c.name.toLowerCase().includes(q);
    return `
      <div class="lt-chan${sel ? " is-selected" : ""}${hidden ? " is-hidden" : ""}" data-chan="${c.id}">
        <span class="lt-handle" title="Drag to move">${GRIP}</span>
        <span class="lt-icon">${ICONS[c.type] || ICONS.text}</span>
        <span class="lt-name">${escapeHtml(c.name)}</span>
        ${c.type !== "text" ? `<span class="lt-type">${c.type}</span>` : ""}
        <span class="lt-mini">
          <button class="lt-icon-btn" data-act="up" title="Move up">&#9650;</button>
          <button class="lt-icon-btn" data-act="down" title="Move down">&#9660;</button>
        </span>
      </div>`;
  }

  function groupHtml(g, q) {
    const loose = g.id === null;
    const sel = !loose && selected && selected.kind === "cat" && selected.id === g.id;
    const isCollapsed = !q && collapsed.has(String(g.id));
    const visible = q ? g.channels.filter((c) => c.name.toLowerCase().includes(q)).length : g.channels.length;
    if (q && loose && !visible) return "";
    const body = g.channels.length
      ? g.channels.map((c) => chanHtml(c, q)).join("")
      : `<div class="lt-group-empty">${loose ? "Drop channels here to take them out of every category." : "Empty &mdash; drop channels here."}</div>`;
    return `
      <div class="lt-group${sel ? " is-selected" : ""}${isCollapsed ? " is-collapsed" : ""}" data-group="${g.id || ""}">
        <div class="lt-group-head">
          ${loose ? '<span class="lt-handle" style="visibility:hidden"></span>' : `<span class="lt-handle" title="Drag to move category">${GRIP}</span>`}
          <span class="lt-chevron" data-act="collapse">${CHEVRON}</span>
          <span class="lt-group-name${loose ? " is-loose" : ""}">${loose ? "No category" : escapeHtml(g.name)}</span>
          <span class="lt-count">${visible}</span>
          ${loose ? "" : `<span class="lt-mini">
            <button class="lt-icon-btn" data-act="up" title="Move category up">&#9650;</button>
            <button class="lt-icon-btn" data-act="down" title="Move category down">&#9660;</button>
          </span>`}
        </div>
        <div class="lt-group-body">${body}</div>
      </div>`;
  }

  function render() {
    if (!guildId) {
      el.tree.innerHTML = '<div class="lt-empty">Pick a server to load its layout.</div>';
    } else if (!groups.length) {
      el.tree.innerHTML = '<div class="lt-empty">Couldn\'t load this server &mdash; is the bot online and still in it?</div>';
    } else {
      const q = el.filter.value.trim().toLowerCase();
      el.tree.innerHTML = groups.map((g) => groupHtml(g, q)).join("");
    }
    renderCreateCats();
    updateBars();
  }

  function renderCreateCats() {
    const current = el.newCat.value;
    el.newCat.innerHTML = '<option value="">No category</option>' +
      groups.slice(1).map((g) => `<option value="${g.id}">${escapeHtml(g.name)}</option>`).join("");
    if (current && findGroup(current)) el.newCat.value = current;
    el.newCat.disabled = el.newType.value === "category";
  }

  function flash(sel) {
    const row = el.tree.querySelector(sel);
    if (!row) return;
    row.classList.add("is-flash");
    row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function updateBars() {
    // action bar — what's selected and what you can do with it
    const chan = selected && selected.kind === "chan" ? findChan(selected.id) : null;
    const cat = selected && selected.kind === "cat" ? findGroup(selected.id) : null;
    if (selected && !chan && !cat) selected = null;
    el.actionBar.hidden = !selected;
    if (chan) {
      el.selLabel.innerHTML = `<strong>${escapeHtml(chan.c.name)}</strong> &middot; ${chan.g.id ? escapeHtml(chan.g.name) : "no category"}`;
      el.moveTo.innerHTML = groups.map((g) =>
        `<option value="${g.id || ""}"${g === chan.g ? " selected" : ""}>${g.id ? "&#8618; " + escapeHtml(g.name) : "&#8618; No category"}</option>`).join("");
      el.moveTo.hidden = false;
      el.syncBtn.hidden = false;
      el.syncBtn.disabled = !chan.g.id;
      el.cloneBtn.hidden = false;
    } else if (cat) {
      el.selLabel.innerHTML = `<strong>${escapeHtml(cat.name)}</strong> &middot; category, ${cat.channels.length} channel${cat.channels.length === 1 ? "" : "s"}`;
      el.moveTo.hidden = true;
      el.syncBtn.hidden = true;
      el.cloneBtn.hidden = true;
    }

    // save bar — status, plus manual save controls when instant save is off
    const dirty = groups.length && isDirty();
    el.saveBar.hidden = !guildId || !groups.length;
    el.saveBar.classList.toggle("is-dirty", !!dirty);
    const manual = !el.autoSave.checked;
    el.discardBtn.hidden = !(manual && dirty);
    el.saveBtn.hidden = !(manual && dirty);
    let dot = "", text = "All changes saved";
    if (saving) { dot = "is-busy"; text = "Saving to Discord&hellip;"; }
    else if (saveError) { dot = "is-error"; text = escapeHtml(saveError); }
    else if (dirty) {
      const n = changeCount();
      dot = "is-pending";
      text = manual ? `${n || 1} unsaved change${n === 1 ? "" : "s"}` : "Saving soon&hellip;";
    }
    el.saveLabel.innerHTML = `<span class="saving-dot ${dot}"></span>${text}`;
  }

  // ----- saving -----

  function changed(flashSel) {
    normalize();
    render();
    if (flashSel) flash(flashSel);
    if (el.autoSave.checked) {
      clearTimeout(saveTimer);
      saveTimer = setTimeout(save, 450);
    }
  }

  async function save() {
    clearTimeout(saveTimer);
    if (saving) { saveAgain = true; return; }
    if (!guildId || (!isDirty() && !pendingSync.size)) { updateBars(); return; }
    saving = true;
    saveError = "";
    updateBars();
    const forGuild = guildId;
    const snapshot = order();
    const sync = [...pendingSync];
    pendingSync.clear();
    let res;
    try {
      res = await api("/api/layout", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          guild_id: forGuild,
          groups: groups.map((g) => ({ id: g.id, channels: g.channels.map((c) => c.id) })),
          sync_ids: sync,
        }),
      });
    } catch (_) {
      res = { ok: false, error: "Couldn't reach the app — is it still running?" };
    }
    saving = false;
    if (forGuild !== guildId) return;  // switched servers mid-save
    if (res.ok) {
      saved = snapshot;
      if (sync.length) toast("Permissions synced with the category.", "success");
    } else {
      saveError = res.error || "Couldn't save the layout.";
      toast(saveError, "error");
    }
    if (saveAgain) {
      saveAgain = false;
      return save();
    }
    updateBars();
  }

  // ----- selection & actions -----

  function select(kind, id) {
    selected = id === null || id === undefined ? null : { kind, id };
    el.tree.querySelectorAll(".is-selected").forEach((n) => n.classList.remove("is-selected"));
    if (selected) {
      const node = kind === "chan" ? el.tree.querySelector(`[data-chan="${id}"]`) : el.tree.querySelector(`[data-group="${id}"]`);
      if (node) node.classList.add("is-selected");
    }
    updateBars();
  }

  function moveSelected(where) {
    if (!selected) return;
    if (selected.kind === "chan") {
      if (moveChan(selected.id, where)) changed(`[data-chan="${selected.id}"]`);
      else toast(where === "up" || where === "top" ? "Already at the top of its block." : "Already at the bottom of its block.");
    } else if (moveCat(selected.id, where)) {
      changed(`[data-group="${selected.id}"] .lt-group-head`);
    }
  }

  function startRename() {
    if (!selected) return;
    const node = selected.kind === "chan"
      ? el.tree.querySelector(`[data-chan="${selected.id}"] .lt-name`)
      : el.tree.querySelector(`[data-group="${selected.id}"] .lt-group-name`);
    const item = selected.kind === "chan" ? findChan(selected.id).c : findGroup(selected.id);
    if (!node || !item) return;
    const input = document.createElement("input");
    input.className = "field-input lt-rename";
    input.value = item.name;
    input.maxLength = 100;
    node.replaceWith(input);
    input.focus();
    input.select();
    let done = false;
    const finish = async (commit) => {
      if (done) return;
      done = true;
      const name = input.value.trim();
      if (!commit || !name || name === item.name) { render(); return; }
      const res = await api("/api/channels/rename", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ guild_id: guildId, channel_id: item.id, name }),
      });
      if (res.ok) {
        // Discord normalizes text channel names (lowercase, dashes)
        item.name = item.type === "text" || item.type === "news" || item.type === "forum"
          ? name.toLowerCase().replace(/\s+/g, "-") : name;
        toast("Renamed.", "success");
      } else {
        toast(res.error || "Couldn't rename that.", "error");
      }
      render();
    };
    input.addEventListener("keydown", (e) => {
      e.stopPropagation();
      if (e.key === "Enter") finish(true);
      if (e.key === "Escape") finish(false);
    });
    input.addEventListener("blur", () => finish(true));
    input.addEventListener("pointerdown", (e) => e.stopPropagation());
    input.addEventListener("click", (e) => e.stopPropagation());
  }

  async function deleteSelected() {
    if (!selected) return;
    const isCat = selected.kind === "cat";
    const item = isCat ? findGroup(selected.id) : findChan(selected.id)?.c;
    if (!item) return;
    const msg = isCat
      ? `Delete category "${item.name}"? Its channels stay, they just become uncategorized.`
      : `Delete #${item.name}? This can't be undone.`;
    if (!confirm(msg)) return;
    const res = await api("/api/channels/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ guild_id: guildId, channel_id: item.id }),
    });
    if (!res.ok) {
      toast(res.error || "Couldn't delete that.", "error");
      return;
    }
    mutateSynced(() => {
      if (isCat) {
        groups[0].channels.push(...item.channels);
        groups = groups.filter((g) => g !== item);
      } else {
        const f = findChan(item.id);
        f.g.channels.splice(f.i, 1);
      }
      normalize();
    });
    selected = null;
    toast(`Deleted ${item.name}.`, "success");
    render();
  }

  async function cloneSelected() {
    if (!selected || selected.kind !== "chan") return;
    const f = findChan(selected.id);
    const res = await api("/api/channels/clone", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ guild_id: guildId, channel_id: f.c.id }),
    });
    if (!res.ok) {
      toast(res.error || "Couldn't clone that.", "error");
      return;
    }
    const copy = { id: res.channel.id, name: res.channel.name, type: f.c.type };
    mutateSynced(() => f.g.channels.push(copy));
    // then tuck the copy right under the original
    const block = f.g.channels.filter((c) => isVoice(c) === isVoice(copy));
    block.splice(block.indexOf(copy), 1);
    block.splice(block.indexOf(f.c) + 1, 0, copy);
    const others = f.g.channels.filter((c) => isVoice(c) !== isVoice(copy));
    f.g.channels = isVoice(copy) ? others.concat(block) : block.concat(others);
    selected = { kind: "chan", id: copy.id };
    toast(`Cloned ${f.c.name}.`, "success");
    changed(`[data-chan="${copy.id}"]`);
  }

  async function create() {
    const name = el.newName.value.trim();
    const type = el.newType.value;
    if (!guildId || !name) {
      toast("Pick a server and type a name first.", "error");
      el.newName.focus();
      return;
    }
    el.createBtn.disabled = true;
    const categoryId = type === "category" ? "" : el.newCat.value;
    const res = await api("/api/channels", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ guild_id: guildId, name, type, category_id: categoryId }),
    });
    el.createBtn.disabled = false;
    if (!res.ok) {
      toast(res.error || "Couldn't create that.", "error");
      return;
    }
    el.newName.value = "";
    mutateSynced(() => {
      if (type === "category") {
        groups.push({ id: res.channel.id, name: res.channel.name, channels: [] });
      } else {
        const g = findGroup(categoryId || null) || groups[0];
        g.channels.push({ id: res.channel.id, name: res.channel.name, type });
        normalize();
      }
    });
    selected = { kind: type === "category" ? "cat" : "chan", id: res.channel.id };
    toast(`Created ${res.channel.name}.`, "success");
    render();
    flash(type === "category" ? `[data-group="${res.channel.id}"] .lt-group-head` : `[data-chan="${res.channel.id}"]`);
  }

  function sortAll() {
    const before = order();
    groups.forEach((g) => g.channels.sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true })));
    normalize();
    if (order() === before) { toast("Already sorted."); return; }
    changed();
    toast("Sorted every category A→Z.");
  }

  // ----- drag & drop (pointer events: works with mouse and touch) -----

  function startDrag(e, handle) {
    if (e.button !== undefined && e.button !== 0) return;
    const chanRow = handle.closest(".lt-chan");
    const groupEl = handle.closest(".lt-group");
    const kind = chanRow ? "chan" : "cat";
    const row = chanRow || groupEl;
    const id = kind === "chan" ? row.dataset.chan : groupEl.dataset.group;
    if (kind === "cat" && !id) return;
    e.preventDefault();
    const item = kind === "chan" ? findChan(id).c : findGroup(id);
    const ghost = document.createElement("div");
    ghost.className = "lt-ghost";
    ghost.innerHTML = kind === "chan" ? `${ICONS[item.type] || ICONS.text} ${escapeHtml(item.name)}` : `&#128193; ${escapeHtml(item.name)}`;
    document.body.appendChild(ghost);
    const drop = document.createElement("div");
    drop.className = "lt-drop" + (kind === "cat" ? " is-group" : "");
    drag = { kind, id, row, ghost, drop, target: null, y: e.clientY, x: e.clientX, raf: 0 };
    row.classList.add("lt-dragging");
    document.body.classList.add("is-dragging");
    select(kind, id);
    positionGhost(e.clientX, e.clientY);
    drag.raf = requestAnimationFrame(autoScroll);
    window.addEventListener("pointermove", onDragMove, { passive: false });
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", cancelDrag);
  }

  function positionGhost(x, y) {
    drag.ghost.style.left = `${x}px`;
    drag.ghost.style.top = `${y}px`;
  }

  function autoScroll() {
    if (!drag) return;
    const edge = 80;
    if (drag.y < edge) window.scrollBy(0, -Math.ceil((edge - drag.y) / 5));
    else if (drag.y > window.innerHeight - edge) window.scrollBy(0, Math.ceil((drag.y - (window.innerHeight - edge)) / 5));
    updateDropTarget();
    drag.raf = requestAnimationFrame(autoScroll);
  }

  function onDragMove(e) {
    if (!drag) return;
    e.preventDefault();
    drag.x = e.clientX;
    drag.y = e.clientY;
    positionGhost(e.clientX, e.clientY);
    updateDropTarget();
  }

  function updateDropTarget() {
    const under = document.elementFromPoint(drag.x, drag.y);
    el.tree.querySelectorAll(".is-drop-target").forEach((n) => n.classList.remove("is-drop-target"));
    if (!under || !el.tree.contains(under)) return;
    const groupEl = under.closest(".lt-group");
    if (!groupEl) return;

    if (drag.kind === "chan") {
      const chanRow = under.closest(".lt-chan");
      const body = groupEl.querySelector(".lt-group-body");
      groupEl.classList.add("is-drop-target");
      if (chanRow && chanRow !== drag.row) {
        const r = chanRow.getBoundingClientRect();
        const after = drag.y > r.top + r.height / 2;
        chanRow.parentNode.insertBefore(drag.drop, after ? chanRow.nextSibling : chanRow);
        drag.target = { group: groupEl.dataset.group || null, ref: chanRow.dataset.chan, after };
      } else if (!chanRow) {
        const inHead = !!under.closest(".lt-group-head");
        if (inHead || groupEl.classList.contains("is-collapsed")) {
          body.insertBefore(drag.drop, body.firstChild);
          drag.target = { group: groupEl.dataset.group || null, ref: null, start: true };
        } else {
          body.appendChild(drag.drop);
          drag.target = { group: groupEl.dataset.group || null, ref: null, start: false };
        }
      }
    } else if (groupEl !== drag.row) {
      if (!groupEl.dataset.group) {
        groupEl.after(drag.drop);
        drag.target = { ref: null };
      } else {
        const r = groupEl.getBoundingClientRect();
        const after = drag.y > r.top + r.height / 2;
        groupEl.parentNode.insertBefore(drag.drop, after ? groupEl.nextSibling : groupEl);
        drag.target = { ref: groupEl.dataset.group, after };
      }
    }
  }

  function stopDrag() {
    cancelAnimationFrame(drag.raf);
    window.removeEventListener("pointermove", onDragMove);
    window.removeEventListener("pointerup", endDrag);
    window.removeEventListener("pointercancel", cancelDrag);
    drag.ghost.remove();
    drag.drop.remove();
    drag.row.classList.remove("lt-dragging");
    document.body.classList.remove("is-dragging");
    const d = drag;
    drag = null;
    return d;
  }

  function cancelDrag() {
    if (!drag) return;
    stopDrag();
    render();
  }

  function endDrag() {
    if (!drag) return;
    const d = stopDrag();
    const t = d.target;
    if (!t) { render(); return; }
    const before = order();

    if (d.kind === "chan") {
      const f = findChan(d.id);
      const g = findGroup(t.group);
      if (!f || !g) { render(); return; }
      f.g.channels.splice(f.i, 1);
      let idx = t.ref ? g.channels.findIndex((c) => c.id === t.ref) : -1;
      if (idx >= 0) idx += t.after ? 1 : 0;
      else idx = t.start ? 0 : g.channels.length;
      g.channels.splice(idx, 0, f.c);
      collapsed.delete(String(g.id));
    } else {
      const cat = findGroup(d.id);
      const rest = groups.filter((g) => g !== cat);
      let idx = t.ref ? rest.findIndex((g) => g.id === t.ref) + (t.after ? 1 : 0) : 1;
      rest.splice(Math.max(1, idx), 0, cat);
      groups = rest;
    }

    normalize();
    if (order() === before) { render(); return; }
    changed(d.kind === "chan" ? `[data-chan="${d.id}"]` : `[data-group="${d.id}"] .lt-group-head`);
  }

  // ----- wiring -----

  el.tree.addEventListener("pointerdown", (e) => {
    const handle = e.target.closest(".lt-handle");
    if (handle && handle.innerHTML) startDrag(e, handle);
  });

  el.tree.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-act]");
    const chanRow = e.target.closest(".lt-chan");
    const groupEl = e.target.closest(".lt-group");
    if (!groupEl) return;
    const groupId = groupEl.dataset.group || null;

    if (btn && btn.dataset.act === "collapse") {
      const key = String(groupId);
      collapsed.has(key) ? collapsed.delete(key) : collapsed.add(key);
      prefSet("layoutCollapsed", [...collapsed]);
      groupEl.classList.toggle("is-collapsed");
      return;
    }
    if (btn && chanRow) {
      select("chan", chanRow.dataset.chan);
      moveSelected(btn.dataset.act);
      return;
    }
    if (btn && groupId) {
      select("cat", groupId);
      moveSelected(btn.dataset.act);
      return;
    }
    if (chanRow) {
      const same = selected && selected.kind === "chan" && selected.id === chanRow.dataset.chan;
      select("chan", same ? null : chanRow.dataset.chan);
    } else if (e.target.closest(".lt-group-head") && groupId) {
      const same = selected && selected.kind === "cat" && selected.id === groupId;
      select("cat", same ? null : groupId);
    }
  });

  el.tree.addEventListener("dblclick", (e) => {
    if (e.target.closest(".lt-name, .lt-group-name") && selected) startRename();
  });

  el.actionBar.querySelectorAll("[data-move]").forEach((b) => b.addEventListener("click", () => moveSelected(b.dataset.move)));
  el.moveTo.addEventListener("change", () => {
    if (!selected || selected.kind !== "chan") return;
    if (setCat(selected.id, el.moveTo.value || null)) changed(`[data-chan="${selected.id}"]`);
  });
  el.syncBtn.addEventListener("click", () => {
    if (!selected || selected.kind !== "chan") return;
    pendingSync.add(selected.id);
    save();
  });
  el.renameBtn.addEventListener("click", startRename);
  el.cloneBtn.addEventListener("click", cloneSelected);
  el.deleteBtn.addEventListener("click", deleteSelected);

  el.server.addEventListener("change", async () => {
    if (isDirty() && !el.autoSave.checked && !confirm("Discard unsaved layout changes?")) {
      el.server.value = guildId;
      return;
    }
    await load();
  });
  el.reload.addEventListener("click", async () => {
    if (isDirty() && !confirm("Reload from Discord and drop unsaved changes?")) return;
    await load();
    toast("Reloaded from Discord.");
  });
  el.createBtn.addEventListener("click", create);
  el.newName.addEventListener("keydown", (e) => { if (e.key === "Enter") create(); });
  el.newType.addEventListener("change", renderCreateCats);
  el.filter.addEventListener("input", render);
  el.autoSave.addEventListener("change", () => {
    prefSet("layoutAutoSave", el.autoSave.checked);
    if (el.autoSave.checked && isDirty()) save();
    else updateBars();
  });
  el.collapseBtn.addEventListener("click", () => {
    const anyOpen = groups.some((g) => !collapsed.has(String(g.id)));
    collapsed = anyOpen ? new Set(groups.map((g) => String(g.id))) : new Set();
    el.collapseBtn.textContent = anyOpen ? "Expand all" : "Collapse all";
    prefSet("layoutCollapsed", [...collapsed]);
    render();
  });
  el.sortBtn.addEventListener("click", sortAll);
  el.discardBtn.addEventListener("click", load);
  el.saveBtn.addEventListener("click", save);

  // keyboard: ↑/↓ select, Alt+↑/↓ move, F2 rename, Delete delete, Esc deselect
  document.addEventListener("keydown", (e) => {
    if (!document.getElementById("tab-layout").classList.contains("is-active")) return;
    if (drag && e.key === "Escape") { cancelDrag(); return; }
    if (e.target.closest && e.target.closest("input, textarea, select")) return;
    if (e.key === "Escape" && selected) { select(null, null); return; }
    if (!groups.length) return;

    if (e.key === "ArrowUp" || e.key === "ArrowDown") {
      e.preventDefault();
      if (e.altKey && selected) { moveSelected(e.key === "ArrowUp" ? "up" : "down"); return; }
      const nodes = [...el.tree.querySelectorAll(".lt-group[data-group]:not([data-group='']) > .lt-group-head, .lt-chan:not(.is-hidden)")]
        .filter((n) => n.offsetParent !== null);
      const cur = nodes.findIndex((n) => n.classList.contains("is-selected") || n.parentNode.classList.contains("is-selected"));
      const next = nodes[Math.max(0, Math.min(nodes.length - 1, cur + (e.key === "ArrowUp" ? -1 : 1)))];
      if (!next) return;
      if (next.classList.contains("lt-chan")) select("chan", next.dataset.chan);
      else select("cat", next.parentNode.dataset.group);
      next.scrollIntoView({ block: "nearest" });
    } else if (e.key === "F2" && selected) {
      e.preventDefault();
      startRename();
    } else if ((e.key === "Delete" || e.key === "Backspace") && selected) {
      e.preventDefault();
      deleteSelected();
    }
  });

  window.addEventListener("beforeunload", (e) => {
    if (guildId && groups.length && isDirty() && !el.autoSave.checked) e.preventDefault();
  });

  return { open };
})();

// ---------- fonts tab ----------

let fontsInitialized = false;

function mathAlphaConverter(upperOffset, lowerOffset, digitOffset, exceptions = {}) {
  return (text) => Array.from(text).map((ch) => {
    if (exceptions[ch]) return exceptions[ch];
    const code = ch.codePointAt(0);
    if (ch >= "A" && ch <= "Z") return String.fromCodePoint(code + upperOffset);
    if (ch >= "a" && ch <= "z") return String.fromCodePoint(code + lowerOffset);
    if (digitOffset !== null && ch >= "0" && ch <= "9") return String.fromCodePoint(code + digitOffset);
    return ch;
  }).join("");
}

function lookupConverter(table) {
  return (text) => Array.from(text).map((ch) => table[ch] || ch).join("");
}

function combiningConverter(mark) {
  return (text) => Array.from(text).map((ch) => (ch === " " ? ch : ch + mark)).join("");
}

const SCRIPT_EXC = { B: "ℬ", E: "ℰ", F: "ℱ", H: "ℋ", I: "ℐ", L: "ℒ", M: "ℳ", R: "ℛ", e: "ℯ", g: "ℊ", o: "ℴ" };
const FRAKTUR_EXC = { C: "ℭ", H: "ℌ", I: "ℑ", R: "ℜ", Z: "ℨ" };
const DOUBLE_EXC = { C: "ℂ", H: "ℍ", N: "ℕ", P: "ℙ", Q: "ℚ", R: "ℝ", Z: "ℤ" };
const ITALIC_EXC = { h: "ℎ" };

const SMALL_CAPS_UPPER = {
  A: "ᴀ", B: "ʙ", C: "ᴄ", D: "ᴅ", E: "ᴇ", F: "ꜰ", G: "ɢ", H: "ʜ", I: "ɪ", J: "ᴊ",
  K: "ᴋ", L: "ʟ", M: "ᴍ", N: "ɴ", O: "ᴏ", P: "ᴘ", Q: "ꞯ", R: "ʀ", S: "ꜱ", T: "ᴛ",
  U: "ᴜ", V: "ᴠ", W: "ᴡ", X: "x", Y: "ʏ", Z: "ᴢ",
};
const SMALL_CAPS = { ...SMALL_CAPS_UPPER };
for (const [k, v] of Object.entries(SMALL_CAPS_UPPER)) SMALL_CAPS[k.toLowerCase()] = v;

const CIRCLED = {};
const SQUARED = {};
const NEG_SQUARED = {};
for (let i = 0; i < 26; i++) {
  const upper = String.fromCharCode(65 + i);
  const lower = String.fromCharCode(97 + i);
  CIRCLED[upper] = String.fromCodePoint(0x24B6 + i);
  CIRCLED[lower] = String.fromCodePoint(0x24D0 + i);
  SQUARED[upper] = String.fromCodePoint(0x1F130 + i);
  SQUARED[lower] = String.fromCodePoint(0x1F130 + i);
  NEG_SQUARED[upper] = String.fromCodePoint(0x1F170 + i);
  NEG_SQUARED[lower] = String.fromCodePoint(0x1F170 + i);
}
for (let i = 1; i <= 9; i++) CIRCLED[String(i)] = String.fromCodePoint(0x2460 + (i - 1));
CIRCLED["0"] = String.fromCodePoint(0x24EA);

const UPSIDE_DOWN = {
  a: "ɐ", b: "q", c: "ɔ", d: "p", e: "ǝ", f: "ɟ", g: "ƃ", h: "ɥ", i: "ᴉ", j: "ɾ",
  k: "ʞ", l: "l", m: "ɯ", n: "u", o: "o", p: "d", q: "b", r: "ɹ", s: "s", t: "ʇ",
  u: "n", v: "ʌ", w: "ʍ", x: "x", y: "ʎ", z: "z",
  A: "∀", B: "B", C: "Ɔ", D: "ᗡ", E: "Ǝ", F: "Ⅎ", G: "⅁", H: "H", I: "I", J: "ſ",
  K: "ʞ", L: "˥", M: "W", N: "N", O: "O", P: "Ԁ", Q: "Ò", R: "ᴚ", S: "S", T: "⊥",
  U: "∩", V: "Λ", W: "M", X: "X", Y: "ʎ", Z: "Z",
  "0": "0", "1": "Ɩ", "2": "ᄅ", "3": "Ɛ", "4": "ㄣ", "5": "5", "6": "9", "7": "ㄥ", "8": "8", "9": "6",
  ".": "˙", ",": "'", "'": ",", "?": "¿", "!": "¡", "(": ")", ")": "(", "[": "]", "]": "[", "_": "‾",
};
function upsideDownConvert(text) {
  return Array.from(text).reverse().map((ch) => UPSIDE_DOWN[ch] || ch).join("");
}

function reversedConvert(text) {
  return Array.from(text).reverse().join("");
}

function wideConvert(text) {
  return Array.from(text).join(" ");
}

const ZALGO_UP = ["̍", "̎", "̄", "̅", "̿", "̑", "̆", "̐", "͒", "͗", "͑", "̇", "̈", "̊", "͂", "̓", "̈́", "͊", "͋", "͌", "̃", "̂", "̌", "͐", "̀", "́", "̋", "̏", "̒"];
const ZALGO_DOWN = ["̖", "̗", "̘", "̙", "̜", "̝", "̞", "̟", "̠", "̤", "̥", "̦", "̩", "̪", "̫", "̬", "̭", "̮", "̯", "̰", "̱", "̲", "̳", "̹", "̺", "̻", "̼", "̣"];
const ZALGO_MID = ["̕", "̛", "̀", "́", "͘", "̡", "̢", "̧", "̨", "̴", "̵", "̶", "͜", "͝", "͞", "͟", "͠", "͢"];

function zalgoConvert(text) {
  return Array.from(text).map((ch) => {
    if (ch === " ") return ch;
    let out = ch;
    for (let i = 0; i < 3; i++) out += ZALGO_UP[Math.floor(Math.random() * ZALGO_UP.length)];
    for (let i = 0; i < 3; i++) out += ZALGO_DOWN[Math.floor(Math.random() * ZALGO_DOWN.length)];
    out += ZALGO_MID[Math.floor(Math.random() * ZALGO_MID.length)];
    return out;
  }).join("");
}

const FONT_STYLES = [
  { id: "bold", label: "Bold", convert: mathAlphaConverter(119743, 119737, 120734) },
  { id: "italic", label: "Italic", convert: mathAlphaConverter(119795, 119789, null, ITALIC_EXC) },
  { id: "bold_italic", label: "Bold Italic", convert: mathAlphaConverter(119847, 119841, null) },
  { id: "script", label: "Script", convert: mathAlphaConverter(119899, 119893, null, SCRIPT_EXC) },
  { id: "bold_script", label: "Bold Script", convert: mathAlphaConverter(119951, 119945, null) },
  { id: "fraktur", label: "Fraktur", convert: mathAlphaConverter(120003, 119997, null, FRAKTUR_EXC) },
  { id: "bold_fraktur", label: "Bold Fraktur", convert: mathAlphaConverter(120107, 120101, null) },
  { id: "double_struck", label: "Double-Struck", convert: mathAlphaConverter(120055, 120049, 120744, DOUBLE_EXC) },
  { id: "sans", label: "Sans-Serif", convert: mathAlphaConverter(120159, 120153, 120754) },
  { id: "sans_bold", label: "Sans-Serif Bold", convert: mathAlphaConverter(120211, 120205, 120764) },
  { id: "sans_italic", label: "Sans-Serif Italic", convert: mathAlphaConverter(120263, 120257, null) },
  { id: "sans_bold_italic", label: "Sans-Serif Bold Italic", convert: mathAlphaConverter(120315, 120309, null) },
  { id: "monospace", label: "Monospace", convert: mathAlphaConverter(120367, 120361, 120774) },
  { id: "fullwidth", label: "Vaporwave (Fullwidth)", convert: mathAlphaConverter(65248, 65248, 65248) },
  { id: "smallcaps", label: "Small Caps", convert: lookupConverter(SMALL_CAPS) },
  { id: "circled", label: "Circled", convert: lookupConverter(CIRCLED) },
  { id: "squared", label: "Squared", convert: lookupConverter(SQUARED) },
  { id: "neg_squared", label: "Squared (Filled)", convert: lookupConverter(NEG_SQUARED) },
  { id: "strikethrough", label: "Strikethrough", convert: combiningConverter("̶") },
  { id: "underline", label: "Underline", convert: combiningConverter("̲") },
  { id: "upside_down", label: "Upside Down", convert: upsideDownConvert },
  { id: "reversed", label: "Reversed", convert: reversedConvert },
  { id: "wide", label: "Wide Spacing", convert: wideConvert },
  { id: "zalgo", label: "Zalgo (Cursed)", convert: zalgoConvert },
];

const INVISIBLE_CHARS = [
  { char: "​", display: "ZWSP", label: "Zero Width Space" },
  { char: "‌", display: "ZWNJ", label: "Zero Width Non-Joiner" },
  { char: "‍", display: "ZWJ", label: "Zero Width Joiner" },
  { char: "⁠", display: "WJ", label: "Word Joiner" },
  { char: "﻿", display: "BOM", label: "Zero Width No-Break Space" },
  { char: "ㅤ", display: "ㅤ", label: "Hangul Filler (renders blank, keeps width)" },
  { char: "⠀", display: "⠀", label: "Braille Pattern Blank" },
  { char: " ", display: "NBSP", label: "No-Break Space" },
  { char: " ", display: "EN", label: "En Space" },
  { char: " ", display: "EM", label: "Em Space" },
];

const ARROW_CHARS = [
  "→", "←", "↑", "↓", "↔", "↕", "↗", "↘", "↙", "↖",
  "⇒", "⇐", "⇑", "⇓", "⇔", "⇕", "⇄", "⇅", "⇆", "⇇",
  "⇈", "⇉", "⇊", "↩", "↪", "↺", "↻", "➜", "➔", "➤",
  "⟶", "⟵", "⟷", "➡", "⬅", "⬆", "⬇", "➳", "➵", "➸",
];

const SYMBOL_CHARS = [
  "★", "☆", "✦", "✧", "✩", "✪", "✫", "✬", "✭", "✮",
  "✯", "✰", "⭐", "🌟", "♦", "♣", "♠", "♥", "❤", "✔",
  "✓", "✗", "✘", "☑", "☒", "※", "‡", "†", "§", "¶",
  "©", "®", "™", "°", "±", "≈", "≠", "≤", "≥", "∞",
  "√", "∑", "∫", "π", "Ω", "µ", "∆", "♪", "♫", "☾",
  "☽", "☼", "☀", "☁", "☂", "☃", "❄", "⚡", "⚠", "☠",
  "☮", "☯", "✈", "⌘", "⚔", "⚑", "⚐", "웃", "유", "ツ",
];

const fontStyleSelect = document.getElementById("fontStyleSelect");
const fontInput = document.getElementById("fontInput");
const fontOutput = document.getElementById("fontOutput");
const fontMsg = document.getElementById("fontMsg");

function applyFontConversion() {
  const style = FONT_STYLES.find((s) => s.id === fontStyleSelect.value) || FONT_STYLES[0];
  fontOutput.value = style.convert(fontInput.value);
}

async function copyToClipboard(text, msgEl) {
  if (!text) {
    if (msgEl) setMsg(msgEl, "Nothing to copy yet.", "error");
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    if (msgEl) setMsg(msgEl, "Copied!", "success");
    return;
  } catch (e) {
    // clipboard API can be unavailable (e.g. a non-HTTPS context) — fall back to a manual copy
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand("copy");
    if (msgEl) setMsg(msgEl, "Copied!", "success");
  } catch (e2) {
    if (msgEl) setMsg(msgEl, "Couldn't copy — select and copy manually.", "error");
  }
  document.body.removeChild(ta);
}

function renderSymbolGrid(containerId, items) {
  const el = document.getElementById(containerId);
  el.innerHTML = items
    .map((item, i) => `<button type="button" class="symbol-chip" data-index="${i}" title="${escapeHtml(item.label || item.char)}">${escapeHtml(item.display || item.char)}</button>`)
    .join("");
  el.querySelectorAll(".symbol-chip").forEach((chip) => {
    const item = items[Number(chip.dataset.index)];
    chip.addEventListener("click", async () => {
      await copyToClipboard(item.char, null);
      chip.classList.add("is-copied");
      setTimeout(() => chip.classList.remove("is-copied"), 800);
    });
  });
}

// ---------- servers tab ----------

const serverList = document.getElementById("serverList");
const serverListEmpty = document.getElementById("serverListEmpty");
const refreshServersBtn = document.getElementById("refreshServersBtn");

async function loadServerList() {
  serverListEmpty.textContent = "Loading…";
  serverListEmpty.style.display = "block";
  serverList.innerHTML = "";

  const guilds = await api("/api/servers");
  if (!guilds.length) {
    serverListEmpty.textContent = "No servers found (is the bot online + invited?)";
    return;
  }
  serverListEmpty.style.display = "none";

  serverList.innerHTML = guilds.map((g) => `
    <div class="mod-list-item">
      ${g.icon_url
        ? `<img src="${g.icon_url}" alt="" width="40" height="40" style="border-radius:50%;flex-shrink:0;">`
        : `<span style="width:40px;height:40px;border-radius:50%;background:var(--surface-2,#2a2a33);display:flex;align-items:center;justify-content:center;flex-shrink:0;font-weight:600;">${escapeHtml(g.name.slice(0, 1).toUpperCase())}</span>`
      }
      <div class="mod-list-info">
        <span class="mod-list-name">${escapeHtml(g.name)}</span>
        <span class="mod-list-sub">${g.member_count ?? "?"} members &middot; ${g.id}</span>
        ${g.invite_url
          ? `<input class="field-input mono" readonly value="${escapeHtml(g.invite_url)}" onclick="this.select()" style="margin-top:6px;">`
          : `<span class="mod-list-sub">No invite available &mdash; the bot has no channel here it can create invites from.</span>`
        }
      </div>
      <div class="mod-list-actions">
        ${g.invite_url ? `<button type="button" class="btn-outline btn-small server-copy-btn" data-invite="${escapeHtml(g.invite_url)}">Copy</button>` : ""}
      </div>
    </div>`).join("");

  serverList.querySelectorAll(".server-copy-btn").forEach((btn) => {
    btn.addEventListener("click", () => copyToClipboard(btn.dataset.invite, null));
  });
}

refreshServersBtn.addEventListener("click", loadServerList);

function initFontsTab() {
  fontsInitialized = true;
  fontStyleSelect.innerHTML = FONT_STYLES.map((s) => `<option value="${s.id}">${s.label}</option>`).join("");
  fontStyleSelect.addEventListener("change", applyFontConversion);
  fontInput.addEventListener("input", applyFontConversion);

  document.getElementById("fontCopyBtn").addEventListener("click", () => copyToClipboard(fontOutput.value, fontMsg));
  document.getElementById("fontClearBtn").addEventListener("click", () => {
    fontInput.value = "";
    fontOutput.value = "";
    setMsg(fontMsg, "", "");
  });

  renderSymbolGrid("invisibleCharGrid", INVISIBLE_CHARS);
  renderSymbolGrid("arrowGrid", ARROW_CHARS.map((c) => ({ char: c })));
  renderSymbolGrid("symbolGrid", SYMBOL_CHARS.map((c) => ({ char: c })));
}

// ---------- init ----------

loadTokenState();
