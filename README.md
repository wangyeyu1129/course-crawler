# course-crawler

UTM Academic Calendar 课程采集器：目录发现、详情解析、断点恢复、刷新、原始 HTML
归档和离线重解析。当前支持 UTM；UTSG、UTSC、关系结构化、API 和前端尚未实现。

共享课程契约位于 `domain/`，采集用例与接口位于 `application/`，
通用时间/文件锁位于 `infrastructure/`；`crawler/` 保留 UTM 适配和旧入口。
`api/`、`frontend/` 当前仅为预留目录。
[目录边界与依赖规则](docs/architecture/module-boundaries.md) 说明允许的依赖及兼容路径。

## 环境与安装

支持 Python 3.11–3.14，推荐 Python 3.12；支持 macOS/Linux（使用 POSIX 文件锁）。
需要 OpenSSL 1.1.1 或更新版本，不通过屏蔽警告兼容 LibreSSL。
以下命令从仓库根目录执行：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python main.py --help
```

运行依赖及传递依赖固定在 `requirements.txt`；开发依赖在 `requirements-dev.txt`。
只运行采集器时可以安装 `requirements.txt`。

## 运行命令

```sh
# 单课程：输出到终端；加 --output PATH 可保存 JSON
.venv/bin/python main.py CSC148H5

# 目录发现：只输出 URL 数组，不下载课程详情
.venv/bin/python main.py --discover --output data/utm_course_urls.json

# 首次采集：空检查点自动发现目录，然后逐门抓取
.venv/bin/python main.py --crawl

# 恢复：已有检查点只处理 pending，不重新发现或刷新成功课程
.venv/bin/python main.py --crawl --operation resume

# 重新发现目录，将新增 URL 加入任务库，不下载详情
.venv/bin/python main.py --crawl --operation discover

# 导入已有队列，并限制本次处理数量
.venv/bin/python main.py --crawl --url-file data/utm_course_urls.json --limit 10

# 待处理课程优先，然后重试失败课程；每个 URL 在单轮最多处理一次
.venv/bin/python main.py --crawl --retry-failed

# 刷新已有队列；不重新发现目录。--refresh 为兼容别名
.venv/bin/python main.py --crawl --operation refresh

# 刷新中断或分批后，使用 resume 继续；不要再次重置刷新任务
.venv/bin/python main.py --crawl --operation resume

# 从历史 HTML 离线重新解析；旧数据没有 HTML 时需先采集或刷新
.venv/bin/python main.py --crawl --operation reparse --raw-directory data/raw/utm_courses
```

`--discover` 输出 URL 文件；`--crawl --operation discover` 更新任务数据库。
两者都执行真实目录发现。目录发现失败不返回或发布部分目录。

空数据库不能执行 refresh；reparse 可从原始 HTML 索引创建空任务库。
任务库中的 URL 只增不减，发现操作不会自动移除网站已下架的课程。
这里的“完整发布”指已知队列全部成功，不代表已经证明目录与网站实时一致。

| 参数 | 默认值与行为 |
| --- | --- |
| `--database` | `data/utm_crawl.sqlite3`；采集检查点与结果 |
| `--output` | crawl 模式为 `data/utm_courses.json`；其他模式不指定时输出到终端 |
| `--log-file` | `data/crawl.log`；10 MB 轮转，保留 3 个备份 |
| `--url-file` | 导入非空 JSON URL 数组，规范化并去重 |
| `--limit` | 单次处理上限；未指定时处理本轮所有选中任务 |
| `--workers` | 默认 1，范围 1–16；全局限速仍然有效 |
| `--commit-batch-size` | 默认 1；增大会增加强制退出后的重抓数量 |
| `--interval` | HTTP 尝试开始时间的最小间隔，默认 1 秒 |
| `--timeout` | 读取超时默认 30 秒；连接超时为 10 秒 |
| `--retries` | 首次失败后最多重试 3 次 |
| `--user-agent` | 项目标识和 GitHub 地址，可配置 |
| `--max-count-drop` | 相比历史输出允许下降的比例，默认 0.1；1 表示关闭数量下降检查 |
| `--http-cache` | 默认在检查点旁创建 `<数据库名>_http.sqlite3` |
| `--raw-directory` | 默认在输出旁使用 `raw/<输出名>/` |

不适用的参数会在 I/O 前报用法错误，包括显式传入的默认值；不接受选项名称缩写。
目录和 crawl 模式不接受课程代码；离线模式不接受网络参数。

## 数据契约与发布

`parse_course_page(html)` 是不访问网络的解析入口，返回以下 12 个字段：

```text
code, name, campus, description, prerequisites, exclusions,
enrolment_limits, distribution_requirement, instruction_hours,
delivery_mode, program_area, url
```

课程标题、代码、Description 和 URL 必须有效。可选字段缺失或为空时为 `None`，
JSON 中为 `null`；多值字段用 `; ` 连接。关系目前保存为原文，尚未解析 AND/OR。
当前契约没有独立 corequisite、学分或学年字段，也没有记录可选字段为空的具体原因。

```text
data/
  utm_course_urls.json                  # URL 队列快照
  utm_crawl.sqlite3                     # 本地检查点，恢复依据
  utm_courses.json                      # 队列全部成功时更新的正式结果
  progress/utm_courses.json             # 未完成运行的进度结果
  history/utm_courses/<SHA-256>.json     # 课程结果历史，按内容去重
  runs/utm_courses/<run_id>.json         # 时间、版本、统计与发布状态
  raw/utm_courses/index.sqlite3         # URL、正文哈希、run ID、采集时间
  raw/utm_courses/html/<SHA-256>.html    # 交给 parser 的解码后 HTML，UTF-8
  utm_crawl_http.sqlite3                # 最近的条件请求缓存
  crawl.log                            # JSON Lines 运行日志
```

上述目录按需生成；早期采集的数据不会自动补齐 HTML、元数据和新版日志。
课程 JSON 可提交 Git；检查点、缓存、日志和 raw 是本地运行资料。
修改 `--output` 时，快照、元数据、进度和默认原始 HTML 路径随输出位置变化。

每门成功结果和任务状态在同一事务提交。课程详情页失败不终止其他课程；
数据库、磁盘、配置或目录发现异常会停止运行。中断尽力导出已提交结果，保留原始退出原因。

只有非空队列全部成功才更新正式输出，否则更新 progress；旧正式输出保留。
刷新失败时，进度中可能保留该课程最近一次成功的数据，失败状态仍然存在。
运行结束输出 run ID、统计、`published`、`export_path` 和快照路径。
存在失败课程时退出码为 1；正常完成或主动分批退出为 0；用法错误为 2。

数据库、正式输出、进度输出和日志加独占锁，避免不同任务覆盖相同文件。
JSON 通过临时文件原子替换；归档与压缩备份执行文件和目录同步。
目录同步失败可能发生在新文件已可见之后，应检查并重新运行；实际断电保证受文件系统影响。

## HTTP 与目录校验

HTTP Session 复用；多线程各自使用 Session，SQLite 结果由主线程写入。
超时、连接失败、响应截断及 429/500/502/503/504 采用指数退避并遵守 Retry-After。
其他 HTTP 错误和解析失败不立即重复尝试。3xx 重定向直接拒绝，不跟随 Location。

有 ETag/Last-Modified 时使用条件请求；304 复用经过哈希校验的缓存正文。
缓存按请求头区分，遵守 no-store 和 Vary:*，不假定源站一定支持条件请求。
缓存不等同于原始 HTML 历史。

目录校验分页容器、当前页、连续下一页、末页证据及分页循环。
有历史输出时比较唯一课程 URL 数量；数量不是写死的固定总数。
课程代码限制 ASCII，URL 限制官方 HTTPS 域名，无凭据，仅接受默认端口。

## 维护与备份

以下命令默认只预览，追加 `--apply` 才修改文件：

```sh
.venv/bin/python -m crawler.maintenance data/utm_courses.json --keep 20
.venv/bin/python -m crawler.durability data --min-age 86400
```

历史维护保留最新指定数量的未压缩快照及当前发布内容。旧快照压缩为 `.json.gz`，
校验解压内容 SHA-256 后才删除原副本；元数据、原始 HTML 和数据库不参与清理。
压缩备份仍在本机，可用 gzip 解压恢复原始字节；当前没有自动异地备份。

临时文件清理不递归，仅处理 `.crawler-tmp-进程号-*`：进程已退出、超过保留时长、
当前用户拥有的普通文件且文件锁可获取。活跃文件、符号链接和未知名称保留。

## 验证与 CI

```sh
.venv/bin/python -m pip check
.venv/bin/lint-imports --no-cache
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m unittest discover -s tests -v
```

测试不访问外部网站；传输测试会绑定回环地址的临时端口。
CI 在 push/PR 时运行 macOS/Linux × Python 3.11/3.12/3.13/3.14 共八组检查，
包含依赖、TLS、架构依赖契约、Ruff 和测试。现有完整页面样本覆盖 CSC108、CSC148、CSC207、MAT102、ANT102。

[T01 基线检查清单](docs/reports/t01-baseline.md) 记录指定提交的验证结果与数据状态。
历史阶段报告保留当时的实施记录；当前运行方式以本 README 和 CLI 帮助为准。

[T02 目录边界与共享模型报告](docs/reports/t02-module-boundaries.md) 记录本次迁移及兼容性验证。
