# apps/ —— 自媒体自动化栈（当前状态）

一条链路：**抓爆款 → 拆爆款 → 照着生成 → 一键发布**。
主程序是 `social-auto-upload`，抓取引擎是 `media-crawler`，其余都是它的依赖或历史版本。
完整的目录体检（该留/该删）见 `PROJECT_MAP.md`。

## 仓库

整个 `apps/` 是一个仓库，远端 `github.com/c707079989-collab/ck_automedia`，从这一层提交与推送：

```bat
cd apps
git add -A && git commit -m "..." && git push
```

- 不再进仓库的：`data/`（含 cookie、账号库）、`cookiesFile/`、`browser_data/`、`.venv/`、
  `node_modules/`、构建产物 `dist/`、`_archive/`
- `docs/HISTORY.md` 是合并前 `social-auto-upload` / `media-crawler` 两个仓库的提交日志
- 原来的两个 `.git` 归档在 `_archive/git-backup/`（本地保留，不发布）
- 为什么是快照重建：原 `social-auto-upload` 是浅克隆，且合并提交 `3847fff` 的父提交
  `7023053e` 在本地不存在（被 `.git/shallow` 掩盖），推上去必然被远端拒绝
  （`remote: fatal: did not receive expected object 7023053e`）

## 组件

| 目录 | 作用 | 状态 |
|---|---|---|
| `social-auto-upload/` | **主程序**。Flask 后端（5409）+ Vue3 控制台 + 17 平台 uploader + 爆款模块 | ✅ 在用 |
| `media-crawler/` | **抓取引擎**。被主程序以子进程调 `main.py`，做小红书/抖音关键词搜索 | ✅ 在用 |
| `hot-search-crawler/data/latest-hourly.json` | 热搜离线快照（24K）。公开源缺板块时用它补齐 | ✅ 在用 |
| `PROJECT_MAP.md` | 目录/文档逐项判定与清理记录 | ✅ |
| `start-stack.cmd` | 一键起后端（5409） | ✅ |
| `_archive/` | 已停用的 v0.3 自研发布版、Android 确认端、v0.4 bridge（tar.gz，含源码不含登录凭据） | 📦 归档 |

## 快速开始

```bat
cd apps
start-stack.cmd
```

打开 http://127.0.0.1:5409 —— 六个入口：

| 页面 | 路径 | 作用 |
|---|---|---|
| 账号管理 | `#/account-management` | 扫码登录小红书/抖音/B站等，登录态落 `db` + `cookiesFile/` |
| 素材管理 | `#/material-management` | 上传待发布视频 |
| 发布中心 | `#/publish-center` | 选账号、填标题、发布到各平台 |
| **爆款工作台** | `#/hot-analysis` | 配置抓取 + 封面列表 + 逐条 agent 拆解 |
| **创作工作台** | `#/creator` | 爆款复刻 / 完全自创 / 口播类 → 方案 → 本地 ComfyUI 出片 |
| 关于 | `#/about` | 版本与依赖状态 |

## 爆款工作台（`#/hot-analysis`）

**配置区**
- 模式：`按领域`（15 个内置赛道 + 自定义补充词） / `按热搜`（读热搜榜抽核心短词）
- 条数 3-200，按关键词均分；自定义词优先保留
- 平台：小红书 / 抖音
- `真抓取` 开关：开=调 MediaCrawler（无头，2-5 分钟）；关=只读本地已有数据（调页面用）
- 登录态**复用「账号管理」**，抓取时不再单独扫码

**结果区**
- 封面卡片：点图或「看原帖」跳原视频，每条一个「分析」按钮
- 分析抽屉：爆款潜力分、钩子、内容结构、情绪需求、为什么爆、标题公式、可复用脚本、风险
- 每条一次**独立 WorkBuddy agent 任务**（本地 `custom-local:qwen3.8-27b`），单条约 60-90 秒
- 已分析的存 `data/hot_capture/analysis/`，再点读缓存；前端并发上限 3

## 创作工作台（`#/creator`）

**页面结构：三步向导**

| 步骤 | 内容 |
|---|---|
| ① 选题 | 模式三卡 +（爆款复刻）已分析列表 /（自创·口播）一句话输入 + 画幅 + 补充要求 |
| ② 方案与分镜 | 方案字段可改；分镜是**一行一镜的紧凑列表**，点一行才展开编辑（画面/字幕/台词/运镜/提示词） |
| ③ 出片与导出 | 出片按钮 + 逐镜状态 + 成片回放 + 运行日志 + 提词卡/分镜表/SRT 导出 |

- 「我的方案」是右侧**竖向浮动按钮**（默认折叠，显示方案数），点开是右侧抽屉小窗
- 抽屉上半是**临时方案**：选题表单一动就自动存（防抖 700ms），写一半跑掉也能点回来继续
- 抽屉下半是**我的方案**：点一条时若有未保存改动，会提示「保存并切换」，保存后才跳转
- 有出片记录的方案直接落到第 ③ 步；没有的落到第 ② 步改稿
- 出片进度条常驻在向导右上角，切到任何一步都看得见，点它跳第 ③ 步
- 第 ② 步顶部有**参考爆款**区（仅爆款复刻）：原片钩子 vs 本片钩子并排对照，
  下面是可复用套路、要避开的坑，展开还有原片结构 / 翻拍建议 / 为什么爆 / 标题公式，
  点原片标题直接跳回原视频。这些数据存在方案的 `source.analysis` 里，不依赖分析缓存
- 选题没走完（还没生成方案）时，点第 ② / ③ 步会被拦住并提示

三种选题入口，共用一套方案结构：

| 模式 | 输入 | 产出 |
|---|---|---|
| **爆款复刻** | 从爆款工作台已分析的条目里选一条 | 保留原片验证过的钩子+节奏，换人设换场景的同构方案 |
| **完全自创** | 一句话说清想拍什么 | 完整方案（可直接出 AI 画面成片） |
| **口播类** | 想讲的观点/知识点 | **口播稿 + 提词卡 + 分镜表 + SRT 字幕**，真人出镜拍，不烧显卡 |

方案字段：标题 / 类型 / 目标人群 / 取胜点 / 前 3 秒钩子 / 人物场景设定 / 整片台本 / 逐镜分镜
（画面·运镜·字幕·口播·音效·英文出片提示词）/ 话题 / 发布建议。全部可在页面上改完再存。

**出片链路（MiniMax H3，本机 ComfyUI）**

```
UNETLoader(minimax_h3_hybrid_fl2va_ref2va_int8)
  → LoraLoaderModelOnly(turbo, 8步) → SamplerCustomAdvanced
CLIPLoader(qwen3vl_32b_minimax_h3) + VAELoader(video fp16) + VAELoader(audio fp32)
  → VAEDecode + VAEDecodeAudio → CreateVideo → SaveVideo   # 原生带 AAC 音轨
```

**前置条件**

- ComfyUI 必须起着（秋叶包 `F:\ComfyUI130\run_nvidia_gpu.bat`，`--lowvram`）
- **端口可配置**：页头「服务地址」里改，或改 `data/creator/config.json` 的 `comfy_url`。
  默认 `http://127.0.0.1:5008`，候选端口 `5008,7878,8188` 会自动探测——地址连不上就依次试，
  命中后自动写回配置并提示「端口已自动切到 …」。本机端口换过 7878 → 5008，不用改代码。
- 权重在 `F:\ComfyUI130\ComfyUI\models\`（diffusion_models 21G 合体版 + text_encoder 15G + 双 VAE）
- 页头会自检，缺什么直接列出来；`D:\qiuye\...` 那个实例是空壳，**没有主权重，别用**

**实测指标（RTX 5060 Ti 16G，`--lowvram`）**

| 项 | 数值 |
|---|---|
| 单段 768×1344 / 4s（107帧）/ 8 步 | **444 秒** |
| 单段 768×1344 / 5s（124帧）/ 8 步 | **600-620 秒**（7 镜实测 604/616/608） |
| 单段 768×448 / 2.3s（56帧）/ 8 步 | 80-120 秒 |
| 方案生成（本地 27B） | 100-130 秒（口播 7 镜结构化输出会到 3-4 分钟，超时 420s） |
| 124 帧 | 16G 显存安全上限，超了必 OOM |
| 生成时显存占用 | 15.9G 里只剩 1G 空闲 |

**踩坑记录（MiniMax H3 专属）**

- **`minimax_h3_turbo_v4_step600_comfyui_T8-convert.safetensors` 与 hybrid 主模型不完全匹配**：
  日志每步刷 `lora diffusion_model.blocks.N.adaln_proj.linear.weight shape '[96768, 8]' is invalid`。
  这些是调制层，加载失败被跳过；其余层正常挂载，8 步仍能出片（画面实测无崩坏）。
  换 LoRA 前先拿小尺寸试一段，别直接批量。
- 宽高必须被 32 整除；`length` 必须落在 17k+5 网格（24fps），否则时长对不上。
- 参考图节点编号必须用 41+，撞上 MiniMax 节点的 6 会返回 502（不是报错信息，极难查）。

**设计取舍**

- 逐段**串行**提交，不并发：一段就吃满显存，并发只会互相踢、全部变慢
- 断点续跑：`data/creator/videos/<plan_id>/s01_*.mp4` 已存在就认账，只补失败镜
- **任务状态会自愈**：后端重启/进程退出后任务会永远停在 running，现在每次读任务都按已落盘的分段重算，
  全齐就自动补拼接，缺镜就标「未完成 n/m」，页面不会再一直转「出片中」
- **口播台词写进出片提示词**：`says in Mandarin Chinese: "该镜原话"` 拼在 prompt 结尾。
  只写 "a man speaking to camera" 模型就自己编，听感是口胡（实测踩过）
- 跨镜一致性靠 `cast.identity_en`（英文人物+场景段）**逐字拼进每镜 prompt**；纯文生视频一定漂移
- 宽高必须被 32 整除，帧数落在 17k+5 网格（`config.frames_for()` 负责换算）

**数据落点**

```
data/creator/plans/*.json     方案（含分镜、出片记录）
data/creator/tasks/*.json     出片任务进度（重启后还在）
data/creator/videos/<plan>/   s01_*.mp4 分段 + 拼接成片
data/creator/config.json      ComfyUI 地址、模型名、步数（覆盖 config.py 默认值）
```

## 已知限制

- **小红书封面 URL 带签名，约 8 小时后 403**。抓完立刻下到 `data/hot_capture/covers/`，老快照的封面会显示「封面已过期」。
- **抖音搜索固定 10 条/页**，条数填小于 10 也会抓一整页再截断。
- **连续抓取会被抖音限流**：接口返回 `status_code=0` 但 `data=[]`，表现就是「没有抓到任何内容」。间隔半小时以上再试。
- **别让纯文本模型读封面图**：单条分析会从 90 秒涨到超时。`llm_config.json` 的 `agent_use_cover` 默认 0。
- 小红书走 CDP 有头（`browser_data/cdp_xhs_user_data_dir`）；抖音走 `--storage-state` 无头，不弹浏览器。

## 手动启动与自测

```bat
cd apps\social-auto-upload
.venv\Scripts\python.exe sau_backend.py                 :: 后端 5409
.venv\Scripts\python.exe hot_capture\test_workbench.py  :: 15 项自测
.venv\Scripts\python.exe hot_capture\selftest.py        :: 抓取自测
.venv\Scripts\python.exe hot_capture\test_analyzer.py   :: 分析自测
```

改前端后要同步构建产物（后端托管的是 `assets/` + `index.html`，不是 `dist/`）：

```bat
cd apps\social-auto-upload\sau_frontend && npm run build
cd .. && copy /Y sau_frontend\dist\index.html index.html && xcopy /Y /E /I sau_frontend\dist\assets assets
```

## 相关文档

- `PROJECT_MAP.md` —— 每个目录/文件该不该留，为什么
- `social-auto-upload/docs/superpowers/specs/2026-11-17-hot-capture-workbench-design.md` —— 爆款工作台设计稿
- `social-auto-upload/docs/` —— 上游文档（install / CLI / legacy-web / dayu / xueqiu …）
- `social-auto-upload/README.md` —— 上游说明（17 平台、CLI 用法）

## 下一步

路线图只剩第 3 步：**ComfyUI 生成同款视频**，把「分析结论 → 分镜脚本 → 本地 ComfyUI 出片 → 发布中心」串成闭环。
