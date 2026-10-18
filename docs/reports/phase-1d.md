# Phase 1D：UTM 单课程 Parser 完成报告

日期：2026-10-18。角色：Parser 实现、测试与文档维护。范围：单课程 HTML 到结构化数据。

## 问题与结果

原实现只读取第一个字段值，仅返回四个字段，对错误标题可能产生 IndexError。
本次实现通用 `get_labeled_field()`，输出约定的全部 12 个字段，单独处理 Description，
并用五门真实课程验证。生产代码不包含针对 CSC148 或其他个别课程的分支。

字段名匹配忽略大小写、空白和末尾冒号，但必须完整匹配，避免将推荐先修条件误读为先修条件。
字段只读取所属容器，避免缺失值时读取下一字段。多值保持顺序，以分号连接；缺失和空值为 None。
Description 通过 UTM 的语义 CSS 类定位，不依赖可见 label；保留独立扩展入口。
标题缺失、格式错误或非 UTM 课程代码明确抛出 ValueError。

## 设计与权衡

采用小函数组合与集中字段映射：通用 DOM 提取不依赖网络，课程 adapter 负责 UTM 语义，
CLI 负责执行顺序。这落实单一职责，并让新增 label 字段通过映射扩展。
网络请求与解析的调用分离；本阶段请求函数仍放在 UTM 模块，暂不引入 transport 接口。
若后续需要重试、限流、多个校区或注入 session，再提取独立 HTTP client。

UTM 域名、字段标签和 CSS 类是明确的站点协议配置，不是课程特例；完全消除这些值会让解析失去依据。
目前不引入工厂、继承层次或依赖注入容器：只有一种站点时，维护成本超过收益。
多值用字符串满足当前契约，代价是不能作为无损列表重新拆分；未来如有筛选需求，应在独立版本的模型中改用列表。
None 表示页面未提供可用值，不据此推断网站政策上“绝对没有要求”。

## 验证证据

执行 `python -m unittest discover -s tests -v`：9 个测试方法通过，包含参数化子用例。

| 范围 | 覆盖 |
| --- | --- |
| 字段边界 | 单值、多值、链接、嵌套标签、Unicode 空白、空 item、缺 item、缺 label、完整匹配、嵌套字段隔离、空查询 |
| 课程契约 | 五门课程的 code、name、campus、prerequisites、hours、URL 和全部 12 个键；必要内容非空 |
| Description | 无 label、多段落、缺失 |
| 输入错误 | 缺标题、标题无分隔符、名称为空、非法代码、URL 路径注入 |
| HTTP | URL、timeout、raise_for_status、HTTP 错误、超时传播 |

官方样本：CSC148H5、CSC108H5、CSC207H5、MAT102H5、ANT102H5。
ANT102 没有 prerequisite 字段；CSC108 有高中数学要求，不能当作无先修课程。
CSC108 的四种 delivery mode 全部保留；CSC207 的成绩、校区限制与 enrolment limits 保留。
另对五门官方完整网页执行在线集成检查，全部输出与离线样本解析相等。
`git diff --check` 通过。上述验证不是对所有未来 DOM 变化的保证。

## 局限与后续架构

网站结构变化可能需要更新 adapter 和 fixtures。当前文本规范化在链接边界插入空格，
可能出现括号前后的多余空格；不改变条件文字，但不是浏览器排版的精确复制。
本机 Python 3.9 链接 LibreSSL，urllib3 提示兼容性警告；本次网络测试通过，建议后续迁移到带现代 OpenSSL 的 Python。

当前代码属于数据采集/外部数据适配层。前端展示、HTTP API、数据库写入和业务规则不应写入 DOM 提取器。
建议后续按需求逐步增加：应用服务编排抓取和存储，领域模型表达课程结构，API 层提供查询，前端负责展示。
当前尚未建设框架或前端；这个阶段的独立解析入口可以被后续应用服务直接调用。
先修关系图尤其应另设业务解析器，避免把自然语言条件的推断混入 HTML 字段读取。

## 报告依据

本报告参考 Google 官方工程实践，组织为问题、变更原因、设计、测试证据与局限，
是本项目的完成报告格式，并非 Google 官方报告模板或认证。

- [Writing good CL descriptions](https://google.github.io/eng-practices/review/developer/cl-descriptions.html)：解释修改内容和原因，记录重要权衡。
- [What to look for in a code review](https://google.github.io/eng-practices/review/reviewer/looking-for.html)：检查设计、复杂度、边界测试与文档。
