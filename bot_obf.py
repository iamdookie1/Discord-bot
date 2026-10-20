"""
A dependency-free Lua / Luau source obfuscator.

The whole point of the design is "works on *any* Lua" — Lua 5.1 through 5.4,
and Luau (Roblox). It gets that by never trying to *parse* the input: the
source is treated as opaque bytes, transformed, and wrapped in a small Lua
"loader" that reconstructs the original text at runtime and runs it. Because
nothing inspects the grammar, it can't choke on a syntax it doesn't know.

Two deliberate compatibility choices:

* The decode step uses only arithmetic (`+`, `-`, `%`) — never bitwise
  operators. Bitwise `~`/`&`/`|` don't exist in 5.1 and differ between 5.3
  and Luau, so avoiding them keeps one output valid everywhere.
* The loader runs the recovered source with `(loadstring or load)(...)`,
  covering 5.1 (`loadstring`) and 5.2+/Luau (`load`), and it `return`s the
  value the script returns — so an obfuscated ModuleScript still returns its
  module table.

Layers just re-apply the transform: layer 2 encodes the *text of* the layer-1
loader, and so on. Running the outer loader peels one layer, which runs the
next, down to the original.

This is a reversible code-protection convenience of the kind the Roblox
scripting community uses routinely to keep their own work from being copied
verbatim — it is not a packer meant to hide behaviour from a reader.
"""
import random
import string

# Reserved words we must never emit as a generated identifier.
_LUA_KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for",
    "function", "goto", "if", "in", "local", "nil", "not", "or",
    "repeat", "return", "then", "true", "until", "while",
    # Luau additions / commonly-reserved
    "continue", "export", "type",
}

# Generated names are drawn from look-alike characters so the output reads as
# noise (Il1lO0…) while staying a valid identifier.
_NAME_FIRST = "lIoO_"
_NAME_REST = "lIoO01_"

MAX_SOURCE_BYTES = 100_000
# Discord's default upload ceiling is ~8 MB; stay well under it. Each layer
# multiplies size by roughly 4 (every byte becomes a "\ddd" escape), so this
# also bounds how many layers a given script can take.
MAX_OUTPUT_BYTES = 7_500_000


def _rand_name(rng: random.Random) -> str:
    length = rng.randint(6, 13)
    name = rng.choice(_NAME_FIRST) + "".join(rng.choice(_NAME_REST) for _ in range(length - 1))
    while name in _LUA_KEYWORDS:
        name += rng.choice(string.ascii_lowercase)
    return name


def _strip_comments(src: str) -> str:
    """Remove Lua comments without touching code or string contents.

    A character scanner (not a regex) so that `--` inside a string, and the
    `]]` that closes a long string vs. a long comment, are handled correctly.
    Whitespace between tokens is left alone, so identifiers can never be
    accidentally glued together — this only ever deletes comments.
    """
    out = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        two = src[i:i + 2]
        # Short string literal.
        if c in "\"'":
            quote = c
            out.append(c)
            i += 1
            while i < n:
                out.append(src[i])
                if src[i] == "\\" and i + 1 < n:
                    out.append(src[i + 1])
                    i += 2
                    continue
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        # Long bracket: either a [[ string or a --[[ comment; detect level.
        if c == "[" and i + 1 < n and src[i + 1] in "[=":
            j = i + 1
            eq = 0
            while j < n and src[j] == "=":
                eq += 1
                j += 1
            if j < n and src[j] == "[":
                closer = "]" + "=" * eq + "]"
                end = src.find(closer, j + 1)
                chunk = src[i:(end + len(closer)) if end != -1 else n]
                out.append(chunk)
                i += len(chunk)
                continue
        # Comment.
        if two == "--":
            j = i + 2
            # Long comment --[[ ... ]] (possibly with = levels).
            if j < n and src[j] == "[":
                k = j + 1
                eq = 0
                while k < n and src[k] == "=":
                    eq += 1
                    k += 1
                if k < n and src[k] == "[":
                    closer = "]" + "=" * eq + "]"
                    end = src.find(closer, k + 1)
                    i = (end + len(closer)) if end != -1 else n
                    continue
            # Line comment: drop to end of line, keep the newline.
            end = src.find("\n", i)
            i = n if end == -1 else end
            continue
        out.append(c)
        i += 1
    # Collapse runs of blank lines left behind by stripped comments.
    lines = [ln for ln in "".join(out).splitlines()]
    cleaned, blanks = [], 0
    for ln in lines:
        if ln.strip():
            blanks = 0
            cleaned.append(ln)
        else:
            blanks += 1
            if blanks <= 1:
                cleaned.append("")
    return "\n".join(cleaned)


def _junk_stmt(rng: random.Random) -> str:
    """A side-effect-free local statement, purely to pad the output."""
    name = _rand_name(rng)
    a, b = rng.randint(1, 99999), rng.randint(1, 99999)
    kind = rng.randint(0, 2)
    if kind == 0:
        return f"local {name}=({a}*{b})%{rng.randint(2, 255)}"
    if kind == 1:
        return f'local {name}="{_rand_name(rng)}"'
    return f"local function {name}() return {a}+{b} end"


def _encode_layer(data: bytes, rng: random.Random, *, names: bool, junk: bool) -> str:
    klen = rng.randint(12, 24)
    key = [rng.randint(0, 255) for _ in range(klen)]
    enc = bytes((data[i] + key[i % klen]) % 256 for i in range(len(data)))

    if names:
        nm = {x: _rand_name(rng) for x in ("k", "e", "t", "i", "s", "f", "c")}
    else:
        nm = {"k": "k", "e": "e", "t": "t", "i": "i", "s": "s", "f": "f", "c": "c"}

    key_lua = "{" + ",".join(str(b) for b in key) + "}"
    esc = "".join("\\%03d" % b for b in enc)

    stmts = [
        f"local {nm['k']}={key_lua}",
        f'local {nm["e"]}="{esc}"',
        f"local {nm['t']}={{}}",
        (f"for {nm['i']}=1,#{nm['e']} do "
         f"{nm['t']}[{nm['i']}]=string.char((string.byte({nm['e']},{nm['i']})"
         f"-{nm['k']}[({nm['i']}-1)%{klen}+1])%256) end"),
        f"local {nm['s']}=table.concat({nm['t']})",
        f"local {nm['f']}=loadstring or load",
        f"local {nm['c']}={nm['f']}({nm['s']})",
        f"if {nm['c']} then return {nm['c']}() end",
    ]

    if junk:
        # Insert junk anywhere before the final `return` statement.
        out = []
        for idx, stmt in enumerate(stmts[:-1]):
            out.append(stmt)
            if rng.random() < 0.6:
                out.append(_junk_stmt(rng))
        out.append(stmts[-1])
        stmts = out

    return "\n".join(stmts)


def obfuscate(source: str, *, layers: int = 2, names: bool = True,
              junk: bool = False, minify: bool = False, seed=None) -> str:
    """Return obfuscated Lua text for `source`.

    layers: how many times to wrap (1–4). names: randomise the loader's own
    identifiers. junk: pad with dead code. minify: strip comments/blank lines
    from the source first (reduces size; comment-only, never risks gluing
    tokens). Raises ValueError on empty / oversized input or output.
    """
    if not source or not source.strip():
        raise ValueError("the script is empty.")
    layers = max(1, min(int(layers), 4))

    if minify:
        source = _strip_comments(source)

    data = source.encode("utf-8", "surrogatepass")
    if len(data) > MAX_SOURCE_BYTES:
        raise ValueError(
            f"script is {len(data):,} bytes; the limit is {MAX_SOURCE_BYTES:,}."
        )

    # Each layer ~quadruples size; refuse up front if it clearly won't fit
    # rather than building a huge string first.
    est = len(data) * (4 ** layers)
    if est > MAX_OUTPUT_BYTES:
        raise ValueError(
            f"{layers} layers on a {len(data):,}-byte script would exceed the "
            f"upload limit — use fewer layers or a smaller script."
        )

    rng = random.Random(seed)
    current = data
    for _ in range(layers):
        current = _encode_layer(current, rng, names=names, junk=junk).encode("utf-8")

    result = "-- Obfuscated with Control Deck\n" + current.decode("utf-8")
    if len(result.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ValueError("obfuscated output is too large to upload — use fewer layers.")
    return result
