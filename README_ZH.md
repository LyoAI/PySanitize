<div align="center">

# 🛡️ PySanitize

**多格式文档脱敏工具 · 本地解析 · 规则 + LLM 定位 · 按类别 + 按文字图片打码 · 保留排版的 PDF 打码**

![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)
![Version](https://img.shields.io/badge/version-0.2.0-4A90D9)
![Tests](https://img.shields.io/badge/tests-143%20passed-brightgreen)
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
| 🖼️ **图片脱敏** | `face` / 任意 YOLO 类别（检测模型），**以及按文字**：裸 `--image-text` 打码图片里全部印刷文字，或传字段列表只打码命中的敏感字段（如印章上的公司名）→ 马赛克 |
| 📄 **PDF 打码** | `--redact-pdf` 产出保留排版的 `redacted.pdf` —— 敏感字符**真删除**后盖马赛克，表格边框保留 |
| 🔁 **可恢复脱敏** | `--recoverable` 把每个敏感值的密文记入 `audit.json`；`pysanitize --recover` 用口令还原原始文档——脱敏文档本身与普通脱敏结果完全一致 |
| 🔌 **供应商可切换** | `--provider openai \| pingan`，内网/外网一键切换 |
| 📊 **审计友好** | 公开摘要不含原文；含原文报告 `--audit` 才生成 |
| 🛡️ **容错优先** | 缺 key / 缺可选依赖 / 模型下载失败 → 降级警告，绝不硬崩 |

## 🔄 处理流程

```text
输入(PDF/DOCX/Excel/扫描件)
  → [parser]      MinerU middle.json（逐行几何坐标）→ ParsedDocument{text, blocks, images/}
  → [detector]    规则 + LLM 定位敏感字段 → 精确字符 offset
  → [masker]      按字段类型掩码（138****5678 / **** / 保前后N位）
  → [image]       按类别（人脸/YOLO）+ 按文字（OCR → 全部文字或命中字段）→ PIL 马赛克
  → [redact]      （仅 PDF，可选）offset → 页面矩形 → redacted.pdf（真删字 + 马赛克）
  → 输出  sanitized.md + images_masked/ + audit.json
                  （--recoverable：audit.json 额外记录密文 → 可用 --recover 还原）
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
uv sync --extra recover         # 可恢复脱敏（--recoverable / --recover；cryptography）
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
uv run pysanitize 样例.pdf --mask-images --image-text         # 全部印刷文字（需 --extra image-ocr）

# 4) 限定字段 + 写出含原文的敏感审计报告（本地审计用，勿外发）
uv run pysanitize 样例.xlsx --fields person_name,phone --audit

# 5) 保留排版的 PDF 打码（PDF 输入可选，加 --redact-pdf 开启）
uv run pysanitize 样例.pdf --redact-pdf                     # 产出 output/<文档名>/redacted.pdf
uv run pysanitize 样例.pdf --redact-pdf --redaction-style block   # 纯色块代替马赛克

# 6) 按字段的图片脱敏：OCR 图片，只打码命中的字段
uv run pysanitize 样例.pdf --mask-images --image-text company_name,address

# 7) 可恢复脱敏 + 还原（需 --extra recover）
uv run pysanitize 样例.pdf --recoverable
#    → 产出与普通脱敏一致；密文记在 audit.json 里
uv run pysanitize output/样例/sanitized.md --recover          # md 精确还原
uv run pysanitize output/样例/redacted.pdf --recover          # pdf：值还原，排版近似
```

每次运行产出一个任务目录（默认 `output/<文档名>/`）：

```
output/<文档名>/
├── sanitized.md            # 脱敏后的 Markdown（图片链接指向 images_masked/）
├── images_masked/          # 每张抽取图——打码副本；未打码时即原图
├── redacted.pdf            # 仅 PDF 输入 + --redact-pdf：保留原排版，敏感区域真删字 + 马赛克
├── audit.json              # 公开审计摘要：字段命中数 + 掩码后文本，不含敏感原文
│                             --recoverable 时额外记录每个 span 的密文与位置
└── .recover.key            # --recoverable 且未传口令时自动生成的口令文件（0600）
```

`--audit` 时额外生成 `sensitive_report.json`（含字段**原文**与字符偏移，用于本地人工复核，**不要外发**）。命令行未指定的参数全部回退到 `config/pipeline.yaml`；指定后命令行优先。

## 🖥️ 交互式 TUI

```bash
uv sync --extra tui        # 一次性安装 Textual
uv run pysanitize --launch tui
```

六页签终端界面（基于 Textual）：**① 字段** — 从 `config/fields.yaml` 勾选要检测的敏感字段类型；**② 选项** — 输入文件、检测模式、LLM 端点、输出，外加 **可还原** 开关与密码式 **还原密钥** 输入框（留空 = 环境变量 / `.recover.key` / 自动生成）；**③ 图片** — 图片脱敏目标（开关、类别列表、全部文字开关、检测器），以及一个「与正文一致」开关，可另选（可更大的）字段集用于 OCR 图片内检测；**④ 运行** — 自由输入自定义要求（会追加到 LLM 提示词），点击运行并实时查看日志；**⑤ 结果** — 各字段命中数与输出路径；**⑥ 还原** — 指向 `sanitized.md` / `redacted.pdf`（其 `audit.json` 就在旁边），可选填还原密钥，即可替代 CLI 的 `--recover` 完成还原。退出：点击右上角 **✕ Quit** 按钮或按 `ctrl+c`（默认的 `ctrl+q` 会被部分终端吞掉，mac 的 `cmd+q` 则属于系统）。命令行仍是主入口，TUI 只是同一流水线（`pysanitize.core.run_sanitizer`）之上的便捷层。

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
    image_fields=["company_name", "address"],  # None=跟随正文字段；[]=不做图片字段检测
    redact_pdf=True,            # 可选：PDF 输入额外产出 redacted.pdf
    redaction_style="mosaic",   # mosaic | block
    audit=False,
    recoverable=True,           # audit.json 记录密文供 --recover 还原（需 recover extra）
    recover_key="passphrase",   # 缺省读 $PYSANITIZE_RECOVER_KEY，或自动生成 .recover.key
)
print(result.sanitized_md)     # Path
print(result.redacted_pdf)     # Path | None（PDF 输入且有可解析区域时）
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

## 🖼️ 图片脱敏

图片里不只有人脸 —— 可能是公司名、印章、文字截图。决定打码什么有两种互补方式；**完全未指定目标时不处理任何图片**（宁可漏，不可误打码）。

### 按类别（`--image-classes`）

`--image-classes` 列出要打码的**物体**——任何检测模型能认出的目标（人脸、行人、门牌号、公司门头招牌…）：

| 类别 | 说明 | 依赖 |
|---|---|---|
| `face` | 人脸：`auto`（默认，YuNet 首次自动下载 ~340KB，离线降级 Haar）/ `yunet` / `haar` / `yolo` | opencv（默认已装） |
| 其它任何目标（`person`、`car`、门牌号、门头招牌…） | YOLO 通用目标检测，按类别名过滤；非标准目标需自定义权重（`--image-model`） | `--extra image-yolo` + `--image-model` |

```bash
uv run pysanitize 合同.pdf --mask-images --image-classes face
uv run pysanitize 合同.pdf --mask-images --image-classes person,car --image-model yolov8n.pt
```

### 按文字（`--image-text`）

公司名、注册地址很少能归入某个*类别* —— 它们是 logo/印章里的**文字**。图片文字统一走 OCR（`--extra image-ocr`），两种粒度：

```bash
uv run pysanitize 合同.pdf --mask-images --image-text                        # 全部印刷文字
uv run pysanitize 合同.pdf --mask-images --image-text company_name,address   # 只打码命中的字段
```

- **裸旗标** `--image-text`：OCR 出的每块文字区域整体打码（印章、截图、盖章）
- **带字段列表**：对 OCR 文本跑与正文**同一套字段检测器**，只打码命中的字段
- 两种都没传时，字段式打码**跟随正文字段集**（`--fields`）；`config/pipeline.yaml` 里 `image.fields: []` 可关闭
- 显式传入的字段集可以是正文的**超集**（盖在印章上的地址，正文里从未出现）
- `--image-classes` 不受影响，仍然并行执行
- 近整页的扫描图会跳过（其文字已作为正文处理）

马赛克为 NEAREST 分块（默认块 16px），只覆盖检测框内区域，框外原样保留。

## 📄 PDF 打码（`redacted.pdf`）

加 `--redact-pdf` 后，PDF 输入会在 `sanitized.md` 旁额外产出 `redacted.pdf`：**保留原始排版**，每个命中的敏感 span 从内容流中**真删除**，再盖上马赛克（`--redaction-style mosaic`，默认）或纯色块（`block`）。表格边框与矢量图形保留，重叠的图片像素一并清空。

- 坐标来自 MinerU `middle.json` 的逐行 bbox；行内命中按字符宽度比例定位（CJK 近似等宽）
- **表格**：middle 3.x 无单元格坐标，因此表格内的命中会打码**整张表**的 bbox —— 保守但安全的过度打码
- **图片**：实际被马赛克的图片区域会贴回对应页面，PDF 与 `images_masked/` 一致；未打码的图片原样保留
- 扫描页（无文本层）天然跳过校验，残留会降级为 warning 而非失败
- 默认关闭：加 `--redact-pdf` 开启，或在 `config/pipeline.yaml` 设 `output.redact_pdf: true` 常开；office 输入永不产出

### 为什么用 PyMuPDF（AGPL）

MinerU 只能**读** PDF —— 没有写入能力，重新渲染会丢字体/表格线/背景，而且仍需要一个 writer。PyMuPDF 是唯一既能读又能忠实重写（真删字）的依赖。它采用 **AGPL 许可**：内部工具没问题，但要把 PySanitize 嵌入闭源产品前请先评估。

## 🔁 可恢复脱敏（`--recoverable` / `--recover`）

默认掩码不可逆。加 `--recoverable`（需 `uv sync --extra recover`）后，脱敏文档**与普通脱敏结果完全一致**——占位符就是你配置的正常掩码（`138****5678`、`***`…）——但每个敏感值的密文会被记入 `audit.json`，之后凭口令即可还原原文。加密为 AES-256-GCM，密钥由口令经 scrypt 派生；同一值在一次运行中始终得到同一密文。

```bash
uv run pysanitize 样例.pdf --recoverable                        # 口令自动生成到 output/<文档>/.recover.key
uv run pysanitize 样例.pdf --recoverable --recover-key s3cret   # 或显式传口令
PYSANITIZE_RECOVER_KEY=s3cret uv run pysanitize 样例.pdf --recoverable   # 或走环境变量

# 之后还原 —— audit.json 必须与文件同目录（或用 --recover-audit 指定）：
uv run pysanitize output/样例/sanitized.md --recover
uv run pysanitize output/样例/redacted.pdf --recover --recover-key s3cret
```

audit 里记录了什么（仅 recoverable 模式）：

- **`recovery` 块** —— 算法、KDF 名称、scrypt 盐与参数、密文格式。盐是公开参数；**密钥材料绝不入 audit**。拿到口令即可还原，拿不到则无能为力。
- **每个 span 额外字段**：`encrypted_value`（`ENC(v1:…)` 密文——`masked_value` 仍是正常占位符）、`start`/`end`（原文偏移）、`md`（占位符在 `sanitized.md` 中的区间）、`rects`（PDF 中被打码的矩形）。

还原方式：

- **Markdown 精确还原** —— 解密值按记录的 `md` 区间拼接回去（每次拼接都先校验该区间仍是占位符，文档若被改动只会计为 unresolved，不会拼错文本）。
- **PDF 尽力还原** —— 原字符在脱敏时已真删除；还原时清掉记录的 `rects` 区域、以自适应字号重新插入解密值：**值**回来了，原排版回不来。**图片不可恢复**（马赛克天然破坏性）。
- 还原是独立消费者（`pysanitize/recover/`）：从不 import 脱敏流水线——只依赖 audit 与口令；且**绝不生成密钥**——找不到口令就报错。

> ⚠️ `--recoverable` 模式下 **`audit.json` 携带所有敏感值的密文**，其保密性等价于口令强度：只随文档发往允许还原的地方，并像保管数据一样保管 `.recover.key`（0600）。脱敏文档本身仍可像普通脱敏结果一样外发。

## ⚙️ LLM 供应商配置

`--model` = `config/llm/<model>.yaml` 的文件名；`--provider` = 该 yaml 里的 **provider 段**（`openai:` / `pingan:`）。一个 yaml 可同时放多段，内网/外网切换只改 flag：

```bash
uv run pysanitize 合同.pdf --detector hybrid --provider pingan --model qwen3.6-27b
```

- `api_key` 一律用 `${ENV_VAR}` 占位，运行时从环境展开——**明文 key 永不入库**
- 选中的段缺失时报错明确：`no 'pingan' section in .../qwen3.6-27b.yaml; have: openai`
- 全局默认在 `config/pipeline.yaml` 的 `text.model` / `text.provider` 设置

## 🔒 安全边界与已知限制

- **扫描件**（无文本层）：OCR 出的 Markdown 照常脱敏；`redacted.pdf` 校验在那里天然无事可删
- **表格在 PDF 中整表打码**（middle 3.x 无单元格坐标）；`sanitized.md` 输出仍是单元格级精确
- **人名 / 公司名**为词典启发式，精度有限；敏感场景建议 `hybrid` 模式
- **LLM 幻觉**：value 回匹配硬防线，漏检概率高于误检
- **图片脱敏默认不启用**：必须显式指定 `--image-classes` 和/或 `--image-text`；裸 `--image-text` 打码图片中**所有**印刷文字
- **长数字误报**（财务表格 18 位数字）：默认开校验位 + `bank_account` 默认关闭
- **PyMuPDF 为 AGPL 许可** —— 仅用于 `redacted.pdf`；内部工具无碍，闭源分发前请评估
- **`--recoverable` 把密文写进 `audit.json`** —— 脱敏文档仍可外发，但 audit 与 `.recover.key`（0600）须与数据同等保管
- MinerU 首次运行会下载模型；`mineru[pipeline]` 依赖较重（torch/opencv）

## 🛠️ 开发

```bash
uv run pytest          # 单测全绿
```

```
pysanitize/
├── parser/     MinerU 封装（middle.json 投影 + v2 回退）+ ParsedDocument（逐行几何 / 图片配对 / offset 映射）
├── detector/   rules / llm / registry（重叠消解）/ image（人脸 / YOLO 类别 / OCR 文字与按字段）
├── masker/     text（按 offset 掩码）/ image（马赛克）
├── redact/     offset → 页面矩形，PyMuPDF 打码 + 校验
├── recover/    可逆令牌（AES-GCM + scrypt）与独立于流水线的还原
├── pipeline.py sanitize_document() 编排（唯一公共入口）
├── cli.py      argparse CLI
├── llm/        LLM 门面（openai / pingan 多 provider）
└── report.py   audit.json / sensitive_report.json
config/         本地覆盖（git 忽略，可选）：fields.yaml（字段规格）/ pipeline.yaml（全部流水线可调参数）/ llm/*.yaml（模型配置）；缺省用内置默认
```

扩展点：**加字段→改 fields.yaml；加检测器→写一个类进 registry；加图片目标→写一个类进 `build_detectors` 路由；加输出格式→M2 加 renderer/**。接口都只有一个方法，不碰主干。

## 🗺️ Roadmap

- **M2**：PDF 打码已落地（`redacted.pdf`）；剩余：DOCX/Excel 原位编辑、元数据清理、匿名输出名
- **M3**：WebUI（上传 + 任务队列 + 进度）

---

<div align="center">

Made with 🛡️ for safer documents · [提交 Issue](https://github.com/LyoAI/PySanitize/issues)

</div>
