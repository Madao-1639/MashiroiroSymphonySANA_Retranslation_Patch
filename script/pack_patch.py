"""Build the retranslation patch archive (CN.xp3).

机制（结论来自 doc/engine-mechanics.md §3.2/§4，均有复跑依据）：

  * 引擎只按 initialize.tjs 里**写死**的名字注册归档；`CN` 在 subfolders 清单里
    （`addArchive("CN")` → `addAutoPath("CN/")` + `<exePath>CN.xp3>`），所以补丁用这个名字
    才不需要改动任何官方文件。
  * autopath 的优先级 = 注册时间，**越晚注册越高**（initialize.tjs 第 233 行注释）。
    语言相关的 `patch.xp3>cn/scn/` 是 Initialize.tjs 注册的最后一个，因此裸名剧情脚本
    永远优先命中 patch.xp3。
  * 破解办法：`CN.xp3` 根域放一份 `main/default.tjs`（官方原文 + 追加钩子）。该文件
    patch.xp3 / patch2.xp3 里都不存在，而 `CN.xp3` 又比 `data.xp3` 注册得晚，
    所以稳定命中我们的副本；钩子装一个连续处理器，等启动流程结束后再
    `addAutoPath(exePath + "CN.xp3>cn/scn/")` —— 这是整条注册链的最后一次，
    因此优先级最高，且只影响 `cn/scn/` 命名空间（不触碰配置、存档、其它资源）。

产物：releases/CN.xp3
卸载：删除该文件即可，游戏目录内没有其它改动。

用法（项目根执行，先产出回写脚本）：
    mamba run -n GalRev python script/writeback.py apply     # -> tmp/out_zhcn/*.scn
    mamba run -n GalRev python script/pack_patch.py          # -> releases/CN.xp3
"""

import json
import os
import struct
import sys
import zlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from tool import hxv4, hxv4_write, scn
from tool.hxv4hash import domain_hash, file_hash

GAME = 'E:/SteamLibrary/steamapps/common/MashiroiroSymphonySANA'
PROFILE = 'resource/hxv4/profile_live.json'
INDEX = 'resource/steam_text_zhcn/_index.json'
SCRIPT_DIR = 'tmp/out_zhcn'
OUT = 'releases/CN.xp3'

# cn/scn/ 的 domain_hash；脚本必须放在这个域里，请求名是裸名（StorageData: path += storage + ".scn"）
SCRIPT_DOMAIN = 'cn/scn/'

HOOK = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'patch_hook.tjs'),
            encoding='utf-8').read()


def stock_warning_entry(fhd):
    """条目 0 = 官方 1x1 警告占位图；其字段自相矛盾（info.fsize != segm.fsize），必须照抄。"""
    w0 = fhd.entries[0]
    idx = struct.unpack_from('<Q', fhd.data, 0x20)[0]
    tree = zlib.decompress(fhd.data[idx + 17:])
    pos = 0
    body = None
    while pos + 12 <= len(tree):
        sig = tree[pos:pos + 4]
        size = struct.unpack_from('<q', tree, pos + 4)[0]
        if sig == b'File':
            body = tree[pos + 12:pos + 12 + size]
            break
        pos += 12 + size
    if body is None:
        raise ValueError('no File chunk in stock archive index')
    sub = {}
    p = 0
    while p + 12 <= len(body):
        sig = body[p:p + 4]
        size = struct.unpack_from('<q', body, p + 4)[0]
        sub[sig] = body[p + 12:p + 12 + size]
        p += 12 + size
    info_fsize, info_zsize = struct.unpack_from('<qq', sub[b'info'], 4)
    seg_flags, _seg_off, seg_fsize, seg_zsize = struct.unpack_from('<IQQQ', sub[b'segm'], 0)
    return {"payload": fhd.read_entry(w0), "domain": w0.record.domain_hash,
            "file_hash": w0.record.file_hash, "key": w0.record.key, "flags": w0.flags,
            "adler": w0.adler, "info_fsize": info_fsize, "info_zsize": info_zsize,
            "seg_flags": seg_flags, "seg_fsize": seg_fsize, "seg_zsize": seg_zsize}


def stock_default_tjs(data):
    """取 data.xp3 的 main/default.tjs 原文（不是 cn/en/tw 语言变体），作为钩子载体基座。"""
    for e in data.entries:
        if not e.record:
            continue
        if e.record.file_hash != file_hash('default.tjs'):
            continue
        if e.record.domain_hash == domain_hash('main/'):
            return data.read_entry(e)
    raise ValueError('stock main/default.tjs not found in data.xp3')


def build():
    if not os.path.isdir(SCRIPT_DIR):
        raise SystemExit('missing %s - run: python script/writeback.py apply' % SCRIPT_DIR)
    prof = hxv4.Hxv4Profile.load_json(PROFILE)
    # 本作没有 data_fhd.xp3：警告占位 entry 0 从官方 patch.xp3 照抄（该包的 entry 0 带映射记录，
    # 而 data.xp3 的 entry 0 没有记录，不能作为来源）。
    warn_arc = hxv4.Hxv4Archive(os.path.join(GAME, 'patch.xp3'), prof)
    data = hxv4.Hxv4Archive(os.path.join(GAME, 'data.xp3'), prof)

    base_tjs = stock_default_tjs(data)
    tjs_payload = b'\xff\xfe' + (base_tjs.decode('utf-16').lstrip('\ufeff') + HOOK).encode('utf-16-le')

    index = json.load(open(INDEX, encoding='utf-8'))['scripts']
    items = [{"domain": domain_hash(''), "file_hash": file_hash('default.tjs'),
              "key": 0x1111222233334444, "plaintext": tjs_payload}]
    for i, key in enumerate(sorted(index)):
        name = key + '.scn'
        payload = open(os.path.join(SCRIPT_DIR, name), 'rb').read()
        domain_hex, fh_hex = index[key]['arcname'].split('/')
        if bytes.fromhex(fh_hex) != file_hash(name):
            raise ValueError(f'file hash mismatch for {name}')
        items.append({"domain": domain_hash(SCRIPT_DOMAIN), "file_hash": bytes.fromhex(fh_hex),
                      "key": 0x2000000000000000 | i, "plaintext": payload})

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    size = hxv4_write.write_archive(OUT, items, prof, open_flag=warn_arc.open_flag,
                                   warn=stock_warning_entry(warn_arc))
    print('written %s  %.1f MB  (1 warning + 1 hook + %d scripts)'
          % (OUT, size / 1e6, len(items) - 1))
    return prof, index, items, size


def _verify_chunk(tasks):
    """worker：解析一批脚本条目，校验页数（PSB 反序列化是纯 CPU，按脚本并行才有收益）。"""
    prof = hxv4.Hxv4Profile.load_json(PROFILE)
    arc = hxv4.Hxv4Archive(OUT, prof)
    checked = 0
    for entry_index, expected_pages, script in tasks:
        entry = arc.entries[entry_index]
        sf = scn.ScnFile(arc.read_entry(entry))
        pages = sum(len(m.pages) for m in sf.iter_messages())
        if pages != expected_pages:
            raise ValueError('page count mismatch for %s: %d != %d'
                             % (script, pages, expected_pages))
        checked += 1
    arc.close()
    return checked


def verify(prof, index, items, jobs=1):
    """回读校验：产物必须能重新解析，且每条载荷与预期逐字节相同、页数与索引一致。"""
    back = hxv4.Hxv4Archive(OUT, prof)
    if len(back.entries) != len(items) + 1:
        raise ValueError('entry count mismatch: %d != %d' % (len(back.entries), len(items) + 1))
    for i, it in enumerate(items, start=1):
        got = back.read_entry(back.entries[i])
        if got != it['plaintext']:
            raise ValueError('payload mismatch at entry %d' % i)
        if back.entries[i].record.domain_hash != it['domain']:
            raise ValueError('domain mismatch at entry %d' % i)
    for e in back.entries[1:]:
        if (zlib.adler32(back.read_entry(e), 1) & 0xFFFFFFFF) != e.adler:
            raise ValueError('adler mismatch at entry %d' % e.index)
    by_hash = {v['arcname'].split('/')[1]: k for k, v in index.items()}
    tasks = []
    for e in back.entries[2:]:
        key = by_hash.get(e.record.file_hash.hex())
        if key is None:
            raise ValueError('unknown script hash at entry %d' % e.index)
        tasks.append((e.index, index[key]['pages'], key))
    jobs = max(1, min(jobs, os.cpu_count() or 1))
    if jobs == 1 or len(tasks) < 8:
        checked = _verify_chunk(tasks)
    else:
        from concurrent.futures import ProcessPoolExecutor
        step = max(1, len(tasks) // (jobs * 4))
        chunks = [tasks[i:i + step] for i in range(0, len(tasks), step)]
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            checked = sum(pool.map(_verify_chunk, chunks))
    print('verify ok: payloads identical, adler ok, %d scripts page-count == index' % checked)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--jobs', type=int, default=min(8, os.cpu_count() or 1),
                    help='worker processes for the verification pass')
    args = ap.parse_args()
    prof, index, items, _ = build()
    verify(prof, index, items, jobs=args.jobs)
