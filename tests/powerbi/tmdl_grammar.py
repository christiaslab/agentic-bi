"""A small checker for the TMDL files of the Power BI project.

It is an independent second opinion on the generator. It knows the TMDL grammar as documented
by Microsoft ("Tabular Model Definition Language (TMDL)") and does not need Microsoft tools.

Two levels of checks:

* `parse_document` reads one file: tab indentation, which object may sit under which parent,
  known properties per object type, name quoting, descriptions and multi-line expressions.
* `check_model` reads a whole definition folder and also checks the cross references: relationships,
  source columns, DAX references, M parameters and a few model facts.

Both return a list of error strings. An empty list means the files pass.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

OBJECT_KEYWORDS = frozenset(
    {
        "database",
        "model",
        "table",
        "column",
        "measure",
        "partition",
        "expression",
        "relationship",
        "annotation",
        "ref",
    }
)

# Which object types may be declared directly under which parent.
CHILDREN: dict[str, frozenset[str]] = {
    "database": frozenset(),
    "model": frozenset({"annotation"}),
    "table": frozenset({"column", "measure", "partition", "annotation"}),
    "column": frozenset({"annotation"}),
    "measure": frozenset({"annotation"}),
    "partition": frozenset(),
    "expression": frozenset({"annotation"}),
    "relationship": frozenset({"annotation"}),
    "annotation": frozenset(),
    "ref": frozenset(),
}

# Which object types may sit at depth 0 of each kind of file.
ROOT_KINDS: dict[str, frozenset[str]] = {
    "database": frozenset({"database"}),
    "model": frozenset({"model"}),
    "expressions": frozenset({"expression"}),
    "relationships": frozenset({"relationship"}),
    "table": frozenset({"table"}),
}
# Power BI Desktop writes these two at depth 0 of model.tmdl, after the model properties.
MODEL_ROOT_LEVEL_CHILDREN = frozenset({"annotation", "ref"})

DATA_TYPES = frozenset(
    {"string", "int64", "double", "dateTime", "decimal", "boolean", "binary", "variant", "unknown"}
)
SUMMARIZE_BY = frozenset(
    {"default", "none", "sum", "min", "max", "count", "average", "distinctCount"}
)
PARTITION_MODES = frozenset({"import", "directQuery", "dual", "default"})
FILTER_DIRECTIONS = frozenset({"oneDirection", "bothDirections", "automatic"})
CARDINALITIES = frozenset({"one", "many"})
SECURITY_DIRECTIONS = frozenset({"oneDirection", "bothDirections", "none"})

# Known properties per object type. A value is a kind of value:
# "bool", "text", "int", "guid", "expr", "ref" (table.column) or a frozenset of allowed words.
PROPERTIES: dict[str, dict[str, str | frozenset[str]]] = {
    "database": {"compatibilityLevel": "int", "compatibilityMode": "text"},
    "model": {
        "culture": "text",
        "defaultPowerBIDataSourceVersion": "text",
        "discourageImplicitMeasures": "bool",
        "sourceQueryCulture": "text",
    },
    "table": {"lineageTag": "guid", "dataCategory": "text", "isHidden": "bool"},
    "column": {
        "dataType": DATA_TYPES,
        "isHidden": "bool",
        "isKey": "bool",
        "formatString": "text",
        "lineageTag": "guid",
        "summarizeBy": SUMMARIZE_BY,
        "sourceColumn": "text",
        "dataCategory": "text",
        "displayFolder": "text",
        "isNullable": "bool",
    },
    "measure": {
        "formatString": "text",
        "lineageTag": "guid",
        "displayFolder": "text",
        "isHidden": "bool",
    },
    "partition": {"mode": PARTITION_MODES, "source": "expr"},
    "expression": {"lineageTag": "guid", "queryGroup": "text"},
    "relationship": {
        "fromColumn": "ref",
        "toColumn": "ref",
        "isActive": "bool",
        "crossFilteringBehavior": FILTER_DIRECTIONS,
        "fromCardinality": CARDINALITIES,
        "toCardinality": CARDINALITIES,
        "relyOnReferentialIntegrity": "bool",
        "securityFilteringBehavior": SECURITY_DIRECTIONS,
    },
    "annotation": {},
    "ref": {},
}

# How an object line may use "=": "none", "expr" (DAX or M), "type" (partition source type) or
# "text" (annotation value).
EQUALS_RULE = {
    "measure": "expr",
    "expression": "expr",
    "partition": "type",
    "annotation": "text",
}
PARTITION_SOURCE_TYPES = frozenset({"m"})

_IDENT = re.compile(r"^[^\W\d]\w*$")
_GUID = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")
_WORD = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(.*)$", re.S)


class TmdlError(Exception):
    """A problem found while reading one line."""


@dataclass
class Prop:
    name: str
    line: int = 0
    value: str | None = None  # text after ":" or after an inline "="; None for a bare name
    bare: bool = False
    expr: bool = False  # written as "name =" (an expression property)
    body: list[str] | None = None

    @property
    def text(self) -> str:
        if self.body is not None:
            return "\n".join(self.body)
        return self.value or ""


@dataclass
class Node:
    kind: str
    name: str | None
    line: int = 0
    file: str = ""
    value: str | None = None  # text after "=" on the declaring line
    body: list[str] | None = None  # lines of a multi-line expression, indentation removed
    description: str | None = None
    props: dict[str, Prop] = field(default_factory=dict)
    children: list[Node] = field(default_factory=list)
    root_level: bool = False

    @property
    def expression(self) -> str:
        if self.body is not None:
            return "\n".join(self.body)
        return self.value or ""

    def prop_text(self, name: str) -> str | None:
        prop = self.props.get(name)
        return None if prop is None else prop.text

    def prop_bool(self, name: str) -> bool:
        prop = self.props.get(name)
        if prop is None:
            return False
        return prop.bare or (prop.value or "").strip() == "true"

    def kids(self, kind: str) -> list[Node]:
        return [child for child in self.children if child.kind == kind]


# --------------------------------------------------------------------------- names


def quote_name(name: str) -> str:
    """Return the name as TMDL writes it: plain when it can be, else single-quoted."""
    if _IDENT.match(name):
        return name
    return "'" + name.replace("'", "''") + "'"


def read_name(text: str, pos: int = 0, stop: str = r"\s=", allow_guid: bool = False):
    """Read one name from text at pos. Return (name, end position).

    A quoted name runs to the closing single quote; two single quotes stand for one.
    An unquoted name must be a plain identifier (a GUID is also accepted when allow_guid is set).
    """
    if pos >= len(text):
        raise TmdlError("a name is missing")
    if text[pos] == "'":
        chars: list[str] = []
        i = pos + 1
        while i < len(text):
            if text[i] == "'":
                if text[i + 1 : i + 2] == "'":
                    chars.append("'")
                    i += 2
                    continue
                if not chars:
                    raise TmdlError("a quoted name must not be empty")
                return "".join(chars), i + 1
            chars.append(text[i])
            i += 1
        raise TmdlError("a quoted name is not closed")
    match = re.compile("[^" + stop + "]+").match(text, pos)
    if match is None:
        raise TmdlError("a name is missing")
    name = match.group()
    if not _IDENT.match(name) and not (allow_guid and _GUID.match(name)):
        raise TmdlError(f"the name {name!r} is not a plain identifier and must be in single quotes")
    return name, match.end()


def parse_column_ref(text: str) -> tuple[str, str]:
    """Parse `table.column`, where each part is quoted when it is not a plain identifier."""
    text = text.strip()
    table, pos = read_name(text, 0, stop=r"\s=.")
    if text[pos : pos + 1] != ".":
        raise TmdlError(f"a column reference must be table.column, found {text!r}")
    column, pos = read_name(text, pos + 1, stop=r"\s=.")
    if text[pos:].strip():
        raise TmdlError(
            f"unexpected text {text[pos:].strip()!r} after the column name; "
            "a name with a space must be in single quotes"
        )
    return table, column


# --------------------------------------------------------------------------- one line


def _parse_object(kind: str, tail: str) -> tuple[Node, bool]:
    """Parse an object line after its keyword. Return the node and whether a body must follow."""
    s = tail.strip()
    name = None
    if kind == "ref":
        match = re.match(r"^(table|expression|culture)\s+(.+)$", s)
        if match is None:
            raise TmdlError("a ref line must be 'ref table <name>'")
        name, end = read_name(match.group(2).strip())
        if match.group(2).strip()[end:].strip():
            raise TmdlError("unexpected text after the name of the ref")
        return Node("ref", name, value=match.group(1)), False
    if s and not s.startswith("="):
        name, pos = read_name(s, 0, allow_guid=(kind == "relationship"))
        rest = s[pos:].strip()
    else:
        rest = s
    if name is None and kind != "database":
        raise TmdlError(f"a {kind} needs a name")
    rule = EQUALS_RULE.get(kind, "none")
    if rest and not rest.startswith("="):
        raise TmdlError(
            f"unexpected text {rest!r} after the name of the {kind}; "
            "a name with a space, a dot or a quote must be in single quotes"
        )
    if rule == "none":
        if rest:
            raise TmdlError(f"a {kind} does not take '='")
        return Node(kind, name), False
    if not rest:
        raise TmdlError(f"a {kind} needs '=' after its name")
    value = rest[1:].strip()
    if rule == "type":
        if value not in PARTITION_SOURCE_TYPES:
            raise TmdlError(f"unsupported partition source type {value!r}")
        return Node(kind, name, value=value), False
    if rule == "text":
        if not value:
            raise TmdlError("an annotation needs a value")
        return Node(kind, name, value=value), False
    if not value:
        return Node(kind, name), True
    return Node(kind, name, value=value), False


def _parse_element(rest: str) -> tuple[Node | Prop, bool]:
    """Parse one non-blank line (indentation removed) into an object or a property."""
    match = _WORD.match(rest)
    if match is None:
        raise TmdlError(f"a line must start with a keyword or property name, found {rest[:20]!r}")
    word, tail = match.group(1), match.group(2)
    stripped = tail.strip()
    if tail.lstrip().startswith(":"):
        return Prop(word, value=tail.lstrip()[1:].strip()), False
    if word not in OBJECT_KEYWORDS and stripped.startswith("="):
        value = stripped[1:].strip()
        return Prop(word, value=value or None, expr=True), not value
    if word in OBJECT_KEYWORDS:
        if tail and not tail[0].isspace():
            raise TmdlError(f"a {word} must be followed by a space")
        if word != "database" and not stripped:
            raise TmdlError(f"a {word} needs a name")
        return _parse_object(word, tail)
    if not stripped:
        return Prop(word, bare=True), False
    raise TmdlError(f"unknown object keyword {word!r}")


def _read_body(lines: list[str], start: int, depth: int) -> tuple[list[str], int]:
    """Read the lines of a multi-line expression that follow a declaring line at `depth`.

    Body lines are indented at least two tabs deeper than the declaring line. Blank lines inside
    the body are kept. Spaces after the tabs are part of the expression.
    """
    prefix = "\t" * (depth + 2)
    body: list[str] = []
    j = start
    while j < len(lines):
        line = lines[j].replace("\r", "")
        if not line.strip():
            body.append("")
        elif line.startswith(prefix):
            body.append(line[len(prefix) :].rstrip())
        else:
            break
        j += 1
    while body and body[-1] == "":
        body.pop()
    return body, start + len(body)


# --------------------------------------------------------------------------- one file


def parse_document(text: str, file_kind: str, file: str = "") -> tuple[list[Node], list[str]]:
    """Parse one TMDL file. Return its top-level nodes and the grammar errors.

    file_kind is one of database, model, expressions, relationships, table.
    """
    errors: list[str] = []
    roots: list[Node] = []

    def err(line: int, message: str) -> None:
        errors.append(f"{file}:{line}: {message}")

    lines = re.split(r"\r\n|\n", text)
    if lines and lines[-1] == "":
        lines.pop()
    stack: list[Node] = []  # stack[d] is the open object at depth d
    pending: list[tuple[int, int, str]] = []  # description lines: (line, depth, text)
    i = 0
    while i < len(lines):
        raw = lines[i]
        line_no = i + 1
        i += 1
        if "\r" in raw:
            err(line_no, "a carriage return that is not part of a CRLF line ending")
            raw = raw.replace("\r", "")
        if not raw.strip():
            if pending:
                err(pending[-1][0], "a description must be directly above its object")
                pending = []
            continue
        depth = len(raw) - len(raw.lstrip("\t"))
        rest = raw[depth:].rstrip()
        if rest[0].isspace():
            err(line_no, "indentation must be tabs only; found a space in the indentation")
            continue
        if rest.startswith("///"):
            pending.append((line_no, depth, rest[3:].removeprefix(" ")))
            continue
        try:
            element, need_body = _parse_element(rest)
        except TmdlError as exc:
            err(line_no, str(exc))
            pending = []
            continue
        element.line = line_no
        if isinstance(element, Node):
            element.file = file
        if need_body:
            body, i = _read_body(lines, i, depth)
            if body:
                element.body = body
            else:
                err(
                    line_no,
                    "the expression has no body; its lines must be indented at least "
                    "two tabs deeper than the line that declares it",
                )
        if isinstance(element, Prop):
            if depth == 0:
                err(line_no, f"the property {element.name!r} cannot sit at the top level")
            elif depth > len(stack):
                err(line_no, f"the property {element.name!r} is indented too deep: no parent")
            else:
                del stack[depth:]
                if pending:
                    err(
                        pending[0][0], "a description must be followed by an object, not a property"
                    )
                    pending = []
                if element.name in stack[-1].props:
                    err(line_no, f"the property {element.name!r} is set twice")
                stack[-1].props[element.name] = element
            pending = []
            continue
        parent = _place(element, depth, stack, roots, file_kind, line_no, err)
        if pending:
            if any(d != depth for _, d, _ in pending):
                err(pending[0][0], "a description is not at the depth of the object below it")
            element.description = "\n".join(t for _, _, t in pending)
            pending = []
        if parent is not None:
            parent.children.append(element)
    if pending:
        err(pending[0][0], "a description at the end of the file is not followed by an object")
    if file_kind in {"database", "model", "table"} and len(roots) != 1:
        err(1, f"a {file_kind} file must hold exactly one top-level {file_kind}")
    for root in roots:
        _validate(root, errors)
    return roots, errors


def _place(element, depth, stack, roots, file_kind, line_no, err) -> Node | None:
    """Put an object into the stack. Return the parent that should list it, if any."""
    kind = element.kind
    if depth == 0:
        if file_kind == "model" and roots and kind in MODEL_ROOT_LEVEL_CHILDREN:
            element.root_level = True
            stack[:] = [element]
            return roots[0]
        if kind not in ROOT_KINDS[file_kind]:
            err(line_no, f"a {kind} cannot sit at the top of a {file_kind} file")
        stack[:] = [element]
        roots.append(element)
        return None
    if depth > len(stack):
        err(line_no, f"the {kind} is indented too deep: there is no parent one level above")
        stack[:] = stack[:depth]
        return None
    parent = stack[depth - 1]
    if kind not in CHILDREN[parent.kind]:
        err(line_no, f"a {kind} is not allowed inside a {parent.kind}")
    del stack[depth:]
    stack.append(element)
    return parent


# --------------------------------------------------------------------------- property checks


def _validate(node: Node, errors: list[str]) -> None:
    def err(line: int, message: str) -> None:
        errors.append(f"{node.file}:{line}: {message}")

    specs = PROPERTIES[node.kind]
    for prop in node.props.values():
        if prop.name not in specs:
            err(prop.line, f"unknown property {prop.name!r} for a {node.kind}")
            continue
        _validate_value(prop, specs[prop.name], err)
    _validate_required(node, err)
    for child in node.children:
        _validate(child, errors)


def _validate_value(prop: Prop, spec, err) -> None:
    if spec == "expr":
        if not prop.expr:
            err(prop.line, f"the property {prop.name!r} is an expression and is written 'name ='")
        return
    if prop.expr:
        err(prop.line, f"the property {prop.name!r} is written 'name: value', not with '='")
        return
    if spec == "bool":
        if not prop.bare and (prop.value or "") not in {"true", "false"}:
            err(prop.line, f"the property {prop.name!r} must be bare, true or false")
        return
    if prop.bare or not prop.value:
        err(prop.line, f"the property {prop.name!r} needs a value")
        return
    value = prop.value
    if isinstance(spec, frozenset):
        if value not in spec:
            err(prop.line, f"{value!r} is not a valid value of {prop.name!r}")
    elif spec == "int" and not value.isdigit():
        err(prop.line, f"{prop.name!r} must be a whole number, found {value!r}")
    elif spec == "guid" and not _GUID.match(value):
        err(prop.line, f"{prop.name!r} must be a GUID, found {value!r}")
    elif spec == "ref":
        try:
            parse_column_ref(value)
        except TmdlError as exc:
            err(prop.line, f"{prop.name}: {exc}")


def _validate_required(node: Node, err) -> None:
    def need(name: str) -> None:
        if name not in node.props:
            err(node.line, f"a {node.kind} needs the property {name!r}")

    if node.kind == "database":
        need("compatibilityLevel")
    elif node.kind == "column":
        need("dataType")
        need("sourceColumn")
    elif node.kind == "partition":
        need("source")
    elif node.kind == "relationship":
        need("fromColumn")
        need("toColumn")
    elif node.kind == "table" and not node.kids("partition"):
        err(node.line, "a table needs a partition")


# --------------------------------------------------------------------------- DAX and M


@dataclass
class DaxRefs:
    columns: list[tuple[str, str]] = field(default_factory=list)  # (table, column)
    bare: list[str] = field(default_factory=list)  # names in [brackets] with no table
    strings: set[str] = field(default_factory=set)  # string literals, for local columns


_DAX_TOKEN = re.compile(
    r"""
    (?P<string>"(?:[^"]|"")*")
  | (?P<lcomment>(?://|--)[^\n]*)
  | (?P<bcomment>/\*.*?\*/)
  | (?P<qtable>'(?P<qt>(?:[^']|'')+)'\s*\[(?P<qc>(?:[^\]]|\]\])+)\])
  | (?P<table>(?<![\w'])(?P<tn>[^\W\d]\w*)\s*\[(?P<tc>(?:[^\]]|\]\])+)\])
  | (?P<bare>\[(?P<bn>(?:[^\]]|\]\])+)\])
    """,
    re.X | re.S,
)
_DAX_KEYWORDS = frozenset({"RETURN", "VAR", "IN", "NOT", "AND", "OR", "ASC", "DESC"})


def dax_references(expr: str) -> DaxRefs:
    """Find the table[column] and bare [name] references of a DAX expression.

    String literals and comments are skipped. The strings are returned so that a caller can
    accept [name] when "name" is a column that the same expression adds (for example ADDCOLUMNS).
    """
    refs = DaxRefs()
    for match in _DAX_TOKEN.finditer(expr):
        if match.group("string") is not None:
            refs.strings.add(match.group("string")[1:-1].replace('""', '"'))
        elif match.group("qtable") is not None:
            table = match.group("qt").replace("''", "'")
            refs.columns.append((table, match.group("qc").replace("]]", "]")))
        elif match.group("table") is not None:
            column = match.group("tc").replace("]]", "]")
            if match.group("tn").upper() in _DAX_KEYWORDS:
                refs.bare.append(column)
            else:
                refs.columns.append((match.group("tn"), column))
        elif match.group("bare") is not None:
            refs.bare.append(match.group("bn").replace("]]", "]"))
    return refs


_FILE_CONTENTS = re.compile(r"File\.Contents\s*\(\s*([^)]*)\)")
_FILE_CONTENTS_PARAM = re.compile(r"^([^\W\d]\w*)\s*&")
_SELECT_COLUMNS = re.compile(r"Table\.SelectColumns\s*\(\s*\w+\s*,\s*\{([^}]*)\}")
_M_STRING = re.compile(r'"((?:[^"]|"")*)"')
_M_TYPED_PAIR = re.compile(r'\{\s*"((?:[^"]|"")*)"\s*,\s*(type\s+\w+|[\w.]+)\s*\}')
# M type to the TMDL data type that Power BI Desktop pairs with it.
_M_TYPES = {
    "type date": "dateTime",
    "type datetime": "dateTime",
    "type text": "string",
    "type logical": "boolean",
    "type number": "double",
    "type binary": "binary",
    "Int64.Type": "int64",
    "Currency.Type": "decimal",
}


# --------------------------------------------------------------------------- the whole model


def file_kind(path: str) -> str | None:
    """Return the kind of a definition file from its path relative to the definition folder."""
    parts = path.replace("\\", "/").split("/")
    if len(parts) == 2 and parts[0] == "tables" and parts[1].endswith(".tmdl"):
        return "table"
    if len(parts) == 1 and parts[0] in {f"{kind}.tmdl" for kind in ROOT_KINDS if kind != "table"}:
        return parts[0].removesuffix(".tmdl")
    return None


def load_definition(root: Path) -> dict[str, str]:
    """Read every .tmdl file under root as UTF-8, keyed by its posix path relative to root."""
    return {
        path.relative_to(root).as_posix(): path.read_bytes().decode("utf-8")
        for path in sorted(root.rglob("*.tmdl"))
    }


def check_model(files: dict[str, str]) -> list[str]:
    """Check a whole definition folder, given as {relative path: text}."""
    errors: list[str] = []
    parsed: dict[str, list[Node]] = {}
    for path in sorted(files):
        kind = file_kind(path)
        if kind is None:
            errors.append(f"{path}: not a TMDL file that this project uses")
            continue
        roots, file_errors = parse_document(files[path], kind, path)
        errors += file_errors
        parsed[path] = roots
    for required in ("database.tmdl", "model.tmdl"):
        if required not in files:
            errors.append(f"{required}: the file is missing")
    if not any(kind == "table" for kind in map(file_kind, files)):
        errors.append("tables: the model has no table file")
    if errors:
        return errors
    errors += _check_cross_references(parsed)
    return errors


def _cross(node: Node, message: str) -> str:
    return f"{node.file}:{node.line}: {message}"


def _check_cross_references(parsed: dict[str, list[Node]]) -> list[str]:
    errors: list[str] = []
    tables: dict[str, Node] = {}
    expressions: dict[str, Node] = {}
    relationships: list[Node] = []
    model_children: list[Node] = []
    for roots in parsed.values():
        for root in roots:
            if root.kind == "table":
                if root.name in tables:
                    errors.append(_cross(root, f"the table {root.name!r} is declared twice"))
                tables[root.name] = root
            elif root.kind == "expression":
                if root.name in expressions:
                    errors.append(_cross(root, f"the expression {root.name!r} is declared twice"))
                expressions[root.name] = root
            elif root.kind == "relationship":
                relationships.append(root)
            elif root.kind == "model":
                model_children += root.children
    for ref in (n for n in model_children if n.kind == "ref"):
        if ref.value == "table" and ref.name not in tables:
            errors.append(_cross(ref, f"ref table {ref.name!r} names no table"))

    columns = {t: {c.name: c for c in node.kids("column")} for t, node in tables.items()}
    measures: dict[str, tuple[str, Node]] = {}
    for tname, table in tables.items():
        seen: set[str] = set()
        for column in table.kids("column"):
            if column.name.casefold() in seen:
                errors.append(_cross(column, f"the column {column.name!r} is declared twice"))
            seen.add(column.name.casefold())
        for measure in table.kids("measure"):
            if measure.name.casefold() in {m.casefold() for m in measures}:
                errors.append(_cross(measure, f"the measure {measure.name!r} is declared twice"))
            if measure.name.casefold() in seen:
                errors.append(_cross(measure, f"the measure {measure.name!r} has a column's name"))
            measures[measure.name] = (tname, measure)

    for tname, table in tables.items():
        for measure in table.kids("measure"):
            errors += _check_measure(measure, tables, columns, measures)
        for partition in table.kids("partition"):
            errors += _check_partition(table, partition, columns[tname], expressions)
    for rel in relationships:
        errors += _check_relationship(rel, columns)
    errors += _check_model_facts(tables)
    errors += _check_lineage_tags(parsed)
    return errors


def _check_measure(measure: Node, tables, columns, measures) -> list[str]:
    errors: list[str] = []
    refs = dax_references(measure.expression)
    for table, column in refs.columns:
        if table not in tables:
            errors.append(_cross(measure, f"{measure.name}: unknown table {table!r}"))
        elif column not in columns[table] and not (
            column in measures and measures[column][0] == table
        ):
            errors.append(_cross(measure, f"{measure.name}: {table!r} has no column {column!r}"))
    for name in refs.bare:
        if name not in measures and name not in refs.strings:
            errors.append(
                _cross(measure, f"{measure.name}: [{name}] is no measure and no local column")
            )
    return errors


def _check_partition(table: Node, partition: Node, columns, expressions) -> list[str]:
    errors: list[str] = []
    source = partition.props["source"].text
    for call in _FILE_CONTENTS.finditer(source):
        argument = call.group(1).strip()
        param = _FILE_CONTENTS_PARAM.match(argument)
        if param is not None:
            if param.group(1) not in expressions:
                errors.append(
                    _cross(partition, f"File.Contents uses {param.group(1)!r}: no such expression")
                )
        elif not argument.startswith('"'):
            errors.append(_cross(partition, f"cannot read the File.Contents argument {argument!r}"))
    source_columns = {
        c.name: c.prop_text("sourceColumn") for c in columns.values() if "sourceColumn" in c.props
    }
    selected = _SELECT_COLUMNS.search(source)
    if selected is not None:
        names = {n.replace('""', '"') for n in _M_STRING.findall(selected.group(1))}
        for column, source_name in source_columns.items():
            if source_name not in names:
                errors.append(
                    _cross(table, f"column {column!r}: source column {source_name!r} not selected")
                )
        for name in sorted(names - set(source_columns.values())):
            errors.append(_cross(partition, f"M selects {name!r} but no column reads it"))
    else:
        for column, source_name in source_columns.items():
            if source_name not in columns:
                errors.append(_cross(table, f"column {column!r}: no source column {source_name!r}"))
    by_source = {v: columns[k] for k, v in source_columns.items()}
    for name, m_type in _M_TYPED_PAIR.findall(source):
        expected = _M_TYPES.get(" ".join(m_type.split()))
        column = by_source.get(name.replace('""', '"'))
        actual = column.prop_text("dataType") if column is not None else None
        if expected and actual and expected != actual:
            errors.append(_cross(column, f"dataType {actual} does not match M type {m_type}"))
    return errors


def _check_relationship(rel: Node, columns) -> list[str]:
    errors: list[str] = []
    for prop_name in ("fromColumn", "toColumn"):
        table, column = parse_column_ref(rel.prop_text(prop_name))
        if table not in columns:
            errors.append(_cross(rel, f"{prop_name}: unknown table {table!r}"))
        elif column not in columns[table]:
            errors.append(_cross(rel, f"{prop_name}: {table!r} has no column {column!r}"))
    if errors:
        return errors
    from_table, from_column = parse_column_ref(rel.prop_text("fromColumn"))
    to_table, to_column = parse_column_ref(rel.prop_text("toColumn"))
    from_type = columns[from_table][from_column].prop_text("dataType")
    to_type = columns[to_table][to_column].prop_text("dataType")
    if from_type != to_type:
        errors.append(_cross(rel, f"the columns differ in data type: {from_type} and {to_type}"))
    return errors


def _check_model_facts(tables: dict[str, Node]) -> list[str]:
    errors: list[str] = []
    time_tables = [t for t in tables.values() if t.prop_text("dataCategory") == "Time"]
    if len(time_tables) != 1:
        errors.append(f"model: exactly one table needs dataCategory Time, found {len(time_tables)}")
        return errors
    time_table = time_tables[0]
    keys = [c for c in time_table.kids("column") if c.prop_bool("isKey")]
    if len(keys) != 1:
        errors.append(
            _cross(time_table, f"the date table needs one isKey column, found {len(keys)}")
        )
    elif keys[0].prop_text("dataType") != "dateTime":
        errors.append(_cross(keys[0], "the isKey column of the date table must be dateTime"))
    return errors


def _check_lineage_tags(parsed: dict[str, list[Node]]) -> list[str]:
    errors: list[str] = []
    seen: dict[str, Node] = {}

    def walk(node: Node) -> None:
        tag = node.prop_text("lineageTag")
        if tag is not None:
            if tag.lower() in seen:
                errors.append(
                    _cross(node, f"lineageTag {tag} is already used by {seen[tag.lower()].name!r}")
                )
            seen[tag.lower()] = node
        for child in node.children:
            walk(child)

    for roots in parsed.values():
        for root in roots:
            walk(root)
    return errors
