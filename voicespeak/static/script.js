const els = {
  unavailableCard: document.getElementById("unavailableCard"),
  unavailableMsg: document.getElementById("unavailableMsg"),
  mainCard: document.getElementById("mainCard"),
  textInput: document.getElementById("textInput"),
  charCount: document.getElementById("charCount"),
  voiceSelect: document.getElementById("voiceSelect"),
  effectSelect: document.getElementById("effectSelect"),
  effectSliders: document.getElementById("effectSliders"),
  volumeSlider: document.getElementById("volumeSlider"),
  volumeValue: document.getElementById("volumeValue"),
  rateSlider: document.getElementById("rateSlider"),
  rateValue: document.getElementById("rateValue"),
  toneSlider: document.getElementById("toneSlider"),
  toneValue: document.getElementById("toneValue"),
  pitchSlider: document.getElementById("pitchSlider"),
  pitchValue: document.getElementById("pitchValue"),
  speakBtn: document.getElementById("speakBtn"),
  stopBtn: document.getElementById("stopBtn"),
  speakMsg: document.getElementById("speakMsg"),
};

let meta = null; // last /api/voices response

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

function setMsg(el, text, kind) {
  el.textContent = text || "";
  el.className = "form-msg" + (kind ? ` ${kind}` : "");
}

async function api(url, opts) {
  const res = await fetch(url, opts);
  return res.json();
}

function wireLiveSlider(slider, valueEl, format) {
  slider.addEventListener("input", () => {
    valueEl.textContent = format(slider.value);
  });
}
wireLiveSlider(els.volumeSlider, els.volumeValue, (v) => `${v}%`);
wireLiveSlider(els.rateSlider, els.rateValue, (v) => `${v} wpm`);
wireLiveSlider(els.toneSlider, els.toneValue, (v) => `${v}/10`);
wireLiveSlider(els.pitchSlider, els.pitchValue, (v) => (Number(v) >= 0 ? `+${v}` : v));

function renderEffectSliders() {
  const mode = els.effectSelect.value;
  const specs = (meta.effect_param_specs && meta.effect_param_specs[mode]) || [];
  if (!specs.length) {
    els.effectSliders.style.display = "none";
    els.effectSliders.innerHTML = "";
    return;
  }
  els.effectSliders.style.display = "flex";
  const tied = (meta.effect_tied_modes || []).includes(mode);
  els.effectSliders.innerHTML = specs.map((spec) => `
    <div class="effect-slider-row" data-param-id="${spec.id}">
      <label class="field-label">
        <span>${escapeHtml(spec.label)}</span>
        <span class="mono effect-slider-value">${spec.default}${spec.unit || ""}</span>
      </label>
      <input type="range" class="field-range effect-slider-input" min="${spec.min}" max="${spec.max}" step="${spec.step}" value="${spec.default}">
    </div>
  `).join("") + (tied ? `
    <label class="checkbox-row">
      <input type="checkbox" id="tiedCheckbox" checked>
      <span>Pitch tied to speed</span>
    </label>
  ` : "");

  els.effectSliders.querySelectorAll(".effect-slider-input").forEach((input) => {
    input.addEventListener("input", () => {
      const row = input.closest(".effect-slider-row");
      const spec = specs.find((s) => s.id === row.dataset.paramId);
      row.querySelector(".effect-slider-value").textContent = `${input.value}${spec.unit || ""}`;
    });
  });
}

function collectEffectParams() {
  const params = {};
  els.effectSliders.querySelectorAll(".effect-slider-row").forEach((row) => {
    params[row.dataset.paramId] = Number(row.querySelector(".effect-slider-input").value);
  });
  const tiedCheckbox = document.getElementById("tiedCheckbox");
  return { params, tied: tiedCheckbox ? tiedCheckbox.checked : true };
}

async function loadMeta() {
  meta = await api("/api/voices");
  if (!meta.available) {
    els.unavailableCard.style.display = "block";
    els.unavailableMsg.textContent = meta.unavailable_reason || "Text-to-speech isn't available.";
    els.mainCard.style.display = "none";
    return;
  }
  if (meta.playback_unavailable_reason) {
    els.unavailableCard.style.display = "block";
    els.unavailableMsg.textContent = meta.playback_unavailable_reason;
  }

  const variantGroup = meta.variants.map((v) => `<option value="${v.id}">${escapeHtml(v.label)}</option>`).join("");
  const languageGroup = meta.languages.map((v) => `<option value="${v.id}">${escapeHtml(v.label)}</option>`).join("");
  els.voiceSelect.innerHTML =
    `<optgroup label="Voices">${variantGroup}</optgroup>` +
    `<optgroup label="Languages">${languageGroup}</optgroup>`;

  els.effectSelect.innerHTML = meta.effect_modes
    .map((m) => `<option value="${m}">${escapeHtml(meta.effect_labels[m] || m)}</option>`)
    .join("");
  renderEffectSliders();
}

els.effectSelect.addEventListener("change", renderEffectSliders);

els.textInput.addEventListener("input", () => {
  els.charCount.textContent = els.textInput.value.length;
});

els.speakBtn.addEventListener("click", async () => {
  const text = els.textInput.value.trim();
  if (!text) {
    setMsg(els.speakMsg, "Type something first.", "error");
    return;
  }
  const { params, tied } = collectEffectParams();
  els.speakBtn.disabled = true;
  setMsg(els.speakMsg, "Speaking...", "");
  const data = await api("/api/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      voice: els.voiceSelect.value,
      volume: Number(els.volumeSlider.value),
      rate: Number(els.rateSlider.value),
      tone: Number(els.toneSlider.value),
      pitch: Number(els.pitchSlider.value),
      effect_mode: els.effectSelect.value,
      effect_params: params,
      custom_tied: tied,
    }),
  });
  els.speakBtn.disabled = false;
  if (data.ok) {
    setMsg(els.speakMsg, "", "");
  } else {
    setMsg(els.speakMsg, data.error || "Couldn't speak that.", "error");
  }
});

els.stopBtn.addEventListener("click", async () => {
  await api("/api/stop", { method: "POST" });
  setMsg(els.speakMsg, "Stopped.", "");
});

loadMeta();
