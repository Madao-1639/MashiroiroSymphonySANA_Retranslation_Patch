# 验收标准（SANA EDITION）

每个阶段结束前必须同时满足：**能复跑的命令通过** + **抽样人工比对** + **文档已更新** + **`tmp/` 已清理**。
下面每一条都给出可执行的判据；没有命令的条目视为未验收。

## 阶段 1：民汉文本提取 ✅（2026-10-29）

| 判据 | 命令 | 结果 |
|---|---|---|
| 脚本数 / 行数 / 选项数 | `script/extract_text.py <3 个明文 xp3> --out resource/chs_text` | 118 / 28,254 / 11 |
| 回读一致 | `script/verify_extract.py --data resource/chs_text --source <3 个 xp3>` | `OK` |
| 后续补丁已合并 | `resource/chs_text/_index.json` 里 3 个脚本的 `container` = `mashiro_sana_cn2.xp3` | ✔ |
| 抽样 | 随机 10 条看 `speaker` / `scene` / 文本是否合理 | ✔ |

## 阶段 2：两版差异与对齐 ✅（2026-10-29）

| 判据 | 命令 | 结果 |
|---|---|---|
| Steam 文本提取 | `script/extract_text.py <data.xp3> <patch.xp3> --profile … --domain 9D31BC7AA367A8F1` | 102 / 23,245 / 11 |
| 提取质量 | `script/verify_extract.py --data resource/steam_text_zhcn` | `OK`（3 条 WARN 为已知现象：官方 `length` 口径、`plain` 罕见、40 条被覆盖） |
| **结构同构** | `script/align_check.py --game "<游戏目录>"` | `OK`：行数/`(scene,row)`/页数/voice id/`char_id`/speaker/选项全等；名字哈希 102/102 |
| 不可移植清单 | 同上输出 | 16 脚本 / 5,009 行（其它女主角路线） |
| 抽样 | 20 条：同一 `(script, scene, row)` 两侧是同一句的不同译本 | ✔ |

## 阶段 3：对齐表 ✅（2026-10-29）

| 判据 | 命令 | 结果 |
|---|---|---|
| 对齐表 | `script/build_pairs.py --out resource/match` | 23,245 行；`aligned` 10,955 / `needs_review` 12,280 / `source_untranslated` 10 |
| 完整性 | 每脚本 `pairs` 行数 == 该脚本对白行数 | ✔ |
| 语义抽查 | `needs_review` 行无语音，按 §`resource/README.md` 的抽样口径核对 | 待项目所有者组织 |

> 与 FHD 不同：本阶段不存在"要不要对齐"的判定，只存在"译文质量抽查"，因为结构零差异。

## 阶段 4：回写与出包（未开始）

迁入 FHD 项目的 `script/writeback.py` + `script/pack_patch.py` 后，必须满足：

| 判据 | 说明 |
|---|---|
| 零改动往返 | 不改任何内容重建 PSB body，与原始**逐字节相同** |
| 单串扰动 | 任选一条文本变长/变短，重建后仍可解析、池内容与预期一致 |
| 槽位自检 | 每页 `length` 内联槽位置与提取结果逐页一致 |
| 回写覆盖 | 23,245 行 + 11 选项全部写入；`script/writeback.py verify` 通过 |
| 补丁包自检 | `CN.xp3` 条目名哈希 102/102 命中游戏 `cn/scn` 域；回读 payload 逐字节相同；adler 正确 |
| 实机 | 简中语言下开场、共通线、纱凪线、后日谈各抽 1 段，确认文本替换、无乱码、语音对应、存档正常 |
| 卸载 | 删除 `CN.xp3` 后官方文本恢复 |

### SANA 专属检查项（FHD 项目没有的）

1. **钩子载体必须取自本作** `data.xp3` 的 `main/default.tjs`（25,398 B），不得照抄 FHD 的（25,548 B）。
2. **entry 0 无映射记录**：`CN.xp3` 的警告占位要自己造 record + 直接读 `File` 块字段。
3. **游戏必须装有官方 `patch.xp3`**：交付说明里要写明；缺它时游戏只有 40 脚本 / 10,496 行（残缺版）。
4. `data_fhd.xp3` 在本作**不存在**，所有"从 `data_fhd.xp3` 照抄"的做法都要改成本作来源。

## 交付物

- `releases/CN.xp3`（单文件，卸载 = 删除）
- `releases/README.txt`（安装/卸载/更新/FAQ；必须写明 `patch.xp3` 前提）
- 文档：`doc/version-diff.md`、`doc/acceptance-criteria.md` 更新到与产物一致的最终状态
