# 跨媒体内容传播观察

这个 Python 项目整理与“跨媒体内容传播观察”有关的来源观察，并生成供研究人员复核的统计摘要。

除按周期汇总观测值外，项目还维护故事谱系（story lineage）：区分可确认的转载关系与独立编辑增补，
在只追加事件账本上支持更正、删稿与历史报告复现。计数口径见 [docs/analysis-notes.md](docs/analysis-notes.md)，
示例事件流见 `fixtures/lineage-events.json`。

## 开发

需要 Python 3.11 或更高版本。安装开发依赖：`python -m pip install -e .`。

运行测试：`python -m unittest discover -s tests -v`。

处理不可信夹具时可用隔离模式（`tests/` 已是包，支持从任意目录运行）：

`python -I -m unittest discover -s "$PWD/tests" -t "$PWD"`。

构建发行包：`python -m build`。
