"""pydantic → TypeScript.

The direction of generation reversed with the migration. When the backend was
TypeScript, `shared/contracts/src` was hand-authored and Python did not exist;
now no backend consumer is TypeScript, so the pydantic models are the source of
truth and the `.ts` is derived. `frontend/call` imports the output and must not
notice that anything moved.

It reads the *source* rather than the runtime models, because the field
comments are the point: `types.ts` is the file that explains why a protocol has
six blocks and which four never reach a prompt. A generator that dropped those
comments would technically work and would quietly destroy the artefact.

    uv run python scripts/gen_contracts.py          # write
    uv run python scripts/gen_contracts.py --check  # fail if stale

`tests/test_contracts_generated.py` runs the check.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "shared" / "contracts" / "src"

HEADER = """/*
 * GENERATED FILE — do not edit by hand.
 *
 * Generated from the pydantic models by `scripts/gen_contracts.py`.
 * pydantic is the single source of truth; run the generator and commit
 * the result. `tests/test_contracts_generated.py` fails when this file
 * is stale.
 */
"""

#: Models that exist only to give a discriminated union its members. They are
#: emitted as part of the union rather than as interfaces of their own.
INLINE_ONLY = {
    "TextCapture",
    "EnumCapture",
    "NumberCapture",
    "BooleanCapture",
    "DateCapture",
}

SKIP_CLASSES = {"CamelModel"}


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def ts_type(annotation: str) -> str:
    """Map a Python annotation, as written, onto TypeScript."""
    a = annotation.strip()

    if m := re.fullmatch(r"Literal\[(.+)\]", a, re.S):
        parts = [p.strip() for p in _split_top(m.group(1))]
        return " | ".join(_ts_literal(p) for p in parts)

    if m := re.fullmatch(r"list\[(.+)\]", a, re.S):
        inner = ts_type(m.group(1))
        return f"({inner})[]" if " " in inner else f"{inner}[]"

    if m := re.fullmatch(r"dict\[(.+?),(.+)\]", a, re.S):
        return f"Record<{ts_type(m.group(1))}, {ts_type(m.group(2))}>"

    if "|" in a:
        return " | ".join(ts_type(p) for p in _split_top(a, sep="|"))

    return {
        "str": "string",
        "int": "number",
        "float": "number",
        "bool": "boolean",
        "None": "null",
        # ISO-8601 on the wire, because that is what pydantic serialises a
        # datetime to and what `new Date(...)` on the other side accepts. A
        # bare `date` is the same story one field shorter — "1951-03-14".
        "datetime": "string",
        "date": "string",
        "object": "unknown",
        "Any": "unknown",
    }.get(a, a)


def _ts_literal(value: str) -> str:
    v = value.strip()
    if v in {"True", "False"}:
        return v.lower()
    return "'" + v.strip("\"'") + "'"


def _split_top(text: str, sep: str = ",") -> list[str]:
    """Split on `sep` at bracket depth zero."""
    out, depth, current = [], 0, ""
    for ch in text:
        if ch in "[(":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == sep and depth == 0:
            out.append(current)
            current = ""
        else:
            current += ch
    out.append(current)
    return [p for p in (s.strip() for s in out) if p]


def field_comments(lines: list[str], lineno: int) -> list[str]:
    """The `#:` block immediately above a field, in source order."""
    out = []
    i = lineno - 2
    while i >= 0 and lines[i].strip().startswith("#:"):
        out.append(lines[i].strip()[2:].strip())
        i -= 1
    return list(reversed(out))


def doc_block(node: ast.ClassDef) -> list[str]:
    """The class docstring as JSDoc.

    Rendered in full rather than first-line-only: these comments are the reason
    the file is worth reading, and half a sentence is worse than none.
    """
    doc = ast.get_docstring(node)
    if not doc:
        return []
    lines = [ln.strip() for ln in doc.strip().splitlines()]
    if len(lines) == 1:
        return [f"/** {lines[0]} */"]
    out = ["/**"]
    out += [f" * {ln}".rstrip() for ln in lines]
    out.append(" */")
    return out


def render_fields(node: ast.ClassDef, lines: list[str], indent: str = "  ") -> list[str]:
    out: list[str] = []
    for item in node.body:
        if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
            continue
        name = item.target.id
        if name == "model_config":
            continue
        annotation = ast.get_source_segment("\n".join(lines), item.annotation) or "unknown"
        rendered = ts_type(annotation)
        optional = item.value is not None and annotation.endswith("| None")
        if optional:
            rendered = " | ".join(p for p in rendered.split(" | ") if p != "null")
        for comment in field_comments(lines, item.lineno):
            out.append(f"{indent}/** {comment} */")
        out.append(f"{indent}{camel(name)}{'?' if optional else ''}: {rendered};")
    return out


def inline_object(node: ast.ClassDef, lines: list[str]) -> str:
    parts = []
    for item in node.body:
        if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
            continue
        annotation = ast.get_source_segment("\n".join(lines), item.annotation) or "unknown"
        optional = item.value is not None and annotation.endswith("| None")
        rendered = ts_type(annotation)
        if optional:
            rendered = " | ".join(p for p in rendered.split(" | ") if p != "null")
        parts.append(f"{camel(item.target.id)}{'?' if optional else ''}: {rendered}")
    return "{ " + "; ".join(parts) + " }"


def generate(source: Path, imports: str = "") -> str:
    text = source.read_text(encoding="utf-8")
    lines = text.splitlines()
    tree = ast.parse(text)

    classes = {
        n.name: n
        for n in tree.body
        if isinstance(n, ast.ClassDef) and n.name not in SKIP_CLASSES
    }

    chunks: list[str] = [HEADER]
    if imports:
        chunks.append(imports)

    for name, node in classes.items():
        if name in INLINE_ONLY:
            continue
        block: list[str] = list(doc_block(node))
        block.append(f"export interface {name} {{")
        block.extend(render_fields(node, lines))
        block.append("}")
        chunks.append("\n".join(block))

    # Module-level aliases: `Capture = Annotated[A | B, ...]`, `FieldStatus = Literal[...]`
    for item in tree.body:
        if not isinstance(item, ast.Assign) or len(item.targets) != 1:
            continue
        target = item.targets[0]
        if not isinstance(target, ast.Name) or not target.id[0].isupper():
            continue
        rhs = (ast.get_source_segment(text, item.value) or "").strip()
        if rhs.startswith("Annotated["):
            # `Annotated[A | B, Field(discriminator=...)]` — keep the union,
            # drop the pydantic metadata and the bracket it lives in.
            rhs = rhs[len("Annotated[") :]
            rhs = re.sub(r",\s*Field\(.*?\)\s*,?\s*\]$", "", rhs, flags=re.S).strip()
            rhs = rhs.rstrip("]").strip() if rhs.endswith("]") and "[" not in rhs else rhs

        members = _split_top(rhs, sep="|")
        if all(m in classes for m in members) and members:
            body = "\n".join(f"  | {inline_object(classes[m], lines)}" for m in members)
            chunks.append(f"export type {target.id} =\n{body};")
        else:
            chunks.append(f"export type {target.id} = {ts_type(rhs)};")

    return "\n\n".join(chunks).rstrip() + "\n"


WIRE_TAIL = """
export const encodeMessage = (m: ServerMessage | ClientMessage): Uint8Array =>
  new TextEncoder().encode(JSON.stringify(m));

export const decodeMessage = <T>(d: Uint8Array): T =>
  JSON.parse(new TextDecoder().decode(d)) as T;
"""


def build() -> dict[Path, str]:
    types_ts = generate(ROOT / "shared" / "contracts" / "models.py")
    wire_ts = generate(
        ROOT / "shared" / "contracts" / "wire.py",
        imports="import type { Clinician, FieldState, CallPhase } from './types.js';",
    )
    # `encodeMessage`/`decodeMessage` have no pydantic counterpart worth
    # generating — they are two lines of browser plumbing, not a contract.
    wire_ts = wire_ts.rstrip() + "\n" + WIRE_TAIL
    index_ts = (
        HEADER
        + "\nexport * from './types.js';\nexport * from './wire.js';\n"
    )
    return {
        OUT / "types.ts": types_ts,
        OUT / "wire.ts": wire_ts,
        OUT / "index.ts": index_ts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    stale = []
    for path, content in build().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == content:
            continue
        if args.check:
            stale.append(path.relative_to(ROOT))
        else:
            path.write_text(content, encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)}")

    if stale:
        print("stale generated contracts:", ", ".join(str(p) for p in stale), file=sys.stderr)
        print("run: uv run python scripts/gen_contracts.py", file=sys.stderr)
        return 1
    if args.check:
        print("contracts are up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
