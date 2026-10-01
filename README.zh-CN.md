# 无人机新规雷达 · UAV Reg Watch

> 无人机法规监控工具（Windows 桌面版 + Android 手机版）。以民航局官网为主源、SearXNG / Bing 为辅，
> 用**本地小模型**逐条分析摘要，按设备所在地自动剔除外地规定，每日推送不少于 200 条。

**[⬇️ 下载安装包（Releases）](https://github.com/Shifu-HU/uav-reg-watch/releases/latest)** ｜ [架构图](docs/architecture.zh-CN.html) ｜ English: [README.md](README.md)

---

## ⚠️ 先看这条：概括是索引，晨报和原文才是结论

应用里的一行概括由本地小模型生成，**只用于快速定位，可能与原文有出入，不能当结论用**。

1. 点开侧栏 **晨报**，逐条看「区域｜时段｜来源」和「怎么办」；
2. 每条正文末尾点 **查看原文 →**，会跳系统浏览器打开官方公告，**以原文为准**；
3. 涉及禁飞、处罚、实名登记的判断，**务必以原文和当地公告为准**，不要依据概括做飞行决定。

---

## 一、安装

两个版本，界面和逻辑完全一致，任选其一或都装。

| | 桌面版 | 手机版 |
|---|---|---|
| 界面 | PySide6 原生窗口 | WebView 壳 App（Android） |
| 安装方式 | `UAVRegWatch-Setup.exe` 直装 | `UAVMobile.apk` 直装 |
| AI 分析 | 本机 Ollama | **复用宿主机的 Ollama** |
| 配色 | 同一套主题令牌（8 套配色 × 深浅模式），两端零漂移 | 同左 |

### 1.1 桌面版

1. 从 [Releases](https://github.com/Shifu-HU/uav-reg-watch/releases/latest) 下载 `UAVRegWatch-Setup.exe`，双击安装（默认装到 `%LOCALAPPDATA%\UAVRegWatchMine`，无需管理员权限）；
2. 从开始菜单 / 桌面快捷方式启动即可。安装包自带一份抓取好的基线数据库，**装完打开就有内容**，不必等首次全量抓取。

> 想从零开始重建基线库：删掉安装目录下的 `data` 文件夹，程序会自动重新全量抓取（较慢）。

### 1.2 手机版（Android / MuMu 等模拟器）

手机（模拟器）装不了 Ollama，所以 AI 由宿主机算，手机只是 WebView 壳。三步：

```bash
# 1) 宿主机启动后端（监听 8765）
python mobile/server_mobile.py

# 2) 把端口反向映射到设备（MuMu 多开时第二实例通常是 16416）
adb -s 127.0.0.1:16416 reverse tcp:8765 tcp:8765

# 3) 安装并打开壳 App
adb -s 127.0.0.1:16416 install -r UAVMobile.apk
```

> 不同设备的 adb 序列号不一样：MuMu 多开第一实例一般是 `16384`、第二实例 `16416`；真机用 `adb devices` 查到的序列号代替。
> 也可以直接双击 `mobile\start_mobile.bat`，自动完成第 2、1 步。

---

## 二、首次配置

安装后只需确认一件事：**本机已装 [Ollama](https://ollama.com) 并拉好模型**。

```bash
ollama pull qwen3:4b
```

程序默认使用模型 `uav-reg`，未找到时自动回退到 `qwen3:4b` → `qwen3:8b`（见 `config.yaml` 的 `llm` 段）。可以在设置页随时换。

其余配置都有默认值，第一次跑之前不用动：

| 配置 | 默认 | 说明 |
|---|---|---|
| 地区 | 深圳 / 广东 | 地理过滤的依据，**建议先改成你的省市** |
| 每日定时 | 07:30 | 到点自动跑一轮增量搜索 |
| 信息源 | 民航局官网 + Bing | SearXNG 默认关，需要自建服务再开 |
| 每日推送量 | ≥ 200 条 | 当日新增 + 历史未读池补足 |

---

## 三、日常使用

- **今日情报**：顶部「今日补充 / 总计收录 / 本地相关」汇总条，下面分三块（重大 / 近期需注意 / 更早），块内按 重大→重要→普通 排序；
- **晨报**：置顶飞行建议横幅（不能飞 / 谨慎 / 可以飞），下面按「临时禁飞 / 今日新增 / 关注 / 参考」分区；
- **收藏**：星标 + 过滤，桌面端和手机端一致；
- **已过滤**：被地理过滤剔除的条目也留档可查；
- 首次打开晨报约 40 秒（含天气联网查询），程序启动时会后台预热，之后秒开。

---

## 四、设置

设置页为浮岛式分组，**改哪存哪，无需点保存**：

- **地区**：省 / 市下拉，决定地理过滤范围（保留全国性 + 本地 + 同省规定）；
- **内容过滤**：是否显示行业动态等；
- **搜索与模型**：Ollama 地址、模型名；
- **外观**：8 套配色 × 深 / 浅模式，即时生效；
- **我的设备**：机型与重量分级（微型 / 轻型 / 小型…），影响晨报建议。

---

## 五、常见问题

| 问题 | 原因与处理 |
|---|---|
| 点晨报要等很久？ | 首次生成约 40 秒（含天气联网）。启动时会后台预热，之后秒开；生成期间页面不卡死，显示「已等待 N 秒」，可先看其它页面 |
| 概括和原文不一致？ | 会。概括只是索引。点进晨报看正文，再点「查看原文」看原始公告；能不能飞一律以原文为准 |
| 手机版提示连不上？ | 检查三件事：① 宿主机 `server_mobile.py` 在跑；② `adb reverse tcp:8765 tcp:8765` 已执行；③ adb 序列号 / 模拟器端口对不对（MuMu 第二实例是 16416） |
| 手机上要装 Ollama 吗？ | 不用，也装不了。AI 全部在宿主机完成 |
| 想清空数据重新开始？ | 删掉 `data` 目录，下次启动自动重建基线库 |
| 民航局官网抓不到内容？ | 该站检索必须 POST（GET 会被 302 到反爬页），程序已内置处理；若仍为空，多为其站点临时维护 |
| 全国的法规被当成"外地"过滤掉了？ | 不会。程序会剥离民航局页脚机构列表再判断，并保留所有带全国性标志词的文件 |

---

## 六、从源码运行

不想装打包版，或者想跑在别的系统上：

```bash
git clone https://github.com/Shifu-HU/uav-reg-watch.git
cd uav-reg-watch
pip install -r requirements.txt

python -m uavwatch.cli doctor        # 检查环境
python -m uavwatch.cli bootstrap     # 首次全量基线（较慢，一次性）
python -m uavwatch.tui               # 桌面版 GUI
python -m uavwatch.cli daemon        # 常驻定时抓取（按 config 的 schedule）
python -m uavwatch.cli brief         # 手动生成今日简报
python mobile/server_mobile.py       # 手机版后端（8765 端口）
```

项目结构：

```
uav-reg-watch/
├── src/uavwatch/          两端共用的业务核心
│   ├── config.py          配置加载/保存
│   ├── storage.py         SQLite 存储（WAL）
│   ├── geo.py             地理位置过滤 ★核心
│   ├── fetcher.py         HTTP 抓取（限速/编码回退）
│   ├── llm.py             本地模型分析 + 规则兜底
│   ├── pipeline.py        抓取→去重→分析→过滤→入库
│   ├── morning.py         晨报生成（Markdown / HTML / 纯文本）
│   ├── theme.py           主题令牌 ★两端唯一样式来源
│   └── sources/           caac / searxng / bing / local
├── mobile/                手机版（后端 + 界面 + 一键启动脚本）
└── config.yaml            全部配置
```

---

## 七、免责声明

本项目只是**信息聚合工具**，不构成任何飞行合规建议。法规以官方发布为准，飞行前请自行核实空域、实名登记与当地管理规定。

## License

[MIT](LICENSE) © 2026 Shifu-HU ｜ English documentation: [README.md](README.md)
