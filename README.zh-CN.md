# 无人机新规雷达 · UAV Reg Watch

每天盯民航局官网的无人机新规，按你所在的地区和你登记的机型重量过滤，本地小模型做摘要分类。电脑端（Windows）+ 手机端（Android），数据全部留在本机。

**简体中文** · [English](README.md)

[架构图](docs/architecture.zh-CN.html) ｜ [下载安装](https://github.com/Shifu-HU/uav-reg-watch/releases/latest)

## 它做什么

| 能力 | 说明 |
|---|---|
| 首次启动 | 全量抓取历史规定，建立本地基线库 |
| 每日定时 | 默认 07:30 搜增量；没开机则当天首次打开时补搜 |
| 信息源 | 民航局官网（主）+ Bing + SearXNG（可选）+ 地方机构 |
| 智能分析 | 本地模型生成摘要、分类、重要度、生效日期；没装模型时降级为规则法 |
| 三级分类 | 重大 / 重要 / 普通，列表用色条区分 |
| 地区过滤 | 只留「全国性 + 本地 + 同省」，剔掉的进「已过滤」页可查 |
| 设备过滤 | 登记机型（品牌 → 系列 → 机型，重量自动带出），只推适用于该重量的条款 |
| 天气与续航 | 抓当地天气，按风估算实际可用续航，含阵风告警 |
| 晨报 | 每日 HTML 晨报，内置浏览器直接看 |
| 外观 | 深浅两模式 × 8 套配色，等级色随主题派生 |

## 下载安装

到 [Releases](https://github.com/Shifu-HU/uav-reg-watch/releases/latest) 页面下载最新版（v2.5）。

**电脑端**：下载桌面版压缩包，解压后双击 `UAVRegWatchMine.exe`。免安装，`config.yaml` 和 `data/` 都在 exe 旁边，删掉目录即卸载。需要 Windows 10/11 x64。

**手机端**：下载 APK（约 489 MB）拷到手机安装，系统提示「未知来源应用」时允许即可。要求 Android 8.0+、arm64、剩余存储 1 GB 以上。

> APK 里带约 470 MB 的本地模型。第一次启动要把它解压到应用目录，屏幕可能停住几十秒到一两分钟，属正常，之后再开就快了。

> 应用里的一行概括由本地小模型生成，**只用于快速定位，可能与原文有出入**。点开每条的「查看原文」以官方公告为准；涉及禁飞、处罚、实名登记的判断，务必以原文和当地公告为准。

## 首次配置

1. **地区**：设置里填城市和省份。改完自动保存并立刻按新地区全量重搜一遍，不用手动保存；连改多次也只跑一轮（带防抖）。
2. **我的设备**：登记机型，重量自动带出，只推适配这个重量的条款。同一机型换电池会跨档的（如 Mini 5 Pro 249~310 g），按最大值定级、界面显示区间。
3. **本地模型**（可选）：装 [Ollama](https://ollama.com) 并拉取 `qwen3.5:0.8b`。完全不装也能用，只是摘要和分类退化为规则法。

## 日常使用

左栏五个页签：**情报流**（每日新规）、**晨报**（当日 HTML 简报）、**已过滤**（被地区/设备规则剔掉的条款，可查）、**收藏**、**设置**。

设置里的配色方案、深浅模式即时生效，晨报配色跟主题走。手机端和电脑端是两套独立数据，各自抓各自的，互不同步。

## 配置（config.yaml）

电脑端的配置文件在 exe 旁边，界面里能改的（地区、设备、外观）都会自动写回：

```yaml
location:
  city: 深圳
  province: 广东
  keep_national: true      # 保留全国性规定

devices:
  - brand: 大疆 DJI
    model: Mini 5 Pro
    grams: 310.0           # 区间最大值，分级按最坏情况
    level: 轻型

schedule:
  daily_time: "07:30"      # 每日抓取时间
  min_daily_items: 0       # 0 = 不设配额下限

llm:
  provider: ollama
  model: "qwen3.5:0.8b"

appearance:
  mode: dark               # dark / light
```

## 几个实现细节

- **民航局抓取**：站内检索必须用 POST，GET 会被 302 到反爬壳页；页面底部的机构列表页脚必须剥离，否则全国性法规会被误判成广州地方文件。
- **地区判断顺序**：本地城市/省 → 同省其他城市 → 管理局辖区（如深圳归中南局）→ 全国性标志词 → 其余剔除。省级政策全省适用，所以同省保留。
- **重量档位**：按民航局口径四档——微型 <0.25 kg、轻型 0.25~4 kg、小型 4~15 kg、中型 15~116 kg，边界算闭区间（250 克整算轻型）。
- **一套配色管两端**：`theme.build_tokens()` 是唯一真相，桌面端读它生成 QSS，手机端经 `/api/theme.css` 转成 CSS 变量，两端颜色永远一致。

## 从源码运行

仓库包含核心引擎 `src/uavwatch` 和 Android 端工程 `src/android`。

```bash
pip install -r requirements.txt

python -m uavwatch.cli bootstrap    # 首次全量基线
python -m uavwatch.cli run          # 跑一轮增量
python -m uavwatch.cli status       # 状态汇总
python -m uavwatch.cli brief        # 生成今日简报
python -m uavwatch.tui              # 终端实时进度
```

Android 端：`src/android/` 内含 Java 壳、FastAPI 服务端（`phone_server.py`）和 PySide6 桩实现（Android 上没有真实 Qt，只保留颜色解析）。APK 构建用 Chaquopy 内嵌 CPython，模型经 llama.cpp（`libllamaserver.so`）本机推理。

## 常见问题

| 问题 | 说明 |
|---|---|
| 第一次打开很慢 / 像卡住了 | 电脑端在建基线库，手机端在解压模型，等它跑完就好 |
| 错过了 07:30 | 当天首次打开时自动补搜 |
| 没装 Ollama | 正常可用，摘要/分类退化为规则法 |
| 手机和电脑数据同步吗 | 不同步，两端各自独立抓取 |
| 某条本地新规没出现 | 先查「已过滤」页；再核对地区填写和设备登记的重量档位 |

## 许可

[MIT](LICENSE)
