# T01：现有项目基线检查

检查日期：2026-10-18（America/Toronto）。
被检查代码：`5925a8082b00754d15ee2e57e0f37358b03ff39c`。
本次交付为基线清单和运行文档整理，未修改业务代码、现有采集结果或本地源检查点。
具体命令、退出码及结构化统计保存于 `t01-baseline-evidence.json`；临时目录路径统一替换为 `$BASELINE_TMP`。

## 1. 检查清单

| 状态 | 检查项 | 结果 |
| --- | --- | --- |
| 通过 | 完整测试 | 145 项，全部通过；包括回环 HTTP 服务和子进程中断测试 |
| 通过 | Ruff 静态检查 | `ruff check .` 无错误 |
| 通过 | Ruff 格式检查 | 44 个 Python 文件符合当前格式 |
| 通过 | 依赖一致性 | `pip check` 无冲突，运行和开发依赖已固定 |
| 通过 | TLS 环境 | Python 3.12.14，OpenSSL 3.5.8；当前环境没有 LibreSSL 导入警告 |
| 通过 | SQLite 环境 | SQLite 3.53.1；当前构建启用 FTS5，项目尚未使用该搜索能力 |
| 通过 | 远端 CI | 被检查提交的 8 组系统/Python 组合全部成功 |
| 通过 | 命令入口 | 主 CLI、历史维护、临时文件清理的帮助入口均正常 |
| 通过 | 真实单课程请求 | CSC148H5 官方页面请求及 JSON 文件输出正常 |
| 通过 | 分批与恢复 | 两门课程队列先处理 1 门，不发布正式输出；恢复后完成 2 门并发布 |
| 通过 | 刷新与继续 | 刷新限制 1 门产生部分进度；resume 完成剩余 1 门 |
| 通过 | 离线重解析 | 阻断 Requests 请求入口后，2 门历史 HTML 重新解析成功 |
| 通过 | 原始 HTML 与运行记录 | 临时采集产生 2 份去重 HTML、5 份运行元数据；13 条日志均含版本和 run ID |
| 通过 | 维护预览 | 历史压缩和临时文件清理的预览命令成功，不执行删除 |
| 通过 | 旧数据库迁移 | 独立副本由 schema 0 迁移至 1；完整性检查通过，课程结果不变 |
| 通过 | 原始数据一致性 | URL 队列、JSON 和数据库结果相互对应；历史快照哈希有效 |

远端记录：`https://github.com/wangyeyu1129/course-crawler/actions/runs/39166114011`。
CI 矩阵：Ubuntu/macOS × Python 3.11、3.12、3.13、3.14。
检查包含固定依赖安装、pip check、TLS 导入、Ruff 检查与格式检查、完整测试。

## 2. 运行命令核验

以下 `main.py` 命令统一由 `.venv/bin/python` 执行；真实采集和迁移验证均将
`--database`、`--output`、`--log-file` 指向隔离临时目录。

| 操作 | 命令形态 | 验证方式 |
| --- | --- | --- |
| 单课程 | `main.py CSC148H5 --output PATH` | 真实官方页面，退出码 0 |
| 目录 URL 输出 | `main.py --discover --output PATH` | 现有 CLI、分页、完整性和失败保护测试 |
| 发现新增任务 | `main.py --crawl --operation discover` | 现有编排测试确认仅发现，不抓取详情 |
| 分批采集 | `main.py --crawl --url-file PATH --limit 1` | 真实两门队列，processed=1，pending=1，published=false |
| 恢复 | `main.py --crawl` | processed=1，pending=0，succeeded=2，published=true |
| 显式刷新 | `main.py --crawl --operation refresh --limit 1` | processed=1，pending=1，published=false |
| 刷新后恢复 | `main.py --crawl` | processed=1，pending=0，succeeded=2，published=true |
| 离线解析 | `main.py --crawl --operation reparse` | 阻断 HTTP 后 processed=2，succeeded=2 |
| 重试失败 | `main.py --crawl --retry-failed` | 现有调度测试确认 pending 优先、单轮不重复入选 |
| 历史预览 | `-m crawler.maintenance PATH --keep 1` | 临时输出执行，退出码 0 |
| 临时文件预览 | `-m crawler.durability DIRECTORY` | 临时目录执行，退出码 0 |

现有源数据库通过 SQLite backup API 复制；副本执行默认 resume，processed=0，
succeeded=2491，published=true。复制前后源数据库 SHA-256 一致。
离线重解析验证中，任何 Requests 请求入口调用都会立即报错；命令仍成功完成。

## 3. 数据状态

| 项目 | 检查结果 |
| --- | --- |
| 课程 JSON | 2,491 条，全部为 UTM；2,491 个唯一课程代码和 URL |
| URL 队列 | 2,491 条，无重复、非法或非规范 URL |
| 队列与结果差集 | 双向均为 0 |
| 课程契约校验 | 2,491 条全部符合现有 12 字段契约 |
| 检查点统计 | succeeded=2491，pending=0，failed=0；没有非空 error |
| 检查点完整性 | 只读 `PRAGMA quick_check` 返回 ok |
| 检查点与 JSON | 按 URL 比较后，所有课程字段一致 |
| 本地 schema | 源库 user_version=0、application_id=0，为版本管理引入前的数据库 |
| 快照 | 1 份未压缩历史 JSON，与当前输出内容一致且文件名 SHA-256 有效 |
| 输出大小 | 2,322,837 字节 |
| 输出 SHA-256 | `e504a6b369968455c98a20f7d0698aecdaf231e50d122320b2d163c52f1e3ec6` |
| 旧数据的 HTML / run metadata | 现有 data/raw、data/runs 不存在；本次临时采集已验证新功能 |
| 旧日志 | 已有日志来自早期运行，不能当作新版 JSON 日志验证依据 |

可选字段为空的记录数：

| 字段 | null 数量 |
| --- | ---: |
| prerequisites | 450 |
| exclusions | 1,343 |
| enrolment_limits | 2,271 |
| distribution_requirement | 33 |
| instruction_hours | 255 |

这些数值是本次观测结果，不是程序固定总数或未来验收阈值。
null 符合现有契约，但当前数据不能区分“来源未提供”和“合法缺失”等原因。
当前 JSON 不含独立 corequisite、学分和学年；先修/排除关系仍为原文。
旧数据没有运行时间和 parser 版本的配套记录，不能由文件修改时间推断其业务时效性。

## 4. 模块职责与依赖检查

| 位置 | 当前职责 | 已确认的边界或耦合 |
| --- | --- | --- |
| fields | DOM 字段提取 | 不负责网络、存储或任务调度 |
| utm_course | UTM 页面适配 | parse 与 fetch 为独立函数；同一模块仍同时导入 HTTP 与解析依赖 |
| urls / models | URL 规则与课程契约 | URL 已集中；模型仍依赖 UTM URL 与固定校区校验 |
| discovery | 分页发现 | 可注入 fetcher，仍有 UTM 专用分页结构和地址规则 |
| http / http_cache | 请求策略与条件缓存 | session、clock、sleep 可注入；策略由 HttpPolicy 统一承载 |
| pipeline | 抓取解析与持久化编排 | fetcher/parser 可注入；存储类型依赖具体 CrawlStore，默认 parser 为 UTM |
| storage / migrations | 队列、结果、导出及 schema 迁移 | 不再依赖 discovery；存储仍依赖 UTM URL/课程校验 |
| runner | 应用资源组装与生命周期 | 组装具体存储、HTTP、归档与记录，是当前组合入口 |
| raw_data / run_records | 原始输入与运行元数据 | raw_data 为时间函数导入 run_records，后者又依赖 storage |
| maintenance | 历史维护 | 为复用文件锁而导入 runner，存在可进一步收窄的依赖边界 |
| cli_validation / main | 参数校验与 CLI 入口 | 模式冲突在 I/O 前拦截，参数表仍通过 argparse 私有 actions 列表读取默认值 |

静态内部导入图未发现循环。测试通过证明当前受测行为稳定，不等同于完整满足 SOLID。
现有可复用机制包括函数注入、HttpPolicy 配置对象、资源上下文管理和独立页面适配器。
当前尚无跨校区接口、存储 Protocol、独立 domain 层或适配器注册机制。
UTM 官方地址、标签及代码规则属于来源规则；当前集中在适配模块中，但尚未支持校区替换。
并发上限等运行约束在多处验证；它们是现有实现事实，不应当作后续领域规则写入数据。

## 5. 本次文档修正与验证边界

README 已统一为当前行为，删除相互矛盾的 Python 3.9、部分结果覆盖正式文件、
无 HTML、无运行元数据、无持久化恢复等早期状态说明。
当前文档说明操作区别、正式/进度输出、旧数据缺少归档、错误退出码及维护预览。
历史报告保持历史记录属性。

本次没有重新全量发现目录或重新下载全部 2,491 门课程。目录行为通过现有测试验证，
真实网络验证限于 CSC148H5 与 ANT102H5；数据一致性不构成源站当前完整性或所有课程新鲜度证明。
本次没有执行压缩删除、孤立文件删除或正式数据刷新，也未修改用户的 .idea 配置。
没有新增业务实现或为文档编写镜像测试；执行的是完整既有测试和隔离命令验证。
