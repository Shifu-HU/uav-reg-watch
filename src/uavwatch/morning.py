"""晨报系统 —— 每日自动生成的"今日必读"情报简报。

与普通列表的区别:
  1. 分层: 必读(重大/即将生效) -> 关注(重要) -> 参考(一般) -> 兜底(历史未读)
  2. 有行动: 每条给出"对飞手意味着什么"的一句话提示
  3. 有时效: 突出即将生效(未来 30 天内)的法规, 提醒赶在生效前办手续
  4. 有对比: 与昨日/上周对比, 让用户感知趋势
  5. 多格式: Markdown(存档) + HTML(阅读) + 纯文本(通知栏)

生成时机: 每日首次搜索完成后自动生成, 也可手动触发。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from .brief_common import (commonsense_items, is_business, map_link,
                           no_fly_lines, summarize_no_fly)
from .knowledge import KnowledgeBase, scan_no_fly

log = logging.getLogger("uavwatch.morning")

# 分类 -> 给飞手的行动提示
ACTION_HINTS = {
    "实名登记": "确认你的无人机已完成实名登记并在机身粘贴登记标志",
    "驾驶员资质": "核查你的执照类别是否覆盖当前机型与作业场景",
    "空域管理": "起飞前查一遍常飞区域是否仍在适飞空域内",
    "禁飞区": "更新你常用的飞行地图，避开新增禁飞/限飞区域",
    "飞行审批": "确认审批流程或申报渠道是否有变化",
    "适航认证": "确认你的机型是否仍在合规名录内",
    "运行管理": "核对日常运行记录与作业规范要求",
    "处罚案例": "了解执法尺度，避免同类违规",
    "产业政策": "关注补贴/试点/产业扶持的申报窗口",
}

# 重要性分层
TIER_MUST = "必读"
TIER_WATCH = "关注"
TIER_REF = "参考"
TIER_BACKLOG = "历史补课"

PREVIEW_DAYS = 30          # 未来展望窗口: 还没生效但快了的
RECENT_DAYS = 90           # 回溯窗口: 刚生效的也要提醒(很多人还没跟上)


class MorningBrief:
    def __init__(self, cfg, store, data_dir: Path):
        self.cfg = cfg
        self.store = store
        self.data_dir = data_dir
        self.brief_dir = data_dir / "briefs"
        self.brief_dir.mkdir(parents=True, exist_ok=True)
        # 飞行常识知识库（内置，不依赖搜索）
        self.kb = KnowledgeBase()
        # 临时禁飞预警缓存
        self._kb_no_fly: list = []
        # 抓正文用（禁飞的起止时间几乎都在正文里，标题常常没有）
        self._fetcher = None
        self._llm = None

    # ------------------------------------------------------------------
    # 已生成晨报的读取（界面用）
    #
    # 界面启动时**不能**同步跑 build() —— 里面要调 scan_no_fly，
    # 也就是 20 次本地模型推理，会把窗口卡死（实测"打开就卡死"）。
    # 所以先读上次的成品顶上，再后台重算。
    # ------------------------------------------------------------------
    def txt_path(self, day: str = "") -> Path | None:
        """最新一份晨报 txt 的路径；一份都没有时返回 None。"""
        day = day or date.today().isoformat()
        p = self.brief_dir / f"morning-{day}.txt"
        if p.exists():
            return p
        # 今天的还没生成 —— 退回到最近一份，总比空白强
        files = sorted(self.brief_dir.glob("morning-*.txt"), reverse=True)
        return files[0] if files else None

    def html_path(self, day: str = "") -> Path | None:
        """最新一份晨报 HTML 的路径；一份都没有时返回 None。

        与 txt_path 同构。界面把它直接交给内嵌浏览器渲染 ——
        这样看到的配色/卡片和"系统浏览器打开"完全一致。
        """
        day = day or date.today().isoformat()
        p = self.brief_dir / f"morning-{day}.html"
        if p.exists():
            return p
        files = sorted(self.brief_dir.glob("morning-*.html"), reverse=True)
        return files[0] if files else None

    def cached_headline(self, day: str = "") -> str:
        """从已生成的 txt 里取标题行，避免为了标题再跑一遍完整 build。

        txt 的第一行就是标题（render_txt 保证如此）。
        """
        p = self.txt_path(day)
        if not p:
            return "今日晨报"
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    return line
        except Exception:                  # noqa: BLE001
            pass
        return "今日晨报"

    # ------------------------------------------------------------------
    def _get_llm(self):
        """惰性建本地模型客户端 —— 只在真要核查要点时才连 Ollama。"""
        if self._llm is None:
            try:
                from .llm import LocalLLM
                self._llm = LocalLLM(self.cfg.llm)
            except Exception as e:             # noqa: BLE001
                log.warning("本地模型初始化失败，跳过要点核查: %s", e)
                self._llm = False
        return self._llm or None

    # ------------------------------------------------------------------
    def _get_fetcher(self):
        """惰性建抓取器 —— 只在真有本地禁飞需要补全时才联网。"""
        if self._fetcher is None:
            try:
                from .fetcher import Fetcher
                # 超时压到 8 秒 —— 政府站多数 2 秒内返回，
                # 慢的站点不值得为一条禁飞等 15 秒。
                self._fetcher = Fetcher(delay=0.3, timeout=8.0)
            except Exception as e:             # noqa: BLE001
                log.warning("抓取器初始化失败，跳过正文补全: %s", e)
                self._fetcher = False
        return self._fetcher or None

    # ------------------------------------------------------------------
    @staticmethod
    def _is_biz(r: dict) -> bool:
        """这条是不是商业资讯。分类字段和内容特征双重判断。"""
        if (r.get("category") or "") == "产业政策":
            return True
        return is_business(r.get("title") or "",
                           r.get("content") or r.get("ai_summary") or "")

    def collect(self, day: str = "", include_common: bool = True,
                include_industry: bool = False) -> dict:
        """汇总晨报所需的全部数据。

        include_industry=False 时滤掉商业资讯（产业规划、招商、
        物流航线）。这些面向企业和投资人，个人飞手看了没用。
        """
        day = day or date.today().isoformat()
        today = datetime.fromisoformat(day).date()

        # 今日新增(本地相关)
        fresh = self.store.query(since=day, only_local=True, limit=600)
        # 历史未读情报池(补足每日配额)
        quota = self.cfg.schedule.min_daily_items
        pool = self.store.unread_pool(limit=max(0, quota - len(fresh)))

        if not include_industry:
            fresh = [r for r in fresh if not self._is_biz(r)]
            pool = [r for r in pool if not self._is_biz(r)]

        seen: set[str] = {r["uid"] for r in fresh}
        backlog = [r for r in pool if r["uid"] not in seen]
        all_rows = fresh + backlog

        stats = self.store.stats()
        by_cat: dict[str, list] = {}
        for r in all_rows:
            by_cat.setdefault(r["category"] or "其他", []).append(r)

        # 分层
        must = [r for r in all_rows if int(r["importance"] or 2) == 3]
        watch = [r for r in all_rows if int(r["importance"] or 2) == 2]
        ref = [r for r in all_rows if int(r["importance"] or 2) <= 1]

        # 时效提醒: 未来 PREVIEW_DAYS 天内将生效的 + 近 RECENT_DAYS 天内已生效的。
        # 只算"未来将生效"会几乎永远为空(法规日期多在过去)，而"刚生效"
        # 恰恰是飞手最需要知道、最容易错过的 —— 所以两头都要看。
        soon: list[dict] = []
        for r in all_rows:
            eff = (r.get("effective_at") or "").strip()
            try:
                d = datetime.fromisoformat(eff[:10]).date()
            except Exception:             # noqa: BLE001
                continue
            delta = (d - today).days
            if 0 <= delta <= PREVIEW_DAYS:
                soon.append({**r, "_days": delta, "_state": "pending"})
            elif -RECENT_DAYS <= delta < 0:
                soon.append({**r, "_days": delta, "_state": "effective"})
        # 将生效的排前面(按临近程度)，已生效的按新鲜程度排
        soon.sort(key=lambda x: (x["_state"] != "pending", abs(x["_days"])))

        # 临时禁飞预警 —— 扫"今日本地相关"的全部条目，
        # 不限于时效窗口：一条三个月后才生效的本地禁飞通告，
        # 恰恰是现在就该知道、好提前安排作业的。
        local_all = self.store.query(only_local=True, include_ignored=True,
                                     limit=1200)
        city = self.cfg.location.city or ""
        province = self.cfg.location.province or ""
        # 每条补全/核验都要联网，用配置控制上限（默认 12）
        cw = self.cfg.crawler
        no_fly = scan_no_fly(
            local_all, city, province, today,
            fetcher=self._get_fetcher(),
            enrich_limit=int(cw.get("enrich_limit", 12)),
            verify_limit=(int(cw.get("verify_limit", 12))
                          if cw.get("verify_sources", True) else 0),
            llm=self._get_llm(),
            llm_limit=int(cw.get("llm_check_limit", 20)))

        # 常识：核心必读 + 与所在地相关的。
        # 用户可以在界面上关掉这一节（见 common() 的 include_common）。
        if include_common:
            essentials = self.kb.essentials(limit=6)
            local_notes = [n for n in self.kb.region_notes(city, province)
                           if n not in essentials]
        else:
            essentials, local_notes = [], []
        # 「在 X 之后需要 Y 方可飞行」句式 —— 比罗列法规名有用。
        commonsense = commonsense_items() if include_common else []

        return {
            "day": day,
            "fresh": fresh,
            "backlog": backlog,
            "all": all_rows,
            "by_cat": by_cat,
            "must": must, "watch": watch, "ref": ref,
            "soon": soon,
            "stats": stats,
            "quota": quota,
            "no_fly": no_fly,
            "essentials": essentials,
            "local_notes": local_notes,
            "commonsense": commonsense,
            "map": map_link(),
        }

    # ------------------------------------------------------------------
    def build(self, day: str = "", include_common: bool = True,
              include_industry: bool = False) -> tuple[Path, Path, Path, dict]:
        """生成晨报。返回 (md, html, txt, 数据)。"""
        d = self.collect(day, include_common=include_common,
                         include_industry=include_industry)
        day = d["day"]
        md_path = self.brief_dir / f"morning-{day}.md"
        html_path = self.brief_dir / f"morning-{day}.html"
        txt_path = self.brief_dir / f"morning-{day}.txt"

        md_path.write_text(self.render_md(d), encoding="utf-8")
        html_path.write_text(self.render_html(d), encoding="utf-8")
        txt_path.write_text(self.render_txt(d), encoding="utf-8")
        self.store.set_meta("last_morning_brief", day)
        log.info("晨报已生成: %s (%d 条)", html_path, len(d["all"]))
        return md_path, html_path, txt_path, d

    # ------------------------------------------------------------------
    def headline(self, day: str = "", data: dict | None = None) -> str:
        """给桌面通知用的一句话摘要。

        传入 data 可复用已算好的结果 —— 否则会重新 collect 一次，
        既慢，又会让调用方注入的临时数据(如禁飞预警)丢失。
        """
        d = data if data is not None else self.collect(day)
        n_must = len(d["must"])
        n_all = len(d["all"])
        parts = [f"今日 {n_all} 条无人机法规动态"]
        # 本地禁飞最优先 —— 直接决定"今天能不能飞"
        crit = [a for a in d.get("no_fly") or [] if a.level == "critical"]
        if crit:
            a0 = crit[0]
            n_local = sum(1 for a in (d.get("no_fly") or []) if a.in_my_area)
            extra = f"，另有 {n_local - 1} 条本地禁飞" if n_local > 1 else ""
            if a0.active_now:
                parts.insert(0, f"⚠ {a0.area_text} 正在禁飞（至 "
                                f"{a0.end.isoformat() if a0.end else '另行通知'}）{extra}")
            elif a0.days_until is not None and 0 < a0.days_until <= 7:
                parts.insert(0, f"⚠ {a0.area_text} {a0.days_until} 天后开始禁飞{extra}")
            elif a0.days_until is not None and a0.days_until > 7:
                parts.insert(0, f"⚠ {a0.area_text} {a0.days_until} 天后有禁飞{extra}")
            else:
                parts.insert(0, f"⚠ {a0.area_text} 有临时禁飞{extra}")
        if n_must:
            parts.append(f"其中 {n_must} 条重大")
        pending = [r for r in d["soon"] if r["_state"] == "pending"]
        if pending:
            n = pending[0]
            if n["_days"] == 0:
                parts.append(f"《{n['title'][:18]}》今天生效")
            else:
                parts.append(f"最近 {n['_days']} 天后有新规生效")
        elif d["soon"]:
            parts.append(f"近期有 {len(d['soon'])} 条已生效规定值得复核")
        return " · ".join(parts)

    # ------------------------------------------------------------------
    @staticmethod
    def _when_text(row: dict) -> str:
        """把 _days 渲染成人话。"""
        n = row.get("_days", 0)
        if row.get("_state") == "pending":
            if n == 0:
                return "今天生效"
            if n == 1:
                return "明天生效"
            return f"{n} 天后生效"
        if n == 0:
            return "今天刚生效"
        return f"已生效 {-n} 天"

    @staticmethod
    def _hint(row: dict) -> str:
        return ACTION_HINTS.get(row.get("category") or "", "建议浏览确认是否影响你的飞行计划")

    @staticmethod
    def _meta_line(row: dict) -> str:
        bits = [row.get("source_name") or row.get("source") or ""]
        if row.get("published_at"):
            bits.append("发布 " + row["published_at"])
        if row.get("effective_at"):
            bits.append("生效 " + row["effective_at"])
        bits.append(row.get("region") or "全国")
        return " · ".join(b for b in bits if b)

    # ------------------------------------------------------------------
    def render_md(self, d: dict) -> str:
        L: list[str] = []
        A = L.append
        st = d["stats"]
        A(f"# 无人机新规晨报 · {d['day']}")
        A("")
        A(f"> {self.headline(d['day'], d)}")
        A("")
        A("| 今日新增 | 历史补课 | 可推送合计 | 累计收录 | 已过滤外地 |")
        A("|---|---|---|---|---|")
        A(f"| {len(d['fresh'])} | {len(d['backlog'])} | {len(d['all'])} "
          f"| {st['total']} | {st['filtered']} |")
        A("")
        A(f"所在地 **{self.cfg.location.city or '(未设置)'}**"
          f" —— 已自动剔除非本地规定 {st['filtered']} 条")
        A("")

        # ---- 临时禁飞（最高优先级）----
        nf = d.get("no_fly") or []
        crit = [a for a in nf if a.level == "critical"]
        warn = [a for a in nf if a.level == "warn"]
        if crit or warn:
            A("## 🚫 临时禁飞 · 重点")
            A("")
            for a in (crit + warn)[:8]:
                flag = "🔴 **在你辖区内**" if a.in_my_area else "⚪ 外地"
                if a.active_now:
                    state = "**【正在禁飞中】**"
                elif a.days_until is not None and a.days_until > 0:
                    state = f"**{a.days_until} 天后开始**"
                elif a.days_until is not None and a.days_until < 0:
                    state = "已结束"
                else:
                    state = ""
                A(f"### {flag} {a.area_text} {state}")
                A("")
                A(f"- **禁飞时段**：{a.when_text}")
                # 范围级别前置 —— "只是几条路"和"全市"对飞手是天壤之别
                _sc = getattr(a, "scope", "")
                if _sc:
                    A(f"- **范围级别**：{_sc}")
                if getattr(a, "area", ""):
                    A(f"- **禁飞范围**：{a.area}")
                A(f"- **通告**：{a.title}")
                if getattr(a, "key_point", ""):
                    A(f"- **要点**：{a.key_point}")
                if getattr(a, "snippet", ""):
                    A(f"- **详情**：{a.snippet}")
                if getattr(a, "verify_reason", ""):
                    A(f"- **来源**：{a.verify_reason}")
                A("")
            A("")

        if d["soon"]:
            A("## 时效提醒")
            A("")
            for r in d["soon"][:10]:
                A(f"- **{self._when_text(r)}**（{r['effective_at']}）{r['title']}")
                A(f"  - {self._hint(r)}")
            A("")

        # ---- 飞行常识（固定输出，不需要搜索）----
        ess = d.get("essentials") or []
        notes = d.get("local_notes") or []
        if ess or notes:
            A("## 📘 飞行常识")
            A("")
            for it in ess:
                A(f"### {it['title']}")
                A("")
                A(f"> {it.get('short', '')}")
                A("")
                if it.get("detail"):
                    for line in it["detail"].strip().splitlines():
                        if line.strip():
                            A(line.strip())
                    A("")
                if it.get("why"):
                    A(f"*为什么重要*：{it['why']}")
                    A("")
            for it in notes:
                A(f"### {it['title']}（本地）")
                A("")
                A(f"> {it.get('short', '')}")
                A("")
                if it.get("detail"):
                    for line in it["detail"].strip().splitlines():
                        if line.strip():
                            A(line.strip())
                    A("")
            A("")

        for tier, rows in ((TIER_MUST, d["must"]),
                           (TIER_WATCH, d["watch"]),
                           (TIER_REF, d["ref"])):
            if not rows:
                continue
            A(f"## {tier}（{len(rows)} 条）")
            A("")
            for r in rows:
                A(f"### {r['title']}")
                A("")
                A(self._meta_line(r))
                A("")
                if r.get("ai_summary"):
                    A(f"> {r['ai_summary']}")
                    A("")
                A(f"**怎么办**: {self._hint(r)}")
                A("")
                A(f"[查看原文]({r['url']})")
                A("")

        A("---")
        A("")
        A(f"按分类: " + " · ".join(
            f"{k} {len(v)}" for k, v in
            sorted(d["by_cat"].items(), key=lambda kv: -len(kv[1]))))
        A("")
        A("*本晨报由无人机新规雷达自动生成，内容来自民航局官网及公开检索结果，"
          "仅供参考，请以官方原文为准。*")
        return "\n".join(L)

    # ------------------------------------------------------------------
    def render_txt(self, d: dict) -> str:
        """纯文本版，适合通知栏/邮件。"""
        L: list[str] = []
        A = L.append
        A(f"无人机新规晨报 · {d['day']}")
        A("=" * 40)
        A(self.headline(d["day"], d))
        A("")
        if d["soon"]:
            A("【时效提醒】")
            for r in d["soon"][:6]:
                A(f"  · {self._when_text(r)} {r['title'][:40]}")
            A("")
        # 禁飞预警 —— 对飞手最要紧，排在法规前面。
        # 格式固定成「区域 ｜ 时间 ｜ 来源」三件套：
        # 有这三样飞手才能判断"跟不关我的事"，其余都是噪音。
        nf = [a for a in (d.get("no_fly") or []) if a.in_my_area]
        if nf:
            A("【本地禁飞预警】")
            for a in nf[:6]:
                A(f"  ⚠ {summarize_no_fly(a)}")
                A(f"    {a.title[:46]}")
                if a.key_point:
                    A(f"    要点：{a.key_point[:60]}")
                A("    详情见官方通告原文")
            A("")

        for tier, rows in ((TIER_MUST, d["must"]), (TIER_WATCH, d["watch"])):
            if not rows:
                continue
            A(f"【{tier}】{len(rows)} 条")
            for r in rows[:10]:
                A(f"  · {r['title'][:44]}")
                A(f"    {self._meta_line(r)}")
                if r.get("key_action"):
                    A(f"    要点：{r['key_action'][:60]}")
            A("")

        # 飞行常识 —— 用「在 X 之后需要 Y 方可飞行」的句式，
        # 这比"某某规定解读"有用得多：飞手要的是行动指引。
        cs = d.get("commonsense") or []
        if cs:
            A("【飞行常识】")
            for it in cs:
                A(f"  · {it['sentence']}")
                if it.get("why"):
                    A(f"    {it['why'][:66]}")
                if it.get("url_text"):
                    A(f"    详情见：{it['url_text']}")
            A("")
        notes = d.get("local_notes") or []
        if notes:
            A("【本地补充】")
            for it in notes:
                A(f"  · {it['title']}")
                if it.get("short"):
                    A(f"    {it['short'][:70]}")
            A("")

        A(f"合计 {len(d['all'])} 条，详见晨报网页版。")
        return "\n".join(L)

    # ------------------------------------------------------------------
    def _fly_verdict(self, d: dict) -> str:
        """顶部飞行建议横幅：不能飞 / 不建议飞 / 可以飞。

        判定：本地活动禁飞(in_my_area+active_now) → 不能飞；
        本地禁飞未开始但 7 天内 → 不建议（临近）；仅外地禁飞或无禁飞 → 可以飞。
        设备等级附注：小型及以上在管控空域本就受限。
        """
        def esc2(s):
            return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;"))

        nf = [a for a in (d.get("no_fly") or []) if a.in_my_area]
        active = [a for a in nf if a.active_now]
        soon = [a for a in nf if not a.active_now and a.days_until is not None
                and 0 <= a.days_until <= 7]
        # 设备等级附注
        lv = ""
        try:
            devs = self.cfg.devices or []
            levels = sorted({x.get("level", "") for x in devs if x.get("level")})
            if levels:
                lv = " · 机型：" + "、".join(levels)
        except Exception:
            pass
        if active:
            a0 = active[0]
            more = f"（另有 {len(active) - 1} 处）" if len(active) > 1 else ""
            cls, ico, main = "v-no", "⛔", "现在不能飞"
            sub = f"{esc2(a0.area_text)} 正在禁飞{more}" + lv
        elif soon:
            a0 = soon[0]
            cls, ico, main = "v-warn", "⚠️", "谨慎飞行 · 先查空域"
            sub = f"{esc2(a0.area_text)} 将于 {a0.days_until} 天后开始禁飞" + lv
        else:
            cls, ico = "v-ok", "✅"
            if nf:
                main = "可以飞（注意范围）"
                sub = "本地有已结束/未生效的禁飞" + lv
            else:
                main = "今天可以飞"
                sub = "未检出你所在区域的禁飞" + lv
        return (f'<div class="verdict {cls}"><div class="v-ico">{ico}</div>'
                f'<div><div class="v-main">{main}</div>'
                f'<div class="v-sub">{sub}</div></div></div>')

    def render_html(self, d: dict, compact: bool = False) -> str:
        def esc(s):
            return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;").replace('"', "&quot;"))

        st = d["stats"]
        city = self.cfg.location.city or "未设置"
        today = d["day"]

        P: list[str] = []
        A = P.append
        A('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">')
        A('<meta name="viewport" content="width=device-width,initial-scale=1">')
        A(f"<title>无人机新规晨报 · {esc(today)}</title><style>")
        A("""
:root{--bg:#0A0B0D;--surface:#111316;--raised:#181A1F;--line:#2A2E35;
--t1:#E9ECF1;--t2:#A2AAB6;--t3:#6F7885;--accent:#5AA2F0;
--must:#FF6B6B;--watch:#FFB224;--ref:#7E8894;--local:#34C08A;}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--t1);font:15px/1.7 "Microsoft YaHei",system-ui,sans-serif;
max-width:880px;margin:0 auto;padding:40px 24px 80px}
h1{font-size:30px;font-weight:700;letter-spacing:-.02em}
.sub{color:var(--t3);margin:8px 0 28px;font-size:14px}
.headline{background:var(--raised);border-left:3px solid var(--accent);
border-radius:0 8px 8px 0;padding:16px 20px;margin-bottom:28px;font-size:15px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));gap:10px;margin-bottom:32px}
.kpi{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:14px 16px}
.kpi b{display:block;font-size:24px;font-weight:700;color:var(--accent);line-height:1.2}
.kpi span{color:var(--t3);font-size:12px}
h2{font-size:19px;font-weight:600;margin:36px 0 16px;padding-bottom:9px;
border-bottom:1px solid var(--line)}
h2 .n{color:var(--t3);font-size:14px;font-weight:400;margin-left:8px}
.soon{border-left:3px solid var(--must);padding:3px 0 3px 14px;margin-bottom:14px}
.wx{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:14px 20px;margin-bottom:16px}
.wx-line{padding:3px 0;font-size:15px}
.soon .when{color:var(--must);font-weight:700;font-size:13px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;
padding:18px 20px;margin-bottom:12px}
.card.must{border-left:3px solid var(--must)}
.card.watch{border-left:3px solid var(--watch)}
.card.ref{border-left:3px solid var(--ref)}
.card h3{font-size:16.5px;font-weight:600;margin-bottom:7px;line-height:1.5}
.meta{color:var(--t3);font-size:12.5px;margin-bottom:10px}
.sum{color:var(--t2);font-size:14px;margin-bottom:12px}
.hint{background:rgba(90,162,240,.09);border-radius:6px;padding:9px 12px;
font-size:13.5px;color:#95C6F8}
.hint::before{content:"怎么办 ";font-weight:700;color:var(--accent)}
a{color:var(--accent);text-decoration:none;font-size:13px}
a:hover{text-decoration:underline}
footer{margin-top:48px;padding-top:20px;border-top:1px solid var(--line);
color:var(--t3);font-size:12.5px;line-height:1.9}

/* ---- 临时禁飞告警 ---- */
.nf{background:linear-gradient(135deg,rgba(255,107,107,.16),rgba(255,107,107,.06));
border:1px solid rgba(255,107,107,.45);border-left:5px solid var(--must);
border-radius:10px;padding:18px 20px;margin-bottom:14px}
.nf.crit{box-shadow:0 0 0 1px rgba(255,107,107,.25),0 6px 22px rgba(255,107,107,.16)}
.nf.warn{border-left-color:var(--watch);border-color:rgba(255,178,36,.4);
background:linear-gradient(135deg,rgba(255,178,36,.12),rgba(255,178,36,.04))}
.nf.local{position:relative}
.nf .tag{display:inline-block;font-size:11.5px;font-weight:700;padding:3px 10px;
border-radius:20px;margin-bottom:10px;letter-spacing:.02em}
.nf.crit .tag{background:var(--must);color:#fff}
.nf.warn .tag{background:var(--watch);color:#231a05}
.nf .where{font-size:19px;font-weight:700;margin-bottom:6px;color:var(--t1)}
.nf .when{font-size:14px;font-weight:700;color:var(--must);margin-bottom:10px}
.nf.warn .when{color:var(--watch)}
.nf .ttl{font-size:14px;color:var(--t2);margin-bottom:10px;line-height:1.6}
.nf .area{background:rgba(0,0,0,.28);border-left:3px solid var(--must);
border-radius:0 6px 6px 0;padding:9px 13px;font-size:13.5px;color:#FFD9D9;
line-height:1.65;margin-bottom:10px}
.nf.warn .area{border-left-color:var(--watch);color:#FFE7C2}
.nf .snip{font-size:12.5px;color:var(--t3);line-height:1.65;margin-bottom:10px;
padding-left:11px;border-left:2px solid var(--line)}
.nf .scope{display:inline-block;padding:3px 11px;border-radius:11px;
font-size:12px;font-weight:700;margin-bottom:9px}
.nf .scope-on{background:rgba(245,158,11,.24);color:#FFD79A;
border:1px solid rgba(245,158,11,.5)}
.nf .scope-all{background:rgba(255,107,107,.24);color:#FFB3B3;
border:1px solid rgba(255,107,107,.5)}
.nf .scope-part{background:rgba(120,160,220,.22);color:#BBD4F5;
border:1px solid rgba(120,160,220,.45)}
.nf .kp{font-size:13px;color:var(--t2);margin-bottom:8px;font-weight:600}
.nf .src{font-size:11.5px;color:var(--t3);margin-top:6px;font-style:italic}
.nf .state{display:inline-block;background:rgba(255,107,107,.2);color:#FFB3B3;
font-size:12.5px;font-weight:700;padding:4px 12px;border-radius:6px;margin-bottom:10px}
.nf.warn .state{background:rgba(255,178,36,.18);color:#FFD79A}

/* ---- 常识卡 ---- */
.kb{background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--local);
border-radius:10px;padding:16px 20px;margin-bottom:12px}
.kb h3{font-size:16px;font-weight:600;margin-bottom:8px}
.kb .q{font-size:14px;color:#7DE0B4;margin-bottom:10px;font-weight:600}
.kb .d{font-size:13.5px;color:var(--t2);line-height:1.75;white-space:pre-line;margin-bottom:10px}
.kb .w{font-size:12.5px;color:var(--t3);border-top:1px dashed var(--line);padding-top:9px}
.kb .w::before{content:"为什么重要 ";color:var(--local);font-weight:700}
""")
        if compact:
            # 手机端精简排版：去大边距大标题，字号压一档
            A("""
body{max-width:100%;padding:12px 12px 30px;font:13px/1.6 "Microsoft YaHei",system-ui,sans-serif}
h1{font-size:19px}.sub{margin:5px 0 14px;font-size:12px}
.headline{padding:10px 12px;margin-bottom:14px;font-size:13px}
.kpis{gap:7px;margin-bottom:16px}
.kpi{padding:8px 10px}.kpi b{font-size:17px}.kpi span{font-size:10.5px}
h2{font-size:15px;margin:20px 0 9px;padding-bottom:6px}
.n{font-size:11px}
*{scrollbar-width:none}
""")
        # 飞行建议横幅样式（深浅两模式都成立）
        A("""
.verdict{display:flex;align-items:center;gap:12px;border-radius:10px;
padding:13px 16px;margin-bottom:18px;border:1px solid var(--line)}
.verdict .v-ico{font-size:26px}
.verdict .v-main{font-size:17px;font-weight:700}
.verdict .v-sub{font-size:12.5px;color:var(--t3);margin-top:2px}
.v-no{background:var(--must-bg);border-left:4px solid var(--must)}
.v-no .v-main{color:var(--must)}
.v-warn{background:var(--watch-bg);border-left:4px solid var(--watch)}
.v-warn .v-main{color:var(--watch)}
.v-ok{background:var(--local-bg);border-left:4px solid var(--local)}
.v-ok .v-main{color:var(--local)}
""")
        A("</style></head><body>")
        # 顶部飞行建议 —— 用户要求"最上面直接加上不建议/可飞/不能飞"
        A(self._fly_verdict(d))
        A(f"<h1>无人机新规晨报</h1>")
        A(f'<div class="sub">{esc(today)} · 所在地 <b style="color:var(--local)">'
          f'{esc(city)}</b> · 已自动剔除非本地规定 {st["filtered"]} 条</div>')

        # 精简版：导语与 KPI 行都砍 —— 顶部 verdict 已给结论，
        # 各分组标题自带条数，重复信息不再占屏。
        if not compact:
            A(f'<div class="headline">{esc(self.headline(today, d))}</div>')
            A('<div class="kpis">')
            n_local_nf = sum(1 for a in (d.get("no_fly") or []) if a.in_my_area)
            for label, val in (("本地禁飞", n_local_nf),
                               ("今日新增", len(d["fresh"])),
                               ("历史补课", len(d["backlog"])),
                               ("今日可推送", len(d["all"])),
                               ("重大", len(d["must"])),
                               ("累计收录", st["total"])):
                A(f'<div class="kpi"><b>{val}</b><span>{esc(label)}</span></div>')
            A("</div>")

        # ---- 临时禁飞（最高优先级，置顶）----
        # 排序：正在禁飞的 > 将来开始的 > 其余。老通告但时段还没过
        # 的自然浮上来 —— 用户："以前的通知但在某个时间段有影响……
        # 提前，同时写进晨报"。
        nf = d.get("no_fly") or []
        crit = [a for a in nf if a.level == "critical"]
        warn = [a for a in nf if a.level == "warn"]
        if crit or warn:
            A(f'<h2>🚫 临时禁飞 · 重点<span class="n">{len(crit) + len(warn)} 条</span></h2>')

            def _nf_key(a):
                if getattr(a, "active_now", False):
                    return (0, a.days_until if a.days_until is not None else 0)
                if a.days_until is not None and a.days_until >= 0:
                    return (1, a.days_until)
                return (2, 0)
            for a in sorted(crit + warn, key=_nf_key)[:8]:
                cls = "crit" if a.level == "critical" else "warn"
                if a.in_my_area:
                    tag = f'<span class="tag">在你辖区内 · {esc(a.area_text)}</span>'
                else:
                    tag = f'<span class="tag">外地 · {esc(a.area_text)}</span>'
                if a.active_now:
                    state = '<div class="state">● 正在禁飞中</div>'
                elif a.days_until is not None and a.days_until > 0:
                    state = (f'<div class="state">◷ {a.days_until} 天后开始</div>')
                elif a.days_until is not None and a.days_until < 0:
                    state = '<div class="state">已结束</div>'
                else:
                    state = ""
                A(f'<div class="nf {cls}">')
                A(tag)
                # 三件套一行说完 —— 用户要的就是「区域 ｜ 时间 ｜ 来源」，
                # 有这三样才判断得出"跟不关我的事"。
                A(f'<div class="brief3">{esc(summarize_no_fly(a))}</div>')
                A(f'<div class="where">{esc(a.area_text)}</div>')
                A(f'<div class="when">禁飞时段：{esc(a.when_text)}</div>')
                A(state)
                _sc = getattr(a, "scope", "")
                if _sc:
                    _cls = "scope-on" if _sc == "单个地点" else (
                        "scope-all" if _sc == "全域" else "scope-part")
                    A(f'<div class="scope {_cls}">范围级别：{esc(_sc)}</div>')
                if getattr(a, "area", ""):
                    A(f'<div class="area">禁飞范围：{esc(a.area)}</div>')
                A(f'<div class="ttl">{esc(a.title)}</div>')
                if getattr(a, "key_point", ""):
                    A(f'<div class="kp">{esc(a.key_point)}</div>')
                if getattr(a, "snippet", ""):
                    A(f'<div class="snip">{esc(a.snippet)}</div>')
                if getattr(a, "verify_reason", ""):
                    A(f'<div class="src">来源：{esc(a.verify_reason)}</div>')
                if a.url:
                    A(f'<a href="{esc(a.url)}">查看通告原文 →</a>')
                A("</div>")

        # ---- 天气 + 风速续航（用户要求：接免费天气，按机型算续航）----
        # Open-Meteo 免费无 key；失败时 brief_lines 返回 []，整节跳过，
        # 晨报绝不因天气接口挂掉而生成失败。
        try:
            from uavwatch.weather import brief_lines
            _wl = brief_lines(self.cfg.location.city, self.cfg.data_dir,
                              self.cfg.devices)
        except Exception:              # noqa: BLE001
            _wl = []
        if _wl and not compact:
            A('<h2>🌦️ 今日天气 · 续航估算<span class="n">'+
              esc(self.cfg.location.city) + '</span></h2>')
            A('<div class="wx">')
            for _line in _wl:
                A(f'<div class="wx-line">{esc(_line)}</div>')
            A('<div class="hint">续航为按当日风的粗略估算，非适航数据；'
              '返航请预留 30% 以上电量。</div>')
            A("</div>")

        # ---- 今日新增（紧跟临时禁飞，用户要求"提前"）----
        # 原来没有独立板块：今天的条目混进底部必读/关注/参考分层，
        # 要翻到底才知道"今天到底来了什么"。单独成节放前面，
        # 先看增量、再看存量。
        fresh = d.get("fresh") or []
        if fresh:
            A(f'<h2>🆕 今日新增<span class="n">{len(fresh)} 条</span></h2>')
            for r in fresh[:15]:
                A('<div class="card watch">')
                A(f'<h3>{esc(r["title"])}</h3>')
                A(f'<div class="meta">{esc(self._meta_line(r))}</div>')
                if not compact and r.get("ai_summary"):
                    A(f'<div class="sum">{esc(r["ai_summary"])}</div>')
                A(f'<div class="hint">{esc(self._hint(r))}</div>')
                if r.get("url"):
                    A(f'<div style="margin-top:10px"><a href="{esc(r["url"])}">'
                      f'查看原文 →</a></div>')
                A("</div>")
            if len(fresh) > 15:
                A(f'<div class="hint">其余 {len(fresh) - 15} 条见下方分层列表</div>')

        # ---- 飞行常识 ----
        cs = d.get("commonsense") or []
        notes = d.get("local_notes") or []
        if (cs or notes) and not compact:
            A(f'<h2>📘 飞行常识<span class="n">{len(cs) + len(notes)} 条</span></h2>')
            # 常识用「在 X 之后需要 Y 方可飞行」句式 —— 飞手要的是
            # 行动指引，不是"某某规定解读"。
            for it in cs:
                A('<div class="kb">')
                A(f'<h3>{esc(it["title"])}</h3>')
                A(f'<div class="q">{esc(it["sentence"])}</div>')
                if it.get("why"):
                    A(f'<div class="d">{esc(it["why"])}</div>')
                if it.get("url_text"):
                    A(f'<div class="w">详情见 {esc(it["url_text"])}'
                      f'（<a href="{esc(it["url"])}">打开</a>）</div>')
                A("</div>")

            # 禁飞区地图 —— 不自己画，指向 UOM
            _mp = d.get("map") or {}
            if _mp:
                A('<div class="kb">')
                A(f'<h3>{esc(_mp["title"])}</h3>')
                A(f'<div class="d">{esc(_mp["text"])}</div>')
                A(f'<div class="w"><a href="{esc(_mp["url"])}">'
                  f'{esc(_mp["url_text"])}</a></div>')
                A("</div>")

            for it in notes:
                A('<div class="kb">')
                A(f'<h3>{esc(it["title"])}</h3>')
                if it.get("short"):
                    A(f'<div class="q">{esc(it["short"])}</div>')
                if it.get("detail"):
                    A(f'<div class="d">{esc(it["detail"].strip())}</div>')
                if it.get("why"):
                    A(f'<div class="w">{esc(it["why"])}</div>')
                A("</div>")

        if d["soon"] and not compact:
            A(f'<h2>时效提醒<span class="n">{len(d["soon"])} 条</span></h2>')
            for r in d["soon"][:10]:
                A('<div class="soon">')
                A(f'<div class="when">{esc(self._when_text(r))} · '
                  f'{esc(r["effective_at"])}</div>')
                A(f'<div>{esc(r["title"])}</div>')
                A(f'<div class="hint">{esc(self._hint(r))}</div>')
                A("</div>")

        for tier, rows, cls in ((TIER_MUST, d["must"], "must"),
                                (TIER_WATCH, d["watch"], "watch"),
                                (TIER_REF, d["ref"], "ref")):
            if not rows:
                continue
            A(f'<h2>{esc(tier)}<span class="n">{len(rows)} 条</span></h2>')
            for r in rows:
                A(f'<div class="card {cls}">')
                A(f'<h3>{esc(r["title"])}</h3>')
                A(f'<div class="meta">{esc(self._meta_line(r))}</div>')
                if not compact and r.get("ai_summary"):
                    A(f'<div class="sum">{esc(r["ai_summary"])}</div>')
                A(f'<div class="hint">{esc(self._hint(r))}</div>')
                if r.get("url"):
                    A(f'<div style="margin-top:10px"><a href="{esc(r["url"])}">'
                      f'查看原文 →</a></div>')
                A("</div>")

        A("<footer>")
        A("按分类: " + esc(" · ".join(
            f"{k} {len(v)}" for k, v in
            sorted(d["by_cat"].items(), key=lambda kv: -len(kv[1])))))
        A("<br>本晨报由无人机新规雷达自动生成，内容来自民航局官网及公开检索结果，"
          "<br>仅供参考，请以官方原文为准。")
        A(f"<br>生成时间 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        A("</footer></body></html>")
        return "".join(P)
