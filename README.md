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
