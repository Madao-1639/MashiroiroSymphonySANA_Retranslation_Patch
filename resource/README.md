# 数据文件说明

本目录只放**数据产物**（提取结果、对齐表、可复用参数），文档一律在 `doc/`。

| 目录/文件 | 内容 | 生成者 |
|---|---|---|
| `chs_text/` | 民汉补丁文本提取（**118 脚本 / 28,254 行 / 11 选项**） | `script/extract_text.py` |
| `steam_text_zhcn/` | Steam 版简中域提取（**102 脚本 / 23,245 行 / 11 选项**） | `script/extract_text.py --profile … --domain 9D31BC7AA367A8F1` |
| `match/` | 行级对齐 `pairs/<script>.jsonl`（23,245 行）+ `_stats.json` | `script/build_pairs.py` |
| `hxv4/profile_live.json` | Hxv4 解密参数（**与 FHD 项目通用**，见 `doc/hxv4.md` §4.1） | 从 FHD 项目复制 |

**布局：一个脚本一个文件**（与归档条目 1:1）

```
resource/<set>/_index.json                    清单（脚本名、hash、页数、域、container…）
resource/<set>/scripts/<script>.jsonl         对白行
resource/<set>/scripts/<script>.selects.jsonl 选项（没有就不建）
resource/match/pairs/<script>.jsonl           行级对齐结果
```

读取统一走 `tool/textset.py`（`names()` / `records_for()` / `iter_records()`）。

## chs_text/（民汉补丁文本）

来源：绿茶汉化组《純白交響曲 SANA EDITION》汉化补丁
`mashiro_sana_chs.xp3`(115 脚本) + `mashiro_sana_cn2.xp3`(3 个 H 脚本) + `patch.xp3`(与 FHD 补丁同名文件的 md5 相同)。

`_index.json` 的 `container` 字段说明每条脚本来自哪个包；`mashiro_sana_cn2.xp3` 这个后续补丁提供的 3 个脚本是
`seアフター_02（Ｈ３）.txt`、`年明け２月１４日（日）_04（Ｈ２）.txt`、`１２月２４日（木）_02（Ｈ１）.txt`。

行字段见 `doc/text-model.md` §2：其中 `pages[].text` / `text_idx` / `len` 是回写要用的，
`plain` 只有 9,576 页有（Steam 侧只有 51 页，回写时不生成）。

## steam_text_zhcn/（Steam 官方简体中文）

由 `script/extract_text.py` + `--profile resource/hxv4/profile_live.json --domain 9D31BC7AA367A8F1` 离线提取，
输入是 **`data.xp3` + `patch.xp3`**（顺序不能反）。

| 项 | 值 |
|---|---|
| 脚本数 | 102（共通线 19 + PSP 美羽线 15 + 纱凪线 60 + 后日谈/日期脚本 8） |
| 对白行 / 页 | 23,245 / 23,245 |
| 选项 | 11 |
| 被覆盖的同名脚本 | 40（`data.xp3` 的残缺版本被 `patch.xp3` 覆盖，`_index.json` → `overridden`） |

- 只保留简中域。繁中/英文同结构（各 102 / 23,245），需要时用同一命令换 `--domain` 重提（约 30 秒）。
- 三域结构严格一致，只有译文与字符串池下标不同 ⇒ 对齐结论可复用，回写必须用目标域自己的 `text_idx` / `length`。

## match/（对齐表）

由 `script/build_pairs.py` 生成，勿手工编辑。判据与结论见 `doc/version-diff.md` §4 §6。

```json
{"script":"scenario_sana_route01_01.txt","scene":"*start","row":15,"page":0,"status":"aligned",
 "speaker":{"chs":"纱凪","steam":"紗凪"},"voice":{"chs":"sn1234","steam":"sn1234","equal":true},
 "text":{"chs":"「……」","steam":"“……”"},"length":{"chs":14,"steam":12},"similarity":0.5}
```

| `status` | 行数 | 含义 |
|---|---|---|
| `aligned` | 10,955 | 两侧 voice id 相同（决定性锚点） |
| `needs_review` | 12,280 | 无语音的叙述行；结构已对齐，需要**语义抽查**（不是"待对齐"） |
| `source_untranslated` | 10 | 民汉仍是日文（人名/拟声/漏译），回写前单独处理 |

`_stats.json`：`totals` + 每脚本 `{rows, voice_aligned, needs_review}` + `suggested_batches`（11 批约 10 脚本）。

## hxv4/profile_live.json

**唯一的密钥材料**，字段与获取流程见 `doc/hxv4.md` §4 §5。与 FHD 项目的那份**完全相同**，
对本作 `data.xp3` / `data_sana.xp3` / `patch.xp3` / `patch2.xp3` 全部有效（实测）。
**与游戏构建绑定**：游戏更新后必须重抓（用 FHD 项目的 `script/capture_live_crypto.py`）。

> **构建中间物不进本目录**：回写产物、解包结果、打包输入都在 `tmp/`，任务结束清空。
