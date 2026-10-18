# 统一课程模型与字段状态

## 数据入口

`domain.course.Course` 是严格的 Pydantic 模型：包含现有 12 个课程值字段，
加 `schema_version=1` 和 `field_states`。`CourseData` 定义旧 JSON 投影，
`CourseRecord` 只是投影字典的类型别名，不是第二套运行时校验器。

- `Course.from_legacy(mapping)`：读取旧 12 字段记录，补充可证明的状态。
- `Course.model_validate(mapping)` / `model_validate_json(text)`：读取带完整状态的新记录。
- `validate_course_model(value, expected_url=None)`：接受新旧记录，校验任务身份并拒绝字段解析失败。
- `Course.to_legacy_dict()`：显式投影为 12 字段，供兼容接口使用，不包含状态。
- `validate_course()`：旧字典入口，内部执行 Pydantic 和成功校验，再返回投影副本。

字段不允许额外键，不把数字、布尔值或 bytes 转成课程文本。所有旧字段键仍然必填；
`Optional[str]` 表示值可以为 null，不表示可以省略键。必填课程文本不能全是空白。
可选字段的空字符串或纯空白标准化为 null，同时保留 empty 状态。

模型和嵌套状态均 frozen；新旧输入会在应用和持久化边界重新验证。
Pydantic 的 `model_construct()`、`model_copy(update=...)` 本身不提供数据校验，不能直接作为可信输入。

## 状态语义

field_states 覆盖 Description 和七个可选内容字段。
code/name/campus/url 属于不可缺失的身份字段，由模型和校区适配器验证，错误时拒绝课程。

| status | 值 | 含义 |
| --- | --- | --- |
| present | 非空文本 | 找到可读取内容；例如明确写着 None 的文字仍是来源文本 |
| empty | null | 找到了字段及其内容节点，节点没有非空文本；或旧输入明确是空字符串 |
| not_provided | null | 按该来源的标签和语义容器规则，没有发现字段 |
| parse_failed | null | 有字段证据但结构异常，必须附非空 error |
| unknown | null | 旧记录只有 null，不能知道当时是缺失、空值还是解析问题 |

非 parse_failed 状态禁止携带 error；present 与 null、empty 与非空文本等组合会被模型拒绝。
内容状态必须完整，与课程值一致，不能只提交部分 field_states。

not_provided 是当前页面和适配规则的观察结论，不是“没有选课要求”的业务判断。
如果字段 DOM 和所有已知标识同时变化，单凭缺失证据无法证明字段不存在。
后续资格判断必须结合关系解析和数据质量策略，不能将任意 null 当作无先修要求。

旧数据中的 450 条 prerequisite=null 在适配时仍为 unknown，不改写为 not_provided。
要获得可靠来源状态，需要用已有原始 HTML 重解析，或重新采集；旧库不会在读取时被批量重写。

## DOM 观察与失败处理

`FieldIndex.observe()` 返回 value 与 FieldState；`get()`、`get_labeled_field()` 保留旧值接口。
观察先检查结构，再读文本，不通过结果为 None 来反推缺失原因。

以下情况记为 parse_failed：缺少属于字段自身的内容节点、重复字段容器、
标签没有字段所有者、已知语义容器与标签不匹配、内容落在预期值节点之外。
嵌套其他字段不会成为当前字段的值或内容节点。标签和 CSS 选择器集中于 UTM 适配配置。

`parse_course_document(html)` 返回带状态的 Course，便于检查可选字段的解析问题。
缺失、空白或损坏的必需 Description 会立即抛出带状态的 CourseFieldError。
`parse_course_page(html)` 是严格的旧 JSON 接口；可选字段 parse_failed 也会报错。

采集用例接收带状态模型，来源校验器拒绝解析失败的模型。
CourseFieldError 在 courses.error 中保存为 JSON 文本，包含 stage、type、field_states；
其他错误仍使用既有错误文本格式。详情页失败不会阻塞后续任务。
刷新失败时保留上次成功的 result；该旧结果的状态不是本次失败页的状态，须同时查看 status/error。

## 保存与兼容

新成功结果在 SQLite 的 courses.result 中保存完整模型 JSON：

```json
{
  "schema_version": 1,
  "field_states": {
    "prerequisites": {"status": "empty", "error": null},
    "exclusions": {"status": "not_provided", "error": null}
  }
}
```

上面仅为元数据片段；实际记录还包含全部 12 个课程值和全部八个内容字段状态。
不需要新增表列，SQLite schema 版本保持原机制；模型 schema_version 与数据库 user_version 含义不同。

JSON 课程数组和单课程 CLI 输出继续使用 12 字段投影，以保持既有使用方兼容。
投影不能用于恢复来源状态；状态权威记录是完整模型及采集检查点中的 result/error。
导出逐条校验新旧记录后写投影，保持流式内存边界。
导出缓存格式版本提升至 2，防止旧缓存跳过新的投影校验。
正式输出仍只在队列全部成功时发布；进度输出可能包含刷新前的旧有效结果。

## 分层规则

domain 负责类型、状态与值的一致性，不包含校区域名或 DOM 规则。
crawler 负责来源观察、UTM 身份和 DOM 到状态的转换。
application 通过既有 validator 端口接收完整 Course，并隔离页面失败。
storage 负责完整 JSON 持久化和兼容投影，不推测缺失原因。

应用层自定义 validator 现在应返回 Pydantic Course；使用 validate_course_model 或相应来源校验器。
Course 已从 TypedDict 升级为模型，直接使用旧构造习惯的代码应改用 from_legacy。
既有 parse_course_page、validate_course、CLI 参数和 12 字段输出保持兼容。
