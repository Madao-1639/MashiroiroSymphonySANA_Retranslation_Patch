# 纯白交响曲 SANA EDITION 汉化移植项目

## 项目目的

把「绿茶汉化组 SANA EDITION 汉化补丁」（简体中文，含 `mashiro_sana_cn2.xp3` 后续补丁）的剧情文本，移植到 Steam 版
《純白交響曲 SANA EDITION》（appid **2737980**）中，替换质量不佳的官方中文。

**与姊妹项目的关系**：技术栈、工具、补丁机制与本机 `../../MashiroiroSymphonyFHD/MashiroiroSymphonyFHD_Retranslation_Patch`
（FHD 本篇，appid 2737970）**完全相同**，本仓库是从那里分出来的第二个产品；引擎级结论的母本是
`../../GalRevserse/GalRev/docs/engines/krkr/`。改动引擎级结论时三处同步；本仓库只写 SANA 专属取值。

## 技术背景（结论速查）

| 项 | 结论 |
|---|---|
| 引擎 | krkrz + M2「MDF」脚本层（剧情是编译后的 PSB，不是 `.ks`） |
| 剧情脚本 | Steam 侧是裸 PSB；民汉侧是 `mdf\0` + `u32 长度` + `zlib(PSB)` 外壳 |
| Steam 侧加密 | 全部 xp3 为 Hxv4；**`resource/hxv4/profile_live.json` 与 FHD 那份通用**（同一套 key/nonce/holder/context/lanes/`xp3hnp` 盐，实测可完整解开 `data.xp3`/`patch.xp3`，无需重抓） |
| **Steam 脚本必须算上 `patch.xp3`** | 只有 `data.xp3` 时游戏是**残缺版**（40 脚本 / 10,496 行）；加上官方 `patch.xp3`（1.10 GB，2024-06）才是完整版 **102 脚本 / 23,245 行 / 11 选项** |
| 语言维度 | 域 = 目录哈希：`9D31BC7A…`=`cn/scn/`、`9A19D83E…`=`tw/scn/`、`BB415FD3…`=`en/scn/`；三域脚本集/行数/voice id 完全一致（各 102 / 23,245 / 11） |
| 归档优先级 | 注册时间越晚越高；官方链顶端是 `patch.xp3><lang>/scn/`（`initialize.tjs:1103`，全链最后一次） |
| 民汉补丁 | 绿茶 SANA EDITION 汉化：`mashiro_sana_chs.xp3`(264 MB, 115 脚本) + `mashiro_sana_cn2.xp3`(141 KB, 3 个 H 脚本) + `patch.xp3`(与 FHD 补丁的 patch.xp3 md5 相同) ⇒ **118 脚本 / 28,254 行 / 11 选项** |
| 文本覆盖 | 共享 102 脚本 **结构完全同构**（行数、`(scene,row)`、页数、voice id、`char_id`、speaker 有无、选项 index 全等）⇒ **位置映射**，与 FHD 阶段同类 |
| 不可移植 | 民汉多出 16 脚本 / 5,009 行（`airi_route*` / `ange_route*` / `sakuno_route01_10`，本作没有这些路线） |
| 补丁形式 | 单文件 `releases/CN.xp3`：根域 `default.tjs`（**取 SANA 自己的 `main/default.tjs`** + 钩子）在启动末尾注册 `CN.xp3>cn/scn/` ⇒ 最高优先级；脚本存 `cn/scn/` 域。卸载 = 删文件 |

详细规范见 `doc/file-formats.md`、`doc/text-model.md`；引擎机制见 `doc/engine-mechanics.md`；加密层见 `doc/hxv4.md`；
两版差异与阶段结论见 `doc/version-diff.md`。

## 工程约定

### 1. 文档同步要求

**任何结论性修改都必须落到文档**，不要只留在对话里：

| 改动 | 必须更新 |
|---|---|
| 文件格式/加密细节 | `doc/file-formats.md`（引擎级改动还要同步 FHD 项目与 galrev） |
| 脚本文本模型（字段含义、可翻译范围） | `doc/text-model.md` |
| 引擎行为、字体、加载顺序、补丁机制、语言维度 | `doc/engine-mechanics.md` |
| 两版差异结论 | `doc/version-diff.md` |
| 踩坑与返工教训 | `doc/lessons-learned.md` |
| 验收标准 | `doc/acceptance-criteria.md` |

文档只写**当前成立的最新结论**，不含探究历史；每条结论必须有验证依据（能复跑的命令或文件指针）。

### 2. 目录与脚本组织

```
tool/        引擎库（PSB / .scn / xp3 / Hxv4 读写），与 FHD 项目同源
script/      项目流程脚本（提取、对齐、校验；回写/出包待阶段 4 迁入）
resource/    可复用的数据产物（提取出的文本、对齐表、Hxv4 参数）
doc/         知识文档
tmp/         临时文件，任务结束即清理（不入库）
releases/    交付物（`CN.xp3`）
```

- `tool/` 是 **Python 包**，脚本用 `from tool import psb, xp3, scn, hxv4`。
- 一次性脚本、调试输出、解包出来的原始资源一律放 `tmp/`，任务结束删除；需要保留的中间结果移入 `resource/`。
- 所有脚本用相对于项目根的路径；从项目根执行。

### 3. 脚本开发规范

- **幂等**：可重复运行，已满足则跳过。
- **回读校验**：任何写操作（重建 `.scn`、重打包 xp3）后必须重新解析产物并断言内容与预期一致。
- **零改动等价性**：改写工具的"不改任何内容"往返重建，PSB body 必须逐字节相同。
- **报错优先**：遇到不认识的 PSB 类型、长度不符、校验失败，一律抛异常并指出文件名+偏移，禁止静默跳过。
- **阶段闸**：本仓库当前的对齐闸是 `script/align_check.py`（结构同构 + 名字哈希覆盖），改动数据结构后先跑它。

### 4. 编码约定

- PSB 字符串池是 **UTF-8**，中文直接写入，不需要编码转换。
- 脚本名在 xp3 索引里是 UTF-16LE，在 PSB `name` 字段里是 UTF-8，不要混用。
- 控制台输出：脚本 print 保持 ASCII 或显式 `sys.stdout.reconfigure(encoding='utf-8')`。
- 文本中的引擎控制码（`%NNN;`、`#RRGGBBAA;`、`#;`、`\n`）**必须原样保留**。

### 5. 安全与备份

- **不修改** Steam 版安装目录里的原始文件（只读）；所有产物写到项目目录。
- 覆盖任何已产出的补丁文件前先备份（`*.bak`，并在 `doc/` 记录原因）。
- GB 级归档只做只读解析（`mmap`），不要复制。

### 6. 测试与验收

每个阶段结束前：
1. 跑该阶段校验脚本（提取→`script/verify_extract.py`；对齐→`script/align_check.py`；回写→往返一致性 + 重新解析）
2. 抽样人工比对（随机 10 条：文件、场景、speaker、文本）
3. 更新对应文档
4. 清理 `tmp/`

详细验收标准见 `doc/acceptance-criteria.md`。

## 环境

- Python 环境：miniforge 的 **GalRev** 环境（3.11）——本项目**只用这一个环境**。执行脚本用 `mamba run -n GalRev python script/xxx.py`。
- 本机 shell 初始化脚本不可写，mamba 每次会打印几行 `libmamba Error opening for writing ...mamba.bat: Permission denied`；**不影响执行**，批量脚本用 `2>&1 | grep -v libmamba` 过滤。
- 本项目的库全部是**标准库**（`zlib`/`struct`/`hashlib`/`ctypes`/`mmap`/`json`/`re`），不依赖 numpy 等。
- **禁止主动安装包**：确需新库时先说明用途并获批，只在 GalRev 环境里装。
- 游戏目录：`E:\SteamLibrary\steamapps\common\MashiroiroSymphonySANA`（ASCII 路径；脚本里写死这个默认值）。
- 民汉补丁原件：`D:\My_Code\tmp\GamePatch\MashiroiroSymphonySANA\retranslation_patch_original`。
- **Steam 侧文本来源（离线）**：`script/extract_text.py --profile resource/hxv4/profile_live.json --domain 9D31BC7AA367A8F1`，读 `data.xp3` + `patch.xp3`（**顺序不能反**，后者覆盖前者）。不启动游戏、不注入。
- **profile 与游戏构建绑定**：游戏更新后参数失效，用 FHD 项目的 `script/capture_live_crypto.py`（x32dbg，须从调试器启动游戏）重抓，见 `doc/hxv4.md` §5。
- 动态分析：x32dbg（MCP `http://127.0.0.1:50301/mcp`）；静态分析：IDA Pro（`http://127.0.0.1:13337/mcp`）。当前流程都不依赖。

## 参考资料

- `../../MashiroiroSymphonyFHD/MashiroiroSymphonyFHD_Retranslation_Patch/`：姊妹项目（本篇），本仓库的 `tool/`、`script/`、`doc/` 母本
- `../../GalRevserse/GalRev/docs/engines/krkr/`：引擎级通用结论与库
- `../../GalRevserse/YuaExtract/`：Hxv4 格式与算法参考实现，仅作参考，不依赖
- `../../A_Sky_Full_of_Stars/`：同系列工程约定与「从汉化补丁提取文本」方法论
