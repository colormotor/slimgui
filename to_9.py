#!/usr/bin/env python3

#!/usr/bin/env python3
import argparse
import os
from pathlib import Path

import libcst as cst
import libcst.matchers as m


def _flatten_bitor(expr: cst.BaseExpression):
    """Flatten A | B | C into [A, B, C]."""
    out = []

    def rec(e):
        if isinstance(e, cst.BinaryOperation) and isinstance(e.operator, cst.BitOr):
            rec(e.left)
            rec(e.right)
        else:
            out.append(e)

    rec(expr)
    return out


def _is_none_expr(e: cst.BaseExpression) -> bool:
    # In annotations, `None` appears as Name("None")
    return isinstance(e, cst.Name) and e.value == "None"


def _make_typing_subscript(name: str, items: list[cst.BaseExpression]) -> cst.Subscript:
    return cst.Subscript(
        value=cst.Attribute(value=cst.Name("typing"), attr=cst.Name(name)),
        slice=[
            cst.SubscriptElement(slice=cst.Index(value=items[0]))
            if len(items) == 1
            else cst.SubscriptElement(
                slice=cst.Index(
                    value=cst.Tuple(elements=[cst.Element(value=i) for i in items])
                )
            )
        ],
    )


def _rewrite_union(expr: cst.BaseExpression) -> cst.BaseExpression:
    parts = _flatten_bitor(expr)

    non_none = [p for p in parts if not _is_none_expr(p)]
    has_none = any(_is_none_expr(p) for p in parts)

    # X | None  -> typing.Optional[X]
    if has_none and len(non_none) == 1:
        return _make_typing_subscript("Optional", [non_none[0]])

    # A | B | C -> typing.Union[A, B, C]
    # (If it included None but also others, keep it as Union[..., None])
    return _make_typing_subscript("Union", parts)


class Pep604ToTyping(cst.CSTTransformer):
    def __init__(self):
        self.saw_import_typing = False

    # Detect existing "import typing"
    def visit_Import(self, node: cst.Import) -> bool:
        for n in node.names:
            if isinstance(n.name, cst.Name) and n.name.value == "typing":
                self.saw_import_typing = True
        return True

    # Only rewrite inside annotations
    def leave_Annotation(self, original_node: cst.Annotation, updated_node: cst.Annotation) -> cst.Annotation:
        ann = updated_node.annotation
        if isinstance(ann, cst.BinaryOperation) and isinstance(ann.operator, cst.BitOr):
            ann = _rewrite_union(ann)
            return updated_node.with_changes(annotation=ann)
        return updated_node

    def leave_Module(self, original_node: cst.Module, updated_node: cst.Module) -> cst.Module:
        if self.saw_import_typing:
            return updated_node

        # Insert "import typing" after future imports and module docstring (if any)
        body = list(updated_node.body)

        insert_at = 0
        # Skip module docstring statement if present
        if body and m.matches(body[0], m.SimpleStatementLine(body=[m.Expr(value=m.SimpleString())])):
            insert_at = 1

        # Skip any from __future__ imports right after that
        while insert_at < len(body) and m.matches(
            body[insert_at],
            m.SimpleStatementLine(body=[m.ImportFrom(module=m.Name("__future__"))]),
        ):
            insert_at += 1

        import_typing = cst.SimpleStatementLine(body=[cst.Import(names=[cst.ImportAlias(name=cst.Name("typing"))])])
        body.insert(insert_at, import_typing)
        return updated_node.with_changes(body=body)


def process_file(path: Path, in_place: bool) -> bool:
    src = path.read_text(encoding="utf-8")
    mod = cst.parse_module(src)
    transformer = Pep604ToTyping()
    new_mod = mod.visit(transformer)
    out = new_mod.code

    if out == src:
        return False

    if in_place:
        path.write_text(out, encoding="utf-8")
    else:
        print(out)
    return True


def iter_py_files(root: Path):
    if root.is_file() and root.suffix == ".py":
        yield root
        return
    for p in root.rglob("*.py"):
        yield p


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path", help="File or directory")
    ap.add_argument("--in-place", action="store_true", help="Rewrite files in place")
    args = ap.parse_args()

    root = Path(args.path)
    changed = 0
    for f in iter_py_files(root):
        if process_file(f, in_place=args.in_place):
            changed += 1
            print(f"rewrote: {f}")

    print(f"done. files changed: {changed}")


if __name__ == "__main__":
    main()
