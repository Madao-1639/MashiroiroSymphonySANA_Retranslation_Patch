# Hxv4 加密层（Wamsoft / KiriKiriZ）

Steam 版全部 xp3 使用 wamsoft 的 Hxv4 变体（容器结构见 [file-formats.md](file-formats.md) §1.2）。
本文写三层：映射表、内容过滤器、参数（含获取方式与判据）。
实现：`tool/hxv4.py`（纯标准库）；参数：`resource/hxv4/profile_live.json`。

## 1. 映射表（`Hxv4` 负载）

```
负载 = [16B Poly1305 tag][XChaCha20 密文]
明文 = u32 解压后长度 + zlib( TJS binary Variant，见 file-formats.md §4 )
记录 = domain_hash[8] + file_hash[32] + packed u32 + key u64
```

- `nonce` 由描述符 `flags bit0` 选：0 → `nonce0`，1 → `nonce1`（各 24 字节）。
- `packed`：**高 16 = archive_slot，低 16 = 条目下标**；记录顺序 ≠ 条目顺序，绑定必须用下标（§5）。
- `key` 是打包方随手挑的 64 位自由值，不是任何公式的产物；读包只需用它，不需要知道来源。
- `domain_hash` 是哈希域，语料上同时充当**语言维度**（见 [engine-mechanics.md](engine-mechanics.md) §4）。

## 2. 密码学细节

- 算法是 **XChaCha20-Poly1305，内层 ChaCha20 为 DJB 布局**：64 位计数器 + 64 位 nonce，**不是** RFC 8439 的 IETF 布局。※ 对 counter < 2³²，两种布局的 state 前两个字相同，不能用「能解开」区分实现是否正确。
- 子密钥与 MAC：

```
subkey   = HChaCha20(key, nonce[0:16])
poly_key = ChaCha20Block(subkey, counter=0, nonce[16:24])[0:32]
密文     从 counter=1 起
tag      = Poly1305(poly_key, ct ‖ pad16 ‖ LE64(0) ‖ LE64(len(ct)))      # 无 AAD
```

- 原语正确性用公开向量回归：Poly1305（RFC 7539 §2.5.2）、ChaCha20 块（§2.3.2）、HChaCha20（draft-irtf-cfrg-xchacha §2.2.1）→ `script/verify_crypto.py`

## 3. 内容层：DRIP VM + 四层 XOR

```
key64 ──DRIP(key64, open_flag)──> 48B 种子状态 ──> 四层 XOR 过滤器 ──> 明文
```

顺序：**先按 `segm.flags bit0` zlib 解压，后过过滤器**；之后再处理 `FE FE` 文本打散。

**DRIP VM**：

- 128 条 lane 程序，每条是 `(param, opcode)` 序列；opcode 以模块 RVA 标识，共 21 种（加/减/乘/移位/按位组合/查表/递归/停机…）
- 1024+ 项 dword 查表；`get64(v)` 的低半 = 对 `v >> 7` 求 `lane[v & 0x7F]`，高半 = 同一条 lane 对 `~seed` 求值

**48 字节种子状态**：

| 偏移 | 内容 |
|---|---|
| `[0:8]` | `get64(key_lo)` |
| `[8:16]` | `get64(key_hi)` |
| `[16:20]` | `split = holder[5] + (holder[4] & (key64 >> 16))` |
| `[24:40]` | 16B bulk key（反复 `~get64(~key)` 产出） |
| `[44]` | 1（has_drip） |
| `[45]` | 0（null_mode） |

其中 `open_flag & 1 == 0` 时才用 `holder[2]`/`holder[3]` 扰动 key 的高低 32 位（**方向别写反**）。

**四层 XOR 过滤器**（加解密同一函数）：

1. 前 16 字节用 bulk key 逐字节 XOR
2. 按 `split` 切两段
3. 每段用「按逻辑位置旋转的 dword key」整段 XOR
4. 每段在 `pos0`/`pos1` 各 XOR 一个字节

## 4. 参数

| 参数 | 大小 | 作用 | 本作取值 |
|---|---|---|---|
| `hxv4_key` | 32 B | 映射表 AEAD 密钥 | `95d4af8a…` |
| `nonce0` / `nonce1` | 24 B ×2 | 按 `flags bit0` 选 | `c85faf6b…` / `6ced430a…` |
| `holder_words` | 6 × u32 | 过滤器种子常量（`[0]` 是 `drip_impl` 指针；`[2..5]` 参与运算） | `[0x038d3400, 0x368fb6bc, 0x6baf5fc8, 0xc74d6167, 0x11c, 0x185]` |
| `context_u32` | ≥1024 × u32 | DRIP 查表 | 3106 项 |
| `lanes` | 128 条 | DRIP 程序（opcode 归一为 RVA） | 3049 条记录 |
| `hash_domain` | 字符串 | 名字哈希的盐 | `xp3hnp` |
| `hash_key` | 32 B | keyed `file_hash` 变体 | 本作不使用（key_len = 0） |

落地格式：`resource/hxv4/profile_live.json`（字段名 `hxv4_key` / `hxv4_nonce0` / `hxv4_nonce1` / `holder_words` / `context_u32` / `lanes` / `hash_domain`，`source_module_base` 为 0 表示 lane 已归一）。
**参数与游戏构建绑定**：游戏更新后必须重抓。

### 4.1 本作与 FHD 共用同一套参数（实测，重要）

本仓库的 `profile_live.json` 是 FHD 项目从 Steam 版 FHD（appid 2737970）运行态抓的，**对本作（appid 2737980）直接可用**：

| 对象 | 结果 |
|---|---|
| `data.xp3`（3,046 entry） | 映射表 Poly1305 tag 通过；内容过滤器正确（解出 PNG / `OggS` / PSB 首部均合法） |
| `patch.xp3`（1,021 entry） | 同上；`cn/scn` 102 条脚本全部解成合法 PSB |
| `data_sana.xp3`（3,799 entry） | 同上 |
| `patch2.xp3`（22,118 entry） | 同上（其中只有图/音/语音） |

⇒ **不需要为 SANA 重抓参数**（不用启动 x32dbg）。两个发行体的 key/nonce/holder/context/lanes/`hash_domain` 完全相同，只有脚本体与目录内容不同。反过来说：**日文原版（非 Steam）仍然打不开**，这与 FHD 项目的结论一致。

## 5. 参数获取：只能从运行中的进程读

参数不是 exe/dll 里的明文常量，而是运行时由内嵌 bootstrap 模块的几个例程算出来的。
`script/capture_live_crypto.py` 的流程：

1. **必须从调试器启动游戏**（附加太晚，启动阶段已经算完）
2. 在 `LoadLibraryW` 下断 → 认出随机十六进制命名的 bootstrap 模块 → `StepOut` 取基址 →在 `base + 0xE2D0`（ManagerCtor）/ `+0x15630`（BootstrapDerive）/ `+0x157D0`（ArchiveDerive）布点**【跨作需重验：RVA 随构建变化】**
3. 命中 `BootstrapDerive` → 记录实参（bootstrap 路径、`PARAMS` 字节）→ `StepOut` 读 manager core：`core+0` 6 个 holder words、`core+0x3038` key、`+0x3058` nonce1、`+0x3078` nonce0
4. 顺 `holder[0]`（= `drip_impl`）取 context 表（`drip_impl + 0x10` 指向）与 128 条 lane（`drip_impl + 0x04` 起、每条 0x10 字节描述）
5. 写 `profile_live.json`

**操作要点（x32dbg 侧）**

- 调试器 MCP 端点 `http://127.0.0.1:50301/mcp`，客户端 `tool/x32dbg_mcp.py`；常用工具：`GetAllModulesFromMemMap`、`GetAllRegisters`、`SetBreakpoint`（参数名是 `target` 不是 `address`）、`ReadDismAtAddress`、`FindAllMem`（支持 `??` 通配）、`SearchForStrings`、`run`/`StepInto`/`StepOver`/`StepOut`。
- 返回的地址/数值是**十进制**（虽然带 `0x` 前缀看起来像十六进制），解析时按十进制读。
- 没有"读 N 字节"接口：`ReadDismAtAddress` 是唯一取原始字节的途径，按绝对地址把每行反汇编的字节列回填（长指令会截断字节列），实现在 `tool/x32dbg_mcp.py::read_bytes`。
- 需要从 64 位 Python 调 32 位 DLL 的导出时，`ctypes.WinDLL` 会报 `[WinError 193]`；改用 **32 位 PowerShell**（`C:\Windows\SysWOW64\WindowsPowerShell
1.0\powershell.exe`）+ `Add-Type` 现场编译 C# P/Invoke 包装。`GlobalAlloc`/`GlobalLock` 的 `restype` 要声明 `c_void_p`，`PostMessageW`/`SendMessageW` 的 `wParam` 要声明 `c_size_t`。

**静态派生不可信**：从 exe 静态派生出的 key 能把映射表解成合法 TJSON，但**内容全解不开**；其 context 表也与游戏实际使用的不一致（128 条 lane 倒是一致）。实测差异：派生 key `e3fd3f3d…` vs 真值 `95d4af8a…`。

## 6. 判据与不变量

1. **Poly1305 tag 通过 ⇒ 映射表 key/nonce 正确**（强判据，错一位即失败）
2. **内容仍乱 ⇒ DRIP 的 context 表或 holder 不对**（不是过滤器代码问题）- context 错时，个别 key 会碰巧走到「不查表」的 lane 上 → 表现为「只有少数条目能解」
3. **记录绑定用 `packed & 0xFFFF`（条目下标）+ offset**，`offset` = 是否存在字面 `startup.tjs` 条目；绑定后校验「每条记录落到唯一条目、除 entry 0 外每个条目都有记录」
4. 条目 0（警告图）不过滤
5. 离线结果必须与另一条独立路径（游戏内注入式解包）**逐字节比对**

## 7. 离线验证结果（本作）

| 对象 | 结果 |
|---|---|
| `data.xp3` + `patch.xp3`（`cn/scn` 域） | 102 脚本 / 23,245 行 / 11 选项，解出的脚本全部可解析；结构校验见 `script/align_check.py` |
| 三域一致性 | `cn`/`tw`/`en` 各 102 脚本 / 23,245 行，逐脚本行数相同 |
| `data.xp3` 内容类型 | 首 400 entry：PNG 397 / `OggS`（后者在 `data_sana.xp3`）/ PSB / 文本，全部首部合理 |
| `data_sana.xp3` 内容类型 | 首 400 entry：`OggS` 261、PNG 114、PSB 9（均非 `scenes` 结构） |
| 其它归档（`evimage` / `voice` / `video` / `patch2`） | PNG / OGG 等首部均可识别 |
| 映射表绑定不变量 | 每条记录落到唯一条目、除 entry 0 外每个条目都有记录（`_bind_records` 自检通过） |

FHD 项目当年的更强判据（与游戏内注入式解包逐字节比对）在本仓库不重复执行：同一份 `tool/hxv4.py`、同一套参数，差异只在归档内容。

## 8. 名字层（已用于补丁条目命名）

- `file_hash = BLAKE2s-256(UTF-16LE(名字) + UTF-16LE(盐))`，盐 = `hash_domain`（本作 `xp3hnp`）
- `dir_hash = SipHash-2-4(同上输入)`
- keyed 变体（`hash_key` 参与）在本作未启用（`key_len = 0`）

作用：

1. **解包产物命名**：脚本自带 `root["name"]`，产物按 `domain/file_hash` 命名即可；
2. **验证对齐前提**：`script/align_check.py --game` 逐条断言 `blake2s(utf16le("<script>.scn") + utf16le("xp3hnp"))`存在于游戏 `cn/scn` 域（**102/102 通过**）；
3. **验证补丁条目名**：把改好的脚本以真名 `<script>.scn` 打进 `CN.xp3`，引擎用同一套哈希去查表。
