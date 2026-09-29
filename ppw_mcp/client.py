"""排排网 HTTP 客户端：SSR 页面抓取 + sppwapi 调用 + 登录态管理。

登录态由单个 cookie ``http_tK_cache`` 承载（调研结论）：
登录排排网任意站点后从浏览器复制该 cookie 值即可，其余 cookie
（统计、WAF、前端展示标志）不影响数据可见性。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import time
from typing import Any

import httpx

from . import nuxt
from .crypto import decode_response_payload

_TAG_RE = re.compile(r"<[^>]+>")

DC = "https://dc.simuwang.com"
WWW = "https://www.simuwang.com"
API = "https://sppwapi.simuwang.com/sun"

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)

CONFIG_PATH = pathlib.Path.home() / ".ppw-mcp.json"
ENV_TOKEN = "PPW_TOKEN"


class AuthError(RuntimeError):
    """登录态缺失或失效。"""


def load_token() -> str:
    if tok := os.environ.get(ENV_TOKEN):
        return tok.strip()
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text("utf-8")).get("token", "")
        except (json.JSONDecodeError, OSError):
            pass
    return ""


def save_token(token: str) -> None:
    CONFIG_PATH.write_text(
        json.dumps({"token": token.strip()}, ensure_ascii=False, indent=2),
        "utf-8",
    )


class PPWClient:
    def __init__(self, token: str | None = None):
        self._token = (token or load_token()).strip()
        self._http = httpx.Client(
            headers={
                "User-Agent": _UA,
                "Referer": f"{DC}/",
                "Accept": "text/html,application/xhtml+xml,application/json",
            },
            follow_redirects=True,
            timeout=30,
        )

    # -- 基础访问 ---------------------------------------------------

    @property
    def token(self) -> str:
        return self._token

    def set_token(self, token: str) -> None:
        self._token = token.strip()
        save_token(self._token)

    def _cookies(self) -> dict[str, str]:
        if not self._token:
            raise AuthError(
                "未配置排排网登录态：请先 set_token 写入浏览器中的 "
                "http_tK_cache cookie 值"
            )
        return {"http_tK_cache": self._token}

    def _get(self, url: str, *, referer: str | None = None,
             params: dict | None = None) -> httpx.Response:
        headers = {"Referer": referer} if referer else None
        r = self._http.get(url, params=params, cookies=self._cookies(),
                           headers=headers)
        r.raise_for_status()
        return r

    # -- SSR 页面 ---------------------------------------------------

    def _ssr_node(self, url: str) -> dict | None:
        html = self._get(url).text
        payload = nuxt.extract_payload(html)
        if payload is None:
            return None
        root = nuxt.hydrate_root(payload)
        if root is None:
            return None
        return nuxt.find_data_node(root)

    # -- sppwapi ----------------------------------------------------

    def api(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """调用 sppwapi 并解密响应。path 形如 ``/search/mtSearch``。"""
        referer = f"{WWW}/"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        r = self._get(f"{API}{path}", referer=referer, params=params)
        body = r.json()
        data = body.get("data") if isinstance(body, dict) else None
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                pass
        decoded = decode_response_payload(data)
        if isinstance(decoded, dict):
            inner = decoded.get("data", decoded)
            return inner if inner is not None else decoded
        return decoded

    # -- 业务方法 ---------------------------------------------------

    def check_auth(self) -> dict:
        """验证登录态：排行页出现"认证可见"占位即视为未认证。"""
        if not self._token:
            return {"ok": False, "reason": "未配置 token"}
        try:
            html = self._get(f"{DC}/smph/a0").text
        except httpx.HTTPError as e:
            return {"ok": False, "reason": f"请求失败: {e}"}
        masked = html.count("认证可见")
        return {
            "ok": masked == 0,
            "reason": "" if masked == 0
            else f"登录态无效或已过期（页面出现 {masked} 处'认证可见'遮蔽）",
        }

    def search(self, query: str, search_type: str = "fund",
               page: int = 1, page_size: int = 20) -> list[dict]:
        """站内搜索。search_type: fund | company | manager。

        返回前剥离站方加在名称字段上的 <b> 高亮标签。
        """
        out = self.api("/search/mtSearch", {
            "q": query,
            "search_type": search_type,
            "page_size": page_size,
            "page": page,
            "timeStamp": int(time.time() * 1000),
        })
        if isinstance(out, dict):
            rows = out.get("data") or out.get("list") or []
        else:
            rows = out or []
        cleaned = []
        for r in rows:
            if isinstance(r, dict):
                r = {k: _TAG_RE.sub("", v) if isinstance(v, str) else v
                     for k, v in r.items()}
            cleaned.append(r)
        return cleaned

    def ranking(self, page: int = 1, size: int = 50) -> dict:
        """私募排行榜（sppwapi fundRankV3，明文 query + 加密响应）。

        每页最多 50 条，含期末净值 nav 与各期收益，可翻页（total 约 3460）。
        """
        return self.api("/ranking/fundRankV3", {
            "page": page,
            "size": size,
            "sort_name": "ret_6m",
            "sort_asc": "desc",
            "condition": '{"first_strategy":"1001","fund_type":"6"}',
            "tab_type": 1,
            "frequency": "week",
        })

    def fund_nav(self, fund_id: str) -> dict | None:
        """查单只基金的当前净值（fundRankV3 keyword 过滤 + fund_id 精确匹配）。

        覆盖排行榜内全部基金（约 3460 只，含净值与各期收益）；
        未上榜基金返回 None（站方对详情页净值做了图片化）。
        """
        detail = self.fund_detail(fund_id)
        name = (detail.get("baseInfo") or {}).get("fund_short_name") or ""
        if not name:
            return None
        data = self.api("/ranking/fundRankV3", {
            "page": 1, "size": 50, "sort_name": "ret_6m", "sort_asc": "desc",
            "condition": json.dumps({
                "keyword": name, "first_strategy": "1001", "fund_type": "6",
            }, ensure_ascii=False, separators=(",", ":")),
            "tab_type": 1, "frequency": "week",
        })
        for r in data.get("list") or []:
            if r.get("fund_id") == fund_id:
                return {
                    "nav": r.get("nav"),
                    "price_date": r.get("price_date"),
                    "ret_ytd": r.get("ret_ytd"),
                    "ret_incep": r.get("ret_incep"),
                }
        return None

    def fund_detail(self, fund_id: str) -> dict:
        """基金详情（SSR：基本信息/公司/托管/策略要素，净值数字站方做了图片化）。"""
        node = self._ssr_node(f"{DC}/product/{fund_id}.html")
        if not node or "baseInfo" not in node:
            raise RuntimeError(f"产品页数据节点解析失败: {fund_id}")
        return node

    def fund_index(self, fund_id: str) -> Any:
        """当前净值与风险收益指标（sppwapi fundIndexInfoV2）。"""
        return self.api("/fund/fundIndexInfoV2", {
            "id": fund_id,
            "index_id": "IN0000008S",
            "time_range": "FromSetup",
        })

    def _user_id(self) -> int:
        """当前登录用户数字 ID（部分接口必需，如 performanceRangeV2）。"""
        cached = getattr(self, "_uid", None)
        if cached:
            return cached
        info = self.api("/member/getUserInfoApi", {"sign": ""})
        uid = info.get("uid") if isinstance(info, dict) else None
        if not uid:
            raise AuthError("无法获取用户 ID，登录态可能已过期")
        self._uid = int(uid)
        return self._uid

    def performance_ranges(self, fund_id: str, range_code: int = 1) -> Any:
        """区间业绩（sppwapi performanceRangeV2，明文 query + 加密响应）。

        range_code: 1=区间汇总(成立来/今年/各年期) 2=年度 3=季度 4=月度，
        均含基准对照，汇总与年度还含同类排名。
        """
        return self.api("/fund/performanceRangeV2", {
            "id": fund_id,
            "compare_id": "IN0000008S",
            "range": range_code,
            "excess_type": 2,
            "USER_ID": self._user_id(),
        })

    def win_statistics(self, fund_id: str) -> Any:
        """周度胜率统计（sppwapi winStatistics）。"""
        return self.api("/fund/winStatistics", {
            "fund_id": fund_id,
            "compare_id": "IN0000008S",
            "frequency": "Weekly",
            "start_date": "",
            "end_date": "",
            "range": 0,
        })

    def asset_size_list(self, fund_id: str) -> Any:
        """基金规模变动序列（sppwapi assetSizeList）。"""
        return self.api("/Fund/assetSizeList", {
            "id": fund_id,
            "period": 0,
            "start_date": "",
            "end_date": "",
            "USER_ID": self._user_id(),
        })
