"""VM obfuscation mode for Lua / Luau.

Unlike bot_obf.py (which encrypts the source text and hands it to
`loadstring`/`load` to run), this compiles the script into a custom opcode
tree and ships a tiny interpreter that *walks* that tree. The plaintext
source is never reconstructed at runtime and there is no `loadstring` call
to hook — which is the whole point.

The trade-off is coverage: a from-scratch VM can only run what it
implements. So this parser REFUSES (raises Unsupported) on anything outside
its subset instead of guessing — it will never silently mis-run a script.

Supported: locals & globals, multiple assignment, all the usual operators
(arithmetic, comparison, concat, and/or/not, length, unary minus), tables
(constructors, `t[k]`, `t.k`), functions / closures / recursion, method
definitions and calls (`t:m()`), numeric and generic `for`, `while`, `if`,
`do`, `break`, `return` (including multiple values). Globals and library
calls (print, pairs, string.*, Roblox services, metatables via
setmetatable, …) pass straight through to the host, so they behave exactly
as normal.

NOT supported (refused): varargs `...`, `repeat ... until`, `goto`/labels,
bitwise operators, long strings/comments `[[ ]]`, numeric escapes you'd
only hit in exotic code. If a script uses any of these, VM mode declines
and the caller falls back to the encoder.
"""
import bot_obf

__all__ = ["Unsupported", "compile_to_vm", "can_compile"]


class Unsupported(ValueError):
    """Raised for valid Lua that this VM subset doesn't implement."""


# ---- opcodes (MUST match the numbers in the Lua runtime below) ----
E_NIL, E_TRUE, E_FALSE, E_NUM, E_STR = 1, 2, 3, 4, 5
E_NAME, E_INDEX, E_CALL, E_METHOD, E_FUNC = 6, 7, 8, 9, 10
E_TABLE, E_BIN, E_UN, E_AND, E_OR = 11, 12, 13, 14, 15
E_PAREN = 16  # (expr) — truncates multiple results to one

S_LOCAL, S_ASSIGN, S_CALL, S_IF, S_WHILE = 20, 21, 22, 23, 24
S_NUMFOR, S_GENFOR, S_RETURN, S_BREAK, S_DO = 25, 26, 27, 28, 29

# binary operator codes
B = {"+": 1, "-": 2, "*": 3, "/": 4, "%": 5, "^": 6, "..": 7,
     "==": 8, "~=": 9, "<": 10, "<=": 11, ">": 12, ">=": 13, "//": 14}
# unary operator codes
U = {"-": 1, "not": 2, "#": 3}

_KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "goto", "if", "in", "local", "nil", "not", "or", "repeat", "return",
    "then", "true", "until", "while",
}


# ==================== lexer ====================

class _Tok:
    __slots__ = ("kind", "value", "pos")

    def __init__(self, kind, value, pos):
        self.kind = kind
        self.value = value
        self.pos = pos


def _lex(src):
    toks = []
    i, n = 0, len(src)
    line = 1
    while i < n:
        c = src[i]
        if c in " \t\r":
            i += 1
            continue
        if c == "\n":
            line += 1
            i += 1
            continue
        # comments
        if src[i:i + 2] == "--":
            if src[i:i + 4] == "--[[" or src[i:i + 4] == "--[=":
                raise Unsupported("long comments ([[ ]]) aren't supported in VM mode")
            j = src.find("\n", i)
            i = n if j == -1 else j
            continue
        # long strings
        if c == "[" and i + 1 < n and src[i + 1] in "[=":
            raise Unsupported("long strings ([[ ]]) aren't supported in VM mode")
        # strings
        if c in "\"'":
            s, i = _lex_string(src, i)
            toks.append(_Tok("str", s, line))
            continue
        # numbers
        if c.isdigit() or (c == "." and i + 1 < n and src[i + 1].isdigit()):
            num, i = _lex_number(src, i)
            toks.append(_Tok("num", num, line))
            continue
        # names / keywords
        if c.isalpha() or c == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            word = src[i:j]
            toks.append(_Tok("kw" if word in _KEYWORDS else "name", word, line))
            i = j
            continue
        # operators / punctuation (longest first)
        if src[i:i + 2] == "..":
            if src[i:i + 3] == "...":
                raise Unsupported("varargs (...) aren't supported in VM mode")
            toks.append(_Tok("op", "..", line))
            i += 2
            continue
        three = src[i:i + 2]
        if three in ("==", "~=", "<=", ">=", "//"):
            toks.append(_Tok("op", three, line))
            i += 2
            continue
        if c == ":" and src[i:i + 2] == "::":
            raise Unsupported("goto labels aren't supported in VM mode")
        if c in "&|~":
            raise Unsupported("bitwise operators aren't supported in VM mode")
        if c in "+-*/%^#<>=(){}[];:,.":
            toks.append(_Tok("op", c, line))
            i += 1
            continue
        raise Unsupported(f"unexpected character {c!r}")
    toks.append(_Tok("eof", None, line))
    return toks


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b",
            "f": "\f", "v": "\v", "\\": "\\", '"': '"', "'": "'", "\n": "\n"}


def _lex_string(src, i):
    quote = src[i]
    i += 1
    out = []
    n = len(src)
    while i < n:
        c = src[i]
        if c == quote:
            return "".join(out), i + 1
        if c == "\n":
            raise Unsupported("unterminated string")
        if c == "\\":
            i += 1
            if i >= n:
                break
            e = src[i]
            if e in _ESCAPES:
                out.append(_ESCAPES[e])
                i += 1
            elif e.isdigit():
                j = i
                while j < n and j < i + 3 and src[j].isdigit():
                    j += 1
                out.append(chr(int(src[i:j]) % 256))
                i = j
            elif e == "x":
                out.append(chr(int(src[i + 1:i + 3], 16)))
                i += 3
            elif e == "z":
                i += 1
                while i < n and src[i] in " \t\r\n":
                    i += 1
            else:
                raise Unsupported(f"string escape \\{e} isn't supported in VM mode")
            continue
        out.append(c)
        i += 1
    raise Unsupported("unterminated string")


def _lex_number(src, i):
    n = len(src)
    j = i
    if src[j:j + 2] in ("0x", "0X"):
        j += 2
        while j < n and (src[j] in "0123456789abcdefABCDEF."):
            j += 1
        return float.fromhex(src[i:j]) if "." in src[i:j] else int(src[i:j], 16), j
    while j < n and (src[j].isdigit() or src[j] in ".eE" or
                     (src[j] in "+-" and src[j - 1] in "eE")):
        j += 1
    text = src[i:j]
    return (float(text) if any(ch in text for ch in ".eE") else int(text)), j


# ==================== parser (recursive descent) ====================

class _Parser:
    def __init__(self, toks):
        self.toks = toks
        self.i = 0

    def _peek(self):
        return self.toks[self.i]

    def _next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def _check(self, kind, value=None):
        t = self._peek()
        return t.kind == kind and (value is None or t.value == value)

    def _accept(self, kind, value=None):
        if self._check(kind, value):
            return self._next()
        return None

    def _expect(self, kind, value=None):
        if not self._check(kind, value):
            t = self._peek()
            raise Unsupported(f"expected {value or kind} near {t.value!r} (line {t.pos})")
        return self._next()

    # ---- blocks & statements ----

    def parse_chunk(self):
        block = self._block()
        self._expect("eof")
        return block

    _BLOCK_END = {"end", "else", "elseif", "until"}

    def _at_block_end(self):
        t = self._peek()
        return t.kind == "eof" or (t.kind == "kw" and t.value in self._BLOCK_END)

    def _block(self):
        stmts = []
        while not self._at_block_end():
            if self._check("kw", "return"):
                stmts.append(self._return())
                break
            s = self._statement()
            if s is not None:
                stmts.append(s)
        return stmts

    def _return(self):
        self._next()  # 'return'
        exprs = []
        if not self._at_block_end() and not self._check("op", ";"):
            exprs = self._exprlist()
        self._accept("op", ";")
        return [S_RETURN, exprs]

    def _statement(self):
        t = self._peek()
        if self._accept("op", ";"):
            return None
        if t.kind == "kw":
            v = t.value
            if v == "local":
                return self._local()
            if v == "if":
                return self._if()
            if v == "while":
                return self._while()
            if v == "for":
                return self._for()
            if v == "do":
                self._next()
                body = self._block()
                self._expect("kw", "end")
                return [S_DO, body]
            if v == "function":
                return self._function_stat()
            if v == "break":
                self._next()
                return [S_BREAK]
            if v in ("repeat", "goto"):
                raise Unsupported(f"'{v}' isn't supported in VM mode")
        # expression statement: assignment or call
        return self._expr_statement()

    def _local(self):
        self._next()  # 'local'
        if self._accept("kw", "function"):
            name = self._expect("name").value
            func = self._funcbody(is_method=False)
            # local function f: declare then assign so the body can recurse.
            return [S_LOCAL, [name], [func], True]  # 4th flag: recursive local func
        names = [self._expect("name").value]
        while self._accept("op", ","):
            names.append(self._expect("name").value)
        exprs = []
        if self._accept("op", "="):
            exprs = self._exprlist()
        return [S_LOCAL, names, exprs]

    def _if(self):
        self._next()  # 'if'
        clauses = []
        cond = self._expr()
        self._expect("kw", "then")
        clauses.append([cond, self._block()])
        while self._accept("kw", "elseif"):
            c = self._expr()
            self._expect("kw", "then")
            clauses.append([c, self._block()])
        else_block = []
        if self._accept("kw", "else"):
            else_block = self._block()
        self._expect("kw", "end")
        return [S_IF, clauses, else_block]

    def _while(self):
        self._next()
        cond = self._expr()
        self._expect("kw", "do")
        body = self._block()
        self._expect("kw", "end")
        return [S_WHILE, cond, body]

    def _for(self):
        self._next()  # 'for'
        first = self._expect("name").value
        if self._accept("op", "="):
            e1 = self._expr()
            self._expect("op", ",")
            e2 = self._expr()
            e3 = [E_NIL]
            if self._accept("op", ","):
                e3 = self._expr()
            self._expect("kw", "do")
            body = self._block()
            self._expect("kw", "end")
            return [S_NUMFOR, first, e1, e2, e3, body]
        names = [first]
        while self._accept("op", ","):
            names.append(self._expect("name").value)
        self._expect("kw", "in")
        exprs = self._exprlist()
        self._expect("kw", "do")
        body = self._block()
        self._expect("kw", "end")
        return [S_GENFOR, names, exprs, body]

    def _function_stat(self):
        self._next()  # 'function'
        # funcname: Name {'.' Name} [':' Name]
        target = [E_NAME, self._expect("name").value]
        is_method = False
        while self._accept("op", "."):
            target = [E_INDEX, target, [E_STR, self._expect("name").value]]
        if self._accept("op", ":"):
            target = [E_INDEX, target, [E_STR, self._expect("name").value]]
            is_method = True
        func = self._funcbody(is_method=is_method)
        return [S_ASSIGN, [target], [func]]

    def _funcbody(self, is_method):
        self._expect("op", "(")
        params = ["self"] if is_method else []
        if not self._check("op", ")"):
            while True:
                if self._check("op", "."):  # '...'
                    raise Unsupported("varargs (...) aren't supported in VM mode")
                params.append(self._expect("name").value)
                if not self._accept("op", ","):
                    break
        self._expect("op", ")")
        body = self._block()
        self._expect("kw", "end")
        return [E_FUNC, params, body]

    def _expr_statement(self):
        first = self._suffixed_expr()
        if self._check("op", "=") or self._check("op", ","):
            targets = [first]
            while self._accept("op", ","):
                targets.append(self._suffixed_expr())
            self._expect("op", "=")
            for tg in targets:
                if tg[0] not in (E_NAME, E_INDEX):
                    raise Unsupported("can only assign to a variable or a table field")
            exprs = self._exprlist()
            return [S_ASSIGN, targets, exprs]
        if first[0] not in (E_CALL, E_METHOD):
            raise Unsupported("a statement must be a call or an assignment")
        return [S_CALL, first]

    # ---- expressions (precedence climbing) ----

    # (left binding power, right binding power) — right<left means right-assoc
    _BINPRI = {
        "or": (1, 1), "and": (2, 2),
        "<": (3, 3), ">": (3, 3), "<=": (3, 3), ">=": (3, 3), "~=": (3, 3), "==": (3, 3),
        "..": (5, 4),  # right associative
        "+": (6, 6), "-": (6, 6),
        "*": (7, 7), "/": (7, 7), "//": (7, 7), "%": (7, 7),
        "^": (10, 9),  # right associative, binds tighter than unary
    }
    _UNARY_PRI = 8

    def _exprlist(self):
        exprs = [self._expr()]
        while self._accept("op", ","):
            exprs.append(self._expr())
        return exprs

    def _expr(self, limit=0):
        t = self._peek()
        if (t.kind == "op" and t.value in ("-", "#")) or (t.kind == "kw" and t.value == "not"):
            self._next()
            operand = self._expr(self._UNARY_PRI)
            left = [E_UN, U[t.value], operand]
        else:
            left = self._simple_expr()
        while True:
            t = self._peek()
            op = t.value if (t.kind == "op" or (t.kind == "kw" and t.value in ("and", "or"))) else None
            if op is None or op not in self._BINPRI:
                break
            lp, rp = self._BINPRI[op]
            if lp <= limit:
                break
            self._next()
            right = self._expr(rp)
            if op == "and":
                left = [E_AND, left, right]
            elif op == "or":
                left = [E_OR, left, right]
            else:
                left = [E_BIN, B[op], left, right]
        return left

    def _simple_expr(self):
        t = self._peek()
        if t.kind == "num":
            self._next()
            return [E_NUM, t.value]
        if t.kind == "str":
            self._next()
            return [E_STR, t.value]
        if t.kind == "kw":
            if t.value == "nil":
                self._next()
                return [E_NIL]
            if t.value == "true":
                self._next()
                return [E_TRUE]
            if t.value == "false":
                self._next()
                return [E_FALSE]
            if t.value == "function":
                self._next()
                return self._funcbody(is_method=False)
        if self._check("op", "{"):
            return self._table()
        return self._suffixed_expr()

    def _primary_expr(self):
        if self._accept("op", "("):
            e = self._expr()
            self._expect("op", ")")
            # Parentheses truncate multiple results to one (Lua semantics).
            return [E_PAREN, e]
        t = self._peek()
        if t.kind == "name":
            self._next()
            return [E_NAME, t.value]
        raise Unsupported(f"unexpected {t.value!r} (line {t.pos})")

    def _suffixed_expr(self):
        e = self._primary_expr()
        while True:
            if self._accept("op", "."):
                e = [E_INDEX, e, [E_STR, self._expect("name").value]]
            elif self._accept("op", "["):
                k = self._expr()
                self._expect("op", "]")
                e = [E_INDEX, e, k]
            elif self._accept("op", ":"):
                method = self._expect("name").value
                args = self._call_args()
                e = [E_METHOD, e, method, args]
            elif self._check("op", "(") or self._check("op", "{") or self._check("str"):
                e = [E_CALL, e, self._call_args()]
            else:
                break
        return e

    def _call_args(self):
        if self._check("str"):
            t = self._next()
            return [[E_STR, t.value]]
        if self._check("op", "{"):
            return [self._table()]
        self._expect("op", "(")
        args = []
        if not self._check("op", ")"):
            args = self._exprlist()
        self._expect("op", ")")
        return args

    def _table(self):
        self._expect("op", "{")
        entries = []
        while not self._check("op", "}"):
            if self._accept("op", "["):
                k = self._expr()
                self._expect("op", "]")
                self._expect("op", "=")
                entries.append([2, k, self._expr()])
            elif self._check("name") and self.toks[self.i + 1].kind == "op" and self.toks[self.i + 1].value == "=":
                name = self._next().value
                self._next()  # '='
                entries.append([3, name, self._expr()])
            else:
                entries.append([1, self._expr()])
            if not (self._accept("op", ",") or self._accept("op", ";")):
                break
        self._expect("op", "}")
        return [E_TABLE, entries]


# ==================== serialization (AST -> bytes) ====================

def _len4(n):
    return bytes((n & 255, (n >> 8) & 255, (n >> 16) & 255, (n >> 24) & 255))


def _serialize(node, out):
    if node is True:
        out.append(3)
    elif node is False:
        out.append(2)
    elif isinstance(node, str):
        b = node.encode("utf-8", "surrogatepass")
        out.append(4)
        out += _len4(len(b))
        out += b
    elif isinstance(node, bool):
        out.append(3 if node else 2)
    elif isinstance(node, int) and not isinstance(node, bool):
        b = str(node).encode()
        out.append(5)
        out += _len4(len(b))
        out += b
    elif isinstance(node, float):
        b = repr(node).encode()
        out.append(5)
        out += _len4(len(b))
        out += b
    elif isinstance(node, list):
        out.append(6)
        out += _len4(len(node))
        for x in node:
            _serialize(x, out)
    else:
        raise Unsupported(f"can't serialize {type(node).__name__}")


# ==================== free-name collection (for the globals table) ====================

def _collect_globals(node, names):
    """Record every name used as a variable reference anywhere in the AST.
    It over-captures (local names end up listed too), which is harmless: the
    globals table is only a fallback, consulted after local scopes. The one
    thing that matters is never MISSING a real global, and walking every
    E_NAME guarantees that."""
    if not isinstance(node, list):
        return
    if len(node) >= 2 and node[0] == E_NAME and isinstance(node[1], str):
        names.add(node[1])
        return
    for item in node:
        _collect_globals(item, names)


# ==================== Lua runtime (walks the opcode tree) ====================

_LUA_RUNTIME = r"""
local unpk = table.unpack or unpack
local function pk(...) return {n=select('#',...), ...} end

-- deserialize PAYLOAD (byte string) into nested tables
local DP = 1
local function rb() local b = string.byte(PAYLOAD, DP); DP = DP + 1; return b end
local function rlen() local a=rb() local b=rb() local c=rb() local d=rb() return a+b*256+c*65536+d*16777216 end
local function rd()
  local t = rb()
  if t == 2 then return false
  elseif t == 3 then return true
  elseif t == 4 then local n=rlen() local s=string.sub(PAYLOAD,DP,DP+n-1) DP=DP+n return s
  elseif t == 5 then local n=rlen() local s=string.sub(PAYLOAD,DP,DP+n-1) DP=DP+n return tonumber(s)
  elseif t == 6 then local n=rlen() local a={} a.n=n for i=1,n do a[i]=rd() end return a end
end
local PROG = rd()

local function newscope(p) return {v={}, d={}, up=p} end
local function getvar(sc, name)
  local s = sc
  while s do if s.d[name] then return s.v[name] end s = s.up end
  return GENV[name]
end
local function setvar(sc, name, val)
  local s = sc
  while s do if s.d[name] then s.v[name] = val return end s = s.up end
  GENV[name] = val
end
local function declare(sc, name, val) sc.d[name] = true sc.v[name] = val end

local eval, evalmulti, evallist, exec, makefunc

local function binop(op, a, b)
  if op==1 then return a+b elseif op==2 then return a-b elseif op==3 then return a*b
  elseif op==4 then return a/b elseif op==5 then return a%b elseif op==6 then return a^b
  elseif op==7 then return a..b elseif op==8 then return a==b elseif op==9 then return a~=b
  elseif op==10 then return a<b elseif op==11 then return a<=b elseif op==12 then return a>b
  elseif op==13 then return a>=b elseif op==14 then return math.floor(a/b) end
end

function makefunc(node, defsc)
  local params, body = node[2], node[3]
  return function(...)
    local a = pk(...)
    local s = newscope(defsc)
    for i=1,params.n do declare(s, params[i], a[i]) end
    local sig = exec(body, s)
    if sig and sig.k == 1 then return unpk(sig.vals, 1, sig.vals.n) end
  end
end

function evalmulti(node, sc)
  local op = node[1]
  if op == 8 then
    local fn = eval(node[2], sc)
    local a = evallist(node[3], sc)
    return pk(fn(unpk(a, 1, a.n)))
  elseif op == 9 then
    local obj = eval(node[2], sc)
    local fn = obj[node[3]]
    local a = evallist(node[4], sc)
    local full = {obj} for i=1,a.n do full[i+1]=a[i] end
    return pk(fn(unpk(full, 1, a.n+1)))
  else
    return {n=1, eval(node, sc)}
  end
end

function evallist(list, sc)
  local out = {n=0}
  local cnt = list.n
  for i=1,cnt do
    if i == cnt then
      local m = evalmulti(list[i], sc)
      for j=1,m.n do out.n=out.n+1 out[out.n]=m[j] end
    else
      out.n=out.n+1 out[out.n]=eval(list[i], sc)
    end
  end
  return out
end

function eval(node, sc)
  local op = node[1]
  if op == 1 then return nil
  elseif op == 2 then return true
  elseif op == 3 then return false
  elseif op == 4 then return node[2]
  elseif op == 5 then return node[2]
  elseif op == 6 then return getvar(sc, node[2])
  elseif op == 16 then return eval(node[2], sc)
  elseif op == 7 then return eval(node[2], sc)[eval(node[3], sc)]
  elseif op == 8 or op == 9 then local m = evalmulti(node, sc) return m[1]
  elseif op == 10 then return makefunc(node, sc)
  elseif op == 11 then
    local t = {}
    local entries = node[2]
    local arrn = 0
    for i=1,entries.n do
      local e = entries[i]
      if e[1] == 1 then
        if i == entries.n then
          local m = evalmulti(e[2], sc)
          for j=1,m.n do arrn=arrn+1 t[arrn]=m[j] end
        else
          arrn=arrn+1 t[arrn]=eval(e[2], sc)
        end
      elseif e[1] == 2 then
        t[eval(e[2], sc)] = eval(e[3], sc)
      else
        t[e[2]] = eval(e[3], sc)
      end
    end
    return t
  elseif op == 12 then return binop(node[2], eval(node[3], sc), eval(node[4], sc))
  elseif op == 13 then
    local v = eval(node[3], sc)
    if node[2] == 1 then return -v elseif node[2] == 2 then return not v else return #v end
  elseif op == 14 then
    local l = eval(node[2], sc) if not l then return l else return eval(node[3], sc) end
  elseif op == 15 then
    local l = eval(node[2], sc) if l then return l else return eval(node[3], sc) end
  end
end

local function assign(target, val, sc)
  if target[1] == 6 then setvar(sc, target[2], val)
  else eval(target[2], sc)[eval(target[3], sc)] = val end
end

function exec(block, sc)
  for i=1,block.n do
    local node = block[i]
    local op = node[1]
    if op == 20 then
      if node[4] then
        declare(sc, node[2][1], nil)
        declare(sc, node[2][1], eval(node[3][1], sc))
      else
        local vals = evallist(node[3], sc)
        local names = node[2]
        for j=1,names.n do declare(sc, names[j], vals[j]) end
      end
    elseif op == 21 then
      local vals = evallist(node[3], sc)
      local targets = node[2]
      for j=1,targets.n do assign(targets[j], vals[j], sc) end
    elseif op == 22 then
      evalmulti(node[2], sc)
    elseif op == 23 then
      local done = false
      local clauses = node[2]
      for j=1,clauses.n do
        if eval(clauses[j][1], sc) then
          local sig = exec(clauses[j][2], newscope(sc))
          if sig then return sig end
          done = true break
        end
      end
      if not done then
        local sig = exec(node[3], newscope(sc))
        if sig then return sig end
      end
    elseif op == 24 then
      while eval(node[2], sc) do
        local sig = exec(node[3], newscope(sc))
        if sig then if sig.k == 2 then break else return sig end end
      end
    elseif op == 25 then
      local i0 = eval(node[3], sc)
      local lim = eval(node[4], sc)
      local st = eval(node[5], sc) if st == nil then st = 1 end
      local iv = i0
      while (st > 0 and iv <= lim) or (st < 0 and iv >= lim) do
        local s2 = newscope(sc) declare(s2, node[2], iv)
        local sig = exec(node[6], s2)
        if sig then if sig.k == 2 then break else return sig end end
        iv = iv + st
      end
    elseif op == 26 then
      local it = evallist(node[3], sc)
      local f, s, ctrl = it[1], it[2], it[3]
      while true do
        local vals = pk(f(s, ctrl))
        if vals[1] == nil then break end
        ctrl = vals[1]
        local s2 = newscope(sc)
        local names = node[2]
        for j=1,names.n do declare(s2, names[j], vals[j]) end
        local sig = exec(node[4], s2)
        if sig then if sig.k == 2 then break else return sig end end
      end
    elseif op == 27 then
      return {k=1, vals=evallist(node[2], sc)}
    elseif op == 28 then
      return {k=2}
    elseif op == 29 then
      local sig = exec(node[2], newscope(sc))
      if sig then return sig end
    end
  end
end

local sig = exec(PROG, newscope(nil))
if sig and sig.k == 1 then return unpk(sig.vals, 1, sig.vals.n) end
"""


# ==================== public API ====================

def _parse(source):
    return _Parser(_lex(source)).parse_chunk()


def can_compile(source):
    """True if VM mode can handle this script. Never raises."""
    try:
        _parse(source)
        return True
    except Unsupported:
        return False
    except Exception:
        return False


def compile_to_vm(source, *, names=True, seed=None):
    """Compile `source` to a self-contained VM program (a Lua string with no
    loadstring). Raises Unsupported if the script uses something the VM
    subset doesn't implement."""
    if not source or not source.strip():
        raise Unsupported("the script is empty.")
    prog = _parse(source)

    # Which globals does the script touch? Bind the function/for locals so we
    # don't capture them; over-capture is harmless.
    used = set()
    _collect_globals(prog, used)
    globals_wanted = sorted(used) or ["print"]
    # Emit `name=name` for each free name so it resolves in the host env
    # (covers Roblox globals like game/workspace that aren't in _G).
    genv_items = ", ".join(f"{g}={g}" for g in globals_wanted if g.isidentifier())

    data = bytearray()
    _serialize(prog, data)

    import random
    rng = random.Random(seed)

    # Encrypt the serialized program with the same LCG+base64 the encoder
    # uses, but decode it to a BYTE STRING we deserialize — never to source
    # text + loadstring.
    a, c, s0, iv = bot_obf._lcg_params(rng)
    enc = bot_obf._encode_bytes(bytes(data), a, c, s0, iv)
    length = len(enc)
    enc_padded = enc + bytes((-length) % 3)
    alphabet = list(bot_obf._B64_CHARS)
    rng.shuffle(alphabet)
    alphabet = "".join(alphabet)
    payload_b64 = bot_obf._b64_encode(enc_padded, alphabet)

    nm = {x: (bot_obf._rand_name(rng) if names else "__" + x)
          for x in ("al", "d", "b", "bi", "i", "n", "s", "p", "k", "c", "o")}

    decode = (
        f'local {nm["al"]}="{alphabet}"\n'
        f'local {nm["d"]}="{payload_b64}"\n'
        f'local {nm["b"]}={{}}\n'
        f'local {nm["bi"]}=0\n'
        f'for {nm["i"]}=1,#{nm["d"]},4 do '
        f'local {nm["n"]}=(string.find({nm["al"]},string.sub({nm["d"]},{nm["i"]},{nm["i"]}),1,true)-1)*262144'
        f'+(string.find({nm["al"]},string.sub({nm["d"]},{nm["i"]}+1,{nm["i"]}+1),1,true)-1)*4096'
        f'+(string.find({nm["al"]},string.sub({nm["d"]},{nm["i"]}+2,{nm["i"]}+2),1,true)-1)*64'
        f'+(string.find({nm["al"]},string.sub({nm["d"]},{nm["i"]}+3,{nm["i"]}+3),1,true)-1) '
        f'{nm["b"]}[{nm["bi"]}+1]=math.floor({nm["n"]}/65536)%256 '
        f'{nm["b"]}[{nm["bi"]}+2]=math.floor({nm["n"]}/256)%256 '
        f'{nm["b"]}[{nm["bi"]}+3]={nm["n"]}%256 '
        f'{nm["bi"]}={nm["bi"]}+3 end\n'
        f'local {nm["s"]}={s0}\n'
        f'local {nm["p"]}={iv}\n'
        f'local {nm["o"]}={{}}\n'
        f'for {nm["i"]}=1,{length} do '
        f'{nm["s"]}=({nm["s"]}*{a}+{c})%{bot_obf._LCG_M} '
        f'local {nm["k"]}=math.floor({nm["s"]}/65536)%256 '
        f'local {nm["c"]}={nm["b"]}[{nm["i"]}] '
        f'{nm["o"]}[{nm["i"]}]=string.char(({nm["c"]}-{nm["k"]}-{nm["p"]})%256) '
        f'{nm["p"]}={nm["c"]} end\n'
        f'local PAYLOAD=table.concat({nm["o"]})\n'
    )

    # PAYLOAD (decoded bytes) and __G (captured globals) are the two names
    # the runtime expects to already be in scope. The metatable sends reads
    # and writes of any name NOT captured by value (e.g. a global the script
    # assigns, or one that was nil at capture) to the real global environment
    # — getfenv(0) on 5.1, _G elsewhere — so global writes actually take.
    header = (
        f"local __real=(getfenv and getfenv(0)) or _G\n"
        f"local __G=setmetatable({{{genv_items}}}, {{__index=__real, __newindex=__real}})\n"
    )
    runtime = _LUA_RUNTIME.replace("GENV", "__G")
    return "return (function()\n" + header + decode + runtime + "\nend)()\n"
