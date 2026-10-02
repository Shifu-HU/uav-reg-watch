"""Android 兼容层：theme.py 只需要 QColor 的颜色解析与通道读写。"""
import re

_HEX = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


class QColor:
    def __init__(self, *a):
        if a and isinstance(a[0], QColor):
            self._set(a[0].red(), a[0].green(), a[0].blue(), a[0].alpha())
        elif a and isinstance(a[0], str):
            self._parse(a[0])
        elif a and isinstance(a[0], (tuple, list)) and len(a) == 1:
            self._parse(a[0][0])
        elif len(a) >= 3:
            r, g, b = a[0], a[1], a[2]
            al = a[3] if len(a) > 3 else 255
            self._set(int(r), int(g), int(b), int(al))
        else:
            self._set(0, 0, 0, 255)

    def _set(self, r, g, b, a=255):
        self._r, self._g, self._b, self._a = r, g, b, a

    def _parse(self, s):
        s = s.strip()
        m = _HEX.match(s)
        if not m:
            self._set(0, 0, 0, 255); return
        h = s[1:]
        if len(h) == 3: h = "".join(ch * 2 for ch in h)
        if len(h) == 6: h += "ff"
        self._set(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16))

    def red(self): return self._r
    def green(self): return self._g
    def blue(self): return self._b
    def alpha(self): return self._a
    def setAlpha(self, a): self._a = int(a)
    def name(self): return "#{:02x}{:02x}{:02x}".format(self._r, self._g, self._b)
    def lighter(self, f=150):
        f = f / 100.0
        return QColor(min(255, int(self._r * f)), min(255, int(self._g * f)),
                      min(255, int(self._b * f)), self._a)
    def darker(self, f=200):
        f = f / 100.0
        return QColor(int(self._r * f), int(self._g * f), int(self._b * f), self._a)
    def isValid(self): return True
    def __eq__(self, o): return isinstance(o, QColor) and (self._r, self._g, self._b, self._a) == (o._r, o._g, o._b, o._a)
    def __repr__(self): return "QColor({},{},{},{})".format(self._r, self._g, self._b, self._a)


class _Enum(int):
    pass


class Qt:
    transparent = _Enum(0)
    SolidLine = _Enum(1)
    DashLine = _Enum(2)
    NoPen = _Enum(0)
    black = _Enum(2)
    white = _Enum(3)
    FlatCap = _Enum(0x10)
    RoundCap = _Enum(0x20)
    SquareCap = _Enum(0x30)
    BevelJoin = _Enum(0x40)
    RoundJoin = _Enum(0x80)
    AlignCenter = _Enum(0x0084)
    AlignHCenter = _Enum(0x0004)
    AlignVCenter = _Enum(0x0080)


class Signal:
    def __init__(self, *a): pass
    def __get__(self, obj, cls): return lambda *a, **k: None


class QRectF:
    def __init__(self, *a): self.x, self.y, self.w, self.h = (list(a) + [0, 0, 0, 0])[:4]


class QPainter:
    def __init__(self, *a): pass
    def __getattr__(self, n): return lambda *a, **k: None


class QPainterPath:
    def __init__(self, *a): pass
    def __getattr__(self, n): return lambda *a, **k: None


class QPen:
    def __init__(self, *a): pass


class QAbstractButton:
    pass


class QPushButton:
    pass
