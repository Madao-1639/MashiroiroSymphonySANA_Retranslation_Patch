# 纯白交响曲 SANA EDITION 汉化移植

把【绿茶汉化组】《純白交響曲 SANA EDITION》汉化补丁的剧情文本，移植到 Steam 版
[《純白交響曲 SANA EDITION》](https://store.steampowered.com/app/2737980/)（appid 2737980），替换官方简体中文内容。

> 本仓库只包含文本移植所需的分析与工具，不包含任何游戏本体文件；补丁仅供已购买正版游戏的用户使用。
> 姊妹项目：[本篇（Love is Pure White）移植](https://github.com/Madao-1639/MashiroiroSymphonyFHD_Retranslation_Patch)。

---

## 补丁状态

**尚未发布**。当前进度：文本提取、两版差异核对、对齐表已完成（见 `doc/version-diff.md`），
回写与出包（阶段 4）待做。

| 项 | 数值 |
|---|---|
| 覆盖脚本 | 102（共通线 19 + PSP 美羽线 15 + 纱凪线 60 + 后日谈/日期脚本 8） |
| 覆盖对白 | 23,245 行 / 11 个选项（= Steam 版全部剧情文本） |
| 不覆盖 | 界面词条、图像、语音；其它女主角路线（本作没有） |

---

## 前提

游戏必须是**完整版**：安装目录里除 `data.xp3` 外还要有官方 `patch.xp3`（1.10 GB，2024-06 更新）。
只有 `data.xp3` 时游戏是"路线限定"版本（40 脚本 / 10,496 行），本补丁的脚本集不匹配。

---

## 开发

```bash
# 环境：miniforge 的 GalRev（3.11），只用这一个
mamba run -n GalRev python script/extract_text.py resource  # 见 AGENTS.md 的完整命令
mamba run -n GalRev python script/verify_extract.py --data resource/steam_text_zhcn
mamba run -n GalRev python script/align_check.py --game "E:/SteamLibrary/steamapps/common/MashiroiroSymphonySANA"
mamba run -n GalRev python script/build_pairs.py --out resource/match
```

| 阶段 | 脚本 | 产物 |
|---|---|---|
| 1 提取民汉 | `script/extract_text.py <3 个明文 xp3> --out resource/chs_text` | 118 脚本 / 28,254 行 |
| 2 提取 Steam | `script/extract_text.py data.xp3 patch.xp3 --profile … --domain 9D31BC7A… --out resource/steam_text_zhcn` | 102 脚本 / 23,245 行 |
| 2 校验 | `script/verify_extract.py`、`script/align_check.py` | 结构同构 + 名字哈希覆盖 |
| 3 对齐 | `script/build_pairs.py` | `resource/match/pairs/*.jsonl` + `_stats.json` |

工程约定、目录组织、文档同步要求见 [`AGENTS.md`](AGENTS.md)；技术结论见 [`doc/`](doc/)：
引擎机制 `engine-mechanics.md`、格式 `file-formats.md`、文本模型 `text-model.md`、加密 `hxv4.md`、
两版差异 `version-diff.md`、踩坑 `lessons-learned.md`、验收 `acceptance-criteria.md`。

---

## 致谢

汉化文本：绿茶汉化组（SANA EDITION 汉化 + `mashiro_sana_cn2.xp3` 补充补丁）
技术参考：GalgameReverse、YuaExtract、GARbro、FreeMote
