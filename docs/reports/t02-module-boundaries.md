# T02：建立目录边界并迁移共享模型

## 问题与目标

原课程模型直接依赖 UTM URL 规则，采集流程依赖具体 SQLite 类和默认 UTM parser。
这些依赖使后续校区、API 或存储实现难以独立接入。维护命令还通过 runner 获取文件锁，
原始 HTML 模块通过运行元数据模块获取时间函数，扩大了不必要的导入链。

本任务建立可执行的依赖规则、迁移共享模型与采集用例，并保持既有 CLI、UTM 校验和结果格式。

## 实现

| 改动 | 原因与结果 |
| --- | --- |
| 新增 domain/course.py | 共享 Course 与形状校验不再导入 crawler、网络或数据库模块 |
| 新增 crawler/validation.py | UTM 身份约束继续严格执行，通用契约不嵌入校区判断 |
| 新增 application/models.py | 共享队列和采集结果对象集中定义，原路径直接重导出 |
| 新增 application/ports.py | 用窄 Protocol 描述存储能力，parser/fetcher/validator 使用可注入函数 |
| 新增 application/crawl.py | 用例只依赖 domain 和接口；线程、事务、异常隔离行为沿用原逻辑 |
| 保留 crawler/pipeline.py | 作为 UTM facade 注入来源规则，旧函数签名与日志名称保持兼容 |
| 抽离 infrastructure | 时间和锁独立复用，维护和 raw 模块不再为通用工具导入编排模块 |
| 集中并发约束 | CLI、runner 和用例使用同一命名上限与校验函数 |
| 新增 api/frontend 目录 | 只预留边界，未安装 Web 框架或创建虚假服务 |
| 增加依赖契约与 CI 步骤 | 禁止反向依赖及模块循环，开发依赖及其传递依赖固定版本 |
| 更新 parser 指纹 | 迁移后的共享模型和 UTM 校验代码变化仍能反映在运行元数据中 |

采用端口与适配器的边界设计、结构化接口和函数注入；旧路径使用兼容 facade 或重导出。
不引入复杂继承体系、全局服务定位器或依赖注入容器。

## 兼容性

- `python main.py` 的命令、默认参数和退出语义不变。
- JSON 保持原有 12 字段，导出仍按 URL 排序。
- 旧 crawler.models 导入继续执行 UTM 严格校验，不会因共享模型支持其他校区标签而放宽现有采集器。
- 旧 CrawlSummary、PageOutcome、WorkQueue、文件锁和时间函数路径继续可导入。
- 原有 crawler.pipeline 日志名称保持，仍受 run ID 日志处理器管理。
- 未修改现有 data 文件、数据库 schema、任务状态或历史快照。

## 验证

| 检查 | 结果 |
| --- | --- |
| 原有回归 | 145 项全部通过 |
| 新增验证 | 12 项通过，总计 157 项 |
| 架构契约 | 6 条全部通过，包含层方向和模块无环 |
| 违规注入 | 反向导入、间接相对导入、网络依赖、系统层反向引用、API 越界及循环均被拒绝 |
| 存储可替换性 | 内存存储无需继承 CrawlStore，即可运行采集用例 |
| 来源可替换性 | 注入另一校区的记录和 parser，应用层无需修改；UTM facade 仍拒绝错误校区 |
| 失败处理 | 来源校验失败隔离，存储异常传播，队列上下文释放，空队列不调用 parser |
| CLI 帮助 | 与改动前 main.py 的 --help 逐字节一致 |
| CLI 子进程 | 禁止真实 HTTP 的情况下完成分批离线解析、完整发布和恢复；输出字段及旧日志名称一致 |
| 内层导入 | 新进程导入 domain/application 不加载 crawler、HTTP、SQLite、API 或系统工具 |
| 指纹回归 | 修改迁移后的模型或 UTM 校验源码会改变 parser 指纹 |
| Ruff / 依赖 | 静态检查、格式检查和 pip check 通过 |
| 已有数据 | 2,491 条 JSON 仍通过 UTM 校验，当前文件 SHA-256 未改变 |

## 限制

静态依赖检查不覆盖任意动态导入；动态导入不得用于规避规则。
共享契约仍是当前版本的 12 字段，尚未新增 corequisite、学年、学分或 Pydantic 模型。
api/frontend 仅有目录约束；三校区适配器、HTTP 服务和页面功能尚未实现。
crawler 继续承载既有采集基础设施，新的通用用例不再反向依赖这些具体实现。
