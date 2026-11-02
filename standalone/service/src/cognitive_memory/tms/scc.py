# -*- coding: utf-8 -*-
"""Tarjan strongly connected components for TMS cycle handling."""

from __future__ import annotations

from typing import Iterable


def strongly_connected_components(
    nodes: Iterable[str],
    edges: dict[str, set[str]],
) -> list[list[str]]:
    """Return SCCs. `edges[u]` = successors of u (u → v)."""
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    result: list[list[str]] = []
    node_set = set(nodes)

    def strongconnect(v: str) -> None:
        nonlocal index
        indices[v] = index
        lowlink[v] = index
        index += 1
        stack.append(v)
        on_stack.add(v)
        for w in edges.get(v, set()):
            if w not in node_set:
                continue
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])
        if lowlink[v] == indices[v]:
            comp: list[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            result.append(comp)

    for n in node_set:
        if n not in indices:
            strongconnect(n)
    return result
