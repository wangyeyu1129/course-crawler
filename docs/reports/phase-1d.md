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
