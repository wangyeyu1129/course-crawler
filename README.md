# course-crawler

UTM Academic Calendar 单课程解析器。Python 3.9+。

```sh
python -m pip install -r requirements.txt
python main.py CSC148H5
python -m unittest discover -s tests -v
```

`parse_course_page(html)` 是不访问网络的解析入口，返回以下稳定字段：
`code`, `name`, `campus`, `description`, `prerequisites`, `exclusions`,
`enrolment_limits`, `distribution_requirement`, `instruction_hours`,
`delivery_mode`, `program_area`, `url`。

可选字段缺失或为空时返回 `None`（JSON 中为 `null`）；多值字段按页面顺序
以 `; ` 连接。先修要求保留原始文字语义，不转换为课程依赖图。
缺失或非法课程标题抛出 `ValueError`；网络错误由 requests 异常传递。

- `crawler/fields.py`：通用 DOM 字段提取。
- `crawler/utm_course.py`：UTM 字段映射、Description、课程解析与请求入口。
- `main.py`：命令行编排；导入不会发送请求。
- `tests/fixtures/`：2026-10-18 官方课程相关 DOM 片段，保留字段结构与文本；
  来源为 `https://utm.calendar.utoronto.ca/course/<文件名去掉 .html>`。
- [Phase 1D 完成报告](docs/reports/phase-1d.md)：设计权衡、测试和后续分层建议。

## 自动发现全部 UTM 课程

```sh
python main.py --discover --output data/utm_course_urls.json
```

不传 `--output` 时向标准输出打印 JSON URL 数组。发现过程从未筛选的
`/course-search?page=0` 开始，跟随官方下一页链接，直至最后一页。
搜索结果标题当前没有课程链接，因此从标题中的课程代码构建 URL，避免漏课，
也避免将先修/排除条件中的引用误当成当前目录条目。
URL 统一为官方 HTTPS、小写路径，按首次出现顺序去重。

`crawler.discovery.discover_course_urls()` 返回 `collections.deque[str]`，后续 worker
可用 `popleft()` 逐项取出。它只生成队列，本阶段不批量下载课程详情。
`extract_course_urls(html)` 可独立用于提取页面中的真实课程链接。
网络请求复用 session，每次搜索页请求之间默认等待 1 秒；函数支持注入 fetcher 离线测试。
分页循环、超过安全页数、结构异常或网络失败会报错，不返回部分队列，
发现失败时不会覆盖已有输出文件。队列目前是 JSON 快照，尚无执行状态、断点续跑或持久化调度。

[Phase 2 完成报告](docs/reports/phase-2.md)

## 完整抓取与断点恢复

```sh
# 首次运行：自动发现目录，再逐门抓取、解析、保存
python main.py --crawl

# 重启同一命令：跳过成功课程，继续待处理课程
python main.py --crawl

# 使用已有 URL 队列，或先处理少量任务
python main.py --crawl --url-file data/utm_course_urls.json --limit 10
```

默认文件：`data/utm_crawl.sqlite3` 保存检查点与课程数据，
`data/utm_courses.json` 保存每门课程最近一次成功结果的 JSON 导出，`data/crawl.log` 保存进度、重试与异常堆栈。
SQLite 和日志是本地运行文件，不提交 Git；课程 JSON 是可分享的数据快照。

| 参数 | 默认值 / 用途 |
| --- | --- |
| `--database` | `data/utm_crawl.sqlite3`；更换文件可开始独立全量抓取 |
| `--output` | 抓取模式默认 `data/utm_courses.json` |
| `--log-file` | `data/crawl.log`；10 MB 轮转，保留 3 个备份 |
| `--url-file` | 导入非空 JSON URL 数组，规范化去重后加入现有队列 |
| `--limit` | 单次最多处理多少门未完成课程 |
| `--interval` | 1 秒；每次 HTTP 尝试的最小开始间隔 |
| `--timeout` | 30 秒读取超时；连接超时为 10 秒 |
| `--retries` | 首次请求失败后最多重试 3 次 |
| `--user-agent` | 项目标识和 GitHub 地址；可以自行配置 |

HTTP 策略也用于单课程和 `--discover` 命令。超时、连接失败以及 429/500/502/503/504
采用指数退避重试，遵守 `Retry-After`；其他 HTTP 错误及解析失败不在当前页面立即重试。
失败课程需显式执行 `python main.py --crawl --retry-failed` 再次尝试，待处理课程始终优先。

每门课程的数据与成功状态在同一 SQLite 事务提交。Ctrl-C 会导出已提交结果；
强制结束或断电后 SQLite 仍是恢复依据，最后一个未提交任务会重抓。
JSON 在正常结束或可处理的中断时原子替换，运行过程中不代表最新进度。
默认恢复不刷新成功课程；使用 `--refresh` 显式刷新已有队列。
默认只在空数据库时重新发现目录，发现过程本身中断后会从第一页重新开始。

运行结束打印本轮和累计统计；存在失败课程时退出码为 1，完成或主动 `--limit` 分批退出为 0。
课程详情页网络/解析错误继续处理；目录发现失败会停止，避免使用不完整目录。
磁盘、数据库、配置错误明确停止。
当前每个数据库由单个进程写入，抓取可选并发；文件锁防止重复启动。支持 macOS/Linux（POSIX）。

[Phase 3 完成报告](docs/reports/phase-3.md)

开发检查（Python 3.9+，当前已在 macOS/Python 3.9 验证）：

```sh
python -m pip install -r requirements-dev.txt
ruff check crawler main.py tests
ruff format --check crawler main.py tests
python -m unittest discover -s tests -v
```

## Phase 4：JSON 数据与历史快照

每次 `--crawl` 导出课程结果时，同时保留历史版本：

```text
data/
  utm_courses.json                         # 当前成功课程结果
  history/
    utm_courses/
      <SHA-256>.json                       # 历史结果，按内容去重
```

命令和 12 个课程字段保持一致。运行结束的统计会给出 `snapshot` 文件路径；
使用自定义 `--output path/name.json` 时，历史文件自动放在同目录的 `history/name/`。
已有旧文件会在替换前归档，新结果也会先归档；字节完全一致时复用原快照，
内容变化时产生新快照。应用不会覆盖已有历史文件，手工编辑当前文件不会修改历史。
归档或发布失败会报错，并保留此前的当前文件；数据库检查点仍可用于恢复。

本阶段的 Raw Data 指课程解析结果 JSON，方便检查和对比历史结果。
JSON 快照本身不包含 HTML。HTTP 缓存保留最近的可验证响应，用于条件请求；它不是完整的历史 HTML 归档。
历史文件是成功结果集合，可能来自分批或含失败的抓取；完整性需结合运行统计/日志判断。
快照内容标识不表示运行时间或 parser 版本；本版不增加元数据文件、自动清理或远程备份。

[Phase 4 完成报告](docs/reports/phase-4.md)

课程页面必须包含非空 Description；缺少正文视为不完整页面并记录失败。
Prerequisites 等可选字段缺失时仍为 `None`。

目录发现会核对分页容器、当前页、连续下一页和末页证据。
已有输出存在时，发现操作会将其中唯一课程 URL 数作为历史基线，默认拒绝超过 10% 的数量下降。
`--max-count-drop 0.2` 允许最多下降 20%；`--max-count-drop 1` 取消数量下降限制，分页检查仍执行。
没有历史输出时，仅执行结构和分页一致性校验。

默认恢复只处理 pending 课程。`--retry-failed` 显式纳入失败任务，始终先处理 pending，
每个失败 URL 在单轮最多处理一次，避免 `--limit` 分批任务被永久失败课程阻塞。
未重试的失败记录仍保留在统计中，因此存在失败任务时退出码仍为 1。

HTTP 客户端禁止自动重定向：3xx 会明确报错，不跟随 Location，也不对该响应立即重试。

课程代码仅允许 ASCII 字母和数字；课程链接和分页统一接受官方域名的大小写变体及显式 443 端口，
输出移除默认端口。包含凭据、非默认端口或相似外域的 URL 会被拒绝。

`--commit-batch-size N` 可减少事务提交次数，默认 `1` 保持逐门提交。
增大该值后，强制结束可能需要重抓最多 N 门未提交任务；正常结束提交尾批。

`--workers N` 启用 1..16 个抓取线程，默认 `1`。所有线程共用全局请求限速和冷却策略，
分别复用 Session，SQLite 由主线程写入；并发不会放宽 `--interval`。
中断时不再提交新请求，已经开始的请求需要完成或超时后释放资源。


条件请求与刷新：

```sh
# 显式刷新已有课程，可复用 ETag / Last-Modified 缓存
python main.py --crawl --refresh --workers 2

# 中断/分批后继续当前队列，不再次重置刷新任务
python main.py --crawl --workers 2
```

缓存默认保存为检查点旁的 `<数据库名>_http.sqlite3`，可用 `--http-cache PATH` 指定。
缓存按实际请求头（包括 Cookie、User-Agent 等）区分，校验响应体哈希，遵守 no-store / Vary:*。
有验证器时发送条件请求，304 复用缓存正文；没有验证器或缓存损坏时正常下载，不假定网站一定支持缓存。
首次刷新需要建立缓存。服务器仍会收到条件请求；缓存减少的是重复正文下载。
刷新期间保留最近成功数据，单页刷新失败仍计入 failed，导出可包含该课程的旧有效结果。
旧检查点会自动迁移以支持此状态，运行统计表示本轮刷新完成情况。


采集操作分离（均使用 `--crawl`）：
- 默认 `--operation resume`：恢复待处理任务；空检查点才自动发现。
- `--operation discover`：重新发现并加入新增 URL，不下载课程详情，不重置成功任务。
- `--operation refresh`：只刷新已知队列，不重新发现目录；空队列需先发现。
旧 `--refresh` 是 refresh 操作的兼容别名；它不再同时执行目录发现。

每次采集的独立元数据保存到输出旁的 `runs/<输出名>/<run_id>.json`，
记录 UTC 时间、Git revision、parser 源码哈希、操作、任务统计及课程快照哈希；课程 JSON 结构不变。

正式 `--output` 仅在队列全部成功时更新。不完整运行写到旁边的 `progress/<输出文件名>`，
不会替换先前完整结果；统计中的 `published` 和 `export_path` 明确指出发布状态与实际位置。

详情页输入保存到输出旁的 `raw/<输出名>/html/<SHA-256>.html`，SQLite 索引关联 URL、run ID 和时间。
这是实际交给 parser 的解码后 HTML 文本，统一 UTF-8 存储；相同正文只保存一份。

```sh
python main.py --crawl --operation reparse --raw-directory data/raw/utm_courses
```

离线重新解析可从归档创建空检查点，不访问网站。归档损坏/缺失明确失败。
旧版本未保存的 HTML 需要后续抓取或刷新后才会出现；本地 raw 目录不自动提交 Git。

运行环境：支持 Python 3.11–3.14，macOS/Linux（任务锁使用 POSIX flock），
需要 OpenSSL 1.1.1 或更新版本。使用 `python3.12 -m venv .venv`，然后
`.venv/bin/python -m pip install -r requirements-dev.txt` 安装固定版本及开发工具。
不要通过屏蔽 TLS 警告继续使用 LibreSSL。CI 在两个系统、四个 Python 版本上检查。
本地旧虚拟环境保留于 `.venv-legacy`；项目解释器使用 `.venv/bin/python`。

日志采用 JSON Lines，每条包含 run ID、Git revision 和 parser 指纹，轮转上限约 40 MB。
历史默认永久保留。`python -m crawler.maintenance data/utm_courses.json --keep 20`
只展示压缩计划；追加 `--apply` 才执行：保留最新 20 个未压缩快照和当前发布内容，
其余快照先压缩为同目录 `.json.gz` 并验证 SHA-256，再移除未压缩副本。
运行元数据、原始 HTML、数据库不参与清理。压缩文件仍需用户按备份策略复制到独立存储；
使用 gzip 解压可恢复原始快照字节。
