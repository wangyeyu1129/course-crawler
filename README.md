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
