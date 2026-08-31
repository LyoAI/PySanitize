<div align="center">

# 🛡️ PySanitize

**多格式文档脱敏工具 · 本地解析 · 规则 + LLM 定位 · 按类别图片打码**

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![Version](https://img.shields.io/badge/version-0.2.0-4A90D9)
![Tests](https://img.shields.io/badge/tests-78%20passed-brightgreen)
![Parse](https://img.shields.io/badge/parse-local%20MinerU-2E7D32)
![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)
![License](https://img.shields.io/badge/license-MIT-blue)

[English](./README.md) | 中文

</div>

对 **PDF / DOCX / Excel / 扫描件** 做敏感信息脱敏，全程**纯本地**解析（依托 [MinerU](https://github.com/opendatalab/MinerU)），无云端调用。

## ✨ 特性

| | |
|---|---|
| 🧩 **多格式输入** | PDF / 图片 / DOCX / PPTX / XLSX 一次解析成统一结构 |
| 🔍 **规则 + LLM 双引擎** | 正则+词典启发式（离线可用）；LLM 只做**定位**不重写，回匹配防幻觉 |
| 🖼️ **按类别图片脱敏** | `face`（人脸）/ `text`（OCR 文字）/ 任意 YOLO 类别 → 马赛克 |
| 🔌 **供应商可切换** | `--provider openai \| pingan`，内网/外网一键切换 |
| 📊 **审计友好** | 公开摘要不含原文；含原文报告 `--audit` 才生成 |
| 🛡️ **容错优先** | 缺 key / 缺可选依赖 / 模型下载失败 → 降级警告，绝不硬崩 |

## 🔄 处理流程

```text
输入(PDF/DOCX/Excel/扫描件)
  → [parser]      MinerU → ParsedDocument{text, blocks, images/}
  → [detector]    规则 + LLM 定位敏感字段 → 精确字符 offset
  → [masker]      按字段类型掩码（138****5678 / **** / 保前后N位）
  → [image]       按类别检测（人脸/OCR文字/YOLO物体）→ PIL 马赛克
  → 输出  sanitized.md + images_masked/ + audit.json
```

## 📦 安装

需要 Python ≥ 3.12，推荐 [uv](https://docs.astral.sh/uv/)：

```bash
git clone https://github.com/LyoAI/PySanitize.git && cd PySanitize
uv sync                    # 安装全部依赖（含 mineru[pipeline]，体积较大）
```

`config/` 与 `.env` **被 git 忽略且非必需**——工具内置默认配置（字段规格、阶段开关），全新 clone 与 CI 开箱即用。要自定义或放入真实 key，在本地创建：

- `config/fields.yaml`、`config/pipeline.yaml`、`config/llm/<model>.yaml` —— 覆盖内置默认值（默认字段规格见 `pysanitize/detector/specs.py`）
- `.env` —— `config/llm/*.yaml` 里 `${VAR}` 占位符对应的 API key：

```
DEEPSEEK_API_KEY=...      # deepseek-v4-flash（默认模型）
DASHSCOPE_API_KEY=...     # qwen3-max / qwen3.6-27b
MINERU_BACKEND=pipeline   # pipeline（CPU）| vlm-engine / hybrid-engine（GPU）
MODELS_DIR=~/Models       # 本地模型下载目录（YuNet ONNX 等）
LLM_TIMEOUT_S=180         # 单次 LLM 请求超时（秒）
```

可选特性按需解锁：

```bash
uv sync --extra image-yolo      # YOLO 通用目标检测（ultralytics）
uv sync --extra image-ocr       # OCR 文字区域检测（paddleocr，体积较大）
uv sync --extra tui             # 交互式 TUI 前端（textual）
```

## 🚀 快速开始

```bash
# 1) 纯本地规则检测（离线可用，不调用任何 LLM）
uv run pysanitize 样例.pdf

# 2) 混合模式：规则 + LLM 定位（默认 openai/deepseek-v4-flash）
uv run pysanitize 样例.pdf --detector hybrid

#    指定 LLM 供应商与模型（与 finsearch-bench 一致）
uv run pysanitize 样例.pdf --detector hybrid --provider pingan --model qwen3.6-27b
uv run pysanitize 样例.pdf --detector llm     --provider openai  --model qwen3-max

# 3) 图片打码：默认不检测任何图片，必须显式指定目标
uv run pysanitize 样例.pdf --mask-images --image-classes face   # 人脸
uv run pysanitize 样例.pdf --mask-images --image-classes text   # 印刷文字（公司名等，需 --extra image-ocr）

# 4) 限定字段 + 写出含原文的敏感审计报告（本地审计用，勿外发）
uv run pysanitize 样例.xlsx --fields person_name,phone --audit
```

每次运行产出一个任务目录（默认 `output/<文档名>/`）：

```
output/<文档名>/
├── sanitized.md            # 脱敏后的 Markdown（图片链接指向 images_masked/）
├── images_masked/          # 每张抽取图——打码副本；未打码时即原图
└── audit.json              # 公开审计摘要：字段命中数 + 掩码后文本，不含敏感原文
```

`--audit` 时额外生成 `sensitive_report.json`（含字段**原文**与字符偏移，用于本地人工复核，**不要外发**）。命令行未指定的参数全部回退到 `config/pipeline.yaml`；指定后命令行优先。

## 🖥️ 交互式 TUI

```bash
uv sync --extra tui        # 一次性安装 Textual
uv run pysanitize --launch tui
```

四页签终端界面（基于 Textual）：**① 字段** — 从 `config/fields.yaml` 勾选要检测的敏感字段类型；**② 选项** — 输入文件、检测模式、LLM 端点、图片打码；**③ 运行** — 自由输入自定义要求（会追加到 LLM 提示词），点击运行并实时查看日志；**④ 结果** — 各字段命中数与输出路径。退出：点击右上角 **✕ Quit** 按钮或按 `ctrl+c`（默认的 `ctrl+q` 会被部分终端吞掉，mac 的 `cmd+q` 则属于系统）。命令行仍是主入口，TUI 只是同一流水线（`pysanitize.core.run_sanitizer`）之上的便捷层。

## 🐍 Python API

```python
from pysanitize.pipeline import sanitize_document

result = sanitize_document(
    "合同.pdf",
    detector="hybrid",          # rules | llm | hybrid
    llm_model="qwen3.6-27b",    # config/llm/<model>.yaml 文件名
    llm_provider="pingan",      # 该 yaml 里的 provider 段：openai | pingan
    fields=["phone", "company_name", "person_name"],
    mask_images=True,
    image_classes=["face"],     # 图片打码目标：face | text | <YOLO 类别>；空=不处理
    audit=False,
)
print(result.sanitized_md)     # Path
print(result.detections)       # 每条含 field_type / start / end / masked_value
```

## 🔍 检测模式

| 模式 | 说明 |
|---|---|
| `rules` | 纯本地正则 + 词典启发式（身份证/信用代码校验位、百家姓、公司后缀词典），**离线可用** |
| `llm` | LLM 逐段**定位**敏感字段：返回 `{"field_type", "value"}`，value 必须为原文逐字子串，再回匹配得到精确 offset |
| `hybrid` | 两者都跑，规则结果在重叠时优先 |

LLM 只做定位不重写，并有**幻觉硬防线**：value 在原文回匹配不到即丢弃（宁漏勿错）；`temperature=0` + `response_format=json_object`。

分块是 block 感知的，并随每篇文档的标题结构自适应：`text.chunking.title_level_limit`（默认 `auto`，也可固定为某级，0=最高）决定哪个级别的标题开启新一轮 LLM 调用——大章节切分调用、小标题与正文累积；表格永远独立成块，且每个 chunk 都是原文的精确切片。`text.chunking.chunk_size` 设置单次调用的目标字符数。

## 📋 默认敏感字段（config/fields.yaml）

| field_type | 说明 | 默认掩码 |
|---|---|---|
| `phone` | 手机号 `1[3-9]xxxxxxxxx` | `138****5678`（保前3后4） |
| `id_card` | 18 位身份证，GB 11643 校验位（可关） | `110105********1239`（保前6后4） |
| `credit_code` | 18 位统一社会信用代码，GB 32100 校验 | `************000N`（保后4） |
| `email` | 邮箱 | `****@***` |
| `stock_code` | A 股代码 `60/68/00/30` 开头 | `******` |
| `bank_account` | 16-19 位数字（低置信，**默认关闭**） | `6222***********5678`（保前4后4） |
| `person_name` | 百家姓 + 上下文启发式 | `***` |
| `company_name` | 公司后缀词典 + 边界剪枝 | `****` |

字段**可增删、可改掩码**（`config/fields.yaml` 加一行即新字段），`--fields a,b` 只检测指定字段。

## 🖼️ 图片脱敏（按类别）

图片里不只有人脸 —— 可能是公司名、印章、文字截图。按 `image.classes` / `--image-classes` 列出要打码的目标；**未指定时不处理任何图片**（宁可漏，不可误打码）。

| 类别 | 说明 | 依赖 |
|---|---|---|
| `face` | 人脸：`auto`（默认，YuNet 首次自动下载 ~340KB，离线降级 Haar）/ `yunet` / `haar` / `yolo` | opencv（默认已装） |
| `text` | OCR 文字区域：公司名、印章、截图里的**所有印刷文字**整块打码 | `--extra image-ocr` |
| 其它（`person`、`car`…） | YOLO 通用目标检测，按类别名过滤 | `--extra image-yolo` + `--image-model` |

```bash
uv run pysanitize 合同.pdf --mask-images --image-classes face
uv run pysanitize 合同.pdf --mask-images --image-classes text
uv run pysanitize 合同.pdf --mask-images --image-classes person,car --image-model yolov8n.pt
```

马赛克为 NEAREST 分块（默认块 16px），只覆盖检测框内区域，框外原样保留。

## ⚙️ LLM 供应商配置

`--model` = `config/llm/<model>.yaml` 的文件名；`--provider` = 该 yaml 里的 **provider 段**（`openai:` / `pingan:`）。一个 yaml 可同时放多段，内网/外网切换只改 flag：

```bash
uv run pysanitize 合同.pdf --detector hybrid --provider pingan --model qwen3.6-27b
```

- `api_key` 一律用 `${ENV_VAR}` 占位，运行时从环境展开——**明文 key 永不入库**
- 选中的段缺失时报错明确：`no 'pingan' section in .../qwen3.6-27b.yaml; have: openai`
- 全局默认在 `config/pipeline.yaml` 的 `text.model` / `text.provider` 设置

## 🔒 安全边界与已知限制

- **扫描件**（无文本层）：M1 只脱敏 OCR 出的 Markdown；M2 用 `middle.json` 的 bbox 做坐标级 redaction
- **人名 / 公司名**为词典启发式，精度有限；敏感场景建议 `hybrid` 模式
- **LLM 幻觉**：value 回匹配硬防线，漏检概率高于误检
- **图片脱敏默认不启用**：必须显式指定 `--image-classes`；`text` 打码图片中**所有**印刷文字
- **长数字误报**（财务表格 18 位数字）：默认开校验位 + `bank_account` 默认关闭
- MinerU 首次运行会下载模型；`mineru[pipeline]` 依赖较重（torch/opencv）

## 🛠️ 开发

```bash
uv run pytest          # 单测全绿
```

```
pysanitize/
├── parser/     MinerU 封装 + ParsedDocument（text / blocks / 图片配对 / offset 映射）
├── detector/   rules / llm / registry（重叠消解）/ image（人脸 / OCR 文字 / YOLO 类别）
├── masker/     text（按 offset 掩码）/ image（马赛克）
├── pipeline.py sanitize_document() 编排（唯一公共入口）
├── cli.py      argparse CLI
├── llm/        LLM 门面（openai / pingan 多 provider）
└── report.py   audit.json / sensitive_report.json
config/         本地覆盖（git 忽略，可选）：fields.yaml（字段规格）/ pipeline.yaml（全部流水线可调参数）/ llm/*.yaml（模型配置）；缺省用内置默认
```

扩展点：**加字段→改 fields.yaml；加检测器→写一个类进 registry；加图片目标→写一个类进 `build_detectors` 路由；加输出格式→M2 加 renderer/**。接口都只有一个方法，不碰主干。

## 🗺️ Roadmap

- **M2**：保留原排版脱敏 —— PDF 用 `middle.json` bbox + PyMuPDF `apply_redactions` 真删除；DOCX/Excel 原位编辑；元数据清理、匿名输出名
- **M3**：WebUI（上传 + 任务队列 + 进度）

---

<div align="center">

Made with 🛡️ for safer documents · [提交 Issue](https://github.com/LyoAI/PySanitize/issues)

</div>
