# 目录边界与依赖规则

本规则描述当前已实现的边界；依赖契约由 `.importlinter` 和 CI 执行。
项目继续使用 Python 轻量模块，不增加运行服务或依赖注入容器。

## 目录职责

| 目录 / 文件 | 职责 | 允许的内部依赖 |
| --- | --- | --- |
| domain/ | 课程共享契约、纯数据校验；后续放业务规则 | domain 内部 |
| application/ | 采集用例、结果对象、窄接口、执行约束 | application、domain |
| infrastructure/ | 通用系统工具，目前为 UTC 时间和 POSIX 文件锁 | infrastructure 内部 |
| crawler/ | UTM 页面规则、HTTP、SQLite、HTML 归档、CLI 兼容 facade、采集资源组装 | crawler、application、domain、infrastructure |
| api/ | HTTP 展示层预留目录，当前只有包标记 | api、application、domain |
| frontend/ | 浏览器 UI 预留目录，当前没有工程依赖 | 通过 HTTP 契约连接 API |
| main.py | CLI 组合入口：解析参数、选择适配器、执行操作 | application、crawler、infrastructure、domain |
| tests/ | 契约、行为、架构违规和 CLI 回归验证 | 可以引用被测各层 |

业务内层不能反向导入 CLI、crawler、API 或系统适配工具。
`requests`、`bs4`、`fastapi` 不得出现在 domain/application/infrastructure 的依赖链上。
API 不直接导入 crawler 或 infrastructure；具体资源组装由展示入口之外的组合入口负责。
后续 API 启动组装代码应放在独立顶层入口，而非让端点直接创建采集器或数据库。

模块间禁止导入循环。`TYPE_CHECKING` 中的导入仍算依赖，不作为绕过边界的手段。
检查覆盖静态直接、间接和相对导入；动态导入不在静态检查保证范围内，不用于规避契约。
导入 API、domain 或 application 不应启动请求、打开数据库或启动服务。

crawler 暂时仍承载来源适配和采集基础设施，以保持现有接口稳定。
将共享对象移到内层，不要求为了目录统一而一次性搬动所有现有模块。

## 共享契约与来源规则

`domain.course.Course` 是唯一的课程类型定义，保持当前 12 字段和 JSON 兼容。
`domain.course.validate_course()` 检查字段形状、文本类型、必填值、待处理 URL 一致性，返回副本。
该函数不判断 UTM 域名、代码后缀或校区；来源身份由适配器负责。
当前形状仍是 TypedDict 加运行时校验；本任务没有引入 Pydantic 或扩展课程字段。

`crawler.validation.validate_utm_course()` 在共享校验之后执行 UTM 校区、课程代码、URL 的严格身份校验。
UTM 域名、标签和代码模式属于适配器规则，继续集中于 crawler 的对应模块。
这次分离不等于实现其他校区的发现、抓取或解析。

`application.models` 定义不可变的 WorkQueue、CrawlSummary、PageOutcome。
它们不承担 SQLite 或 HTML 解析职责。

## 用例与接口

`application.crawl.crawl_courses()` 显式接收：

- CourseStore：队列上下文、批量事务上下文、保存成功、保存失败四个能力。
- PageFetcher：URL 到 HTML 的函数。
- CourseParser：HTML 到待校验对象的函数，必须由调用方指定。
- CourseValidator：对象及 expected_url 到已校验 Course 的函数，默认共享校验。
- Logger：可选日志对象；适配层传入旧 logger，以保持运行日志兼容。

CourseStore 使用 Protocol 描述结构，不要求适配器继承某个基类。
CrawlStore 以其现有方法满足端口；无需创建仅转发调用的数据库包装类。
队列和事务上下文仍由存储实现控制，存储异常必须向上传播。
单页抓取、解析或校验异常由用例隔离，不影响下一门课程。

执行并发上限集中为 `application.policies.MAX_CRAWL_WORKERS`，CLI、runner 和用例复用校验。
配置值通过现有参数传入，不在页面流程中复制运行策略。

## 兼容入口

| 保留入口 | 当前实现 |
| --- | --- |
| crawler.models.Course | 直接重导出 domain.course.Course，为同一类型对象 |
| crawler.models.validate_course | 重导出 UTM 严格校验，不能替换成宽松的共享校验 |
| crawler.pipeline.crawl_courses | UTM facade，注入原 parser、UTM 校验器与旧日志对象 |
| crawler.pipeline.CrawlSummary / PageOutcome | 重导出 application.models 类型 |
| crawler.storage.WorkQueue | 重导出 application.models.WorkQueue |
| crawler.runner.crawl_lock | 重导出 infrastructure.locking.crawl_lock |
| crawler.run_records.utc_now | 重导出 infrastructure.clock.utc_now |
| main.py 参数及默认值 | 继续支持现有命令，无需迁移用户操作 |

新代码应使用定义所在模块；保留入口只负责兼容，不复制第二份实现。
维护命令直接使用 infrastructure.locking；原始 HTML 归档直接使用 infrastructure.clock。
parser 指纹包含迁移后的 domain/course.py 和 crawler/validation.py，保证校验变化可追踪。

## 如何检查

```sh
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/lint-imports --no-cache
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/python -m unittest discover -s tests
```

六条依赖契约在 push/PR CI 中单独执行。测试会复制源码到临时目录，注入反向依赖、
相对导入越界、网络库依赖和循环，验证检查工具确实返回失败。
新模块按照职责选目录；调整依赖规则时，必须同时更新本说明和违规回归测试。
