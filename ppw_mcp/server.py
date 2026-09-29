"""私募排排网 MCP server。

可查询站内搜索、排行榜、基金详情、区间业绩。数据来源为排排网
dc.simuwang.com（SSR 页面）与 sppwapi.simuwang.com（加密 JSON API，
解密逻辑见 crypto.py）。

登录态：浏览器登录排排网后，将 cookie 中 ``http_tK_cache`` 的值
通过 set_token 工具写入（保存于 ~/.ppw-mcp.json），或预先设置
环境变量 PPW_TOKEN。
"""

from __future__ import annotations

import logging

from mcp.server.mcpserver import MCPServer

from .client import PPWClient

mcp = MCPServer("ppw-mcp")

# 排行榜每行字段中挑选对查询最有用的子集，避免 50 行 × 全字段刷屏
_RANK_FIELDS = (
    "fund_id fund_short_name strategy substrategy company_short_name "
    "company_id nav price_date ret_incep ret_ytd ret_1y ret_3y ret_5y "
    "annual_ret max_drawdown sharpe setdate fund_status"
).split()

_FUND_FIELDS = (
    "fund_id fund_name fund_short_name fund_status strategy substrategy "
    "inception_date initial_unit_value nav_frequency open_day "
    "performance_disclosure_mark trust_short_name custodian_short_name "
    "register_number company_register_number investment_scope"
).split()

_COMPANY_FIELDS = (
    "company_id company_name company_short_name company_asset_size_text "
    "establish_date registered_city registered_capital paid_capital "
    "employee_cnts fund_practitioners_count business_type key_figure_name "
    "amac_link company_class_text risk_warning company_profile"
).split()


def _client() -> PPWClient:
    return PPWClient()


def _pick(d: dict, fields: list[str]) -> dict:
    return {k: d.get(k) for k in fields if d.get(k) not in (None, "", [])}


@mcp.tool()
def set_token(token: str) -> str:
    """保存排排网登录态（浏览器 cookie 中 http_tK_cache 的值）。

    获取方法：浏览器登录 simuwang.com 后，F12 -> 应用 -> Cookie ->
    找到 http_tK_cache，复制其值传入。保存到 ~/.ppw-mcp.json，
    过期后重新登录再更新一次即可。
    """
    _client().set_token(token)
    return "已保存。可用 check_auth 验证登录态。"


@mcp.tool()
def check_auth() -> dict:
    """验证排排网登录态是否有效（合格投资者数据是否解锁）。"""
    return _client().check_auth()


@mcp.tool()
def search_funds(query: str, page_size: int = 10) -> dict:
    """按名称搜索私募基金，返回 fund_id、公司、经理、成立日、
    今年/成立以来收益、最大回撤等摘要。后续用 get_fund 查详情。"""
    rows = _client().search(query, "fund", page_size=page_size)
    out = []
    for r in rows:
        out.append(_pick(r, [
            "fund_id", "fund_short_name", "fund_name", "company_short_name",
            "company_name", "fund_manager_name", "strategy",
            "inception_date", "price_date", "ret_ytd", "ret_incep",
            "ret_incep_a", "maxdrawdown_incep", "register_number",
            "fund_status", "trust_id",
        ]))
    return {"count": len(out), "funds": out}


@mcp.tool()
def search_companies(query: str, page_size: int = 10) -> dict:
    """按名称搜索私募公司，返回 company_id 与基本信息。"""
    rows = _client().search(query, "company", page_size=page_size)
    return {"count": len(rows), "companies": rows}


@mcp.tool()
def get_ranking(page: int = 1) -> dict:
    """获取私募排行榜（默认近半年收益排序，每页 50 条，共约 3460 只
    可翻页）。每条含 fund_id、期末净值 nav、成立来/今年/近半年~近五年
    各期收益，可直接用作基金当前单位净值来源。"""
    node = _client().ranking(page=page)
    rows = [_pick(r, _RANK_FIELDS) for r in (node.get("list") or [])]
    return {
        "page": page,
        "total": node.get("total"),
        "price_date": node.get("price_date"),
        "funds": rows,
    }


@mcp.tool()
def get_fund(fund_id: str) -> dict:
    """查询基金详情（fund_id 形如 HF0000DEHO，可先用 search_funds 检索）。

    返回当前净值 nav（含净值日期、今年以来/成立以来收益）、基本信息
    （策略、成立日、开放日、托管人、备案号、投资范围）、管理公司与经理。
    nav 为 None 表示该基金未进排行榜（站方对详情页净值做了图片化）。
    """
    node = _client().fund_detail(fund_id)
    base = _pick(node.get("baseInfo") or {}, _FUND_FIELDS)
    company = _pick(node.get("companyInfo") or {}, _COMPANY_FIELDS)
    out = {"base": base, "company": company}
    managers = node.get("relationManager") or []
    if isinstance(managers, dict):
        managers = [managers]
    picked = [
        _pick(m, [
            "personnel_id", "personnel_name", "investment_experience",
            "education", "profession_background", "expertise_area",
            "ret_tenure", "ret_incep",
        ])
        for m in managers if isinstance(m, dict)
    ]
    if picked:
        out["managers"] = picked
    nav_info = _client().fund_nav(fund_id)
    if nav_info:
        out["nav"] = nav_info
    else:
        out["nav"] = None  # 未上榜基金无明文净值（站方图片化）
    return out


@mcp.tool()
def get_fund_performance(fund_id: str) -> dict:
    """查询基金当前指标与区间业绩汇总（净值日期、收益/回撤/夏普等指标，
    以及成立来/今年/近一月~近五年各区间收益与基准对照、同类排名）。"""
    c = _client()
    return {
        "index": c.fund_index(fund_id),
        "ranges": c.performance_ranges(fund_id, 1),
        "win_statistics": c.win_statistics(fund_id),
    }


_GRANULARITY = {"summary": 1, "yearly": 2, "quarterly": 3, "monthly": 4}


@mcp.tool()
def get_fund_returns(fund_id: str, granularity: str = "monthly") -> dict:
    """查询基金收益序列（数据来自 sppwapi performanceRangeV2，明文可解）。

    granularity: monthly=月度收益序列 | quarterly=季度 | yearly=年度
    （含基准对照；年度还含同类排名与四分位）。
    配合初始净值 1.0 可合成累计净值走势。
    """
    code = _GRANULARITY.get(granularity)
    if code is None:
        return {"error": f"granularity 须为 {'/'.join(_GRANULARITY)}"}
    return _client().performance_ranges(fund_id, code)


@mcp.tool()
def get_fund_asset_size(fund_id: str) -> dict:
    """查询基金规模变动序列（各期规模与环比变化）。"""
    data = _client().asset_size_list(fund_id)
    rows = data.get("list", []) if isinstance(data, dict) else []
    return {"count": len(rows), "list": rows[:60]}


def main() -> None:
    # httpx 的请求日志默认 INFO 且逐条打印，stdio 模式下保持安静
    logging.getLogger("httpx").setLevel(logging.WARNING)
    mcp.run()


if __name__ == "__main__":
    main()
