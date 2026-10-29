"""Per-script text set storage.

目录布局（**一个脚本一个文件**，与归档条目 1:1，便于 diff / 增量 / 并行）：

    <set_dir>/_index.json                     清单：{"scripts": {name: {...}}}
    <set_dir>/scripts/<script>.jsonl          对白行（一行一条）
    <set_dir>/scripts/<script>.selects.jsonl  选项（没有就不建文件）

    <match_dir>/pairs/<script>.jsonl          行级匹配结果（一行一条，含 status）

文件名里的 `<script>` 是**逻辑脚本名**（`scenario_01.txt`，引擎请求 `<名字>.scn` 的那个名字），
所以文件形如 `scenario_01.txt.jsonl`——`.txt` 属于名字本身，不是我们加的容器后缀。

兼容期：分片目录不存在时回落到旧的聚合文件（`messages.jsonl` / `selects.jsonl` / `pairs.jsonl`）。
"""

import json
import os

MESSAGES = "messages"
SELECTS = "selects"
PAIRS = "pairs"


def _split_dir(base):
    """文本集落在 <base>/scripts/，匹配结果落在 <base>/pairs/。"""
    leaf = "pairs" if os.path.basename(os.path.normpath(base)) == "match" else "scripts"
    return os.path.join(base, leaf)


def read_jsonl(path):
    with open(path, encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                yield json.loads(line)


def names(base):
    """脚本名清单：优先 `_index.json`，否则扫分片目录，最后回落聚合文件。"""
    idx = os.path.join(base, "_index.json")
    if os.path.exists(idx):
        with open(idx, encoding="utf-8") as fp:
            scripts = json.load(fp).get("scripts") or {}
        if scripts:
            return sorted(scripts)
    d = _split_dir(base)
    if os.path.isdir(d):
        return sorted({f[:-6] for f in os.listdir(d)
                       if f.endswith(".jsonl") and not f.endswith(".selects.jsonl")})
    legacy = os.path.join(base, "pairs.jsonl" if base.endswith("match") else "messages.jsonl")
    if os.path.exists(legacy):
        return sorted({rec["script"] for rec in read_jsonl(legacy)})
    return []


def path_for(base, script, kind=MESSAGES):
    d = _split_dir(base)
    if kind == SELECTS:
        return os.path.join(d, script + ".selects.jsonl")
    if kind == PAIRS:
        return os.path.join(d, script + ".jsonl")
    return os.path.join(d, script + ".jsonl")


def iter_records(base, kind=MESSAGES, scripts=None):
    """按脚本逐条产出记录；scripts 给出时只读这些脚本（并行 worker 用）。"""
    d = _split_dir(base)
    if os.path.isdir(d):
        todo = scripts if scripts is not None else names(base)
        for script in todo:
            p = path_for(base, script, kind)
            if os.path.exists(p):
                yield from read_jsonl(p)
        return
    legacy = os.path.join(base, "pairs.jsonl" if kind == PAIRS else
                          ("selects.jsonl" if kind == SELECTS else "messages.jsonl"))
    if not os.path.exists(legacy):
        return
    keep = None if scripts is None else set(scripts)
    for rec in read_jsonl(legacy):
        if keep is None or rec["script"] in keep:
            yield rec


def records_for(base, script, kind=MESSAGES):
    p = path_for(base, script, kind)
    if os.path.exists(p):
        return list(read_jsonl(p))
    return [rec for rec in iter_records(base, kind, [script])]


def write_script(base, script, rows, selects=None, kind=MESSAGES):
    """写单个脚本的分片文件（空则删除旧文件，保持幂等）。"""
    d = _split_dir(base)
    os.makedirs(d, exist_ok=True)
    dest = path_for(base, script, kind)
    if rows:
        with open(dest, "w", encoding="utf-8", newline="\n") as fp:
            for rec in rows:
                fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
    elif os.path.exists(dest):
        os.remove(dest)
    if kind == MESSAGES:
        sel_dest = path_for(base, script, SELECTS)
        if selects:
            with open(sel_dest, "w", encoding="utf-8", newline="\n") as fp:
                for rec in selects:
                    fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
        elif os.path.exists(sel_dest):
            os.remove(sel_dest)


def write_pairs(base, script, rows):
    write_script(base, script, rows, kind=PAIRS)
