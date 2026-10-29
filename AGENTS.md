# 纯白交响曲 SANA EDITION 汉化移植项目

## 项目目的

把「绿茶汉化组」的 SANA EDITION 汉化文本（简体中文，含 `mashiro_sana_cn2.xp3` 补充补丁）移植到 Steam 版（appid **2737980**）中，替换质量不佳的官方中文。

**与姊妹项目的关系**：技术栈、工具、补丁机制与本机 `../../MashiroiroSymphonyFHD/MashiroiroSymphonyFHD_Retranslation_Patch`（FHD 本篇，appid 2737970）完全相同，本仓库是从那里分出来的第二个产品。

**流水线**：
1. 提取民汉文本（`resource/chs_text/`）
2. 从文本层面比对两版差异（`doc/version-diff.md`）
3. 文本匹配与语义抽检（由项目所有者组织）
4. 回写 Steam 版脚本，产出补丁（`releases/CN.xp3`）

详细规范见 `doc/file-formats.md`、`doc/text-model.md`；引擎机制见 `doc/engine-mechanics.md`；加密层见 `doc/hxv4.md`。

> **文档分工**：本项目 `doc/` 面向外部读者，保持**自包含**（含通用格式细节）。
> 同一批结论另有一份**通用版**在本机知识库：`../../GalRevserse/GalRev/docs/engines/krkr/`
> （已剔除本项目专属取值、标注「跨作需重验」），配套库 `galrev/krkr/`。
> 改动引擎级结论时**三处同步**（本仓库、FHD 仓库、galrev）；本项目专属取值只写在这里。

## 技术背景（结论速查）

| 项 | 结论 |
|---|---|
| 引擎 | krkrz + M2「MDF」脚本层（剧情脚本不是 `.ks`/`.tjs`，是编译后的 PSB） |
| 剧情脚本 | `*.scn` = `mdf\0` + `u32 原文长度` + `zlib(PSB v3)`；Steam 侧是裸 PSB（无 `mdf` 外壳） |
| PSB 结构 | 字典树式 names + 字符串池 + 对象图；文本是 UTF-8 字符串槽 |
| 民汉补丁 | 【绿茶汉化组】SANA EDITION 汉化：`mashiro_sana_chs.xp3` + `mashiro_sana_cn2.xp3`（H 场景补充）+ `patch.xp3`；明文，可直接解包 |
| Steam 取文本 | Hxv4 **已离线可读**：`tool/hxv4.py` + `resource/hxv4/profile_live.json`（**与本篇通用**，实测可解开本作全部归档，无需重抓） |
| Steam 版归档 | 全部 xp3 为 Hxv4 加密。**剧本在 `data.xp3`（40 个，残缺）+ `patch.xp3`（102 个，完整版靠它）**；`data_sana.xp3`、`patch2.xp3` 不含剧本 |
| 文本字段 | `scenes[].texts[]`（对白）、`scenes[].selects[].text`（选项） |
| 语言维度 | domain = **目录哈希**（`SipHash-2-4(小写目录+盐)`）：`9D31BC7A…`=`cn/scn/`、`9A19D83E…`=`tw/scn/`、`BB415FD3…`=`en/scn/`；三域同构（各 102 脚本 / 23,245 行） |
| 归档优先级 | **注册时间越晚越高**（`initialize.tjs` 原文注释）。官方链顶端是 `patch.xp3><lang>/scn/`（全链最后一次注册）；散目录 `data/<lang>/scn/` 反而更低 |
| 字体 | Steam 自带官方简中，字体链已就绪；民汉的字体替换不纳入本补丁 |
| 文本产物布局 | **一脚本一文件**：`resource/<set>/scripts/<script>.jsonl`、`resource/match/pairs/<script>.jsonl`（读写统一走 `tool/textset.py`） |
| 回写 | `script/writeback.py`：字符串槽与页 `length` 均**原位改字节**，无需完整序列化器；闸 = 零改动往返 + 单串扰动 + 槽位自检 |
| 补丁形式 | **单文件 `releases/CN.xp3`**（3.1 MB）：根域 `default.tjs`（本作原文 + 钩子）在启动末尾注册 `CN.xp3>cn/scn/` ⇒ 最高优先级；脚本存 `cn/scn/` 域。卸载 = 删文件 |

## 工程约定

### 1. 文档同步要求

**任何结论性修改都必须落到文档**，不要只留在对话里：

| 改动 | 必须更新 |
|---|---|
| 文件格式/加密细节 | `doc/file-formats.md` |
| 脚本文本模型（字段含义、可翻译范围） | `doc/text-model.md` |
| 引擎行为、字体、加载顺序、补丁机制、语言维度 | `doc/engine-mechanics.md` |
| 两版差异结论、抽检结论 | `doc/version-diff.md` |
| 踩坑与返工教训 | `doc/lessons-learned.md` |
| 验收标准 | `doc/acceptance-criteria.md` |

文档只写**当前成立的最新结论**，不含探究历史；每条结论必须有验证依据（能复跑的命令或文件指针）。

### 2. 目录与脚本组织

```
tool/        长期复用的库（PSB / .scn / xp3 / Hxv4 读写、文本集），与 FHD 项目同源
script/      项目流程脚本（提取、对齐、回写、打包、校验），从项目根执行
resource/    可复用的数据产物与映射表（提取出的文本、对齐表、Hxv4 参数）
doc/         知识文档
tmp/         临时文件，任务结束即清理（不入库）
releases/    交付物（`CN.xp3`；只有 `README.txt` 入库）
```

- `tool/` 是 **Python 包**（`__init__.py`），脚本用 `from tool import psb, xp3, scn, hxv4`。
- 一次性脚本、调试输出、解包出来的原始资源、一次性 prompt 一律放 `tmp/`，任务结束删除；需要保留的中间结果移入 `resource/`。
- 所有脚本用相对于项目根的路径；从项目根执行。

### 3. 脚本开发规范

- **幂等**：脚本必须可重复运行，运行前先检查「已满足则跳过」；不得因重复运行产生不同结果。
- **回读校验**：任何写操作（重建 `.scn`、重打包 xp3）之后必须**重新解析**产物并断言内容与预期一致。
- **零改动等价性**：改写工具必须支持「不改任何内容」的往返重建，且重建后的 PSB body 与原始**逐字节相同**（压缩流允许不同）。这是验证改写逻辑正确的第一道关。
- **报错优先**：遇到不认识的 PSB 类型、长度不符、校验失败，一律抛异常并指出文件名+偏移，禁止静默跳过。
- **独立复核**：验收脚本不要与被测实现共用期望值——`script/verify_patch.py` 从 `resource/` 重建期望逐页比对产物，而不是相信打包器自检。

### 4. 编码约定

- PSB 字符串池是 **UTF-8**，中文可直接写入，**不需要**编码转换（与 AdvHD 的 Shift-JIS 项目不同）。
- 脚本名/资源名在 xp3 索引里是 UTF-16LE，在 PSB `name` 字段里是 UTF-8，读写时不要混用。
- 控制台输出：中文文档用 UTF-8；脚本 print 保持 ASCII 或显式 `sys.stdout.reconfigure(encoding='utf-8')`。
- 文本中的引擎控制码（`%NNN;`、`#RRGGBBAA;`、`#;`、`\n`）**必须原样保留**，提取时单独标注，回写时不得丢失。

### 5. 安全与备份

- **不修改** Steam 版安装目录里的原始文件（只读）；所有产物写到项目目录。
- 覆盖任何已产出的补丁文件前先备份（`*.bak`，并在 `doc/` 记录原因）。
- 从 Steam 安装目录复制大数据（`data.xp3` 等 GB 级文件）只做只读解析，不要复制。
- **不要私自提交**：分支上的 commit 由项目所有者决定；改完留工作区并给出改动清单与建议的 commit message。

### 6. 测试与验收

每个阶段结束前：
1. 跑该阶段的校验脚本（提取→`script/verify_extract.py`；对齐→`script/align_check.py`；出包→`script/verify_patch.py`）
2. 抽样人工比对（随机 10 条：文件、场景、speaker、文本）
3. 更新对应文档
4. 清理 `tmp/`

详细验收标准见 `doc/acceptance-criteria.md`。

## 环境

- Python 环境：miniforge 的 **GalRev** 环境（3.11）——本项目**只用这一个环境**。执行脚本用 `mamba run -n GalRev python script/xxx.py`。
- 本机 shell 初始化脚本不可写，mamba 每次会固定打印几行 `libmamba Error opening for writing ...mamba.bat: Permission denied`；**不影响解释器执行**，批量脚本用 `2>&1 | grep -v libmamba` 过滤，不要去改 conda 目录权限。
- 本项目的库全部是**标准库**（`zlib`/`struct`/`hashlib`/`ctypes`/`mmap`/`json`/`re`），不依赖 numpy 等。
- **禁止主动安装包**：确需新库时先说明用途并获批，只在 GalRev 环境里装。
- 游戏目录：`E:\SteamLibrary\steamapps\common\MashiroiroSymphonySANA`；民汉补丁原件：`D:\My_Code\tmp\GamePatch\MashiroiroSymphonySANA\retranslation_patch_original`（脚本里写死这两个默认值）。
- **Steam 侧文本来源（离线）**：`script/extract_text.py` + `--profile resource/hxv4/profile_live.json`，输入是 **`data.xp3` + `patch.xp3` 两个归档**（顺序不能反，后者覆盖前者）。**不启动游戏、不注入、不依赖任何外部工具**。
- 全链条命令（改文本只需重跑最后三步）：
  ```bash
  mamba run -n GalRev python script/extract_text.py "<民汉>/mashiro_sana_chs.xp3" "<民汉>/mashiro_sana_cn2.xp3" "<民汉>/patch.xp3" --out resource/chs_text
  mamba run -n GalRev python script/extract_text.py "<游戏>/data.xp3" "<游戏>/patch.xp3" --profile resource/hxv4/profile_live.json --domain 9D31BC7AA367A8F1 --out resource/steam_text_zhcn
  mamba run -n GalRev python script/verify_extract.py --data resource/steam_text_zhcn
  mamba run -n GalRev python script/align_check.py --game "<游戏>"
  mamba run -n GalRev python script/build_pairs.py --out resource/match
  mamba run -n GalRev python script/writeback.py fetch|roundtrip|apply
  mamba run -n GalRev python script/pack_patch.py
  mamba run -n GalRev python script/verify_patch.py
  ```
- **profile 与游戏构建绑定**：游戏更新后参数失效，用 FHD 项目的 `script/capture_live_crypto.py`（x32dbg，必须**从调试器启动**游戏）重抓，见 `doc/hxv4.md` §5。
- 动态分析：x32dbg（MCP 端点 `http://127.0.0.1:50301/mcp`，客户端在 FHD 项目 `tool/x32dbg_mcp.py`）。只在「重抓 Hxv4 参数」时用；日常提文本不需要调试器。
- 静态分析：IDA Pro（端点 `http://127.0.0.1:13337/mcp`）备用，当前流程不依赖。

## 参考资料

- `../../MashiroiroSymphonyFHD/MashiroiroSymphonyFHD_Retranslation_Patch/`：姊妹项目（本篇），本仓库 `tool/`、`script/`、`doc/` 的母本
- `../../GalRevserse/GalRev/docs/engines/krkr/`：krkr 引擎级通用结论与库
- `../../GalRevserse/GalgameReverse/project/krkr/`：krkr xp3 工具与笔记（`tool/xp3.py` 由 `krkr_xp3.py` 而来）
- `../../GalRevserse/YuaExtract/`：Hxv4 格式与算法的参考实现，仅作参考，不依赖
- `../../A_Sky_Full_of_Stars/`：同系列工程约定与「从汉化补丁提取文本」方法论
