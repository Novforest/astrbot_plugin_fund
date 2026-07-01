"""
AstrBot 基金助手插件
支持基金净值估算与AI预测功能。
- /基金小助手            查看所有指令
- /净值估算<基金代码>    实时净值估算
- /基金预测<基金代码>    综合数据+AI预测分析
- /基金评分<基金代码>    基金综合评级
- /搜索基金经理<基金代码> 基金经理综合评价
- /黄金                  黄金价格速览
- /白银                  白银价格速览
"""

import re
import json
import asyncio
from datetime import datetime, timedelta, timezone

import aiohttp

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig

# 上海时区（UTC+8），无需额外依赖
SHANGHAI_TZ = timezone(timedelta(hours=8))

# 接口地址
FUND_GZ_URL = "http://fundgz.1234567.com.cn/js/{code}.js"
FUND_PZ_URL = "http://fund.eastmoney.com/pingzhongdata/{code}.js"
GOLD_PRICE_URL = "https://tmini.net/api/gold-price"

# 基金代码格式：6 位数字
FUND_CODE_PATTERN = re.compile(r"\d{6}")


@register(
    "astrbot_plugin_fund",
    "Novforest",
    "基金净值估算与AI预测插件",
    "1.0.0",
    "",
)
class FundPlugin(Star):
    """基金助手插件主类"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # 延迟初始化的 aiohttp 会话，复用连接池
        self._session: aiohttp.ClientSession | None = None

    # ==================== 工具方法 ====================

    def _get_request_timeout(self) -> int:
        """从配置获取请求超时秒数"""
        return int(self.config.get("request_timeout", 10))

    def _get_max_retries(self) -> int:
        """从配置获取最大重试次数"""
        return int(self.config.get("max_retries", 3))

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 aiohttp 会话（带连接池限制与默认 UA）"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self._get_request_timeout())
            connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    "Referer": "https://fund.eastmoney.com/",
                },
            )
        return self._session

    async def _fetch_with_retry(self, url: str) -> str:
        """带重试机制的异步 HTTP 请求，返回响应文本

        Args:
            url: 请求地址

        Returns:
            响应文本字符串

        Raises:
            RuntimeError: 所有重试均失败后抛出
        """
        session = await self._get_session()
        max_retries = self._get_max_retries()
        last_error: str = ""

        for attempt in range(1, max_retries + 1):
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        last_error = f"HTTP {resp.status}"
                        logger.warning(
                            f"基金接口返回状态码 {resp.status}（第{attempt}次尝试）"
                        )
                        continue
                    return await resp.text()
            except asyncio.TimeoutError:
                last_error = f"请求超时（{self._get_request_timeout()}秒）"
                logger.warning(f"基金接口请求超时（第{attempt}/{max_retries}次）")
            except aiohttp.ClientError as e:
                last_error = f"网络错误: {e}"
                logger.warning(f"基金接口网络错误: {e}（第{attempt}/{max_retries}次）")
            except Exception as e:
                last_error = f"未知错误: {e}"
                logger.warning(f"基金接口未知错误: {e}（第{attempt}/{max_retries}次）")

            if attempt < max_retries:
                await asyncio.sleep(1 * attempt)

        raise RuntimeError(f"请求失败（重试{max_retries}次）: {last_error}")

    @staticmethod
    def _validate_fund_code(code: str) -> str | None:
        """验证并提取6位基金代码

        Args:
            code: 用户输入的原始文本

        Returns:
            6位数字基金代码，或 None（格式不合法）
        """
        match = FUND_CODE_PATTERN.search(code)
        if match:
            return match.group()
        return None

    # ==================== 净值估算数据获取 ====================

    async def _fetch_fund_gz(self, code: str) -> dict:
        """请求基金净值估算接口并解析数据

        接口返回格式：jsonpgz({...});  需用正则提取括号内 JSON

        Args:
            code: 6位基金代码

        Returns:
            解析后的字典数据

        Raises:
            RuntimeError: 请求失败或数据不存在
        """
        url = FUND_GZ_URL.format(code=code)
        text = await self._fetch_with_retry(url)

        match = re.search(r"jsonpgz\((.+)\);", text)
        if not match:
            raise RuntimeError("净值估算数据不存在，请检查基金代码是否正确")

        data = json.loads(match.group(1))
        if not data or "fundcode" not in data:
            raise RuntimeError("净值估算数据解析失败")

        return data

    # ==================== 基金预测数据获取 ====================

    async def _fetch_fund_pingzhongdata(self, code: str) -> dict:
        """请求基金综合数据接口并解析全部变量

        接口返回一个 JS 文件，包含多个 var 声明。
        本方法解析以下关键变量：
        - fS_name / fS_code: 基金名称与代码
        - syl_1n / syl_6y / syl_3y / syl_1y: 各期限收益率
        - Data_netWorthTrend: 净值走势
        - Data_fluctuationScale: 规模变动
        - Data_assetAllocation: 资产配置
        - Data_holderStructure: 持有人结构
        - Data_buySedemption: 申购赎回
        - Data_performanceEvaluation: 业绩评价
        - Data_currentFundManager: 基金经理

        Args:
            code: 6位基金代码

        Returns:
            包含所有解析数据的字典

        Raises:
            RuntimeError: 请求失败或数据不存在
        """
        url = FUND_PZ_URL.format(code=code)
        text = await self._fetch_with_retry(url)

        if not text or len(text) < 100:
            raise RuntimeError("基金数据文件为空，请检查基金代码是否正确")

        result: dict = {}

        # --- 基本信息变量（字符串类型）---
        result["fS_name"] = self._extract_str_var(text, "fS_name")
        result["fS_code"] = self._extract_str_var(text, "fS_code")

        # --- 各期限收益率（字符串类型）---
        result["syl_1n"] = self._extract_str_var(text, "syl_1n")   # 近一年
        result["syl_6y"] = self._extract_str_var(text, "syl_6y")   # 近六月
        result["syl_3y"] = self._extract_str_var(text, "syl_3y")   # 近三月
        result["syl_1y"] = self._extract_str_var(text, "syl_1y")   # 近一月

        # --- 复合数据变量（JSON/数组类型）---
        result["Data_netWorthTrend"] = self._parse_json_var(
            text, "Data_netWorthTrend"
        )
        # 规模变动：使用注释锚点精确匹配 /*规模变动 mom-较上期环比*/ var Data_fluctuationScale = ...
        result["Data_fluctuationScale"] = self._extract_fluctuation_scale(text)
        result["Data_assetAllocation"] = self._parse_json_var(
            text, "Data_assetAllocation"
        )
        result["Data_holderStructure"] = self._parse_json_var(
            text, "Data_holderStructure"
        )
        result["Data_buySedemption"] = self._parse_json_var(
            text, "Data_buySedemption"
        )
        result["Data_performanceEvaluation"] = self._parse_json_var(
            text, "Data_performanceEvaluation"
        )
        result["Data_currentFundManager"] = self._parse_json_var(
            text, "Data_currentFundManager"
        )

        return result

    @staticmethod
    def _extract_str_var(js_content: str, var_name: str) -> str:
        """从 JS 内容中提取字符串类型变量的值

        匹配格式：var var_name = "value";

        Args:
            js_content: JS 文件全文
            var_name: 变量名

        Returns:
            变量值字符串，未找到时返回空字符串
        """
        pattern = rf'var\s+{var_name}\s*=\s*["\']([^"\']*)["\']'
        match = re.search(pattern, js_content)
        return match.group(1) if match else ""

    @staticmethod
    def _clean_js_json(raw: str) -> str:
        """将 JS 变量值清理为可被 json.loads 解析的字符串

        仅做两件事：移除 /* */ 块注释、移除尾逗号。
        不做 // 行注释剥离——Data_currentFundManager.pic 等字段含
        https:// URL，r"//.*" 会误删 URL 及闭合引号。

        Args:
            raw: JS 变量赋值的原始字符串（等号到分号之间）

        Returns:
            清理后的 JSON 兼容字符串
        """
        cleaned = re.sub(r"/\*.*?\*/", "", raw, flags=re.DOTALL)
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        return cleaned

    @staticmethod
    def _extract_fluctuation_scale(js_content: str):
        """基于注释锚点提取规模变动数据

        匹配 /*规模变动 mom-较上期环比*/ var Data_fluctuationScale = {...};
        相比通用 _parse_json_var，注释锚点避免误匹配同名近似变量，
        并能稳定取到含 mom 环比字段的 series 结构。

        Args:
            js_content: JS 文件全文

        Returns:
            解析后的字典 {categories, series:[{y, mom}]}，失败返回 None
        """
        pattern = (
            r"/\*规模变动\s+mom-较上期环比\*/\s*"
            r"var\s+Data_fluctuationScale\s*=\s*([\s\S]*?);"
        )
        match = re.search(pattern, js_content)
        if not match:
            logger.debug("Data_fluctuationScale 注释锚点未匹配")
            return None
        raw = match.group(1).strip()
        try:
            return json.loads(FundPlugin._clean_js_json(raw))
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Data_fluctuationScale JSON 解析失败: {e}")
            return None

    @staticmethod
    def _extract_raw_var(js_content: str, var_name: str) -> str | None:
        """提取 JS 变量的原始赋值字符串（等号到分号之间）

        Args:
            js_content: JS 文件全文
            var_name: 变量名

        Returns:
            原始赋值内容，未找到时返回 None
        """
        pattern = rf"var\s+{var_name}\s*=\s*(.+?);"
        match = re.search(pattern, js_content, re.DOTALL)
        return match.group(1).strip() if match else None

    def _parse_json_var(self, js_content: str, var_name: str):
        """清理 JS 注释并解析变量为 Python 对象

        Args:
            js_content: JS 文件全文
            var_name: 变量名

        Returns:
            解析后的 Python 对象（list/dict），解析失败返回空列表
        """
        raw = self._extract_raw_var(js_content, var_name)
        if raw is None:
            logger.debug(f"变量 {var_name} 未找到")
            return []

        try:
            return json.loads(self._clean_js_json(raw))
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"变量 {var_name} JSON 解析失败: {e}")
            return []

    # ==================== 数据处理与格式化 ====================

    @staticmethod
    def _filter_recent_month(net_worth_trend: list) -> list:
        """过滤最近一个月的净值走势记录

        东方财富数据的时间戳 x 为毫秒级，需统一转换为秒级后再比较。

        Args:
            net_worth_trend: 净值走势原始列表，每项含 x(时间戳) 字段

        Returns:
            最近30天的净值记录列表
        """
        if not net_worth_trend:
            return []

        now_sh = datetime.now(SHANGHAI_TZ)
        thirty_days_ago = now_sh - timedelta(days=30)
        cutoff_ts = thirty_days_ago.timestamp()

        result = []
        for item in net_worth_trend:
            if not isinstance(item, dict):
                continue
            raw_ts = item.get("x", 0)
            # 统一转换为秒级时间戳（东方财富使用毫秒级）
            ts = raw_ts / 1000 if raw_ts > 1e12 else raw_ts
            if ts >= cutoff_ts:
                result.append(item)
        return result

    def _format_estimation(self, data: dict) -> str:
        """格式化净值估算结果

        Args:
            data: 净值估算接口返回的字典

        Returns:
            格式化的文本消息
        """
        gszzl = data.get("gszzl", "--")
        # 涨跌幅添加正负号显示
        try:
            val = float(gszzl)
            sign = "+" if val >= 0 else ""
            gszzl_str = f"{sign}{val}%"
        except (ValueError, TypeError):
            gszzl_str = f"{gszzl}%"

        return (
            f"📊 基金代码：{data.get('fundcode', '未知')}\n"
            f"📛 基金名称：{data.get('name', '未知')}\n"
            f"📅 净值日期：{data.get('jzrq', '未知')}\n"
            f"💰 单位净值：{data.get('dwjz', '未知')}\n"
            f"📈 估算净值：{data.get('gsz', '未知')}\n"
            f"🚀 估算涨跌幅：{gszzl_str}\n"
            f"🕒 估算时间：{data.get('gztime', '未知')}"
        )

    # ==================== AI 预测 ====================

    @staticmethod
    def _get_prediction_time() -> str:
        """计算预测时间（上海时区）

        规则：
        - 当前时间在 15:00 之前：预测当天
        - 当前时间在 15:00 及之后：预测次日

        Returns:
            预测时间字符串，精确到分钟，格式 %Y-%m-%d %H:%M
        """
        now_sh = datetime.now(SHANGHAI_TZ)
        if now_sh.hour < 15:
            predict_dt = now_sh
        else:
            predict_dt = now_sh + timedelta(days=1)
        return predict_dt.strftime("%Y-%m-%d %H:%M")

    def _build_ai_complete_data(self, data: dict) -> str:
        """构建完整的基金数据文本供 AI 内部使用（不向用户展示）

        包含全部净值数据（按最近一个月时间过滤），供 AI 进行预测分析。
        严禁将此文本通过任何形式展示给用户。

        Args:
            data: pingzhongdata 接口解析后的字典

        Returns:
            完整基金数据文本，仅供 AI 消费
        """
        lines: list[str] = []

        # --- 基金基本信息 ---
        lines.append("【基金基本信息】")
        lines.append(f"基金名称：{data.get('fS_name', '未知')}")
        lines.append(f"基金代码：{data.get('fS_code', '未知')}")

        # --- 各期限收益率 ---
        syl_1n = data.get("syl_1n", "")
        syl_6y = data.get("syl_6y", "")
        syl_3y = data.get("syl_3y", "")
        syl_1y = data.get("syl_1y", "")
        if syl_1n or syl_6y or syl_3y or syl_1y:
            lines.append("【收益率】")
            if syl_1y:
                lines.append(f"  近1月：{syl_1y}%")
            if syl_3y:
                lines.append(f"  近3月：{syl_3y}%")
            if syl_6y:
                lines.append(f"  近6月：{syl_6y}%")
            if syl_1n:
                lines.append(f"  近1年：{syl_1n}%")

        # --- 净值走势（完整数据，按时间过滤，仅供 AI 内部使用）---
        # 解析净值数据并按时间过滤，确保 AI 能完整读取全部净值数据
        net_worth = data.get("Data_netWorthTrend", [])
        recent = self._filter_recent_month(net_worth)
        if recent:
            lines.append("【净值走势（近一月完整数据）】")
            # 保留时间格式化代码，仅用于内部处理，不向用户输出
            for d in recent:
                dt = datetime.fromtimestamp(
                    d.get("x", 0) / 1000, tz=SHANGHAI_TZ
                ).strftime("%Y-%m-%d")
                y = d.get("y", "--")
                growth = d.get("equityReturn", "")
                try:
                    growth_val = float(growth)
                    growth_str = f"{growth_val:+.2f}%"
                except (ValueError, TypeError):
                    growth_str = ""
                lines.append(f"  {dt}  净值：{y} {growth_str}")

        # --- 规模变动 ---
        # 结构：{categories:[日期], series:[{y:规模, mom:"环比%"}]}
        fluctuation = data.get("Data_fluctuationScale")
        if fluctuation and isinstance(fluctuation, dict):
            categories = fluctuation.get("categories", [])
            series = fluctuation.get("series", [])
            if categories and series and isinstance(series, list):
                lines.append("【规模变动】")
                # 取最近4个季度，categories 与 series 一一对应
                recent_cats = categories[-4:]
                recent_series = series[-4:]
                for cat, s in zip(recent_cats, recent_series):
                    if not isinstance(s, dict):
                        continue
                    y = s.get("y", "--")
                    mom = s.get("mom", "")
                    mom_str = f"（环比{mom}）" if mom else ""
                    lines.append(f"  {cat}: {y}亿{mom_str}")

        # --- 资产配置 ---
        # 结构：{series:[{name, type, data:[...], yAxis}], categories:[日期]}
        allocation = data.get("Data_assetAllocation")
        if allocation and isinstance(allocation, dict):
            a_series = allocation.get("series", [])
            a_cats = allocation.get("categories", [])
            if a_series and a_cats:
                lines.append("【资产配置】")
                lines.append(f"  报告期：{a_cats[-1]}")
                for s in a_series:
                    if not isinstance(s, dict):
                        continue
                    name = s.get("name", "")
                    sdata = s.get("data", [])
                    latest_val = sdata[-1] if sdata else "--"
                    lines.append(f"  {name}：{latest_val}")

        # --- 持有人结构 ---
        # 结构：{series:[{name, data:[...]}], categories:[日期]}
        holder = data.get("Data_holderStructure")
        if holder and isinstance(holder, dict):
            h_series = holder.get("series", [])
            h_cats = holder.get("categories", [])
            if h_series and h_cats:
                lines.append("【持有人结构】")
                lines.append(f"  报告期：{h_cats[-1]}")
                for s in h_series:
                    if not isinstance(s, dict):
                        continue
                    name = s.get("name", "")
                    sdata = s.get("data", [])
                    latest_val = sdata[-1] if sdata else "--"
                    lines.append(f"  {name}：{latest_val}%")

        # --- 业绩评价 ---
        # 结构：{avr:综合分, categories:[维度], data:[评分], dsc:[说明]}
        perf = data.get("Data_performanceEvaluation")
        if perf and isinstance(perf, dict):
            lines.append("【业绩评价】")
            lines.append(f"  综合评分：{perf.get('avr', '--')}")
            p_cats = perf.get("categories", [])
            p_data = perf.get("data", [])
            for cat, val in zip(p_cats, p_data):
                lines.append(f"  {cat}：{val}")

        # --- 基金经理 ---
        # 结构：[{name, star, workTime, fundSize, power:{avr,category,data}, profit:{...}}]
        managers = data.get("Data_currentFundManager", [])
        if managers and isinstance(managers, list):
            lines.append("【基金经理】")
            for mgr in managers:
                if not isinstance(mgr, dict):
                    continue
                name = mgr.get("name", "未知")
                star = mgr.get("star", "--")
                work_time = mgr.get("workTime", "未知")
                fund_size = mgr.get("fundSize", "未知")
                power = mgr.get("power") if isinstance(mgr.get("power"), dict) else {}
                power_avr = power.get("avr", "--")
                lines.append(
                    f"  {name}（{star}星，从业{work_time}，"
                    f"管理规模{fund_size}，能力评分{power_avr}）"
                )

        return "\n".join(lines)

    async def _get_ai_prediction(
        self, event: AstrMessageEvent, fund_data_str: str, predict_time: str
    ) -> str:
        """调用系统配置的 LLM 生成基金预测分析

        整合三要素提供给 AI：
        1. 当前时间（predict_time）
        2. 提示词文件中的内容（从配置读取）
        3. 完整的基金净值数据（fund_data_str，内部处理后）

        Args:
            event: 消息事件
            fund_data_str: 完整基金数据文本（仅供 AI 内部使用，不展示给用户）
            predict_time: 预测时间字符串

        Returns:
            AI 生成的预测分析文本，失败时返回错误提示
        """
        return await self._call_llm(
            event,
            "ai_prompt",
            fund_data_str,
            predict_time,
            "请基于以下基金数据进行综合分析并给出投资建议。\n"
            "当前时间：{predict_time}\n基金数据：\n{fund_data}",
        )

    async def _call_llm(
        self,
        event: AstrMessageEvent,
        config_key: str,
        fund_data_str: str,
        predict_time: str,
        fallback_prompt: str,
    ) -> str:
        """通用 LLM 调用：读取指定配置项的提示词模板，替换占位符后调用模型

        Args:
            event: 消息事件
            config_key: 配置项键名（ai_prompt / rating_prompt / manager_prompt）
            fund_data_str: 基金数据文本
            predict_time: 当前/预测时间字符串
            fallback_prompt: 配置缺失时的兜底提示词

        Returns:
            AI 生成的文本，失败时返回错误提示
        """
        try:
            umo = event.unified_msg_origin
            provider_id = await self.context.get_current_chat_provider_id(umo=umo)
        except Exception as e:
            logger.warning(f"获取AI模型失败: {e}")
            return "⚠️ 未找到可用的AI模型，请在 AstrBot 设置中配置大语言模型。"

        if not provider_id:
            return "⚠️ 未找到可用的AI模型，请在 AstrBot 设置中配置大语言模型。"

        prompt_template = self.config.get(config_key, fallback_prompt)
        prompt = prompt_template.replace("{predict_time}", predict_time)
        prompt = prompt.replace("{fund_data}", fund_data_str)

        try:
            llm_resp = await self.context.llm_generate(
                chat_provider_id=provider_id,
                prompt=prompt,
            )
            return llm_resp.completion_text or "⚠️ AI模型返回空结果。"
        except Exception as e:
            logger.error(f"AI模型调用失败: {e}")
            return f"⚠️ AI模型调用失败：{e}"

    async def _get_ai_rating(
        self, event: AstrMessageEvent, fund_data_str: str, predict_time: str
    ) -> str:
        """调用 LLM 生成基金综合评级"""
        return await self._call_llm(
            event,
            "rating_prompt",
            fund_data_str,
            predict_time,
            "请基于以下基金数据进行综合评级。\n"
            "当前时间：{predict_time}\n基金数据：\n{fund_data}",
        )

    async def _get_ai_manager_search(
        self, event: AstrMessageEvent, fund_data_str: str, predict_time: str
    ) -> str:
        """调用 LLM 生成基金经理综合评价"""
        return await self._call_llm(
            event,
            "manager_prompt",
            fund_data_str,
            predict_time,
            "请基于以下基金经理数据进行综合评价。\n"
            "当前时间：{predict_time}\n基金数据：\n{fund_data}",
        )

    def _build_manager_data(self, managers: list) -> str:
        """构建基金经理数据文本供 AI 评价

        结构：[{name, star, workTime, fundSize, power:{avr,category,data},
               profit:{categories, series:[{data:[{y}]}]}}]

        Args:
            managers: Data_currentFundManager 列表

        Returns:
            格式化的基金经理数据文本
        """
        lines: list[str] = []
        for mgr in managers:
            if not isinstance(mgr, dict):
                continue
            lines.append(f"姓名：{mgr.get('name', '未知')}")
            lines.append(f"星级：{mgr.get('star', '--')}")
            lines.append(f"从业时间：{mgr.get('workTime', '未知')}")
            lines.append(f"管理规模：{mgr.get('fundSize', '未知')}")

            power = mgr.get("power") if isinstance(mgr.get("power"), dict) else {}
            if power:
                lines.append(f"能力综合评分：{power.get('avr', '--')}")
                p_cats = power.get("category", [])
                p_data = power.get("data", [])
                for cat, val in zip(p_cats, p_data):
                    lines.append(f"  {cat}：{val}")

            profit = mgr.get("profit") if isinstance(mgr.get("profit"), dict) else {}
            if profit:
                pcats = profit.get("categories", [])
                pseries = profit.get("series", [])
                pdata = pseries[0].get("data", []) if pseries and isinstance(
                    pseries[0], dict
                ) else []
                lines.append("任期收益：")
                for cat, val in zip(pcats, pdata):
                    y = val.get("y", "--") if isinstance(val, dict) else val
                    lines.append(f"  {cat}：{y}")
            lines.append("")
        return "\n".join(lines)

    # ==================== 指令处理 ====================

    @filter.command("净值估算")
    async def estimate_nav(self, event: AstrMessageEvent):
        """基金净值估算指令，用法：/净值估算 123456 或 /净值估算123456"""
        # 从消息中提取基金代码
        raw_input = event.message_str.strip()
        code = self._validate_fund_code(raw_input)

        if not code:
            yield event.plain_result(
                "❌ 请输入正确的6位基金代码\n"
                "用法：/净值估算 123456"
            )
            return

        # 请求数据
        yield event.plain_result("🔍 正在查询净值估算数据...")

        try:
            data = await self._fetch_fund_gz(code)
            result_text = self._format_estimation(data)
            yield event.plain_result(result_text)
        except RuntimeError as e:
            yield event.plain_result(f"❌ 查询失败：{e}")
        except json.JSONDecodeError:
            yield event.plain_result("❌ 数据解析失败，请稍后重试。")
        except Exception as e:
            logger.error(f"净值估算未知错误: {e}")
            yield event.plain_result(f"❌ 发生未知错误：{e}")

    @filter.command("基金预测")
    async def predict_fund(self, event: AstrMessageEvent):
        """基金AI预测指令，用法：/基金预测 123456 或 /基金预测123456

        数据处理说明：
        - 全部净值数据仅在内部处理并供 AI 预测使用，禁止向用户输出任何原始净值数据
        - 整合当前时间、提示词、完整基金数据三要素交由 AI 生成预测
        - 预测时间规则：15:00 前预测当天，15:00 及之后预测次日
        """
        # 从消息中提取基金代码
        raw_input = event.message_str.strip()
        code = self._validate_fund_code(raw_input)

        if not code:
            yield event.plain_result(
                "❌ 请输入正确的6位基金代码\n"
                "用法：/基金预测 123456"
            )
            return

        yield event.plain_result("🔍 正在获取基金综合数据，请稍候...")

        try:
            data = await self._fetch_fund_pingzhongdata(code)
        except RuntimeError as e:
            yield event.plain_result(f"❌ 数据获取失败：{e}")
            return
        except Exception as e:
            logger.error(f"基金预测数据获取错误: {e}")
            yield event.plain_result(f"❌ 发生未知错误：{e}")
            return

        # 计算预测时间（上海时区，15:00 前预测当天，之后预测次日）
        predict_time = self._get_prediction_time()

        # 构建完整基金数据文本（仅供 AI 内部使用，不向用户展示）
        fund_data_str = self._build_ai_complete_data(data)

        # 提示用户 AI 正在分析
        yield event.plain_result("🤖 AI正在分析中，请稍候...")

        # 调用 AI 生成预测分析，整合当前时间、提示词、完整基金数据三要素
        try:
            ai_result = await self._get_ai_prediction(
                event, fund_data_str, predict_time
            )
            # 仅向用户输出 AI 预测结果，不输出任何原始净值数据
            yield event.plain_result(ai_result)
        except Exception as e:
            logger.error(f"AI预测失败: {e}")
            yield event.plain_result(f"⚠️ AI预测生成失败：{e}")

    @filter.command("基金评分")
    async def fund_rating(self, event: AstrMessageEvent):
        """基金评分指令，用法：/基金评分 123456

        调用 pingzhongdata 接口获取基金综合数据，提取规模变动等关键信息后，
        交由 AI 按评级提示词进行多维度综合评级。
        """
        raw_input = event.message_str.strip()
        code = self._validate_fund_code(raw_input)

        if not code:
            yield event.plain_result(
                "❌ 请输入正确的6位基金代码\n"
                "用法：/基金评分 123456"
            )
            return

        yield event.plain_result("🔍 正在获取基金综合数据，请稍候...")

        try:
            data = await self._fetch_fund_pingzhongdata(code)
        except RuntimeError as e:
            yield event.plain_result(f"❌ 数据获取失败：{e}")
            return
        except Exception as e:
            logger.error(f"基金评分数据获取错误: {e}")
            yield event.plain_result(f"❌ 发生未知错误：{e}")
            return

        # 构建完整基金数据（含规模变动 mom 环比）供 AI 评级
        fund_data_str = self._build_ai_complete_data(data)
        predict_time = self._get_prediction_time()

        yield event.plain_result("🤖 AI正在评级中，请稍候...")
        try:
            ai_result = await self._get_ai_rating(event, fund_data_str, predict_time)
            yield event.plain_result(ai_result)
        except Exception as e:
            logger.error(f"基金评分失败: {e}")
            yield event.plain_result(f"⚠️ 评级生成失败：{e}")

    @filter.command("搜索基金经理")
    async def search_fund_manager(self, event: AstrMessageEvent):
        """搜索基金经理指令，用法：/搜索基金经理 123456

        从 pingzhongdata 接口提取 Data_currentFundManager，构建经理能力与
        任期收益数据后交由 AI 按经理提示词进行综合评价。
        """
        raw_input = event.message_str.strip()
        code = self._validate_fund_code(raw_input)

        if not code:
            yield event.plain_result(
                "❌ 请输入正确的6位基金代码\n"
                "用法：/搜索基金经理 123456"
            )
            return

        yield event.plain_result("🔍 正在获取基金经理数据，请稍候...")

        try:
            data = await self._fetch_fund_pingzhongdata(code)
        except RuntimeError as e:
            yield event.plain_result(f"❌ 数据获取失败：{e}")
            return
        except Exception as e:
            logger.error(f"基金经理数据获取错误: {e}")
            yield event.plain_result(f"❌ 发生未知错误：{e}")
            return

        managers = data.get("Data_currentFundManager", [])
        if not managers:
            yield event.plain_result("❌ 未找到该基金的经理数据")
            return

        manager_data_str = self._build_manager_data(managers)
        predict_time = self._get_prediction_time()

        yield event.plain_result("🤖 AI正在分析经理信息，请稍候...")
        try:
            ai_result = await self._get_ai_manager_search(
                event, manager_data_str, predict_time
            )
            yield event.plain_result(ai_result)
        except Exception as e:
            logger.error(f"基金经理搜索失败: {e}")
            yield event.plain_result(f"⚠️ 分析生成失败：{e}")

    @filter.command("基金小助手")
    async def fund_helper(self, event: AstrMessageEvent):
        """查看基金助手所有指令，用法：/基金小助手"""
        yield event.plain_result(
            "📊 基金助手指令列表\n"
            "- /净值估算<基金代码>\n"
            "- /基金预测<基金代码>\n"
            "- /基金评分<基金代码>\n"
            "- /搜索基金经理<基金代码>\n"
            "- /黄金\n黄金价格速览\n"
            "- /白银\n白银价格速览"
        )

    # ==================== 金银价格 ====================

    async def _fetch_gold_price(self) -> dict:
        """请求金银价格接口并解析 JSON

        接口返回包含 metals、stores、banks 三个数组的 JSON 对象。

        Returns:
            解析后的完整字典

        Raises:
            RuntimeError: 请求失败或数据不存在
        """
        text = await self._fetch_with_retry(GOLD_PRICE_URL)
        data = json.loads(text)
        if not data or "metals" not in data:
            raise RuntimeError("金银价格数据解析失败")
        return data

    @staticmethod
    def _find_metal(metals: list, name: str) -> dict | None:
        """从 metals 列表中按 name 精确匹配查找"""
        for m in metals:
            if isinstance(m, dict) and m.get("name") == name:
                return m
        return None

    @staticmethod
    def _calc_k_gold_price(gold_9999_price: float, purity: float) -> str:
        """根据足金9999价格和纯度计算K金回收价格

        Args:
            gold_9999_price: 足金9999的最新价
            purity: 纯度比率（如 0.916 表示 22K）

        Returns:
            格式化的价格字符串
        """
        try:
            price = gold_9999_price * purity
            return f"{price:.1f}"
        except (ValueError, TypeError):
            return "--"

    def _format_gold_price(self, data: dict) -> str:
        """格式化黄金价格展示

        Args:
            data: 金银价格接口返回的完整字典

        Returns:
            格式化的文本消息
        """
        metals = data.get("metals", [])
        stores = data.get("stores", [])
        banks = data.get("banks", [])

        # 查找关键金属数据
        gold_9999 = self._find_metal(metals, "黄金_9999") or {}
        london_gold = self._find_metal(metals, "伦敦金(现货黄金)") or {}
        ny_gold = self._find_metal(metals, "纽约黄金(美国)") or {}

        # 店铺数据构建索引
        store_map: dict[str, str] = {}
        for s in stores:
            if isinstance(s, dict):
                brand = s.get("brand", "")
                price = s.get("price", "")
                store_map[brand] = str(price)

        # 银行数据构建索引
        bank_map: dict[str, str] = {}
        for b in banks:
            if isinstance(b, dict):
                bank_name = b.get("bank", "")
                price = b.get("price", "")
                bank_map[bank_name] = str(price)

        date_str = data.get("date", datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d"))
        updated = gold_9999.get("updated", "")

        lines = [f"💰 今日金价速览（{date_str}）"]

        # 足金9999
        lines.append("🏆 足金（9999）")
        lines.append(f"最新价：{gold_9999.get('today_price', '--')} 元/克")
        high = gold_9999.get("high_price", "--")
        low = gold_9999.get("low_price", "--")
        lines.append(f"日内高/低：{high} / {low} 元/克")
        if updated:
            lines.append(f"数据时间：{updated}")

        # 国际金价参考
        lines.append("🌍 国际金价参考")
        lines.append(f"伦敦金（现货）：{london_gold.get('today_price', '--')} 美元/盎司")
        lines.append(f"纽约金（期货）：{ny_gold.get('today_price', '--')} 美元/盎司")

        # 银行投资金条
        lines.append("🏦 银行投资金条")
        for bank_name in ["工商银行", "中国银行", "建设银行", "农业银行", "平安银行"]:
            price = bank_map.get(bank_name, "--")
            lines.append(f"{bank_name}：{price} 元/克")

        # 品牌金店饰品价
        lines.append("💎 品牌金店饰品价")
        for brand_name in ["周大福", "老凤祥", "周生生", "菜百首饰", "中国黄金"]:
            price = store_map.get(brand_name, "--")
            lines.append(f"{brand_name}：{price} 元/克")

        # 黄金回收价
        lines.append("♻️ 黄金回收价")
        # 足金9999最新价用于计算K金回收价
        try:
            gold_price = float(gold_9999.get("today_price", 0))
        except (ValueError, TypeError):
            gold_price = 0
        lines.append(f"足金（99.9%+）：{gold_price:.1f} 元/克" if gold_price else "足金（99.9%+）：-- 元/克")
        lines.append(f"22K（91.6%）：{self._calc_k_gold_price(gold_price, 0.916)} 元/克")
        lines.append(f"18K（75.0%）：{self._calc_k_gold_price(gold_price, 0.75)} 元/克")

        return "\n".join(lines)

    def _format_silver_price(self, data: dict) -> str:
        """格式化白银价格展示

        Args:
            data: 金银价格接口返回的完整字典

        Returns:
            格式化的文本消息
        """
        metals = data.get("metals", [])

        silver = self._find_metal(metals, "白银价格") or {}

        date_str = data.get("date", datetime.now(SHANGHAI_TZ).strftime("%Y-%m-%d"))
        latest = silver.get("today_price", "--")
        high = silver.get("high_price", "--")
        low = silver.get("low_price", "--")
        updated = silver.get("updated", "")

        lines = [f"🥈 今日白银速览（{date_str}）"]
        lines.append("💰 白银现货价格")
        lines.append(f"最新价：{latest} 元/克")
        # 开盘价使用前日收盘价（sell_price）作为近似
        lines.append(f"开盘价：{silver.get('sell_price', '--')} 元/克")
        lines.append(f"日内高/低：{high} / {low} 元/克")
        if updated:
            lines.append(f"数据时间：{updated}")

        # 日内波动
        lines.append("📊 日内波动")
        try:
            high_f = float(high)
            low_f = float(low)
            latest_f = float(latest)
            open_f = float(silver.get("sell_price", latest_f))
            if low_f > 0:
                amplitude = (high_f - low_f) / low_f * 100
                lines.append(f"振幅：约 {amplitude:.2f}%")
            else:
                lines.append("振幅：--")
            # 当前较开盘
            diff = latest_f - open_f
            if diff >= 0:
                direction = "微涨"
                emoji = "📈"
                sign = "+"
            else:
                direction = "微跌"
                emoji = "📉"
                sign = ""
            lines.append(f"当前较开盘：{direction} {sign}{diff:.2f} 元/克 {emoji}")
        except (ValueError, TypeError):
            lines.append("振幅：--")
            lines.append("当前较开盘：--")

        # 白银回收价（足银99.9%）
        lines.append("♻️ 白银回收价")
        try:
            silver_recycle = float(latest)
            lines.append(f"足银（99.9%）：{silver_recycle:.1f} 元/克" if silver_recycle else "足银（99.9%）：-- 元/克")
        except (ValueError, TypeError):
            lines.append("足银（99.9%）：-- 元/克")

        return "\n".join(lines)

    @filter.command("黄金")
    async def gold_price(self, event: AstrMessageEvent):
        """黄金价格查询指令，用法：/黄金"""
        yield event.plain_result("🔍 正在查询黄金价格...")

        try:
            data = await self._fetch_gold_price()
            result_text = self._format_gold_price(data)
            yield event.plain_result(result_text)
        except RuntimeError as e:
            yield event.plain_result(f"❌ 查询失败：{e}")
        except json.JSONDecodeError:
            yield event.plain_result("❌ 数据解析失败，请稍后重试。")
        except Exception as e:
            logger.error(f"黄金价格查询错误: {e}")
            yield event.plain_result(f"❌ 发生未知错误：{e}")

    @filter.command("白银")
    async def silver_price(self, event: AstrMessageEvent):
        """白银价格查询指令，用法：/白银"""
        yield event.plain_result("🔍 正在查询白银价格...")

        try:
            data = await self._fetch_gold_price()
            result_text = self._format_silver_price(data)
            yield event.plain_result(result_text)
        except RuntimeError as e:
            yield event.plain_result(f"❌ 查询失败：{e}")
        except json.JSONDecodeError:
            yield event.plain_result("❌ 数据解析失败，请稍后重试。")
        except Exception as e:
            logger.error(f"白银价格查询错误: {e}")
            yield event.plain_result(f"❌ 发生未知错误：{e}")

    # ==================== 生命周期 ====================

    async def terminate(self):
        """插件卸载时清理资源"""
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("基金助手插件已卸载，资源已释放。")


# ponytail: 最小自检——验证注释锚点正则能从真实 JS 片段取出规模变动数据。
# 直接 `python main.py` 运行；被 AstrBot 加载时 __name__ != "__main__"，不执行。
if __name__ == "__main__":
    _sample = (
        '/*规模变动 mom-较上期环比*/var Data_fluctuationScale = '
        '{"categories":["2025-03-31","2025-06-30","2025-09-30","2025-12-31","2026-03-31"],'
        '"series":[{"y":19.28,"mom":"-16.48%"},{"y":19.87,"mom":"3.10%"},'
        '{"y":21.16,"mom":"6.49%"},{"y":25.06,"mom":"18.44%"},{"y":27.22,"mom":"8.62%"}]};'
    )
    _r = FundPlugin._extract_fluctuation_scale(_sample)
    assert _r is not None, "注释锚点未匹配到 Data_fluctuationScale"
    assert _r["categories"][-1] == "2026-03-31", "categories 取值错误"
    assert _r["series"][-1]["y"] == 27.22 and _r["series"][-1]["mom"] == "8.62%", "series 取值错误"
    # 反例：无注释锚点时应返回 None
    assert FundPlugin._extract_fluctuation_scale("var Data_fluctuationScale = {};") is None, "应仅匹配带注释锚点的变量"

    # 回归自检：_parse_json_var 不得误删值内 https:// URL（曾导致
    # Data_currentFundManager 的 pic 字段未闭合、经理数据返回空列表）
    # _parse_json_var 仅经 self 调用静态方法 _extract_raw_var，故以类本身作 self
    _mgr_js = (
        'var Data_currentFundManager = [{"id":"30304626",'
        '"pic":"https://pdf.dfcfw.com/pdf/H8_30304626_1.PNG",'
        '"name":"金梓才","star":5,"workTime":"11年又227天",'
        '"fundSize":"89.09亿（13只基金）",'
        '"power":{"avr":"85.29","category":["经验值","收益率","抗风险","稳定性","择时能力"],'
        '"data":[95.10,99.90,62.60,50.20,94.50]},'
        '"profit":{"categories":["任期收益","同类平均","沪深300"],'
        '"series":[{"data":[{"name":null,"color":"#7cb5ec","y":1766.3978}]}]}}];'
    )
    _parsed = FundPlugin._parse_json_var(FundPlugin, _mgr_js, "Data_currentFundManager")
    assert _parsed and isinstance(_parsed, list) and len(_parsed) == 1, "经理数据解析失败"
    assert _parsed[0]["pic"] == "https://pdf.dfcfw.com/pdf/H8_30304626_1.PNG", "pic URL 被误删"
    assert _parsed[0]["name"] == "金梓才", "name 取值错误"
    assert _parsed[0]["power"]["category"][0] == "经验值", "power.category 取值错误"
    print("self-check ok")

