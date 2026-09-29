# smppw-mcp

私募排排网（simuwang.com）MCP Server。提供 9 个结构化工具，将基金/公司查询、排行榜分页、净值与产品要素、风险收益指标、月/季/年度收益序列、规模变动等查询能力接入 LLM/Agent。

兼容 Antigravity、Claude Code、Cursor、Copilot 等支持 MCP 的 Agent。当前仅在私募排排网个人版账号中验证通过。

## 在开始之前

请先注册 [私募排排网](https://www.simuwang.com) 账号，并完成合格投资者认证。

合格投资者应具备相应的风险识别能力和风险承受能力，具有 2 年以上投资经历，并满足以下任一条件：

1. 近 3 年个人年均收入不低于 50 万元；

2. 个人金融资产不低于 300 万元，或家庭金融净资产不低于 300 万元，或家庭金融资产不低于 500 万元。

## 工具

| 工具 | 说明 |
| --- | --- |
| `set_token` | 保存登录态（浏览器 cookie 中 `http_tK_cache` 的值），写入 `~/.ppw-mcp.json` |
| `check_auth` | 验证登录态是否有效 |
| `search_funds` | 按名称搜索基金（fund_id、公司、经理、收益、回撤摘要） |
| `search_companies` | 按名称搜索私募公司 |
| `get_ranking(page)` | 排行榜（可翻页，约 3460 只），每条含期末净值 nav + 各期收益 |
| `get_fund` | 基金详情：**当前净值（含日期、今年以来/成立以来收益）** + 策略/托管/备案等要素 + 公司与经理 |
| `get_fund_performance` | 指标（收益/年化/Alpha/回撤/夏普/贝塔）+ 各区间收益与基准对照 + 周胜率 |
| `get_fund_returns` | 收益序列：月度 / 季度 / 年度（含基准对照，年度含同类排名） |
| `get_fund_asset_size` | 基金规模变动序列 |

典型用法：`search_funds` 找到 fund_id → `get_fund` 看净值与要素 → 需要时拉收益序列或指标。

## 快速开始

要求 Python ≥ 3.10。

```bash
git clone https://github.com/SuperLangdon/smppw-mcp.git
cd smppw-mcp
pip install -e .
```

### 获取登录态（一次性）

查询净值与业绩需要排排网合格投资者认证账号：

1. 浏览器登录 simuwang.com（需已完成合格投资者认证）
2. F12 → 应用 → Cookie → 复制 `http_tK_cache` 的值
3. 两种方式提供给 server：
   - 对 MCP 客户端说 `set_token <粘贴值>`（保存到 `~/.ppw-mcp.json`）
   - 或设置环境变量 `PPW_TOKEN`

登录态过期后重新登录、更新一次即可。

### MCP 客户端配置

```json
{
  "mcpServers": {
    "ppw": {
      "command": "python",
      "args": ["-m", "ppw_mcp.server"],
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

`pip install -e .` 会同时生成命令行入口，也可以直接写 `"command": "smppw-mcp"`。

## 技术说明

数据来源两条通道：

1. **SSR 页面**（`dc.simuwang.com`）：Nuxt 3 服务端渲染，数据在 `__NUXT_DATA__` 扁平 payload 中明文内嵌，由 `ppw_mcp/nuxt.py` 还原。
2. **sppwapi**（`sppwapi.simuwang.com/sun/*`）：JSON API，响应为动态 AES 加密。`ppw_mcp/crypto.py` 复刻了站点前端的解密链：响应携带混淆 JS 形式的动态 key → 按 `encode` 编号变形 → `MD5(key)` 作 AES-256-CBC key、其后 16 位作 IV → 双重 base64 密文解密。

登录态由单个 cookie `http_tK_cache` 承载；部分接口需要 USER_ID，
由 `getUserInfoApi` 自动获取并缓存。

## 已知限制

- **当前净值的覆盖范围**：`get_fund` 的净值来自排行榜接口（`fundRankV3` keyword 反查），覆盖榜单内约 3460 只基金；未上榜基金（无有效业绩区间）详情页净值被站方图片化，返回 null。
- **日度净值曲线**（`fundNavTrend`）请求参数为 `sdata` 加密（算法已逆向：AES key = MD5(cookie `8hIn9IA`)，双层 base64），但服务端对该请求做了会话/指纹级绑定，浏览器外重放会被拒绝（"参数错误 1"）。已用 `performanceRangeV2` 提供月度/季度/年度收益序列；配合初始净值 1.0 可合成累计净值走势。
- 登录态 `http_tK_cache` 会被服务端轮换，保存的旧值通常继续有效；若 `check_auth` 报失效，重新登录后更新一次 token。
- 站方若调整加密方案或页面结构，`crypto.py` / `nuxt.py` 需同步更新。

## 免责声明

- 本项目仅供**个人学习研究**与查询**账号权限内**的数据，请勿用于批量抓取、商业用途或向他人提供数据服务。
- 私募基金业绩与其他非公开信息仅向合格投资者展示为监管要求（见 [《私募投资基金监督管理暂行办法》](https://www.csrc.gov.cn/csrc/c106256/c1653981/content.shtml)），使用时请务必确保合规性。
- 数据归私募排排网所有，本项目不对数据的准确性、完整性作任何保证；过往业绩不预示未来表现，不构成投资建议。
- 本项目的逆向分析仅用于实现个人账号数据的程序化读取，不试图绕过任何付费机制、登录权限或访问控制。本项目为非官方、非授权项目，具有一定逆向工程与自动化访问性质，可能违反私募排排网的 [服务条款](https://www.simuwang.com/declare)（ToS）。本项目与私募排排网无任何关联，亦未获其授权或认可。使用者应自行评估合规风险，并自行承担使用本项目所产生的一切后果，包括但不限于账号限制、服务中断、数据丢失及法律责任。

## License

[MIT](LICENSE)
