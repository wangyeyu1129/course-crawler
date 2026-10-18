# Phase 2：UTM 全课程发现完成报告

日期：2026-10-18。角色：课程发现模块实现、验证、文档维护。

## 目标与实现

从手动输入课程代码升级为访问官方课程搜索目录、遍历分页、生成去重后的待抓取队列。
入口为 `python main.py --discover --output data/utm_course_urls.json`。
原单课程解析命令继续可用；发现过程不下载每门课程详情。

官方搜索页的结果标题没有 `<a href="/course/...">`，而正文链接包含先修与排除课程。
因此仅扫描链接既会漏掉目录课程，也可能引入不属于当前结果的课程引用。
本次以 `.view-content` 内的课程标题作为目录条目，从标题代码调用现有 `course_url()`
生成标准 URL。另提供独立 `extract_course_urls()` 支持真正的链接提取。
这一区分由真实 DOM 决定，生产逻辑没有单门课程特例。

## 组件职责与权衡

- URL 规范化：限制官方 HTTPS 域名与合法 UTM 课程路径，消除大小写、末尾斜线、query、fragment 带来的重复。
- 页面解析：纯函数读取结果标题和官方 next 链接，不访问网络。
- 发现编排：注入 fetcher，复用 requests Session，串行分页、全局去重、生成 deque。
- CLI：选择单课程或全目录模式，成功后写 JSON 文件，失败时保留原有文件。

队列保持首次出现顺序，`deque.popleft()` 支持下一阶段 FIFO 消费；JSON 便于检查和后续读取。
不增加消息队列框架或数据库：当前没有并发消费、任务确认与恢复的需求，引入这些设施成本大于收益。
未来若要断点续跑，应另建任务存储层，保存 pending/succeeded/failed 状态，避免把调度规则混进 DOM parser。

分页数量来自页面 next 链接，不硬编码目录总页数。1000 页仅为可配置安全上限：
触达上限时抛错，而非悄悄返回截断队列。已访问页集合阻止循环。
每次请求之间默认暂停 1 秒；测试可设为 0。HTTP、解析失败均不返回部分成功结果。

## 验证

`python -m unittest discover -s tests -v`：22 个测试方法通过，包含参数化子用例。
`git diff --check` 通过。

| 范围 | 验证内容 |
| --- | --- |
| URL | 相对/绝对路径、大小写、尾斜线、query/fragment 去重；拒绝外域、相似域、其他校区、非法路径、非 HTTPS、非法 URL |
| 搜索解析 | 官方首个搜索页 30 个标题、next 链接、纯文本标题生成 URL、不将正文课程引用当成结果 |
| 分页与队列 | 多页、全局重复、顺序、末页停止、循环、页数上限、非法或多个 next 链接 |
| 失败行为 | 页面结构缺失、错误标题、后续页网络失败、HTTP 状态错误、无部分结果 |
| HTTP | Session 请求参数、请求间等待、状态检查 |
| CLI | 标准输出、JSON 文件、发现失败时保留已有文件 |
| 回归 | Phase 1D 原有单课程测试全部通过 |

在线全量验证：按 next 链接依次遍历 page=0 至 page=83，共 84 个搜索页，
成功生成 2,491 个唯一课程 URL，保存到 `data/utm_course_urls.json`。
额外检查 URL 唯一性和五门 Phase 1D 样本均在队列中。该文件是本次验证快照，
重新执行发现命令即可刷新；2,491 不是程序中的期望总数或硬编码上限。

官方首页和末页的标题与分页 DOM 分别保存于 `tests/fixtures/search-first.html`
和 `tests/fixtures/search-last.html`，覆盖真实末页终止行为。
来源：https://utm.calendar.utoronto.ca/course-search，抓取日期：2026-10-18。
