# 无人机新规雷达 · UAV Reg Watch

> 无人机法规监控，**双端独立运行**：手机版一个 APK 装完即用（AI 在手机本机跑），
> 桌面版 PySide6 原生窗口。以民航局官网为主源、SearXNG / Bing 为辅，
> 按所在城市自动剔除外地规定，晨报每日送达。

**[⬇️ 下载安装包（Releases）](https://github.com/Shifu-HU/uav-reg-watch/releases/latest)** ｜ [架构图](docs/architecture.zh-CN.html) ｜ English: [README.md](README.md)

---

## ⚠️ 先说最重要的一件事：概括是索引，晨报和原文才是结论

应用里的一行概括由本地小模型生成，**只用于快速定位，可能与原文有出入，不能当结论用**。

1. 点开侧栏 **晨报**，逐条看「区域｜时段｜来源」和「怎么办」；
2. 每条正文末尾点 **查看原文 →**，会跳浏览器打开官方公告，**以原文为准**；
3. 涉及禁飞、处罚、实名登记的判断，**务必以原文和当地公告为准**，不要依据概括做飞行决定。

---

## 一、两个版本，各自独立

| | 手机版（推荐） | 桌面版 |
|---|---|---|
| 安装 | 一个 APK 直装，**不需要电脑** | `UAVRegWatch-Setup.exe` |
| 运行 | APK 内自带 Python 运行时与全部业务代码 | PySide6 原生窗口 |
| AI | **手机本机跑 llama.cpp + Qwen2.5-0.5B 量化模型** | 本机 Ollama（可用更大模型，摘要质量更好） |
| 网络 | 只有抓新法规时联网；阅读、过滤、AI 摘要全部离线 | 同左 |
| 隐私 | 数据全在本机，不上传任何服务器 | 同左 |
| 配色 | 同一套主题令牌（8 套配色 × 深浅模式），两端零漂移 | 同左 |

> 手机版安装包约 500 MB，因为**运行时和模型都打包在里面** ——
> 换来的是装完就能离线用，不用再下载任何东西。

---

## 二、安装

### 2.1 手机版（Android，推荐）

1. 从 [Releases](https://github.com/Shifu-HU/uav-reg-watch/releases/latest) 下载 `UAVMobile.apk`，直接安装；
2. 首次启动会把模型解包到本机（约 470 MB，**一次性**，等一分钟左右，之后启动很快）；
3. 在设置里选省份 / 城市，其余全自动 —— 自带法规基线库，装完打开就有内容。

### 2.2 桌面版（Windows，可选）

1. 从 [Releases](https://github.com/Shifu-HU/uav-reg-watch/releases/latest) 下载 `UAVRegWatch-Setup.exe`，双击安装（默认装到 `%LOCALAPPDATA%\UAVRegWatchMine`，无需管理员权限）；
2. 从开始菜单 / 桌面快捷方式启动即可，安装包自带基线数据库，装完就有内容。

> 想清空数据重新开始：删掉 `data` 文件夹，程序会自动重新全量抓取（较慢，一次性）。

---

## 三、首次配置

两个版本都只需要确认一件事：**地区**。设置里选好省 / 市，地理过滤就按它工作（保留全国性 + 本地 + 同省规定）。

其余配置都有默认值：

| 配置 | 默认 | 说明 |
|---|---|---|
| 地区 | 深圳 / 广东 | **建议先改成你的省市** |
| 信息源 | 民航局官网 + Bing | SearXNG 默认关，自建服务后可在 config 开启 |
| AI 模型 | 手机版内置 Qwen2.5-0.5B；桌面版 `uav-reg`（未找到时回退 `qwen3:4b` → `qwen3:8b`） | 桌面版执行 `ollama pull qwen3:4b` 即可 |
| 主题 | 8 套配色 × 深 / 浅模式 | 改哪存哪，无需点保存 |

---

## 四、日常使用

- **今日情报**：顶部「今日补充 / 总计收录 / 本地相关」汇总条，下面分三块（重大 / 近期需注意 / 更早），块内按 重大→重要→普通 排序；
- **晨报**：置顶飞行建议横幅（不能飞 / 谨慎 / 可以飞），下面按「临时禁飞 / 今日新增 / 关注 / 参考」分区；
- **收藏**：★ 收藏、🚫 过滤，两端一致；
- **每日增量**：自动搜索上次之后新发布的规定，当日新增 + 历史未读池补足，不重复；
- 首次打开晨报约 40 秒（含天气联网查询），启动时会后台预热，之后秒开。

---

## 五、常见问题

| 问题 | 原因与处理 |
|---|---|
| 概括和原文不一致？ | 会。概括只是索引。点进晨报看正文，再点「查看原文」看原始公告；能不能飞一律以原文为准 |
| 手机首次打开很慢？ | 首次启动要把约 470 MB 的模型解包到本机（一次性），等一分钟左右，之后就快了 |
| 需要一直联网吗？ | 不用。只有抓新法规时联网；阅读、过滤、AI 摘要全部本机完成 |
| 手机版和电脑版什么关系？ | 两个独立版本，各自都能单独使用，共用同一套法规逻辑与配色 |
| 手机版要连电脑吗？ | 不用。运行时、法规库、AI 模型都在 APK 里 |
| 民航局官网抓不到内容？ | 该站检索必须 POST（GET 会被 302 到反爬页），程序已内置处理；若仍为空，多为其站点临时维护 |
| 全国的法规被当成"外地"过滤掉了？ | 不会。程序会剥离民航局页脚机构列表再判断，并保留所有带全国性标志词的文件 |

---

## 六、从源码运行

不想装打包版，或者想参与开发：

```bash
git clone https://github.com/Shifu-HU/uav-reg-watch.git
cd uav-reg-watch
pip install -r requirements.txt

python -m uavwatch.cli doctor        # 检查环境
python -m uavwatch.cli bootstrap     # 首次全量基线（较慢，一次性）
python -m uavwatch.tui               # 桌面版 GUI
python -m uavwatch.cli daemon        # 常驻定时抓取（按 config 的 schedule）
python -m uavwatch.cli brief         # 手动生成今日简报
```

手机版工程在 `src/android/`（Java 壳 + 手机端 Python 入口 `phone_server.py` + Qt 兼容 shim）。
APK 打包需要交叉编译 llama.cpp 与内嵌 CPython，直接安装 Release 里的 APK 更省事。

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
├── src/android/           手机版工程（Java 壳 + phone_server.py + shim）
└── config.yaml            桌面版全部配置
```

---

## 七、免责声明

本项目只是**信息聚合工具**，不构成任何飞行合规建议。法规以官方发布为准，飞行前请自行核实空域、实名登记与当地管理规定。

## License

[MIT](LICENSE) © 2026 Shifu-HU ｜ English documentation: [README.md](README.md)
