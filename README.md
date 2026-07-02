<div align="center">

<img src="https://raw.githubusercontent.com/Novforest/astrbot_plugin_fund/refs/heads/master/logo.png" width="120" alt="基金小助手 Logo">

# 💰 基金小助手

[![AstrBot](https://img.shields.io/badge/AstrBot-Plugin-6C5CE7?logo=python)](https://github.com/Soulter/AstrBot)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue?logo=python)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.0.0-orange)](https://github.com/Novforest/astrbot_plugin_fund/releases)

*基金净值估算 · AI智能预测 · 综合评级 · 经理评价 · 金银价格，一站搞定*

</div>

---

## ✨ 功能一览

| 指令 | 说明 |
|:--|:--|
| `/基金小助手` | 查看全部指令列表 |
| `/净值估算 <代码>` | 实时净值估算（涨跌幅 + 单位净值） |
| `/基金预测 <代码>` | 综合数据 + AI 智能预测分析 |
| `/基金评分 <代码>` | 多维度综合评级 |
| `/搜索基金经理 <代码>` | 基金经理能力评价 & 任期收益 |
| `/黄金` | 足金 / K金 / 银行金条 / 品牌饰品 / 回收价速览 |
| `/白银` | 白银现货价 / 日内波动 / 回收价速览 |

> 💡 **基金代码为 6 位数字**，如 `002170`

---

## 📦 安装

### 方式一：AstrBot 插件市场（推荐）

在 AstrBot 面板中搜索 `astrbot_plugin_fund`，一键安装。

### 方式二：手动安装

```bash
# 克隆到 AstrBot 插件目录
git clone https://github.com/Novforest/astrbot_plugin_fund.git
```

或下载 zip 解压到 `addons/astrbot_plugin_fund/`。

### 依赖

插件仅依赖 `aiohttp`，AstrBot 环境已内置，**无需额外安装**。

---

## 🚀 快速上手

```
你：/净值估算 002170
🤖：📊 基金代码：002170
    📛 基金名称：东吴移动互联混合A
    📅 净值日期：2026-07-01
    💰 单位净值：3.8520
    📈 估算净值：3.8651
    🚀 估算涨跌幅：+0.34%
    🕒 估算时间：2026-07-02 14:35

你：/黄金
🤖：💰 今日金价速览（2026-07-02）
    🏆 足金（9999）
    最新价：588.50 元/克
    🌍 国际金价参考
    伦敦金（现货）：2350.20 美元/盎司
    ...

你：/基金预测 002170
🤖：🔍 正在获取基金综合数据...
    🤖 AI正在分析中...
    （AI 综合近一月净值走势、规模变动、资产配置等数据，给出多维度分析建议）
```

> ⚠️ **使用 `/基金预测`、`/基金评分`、`/搜索基金经理` 前**，请确保 AstrBot 已配置大语言模型（LLM）。

---

## 🔧 配置项

在 AstrBot 插件配置面板中可自定义：

| 配置项 | 说明 | 默认值 |
|:--|:--|:--|
| `request_timeout` | 接口请求超时（秒） | `10` |
| `max_retries` | 请求失败重试次数 | `3` |
| `ai_prompt` | AI 预测提示词 | 内置默认 |
| `rating_prompt` | AI 评级提示词 | 内置默认 |
| `manager_prompt` | AI 经理评价提示词 | 内置默认 |

---

## 🙏 致谢

- 数据接口来自 [天天基金](https://fund.eastmoney.com/) 与 [东方财富](https://www.eastmoney.com/)
- 金银价格数据来自 [tmini.net](https://tmini.net)
- 插件框架基于 [AstrBot](https://github.com/Soulter/AstrBot) 生态

特别感谢每一位使用和反馈的用户，你们的 ⭐ Star 是我持续更新的动力 💪

---

<div align="center">

**[⬆ 回到顶部](#-基金小助手)** · **[🐛 提交 Issue](https://github.com/Novforest/astrbot_plugin_fund/issues)** · **[⭐ Star](https://github.com/Novforest/astrbot_plugin_fund)**

<sub>Made with ❤️ by [Novforest](https://github.com/Novforest)</sub>

</div>
