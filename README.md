# PySanitize · 多格式文档脱敏工具

对 **PDF / DOCX / Excel / 扫描件** 做敏感信息脱敏的开源工具：

- 依托 **MinerU** 本地解析文档（纯本地 subprocess，无云端调用）
- **规则 + LLM** 定位敏感字段（手机号、身份证号、公司名、人名……），LLM 只做**定位**、不重写原文
- 图片**按类别**脱敏：人脸（YuNet/Haar/YOLO）、印刷文字（OCR 文本区域）、任意物体（YOLO 类别过滤），输出**马赛克**打码副本
- M1 输出脱敏后 **Markdown + 打码图片 + 审计报告**；M2 将保留原排版（PDF 原地 redaction / DOCX / Excel 编辑）

```text
输入(PDF/DOCX/Excel/扫描件)
  → [parser]      MinerU → ParsedDocument{text, blocks, images/}
  → [detector]    规则 + LLM 定位敏感字段 → 精确字符 offset
  → [masker]      按字段类型掩码（138****5678 / **** / 保前后N位）
  → [image]       按类别检测（人脸/OCR文字/YOLO物体）→ PIL 马赛克
  → 输出 sanitized.md + images_masked/ + audit.json
```

## 安装

需要 Python ≥ 3.12，推荐 [uv](https://docs.astral.sh/uv/)：

```bash
git clone <repo> && cd PySanitize
uv sync                    # 安装全部依赖（含 mineru[pipeline]，体积较大）
cp .env.example .env       # 填上你要用的 LLM key（仅 llm / hybrid 模式需要）
```

可选特性：

```bash
uv sync --extra image-yolo      # YOLO 通用目标检测（ultralytics）
uv sync --extra image-ocr       # OCR 文字区域检测（paddleocr，体积较大）
```

## 快速开始

```bash
# 1) 纯本地规则检测（离线可用，不调用任何 LLM）
uv run pysanitize sanitize 样例.pdf

# 2) 混合模式：规则 + LLM 定位（默认 openai/deepseek-v4-flash）
uv run pysanitize sanitize 样例.pdf --detector hybrid

#    指定 LLM 供应商与模型（与 finsearch-bench 一致）：--provider 选 config/llm/<model>.yaml 里的段
uv run pysanitize sanitize 样例.pdf --detector hybrid --provider pingan --model qwen3.6-27b
uv run pysanitize sanitize 样例.pdf --detector llm     --provider openai  --model qwen3-max

# 3) 图片打码：默认不检测任何图片，必须显式指定目标
uv run pysanitize sanitize 样例.pdf --mask-images --image-classes face   # 人脸
uv run pysanitize sanitize 样例.pdf --mask-images --image-classes text   # 印刷文字（公司名等，需 --extra image-ocr）

# 4) 限定字段 + 写出含原文的敏感审计报告（本地审计用，勿外发）
uv run pysanitize sanitize 样例.xlsx --fields person_name,phone --audit
```

每次运行产出一个任务目录（默认 `output/<文档名>/`）：

```
output/<文档名>/
├── sanitized.md            # 脱敏后的 Markdown（图片链接指向 images_masked/）
├── images_masked/          # 打码（或无敏感）图片副本
└── audit.json              # 公开审计摘要：字段命中数 + 掩码后文本，不含敏感原文
```

`--audit` 时额外生成 `sensitive_report.json`（含字段**原文**与字符偏移，用于本地人工复核，**不要外发**）。

命令行未指定的参数全部回退到 `config/pipeline.yaml`；指定后命令行优先。

## Python API

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

## 检测模式

| 模式 | 说明 |
|---|---|
| `rules` | 纯本地正则 + 词典启发式（身份证/信用代码校验位、百家姓、公司后缀词典），**离线可用** |
| `llm` | LLM 逐段**定位**敏感字段：返回 `{"field_type", "value"}`，value 必须为原文逐字子串，再回匹配得到精确 offset |
| `hybrid` | 两者都跑，规则结果在重叠时优先 |

LLM 只做定位不重写，并有**幻觉硬防线**：value 在原文回匹配不到即丢弃（宁漏勿错）；`temperature=0` + `response_format=json_object`。模型与 key 在 `config/llm/<model>.yaml` 配置（`api_key` 用 `${ENV_VAR}` 占位，运行时展开）。

**供应商/模型选择**：一个 yaml 可含多个 provider 段（`openai:` / `pingan:`），`--provider` 指定用哪段、`--model` 指定哪个 yaml：

```bash
uv run pysanitize sanitize 合同.pdf --detector hybrid --provider pingan --model qwen3.6-27b
```

- `--model` = `config/llm/<model>.yaml` 的文件名
- `--provider` = 该 yaml 里的段名；默认 `openai`，可通过 `config/pipeline.yaml` 的 `text.provider` / `text.model` 改全局默认
- 例如内网用 pingan 时，在 `config/llm/qwen3.6-27b.yaml` 里补一段 `pingan:`（含 `sceneId`、`appKey` 等，参考 `llm/provider/pingan.py`），CLI 即可 `--provider pingan --model qwen3.6-27b`
- 选中的段不存在时会明确报错：`no 'pingan' section in .../qwen3.6-27b.yaml; have: openai`

## 默认敏感字段（config/fields.yaml）

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

字段**可增删、可改掩码**（`config/fields.yaml`），`--fields a,b` 只检测指定字段（可临时启用默认关闭的字段）。

## 图片脱敏（按类别检测）

图片里不只有人脸 —— 可能是公司名、印章、文字截图。PySanitize 按**类别**驱动图片打码：`image.classes` / `--image-classes` 列出要打码的目标；**未指定时不处理任何图片**（宁可漏，不可误打码）。

| 类别 | 说明 | 依赖 |
|---|---|---|
| `face` | 人脸检测。后端 `auto`（默认：YuNet，~340KB ONNX 首次自动下载，离线降级 Haar）/ `yunet` / `haar` / `yolo` | opencv（默认已装）；`yolo` 需 `--extra image-yolo` |
| `text` | OCR 文字区域：公司名、印章、截图里的**所有印刷文字**整块打码（安全优先，不按内容区分） | `--extra image-ocr`（paddleocr，体积大） |
| 其它（`person`、`car`…） | YOLO 通用目标检测，按类别名过滤 | `--extra image-yolo` + 模型（`--image-model yolov8n.pt` 等） |

示例：

```bash
uv run pysanitize sanitize 合同.pdf --mask-images --image-classes face
uv run pysanitize sanitize 合同.pdf --mask-images --image-classes text
uv run pysanitize sanitize 合同.pdf --mask-images --image-classes person,car --image-model yolov8n.pt
```

多个目标可组合：`--image-classes face,text`。马赛克为 NEAREST 分块（默认块 16px），只覆盖检测框内区域，框外原样保留。

## 开发

```bash
uv run pytest          # 单测全绿
```

代码结构：

```
pysanitize/
├── parser/     MinerU 封装 + ParsedDocument（text / blocks / 图片配对 / offset 映射）
├── detector/   rules / llm / registry（重叠消解）/ image（人脸 / OCR 文字 / YOLO 类别）
├── masker/     text（按 offset 掩码）/ image（马赛克）
├── pipeline.py sanitize_document() 编排
├── cli.py      argparse CLI
└── report.py   audit.json / sensitive_report.json
config/         fields.yaml（字段规格）/ pipeline.yaml（阶段开关）/ llm/*.yaml（模型配置）
```

## 安全边界与已知限制

- **扫描件**（无文本层）：M1 只脱敏 OCR 出的 Markdown；M2 用 `middle.json` 的 bbox 做坐标级 redaction
- **人名 / 公司名**为词典启发式，精度有限；对敏感场景建议 `hybrid` 模式（LLM 提升召回）
- **LLM 幻觉**：value 回匹配硬防线，漏检概率高于误检
- **图片脱敏默认不启用**：必须显式指定 `--image-classes`；`text` 会打码图片中**所有**印刷文字（安全优先，粒度是整个文字行）
- **长数字误报**（财务表格 18 位数字）：默认开启校验位 + `bank_account` 默认关闭，可再关
- MinerU 首次运行会下载模型；`mineru[pipeline]` 依赖较重（torch/opencv）
- Excel 经 MinerU 摊平成 markdown 表格，M1 直接对表格文本掩码（**固定长度掩码不破坏表格对齐**）

## Roadmap

- **M2**：保留原排版脱敏 —— PDF 用 `middle.json` bbox + PyMuPDF `apply_redactions` 真删除；DOCX/Excel 原位编辑；元数据清理、匿名输出名
- **M3**：WebUI（上传 + 任务队列 + 进度）
