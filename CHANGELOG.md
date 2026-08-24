# Changelog

## [0.2.0] - 2026-08-24

图片脱敏从「仅人脸」泛化为**按类别驱动**：用户指定目标 → 检测模型定位 → 打码。

### 新增

- **detector/image**：`FaceBox` → `DetectedObject`（新增 `label`：`face` / `text` / YOLO 类别名，保留 `FaceBox` 别名）
- **YOLO 类别过滤**：`YOLODetector(classes=[...])` 支持任意权重 + 按类别名过滤（`row.cls` → `model.names`）；`YOLOFaceDetector` 别名保留
- **OCR 文字区域**：`OCRTextDetector`（PaddleOCR，`--extra image-ocr`），`text` 类别打码图片中全部印刷文字（公司名/印章/截图）
- **`build_detectors(classes, ...)`**：类别→后端路由 —— `face`→YuNet/Haar/YOLO，`text`→OCR，其它→YOLO 类别过滤；后端缺失时降级警告不硬崩
- **默认不检测图片**：`image.classes` 为空时 `--mask-images` 不处理任何图片（宁可漏、不可误打码）；CLI 新增 `--image-classes face,text,person`（逗号分隔）

### 变更

- **LLM 供应商/模型可指定**：CLI 新增 `--provider`（`openai`/`pingan`）与 `--model`，与 finsearch-bench 一致 —— `--provider` 选 `config/llm/<model>.yaml` 里的 provider 段；`sanitize_document(llm_provider=...)` + `config/pipeline.yaml` `text.provider`
- `config/pipeline.yaml` 新增 `image.classes`（默认 `[]`）；`image.detector` 语义收敛为「人脸后端」
- 所有图片检测器/掩码器输出与消费 `DetectedObject`（带 label，供审计区分目标）

### 测试

- 74 个单测（+13）：路由降级（空 classes / 缺 paddleocr / 缺 ultralytics）、YOLO 类别过滤、
  OCR 框构建/置信度过滤/空页、`FaceBox` 别名、无 classes 时图片不处理

## [0.1.0] - 2026-08-24

M1 MVP：多格式文档脱敏 pipeline（脱敏后 Markdown + 打码图片 + 审计报告）。

### 新增

- **parser**：MinerU CLI 封装（`-p doc -o out -b backend -l ch`），支持 PDF/图片/DOCX/PPTX/XLSX；
  `content_list_v2` 投影为 `Block`，图片按阅读顺序与 image 块配对；`ParsedDocument.text` 提供全文 +
  每块字符偏移（meta 页眉/页脚不进脱敏文本）
- **detector/rules**：正则 + 词典启发式，8 类默认字段；身份证（GB 11643）与统一社会信用代码（GB 32100）
  校验位可开关；百家姓上下文人名、公司后缀回溯 + 边界/黑名单剪枝
- **detector/llm**：LLM 只做定位（返回 `{field_type, value}` 逐字子串），chunk（6000+300 重叠）+ verbatim
  回匹配得精确 offset，回匹配不到的 value 丢弃（幻觉硬防线）；`temperature=0` + `response_format=json_object`
- **detector/registry**：多检测器汇总 + 精确去重 + 包含消解（规则命中优先于 LLM）
- **masker/text**：按 offset 左→右重建输出，固定长度占位不破坏 markdown 表格对齐；重叠防御性合并
- **detector/image**：YuNet（ONNX，首次自动下载）优先，离线降级 Haar；可选 `[image-yolo]` extra
- **masker/image**：PIL NEAREST 分块马赛克（默认 16px），只覆盖检测框
- **pipeline + cli + report**：`sanitize_document()` 编排；CLI `pysanitize sanitize <file>
  [--detector rules|llm|hybrid] [--fields a,b] [--mask-images] [--audit]`；输出
  `sanitized.md` + `images_masked/` + `audit.json`（公开摘要不含原文），`--audit` 时附含原文的
  `sensitive_report.json`
- **config**：`config/fields.yaml`（字段规格）、`config/pipeline.yaml`（阶段开关）、`config/llm/*.yaml`
  （`${ENV_VAR}` 占位，去除明文 key）；`.env.example`

### 修复

- 修复脚手架残留：`finsearch.*` import → `pysanitize.*`；`openai>=1` / `mineru[pipeline]` 依赖补齐
- `opencv-python` 钉在 `<5.0`（5.x 移除了 Haar `CascadeClassifier`，破坏离线降级路径）
- `six` 显式声明（`mineru[pipeline]` 传递依赖缺失导致 pipeline 后端启动失败）

### 测试

- 60 个单测：parser 投影/文档偏移、规则检测器（校验位/黑名单/边界）、LLM 检测器（chunk/回匹配/
  幻觉过滤）、registry 消解、文本/图片掩码、pipeline 编排、CLI；真实 MinerU 端到端样例 PDF 验证通过
