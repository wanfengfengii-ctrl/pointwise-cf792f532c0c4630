# 分页合并表格（V2）交接说明

本文件说明 `tablecapture.Capture` 如何把分页公示的物理行转成逻辑记录、
如何追踪原单元格来源，以及在发布前、暂存中、发布后和重开复核时的状态不变量。
V1 接口、V1 成果格式、`parse_table`、`IntakeService` 与六个原测试文件的
行为均未改变。

## 入口

```python
from permit_capture.source_manifest import manifest_from_object
from tablecapture import Capture, CaptureError

cap = Capture(workdir, manifest_from_object(declaration))
for page in manifest.pages:                 # 按声明顺序 1..N
    cap.ingest_page(page.url, page.html, page.page_no)
publication_id = cap.finish()               # 不可变身份
record_set = Capture(workdir).read(declaration['batch_id'])
report = Capture(workdir).verify(declaration['batch_id'])
```

- 带 `manifest` 的实例绑定一个批次，可写；不带 `manifest` 的实例只读，
  仅从归档中的原页重放。
- `ingest_page` 返回 V1 相同的 `{'status', 'page_no', 'sha256'}`；
  同页号、同规范 URL、同原字节的重投返回 `duplicate`，不推进任何跨度，
  重投发生在 `finish` 之后也保持 `duplicate`，不会再消耗一次跨度。
- `finish` 幂等：成功返回同一 `publication_id`，重复调用返回同一身份。
- `read` 返回 `{"publication_id", "records"}` 的独立副本；`verify` 返回
  `VerificationReport(ok, publication_id, checked_records, errors)`。

## 物理行 → 逻辑记录

1. **页内解析**（`permit_capture.grid.build_span_page_grid`）：每页唯一
   `table#id`，一个 `thead`（五个单位跨度表头）和一个 `tbody`。
   `tbody` 中无 `data-row-key`、五个单位跨度、去首尾空白文本恰为列名的
   `tr` 在展开前排除：不占数据行号，也不消耗任何 `rowspan`。脚注子树被
   排除，但 `td/th` 本身仍占槽位。V1 的字节、实体、闭合、结构错误规则沿用。
2. **跨页展开**（`span_capture.expand_pages`）：所有页的实际数据行按页序
   接成一个五列逻辑网格。`rowspan`/`colspan` 缺省为 1，只接受大于零的无
   符号十进制；`colspan` 不得越过第五列，`rowspan` 按实际数据行计数，可
   越过页界与重复表头，空页与脚注不消耗跨度。当前行先保留从前行延伸的
   占用，再按源单元格顺序放到最左空列，`colspan` 向右、`rowspan` 向下。
   矩形不得与已有占用重叠，也不能为躲开重叠而改放到后面的空段。每行必须
   恰好覆盖五列；被前行全部占满时允许空 `tr`。最后一页之后仍有未闭合
   跨度即拒绝（即使文本已齐备）。
3. **行身份与续行**（`span_capture.assemble_records`）：显式
   `data-row-key` 与缺省许可号身份按 V1 规则规范化。一个键只能占连续一段
   实际数据行；后继同键行必须至少含一个继承单元格，且五个规范字段都与该
   键首行相同，才并入同一逻辑记录。无继承的重键、字段变化、非连续重现都
   拒绝。不同键即使共享继承单元格或规范化许可号相同，也始终是两条记录—
   —共享的区划单元格不构成续行身份。

## 原单元格 → 字段来源

- `member_rows`：按采集顺序列出该逻辑记录的每个实际 `tr`（含完全继承的
  空 `tr`），每项为 V1 `EvidenceRef` 的六个字段，覆盖完整 `<tr …>…</tr>`
  的 UTF-8 半开字节范围。
- `field_sources[i]`：在该记录所有成员行第 `i` 列出现过的不同原单元格，
  每项额外带 `column_no`（逻辑网格首次放置的最左列）。继承多次的同一
  原单元格只列一次；原字节或规范值相同的两个不同单元格分别保留。`colspan`
  跨列时两列可引用同一来源；跨页继承的来源坐标始终指向最初页面，绝不
  伪造续页单元格。
- 排序：`field_sources` 内按 `(page_no, row_no, column_no, start)`；
  `member_rows` 与记录整体按首次出现的 `(page_no, row_no)`。
- 摘要：`page_sha256` 为完整原页，`fragment_sha256` 为该原始片段，均为
  SHA-256 小写十六进制。

## 发布前后状态不变量

| 阶段 | 存储位置 | 可读性 | 复核要求 |
| --- | --- | --- | --- |
| 接纳页 | `crosspage/` 状态 + `crosspage-pages/` 原字节 | 仅 `ingest_page` 可读回 | 重投比对页号/URL/字节 |
| 失败 `finish` | 无可见成果；暂存清空 | `read`/`verify` 均失败 | 旧成果逐字节不变 |
| 暂存 | `archive/staging/candidate-*` | 读者忽略 | 材料 + 语义重放校验 |
| 发布 | `archive/publications/<id>/` | 只读 | 每次 `read`/`verify` 从绑定原页重推 |

- 成果身份由声明、全部原页、规则版本与记录/来源关系共同决定：
  `publication_id` 是规范快照字节的 SHA-256（UTF-8、排序键、紧凑分隔符、
  显式 null、无 NaN）。V2 快照带 `format: 2, rules: "permit-span-v2"`，
  与 V1 快照存于同一 `ArchiveRepository` 且互不改写。
- `finish` 提交前、每次 `read`/`verify` 都从归档原页重新执行完整展开与
  装配，并与快照逐字段比较；只比对摘要或复用传入候选值不足以通过。
- 缺页、未闭合跨度、重键冲突、坏结构或坏证据一律抛 `CaptureError`，
  不留下可读半批，不改变旧成果；补齐缺页后可再次 `finish`。
- 重开（新的 `Capture(workdir)`）不依赖原 `manifest` 对象、外部输入文件或
  网络，只凭归档中绑定原页重放同一结果；原页被改动或快照来源被改动时
  `verify` 报失败、`read` 抛 `CaptureError`，且不修复存储。

## 运行

```sh
python3.11 -B -m unittest discover -s tests -v      # 原套件 + V2 业务回归
python3.11 -B crosspage_cases.py materials          # 仅材料自检
python3.11 -B crosspage_cases.py verify             # 公开样例核对
python3.11 -B verify_v2.py verify                   # 完整原套件 + 跨页核验
python3.11 -B verify_v2.py demo                     # 展示来源与逻辑记录
```

`RUNBOOK.json` 与 `.pointwise-runtime.json` 保留原有字段并列出了上述命令；
`verify` 组合入口任一失败即非零退出。
