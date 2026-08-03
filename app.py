import json
import os

from flask import Flask, jsonify, render_template, request

import bot_commands
import bot_rp
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


@app.route("/api/bot/name", methods=["POST"])
def bot_update_name():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "").strip()

    if bot_manager.status != "online":
        return jsonify({"ok": False, "error": "Bot isn't connected yet."}), 400
    if not (2 <= len(name) <= 32):
        return jsonify({"ok": False, "error": "Name must be 2-32 characters."}), 400

    result = bot_manager.update_username(name)
    return jsonify(result), (200 if result.get("ok") else 400)


@app.route("/api/bot/avatar", methods=["POST"])
def bot_update_avatar():
    if bot_manager.status != "online":
        return jsonify({"ok": False, "error": "Bot isn't connected yet."}), 400

    file = request.files.get("avatar")
    if not file or not file.filename:
        return jsonify({"ok": False, "error": "Choose an image first."}), 400

    image_bytes = file.read()
    if not image_bytes:
        return jsonify({"ok": False, "error": "That file looks empty."}), 400
    if len(image_bytes) > 8 * 1024 * 1024:
        return jsonify({"ok": False, "error": "Image too large (max 8MB)."}), 400

    result = bot_manager.update_avatar(image_bytes)
    return jsonify(result), (200 if result.get("ok") else 400)


# ---------------- built-in commands (utility / moderation / music) ----------------

@app.route("/api/commands/builtin")
def commands_builtin():
    return jsonify([
        {
            "name": name,
            "description": spec.description,
            "category": spec.category,
            "required_perm": spec.required_perm,
            "required_perm_label": bot_commands.PERM_LABELS.get(spec.required_perm, spec.required_perm),
            "enabled": bot_commands.is_builtin_enabled(name),
        }
        for name, spec in bot_commands.BUILTIN_COMMANDS.items()
    ])


@app.route("/api/commands/builtin/toggle", methods=["POST"])
def commands_builtin_toggle():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "")
    enabled = bool(data.get("enabled", True))
    if name not in bot_commands.BUILTIN_COMMANDS:
        return jsonify({"ok": False, "error": "Unknown command."}), 404
    bot_commands.set_builtin_enabled(name, enabled)
    return jsonify({"ok": True})


# ---------------- custom (python) commands ----------------

@app.route("/api/commands/custom", methods=["GET"])
def commands_custom_list():
    data = bot_commands.load_custom_commands()
    return jsonify([
        {
            "name": name,
            "description": info.get("description", ""),
            "code": info.get("code", ""),
            "enabled": info.get("enabled", True),
        }
        for name, info in data.items()
    ])


@app.route("/api/commands/custom", methods=["POST"])
def commands_custom_create():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "").strip().lower()
    code = data.get("code", "")
    description = data.get("description", "").strip()

    if not bot_commands.COMMAND_NAME_RE.match(name):
        return jsonify({"ok": False, "error": "Name must be 1-32 characters: lowercase letters, numbers, - or _."}), 400
    if name in bot_commands.BUILTIN_COMMANDS:
        return jsonify({"ok": False, "error": f"!{name} is a built-in command — pick a different name."}), 400
    if bot_rp.has_command(name) and name not in bot_commands.load_custom_commands():
        return jsonify({"ok": False, "error": f"!{name} is already used as an RP command — pick a different name."}), 400
    if not code.strip():
        return jsonify({"ok": False, "error": "Code can't be empty."}), 400

    try:
        bot_commands.validate_custom_code(code)
    except SyntaxError as exc:
        return jsonify({"ok": False, "error": f"Syntax error: {exc}"}), 400

    bot_commands.set_custom_command(name, code, description)
    return jsonify({"ok": True})


@app.route("/api/commands/custom/<name>/toggle", methods=["POST"])
def commands_custom_toggle(name):
    data = request.get_json(force=True, silent=True) or {}
    enabled = bool(data.get("enabled", True))
    ok = bot_commands.set_custom_command_enabled(name.strip().lower(), enabled)
    return jsonify({"ok": ok})


@app.route("/api/commands/custom/<name>", methods=["DELETE"])
def commands_custom_delete(name):
    ok = bot_commands.delete_custom_command(name.strip().lower())
    return jsonify({"ok": ok})


# ---------------- roleplay (rp) commands ----------------

@app.route("/api/rp/commands")
def rp_commands_list():
    return jsonify(bot_rp.list_commands())


@app.route("/api/rp/commands", methods=["POST"])
def rp_commands_create():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("name", "").strip().lower()
    description = data.get("description", "").strip()
    gifs = data.get("gifs") or []

    if not bot_commands.COMMAND_NAME_RE.match(name):
        return jsonify({"ok": False, "error": "Name must be 1-32 characters: lowercase letters, numbers, - or _."}), 400
    if name in bot_commands.BUILTIN_COMMANDS or name in bot_commands.load_custom_commands():
        return jsonify({"ok": False, "error": f"!{name} is already in use — pick a different name."}), 400
    if bot_rp.has_command(name):
        return jsonify({"ok": False, "error": f"!{name} already exists as an RP command."}), 400

    bot_rp.create_custom(name, description, gifs)
    return jsonify({"ok": True})


@app.route("/api/rp/commands/<name>/gifs", methods=["POST"])
def rp_commands_gifs(name):
    data = request.get_json(force=True, silent=True) or {}
    gifs = data.get("gifs") or []
    name = name.strip().lower()
    if not bot_rp.has_command(name):
        return jsonify({"ok": False, "error": "Unknown RP command."}), 404
    bot_rp.set_gifs(name, gifs)
    return jsonify({"ok": True})


@app.route("/api/rp/commands/<name>/toggle", methods=["POST"])
def rp_commands_toggle(name):
    data = request.get_json(force=True, silent=True) or {}
    enabled = bool(data.get("enabled", True))
    name = name.strip().lower()
    if not bot_rp.has_command(name):
        return jsonify({"ok": False, "error": "Unknown RP command."}), 404
    bot_rp.set_enabled(name, enabled)
    return jsonify({"ok": True})


@app.route("/api/rp/commands/<name>", methods=["DELETE"])
def rp_commands_delete(name):
    ok = bot_rp.delete_custom(name.strip().lower())
    return jsonify({"ok": ok})


# ---------------- auto-reconnect on boot if a token is already saved ----------------

def _autostart():
    cfg = load_config()
    token = cfg.get("token", "")
    if token:
        bot_manager.start(token)


_autostart()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
