# apps/ 项目地图与清理结论

> 生成于 2026-11-17。口径：**是否被当前运行链路引用**（不是"写得好不好"）。
> 当前唯一在跑的主链路：`social-auto-upload`（主程序）+ `media-crawler`（抓取引擎）。

## 一、顶层目录判定

| 目录 | 体积 | 判定 | 依据 |
|---|---|---|---|
| `social-auto-upload/` | 814M | **必须** | 主程序。后端 5409、Vue 控制台、17 平台上传器、爆款模块（`hot_capture/`）、账号库 |
| `media-crawler/` | 751M | **必须** | 真抓取引擎。被 `hot_capture/pipeline.py` 以子进程调 `main.py` |
| `hot-search-crawler/` | 11M | **只需 1 个文件** | 只有 `data/latest-hourly.json`（24K）被当热搜离线兜底读；Go 源码/docs 不参与运行 |
| `hot-capture/` | 1.6M | **可删** | 上一代独立版，代码已复制并增强进 `social-auto-upload/hot_capture/`（capture.py / run_search.py / login_xhs.py 新版都改过），且无任何引用 |
| `sau-bridge/` | 1.1M | **待定** | v0.4 增强层（改写 + 3-gram 相似度拦截 + 留痕 + 8790 API）。`social-auto-upload` 前后端**零引用**，只有 `start-stack.cmd` 会拉起它 |
| `publish-bridge/` | 36M | **可删** | v0.3 自研 Node 发布版，README 自述"已被取代"，无引用 |
| `publish-confirm-android/` | 59K | **可删** | v0.3 Android 确认端，未接线 |
| `start-stack.cmd` | 1K | **可用**（需同步） | 拉起 5409 + 8790；若删 sau-bridge 必须同步改它 |
| `README.md` | 8K | **过时** | 只写了 v0.4 发布栈，没提爆款采集/分析，目录表也缺 social-auto-upload 内部说明 |

⚠️ **重要**：`sau-bridge/`、`publish-bridge/`、`publish-confirm-android/` **都不在 git 里**（不是仓库，也没被任何仓库跟踪）。
删除 = 永久丢失。要清就先打包成 zip。

## 二、social-auto-upload 内部

| 子项 | 体积 | 判定 | 说明 |
|---|---|---|---|
| `sau_backend.py` | 42K | **必须** | Flask 入口，含 `/hotCapture/*` 全部路由 |
| `hot_capture/` | 133K | **必须** | 爆款模块：capture / domains / cover_cache / pipeline / analyzer / agent_cli / accounts |
| `uploader/` `myUtils/` `sau_commands/` | 943K | **必须** | 17 平台上传器、登录与 cookie 校验、CLI |
| `db/database.db` | 20K | **必须** | 账号表（小红书「就吃香菜」、抖音「浪浪」） |
| `cookiesFile/` | 76K | **必须** | 登录态 storage_state；抓取复用就靠它 |
| `assets/` + `index.html` | 1.7M | **必须** | 前端构建产物，后端实际托管这份（不是 dist） |
| `sau_frontend/` | 129M | **改前端才要** | `node_modules` 占 127M；只跑不改可用 assets 顶住 |
| `.venv/` | 590M | **必须** | 运行环境（jieba、flask、playwright 都在里面） |
| `data/hot_capture/` | 19M | **运行时数据** | 快照 / 封面缓存 / 单条分析 / 任务进度 |
| `logs/` | 161K | **运行时** | 可随时清 |
| `videoFile/` `media/` `videos/` | 36M | **运行时素材** | 上传缓存与演示素材；可清旧的 |
| `utils/article_renderer/` | 31M | **图文才用** | 短视频链路用不到，留着不碍事 |
| `docs/` | 176K | **参考** | 上游文档（install / CLI / legacy-web 等）；含我们的 `superpowers/specs/` 设计稿 |
| `tests/` `examples/` `skills/` | 578K | **参考** | 上游自带，删了不影响运行 |
| `static/` | 612K | **必须** | 页面内置图（logo、二维码底图） |
| `cookies/` | 4K | **可删** | 6 个空目录，无文件引用 |
| `sau_backend/` | 32K | **可删** | 只剩一个 README，早期蓝图残留 |

## 三、media-crawler 内部

| 子项 | 体积 | 判定 | 说明 |
|---|---|---|---|
| `main.py` `base/` `cmd_arg/` `config/` `constant/` `media_platform/` `model/` `store/` `tools/` `proxy/` `libs/` | ~3.2M | **必须** | 抓取执行体 |
| `.venv/` | 480M | **必须** | 独立环境（playwright、patchright） |
| `.run/xhs_cookie.txt` `.run/xhs_storage_state.json` | 58K | **必须** | 小红书登录态，抓取时读它 |
| `.run/xhs_test*.log` | 22K | **可删** | 早期联调日志 |
| `data/xhs/` `data/douyin/` | 828K | **必须** | jsonl 落盘，列表接口从这里读 |
| `browser_data/cdp_xhs_user_data_dir/` | 159M | **必须** | 小红书 CDP 有头链路仍在使用（`--headless false` + CDP 开启） |
| `browser_data/cdp_dy_user_data_dir/` | 69M | **可删** | 抖音已改 `--storage-state` 无头模式并跳过 CDP，这套 profile 不再用 |
| `docs/` | 12M | **只留两个文件** | `hit_stopwords.txt` 与 `STZHONGS.TTF` 被 `config.STOP_WORDS_FILE` / `FONT_PATH` 引用（词云用），其余文档站资源已删 |
| `webui/` `cache/` | 911K | **保留** | `webui` 只被 `api/main.py`（FastAPI 模式）用到，`cache` 是下载缓存；都小，不动 |
| `tests/` `test/` | 373K | **已删** | 上游自测，不影响运行，git 可恢复 |
| `database/` | 48K | **必须** | `main.py` 里 `from database import db`，删了直接起不来 |
| `api/` `media_downloader/` | 268K | **备用** | 图/视频下载与 API 模式，当前 `--get_media false` 不启用 |

## 四、文档判定

**项目自己的（要留，且要更新）**
- `apps/PROJECT_MAP.md` ← 本文
- `apps/README.md` ← 内容过时，需补爆款模块与新目录表
- `social-auto-upload/docs/superpowers/specs/2026-11-17-hot-capture-workbench-design.md` ← 爆款工作台设计稿
- `docs/superpowers/specs/2026-11-13-cn-platform-landing-design.md`（工作区根）← 早期平台落地设计

**已失效（与代码不一致，留着会误导）**
- `hot-capture/README.md` ← 描述已被整合进主程序的旧版
- `publish-bridge/README.md` ← v0.3 自研版，自述已被取代
- `sau-bridge/README.md` ← 若保留 sau-bridge 则仍有效，否则一并归档

**上游文档（参考价值，非运行必需）**
- `social-auto-upload/docs/*.md`（10 篇：install / CLI / legacy-web / dayu / xueqiu …）
- `social-auto-upload/README.md`、`CHANGELOG.md`
- `media-crawler/README*.md`、`media-crawler/docs/`（仅剩停止词与字体，其余文档站已移）
- `hot-search-crawler/README.md`、`hot-search-crawler/docs/`

## 五、清理分档

**A 档：安全（全部可从 git 恢复，约 100M）**
- 删 `hot-capture/`（git 有 c85707d）
- 删 `media-crawler/docs/` 文档站资源（保留停止词/字体）、`tests/`、`test/`、`.run/xhs_test*.log`
- 删 `media-crawler/browser_data/cdp_dy_user_data_dir/`（69M，抖音已不用 CDP）
- 删 `hot-search-crawler` 除 `data/latest-hourly.json` 外的内容（保留快照即可运行）
- 删 `social-auto-upload/cookies/`（空目录）、`sau_backend/`（空壳）

**B 档：需先打包（约 37M，三者都不在 git）**
- `sau-bridge/` + `publish-bridge/` + `publish-confirm-android/` → `apps/_archive/v0.3-v0.4-*.zip` 后移除
- 同时改 `start-stack.cmd`（不再拉 8790）与 `apps/README.md`

**不动**
- 两个 `.venv`、`sau_frontend/node_modules`、`assets/`、`cookiesFile/`、`db/`、`data/hot_capture/`
- `browser_data/cdp_xhs_user_data_dir/`（小红书 CDP 仍在用）

## 六、执行记录（2026-11-17 11:32）

按 **B 档** 执行完毕，`apps/` 从 1.6G 降到 **1.45G**。

| 动作 | 结果 |
|---|---|
| 归档 `sau-bridge` / `publish-bridge` / `publish-confirm-android` | `_archive/*.tar.gz`（16K + 28K + 9.3K；**排除** node_modules 与 data/ 里的登录凭据），原目录已移除 |
| 删 `hot-capture/` | 可恢复：`git -C <clone> log c85707d` |
| 删 `media-crawler/docs` 的文档站资源(约 10M)、`tests` `test` `.run/xhs_test*.log` | 保留 `hit_stopwords.txt` + `STZHONGS.TTF`（12M，配置里引用，删了词云会 FileNotFoundError） |
| 删 `media-crawler/browser_data/cdp_dy_user_data_dir`(69M) | 抖音改 storage_state 无头后不再用 CDP profile |
| `hot-search-crawler` 只留 `data/latest-hourly.json` | 少了 11M 源码与历史快照；公开源失效时仍需这个文件补板块 |
| 删 `social-auto-upload/cookies/`（6 个空目录）`sau_backend/`（只剩 README） | 无引用 |
| 改 `start-stack.cmd` | 不再拉 8790，只起 5409 |
| 重写 `apps/README.md` | 按当前链路写（采集/分析/发布/限制） |

清理后回归：服务 5409 正常，`/hotCapture/domains` 200，`test_workbench.py` 15 项全过，
热搜模式关键词正常（公开源 11:30 数据 + 本地快照补 xiaohongshu 板块）。

**清理中发现的一个独立问题**：抖音抓取在 11:34 起连续返回 0 条。
实测 `search_info_by_keyword` 返回 `status_code=0` 但 `data=[]`（冷启动与预热搜索页都一样，`xmst` 均为空），
而同一条命令 10:51 / 11:01 / 11:25 三次都成功——判定为**抖音侧限流**（40 分钟内 3 次抓取触发），
与本次清理无关。处理：间隔 30 分钟以上再抓；代码不需要改。

