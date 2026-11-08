# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Safe arithmetic evaluator for number fields ("2*pi", "sqrt(2)/2", "10%3").

Only a whitelisted subset of Python expression syntax is accepted; there is no
attribute access, no subscripts, no names other than the constants and
functions below, and every operation runs on floats so huge integer powers
fail fast with OverflowError instead of hanging.
"""
from __future__ import annotations

import ast
import math
import operator

_CONSTS = {"pi": math.pi, "tau": math.tau, "e": math.e, "inf": math.inf}
_FUNCS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan, "asin": math.asin, "acos": math.acos,
    "atan": math.atan, "atan2": math.atan2, "sqrt": math.sqrt, "abs": abs, "min": min, "max": max,
    "round": round, "floor": math.floor, "ceil": math.ceil, "log": math.log, "log10": math.log10,
    "log2": math.log2, "exp": math.exp, "radians": math.radians, "degrees": math.degrees,
    "rad": math.radians, "deg": math.degrees, "hypot": math.hypot, "pow": math.pow,
}
_BIN = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
    ast.Pow: math.pow,
}
_UN = {ast.UAdd: operator.pos, ast.USub: operator.neg}

MAX_LEN = 256


class ExprError(ValueError):
    pass


def _ev(node):
    if isinstance(node, ast.Expression):
        return _ev(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return float(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        return float(_BIN[type(node.op)](_ev(node.left), _ev(node.right)))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
        return _UN[type(node.op)](_ev(node.operand))
    if isinstance(node, ast.Name) and node.id in _CONSTS:
        return _CONSTS[node.id]
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS
            and not node.keywords and len(node.args) <= 4):
        return float(_FUNCS[node.func.id](*[_ev(a) for a in node.args]))
    raise ExprError(f"unsupported expression: {ast.dump(node)[:40]}")


def evaluate(text: str) -> float:
    """Evaluate ``text``; raises ExprError on anything invalid."""
    s = text.strip().replace(",", ".") if text.count(",") == 1 and "(" not in text else text.strip()
    if not s:
        raise ExprError("empty")
    if len(s) > MAX_LEN:
        raise ExprError("too long")
    s = s.replace("^", "**")
    try:
        tree = ast.parse(s, mode="eval")
        v = _ev(tree)
    except ExprError:
        raise
    except (SyntaxError, ValueError, ZeroDivisionError, OverflowError, TypeError) as exc:
        raise ExprError(str(exc)) from None
    if v != v:
        raise ExprError("not a number")
    return v
