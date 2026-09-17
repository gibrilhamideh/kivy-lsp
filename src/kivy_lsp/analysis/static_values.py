from __future__ import annotations

import ast


def expression_annotation(source: str) -> str | None:
    try:
        expression = ast.parse(
            source,
            mode="eval",
        ).body
    except SyntaxError:
        return None

    return _node_annotation(expression)


def _node_annotation(node: ast.expr) -> str | None:
    if isinstance(node, ast.Constant):
        value = node.value

        if value is None:
            return "None"

        if isinstance(value, bool):
            return "bool"

        if isinstance(value, int):
            return "int"

        if isinstance(value, float):
            return "float"

        if isinstance(value, str):
            return "str"

        return None

    if isinstance(node, ast.JoinedStr):
        return "str"

    if isinstance(node, ast.List):
        return "list[Any]"

    if isinstance(node, ast.Tuple):
        return "tuple[Any, ...]"

    if isinstance(node, ast.Dict):
        return "dict[Any, Any]"

    if isinstance(node, ast.Set):
        return "set[Any]"

    if isinstance(node, ast.Lambda):
        return "Callable[..., Any]"

    if isinstance(node, ast.IfExp):
        first = _node_annotation(node.body)
        second = _node_annotation(node.orelse)

        if first is None or second is None:
            return None

        if first == second:
            return first

        return f"{first} | {second}"

    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.operand, ast.Constant)
    ):
        value = node.operand.value

        if isinstance(value, int) and not isinstance(value, bool):
            return "int"

        if isinstance(value, float):
            return "float"

    return None


def annotation_references(annotation: str) -> tuple[str, ...]:
    annotation = annotation.strip().strip("'\"")

    if annotation.startswith("Optional[") and annotation.endswith("]"):
        annotation = annotation[9:-1]

    references: list[str] = []

    for part in annotation.split("|"):
        reference = part.strip()

        if reference in {"", "None", "NoneType"}:
            continue

        references.append(reference)

    return tuple(references)


