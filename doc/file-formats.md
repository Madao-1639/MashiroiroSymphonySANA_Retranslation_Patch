# 文件格式规范

本作两版都是 **krkrz + M2「MDF」脚本**：krkrz 提供归档与 TJS 运行时，M2 的工具链把剧情编译成PSB 对象图，由 `psbfile.dll` 解释执行。本文只写「磁盘上长什么样」。

## 0. 判型速查

| 判据 | 观察点 |
|---|---|
| 引擎 | `plugin/` 里有 `psbfile.dll`、`KAGParserEx.dll`、`krmovie.dll`、`layerExDraw.dll`、`PackinOne.dll`；exe 内有 `TVPXP3Archive`、`TVPSelectXP3FileOrFolder` 等 `TVP*` 字符串 |
| 剧情载体 | `.scn` 文件头 `mdf\0`；加密归档解出来的脚本则是**裸 PSB**（`PSB\0`） |
| 归档 | `*.xp3`，11 字节魔数 `58 50 33 0D 0A 20 0A 1A 8B 67 01` |
| 加密变体 | `0x17` 处为 `0x80`（真索引偏移在 `0x20`），索引树里多一个 `Hxv4` 块 |

**本作归档清单**（`E:\SteamLibrary\steamapps\common\MashiroiroSymphonySANA`）：

| 文件 | 大小 | entry | 与剧本有关 |
|---|---|---|---|
| `data.xp3` | 1.71 GB | 3,046 | 有（`cn/scn` 40 条，是**残缺版**） |
| `data_sana.xp3` | 704 MB | 3,799 | **无**（全在根域，9 个非 `scenes` 的 PSB） |
| `patch.xp3` | 1.10 GB | 1,021 | **有（完整版靠它）**：`cn/scn` 102 条 |
| `patch2.xp3` | 2.09 GB | 22,118 | **无**（`cn/scn` 零条目） |
| `evimage` / `fgimage` / `fgimage2` / `video` / `voice` | — | — | 无 |

⇒ 取剧本必须 **`data.xp3` + `patch.xp3` 两个一起**（后者覆盖同名脚本并补齐 62 条），详见 [version-diff.md](version-diff.md) §1.1。

---

## 1. xp3（krkrz 归档）

### 1.1 普通 xp3

```
0x00 11 魔数 58 50 33 0D 0A 20 0A 1A 8B 67 01
0x0B 8  u64 索引偏移
索引区:
  u8  compress_flag           0 = 索引明文，1 = 索引 zlib
  [u64 zsize, u64 fsize]      仅 compress_flag = 1
  entry[]:
    "File" + i64 size
      "info" + i64 size + u32 flags, i64 fsize, i64 zsize, u16 namelen, UTF-16LE name
      "segm" + i64 size + [u32 flags, u64 offset, u64 fsize, u64 zsize] * n
      "adlr" + i64 size + u32 adler32
```

- `segm.flags & 1` = 该段 zlib 压缩；`offset` 相对文件头。
- **文本打散**：段数据若以 `FE FE <mode> FF FE` 开头，其后是 tvp 文本混淆（mode 1 = 相邻 bit 两两对调，解出 `FF FE` 开头的 UTF-16LE；mode 2 = zlib）。这是引擎层通用机制，不限于剧情脚本。
- 索引里的文件名是 UTF-16LE。
- 落点：`tool/xp3.py`

### 1.2 Hxv4 变体（wamsoft 加密，Steam 版全部适用）

```
文件头 40 字节
  0x00 11 魔数
  0x0B 8  u64 = 0x17            ★ 诱饵：照它解析索引必崩
  0x13 4  u32 = 1
  0x17 1  u8  = 0x80            ★ 标志：真索引偏移在 0x20
  0x18 8  保留 0
  0x20 8  u64 真索引偏移
索引树 = zlib( chunk 树 )：先一个 Hxv4 块，再逐 File 块
  Hxv4 描述符 14 字节： i64 payload_offset | u32 payload_size | u16 flags(bit0 = open_flag)
  File 块与普通 xp3 相同
```

- 条目名一律是**伪名**：第 i 条 = `U+5000 + i`（`倀倁倂…`）。个别包 entry 1 会写成字面 `startup.tjs`，此时**条目数 = 记录数 + 1**，记录从 entry 1 起。
- **entry 0 恒为 1×1 警告 PNG**（910 B，tEXt 关键字 `Warning`），不过过滤器、原样写出。
- `*.sig`（若存在）是 `-- SIGNATURE - SHA256/PSS/RSA --` 的 base64 签名，只做完整性校验，与解密无关；未签名的新增包不影响加载。
- `Hxv4` 负载的加密细节与内容层过滤器见 [hxv4.md](hxv4.md)。
- **名字与域都先规范化再哈希**：名字**小写**；`file_hash = BLAKE2s-256(UTF16LE(小写名字)+UTF16LE(盐))`，`domain_hash = SipHash-2-4(UTF16LE(小写目录+盐), key=16×0)`（根目录 = 只哈希盐，目录带结尾 `/`）。实现：`tool/hxv4hash.py`；本作盐 = `xp3hnp`（映射表里的 `HashDomain`）。反查表见[engine-mechanics.md](engine-mechanics.md) §4.1。
- **映射表结构**（`Hxv4` 负载解密后 zlib → TJS variant）：`array[ octet(domain 8B), array[ octet(file_hash 32B), array[packed:int, key:int] ] ]`。`packed & 0xFFFF` = 条目序号（若 entry 1 的名字是字面 `startup.tjs` 则整体 +1），`packed >> 16` 实测恒 0；`key` 是该文件数据层过滤器的种子（见 [hxv4.md](hxv4.md)）。
- `File` 块内 `segm` 每条 28 字节：`flags@0(u32) offset@4(u64) fsize@12(u64) zsize@20(u64)`；**zlib 条目的 `fsize` 是解压后长度**（写成压缩后长度 ⇒ 运行时 `Read error`）。
- `File` 块内 `info`：`flags@0(u32) fsize@4(u64) zsize@12(u64) namelen@20(u16)`，名字从 22 起、带 2 字节 NUL；子块顺序官方为 `adlr, segm, info`。
- `adlr` = `adler32(明文, init=1)`；**entry 0 的官方值是畸形占位**（`info.fsize** `≠`segm.fsize`、adler 与内容不符），照抄即可，不要"修正"。
- 落点：`tool/hxv4.py::Hxv4Archive`

---

## 2. `.scn`（剧情脚本容器）

| 外壳 | 结构 | 出现场合 |
|---|---|---|
| `mdf` 包装 | `"mdf\0"` + `u32 原文长度` + zlib 流（无尾随校验字节） | 明文补丁里的 `.scn` |
| 裸 PSB | 直接 `"PSB\0"` | 加密归档解出来的脚本文件 |

**解析入口必须同时接受两种魔术**（读前 4 字节判断），不能只看扩展名 ——加密归档解出来的文件没有 `.scn` 后缀，扩展名与真名要靠 PSB 内的 `root["name"]` 恢复。

---

## 3. PSB v3（M2 序列化对象）

### 3.1 头部（44 字节）

```
0x00 "PSB\0"
0x04 u16 version = 3        0x06 u16 header_encrypt（非 0 = 头部加密，当前实现不支持）
0x08 u32 header_length = 0x2C
0x0C u32 offset_names         名字区（charset / 字典树数据 / 字典树根 / 对象图数据）
0x10 u32 offset_strings       字符串偏移表
0x14 u32 offset_strings_data  字符串正文（UTF-8，NUL 分隔）
0x18 u32 offset_chunk_offsets
0x1C u32 offset_chunk_lengths
0x20 u32 offset_chunk_data
0x24 u32 offset_entries       对象图根节点
0x28 u32 checksum             adler32(0x0C..0x27)
```

本作实测布局（`scenario_miu_route02_10.txt.scn`）：

```
0x2C .. 0x2AAF1   names 区（含对象图）
0x2AAF1..0x2ACFB  字符串偏移表
0x2ACFB..0x2C3D7  字符串正文
0x2C3D7..0x2C3E0  chunk_offsets / chunk_lengths（本作为空数组）
0x2C3E0..EOF      chunk_data（空）
根节点在 0xE79，落在 names 区内
```

### 3.2 值类型（全部小端）

| 类型字节 | 含义 |
|---|---|
| 0x00 / 0x01 | None / Null |
| 0x02 / 0x03 | False / True |
| 0x04 | 整数 0 |
| 0x05..0x0C | 整数 N1..N8（跟随 1..8 字节） |
| 0x0D..0x14 | 数组 ArrayN1..N8：N 字节计数 + 1 字节元素宽度类型 + 数据 |
| 0x15..0x18 | 字符串索引（指向字符串偏移表） |
| 0x19..0x1C | 资源索引（chunk） |
| 0x1D / 0x1E / 0x1F | float 0.0 / float32 / float64 |
| 0x20 | List：偏移表 + 子对象（偏移相对偏移表结束处） |
| 0x21 | Objects：名字索引数组 + 偏移表 + 子对象 |
| 0x22..0x25 | ExtraChunk |

### 3.3 names 区是字典树

```
charset 数组       每项是一个字符的偏移修正量
names_data 数组    节点：每项 = 下一个节点的下标
name_indexes       每个名字对应一棵树的根下标
解码： node = names_data[root]
       while node != 0:
           nxt  = names_data[node]
           byte = (node - charset[nxt]) & 0xFF
           node = nxt
       得到的字节倒序拼接 → UTF-8 解出名字
```

### 3.4 字符串池

`offset_strings` 处是偏移表，`offset_strings_data + offset[i]` 起、到下一个 `0x00` 为止，是第 i 个字符串的 **UTF-8** 正文 → 中文可直接写入，不需要编码转换。

### 3.5 重写策略与验收判据

只重排**字符串正文 + 偏移表**，其余字节原样复制，再修正头部偏移并重算 checksum：

- 字符串**数量可以增加**：新槽追加到池尾，槽号 = 原数量 + k（引用需自己改指）
- 不变量：不改内容重建 ⇒ PSB body **与原始逐字节相同**（闸 1）
- **闸 1 是必要条件，不是充分条件**：偏移量为 0 时它看不见「偏移量算错」。必须再跑闸 1b——**单条字符串变长 / 变短后重建，仍能解析且池内容与预期一致**
- 头部里 `offset_names` / `offset_strings` / `offset_entries` 指向**字符串表之前**的区域，重排字符串后**不得**跟着平移；只有 `offset_chunk_*` 需要加 `shift`。（实测踩过：把 `offset_entries` 一起平移，零改动往返仍然全绿，一改内容就解析崩）
- 要改页的 `length` **不必**写完整序列化器：它是页面数组里的内联定宽整数，同宽度可原位改字节（`PsbFile.patch_ints`）；文本引用同理（`STRING_Nx` 内联索引）。内联字段的类型字节按族区分：整数 `0x04+width`、字符串引用 `0x14+width`
- 落点：`tool/psb.py::PsbFile.rebuild` / `find_ints` / `find_str_refs` / `patch_ints`；上层用法见 [text-model.md](text-model.md) §6

---

## 4. TJS binary Variant（Hxv4 映射表用的编码）

Hxv4 映射表明文是这套编码，**与 PSB 的值编码不是同一套**（整数定长 8 字节大端、数组内联）：

| 标签 | 含义 |
|---|---|
| `1` | null |
| `2` | string：`i32` 字符数 + UTF-16BE |
| `3` | octet：`i32` 字节数 + 数据 |
| `4` | int：8 字节大端 |
| `5` | real：8 字节大端 double |
| `0x81` | array：`i32` 计数 + 元素 |
| `0xC1` | dict：`i32` 计数 + (键=string, 值) 对 |

落点：`tool/hxv4.py::tjs_parse`

## 5. 其它已知载体

| 文件 | 说明 |
|---|---|
| `*.tjs` / `*.ks` | KAG 系统脚本，**明文源码**（UTF-16LE），含 UI 文本与字体配置 |
| `*.pimg` | M2 图片容器（PSB 变体，无 `scenes`，解析时要按此排除） |
| `*.mmv.json` | 立绘/背景动画（AffineSourceMMV）配置 |
| `*.otf` | 补丁自带的思源黑体（`sourcehansansjp-bold.otf`） |
| `*.sig` | 见 §1.2 |
