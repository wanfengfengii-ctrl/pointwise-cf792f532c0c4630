# 分页合并表格采集契约

## 已有能力与本次交付

README.md、V1-CONTRACT.md及全部原测试继续有效。V1的parse_table、IntakeService和旧成果格式仍保持原行为，尤其继续拒绝非单位跨度和tbody重复表头。本次新增tablecapture.Capture，并扩展现有页面结构解析、证据归档及语义重放职责；不能只在新入口旁另存一套未经既有归档校验的成果。V2使用同一workdir下的ArchiveRepository进行暂存、提交、查找和加载，允许新增带规则版本的快照表示，不改写V1成果。本题不要求给V2增加筛选、游标或导出功能；原V1对应能力必须保留。

允许缓冲全部页面，在finish时统一展开和重放；没有流式、并发、额外内存或局部更新指标。仅处理已有HTML子集、固定五列和有序离线页面，不新增浏览器或网络获取。

## 调用与结果

使用Python标准库。模块tablecapture导出Capture和CaptureError（ValueError子类）。构造Capture(workdir, manifest=None)，manifest为已有source_manifest.SourceManifest；使用manifest_from_object或load_manifest构造，原声明中的页面顺序、URL规则、完整页面SHA-256及批次身份规则不变。传入manifest的实例绑定一个采集批次；不传时仅作读取和复核。相同声明重开可继续已接纳页面，改变同批声明须报错。

- ingest_page(page_url, html, page_no)：按声明顺序接纳str或UTF-8 bytes；返回与V1相同的status、page_no、sha256字典。此前已接纳的相同页号、规范URL和原字节重投返回duplicate，不再次推进任何跨度；V2在finish成功后仍允许这种精确重投。新页不得跳号，输入与声明不符或与已接纳页冲突时抛CaptureError，已接纳材料保持不变。
- finish()：全部声明页齐备后检查完整批次，成功返回不可变publication_id，重复finish返回同一身份。缺页、跨度未闭合、结构、字段、身份、证据或提交错误均抛CaptureError；可推迟结构/字段检查到此处。缺页失败后可以补齐再finish。任何失败都不得留下可读半批或改动先前成果。
- read(batch_id)：只从已发布归档返回独立字典，形如{"publication_id": "...", "records": [...]}，记录按首次出现的(page_no,row_no)排序。每条为{"row_key": str, "fields": 五值list, "field_sources": 五项list, "member_rows": list}，其语义见下。修改返回值不能改变归档。
- verify(batch_id)：返回已有VerificationReport类型，含ok、publication_id、checked_records、errors；成功时checked_records为逻辑记录数，失败时ok=False且errors非空。未知或损坏批次read抛CaptureError，verify报告失败，不修复存储。

同一workdir中V1和V2批次共存，批次id仍不可改写。Capture只需read/verify V2批次。finish提交前以及每次read/verify均需从绑定原页重新推导并比较逻辑记录、成员行和字段来源；不能只检查摘要或复用传入的候选值。声明、全部原页、规则版本、记录及来源关系共同参与成果身份。归档复制原页，重开后读取不得依赖原manifest对象、外部输入文件或网络。暂存、原子可见和失败清理由既有归档机制承担，扩展后不能绕过其材料与语义校验。

## 网格与行身份

每页仍有唯一目标table、一个thead和一个tbody；thead保持V1的五个单位跨度表头。tbody中，无data-row-key、五个单位跨度单元格且去首尾空白文本恰为COLUMN_ORDER的tr是重复表头；在展开前排除，既不占数据行号也不消耗任何rowspan。其余tr均为数据行。V1对footnote子树、原始字节、实体、inline文本、闭合及结构错误的规则继续有效；被排除的脚注不占行列，td/th本身仍保留槽位。

将各页的实际数据行按页序接成一个五列逻辑网格。rowspan和colspan省略为1，只接受值大于零的无符号十进制拼写；colspan不得超出五列，rowspan按实际数据行计数，包括起始行，可以越过页界及重复表头。在当前行，先保留从前行延伸的占用，再按源单元格顺序把新单元格放到最左空列，其colspan向右占连续列，rowspan向下占连续数据行。矩形不得与已有占用重叠或越过第五列；不能为躲开重叠而改放到后面的空段。每条实际数据行必须恰好覆盖五列；已被前行全部占用时允许空tr。最后一页之后仍有占用需求，即使文本已经齐备，也拒绝。空页不消耗跨度。表头跨度不得进入正文。

每个逻辑槽使用其原单元格文本，按V1对应列规则分别规范化；同一个colspan的文本可在不同列得到不同规范值或字段错误。逐数据行沿用V1的显式data-row-key规范化及缺省许可号身份规则。一个row_key只能占连续的一段实际数据行；后继同键行必须至少含一个继承单元格，且五个规范字段均与该键首行相同，才合并为一个逻辑记录。非连续重现、无继承却重键或任一字段变化均拒绝。不同键始终是不同记录，即使它们共享继承单元格或许可号相同；共享区划单元格不能被当成续行身份。

## 字段与成员证据

member_rows依采集顺序列出该逻辑记录的每个实际tr，包含完全继承的空tr；每项为V1 EvidenceRef同名六字段：page_no、row_no、start、end、page_sha256、fragment_sha256。row_no是该页排除重复表头和脚注后的数据行序号，从1计数。start/end是完整UTF-8原页中含开闭标签的tr半开字节范围。

field_sources第i项列出在该记录所有成员行第i列出现过的不同原单元格；同一原单元格继承多次只列一次，两个原单元格即使原字节或规范值相同也分别保留。每项含page_no、row_no、column_no、start、end、page_sha256、fragment_sha256。row_no为原单元格所在数据行，column_no为它在逻辑网格首次放置的最左列，从1计数；start/end覆盖完整原td/th，包括内部脚注。按(page_no,row_no,column_no,start)排序。摘要分别为完整原页和该原始片段的SHA-256小写十六进制值。

colspan跨两列时两列可引用同一来源，跨页继承的来源坐标仍指向最初页面；不得伪造续页单元格。member_rows与field_sources必须精确完整，不得省略空续行、额外添入无关片段或把不同原节点仅按文本/摘要去重。实际页缺失、任一原字节、位置或来源关系被改动，均不能成功复核或读取。快照的磁盘组织可自行设计，但须保留原归档的不可变身份、规范编码、材料核验和语义重放性质。

## 公开样例及运行

crosspage_cases.py内的examples()返回三份可直接交给manifest_from_object的完整声明，expected()是逐字枚举的预期来源，不实现网格算法：
whole中第一页四个单元格展开为五列，全部rowspan=2；第二页在表头及脚注后只有同键空tr。只得到K一条，字段为P-1、2024-02-29、approved、NORTH、NORTH，成员含两页，五列来源全部指第一页。
split中K的许可号、日期和区域跨页，状态和申请人在第二页重写为规范化后相同的值；仍一条，状态及申请人各保留两个原单元格来源。
shared中A、B共享跨页的NORTH单元格，但许可号不同，必须保留两条记录；两条的区域和申请人来源均指第一页第四列。

运行python3.11 -B crosspage_cases.py materials仅自检材料及V1缺口，不代表V2完成。开发后运行python3.11 -B crosspage_cases.py verify实际核对来源、重投、重开、缺续页、冲突拒绝、损坏拒读及V1成果保全；demo打印真实归档结果并执行相同核对。该有限夹具不替代全部公开规则：补充重键、跨度重叠/越界、相同值不同来源、字节坐标及失败保全的业务回归，并保留六个原测试文件的全部字节。

沿用.pointwise-runtime.json的现有字段格式；最终verify须同时执行完整原套件及V2业务核验，任一失败须非零退出，demo须展示跨页来源与逻辑记录而非仅打印成功。补一份简短交接说明，解释物理行到逻辑记录、原单元格到字段来源及发布前后状态的不变量。
