# 验收标准

每一条都是**当前必须满足的判据**，格式为「判据 + 命令/依据 + 期望结果」。
没有命令或文件指针支撑的条目视为未定义，不要写进来。

## 1. 民汉文本提取

| 判据 | 命令 | 期望 |
|---|---|---|
| 脚本数 / 行数 / 选项数 | `script/extract_text.py <mashiro_sana_chs.xp3> <mashiro_sana_cn2.xp3> <patch.xp3> --out resource/chs_text` | 118 / 28,254 / 11 |
| 回读一致 | `script/verify_extract.py --data resource/chs_text --source <同样三个 xp3>` | `OK` |
| 后续补丁已合并 | `resource/chs_text/_index.json` 的 `container` 字段 | 3 个脚本来自 `mashiro_sana_cn2.xp3` |
| 抽样 | 随机 10 条看 `speaker` / `scene` / 文本 | 与原文一致，控制码未丢 |

## 2. Steam 侧提取与结构核对

| 判据 | 命令 | 期望 |
|---|---|---|
| 文本提取 | `script/extract_text.py <data.xp3> <patch.xp3> --profile resource/hxv4/profile_live.json --domain 9D31BC7AA367A8F1 --out resource/steam_text_zhcn` | 102 / 23,245 / 11 |
| 提取质量 | `script/verify_extract.py --data resource/steam_text_zhcn` | `OK`（官方 `length` 口径、`plain` 罕见、40 条被覆盖是已知现象） |
| 结构同构 | `script/align_check.py --game "<游戏目录>"` | 行数 / `(scene,row)` / 页数 / voice id / `char_id` / speaker / 选项全等；名字哈希 102/102 |
| 不可移植清单 | 同上输出 | 16 脚本 / 5,009 行（其它女主角路线），不进补丁 |
| 语义抽检 | 抽 `needs_review` 行，从脚本尾部往前取，发现疑点用二分定位 | 无 `offset`；结论与记录见 `doc/version-diff.md` §9、`resource/match/offsets.jsonl` |

## 3. 回写与出包

| 判据 | 命令/依据 | 期望 |
|---|---|---|
| 零改动往返 | `script/writeback.py roundtrip` | 102/102 脚本 body 逐字节相同；单串 grow/shrink 后仍可解析 |
| 槽位自检 | 同上 | 每页 `length` 内联槽位置与提取结果逐页一致 |
| 回写覆盖 | `script/writeback.py apply --jobs 8` → `tmp/_report_apply.json` | 23,245 行 + 11 选项；`page_kept_official=10`（民汉残留日文） |
| 打包自检 | `script/pack_patch.py` | payload 逐字节、adler、页数全通过；条目名哈希 102/102 |
| 独立复核 | `script/verify_patch.py` | 23,238/23,245 页与期望一致；余 7 页只允许是 `_report_apply.json` 里的已知项（引用宽度 4 页、页对象共享 3 页） |
| 可复跑 | 清空 `tmp/` 后重跑 fetch → apply → pack_patch → verify_patch | ~84 s，产物 md5 不变 |
| 产物体积 | `releases/CN.xp3` | 1 警告占位 + 1 钩子 + 102 脚本 |

## 4. 实机

| 判据 | 期望 |
|---|---|
| 简中路由 | 语言选「简体中文」进入游戏，剧情文本为民汉译文 |
| 语音对应 | 共通线 / 纱凪线 / 后日谈各抽 1 段：语音与文本不属于同一行的情况为零 |
| 存档读写 | 存档、读档、快进与自动播放的节奏正常 |
| 卸载 | 删除 `CN.xp3` 后官方文本恢复，不需要验证文件完整性 |

## 5. 本作专属前提

1. 钩子载体取自本作 `data.xp3` 的 `main/default.tjs`（25,398 B），不得用姊妹项目的版本。
2. 警告占位（entry 0）从官方 `patch.xp3` 的 entry 0 照抄（157 B，`fsize=910`，`seg_flags=0`，带映射记录）——`data.xp3` 的 entry 0 没有映射记录。
3. 游戏必须装齐官方 `patch.xp3`：缺它时游戏只有 40 脚本 / 10,496 行，与本补丁的脚本集不适配。

## 6. 交付物

- `releases/CN.xp3`（单文件；卸载 = 删除）
- `releases/README.txt`（安装 / 卸载 / 更新 / FAQ，含 `patch.xp3` 前提）
