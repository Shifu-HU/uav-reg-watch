"""本地模型处理管线(Ollama + Qwen3.5 小模型)。

设计目标: 为无显卡电脑与未来手机端适配 —— 默认使用 0.8B 极小模型，
逐级降级到 2B / 4B。所有推理都在本地完成，不联网。

职责:
  1. 生成 2-3 行中文摘要
  2. 归类到固定主题
  3. 判定重要度(1-3)
  4. 识别适用地区(供 geo.py 二次校验)

模型不可用时全部降级为规则法，保证软件在无模型环境下依然可用。
"""

from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx

from .models import CATEGORY_KEYWORDS, RegItem

log = logging.getLogger("uavwatch.llm")

CATEGORIES = list(CATEGORY_KEYWORDS.keys()) + ["其他"]

# region 兜底用的城市表 —— 覆盖直辖市 + 主要低空经济城市。
# 正文/来源里出现这些名字，region 就不该是"全国"。
_KNOWN_CITIES = [
    "深圳", "广州", "北京", "上海", "天津", "重庆",
    "成都", "杭州", "南京", "武汉", "西安", "苏州",
    "合肥", "长沙", "郑州", "济南", "青岛", "沈阳",
    "哈尔滨", "长春", "昆明", "贵阳", "南宁", "海口",
    "福州", "厦门", "南昌", "太原", "石家庄", "兰州",
    "西宁", "银川", "乌鲁木齐", "拉萨", "呼和浩特",
    "珠海", "东莞", "佛山", "惠州", "中山", "江门",
]

SYSTEM_PROMPT = (
    "你是无人机法规情报分析助手，服务深圳的飞手。你只输出 JSON，"
    "不输出任何解释。严格忠实原文，绝不编造原文没有的内容。"
)

USER_TEMPLATE = """分析下面这条中国无人机相关法规/通知，只输出一个 JSON 对象。

标题: {title}
来源: {source}
日期: {date}
正文节选: {body}

JSON 字段要求:
- "summary": 用 2 句中文说清这条规定**要求什么、影响谁**，不超过 90 字。
  只能概括原文写明的内容，不能添加原文没有的要求或推论。
- "category": 从这些里选一个: 空域管理 / 飞行审批 / 实名登记 / 驾驶员资质 / 禁飞区 / 处罚案例 / 适航认证 / 产业政策 / 运行管理 / 其他
- "importance": 整数 1/2/3，3=对飞手日常有强制约束 2=有一定影响 1=一般信息
- "effective_at": 生效日期，格式 YYYY-MM-DD，没写就输出空字符串。
  注意"即日起至X月X日"= 通告发布时已生效，结束于 X 月 X 日，
  生效日期不是 X 月 X 日。
- "region": 适用地区。发文机关是市/区级（如"深圳市公安局"）或正文
  明确提到具体城市 -> 写该城市名；只有国家部委发文且面向全国 ->
  才写"全国"。来源地名可作参考。

只输出 JSON:"""


class LocalLLM:
    def __init__(self, cfg):
        self.host = cfg.host.rstrip("/")
        self.model = cfg.model
        self.fallbacks = list(cfg.fallback_models or [])
        self.timeout = cfg.timeout
        self.think = cfg.think
        self.concurrency = max(1, int(cfg.concurrency))
        self.summary_max_chars = cfg.summary_max_chars
        self._active: str = ""
        self._available: bool | None = None
        self._client = httpx.Client(timeout=self.timeout)

    # ------------------------------------------------------------------
    def _candidates(self) -> list[str]:
        return [self.model, *self.fallbacks]

    def list_models(self) -> list[str]:
        try:
            r = self._client.get(f"{self.host}/api/tags", timeout=8)
            if r.status_code == 200:
                return [m["name"] for m in r.json().get("models", [])]
        except Exception as e:  # noqa: BLE001
            log.debug("ollama 不可达: %s", e)
        return []

    def probe(self) -> tuple[bool, str]:
        """探测 Ollama 与模型可用性，选定实际使用的模型。"""
        models = self.list_models()
        if not models:
            self._available = False
            return False, "Ollama 未运行或不可达(默认 http://127.0.0.1:11434)"
        for cand in self._candidates():
            base = cand.split(":")[0]
            for m in models:
                # 允许 "uav-reg" 命中 "uav-reg:latest" 这种带 tag 的名字
                if m == cand or m.split(":")[0] == base:
                    self._active = m
                    self._available = True
                    return True, f"使用模型 {m}"
        # 都没命中: 用本地任意一个小模型兜底
        # 不限定 qwen —— 领域模型 uav-reg 名字里没有 qwen，
        # 限定成 qwen 会让它在这个分支被漏掉。
        for m in models:
            if "qwen" in m.lower() or "uav" in m.lower():
                self._active = m
                self._available = True
                return True, f"降级使用本地模型 {m}"
        self._available = False
        return False, f"本地无可用模型。已安装: {', '.join(models) or '无'}"

    @property
    def active_model(self) -> str:
        return self._active or self.model

    # ------------------------------------------------------------------
    def generate(self, prompt: str, *, model: str = "", max_tokens: int = 400) -> str:
        if self._available is False:
            return ""
        payload: dict[str, Any] = {
            "model": model or self.active_model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": max_tokens},
        }
        if not self.think:
            # qwen3 系列默认带思考；关掉可显著提速
            payload["think"] = False
        try:
            r = self._client.post(f"{self.host}/api/generate", json=payload)
            if r.status_code != 200:
                log.debug("generate HTTP %s", r.status_code)
                return ""
            data = r.json()
            return (data.get("response") or "").strip()
        except Exception as e:  # noqa: BLE001
            log.debug("generate 失败: %s", e)
            return ""

    # ------------------------------------------------------------------
    def analyze(self, item: RegItem) -> dict[str, Any]:
        """分析单条。模型不可用时返回规则法结果。"""
        body = (item.content or item.fetch_excerpt or "")[:1200]
        prompt = USER_TEMPLATE.format(
            title=item.title[:160],
            source=item.source_name or item.source,
            date=item.published_at or "未知",
            body=body or "(仅有标题)",
        )
        raw = self.generate(f"{SYSTEM_PROMPT}\n\n{prompt}", max_tokens=350)
        parsed = self._parse_json(raw)
        if parsed:
            return self._normalize(parsed, item)
        return self._rule_based(item)

    # ------------------------------------------------------------------
    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any] | None:
        if not raw:
            return None
        # 去掉思考块
        raw = re.sub(r"<think.*?</think>", "", raw, flags=re.S)
        # 抓第一个 {...}
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            return None
        txt = m.group(0)
        try:
            return json.loads(txt)
        except Exception:  # noqa: BLE001
            pass
        # 修常见问题: 单引号 / 尾逗号
        try:
            fixed = re.sub(r",\s*([}\]])", r"\1", txt.replace("'", '"'))
            return json.loads(fixed)
        except Exception:  # noqa: BLE001
            return None

    # ------------------------------------------------------------------
    def _normalize(self, d: dict[str, Any], item: RegItem) -> dict[str, Any]:
        summary = str(d.get("summary") or "").strip()
        if not summary:
            summary = self._fallback_summary(item)
        summary = summary[: self.summary_max_chars + 40]

        cat = str(d.get("category") or "").strip()
        if cat not in CATEGORIES:
            cat = self._rule_category(item.title + " " + (item.content or ""))

        try:
            imp = int(d.get("importance", 2))
        except Exception:  # noqa: BLE001
            imp = 2
        # importance 规则下限 —— 小模型打分抖动大（同一条禁飞通告
        # 这次 3 下次 1）。禁飞/管控类不管 AI 打几分至少 2，
        # 官方法规原文至少 3；用户："AI 还得整"。
        rule_imp = self._rule_importance(item.title)
        if rule_imp >= 2 and imp < 2:
            imp = 2
        if rule_imp == 3 and imp < 3 and not any(
                k in (item.title or "") for k in LocalLLM._NEWS_MARKERS):
            imp = 3
        imp = min(3, max(1, imp))

        eff = str(d.get("effective_at") or "").strip()
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", eff):
            eff = ""

        region = str(d.get("region") or "").strip() or "全国"
        # region 兜底 —— 小模型常把市级通告判成"全国"（实测：
        # 深圳公安局的通告被标全国）。发文机关名/正文里出现具体
        # 城市名时，以城市为准。
        if region == "全国":
            blob = f"{item.source_name or item.source or ''} {item.title} {item.content or ''}"
            for _city in _KNOWN_CITIES:
                if _city in blob:
                    region = _city
                    break
        return {"summary": summary, "category": cat, "importance": imp,
                "effective_at": eff, "region": region, "ai": True}

    # ------------------------------------------------------------------
    def _rule_based(self, item: RegItem) -> dict[str, Any]:
        text = item.title + " " + (item.content or "")
        return {
            "summary": self._fallback_summary(item),
            "category": self._rule_category(text),
            "importance": self._rule_importance(item.title),
            "effective_at": self._rule_effective(text),
            "region": "全国",
            "ai": False,
        }

    @staticmethod
    def _fallback_summary(item: RegItem) -> str:
        base = (item.content or "").strip()
        if base:
            s = re.sub(r"\s+", " ", base)
            return (s[:110] + "…") if len(s) > 110 else s
        return f"《{item.title}》—— 来自{item.source_name or item.source}的无人机相关规定。"

    @staticmethod
    def _rule_category(text: str) -> str:
        best, best_n = "其他", 0
        for cat, kws in CATEGORY_KEYWORDS.items():
            n = sum(1 for k in kws if k in text)
            if n > best_n:
                best, best_n = cat, n
        return best

    # 新闻/解读类特征 —— 这类内容即使标题带"管理办法"也不是法规原文，
    # 不能判为"重大"，否则媒体文章会把重大档位灌满，分层失去意义。
    _NEWS_MARKERS = (
        "必看", "解读", "详解", "科普", "收藏", "一文看懂", "盘点", "汇总",
        "问答", "热点", "解读|", "|", "_", "泪雪网", "头条", "知乎", "百家号",
        "新浪", "搜狐", "网易", "腾讯", "澎湃", "界面", "观察者",
        "影响", "意味着", "怎么回事", "真的吗", "吗?", "吗？", "!", "！",
    )
    # 法规原文特征 —— 有这些才是真正要遵守的文件
    _LAW_MARKERS = (
        "条例", "管理办法", "管理规定", "实施细则", "部令", "公告", "通告",
        "咨询通告", "适航指令", "国家标准", "行业标准", "CCAR", "MH/T",
    )

    @staticmethod
    def _rule_importance(title: str) -> int:
        t = title or ""
        is_news = any(k in t for k in LocalLLM._NEWS_MARKERS)
        is_law = any(k in t for k in LocalLLM._LAW_MARKERS)
        # 原文级法规 -> 重大
        if is_law and not is_news:
            return 3
        # 新闻解读里提到具体强制措施 -> 关注
        if is_law or any(k in t for k in ("禁飞", "处罚", "罚款", "实名登记", "执照")):
            return 2
        return 1

    @staticmethod
    def _rule_effective(text: str) -> str:
        m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日\s*起(?:施行|实施|生效)",
                      text)
        if m:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return ""

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    @staticmethod
    def _system_busy() -> bool:
        """电脑现在忙不忙 —— 用户："偶尔对着监视电脑，选相对不耗能的
        时候计算"。

        0.8b 模型本身很小，但并发分析几百条时 CPU 会持续拉满，
        用户在用电脑时就会卡。判定条件（任一命中就算忙）：
          1. 使用交流电的台式机不看这条；笔记本用电池 → 忙（省电）；
          2. CPU 总占用 > 60%（psutil 没有就退回 typeperf 采样 1 秒）。
        忙则整批等 20 秒再试，最多等 10 分钟 —— 深夜/锁屏时
        CPU 自然空闲，批次会立刻放行。
        """
        try:
            import psutil
            # 用电池 → 省电优先
            try:
                bat = psutil.sensors_battery()
                if bat and not bat.power_plugged:
                    return True
            except Exception:          # noqa: BLE001
                pass
            return psutil.cpu_percent(interval=0.5) > 60
        except Exception:              # noqa: BLE001  psutil 不在就用 typeperf
            try:
                import subprocess
                out = subprocess.run(
                    ["typeperf", r"\Processor(_Total)\% Processor Time",
                     "-sc", "1"], capture_output=True, text=True, timeout=15)
                for line in out.stdout.splitlines():
                    if "," in line and '"' in line:
                        # typeperf 行: "计数器名","数值" —— 取**最后**
                        # 一个能转成数的列（有的机器数值列带引号有的不带）
                        for cell in reversed(line.split(',')):
                            v = cell.strip().strip('"')
                            try:
                                return float(v) > 60
                            except ValueError:
                                continue
                        return False
            except Exception:          # noqa: BLE001
                return False
        return False

    def _wait_for_quiet(self, max_wait_s: int = 600) -> None:
        """等系统空闲（最多 max_wait_s），期间每 20 秒探一次。"""
        waited = 0
        while self._system_busy() and waited < max_wait_s:
            log.debug("系统忙/用电池，AI 分析让路 20s（已等 %ds）", waited)
            time.sleep(20)
            waited += 20

    def analyze_batch(self, items: list[RegItem],
                      progress=None) -> list[RegItem]:
        """并发分析一批条目，就地写回结果。"""
        if not items:
            return items
        # 低功耗调度：先等一个不忙的窗口再开工
        self._wait_for_quiet()
        done = 0
        with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
            futs = {pool.submit(self.analyze, it): it for it in items}
            for fut in as_completed(futs):
                it = futs[fut]
                try:
                    res = fut.result()
                except Exception as e:  # noqa: BLE001
                    log.debug("analyze 异常: %s", e)
                    res = self._rule_based(it)
                it.ai_summary = res["summary"]
                it.category = res["category"]
                it.importance = res["importance"]
                # region 之前判了但没写回 —— 模型/兜底算出的地区
                # 白算了，地方通告全显示"全国"。
                if res.get("region"):
                    it.region = res["region"]
                if res.get("effective_at"):
                    it.effective_at = res["effective_at"]
                it.ai_processed = True
                done += 1
                if progress:
                    progress(done, len(items), it)
        return items
