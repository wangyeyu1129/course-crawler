# Phase 3：完整 UTM 爬取与恢复报告

日期：2026-10-18。角色：HTTP 策略、采集编排、持久化、测试及文档维护。

## 交付行为

`python main.py --crawl` 完成目录发现 → 课程请求 → HTML 解析 → 保存结果。
空数据库会通过官方搜索目录发现课程；队列非空时恢复现有任务。
也可通过 `--url-file` 导入 Phase 2 队列，输入去重不覆盖已完成课程。

默认 SQLite 检查点为 `data/utm_crawl.sqlite3`，结果导出为 `data/utm_courses.json`，
运行日志为 `data/crawl.log`。命令行打印本轮处理统计和累计 pending/succeeded/failed 数量。
存在失败课程时返回退出码 1，单页失败不打断其他课程处理。

## 责任边界与设计

| 组件 | 职责 |
| --- | --- |
| `crawler/http.py` | 可配置 User-Agent、连接/读取超时、GET 重试、指数退避、Retry-After 和跨请求间隔 |
| `crawler/discovery.py` | 目录 DOM 与分页解析；复用 HTTP 策略 |
| `crawler/utm_course.py` | 单课程解析与课程请求入口；复用 HTTP 策略 |
| `crawler/storage.py` | URL 去重、任务状态、课程结果、SQLite 事务与 JSON 原子导出 |
| `crawler/pipeline.py` | 注入 fetch/parse，逐门编排，隔离页面异常 |
| `crawler/runner.py` | 生命周期、文件锁、日志轮转、发现/恢复选择、最终导出 |
| `main.py` | 参数解析、调用服务、打印统计及退出码 |

通过函数组合和 fetch/parse 注入实现职责分离；CrawlStore 承担 Repository 的角色，
可注入行为承担策略角色。没有引入没有现阶段需求的继承体系或容器。
HTTP 策略不可变并在启动时验证，避免运行一半才发现非法超时、间隔或重试次数。

## 请求与故障策略

默认 User-Agent 标识本项目并提供 GitHub 链接；默认连接超时 10 秒、读取超时 30 秒，
请求开始间隔 1 秒，首次失败后最多重试 3 次。timeout 是连接/读取等待限制，并非整个下载的总时限。

只对超时、连接失败、传输截断、429/500/502/503/504 重试。退避为 1、2、4 秒；若服务器 Retry-After
要求更长等待则遵守，支持秒数与 HTTP 日期。请求间隔和退避取较大等待时间，避免重复累加。
最后一次 429/503 失败的服务器冷却要求仍约束下一门课程请求。
重试记录 URL、次数、等待时间和异常；页面失败记录 URL、fetch/parse 阶段及堆栈。
日志轮转至多 10 MB × 4 文件；历史失败状态仍保存在 SQLite，日志不是恢复的唯一依据。

单页 fetch/parse 边界捕获 Exception，包含未知解析错误，使后续课程继续执行；
不捕获 KeyboardInterrupt/SystemExit。存储操作置于这一边界外，磁盘或事务失败必须终止，
不能把未落盘数据误标为成功。解析出的课程 URL 必须等于队列 URL，防止错误跳转或错误页面污染结果。

## 断点恢复与一致性

数据库每个 URL 只有一行，规范化后作为主键。状态有 pending/succeeded/failed。
成功结果 JSON 与 succeeded 状态在同一事务提交；失败保存错误并增加处理次数。
字段 attempts 表示课程处理轮次，HTTP 尝试次数由重试日志记录。

重启时快照选择 pending 和 failed，每个任务在同一轮只处理一次；成功课程不重复下载。目录发现失败会明确终止，不导入不完整目录。
中断发生在 HTTP/parse/提交之前时，任务仍未完成，下次可再抓。
正常或 Ctrl-C 退出时导出已提交结果；强制终止时可能没有新的 JSON，但数据库可继续恢复。
导出使用同目录临时文件、flush/fsync 和 os.replace，失败时保留上次完整导出。

本机 POSIX 文件锁确保同一个数据库只由一个 runner 消费；不提供多主机/分布式调度。
选择 SQLite 而不是追加 JSONL：需要在课程数据与任务状态间提供事务一致性，
额外 SQLite 逻辑的收益高于维护两份可能错位的文件。

## 测试与证据

执行 `python -m unittest discover -s tests`：55 个测试方法通过，包含参数化子用例。
Ruff 0.17.1 静态检查、格式检查和 `git diff --check` 通过。
`requirements-dev.txt` 固定开发工具版本，`pyproject.toml` 固定规则，检查命令已写入 README。
离线测试涵盖 HTTP 策略、持久化、服务编排、CLI 和此前 parser/discovery 回归。

| 类别 | 关键边界 |
| --- | --- |
| HTTP | 自定义 UA/timeout、间隔、全部可重试状态、404 等永久错误、耗尽重试、零重试、日期/秒数/非法 Retry-After、最终冷却、非法配置 |
| 队列 | 规范化、重复 URL、非法批次无部分插入、空任务、分批 limit、失败后下一轮恢复、成功后跳过 |
| 一致性 | Ctrl-C 保留已提交结果；子进程在未提交 SQLite 更新后 os._exit(9)，重开验证回滚；序列化和磁盘故障不标成功 |
| 页面隔离 | timeout、未知解析错误、课程身份错误不阻止下一门；日志含 URL、阶段和堆栈 |
| 导出与生命周期 | 原子替换失败保留旧文件、清理临时文件、日志 handler 释放、同库排他锁、冲突路径拒绝 |
| 应用入口 | 自动发现一次后恢复、队列文件导入、Ctrl-C 导出、命令行策略参数和失败退出码 |

## 全量在线实测

先在空数据库执行 `python main.py --crawl --limit 5 --interval 0.25`：
自动发现 2,491 门课程，5 门成功保存、2,486 门待处理。
随后执行 `python main.py --crawl --interval 0.25`：跳过之前 5 门，处理剩余 2,486 门。
最终累计 **2,491 门成功、0 门失败、0 门待处理**。
本次验证显式配置 0.25 秒串行开始间隔；产品默认仍为 1 秒。

对最终 `data/utm_courses.json` 检查：课程 URL 集合与 Phase 2 队列完全相等，
code/URL 均唯一，每条均满足 12 个字段契约及字符串/None 值类型，code/name/campus 有效。
SQLite `PRAGMA integrity_check` 返回 ok。
再次恢复时把所有 HTTP Session.get 替换为立即报错的测试探针，仍正常完成：
本轮 processed=0，累计 succeeded=2491，证明完成检查点不会发起重复网络请求。

实际网络运行没有出现失败；重试、失败隔离和崩溃恢复由离线注入故障及子进程崩溃测试验证。
