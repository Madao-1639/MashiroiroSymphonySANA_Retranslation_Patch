# 两版文本差异（SANA EDITION）

> 状态：**已完成**（2026-10-29）。民汉侧与 Steam 侧都已提取，逐脚本结构核对结果为「零结构差异」，
> 可执行命令：`script/align_check.py`。

## 1. 两版是什么

| | 民汉补丁（绿茶汉化组 SANA EDITION 汉化） | Steam 版（appid 2737980） |
|---|---|---|
| 组成 | `mashiro_sana_chs.xp3`(264 MB, 115 脚本) + `mashiro_sana_cn2.xp3`(141 KB, 3 个 H 脚本) + `patch.xp3`(15 MB，与 FHD 补丁同名文件 **md5 相同**) | `data.xp3` + **`patch.xp3`**（1.10 GB，2024-06 官方补丁，补齐内容用）+ `patch2.xp3`（2.09 GB，无剧本） |
| 母体 | 日文原版《純白交響曲 SANA EDITION》 | 同一部游戏的 Steam 版 |
| 文本载体 | 明文 xp3 里的 `*.scn`（`mdf\0` 外壳 + zlib + PSB） | Hxv4 加密 xp3 里的裸 PSB |
| 每个脚本的副本数 | 1 | **3**（`cn/scn/`、`tw/scn/`、`en/scn/` 三域各一份，结构完全相同） |
| 提取结果 | `resource/chs_text/`：**118 脚本 / 28,254 行 / 11 选项** | `resource/steam_text_zhcn/`：**102 脚本 / 23,245 行 / 11 选项** |

### 1.1 `patch.xp3` 是完整版的前提（关键）

Steam 版**只装 `data.xp3` 时内容是不完整的**：`cn/scn/` 只有 40 个脚本 / 10,496 行，而且是"路线限定"版本
（`scenario_01/02` 被裁、`scenario_sana_route01..04` 各少十几到上百行、`route05..08` 与 `seアフター_*` 整段缺失）。
官方 `patch.xp3`（2024-06-03，1.10 GB）覆盖 40 条同名脚本并补上另外 62 条 ⇒ 102 脚本 / 23,245 行。

判据（可复跑）：分别只提 `data.xp3` 与 `data.xp3+patch.xp3`，行数 10,496 vs 23,245；
残缺版与民汉的差集 = 23 个脚本共 858 行"民汉有、Steam 无"，而完整版与民汉 **零差异**。

⇒ 本项目**要求游戏装有官方 `patch.xp3`**（Steam 正常安装即自带）。缺它时补丁仍不会崩，但会注入游戏并不会请求的脚本、且被覆盖的 40 个脚本会带上多出来的行。

## 2. Steam 侧怎么取到的（结论，可复跑）

Steam 版 xp3 全为 Hxv4 加密，参数 `resource/hxv4/profile_live.json` **与 FHD 项目通用**（实测映射表 tag 通过、
内容过滤器正确，`data.xp3`/`patch.xp3` 全量条目都能解）。整条链路离线：

```bash
mamba run -n GalRev python script/extract_text.py \
    "<游戏目录>/data.xp3" "<游戏目录>/patch.xp3" \
    --profile resource/hxv4/profile_live.json --domain 9D31BC7AA367A8F1 --out resource/steam_text_zhcn
```

- 两个归档的**顺序不能反**：`patch.xp3` 覆盖 `data.xp3`（工具按"后传入者胜"处理，`_index.json` 的 `overridden=40` 就是这 40 条覆盖）。
- `patch2.xp3` 与剧本无关：22,118 条目里 `cn/scn`/`tw/scn`/`en/scn` 三域**零条目**（域是高编号的图/音/语音目录）。
- 三域同构：`cn` / `tw` / `en` 各 102 脚本 / 23,245 行 / 11 选项，脚本集与行数逐脚本相同 ⇒ 匹配结论可跨域复用，回写要用目标域自己的 `text_idx`/`length`。
- `data_sana.xp3`（704 MB / 3,799 条目）**不含剧本**：全部落在根域，其中只有 9 个 PSB 且都不是 `scenes` 结构（UI/配置类）。

## 3. 脚本清单差异

| 项 | 数量 |
|---|---|
| 两边都有 | **102** |
| 仅 Steam 有 | **0** |
| 仅民汉有 | **16**（5,009 行） |

Steam 的 102 个脚本 = 共通线 `scenario_01..19`（19）+ PSP 追加美羽线 `scenario_miu_psp_route01_*`（15）
+ 纱凪线 `scenario_sana_route01..08`（60）+ 后日谈/日期脚本 8（`seアフター_01..03`、`年明け２月０９日（火）_01`、
`年明け２月１４日（日）_04（Ｈ２）`、`年明け２月１４日（日）_05`、`１２月２４日（木）_02（Ｈ１）`、`１２月２４日（木）_03`）。

仅民汉有的 16 个是**其它女主角路线的碎片**（本作没有这些路线，对应 FHD 本篇的个人线/H 场景）：

| 类别 | 数量 | 行数 |
|---|---|---|
| `scenario_airi_route01_{01,09,17,23}` | 4 | 1,698 |
| `scenario_ange_route01_{01..08,17,19}` / `scenario_ange_route02_10` | 11 | 2,820 |
| `scenario_sakuno_route01_10` | 1 | 491 |

⇒ 这 16 个脚本**不参与移植**（`script/align_check.py` 会把它们列出来并排除）。

## 4. 结构级对照（102 个共有脚本）

| 指标 | 结果 |
|---|---|
| 对白行 | 23,245 = 23,245，**逐脚本行数全等** |
| `(scene, row)` 键序列 | **102/102 全等**（无重排、无增删） |
| 每行页数 | 23,245 行全等 |
| 有语音的行（`pages[].voice`） | **10,964，voice id 100% 相同** |
| `char_id` | 23,245 页全等 |
| `speaker` 有无 | 23,245 行全等 |
| 选项 `index` | 11/11 全等（无双侧不一致） |
| 名字哈希覆盖 | 102/102 脚本的 `blake2s(<名字>.scn)` 在游戏 `cn/scn` 域存在 |

⇒ **位置映射成立**：`(script, scene, row, page)` 四元键一一对应，回写不需要重排或模糊对齐，
与 FHD 本篇阶段的结论同类（`doc/text-model.md` §6.3）。

## 5. 文本层面的差异（影响回写）

1. **引号风格**：民汉 `「……」`，官方简中 `“……”`（繁中 `「」`）。回写采用民汉风格。
2. **分页元数**：民汉 28,254 页里有 **9,576 页是 5 元分页**（`text` + 去换行的 `text_plain`），
   Steam 23,245 页里只有 **51 页**带 `plain`。回写只写 `text` + `length`，**不要**生成 `plain`。
3. **`alt_speaker`（逐页说话人覆盖）**：Steam 有 15,064 处、民汉 0 处，方向是**单向**的
   （没有"民汉有、Steam 无"的情况）⇒ 回写时保留 Steam 自己的 `alt_speaker` 不动即可。
4. **`length` 口径不同**：民汉把 `%NNN;`、`#RRGGBBAA;` 等标记算进长度，官方不算
   （Steam 侧 24 个脚本共 51 页可证）。回写统一用 `tool/scn.py::page_length()` 按**目标版口径**重算。
5. **民汉自身残留**：28,254 行里有 10 行仍含假名（落在 102 个共有脚本内），属于人名/拟声或漏译，
   回写前需单独处理（`resource/match/_stats.json` 的 `source_untranslated=10`）。
6. **与 FHD 补丁的关系**：民汉 SANA 补丁的 118 个脚本 **FHD 补丁全都包含**（FHD 277 脚本是其超集），
   其中 100 个逐行完全相同，18 个是后续修订（`庆功会/庆功宴`、`呆在/待在`、`目光/眼光`、`２７号/27号`…）
   ⇒ 以 **SANA 补丁为准**，FHD 那份不参与本仓库。

## 6. 对匹配阶段的结论（已完成）

对齐表：`resource/match/pairs/<script>.jsonl`（23,245 行），统计：`resource/match/_stats.json`。

| status | 行数 | 含义 |
|---|---|---|
| `aligned` | 10,955 | 两侧 voice id 相同（决定性锚点） |
| `needs_review` | 12,280 | 无语音的叙述行，结构与行号已对齐，需要语义抽查 |
| `source_untranslated` | 10 | 民汉仍是日文（见 §5.5） |

因为结构零差异，`needs_review` 不再是"要不要对齐"的问题，而是"抽查译文质量"的问题；
`_stats.json` 的 `suggested_batches` 给出 11 批约 10 脚本的切分，供人工/LLM 抽查使用。

## 7. 与 FHD 项目的方法差异（给后来者）

| 项 | FHD 本篇 | 本仓库 |
|---|---|---|
| Steam 脚本数 / 行数 | 199 / 53,218（+繁中兜底域 202 / 53,218） | **102 / 23,245**（三域一致） |
| 民汉脚本数 | 277 / 69,544 | 118 / 28,254 |
| 不可移植脚本 | 76（SANA 追加线 + PSP 美羽线 + dummy） | **16**（其它女主角路线碎片） |
| Hxv4 参数 | 从 Steam FHD 抓取 | **直接复用 FHD 那份**，无需重抓 |
| 对齐 | 位置映射 + 抽样复核 | 位置映射（本轮结构核对已通过） |
| 补丁钩子载体 | `data.xp3` 的 `main/default.tjs` | `data.xp3` 的 `main/default.tjs`（**SANA 自己的版本，25,398 B，与 FHD 差 5 行标题串**） |
| 警告占位条目（entry 0） | 有映射记录，可从 `data_fhd.xp3` 照抄 | **`data.xp3` 的 entry 0 没有映射记录**，打包时要直接读 `File` 块字段 |
