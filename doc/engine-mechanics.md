# 引擎与补丁机制

本文写 krkrz + M2「MDF」这套组合的**运行时行为**：引擎身份、脚本执行层、归档加载与覆盖、语言维度、字体，以及做移植时的操作顺序。格式细节见 [file-formats.md](file-formats.md)，加密细节见 [hxv4.md](hxv4.md)。

## 1. 引擎身份

| 项 | 本作（SANA EDITION） | 姊妹项目（FHD 本篇） |
|---|---|---|
| Steam 主程序 | `mashiro_sana.exe`（4,583,128 B，x86） | `mashiro_fhd.exe`（4,551,896 B） |
| 民汉主程序 | `mashiro_sana_chs_dl.exe` / `mashiro_sana_chs_pkg.exe`（4,423,168 B）+ `mashiro_sana_chs.dll`（56,320 B） | `mashiro_fhd_chs.exe`（4,392,448 B）+ `.dll` |
| SteamStub | `.text` 熵 8.00、加密 `.text`、多出 `.bind` | 同 |
| 引擎修订信息 | 民汉补丁目录里**没有** `enginerev.ini`（FHD 那份是 `Revision 1005 / 2023-06-12`） | 有 |

两作是**同一份 krkrz 基础构建**的两个发行体。判据不是 exe 字节相同（不同），而是运行时脚本链的静态一致性：
把 Steam 侧的 `system/initialize.tjs` 解出来与 FHD 那份逐行 diff，只有标题字符串、语言段顺序、以及被删掉的
`scenario2/{ALLAGE,R18}/<lang>/` 三行不同，`ratio=0.939`；`main/config.tjs`、`main/custom.tjs`、`main/storages.tjs`、
`sysscn/append_init.tjs`、`sysscn/override.tjs` **逐字节完全相同**（`ratio=1.000`）。

krkrz 指纹：`TVPSelectXP3FileOrFolder`、`TVPCannotFindXP3Mark`、`forcedataxp3`、RTTI `tTVPXP3Archive`（同一套）。
引擎级细节的完整取证在 FHD 项目文档里，本文件只保留与本作相关的取值。

## 2. 脚本执行层

```
.scn（mdf + zlib 外壳）或裸 PSB → PSB 对象图 → plugin/psbfile.dll 解释执行
```

- 剧情**不走 KAG**（`.ks`）：`KAGParserEx.dll` 只负责系统级 KAG 脚本，剧情由 `psbfile.dll` 消费 PSB。
- `plugin/PackinOne.dll` 是归档/加密侧（RTTI 可见`BasicCryptFilter<BlockStreamCipher<ChaCha<DJBernsteinStreamCipher>>>`、`ArchiveStreamBuf`），密钥不在其中，由 exe 内嵌 bootstrap 模块运行时算出（见 [hxv4.md](hxv4.md) §5）。
- 脚本对象模型（`scenes/texts/selects`）见 [text-model.md](text-model.md)。

## 3. 归档加载与覆盖

### 3.1 启动链路（谁在跑）

```
exe
 └─ 根域 startup.tjs（编译字节码，1300 B）
     └─ Scripts.execStorage("system/Initialize.tjs")   ← 真正的启动脚本（72,544 B，域 system/）
         ├─ addTools("system") / addTools("sysscn")                (:252)
         ├─ subfolders 循环 addArchive(name)                        (:289)
         ├─ Scripts.execStorage("Storages.tjs")                     (:319，官方定制点)
         ├─ useArchiveIfExists("patch.xp3")                          (:325)
         ├─ patch2.xp3 … 只探测存在性，addAutoPath 被注释            (:355)
         ├─ override.tjs / custom.tjs …（KAG 脚本层）
         ├─ askLanguage() → language ∈ {EN,TW,CN}                   (:830)
         ├─ patch2.xp3> + 40 余个 patch2.xp3>voice/…               (:870)
         ├─ 语言段落：松散目录 + data.xp3>/patch.xp3> 子目录前缀      (:1055–1108)
         └─ KAGLoadScript("default.tjs") → kag.process("first.ks")  (:1113)
```

要点：

- `initialize.tjs` 在 `data.xp3` 里位于 **`system/` 域**（不是根域），由 `startup.tjs` 显式点名。
- `default.tjs` 在启动里被加载**两次**：一次早（`override.tjs` 顶层），一次晚（`Initialize.tjs:1113`，此时语言段落已注册完）。晚那次按 `cn/main/default.tjs` 等语言域解析，与早那次的 `main/default.tjs` 是**不同文件**。
- 官方配置/定制文件：`main/storages.tjs`（`AppendDataConfig` + `addArchive("dmmcloud"/"fgimage2"/"anim")` + `KAGLoadScript("append_init.tjs")`）、`main/custom.tjs`（`SetupAppendData()` 在 :216）。

### 3.2 优先级 = 注册顺序（越晚越高）

`initialize.tjs` 原注释：「フォルダ登録及びアーカイブの検索と使用（**後に登録した物ほど検索の優先度が高くなります**）」。
查找时按注册时间**从新到旧**遍历 autopath，第一个命中的赢。

`xxx.xp3>` 的 `>` 是「归档 → 归档内目录」的分隔符：`A.xp3>` = 归档根，`A.xp3>cn/scn/` = 归档内该目录。
**优先级与有没有 `>` 无关**，只与注册时间有关。

已知的注册时间顺序（同一段代码里，行号即时间顺序）：

| 时间 | 来源 | 注册的 autopath |
|---|---|---|
| 最早 | exe | `data.xp3>` |
| 早 | `Initialize.tjs:252` | `system/`、`sysscn/`（+ 同名 `.xp3` 若存在） |
| 早 | `Initialize.tjs:289`（subfolders 27 项，含 `EN`/`CN`/`TW`） | `<name>/`，存在则 `<exePath><name>.xp3>` |
| 早 | `Initialize.tjs:319` → `main/storages.tjs` | `dmmcloud` / `fgimage2` / `anim` 归档 + `setupSubFolders` 里那一批 |
| 早–中 | `custom.tjs:216` → `SetupAppendData()` → `append_init.tjs` | `patch_append<id>.xp3` 经 `useArchiveIfExists` 注册；`AppendDataConfig` 定义 `patch_append1 = data_sana`、`patch_append2 = data_fhd` ⇒ 实际注册 `data_sana.xp3>`（存在）、`data_fhd.xp3>`（**本作不提供该文件，跳过**） |
| 中 | `Initialize.tjs:325` | `patch.xp3>` |
| 晚 | `Initialize.tjs:870` | `patch2.xp3>` + 40 余个 `patch2.xp3>voice/…` |
| 最晚（官方） | `Initialize.tjs:1055–1108` | `scenario2/{ALLAGE,R18}/<lang>/`、`data/<lang>/{scn,scenario,sysscn,main,…}`、`data/patch/<lang>/scn/`、`data.xp3><lang>/scn/`、**`patch.xp3><lang>/scn/`（整条链最后一次）** |

⇒ 对**裸名**剧情脚本（`scenario_01.txt.scn`）而言，官方链的顶端是 `patch.xp3>cn/scn/`；同批里 `data/<lang>/scn/`、`data/patch/<lang>/scn/` 这些**松散目录反而排在前面（更低）**，所以往 `data/patch/CN/scn/` 放文件**不会生效**（2026-10 实测，与"目录写错"无关，是排序问题）。

### 3.3 名字与域的规范化（否则查不到）

| 项 | 规则 | 判据 |
|---|---|---|
| 资源名 | **小写化**后参与哈希（`system/Initialize.tjs` → `initialize.tjs`） | 归档里实际条目名全小写；大写 S 的 `Storages.tjs` 任何归档都查不到，而小写 `storages.tjs` 命中 `main/` 域 |
| `file_hash` | `BLAKE2s-256(UTF16LE(小写名字) + UTF16LE(盐))`，盐 = 映射表里的 `HashDomain`（本作 `xp3hnp`） | 199/199 脚本名重算一致 |
| `domain_hash` | `SipHash-2-4(UTF16LE(小写目录 + 盐), key = 16 个 0 字节)`；**根目录 = 只哈希盐** | 实测反查见 §4.1 |
| 目录写法 | 带结尾 `/`、小写 | `system`（不带 `/`）算出另一个值，对不上 |
| 前缀来源 | 请求名是**裸名**（`system/` 域的 `StorageData`：`path += storage + ".scn"`），目录前缀只来自 autopath | `tmp/dx_text` 里全项目没有代码拼 `cn/scn/…` |

### 3.4 不改官方包也能拿到最高优先级（本项目采用的方案）

三条路都试过，只有第三条成立：

| 方案 | 结果 |
|---|---|
| 往 `data/patch/CN/scn/`、`data/CN/scn/` 放松散 `.scn` | ✗ 同批内被 `data.xp3><lang>/scn/`、`patch.xp3><lang>/scn/` 压住 |
| 用 `patch_appendN.xp3` / `patch2.xp3` / `data_fhd.xp3` 当载体 | ✗ 注册时间都早于 `patch.xp3><lang>/scn/`（`data_fhd.xp3` 里的脚本从未生效，已实测） |
| 自命名归档 `retranslate_zhcn.xp3` | ✗ 名字不在任何注册清单里（exe 无 `*.xp3` 通配） |
| **`CN.xp3` + 载体钩子**（本项目交付形态） | ✓ 见下 |

做法（`script/pack_patch.py` + `script/patch_hook.tjs`）：

1. `CN` 在 subfolders 清单里 ⇒ 存在 `<exePath>CN.xp3` 时引擎会自己注册 `CN.xp3>`（早于 `patch.xp3>`）。
2. 在 `CN.xp3` **根域**放一份 `main/default.tjs`（官方原文 + 追加钩子）。该文件在 `patch.xp3`/`patch2.xp3` 里**不存在**，而 `CN.xp3>` 比 `data.xp3` 新 ⇒ 稳定命中我们的副本；官方内容逐字节保留，只追加；早、晚两次加载都等价于原版行为（晚那次走 `cn/main/default.tjs`，不受影响）。
3. 钩子用 `System.addContinuousHandler` 等启动走完（`MessageDefaultFontFace` 有值，兜底 300 帧），再 `Storages.addAutoPath(System.exePath + "CN.xp3>cn/scn/")` —— **整条注册链的最后一次** ⇒ 最高优先级。
4. 脚本存 `cn/scn/` 域（等价于官方的 `patch.xp3>cn/scn/` 布局），只影响 `cn/scn/` 命名空间：配置、存档、其它资源都不经过我们的包（存档是 `saveDataLocation/<name><tag>.ksd` 绝对路径）。
5. 卸载 = 删除 `CN.xp3`；游戏目录内没有其它改动。

#### 3.4.1 本作（SANA EDITION）的实现要点

机制完全沿用，但有**三处与 FHD 不同**，打包时必须照做：

| 项 | FHD 本篇 | 本作 SANA EDITION |
|---|---|---|
| 钩子载体基座 | `data.xp3` 的 `main/default.tjs`（25,548 B） | `data.xp3` 的 `main/default.tjs`（**25,398 B**，与 FHD 差 5 行：`System.title`、语言/标题串）；必须用本作的，不得照拄 FHD 的 |
| 警告占位 entry 0 | 直接从 `data_fhd.xp3` 照拄（有映射记录） | `data.xp3` 的 entry 0 **没有映射记录**（`_bind_records` 的 offset=1 分支），打包时要自己造一条 record，并直接读 `File` 块的 `info`/`segm` 字段（payload 157 B，`fsize=910`，`seg_flags=0`） |
| 优先级前途 | 官方链顶端 `patch.xp3>cn/scn/` 永命中 | 官方 `patch.xp3` **真实存在**（1.10 GB，102 条脚本）⇒ 钩子必须确认注册时间晚于它；启动后追加的 `CN.xp3>cn/scn/` 满足这一条件 |

另：本作脚本集必须按 **`data.xp3` + `patch.xp3` 两个归档**计算（后者覆盖同名脚本并补齐 62 条），
只看 `data.xp3` 会得到残缺的 40 脚本集。

### 3.5 已签名包与明文包

`*.sig` 是 RSA-PSS 签名（`-- SIGNATURE - SHA256/PSS/RSA --`）：**不修改已签名原包、只新增未签名包**即可绕过校验风险。
正式交付只新增 `CN.xp3` 一个文件，不碰 `data.xp3`（1.72 GB）/`patch.xp3`（1.9 GB）/`patch2.xp3`（1.66 GB）。

> 补充事实：本作 Steam 版全部归档都是 Hxv4 加密；重建后的包仍必须是 Hxv4（写法见 [hxv4.md](hxv4.md)、
> `tool/hxv4_write.py`）。把改好的文件打进**明文** xp3 也能被引擎读（日文原版自带明文 `unencrypted.xp3`），
> 但本作实测明文包在脚本查找链上不占优，故未采用。

### 3.6 patch2.xp3 与脚本无关

本作 `patch2.xp3`（2.09 GB，22,118 entry，2024-06-21）与剧情脚本**没有交集**：其域集合（`81961dcb…`/
`691498b6…`/`1ad5da71…`/`213d9b94…` 等）与脚本三域零重叠，`cn/scn`/`tw/scn`/`en/scn` 三域 **0 条目**。
⇒ 脚本提取只需 **`data.xp3` + `patch.xp3`**；`data_sana.xp3`（704 MB / 3,799 entry，全在根域）同样不含剧本，
其中只有 9 个 PSB 且都不是 `scenes` 结构（UI/配置类）。

## 4. 域（`domain_hash`）= 目录哈希，语言只是目录名的一部分

Hxv4 映射表按 `domain_hash`（8 字节）分组，而 `domain_hash` 是**目录路径的哈希**（§3.3），所以「语言」在本作里的表现是**目录**：脚本都放在 `<lang>/scn/` 下，即 `cn/scn/`、`tw/scn/`、`en/scn/`。

### 4.1 已反查的域

| domain | 目录 | 用途 |
|---|---|---|
| `94D4A97C61498621` | 根 `''` | 根域（`startup.tjs`、警告占位等） |
| **`9D31BC7AA367A8F1`** | **`cn/scn/`** | 简体中文剧情脚本（`data.xp3` 40 + `patch.xp3` 102，合计 102） |
| **`9A19D83EC5BE3036`** | **`tw/scn/`** | 繁体中文剧情脚本（102） |
| **`BB415FD37CFB564A`** | **`en/scn/`** | 英文剧情脚本（102） |
| `BC6792F831D5FBD5` | `system/` | `initialize.tjs` 等内核脚本 |
| `F051058EFED71C42` | `main/` | `config.tjs`、`custom.tjs`、`default.tjs`、`storages.tjs`… |
| `E7D6DA05A2AF355F` / `9A2AEC6E659341BE` / `A801C704E74B1C0D` | `cn/main/` / `en/main/` / `tw/main/` | 语言版 `default.tjs` 等 |
| `902994BB1913D6F2` | `scenario/` | `.ks` 剧情脚本 |
| `9418D6C07072C261` | `sysscn/` | `first.ks`、`override.tjs`、`append_init.tjs`… |
| `AF5FB049…` / `F12B4AAE…` | `uipsd/` / `tw/uipsd/` | UI 素材定义 |

反查方法：`tool/hxv4hash.domain_hash(目录)` 与映射表里的 8 字节比较（`script/` 里的打包脚本复用同一实现；另有参考实现 `GalRevserse/YuaExtract/docs/hxv4/02-资源名与哈希.md`，本表与之一致）。

### 4.2 三域脚本集与结构

| domain | 语言 | 首句示例 | 脚本数 | 对白数 |
|---|---|---|---|---|
| `9D31BC7A…` | 简体中文 | `“啊！”` / `是否观看纱凪的后日谈？` | 102 | 23,245 |
| `9A19D83E…` | 繁体中文 | `「啊！」` / `是否觀看紗凪的後日談？` | 102 | 23,245 |
| `BB415FD3…` | 英文 | `"Ah!"` / `Read the Sana After Story?` | 102 | 23,245 |

- **三域完全同构**：脚本集、逐脚本行数、页数、`voice` id、`char_id`、选项 `index` 全部相同；
  只有译文与字符串池下标（`text_idx`）不同 ⇒ 一域上算出的 `(script, scene, row, page)` 对齐可跨域复用，
  但回写必须用目标域自己的 `text_idx` / `length`（见 `doc/text-model.md` §7.1）。
- 与 FHD 不同，本作三域**没有**“某域多几个脚本”的情况（FHD 的繁中多了 3 个 `seアフター_*`；本作三个域都有这 3 个）。
- 本项目只保留简中一份产物：`resource/steam_text_zhcn`（需要繁中/英文时同命令换 `--domain` 重提，约 30 秒）。
- **按域取数要在解密前过滤**：`domain_hash` 在映射表里，先跳过其它域的 entry 再解密，全量提取从十几分钟降到几十秒。
- **【跨作需重验】** 别把「域」当成语言：域 = 目录哈希，换作别的游戏，目录命名不同则域也不同。

## 5. 字体

本作 Steam 版**自带官方简中**（`cn/scn/` 三域齐全），字体链已就绪：不动任何字体也能直接显示移植后的简体中文。
项目验收时只需确认「官方简中能正常显示」即可（本项目交付的不包含字体修改）。

民汉补丁那边不改图片、只靠 `embfontlist.tjs` / `deffontmap.tjs` / `prerenderfontex.tjs` 把默认字体重定向到自带思源黑体
（`sourcehansansjp-bold.otf`，字面名如`純白交響曲 SANA EDITION`）；这套文件装在本作的民汉 `mashiro_sana_chs.xp3` 内，
**不进入移植范围**（我们要的效果是“官方排版 + 民汉文本”，字体替换会改变观感，属于可选衍生）。

> 注：`data.xp3` 的 `main/` 域里**没有** `deffontmap.tjs` / `embfontlist.tjs`（已按名字哈希查过），
> 说明 Steam 侧的字体映射走的是别处（`lang_*` 通配或 `cn/main/` 语言域）；本项目不需要动它。

## 6. 操作顺序（做一次文本移植）

| 步 | 做什么 | 落点 |
|---|---|---|
| 1 | 判型：确认 krkr + MDF 脚本 + 归档是否 Hxv4 变体 | [file-formats.md](file-formats.md) §0 |
| 2 | 明文归档直接读；加密归档用已抓到的参数（**本作与 FHD 共用同一份**，无需重抓） | [hxv4.md](hxv4.md) §4、`resource/hxv4/profile_live.json` |
| 3 | 解包：描述符 → 映射表 → 逐条目「zlib → 过滤器 → 文本打散」 | `script/unpack_xp3.py`、`tool/hxv4.py` |
| 4 | 提文本：按魔术字节识别脚本，取 `scenes[].texts` / `selects`，按语言域与归档优先级取唯一副本（**本作：`data.xp3` + `patch.xp3`，后传者胜**） | `script/extract_text.py`、`tool/scn.py` |
| 5 | 校验：零改动往返逐字节一致、`length` 口径、标记数守恒；对齐同构性 | `script/verify_extract.py`、`script/align_check.py` |
| 6 | 回写：字符串池级重写（数量不变）；要改整数再补完整对象图序列化器 | `tool/psb.py` |
| 7 | 出包：把改好的脚本按真名打进普通 xp3，覆盖进游戏目录 | [engine-mechanics.md](engine-mechanics.md) §3 |
