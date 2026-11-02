"""
A dependency-free Lua / Luau source obfuscator.

The whole point of the design is "works on *any* Lua" — Lua 5.1 through 5.4,
and Luau (Roblox). It gets that by never trying to *parse* the input: the
source is treated as opaque bytes, transformed, and wrapped in a small Lua
"loader" that reconstructs the original text at runtime and runs it. Because
nothing inspects the grammar, it can't choke on a syntax it doesn't know.

How the encoding resists a casual read (static analysis / an LLM glancing
at the blob):

* No key is stored. Each layer seeds a little LCG (linear congruential
  generator) and derives one keystream byte per source byte from its *high*
  bits (the low bits of an LCG have short periods, so we don't use them).
  There's nothing to copy out — you'd have to re-run the generator.
* The keystream never repeats across the payload, and each byte is chained
  to the previous *ciphertext* byte (CBC-style), so identical source bytes
  don't produce identical output and you can't line up repeats.
* The payload string is split into several randomly-sized, randomly-named
  fragments that are concatenated back, and the loader's own identifiers are
  scrambled into look-alike noise. Optional junk interleaves dead code.
* Layers stack with fresh constants each time: layer 2 encodes the *text of*
  the layer-1 loader, so peeling one only reveals another encoded loader.

Two deliberate compatibility choices keep one output valid everywhere:

* Decoding uses only arithmetic (`+`, `-`, `*`, `%`, `math.floor`) — never
  bitwise operators, which don't exist in 5.1 and differ between 5.3 and
  Luau.
* The loader runs the recovered source with `(loadstring or load)(...)`,
  covering 5.1 (`loadstring`) and 5.2+/Luau (`load`), and it `return`s the
  script's value, so an obfuscated ModuleScript still returns its table.

Honest limit: this raises the bar a lot, but it is not unbreakable. The
final step always hands the real source to `loadstring`/`load`, so anyone
who hooks that call on the running machine can still capture it — true of
every pure-Lua obfuscator. Defeating that needs a bytecode VM, not a loader.
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


def _rand_name(rng: random.Random, used: set | None = None) -> str:
    while True:
        length = rng.randint(6, 13)
        name = rng.choice(_NAME_FIRST) + "".join(rng.choice(_NAME_REST) for _ in range(length - 1))
        if name in _LUA_KEYWORDS:
            continue
        if used is not None:
            if name in used:
                continue
            used.add(name)
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


def _junk_stmt(rng: random.Random, used: set) -> str:
    """A side-effect-free local statement, purely to pad the output."""
    name = _rand_name(rng, used)
    a, b = rng.randint(1, 99999), rng.randint(1, 99999)
    kind = rng.randint(0, 2)
    if kind == 0:
        return f"local {name}=({a}*{b})%{rng.randint(2, 255)}"
    if kind == 1:
        return f'local {name}="{_rand_name(rng)}"'
    return f"local function {name}() return {a}+{b} end"


# LCG modulus — a power of two kept under 2^24 so s*A stays well within the
# exact-integer range of a Lua 5.1 double (< 2^53). The keystream is taken
# from the HIGH bits, which run the full period, not the low bits.
_LCG_M = 1 << 24


def _lcg_params(rng: random.Random):
    """Full-period LCG constants (Hull-Dobell): A % 4 == 1, C odd."""
    a = rng.randrange(1, _LCG_M // 4) * 4 + 1
    c = rng.randrange(0, _LCG_M // 2) * 2 + 1
    seed = rng.randrange(0, _LCG_M)
    iv = rng.randrange(0, 256)
    return a, c, seed, iv


def _encode_bytes(data: bytes, a: int, c: int, seed: int, iv: int) -> bytes:
    """Mirror of the loader's decode loop, in reverse. Keystream byte comes
    from bits 16-23 of the LCG state; each cipher byte is chained to the one
    before it."""
    out = bytearray()
    s = seed
    prev = iv
    for p in data:
        s = (s * a + c) % _LCG_M
        k = (s // 65536) % 256
        ct = (p + k + prev) % 256
        out.append(ct)
        prev = ct
    return bytes(out)


def _fragment(esc: str, n_esc: int, rng: random.Random, used: set):
    """Split the escaped payload into 2-5 pieces on escape boundaries (each
    escape is exactly 4 chars, so we never cut one in half), each a separate
    randomly-named local. Returns (declarations, concat_expression)."""
    if n_esc < 8:
        name = _rand_name(rng, used)
        return [f'local {name}="{esc}"'], name
    parts = rng.randint(2, 5)
    cuts = sorted(rng.sample(range(1, n_esc), parts - 1))
    bounds = [0] + cuts + [n_esc]
    decls, names = [], []
    for lo, hi in zip(bounds, bounds[1:]):
        name = _rand_name(rng, used)
        decls.append(f'local {name}="{esc[lo * 4:hi * 4]}"')
        names.append(name)
    return decls, "..".join(names)


def _encode_layer(data: bytes, rng: random.Random, *, names: bool, junk: bool) -> str:
    a, c, seed, iv = _lcg_params(rng)
    enc = _encode_bytes(data, a, c, seed, iv)
    esc = "".join("\\%03d" % b for b in enc)

    used: set = set()
    if names:
        nm = {x: _rand_name(rng, used) for x in ("e", "s", "p", "o", "i", "k", "b", "src", "f", "run")}
    else:
        nm = {x: x for x in ("e", "s", "p", "o", "i", "k", "b", "src", "f", "run")}
        used.update(nm.values())

    frag_decls, concat = _fragment(esc, len(enc), rng, used)

    stmts = list(frag_decls)
    stmts += [
        f"local {nm['e']}={concat}",
        f"local {nm['s']}={seed}",
        f"local {nm['p']}={iv}",
        f"local {nm['o']}={{}}",
        # decode loop: advance the LCG, pull a keystream byte from its high
        # bits, undo the chain, feed the ciphertext byte forward as the next
        # chain value.
        (f"for {nm['i']}=1,#{nm['e']} do "
         f"{nm['s']}=({nm['s']}*{a}+{c})%{_LCG_M} "
         f"local {nm['k']}=math.floor({nm['s']}/65536)%256 "
         f"local {nm['b']}=string.byte({nm['e']},{nm['i']}) "
         f"{nm['o']}[{nm['i']}]=string.char(({nm['b']}-{nm['k']}-{nm['p']})%256) "
         f"{nm['p']}={nm['b']} end"),
        f"local {nm['src']}=table.concat({nm['o']})",
        f"local {nm['f']}=loadstring or load",
        f"local {nm['run']}={nm['f']}({nm['src']})",
        f"if {nm['run']} then return {nm['run']}() end",
    ]

    if junk:
        # Interleave junk anywhere before the final `return`. (The frag decls
        # and the real statements still run in order; junk is independent.)
        out = []
        for stmt in stmts[:-1]:
            out.append(stmt)
            if rng.random() < 0.5:
                out.append(_junk_stmt(rng, used))
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

    result = current.decode("utf-8")
    if len(result.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise ValueError("obfuscated output is too large to upload — use fewer layers.")
    return result
