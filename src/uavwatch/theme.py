"""主题系统 —— 配色方案 + 深浅模式 + 三种界面风格。

配色参考本机「菜鸟包裹监控」项目：8 套方案，每套三色（主/次/三级色），
选择器做成"圆内三等分色饼"，一眼看清整套配色。

风格（style）则是三套不同的界面实现：
    classic —— Codex 第一版（`ui/`）
    compact —— Codex 第二版（`ui/`）
    mine    —— 本机实现（`ui_mine/`）
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QPushButton

# ---------------------------------------------------------------------------
# 配色方案表：8 套 × 三色（主 / 次 / 三级）
# 取自「菜鸟包裹监控」的既有方案，保持两套软件观感一致
# ---------------------------------------------------------------------------
COLOR_SCHEMES: dict[str, tuple[str, str, str]] = {
    "浅蓝灰·紫粉": ("#B5C5D7", "#D4B5D7", "#E8E7E9"),
    "暖米沙·豆沙绿": ("#EBD7C6", "#D2D7B8", "#EEC8C4"),
    "天蓝·柔粉": ("#8DC5F2", "#EAB2E6", "#F0F5CA"),
    "抹茶绿·米白": ("#DFE691", "#F3F1DA", "#FFFFFF"),
    "冷蓝灰·浅灰": ("#B4C1D4", "#CFD3DD", "#CFD3DD"),
    "薄荷绿·黄绿": ("#ABEBC7", "#CAEBC7", "#FFFFFF"),
    "奶油米·肉粉": ("#F5EAD7", "#F5D7D8", "#F5D7D8"),
    "淡紫丁香·浅粉": ("#D7CDF5", "#F5CDF0", "#F5CDF0"),
}
SCHEME_ORDER = list(COLOR_SCHEMES.keys())
DEFAULT_SCHEME = SCHEME_ORDER[0]


def scheme_colors(name: str) -> tuple[str, str, str]:
    return COLOR_SCHEMES.get(name) or COLOR_SCHEMES[DEFAULT_SCHEME]


# ---------------------------------------------------------------------------
# 界面风格
# ---------------------------------------------------------------------------
STYLES: dict[str, tuple[str, str]] = {
    "classic": ("经典版", "Codex 第一版 —— 侧边栏 + 卡片流"),
    "compact": ("紧凑版", "Codex 第二版 —— 信息密度更高"),
    "mine": ("本机版", "本机实现 —— 令牌化 + 完整状态机"),
}
STYLE_ORDER = list(STYLES.keys())
DEFAULT_STYLE = "mine"


def _lum(c: str) -> float:
    """感知亮度，用于决定压在强调色上的文字该用黑还是白。"""
    q = QColor(c)
    return 0.299 * q.red() + 0.587 * q.green() + 0.114 * q.blue()


def _blend(c1: QColor, c2: QColor, t: float) -> QColor:
    return QColor(int(c1.red() + (c2.red() - c1.red()) * t),
                  int(c1.green() + (c2.green() - c1.green()) * t),
                  int(c1.blue() + (c2.blue() - c1.blue()) * t))


def build_tokens(mode: str, scheme: str) -> dict[str, str]:
    """由「深浅模式 + 配色方案」派生一整套界面令牌。

    深色用深海军蓝打底再按方案色染色，浅色用浅灰蓝打底，
    这样同一套方案在两种模式下都是同一个"性格"。
    """
    a, b2, b3 = (QColor(c) for c in scheme_colors(scheme))
    on_accent = "#10151F" if _lum(scheme_colors(scheme)[0]) > 150 else "#FFFFFF"

    def rgba(c: QColor, alpha: float) -> str:
        return f"rgba({c.red()},{c.green()},{c.blue()},{alpha:g})"

    if mode == "light":
        base_top, base_bot = QColor("#F4F6FB"), QColor("#E7EBF4")
        bg_top = _blend(base_top, a, 0.26).name()
        bg_bot = _blend(base_bot, b3, 0.22).name()
        card = _blend(QColor("#FFFFFF"), b2, 0.18)
        return {
            "canvas": bg_top, "canvas_2": bg_bot,
            "surface": card.name(), "raised": _blend(card, a, 0.10).name(),
            "overlay": _blend(QColor("#FFFFFF"), a, 0.24).name(),
            "highlight": rgba(a, 0.18),
            "line": rgba(QColor("#1A1B20"), 0.14),
            "line_subtle": rgba(QColor("#1A1B20"), 0.09),
            "line_strong": rgba(QColor("#1A1B20"), 0.26),
            # text_3 原 #6E7480 只有 3.8:1，达不到 WCAG AA 的 4.5，
            # 加深到 #5A6069（5.3:1）—— 它承载"来源/时间/地区"等元信息。
            "text": "#1A1B20", "text_2": "#565B66", "text_3": "#5A6069",
            "text_disabled": "#9AA0AB",
            "accent": a.darker(115).name(), "on_accent": on_accent,
            "accent_2": b2.darker(115).name(), "tertiary": b3.name(),
            "focus": a.darker(105).name(),
            "must": "#B42318", "must_bg": "rgba(180,35,24,0.13)",
            "watch": "#8A5300", "watch_bg": "rgba(251,191,36,0.20)",
            "local": "#007A54", "local_bg": "rgba(0,122,84,0.13)",
            "normal": "#565B66", "normal_bg": "rgba(86,91,102,0.12)",
            "info_bg": rgba(a, 0.14),
        }
    # 深色（默认）
    base_top, base_bot = QColor("#0A1122"), QColor("#0E1A33")
    bg_top = _blend(base_top, a, 0.18).name()
    bg_bot = _blend(base_bot, b3, 0.14).name()
    card = _blend(QColor("#141A2A"), b2, 0.16)
    return {
        "canvas": bg_top, "canvas_2": bg_bot,
        "surface": card.name(), "raised": _blend(card, a, 0.10).name(),
        "overlay": _blend(card, a, 0.22).name(),
        "highlight": rgba(a, 0.16),
        "line": rgba(QColor("#FFFFFF"), 0.14),
        "line_subtle": rgba(QColor("#FFFFFF"), 0.08),
        "line_strong": rgba(QColor("#FFFFFF"), 0.26),
        # text_3 原 #8A97B5 只有 4.5:1 卡在门槛上，提亮到 #97A4C0（5.4:1）
        "text": "#E9F1FF", "text_2": "#B6C2D9", "text_3": "#97A4C0",
        "text_disabled": "#5C6880",
        "accent": a.name(), "on_accent": on_accent,
        "accent_2": b2.name(), "tertiary": b3.name(),
        "focus": a.lighter(130).name(),
        "must": "#FFA8A8", "must_bg": "rgba(248,113,113,0.16)",
        "watch": "#FFD58A", "watch_bg": "rgba(251,191,36,0.16)",
        "local": "#8AF0C8", "local_bg": "rgba(52,211,153,0.16)",
        "normal": "#B6C2D9", "normal_bg": "rgba(182,194,217,0.12)",
        "info_bg": rgba(a, 0.14),
    }


# ---------------------------------------------------------------------------
class SchemeSwatch(QAbstractButton):
    """配色方案块：圆内三等分色饼 —— 一眼看清整套配色。

    与「菜鸟包裹监控」的选择器同款，保持两套软件操作一致。

    基类选 QAbstractButton 而不是 QPushButton —— 这是第三次为
    "色饼变成扁块"返工后的结论：
      1. 最初 QPushButton：全局 QSS 的 min-height/padding 把 64×64
         撑成 90×64 扁块；
      2. 加 #swatch 豁免规则：QSS 一应用，构造期 setFixedSize 被
         清掉，show 之后塌成 minimumSizeHint 34×17（WorkBuddy 实测
         49×23 @1.5x DPR 正是这个）；
      3. QAbstractButton 没有默认 QSS 规则可被全局按钮样式命中，
         setFixedSize 才真正说了算。checked/clicked 语义照旧。
    """

    def __init__(self, colors: tuple[str, str, str], size: int = 52, parent=None):
        super().__init__(parent)
        self.colors = colors
        self._accent = "#E9F1FF"
        self.setFixedSize(size, size)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)

    def set_accent(self, color: str) -> None:
        self._accent = color
        self.update()

    def paintEvent(self, event) -> None:      # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        path = QPainterPath()
        path.addRoundedRect(1, 1, w - 2, h - 2, 12, 12)
        p.fillPath(path, QColor(255, 255, 255, 22))

        c1, c2, c3 = self.colors
        box = QRectF(6, 6, w - 12, h - 12)
        # 每个扇区都带一圈比自己略深的描边。
        #
        # 「抹茶绿·米白」「奶油米·肉粉」里的浅色扇区在浅色主题下
        # 几乎和背景同色，圆的边缘看不出来。描边让轮廓始终可辨，
        # 同时扇区之间的分界也清楚了。
        for color, start in ((c1, 90), (c2, 210), (c3, 330)):
            qc = QColor(color)
            edge = qc.darker(118)
            p.setPen(QPen(edge, 1))
            p.setBrush(qc)
            p.drawPie(box, start * 16, 120 * 16)
        # 外圈再来一道，把整个圆从背景里提出来
        p.setPen(QPen(QColor(0, 0, 0, 38), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(box)

        if self.isChecked():
            p.setPen(QPen(QColor(self._accent), 2.5))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(2, 2, w - 4, h - 4, 12, 12)
        p.end()


class ThemeSwatch(SchemeSwatch):
    """主题（深浅）按钮：同样做成圆形，用该模式的底色示意。"""

    def __init__(self, mode: str, tokens: dict[str, str], size: int = 52, parent=None):
        super().__init__((tokens["canvas"], tokens["surface"],
                          tokens["accent"]), size, parent)
        self.mode = mode
        self.setToolTip("深色主题" if mode == "dark" else "浅色主题")
