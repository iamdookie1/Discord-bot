"""
Voice Speaker — standalone Flask app: type text into the web page, it's
read aloud out loud on this device. Same idea as the Discord bot's !tts,
but for whoever's around you instead of a voice channel — no Discord
connection at all, runs completely independently (see the repo root's
setup.sh, which lets you launch this instead of the bot).
"""
import os

from flask import Flask, jsonify, render_template, request

import tts_engine

app = Flask(__name__)

# The one WAV file currently playing (or about to), so a new request can
# clean up the previous one instead of leaking temp files — only one
# utterance plays at a time in this app, so a single tracked path is enough.
_last_audio_path = None


def _cleanup_last():
    global _last_audio_path
    if _last_audio_path and os.path.exists(_last_audio_path):
        try:
            os.remove(_last_audio_path)
        except OSError:
            pass
    _last_audio_path = None


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/voices")
def api_voices():
    return jsonify({
        "ok": True,
        "available": tts_engine._unavailable_reason() is None,
        "unavailable_reason": tts_engine._unavailable_reason(),
        "playback_unavailable_reason": tts_engine._playback_unavailable_reason(),
        "variants": [{"id": v, "label": label} for v, label in tts_engine.VOICE_VARIANTS],
        "languages": [{"id": v, "label": label} for v, label in tts_engine.list_language_voices()],
        "effect_modes": tts_engine.EFFECT_MODES,
        "effect_labels": tts_engine.EFFECT_LABELS,
        "effect_param_specs": tts_engine.EFFECT_PARAM_SPECS,
        "effect_tied_modes": sorted(tts_engine.EFFECT_TIED_MODES),
        "max_chars": tts_engine.MAX_CHARS,
    })


@app.route("/api/speak", methods=["POST"])
def api_speak():
    global _last_audio_path
    data = request.get_json(force=True, silent=True) or {}
    text = data.get("text", "")

    if tts_engine._unavailable_reason():
        return jsonify({"ok": False, "error": tts_engine._unavailable_reason()}), 400
    if not text or not text.strip():
        return jsonify({"ok": False, "error": "Type something first."}), 400
    if len(text) > tts_engine.MAX_CHARS:
        return jsonify({"ok": False, "error": f"Keep it under {tts_engine.MAX_CHARS} characters."}), 400

    playback_reason = tts_engine._playback_unavailable_reason()
    if playback_reason:
        return jsonify({"ok": False, "error": playback_reason}), 400

    path = tts_engine.synthesize(
        text,
        voice=data.get("voice") or "en-us",
        volume=data.get("volume", 100),
        rate=data.get("rate", 175),
        tone=data.get("tone", 5),
        pitch=data.get("pitch", 0),
        effect_mode=data.get("effect_mode", "off"),
        effect_params=data.get("effect_params") or {},
        custom_tied=data.get("custom_tied", True),
    )
    if path is None:
        return jsonify({"ok": False, "error": "Couldn't synthesize that."}), 500

    _cleanup_last()
    if not tts_engine.play(path):
        _last_audio_path = None
        if os.path.exists(path):
            os.remove(path)
        return jsonify({"ok": False, "error": "Couldn't start playback."}), 500

    _last_audio_path = path
    return jsonify({"ok": True})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    tts_engine.stop()
    _cleanup_last()
    return jsonify({"ok": True})


if __name__ == "__main__":
    print("Open http://127.0.0.1:5050 in your browser")
    app.run(host="127.0.0.1", port=5050, debug=False, use_reloader=False)
