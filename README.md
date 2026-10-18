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

# 重启同一命令：跳过成功课程，继续待处理及失败课程
python main.py --crawl

# 使用已有 URL 队列，或先处理少量任务
python main.py --crawl --url-file data/utm_course_urls.json --limit 10
```

默认文件：`data/utm_crawl.sqlite3` 保存检查点与课程数据，
`data/utm_courses.json` 保存成功课程的 JSON 导出，`data/crawl.log` 保存进度、重试与异常堆栈。
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
所有页面失败在下一次 `--crawl` 时可再次尝试。

每门课程的数据与成功状态在同一 SQLite 事务提交。Ctrl-C 会导出已提交结果；
强制结束或断电后 SQLite 仍是恢复依据，最后一个未提交任务会重抓。
JSON 在正常结束或可处理的中断时原子替换，运行过程中不代表最新进度。
成功课程不会自动刷新；要更新全目录，请使用新的 `--database` 路径。
默认只在空数据库时重新发现目录，发现过程本身中断后会从第一页重新开始。

运行结束打印本轮和累计统计；存在失败课程时退出码为 1，完成或主动 `--limit` 分批退出为 0。
课程详情页网络/解析错误继续处理；目录发现失败会停止，避免使用不完整目录。
磁盘、数据库、配置错误明确停止。
当前是本机单 worker 架构，数据库文件锁防止重复启动；支持 macOS/Linux（POSIX）。

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
它没有保存原始 HTML，不能独立用于离线重新运行 parser；如需这一能力，后续应在 HTTP/持久化层保存 HTML。
历史文件是成功结果集合，可能来自分批或含失败的抓取；完整性需结合运行统计/日志判断。
快照内容标识不表示运行时间或 parser 版本；本版不增加元数据文件、自动清理或远程备份。

[Phase 4 完成报告](docs/reports/phase-4.md)

课程页面必须包含非空 Description；缺少正文视为不完整页面并记录失败。
Prerequisites 等可选字段缺失时仍为 `None`。
