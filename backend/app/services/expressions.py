"""Calculated fields: single CEL expressions, evaluated on the server.

CEL (Common Expression Language) has no I/O, imports, assignments or unbounded loops, so a
user-written expression cannot reach the system it runs on. On top of that we cap expression
length and syntax-tree size, and only allow a fixed list of functions.

Extra functions available in expressions:
  today()                 the current date (midnight, in the configured time zone)
  now()                   the current timestamp (UTC)
  date("2026-01-31")      parse an ISO date
  days_between(a, b)      whole days from date a to date b (b - a)
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo

import celpy
from celpy import celtypes
from lark import Token, Tree

from app.config import get_settings

TIME_FUNCTIONS = {"today", "now"}
CUSTOM_FUNCTIONS = {"today", "now", "date", "days_between"}
# CEL built-ins we allow (conversions, string helpers and size).
BUILTIN_FUNCTIONS = {
    "size", "int", "uint", "double", "string", "bool", "duration", "timestamp", "has",
    "contains", "startsWith", "endsWith", "matches", "lowerAscii", "upperAscii",
    "getFullYear", "getMonth", "getDate", "getDayOfMonth", "getDayOfWeek", "getDayOfYear",
    "getHours", "getMinutes", "getSeconds",
}
MACROS = {"all", "exists", "exists_one", "map", "filter"}
ALLOWED_FUNCTIONS = CUSTOM_FUNCTIONS | BUILTIN_FUNCTIONS | MACROS

_env = celpy.Environment()


class ExpressionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ParsedExpression:
    source: str
    tree: Tree
    variables: set[str] = field(default_factory=set)
    functions: set[str] = field(default_factory=set)

    @property
    def uses_time(self) -> bool:
        return bool(self.functions & TIME_FUNCTIONS)


# --- Parsing ----------------------------------------------------------------------------


def parse(expression: str) -> ParsedExpression:
    settings = get_settings()
    source = (expression or "").strip()
    if not source:
        raise ExpressionError("empty", "The expression is empty.")
    if len(source) > settings.max_expression_length:
        raise ExpressionError(
            "too_long", f"The expression is longer than {settings.max_expression_length} characters."
        )
    try:
        tree = _env.compile(source)
    except celpy.CELParseError as exc:
        raise ExpressionError("syntax", f"Syntax error: {str(exc).strip()}") from exc

    nodes = 0
    idents: set[str] = set()
    functions: set[str] = set()
    bound: set[str] = set()
    for node in tree.iter_subtrees():
        nodes += 1
        name = _token_name(node)
        if node.data == "ident" and name:
            idents.add(name)
        elif node.data == "ident_arg" and name:
            functions.add(name)
        elif node.data in ("member_dot_arg",) and name:
            functions.add(name)
            if name in MACROS:
                bound |= _macro_bound_names(node)
        elif node.data in ("dot_ident_arg",) and name:
            functions.add(name)
    if nodes > settings.max_expression_nodes:
        raise ExpressionError("too_complex", "The expression is too complex.")

    unknown = sorted(functions - ALLOWED_FUNCTIONS)
    if unknown:
        raise ExpressionError("unknown_function", f"Unknown function: {', '.join(unknown)}.")
    return ParsedExpression(source=source, tree=tree, variables=idents - bound, functions=functions)


def _token_name(node: Tree) -> str | None:
    for child in node.children:
        if isinstance(child, Token) and child.type == "IDENT":
            return str(child)
    return None


def _macro_bound_names(node: Tree) -> set[str]:
    """The loop variable of a macro, e.g. `t` in tags.exists(t, t == "a")."""
    for child in node.children:
        if isinstance(child, Tree) and child.data == "exprlist" and child.children:
            first = child.children[0]
            for sub in first.iter_subtrees():
                if sub.data == "ident":
                    name = _token_name(sub)
                    if name:
                        return {name}
    return set()


# --- Values <-> CEL ---------------------------------------------------------------------


def _date_to_timestamp(value: dt.date) -> celtypes.TimestampType:
    return celtypes.TimestampType(dt.datetime(value.year, value.month, value.day, tzinfo=dt.UTC))


def to_cel(value, field_type: str, config: dict | None = None):
    config = config or {}
    if value is None:
        return None
    if field_type == "text":
        return celtypes.StringType(value)
    if field_type == "number":
        if isinstance(value, int) and not isinstance(value, bool):
            return celtypes.IntType(value)
        return celtypes.DoubleType(float(value))
    if field_type == "date":
        return _date_to_timestamp(dt.date.fromisoformat(value))
    if field_type == "options":
        if config.get("multiple"):
            return celtypes.ListType([celtypes.StringType(v) for v in value])
        return celtypes.StringType(value)
    if field_type == "list":
        item_type = config.get("item_type", "text")
        return celtypes.ListType([to_cel(v, item_type, config.get("item") or {}) for v in value])
    raise ExpressionError("type", f"Unsupported field type {field_type}.")


def from_cel(value, target_type: str, config: dict | None = None):
    """Convert a CEL result into a plain JSON value for a field of `target_type`."""
    config = config or {}
    if value is None:
        return None
    if isinstance(value, celpy.CELEvalError):
        raise ExpressionError("evaluation", _eval_message(value))
    value = _as_cel(value)
    if target_type == "text":
        if isinstance(value, celtypes.BoolType):
            return "true" if value else "false"
        if isinstance(value, celtypes.TimestampType):
            return _timestamp_to_date(value).isoformat()
        if isinstance(value, (celtypes.StringType, celtypes.IntType, celtypes.UintType, celtypes.DoubleType)):
            return str(value) if not isinstance(value, celtypes.DoubleType) else _format_number(float(value))
    elif target_type == "number":
        if isinstance(value, celtypes.BoolType):
            raise ExpressionError("type", "The expression returns true/false, not a number.")
        if isinstance(value, (celtypes.IntType, celtypes.UintType)):
            return int(value)
        if isinstance(value, celtypes.DoubleType):
            f = float(value)
            return int(f) if f.is_integer() and config.get("integer_only") else f
        if isinstance(value, celtypes.DurationType):
            return value.total_seconds() / 86400
    elif target_type == "date":
        if isinstance(value, celtypes.TimestampType):
            return _timestamp_to_date(value).isoformat()
        if isinstance(value, celtypes.StringType):
            return str(value)
    elif target_type == "options":
        if config.get("multiple") and isinstance(value, celtypes.ListType):
            return [str(v) for v in value]
        if isinstance(value, celtypes.StringType):
            return str(value)
    elif target_type == "list":
        if isinstance(value, celtypes.ListType):
            item_type = config.get("item_type", "text")
            return [from_cel(v, item_type, config.get("item") or {}) for v in value]
    raise ExpressionError(
        "type", f"The expression returns {_cel_type_name(value)}, which does not fit a {target_type} field."
    )


def _as_cel(value):
    """Some CEL operations hand back plain Python values; wrap them in CEL types."""
    if isinstance(value, celtypes.BoolType) or isinstance(value, (celtypes.StringType, celtypes.IntType,
                  celtypes.UintType, celtypes.DoubleType, celtypes.TimestampType, celtypes.DurationType,
                  celtypes.ListType, celtypes.MapType)):
        return value
    if isinstance(value, bool):
        return celtypes.BoolType(value)
    if isinstance(value, str):
        return celtypes.StringType(value)
    if isinstance(value, int):
        return celtypes.IntType(value)
    if isinstance(value, float):
        return celtypes.DoubleType(value)
    if isinstance(value, list):
        return celtypes.ListType(value)
    return value


def _timestamp_to_date(value: celtypes.TimestampType) -> dt.date:
    return value.astimezone(dt.UTC).date()


def _format_number(f: float) -> str:
    return str(int(f)) if f.is_integer() else repr(f)


def _cel_type_name(value) -> str:
    names = {
        celtypes.BoolType: "true/false",
        celtypes.IntType: "a number",
        celtypes.UintType: "a number",
        celtypes.DoubleType: "a number",
        celtypes.StringType: "text",
        celtypes.TimestampType: "a date",
        celtypes.DurationType: "a duration",
        celtypes.ListType: "a list",
        celtypes.MapType: "a map",
    }
    for t, n in names.items():
        if isinstance(value, t):
            return n
    return type(value).__name__


def _eval_message(err) -> str:
    text = str(err.args[0]) if getattr(err, "args", None) else str(err)
    return f"The expression could not be evaluated: {text}"


# --- Evaluation -------------------------------------------------------------------------


def _functions(now: dt.datetime | None = None):
    settings = get_settings()
    current = now or dt.datetime.now(dt.UTC)

    def today():
        local = current.astimezone(ZoneInfo(settings.timezone)).date()
        return _date_to_timestamp(local)

    def now_fn():
        return celtypes.TimestampType(current)

    def date_fn(text):
        try:
            return _date_to_timestamp(dt.date.fromisoformat(str(text)))
        except ValueError as exc:
            raise ValueError(f"date() needs an ISO date like 2026-01-31, got {text!r}") from exc

    def days_between(a, b):
        if not isinstance(a, celtypes.TimestampType) or not isinstance(b, celtypes.TimestampType):
            raise TypeError("days_between() needs two dates")
        return celtypes.IntType((_timestamp_to_date(b) - _timestamp_to_date(a)).days)

    return {"today": today, "now": now_fn, "date": date_fn, "days_between": days_between}


def evaluate(parsed: ParsedExpression, variables: dict, now: dt.datetime | None = None):
    """Evaluate with CEL-typed variables. Returns the raw CEL result."""
    program = _env.program(parsed.tree, functions=_functions(now))
    try:
        result = program.evaluate(variables)
    except celpy.CELEvalError as exc:
        raise ExpressionError("evaluation", _eval_message(exc)) from exc
    except Exception as exc:  # errors raised from our own functions or type mismatches
        raise ExpressionError("evaluation", f"The expression could not be evaluated: {exc}") from exc
    if isinstance(result, celpy.CELEvalError):
        raise ExpressionError("evaluation", _eval_message(result))
    return result


def sample_value(field_def: dict):
    """A representative value of a field's type, used to type-check expressions at save time."""
    t = field_def["type"]
    config = field_def.get("config") or {}
    if t == "text":
        return "sample"
    if t == "number":
        lo = config.get("min")
        return lo if isinstance(lo, (int, float)) and lo != 0 else 1
    if t == "date":
        return dt.date.today().isoformat()
    if t == "options":
        choices = [c["value"] for c in config.get("choices", [])] or ["option"]
        return choices[:1] if config.get("multiple") else choices[0]
    if t == "list":
        item = {"type": config.get("item_type", "text"), "config": config.get("item") or {}}
        return [sample_value(item)]
    return None


# --- Dependency graph -------------------------------------------------------------------


def calculation_order(fields: list[dict]) -> list[dict]:
    """Calculated fields sorted so each comes after the calculated fields it depends on.

    Raises ExpressionError("cycle") when calculated fields depend on each other in a loop.
    """
    calculated = {f["key"]: f for f in fields if f.get("calculated")}
    order: list[dict] = []
    state: dict[str, int] = {}  # 1 = visiting, 2 = done

    def visit(key: str, path: list[str]):
        if state.get(key) == 2:
            return
        if state.get(key) == 1:
            loop = path[path.index(key):] + [key]
            raise ExpressionError("cycle", "Calculated fields depend on each other in a loop: " + " → ".join(loop))
        state[key] = 1
        for dep in calculated[key].get("depends_on") or []:
            if dep in calculated:
                visit(dep, path + [key])
        state[key] = 2
        order.append(calculated[key])

    for key in calculated:
        visit(key, [])
    return order


def time_dependent_keys(fields: list[dict]) -> set[str]:
    """Keys of calculated fields that use today()/now(), directly or through another field."""
    by_key = {f["key"]: f for f in fields}
    result: set[str] = set()
    for f in calculation_order(fields):
        uses_time = False
        if f.get("expression"):
            try:
                uses_time = parse(f["expression"]).uses_time
            except ExpressionError:
                uses_time = False
        if uses_time or any(d in result for d in f.get("depends_on") or [] if d in by_key):
            result.add(f["key"])
    return result


def compute(fields: list[dict], values: dict, now: dt.datetime | None = None, only: set[str] | None = None):
    """Compute calculated fields into a copy of `values`.

    Returns (values, errors). A calculated field whose inputs are missing is left empty.
    """
    out = dict(values)
    errors: list[dict] = []
    by_key = {f["key"]: f for f in fields}
    for f in calculation_order(fields):
        key = f["key"]
        if only is not None and key not in only:
            continue
        deps = f.get("depends_on") or []
        if any(out.get(d) is None for d in deps):
            out[key] = None
            continue
        try:
            parsed = parse(f["expression"])
            variables = {d: to_cel(out[d], by_key[d]["type"], by_key[d].get("config")) for d in deps}
            out[key] = from_cel(evaluate(parsed, variables, now), f["type"], f.get("config"))
        except ExpressionError as exc:
            out[key] = None
            errors.append({"field": key, "code": "calculation_failed", "message": exc.message})
    return out, errors
