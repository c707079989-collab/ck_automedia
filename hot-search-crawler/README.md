# hot-search-crawler

热搜榜采集器的工作目录。

- `data/latest-hourly.json` —— 最近一次抓到的热搜榜原始数据（每小时一份，**运行期数据，不入库**）
- 消费方：`social-auto-upload/hot_capture/pipeline.py` 的「按热搜」模式，
  从这份 JSON 里抽核心短词当抓取关键词

数据文件由抓取任务生成，`data/` 已在 .gitignore 里排除，所以这个目录本身只留这份说明。
