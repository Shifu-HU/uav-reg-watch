"""推送: 每日简报生成 + 桌面通知。"""

from __future__ import annotations

import logging
import subprocess
import sys
from datetime import date
from pathlib import Path

log = logging.getLogger("uavwatch.notify")


class Notifier:
    def __init__(self, cfg, store, data_dir: Path):
        self.cfg = cfg
        self.store = store
        self.data_dir = data_dir
        self.brief_dir = data_dir / "briefs"
        self.brief_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def desktop(self, title: str, message: str) -> bool:
        """Windows 气泡通知。失败不影响主流程。"""
        if not self.cfg.notify.desktop:
            return False
        try:
            if sys.platform != "win32":
                return False
            q = chr(39)
            ps = "".join([
                "[reflection.assembly]::loadwithpartialname(", q, "System.Windows.Forms", q, ");",
                "[reflection.assembly]::loadwithpartialname(", q, "System.Drawing", q, ");",
                "$n=New-Object System.Windows.Forms.NotifyIcon;",
                "$n.Icon=[System.Drawing.SystemIcons]::Information;",
                "$n.Visible=$true;",
                "$n.ShowBalloonTip(8000,", q, title, q, ",", q, message, q, ",",
                "[System.Windows.Forms.ToolTipIcon]::Info);",
                "Start-Sleep -Seconds 6;",
                "$n.Dispose();",
            ])
            subprocess.Popen(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return True
        except Exception as e:
            log.debug("桌面通知失败: %s", e)
            return False

    # ------------------------------------------------------------------
    def build_brief(self, day: str = ""):
        """生成当日简报(md + html)，返回 (md路径, html路径, 统计)。"""
        day = day or date.today().isoformat()
        rows = self.store.query(since=day, only_local=True, limit=1000)
        stats = self.store.stats()
        by_cat = {}
        for r in rows:
            by_cat.setdefault(r["category"] or "其他", []).append(r)

        md_path = self.brief_dir / ("brief-" + day + ".md")
        html_path = self.brief_dir / ("brief-" + day + ".html")
        md_path.write_text(self._render_md(day, rows, by_cat, stats), encoding="utf-8")
        html_path.write_text(self._render_html(day, rows, by_cat, stats), encoding="utf-8")
        log.info("简报已生成: %s", html_path)
        return md_path, html_path, stats

    # ------------------------------------------------------------------
    def _render_md(self, day, rows, by_cat, stats) -> str:
        L = []
        A = L.append
        A("# 无人机新规雷达 · 每日简报")
        A("")
        A("**日期**: " + day + "  ")
        A("**今日新增**: " + str(len(rows)) + " 条  ")
        A("**累计收录**: " + str(stats["total"]) + " 条 (本地相关 " + str(stats["local"]) + " 条)  ")
        A("**已过滤外地规定**: " + str(stats["filtered"]) + " 条")
        A("")
        A("---")
        for cat, items in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
            A("")
            A("## " + cat + " (" + str(len(items)) + ")")
            for it in items:
                imp = "!!!" if it["importance"] == 3 else ("!" if it["importance"] == 2 else "-")
                A("")
                A("### [" + imp + "] " + it["title"])
                meta = ["来源: " + (it["source_name"] or it["source"])]
                if it["published_at"]:
                    meta.append("发布: " + it["published_at"])
                if it["effective_at"]:
                    meta.append("生效: " + it["effective_at"])
                meta.append("地区: " + it["region"])
                A(" · ".join(meta))
                if it["ai_summary"]:
                    A("")
                    A("> " + it["ai_summary"])
                A("")
                A("[查看原文](" + it["url"] + ")")
        return "\n".join(L)

    # ------------------------------------------------------------------
    def _render_html(self, day, rows, by_cat, stats) -> str:
        def esc(s):
            return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                    .replace(">", "&gt;").replace(chr(34), "&quot;"))

        p = []
        A = p.append
        A('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">')
        A("<title>无人机新规雷达 · " + esc(day) + "</title>")
        A("<style>")
        A("body{background:#171719;color:#e8e8ea;font-family:'Microsoft YaHei','PingFang SC',sans-serif;margin:0;padding:32px;line-height:1.7}")
        A(".wrap{max-width:920px;margin:0 auto}")
        A("h1{font-size:26px;margin:0 0 6px}")
        A(".sub{color:#8b8b96;font-size:13px;margin-bottom:22px}")
        A(".stat{display:flex;gap:16px;margin:20px 0 34px;flex-wrap:wrap}")
        A(".stat div{background:#1f1f23;border:1px solid #2c2c33;border-radius:10px;padding:14px 22px;min-width:110px}")
        A(".stat b{display:block;font-size:23px;color:#4ea1ff;font-weight:600}")
        A(".stat span{font-size:12px;color:#9a9aa5}")
        A("h2{font-size:18px;margin:38px 0 14px;padding-bottom:8px;border-bottom:1px solid #2c2c33}")
        A(".item{background:#1c1c20;border:1px solid #2a2a31;border-radius:12px;padding:18px 20px;margin-bottom:13px}")
        A(".item h3{margin:0 0 8px;font-size:15.5px;font-weight:600}")
        A(".meta{font-size:12px;color:#8b8b96;margin-bottom:9px}")
        A(".sum{color:#c9c9d1;font-size:14px}")
        A("a{color:#4ea1ff;text-decoration:none;font-size:13px}")
        A(".imp3{border-left:3px solid #ff5f56}")
        A(".imp2{border-left:3px solid #ffbd2e}")
        A(".imp1{border-left:3px solid #3a3a44}")
        A("</style></head><body><div class=\"wrap\">")
        A("<h1>无人机新规雷达 · 每日简报</h1>")
        A("<div class=\"sub\">" + esc(day) + " · 仅显示与所在地相关的规定</div>")
        A("<div class=\"stat\">")
        A("<div><b>" + str(len(rows)) + "</b><span>今日新增</span></div>")
        A("<div><b>" + str(stats["total"]) + "</b><span>累计收录</span></div>")
        A("<div><b>" + str(stats["filtered"]) + "</b><span>已过滤外地</span></div>")
        A("</div>")
        for cat, items in sorted(by_cat.items(), key=lambda kv: -len(kv[1])):
            A("<h2>" + esc(cat) + " (" + str(len(items)) + ")</h2>")
            for it in items:
                A("<div class=\"item imp" + str(it["importance"]) + "\">")
                A("<h3>" + esc(it["title"]) + "</h3>")
                meta = [esc(it["source_name"] or it["source"])]
                if it["published_at"]:
                    meta.append("发布 " + esc(it["published_at"]))
                if it["effective_at"]:
                    meta.append("生效 " + esc(it["effective_at"]))
                meta.append(esc(it["region"]))
                A("<div class=\"meta\">" + " · ".join(meta) + "</div>")
                if it["ai_summary"]:
                    A("<div class=\"sum\">" + esc(it["ai_summary"]) + "</div>")
                A("<div style=\"margin-top:10px\"><a href=\"" + esc(it["url"]) + "\">查看原文 &rarr;</a></div>")
                A("</div>")
        A("</div></body></html>")
        return "".join(p)
