import json
import os

from flask import Flask, jsonify, render_template, request

from bot_manager import bot_manager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

app = Flask(__name__)


# ---------------- config helpers ----------------

def load_config():
    if not os.path.exists(CONFIG_PATH):
        return {"token": ""}
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"token": ""}


def save_config(data: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(data, f)


def masked_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return token[:4] + "*" * (len(token) - 8) + token[-4:]


# ---------------- page ----------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------- token / connection ----------------

@app.route("/api/token", methods=["GET"])
def get_token():
    cfg = load_config()
    return jsonify({
        "has_token": bool(cfg.get("token")),
        "masked_token": masked_token(cfg.get("token", "")),
    })


@app.route("/api/token", methods=["POST"])
def set_token():
    data = request.get_json(force=True, silent=True) or {}
    token = data.get("token", "").strip()
    if not token:
        return jsonify({"ok": False, "error": "Token can't be empty."}), 400

    cfg = load_config()
    cfg["token"] = token
    save_config(cfg)

    bot_manager.start(token)
    return jsonify({"ok": True, "masked_token": masked_token(token)})


@app.route("/api/status")
def status():
    return jsonify({
        "status": bot_manager.status,
        "user_tag": bot_manager.user_tag,
        "error": bot_manager.error_message,
    })


@app.route("/api/disconnect", methods=["POST"])
def disconnect():
    bot_manager.stop()
    return jsonify({"ok": True})


# ---------------- guilds / channels ----------------

@app.route("/api/guilds")
def guilds():
    return jsonify(bot_manager.get_guilds())


@app.route("/api/channels")
def channels():
    guild_id = request.args.get("guild_id", "")
    if not guild_id:
        return jsonify([])
    return jsonify(bot_manager.get_text_channels(guild_id))


# ---------------- send message ----------------

@app.route("/api/send", methods=["POST"])
def send():
    data = request.get_json(force=True, silent=True) or {}
    guild_id = data.get("guild_id", "")
    channel_id = data.get("channel_id", "")
    message = data.get("message", "").strip()

    if bot_manager.status != "online":
        return jsonify({"ok": False, "error": "Bot isn't connected yet."}), 400
    if not (guild_id and channel_id):
        return jsonify({"ok": False, "error": "Pick a server and a channel first."}), 400
    if not message:
        return jsonify({"ok": False, "error": "Message can't be empty."}), 400

    ok = bot_manager.send_message(guild_id, channel_id, message)
    if not ok:
        return jsonify({"ok": False, "error": "Couldn't send that message. Check the bot's permissions in that channel."}), 500
    return jsonify({"ok": True})


# ---------------- send embed ----------------

@app.route("/api/send_embed", methods=["POST"])
def send_embed():
    data = request.get_json(force=True, silent=True) or {}
    guild_id = data.get("guild_id", "")
    channel_id = data.get("channel_id", "")
    embed = data.get("embed") or {}

    if bot_manager.status != "online":
        return jsonify({"ok": False, "error": "Bot isn't connected yet."}), 400
    if not (guild_id and channel_id):
        return jsonify({"ok": False, "error": "Pick a server and a channel first."}), 400
    if not (embed.get("title") or embed.get("description") or embed.get("fields")):
        return jsonify({"ok": False, "error": "Add at least a title, description, or a field."}), 400

    ok = bot_manager.send_embed(guild_id, channel_id, embed)
    if not ok:
        return jsonify({"ok": False, "error": "Couldn't send that embed. Check the bot's permissions in that channel."}), 500
    return jsonify({"ok": True})


# ---------------- bot profile ----------------

@app.route("/api/bot/profile")
def bot_profile():
    return jsonify(bot_manager.get_bot_profile() or {})


@app.route("/api/bot/refresh", methods=["POST"])
def bot_refresh():
    if bot_manager.status != "online":
        return jsonify({"ok": False, "error": "Bot isn't connected — can't check Discord."}), 400
    profile = bot_manager.refresh_bot_profile()
    if not profile:
        return jsonify({"ok": False, "error": "Couldn't reach Discord. Try again in a moment."}), 502
    return jsonify({"ok": True, "profile": profile})


# ---------------- auto-reconnect on boot if a token is already saved ----------------

def _autostart():
    cfg = load_config()
    token = cfg.get("token", "")
    if token:
        bot_manager.start(token)


_autostart()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
