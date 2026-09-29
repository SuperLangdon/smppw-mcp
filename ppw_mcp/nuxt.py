"""Nuxt 3 __NUXT_DATA__ 扁平 payload 还原。

SSR 页面把组件初始状态序列化为一个数组：对象/数组的槽位值是
指向数组其他位置的整数引用，叶子位置才是真实值（字符串/数字/
null）。数组形如 ["ShallowReactive", 1] 的元素是类型包装标记。
"""

from __future__ import annotations

import json
import re

_WRAPPERS = {"ShallowReactive", "ShallowRef", "Ref", "EmptyRef", "Null", "Lazy"}

_NUXT_RE = re.compile(
    r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S
)


def extract_payload(html: str) -> list | None:
    m = _NUXT_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def hydrate(payload: list, slot, depth: int = 0):
    """按槽位还原值。depth 只作为保险丝，正常数据远浅于上限。"""
    if depth > 30:
        return slot
    if not isinstance(slot, int) or isinstance(slot, bool):
        return slot
    if not (0 <= slot < len(payload)):
        return slot
    v = payload[slot]
    if (
        isinstance(v, list)
        and v
        and isinstance(v[0], str)
        and v[0] in _WRAPPERS
    ):
        return hydrate(payload, v[1], depth + 1) if len(v) > 1 else None
    if isinstance(v, dict):
        return {k: hydrate(payload, x, depth + 1) for k, x in v.items()}
    if isinstance(v, list):
        return [hydrate(payload, x, depth + 1) for x in v]
    return v


def hydrate_root(payload: list) -> dict | None:
    """还原根对象。返回形如 {"data": {...路由节点...}, ...} 的结构。"""
    if not payload:
        return None
    root = hydrate(payload, 1)
    return root if isinstance(root, dict) else None


def find_data_node(root: dict) -> dict | None:
    """定位页面数据节点，向下递归穿过 store 分片/路由 key/API 包装层。

    - 排行页: data -> {"rank": {...api响应}} -> {"data": {"list": [...]}}
    - 详情页: data -> {"<路由key>": {baseInfo/marketInfo/...}}
    """
    data = root.get("data")
    if not isinstance(data, dict):
        return None
    return _descend(data, 0)


def _descend(node: dict, depth: int) -> dict | None:
    if depth > 5:
        return None
    if any(k in node for k in ("list", "baseInfo")):
        return node
    for v in node.values():
        if isinstance(v, dict):
            found = _descend(v, depth + 1)
            if found is not None:
                return found
    return None
