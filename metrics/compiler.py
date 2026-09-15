"""Compile metrics/*.yaml into SQL views. The YAML is the only place a metric is defined (CLAUDE.md §5).

Expressions use a small Python-like language, parsed with `ast` and rendered to SQL through a whitelist:
comparisons (incl. `in (...)`), `and`/`or`/`not`, + - * /, `a if cond else b`, numbers, strings, and names.
Aggregations may also call sum/avg/count/min/max; `count(*)` is allowed. Anything else is a CompileError,
so a definition can never smuggle arbitrary SQL into a view.

Each metric version compiles to one row-level view, `gold.<metric>_v<version>_lines`: key, `metric_date`
(the period column), dimensions, the columns its aggregations need, and every input/rule as a boolean.
A missing input makes a flag FALSE, never NULL — a line with no confirmed date is not on time.
`aggregate_sql()` builds the grouped query consumer views and reconciliation are compiled from.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator
from sqlalchemy import text
from sqlalchemy.engine import Connection

METRICS_DIR = Path(__file__).resolve().parent
COMPILED_DIR = METRICS_DIR / "compiled"
FILENAME_RE = re.compile(r"^([a-z][a-z0-9_]*)\.v(\d+)\.yaml$")
PERIODS = {"day": "metric_date", "month": "to_char(metric_date, 'YYYY-MM')"}
AGGREGATES = {"sum": "SUM", "avg": "AVG", "count": "COUNT", "min": "MIN", "max": "MAX"}
COMPARE = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "=", ast.NotEq: "<>"}
ARITHMETIC = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*"}

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]


class CompileError(ValueError):
    pass


class MetricDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric: Identifier
    version: int = Field(gt=0)
    status: Literal["current", "legacy"]
    owner: str
    changed_by: str
    effective_from: date
    grain: str
    source: Annotated[str, StringConstraints(pattern=r"^gold\.[a-z][a-z0-9_]*$")]
    key: list[Identifier] = Field(min_length=1)
    period: Identifier
    dimensions: list[Identifier] = []
    params: dict[Identifier, bool | int | float | str] = {}
    inputs: dict[Identifier, str] = {}
    rules: dict[Identifier, str] = {}
    aggregations: dict[Identifier, str] = Field(min_length=1)
    notes: str = ""

    @model_validator(mode="after")
    def _names_unique(self) -> MetricDefinition:
        groups = [set(self.params), set(self.inputs), set(self.rules), set(self.aggregations)]
        seen: set[str] = set()
        for group in groups:
            if seen & group:
                raise ValueError(f"names defined twice: {sorted(seen & group)}")
            seen |= group
        return self

    @property
    def view(self) -> str:
        return f"gold.{self.metric}_v{self.version}_lines"

    @property
    def flags(self) -> dict[str, str]:
        return {**self.inputs, **self.rules}


@dataclass(frozen=True)
class CompiledMetric:
    definition: MetricDefinition
    source_file: str
    source_columns: list[str]  # every column the definition reads from its source table
    statements: list[str]  # executed one at a time by apply()

    @property
    def ddl(self) -> str:
        return "\n".join(s + ";" for s in self.statements) + "\n"


class _Renderer:
    def __init__(self, d: MetricDefinition, known_flags: set[str], aggregate: bool) -> None:
        self.d = d
        self.flags = known_flags
        self.aggregate = aggregate
        self.columns: set[str] = set()
        self._in_call = False

    def render(self, expr: str) -> str:
        source = expr.replace("count(*)", "count()")
        try:
            tree = ast.parse(source, mode="eval")
        except SyntaxError as exc:
            raise CompileError(f"cannot parse {expr!r}: {exc.msg}") from exc
        return self._node(tree.body, expr)

    def _node(self, node: ast.expr, expr: str) -> str:
        match node:
            case ast.Constant(value=bool() as v):
                return "TRUE" if v else "FALSE"
            case ast.Constant(value=int() | float() as v):
                return repr(v)
            case ast.Constant(value=str() as v):
                return "'" + v.replace("'", "''") + "'"
            case ast.Name(id=name):
                return self._name(name, expr)
            case ast.BoolOp(op=op, values=values):
                joiner = " AND " if isinstance(op, ast.And) else " OR "
                return "(" + joiner.join(self._node(v, expr) for v in values) + ")"
            case ast.UnaryOp(op=ast.Not(), operand=operand):
                return f"(NOT {self._node(operand, expr)})"
            case ast.UnaryOp(op=ast.USub(), operand=operand):
                return f"(-{self._node(operand, expr)})"
            case ast.BinOp(left=left, op=ast.Div(), right=right):
                numerator, denominator = self._node(left, expr), self._node(right, expr)
                return f"(({numerator})::double precision / NULLIF({denominator}, 0))"
            case ast.BinOp(left=left, op=op, right=right) if type(op) in ARITHMETIC:
                return f"({self._node(left, expr)} {ARITHMETIC[type(op)]} {self._node(right, expr)})"
            case ast.IfExp(test=test, body=body, orelse=orelse):
                return (f"(CASE WHEN {self._node(test, expr)} THEN {self._node(body, expr)} "
                        f"ELSE {self._node(orelse, expr)} END)")
            case ast.Compare(left=left, ops=[op], comparators=[right]):
                if isinstance(op, (ast.In, ast.NotIn)):
                    if not isinstance(right, (ast.Tuple, ast.List)) or not right.elts:
                        raise CompileError(f"{expr!r}: 'in' needs a literal tuple or list")
                    keyword = "IN" if isinstance(op, ast.In) else "NOT IN"
                    items = ", ".join(self._node(e, expr) for e in right.elts)
                    return f"({self._node(left, expr)} {keyword} ({items}))"
                if type(op) in COMPARE:
                    return f"({self._node(left, expr)} {COMPARE[type(op)]} {self._node(right, expr)})"
            case ast.Call(func=ast.Name(id=fn), args=args, keywords=[]) if fn in AGGREGATES:
                if not self.aggregate:
                    raise CompileError(f"{expr!r}: {fn}() is only allowed in aggregations")
                if self._in_call:
                    raise CompileError(f"{expr!r}: nested aggregate")
                if fn == "count" and not args:
                    return "COUNT(*)"
                if len(args) != 1:
                    raise CompileError(f"{expr!r}: {fn}() takes exactly one argument")
                self._in_call = True
                try:
                    return f"{AGGREGATES[fn]}({self._node(args[0], expr)})"
                finally:
                    self._in_call = False
        raise CompileError(f"{expr!r}: unsupported expression {ast.unparse(node)!r}")

    def _name(self, name: str, expr: str) -> str:
        if name in self.d.params:
            return self._node(ast.Constant(self.d.params[name]), expr)
        if self.aggregate and not self._in_call:
            raise CompileError(f"{expr!r}: '{name}' must be inside an aggregate")
        if name in self.flags:
            # flags are boolean in the row view; aggregates count them as 0/1
            return f"{name}::int" if self.aggregate else name
        if name in self.d.flags:
            raise CompileError(f"{expr!r}: '{name}' is used before it is defined")
        self.columns.add(name)
        return name


def load(path: Path) -> MetricDefinition:
    match = FILENAME_RE.match(path.name)
    if not match:
        raise CompileError(f"{path.name}: metric files must be named <metric>.v<version>.yaml")
    try:
        definition = MetricDefinition.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    except ValidationError as exc:
        raise CompileError(f"{path.name}: {exc}") from exc
    if (definition.metric, definition.version) != (match.group(1), int(match.group(2))):
        raise CompileError(f"{path.name}: file name does not match metric '{definition.metric}' "
                           f"version {definition.version}")
    return definition


def compile_metric(d: MetricDefinition, source_file: str) -> CompiledMetric:
    steps: list[str] = []
    known: set[str] = set()
    columns: set[str] = set(d.key) | {d.period} | set(d.dimensions)
    previous = "step_0"
    for i, (name, expr) in enumerate(d.flags.items(), start=1):
        renderer = _Renderer(d, known, aggregate=False)
        sql = renderer.render(expr)
        columns |= renderer.columns
        steps.append(f"step_{i} AS (\n    SELECT {previous}.*, COALESCE({sql}, FALSE) AS {name}\n"
                     f"    FROM {previous}\n)")
        known.add(name)
        previous = f"step_{i}"
    clash = columns & known
    if clash:
        raise CompileError(f"{source_file}: flag names shadow source columns: {sorted(clash)}")

    needed: list[str] = []
    for expr in d.aggregations.values():
        renderer = _Renderer(d, known, aggregate=True)
        renderer.render(expr)
        columns |= renderer.columns
        needed += [c for c in sorted(renderer.columns) if c not in needed]

    select = [*d.key, f"{d.period} AS metric_date", *d.dimensions]
    select += [c for c in needed if c not in select]
    select += list(d.flags)
    header = (f"-- Compiled from metrics/{source_file} by metrics/compiler.py. Do not edit; edit the YAML.\n"
              f"-- {d.metric} v{d.version} ({d.status}), owner {d.owner}, effective {d.effective_from}.\n")
    cte = ",\n".join([f"step_0 AS (\n    SELECT * FROM {d.source}\n)", *steps])
    statements = [
        f"{header}DROP VIEW IF EXISTS {d.view}",
        f"CREATE VIEW {d.view} AS\nWITH {cte}\nSELECT {', '.join(select)}\nFROM {previous}",
        f"COMMENT ON VIEW {d.view} IS 'Compiled from metrics/{source_file}: {d.metric} v{d.version} "
        f"({d.status}). Do not edit.'",
    ]
    return CompiledMetric(d, source_file, sorted(columns), statements)


def compile_all(directory: Path = METRICS_DIR) -> list[CompiledMetric]:
    compiled = [compile_metric(load(p), p.name) for p in sorted(directory.glob("*.yaml"))]
    current: dict[str, list[int]] = {}
    for c in compiled:
        if c.definition.status == "current":
            current.setdefault(c.definition.metric, []).append(c.definition.version)
    for metric, versions in current.items():
        if len(versions) > 1:
            raise CompileError(f"metric '{metric}' has more than one current version: {sorted(versions)}")
    return compiled


def aggregate_sql(c: CompiledMetric, group_by: list[str]) -> str:
    """SELECT over the metric's row view grouped by periods ('day', 'month') and/or declared dimensions."""
    d = c.definition
    groups: list[str] = []
    for g in group_by:
        if g in PERIODS:
            groups.append(f"{PERIODS[g]} AS {g}")
        elif g in d.dimensions:
            groups.append(g)
        else:
            raise CompileError(f"{d.metric} v{d.version}: cannot group by '{g}' (not a period or dimension)")
    known = set(d.flags)
    aggs = [f"{_Renderer(d, known, aggregate=True).render(e)} AS {n}" for n, e in d.aggregations.items()]
    order = ", ".join(str(i) for i in range(1, len(groups) + 1))
    sql = f"SELECT {', '.join([*groups, *aggs])}\nFROM {d.view}"
    if groups:
        sql += f"\nGROUP BY {order}\nORDER BY {order}"
    return sql


def write_compiled(compiled: list[CompiledMetric], out_dir: Path = COMPILED_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for c in compiled:
        path = out_dir / f"{c.definition.metric}_v{c.definition.version}.sql"
        path.write_text(c.ddl, encoding="utf-8", newline="\n")
        paths.append(path)
    return paths


def apply(conn: Connection, compiled: list[CompiledMetric]) -> None:
    """Create every view in the caller's transaction, after checking source columns exist."""
    for c in compiled:
        schema, table = c.definition.source.split(".")
        present = set(conn.execute(
            text("SELECT column_name FROM information_schema.columns "
                 "WHERE table_schema = :s AND table_name = :t"), {"s": schema, "t": table}).scalars())
        if not present:
            raise CompileError(f"{c.source_file}: source table {c.definition.source} does not exist")
        missing = [col for col in c.source_columns if col not in present]
        if missing:
            raise CompileError(f"{c.source_file}: {c.definition.source} has no column(s) {missing}")
        for statement in c.statements:
            conn.execute(text(statement))
