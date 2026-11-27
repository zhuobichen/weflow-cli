#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M-05 正式提取：从 Windows 微信 4.2 WMPF 进程内存抽公众号信息流文章列表。

只读（ReadProcessMemory），不注入、不改微信、不联网。
数据形状（实测 2026-11-27）：内存里是转义 JSON 数组，每篇：
  {"AppMsgId":N,"DateTime":<unix>,"weight":N,"uniqueId":"gh_xxx@N","localId":N,"Title":"标题",...}
  出现在 `firstGroupAfterResortList | N | [{...}]` 等缓存值里。

用法: python scripts/extract_feed_memory.py [--pid 41420] [--out articles.json] [--date YYYY-MM-DD]
"""
import argparse, ctypes, ctypes.wintypes as wt, datetime, json, os, re, sys, time

# Windows 控制台默认 GBK，文章标题含 emoji/生僻字时 print 会炸——统一 UTF-8（项目惯例）
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

k = ctypes.WinDLL('kernel32', use_last_error=True)
k.OpenProcess.argtypes=[wt.DWORD,wt.BOOL,wt.DWORD]; k.OpenProcess.restype=wt.HANDLE
k.ReadProcessMemory.argtypes=[wt.HANDLE,wt.LPCVOID,wt.LPVOID,ctypes.c_size_t,ctypes.POINTER(ctypes.c_size_t)]
k.ReadProcessMemory.restype=wt.BOOL
k.VirtualQueryEx.argtypes=[wt.HANDLE,wt.LPCVOID,ctypes.c_void_p,ctypes.c_size_t]
k.VirtualQueryEx.restype=ctypes.c_size_t
k.CloseHandle.argtypes=[wt.HANDLE]
class MBI(ctypes.Structure):
    _fields_=[('BaseAddress',ctypes.c_void_p),('AllocationBase',ctypes.c_void_p),
              ('AllocationProtect',wt.DWORD),('PartitionId',wt.WORD),('RegionSize',ctypes.c_size_t),
              ('State',wt.DWORD),('Protect',wt.DWORD),('Type',wt.DWORD)]
READABLE={0x02,0x04,0x08,0x10,0x20,0x40,0x80}

def read_regions(pid, deadline=180):
    h=k.OpenProcess(0x0400|0x0010,False,int(pid))
    if not h: raise RuntimeError(f'OpenProcess failed {ctypes.get_last_error()}')
    chunks=[]; addr=0; mbi=MBI(); t0=time.time(); CHUNK=16*1024*1024
    try:
        while addr<0x7fffffffffff and time.time()-t0<deadline:
            if k.VirtualQueryEx(h,ctypes.c_void_p(addr),ctypes.byref(mbi),ctypes.sizeof(mbi))==0: break
            sz=mbi.RegionSize
            if mbi.State==0x1000 and mbi.Protect in READABLE and sz<=CHUNK:
                buf=ctypes.create_string_buffer(sz); got=ctypes.c_size_t()
                if k.ReadProcessMemory(h,ctypes.c_void_p(addr),buf,sz,ctypes.byref(got)) and got.value:
                    data=buf.raw[:got.value]
                    if b'AppMsgId' in data and b'Title' in data:
                        chunks.append(data)
            addr+=sz
    finally: k.CloseHandle(h)
    return chunks

# 单个文章对象：转义形式 \"AppMsgId\":... \"Title\":\"...\" \"DateTime\":...
# 标题结束于 \",\" （即下一个字段开始），用非贪婪 + 明确终止符，避免吞进 VideoDuration 等后续字段
ARTICLE_RE = re.compile(
    rb'\{\\"AppMsgId\\":(\d+),\\"DateTime\\":(\d+),.*?\\"uniqueId\\":\\"(gh_[^\\]+)@'
    rb'\d+\\",.*?\\"Title\\":\\"((?:[^"\\]|\\.)*?)\\",\\"', re.S)

def parse(data):
    out=[]
    for m in ARTICLE_RE.finditer(data):
        try:
            appmsg=int(m.group(1)); ts=int(m.group(2))
            account=m.group(3).decode('utf-8','replace')
            title=m.group(4).replace(b'\\"',b'"').replace(b'\\\\',b'\\').decode('utf-8','replace')
            # 后处理：正则可能吞进下一个对象的开头（跨对象污染），砍到 }\",{\"AppMsgId 之前
            for marker in ('"},{"AppMsgId', '"},{', '","VideoDuration', '"VideoDuration"'):
                pos=title.find(marker)
                if pos>0: title=title[:pos]
            title=title.strip().rstrip('"').strip()
            if not title: continue
            out.append({'title':title,'account':account,'date_ts':ts,'appmsg_id':appmsg,
                        'date':datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M')})
        except Exception: continue
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--pid',type=int,default=41420)
    ap.add_argument('--out',default=os.path.join(os.path.dirname(__file__),'..','..','_tools','tmp','feed-extracted.json'))
    ap.add_argument('--date',default='',help='只保留该日期 YYYY-MM-DD')
    args=ap.parse_args()
    chunks=read_regions(args.pid)
    print(f'data chunks: {len(chunks)}')
    arts={}
    for data in chunks:
        for a in parse(data):
            arts[a['appmsg_id']]=a
    allarts=list(arts.values())
    print(f'extracted {len(allarts)} articles')
    if args.date:
        allarts=[a for a in allarts if a['date'].startswith(args.date)]
        print(f'after date {args.date}: {len(allarts)}')
    for a in sorted(allarts,key=lambda x:x['date_ts'],reverse=True)[:10]:
        print(f'  {a["date"]} | {a["title"][:36]} | {a["account"]}')
    json.dump(allarts,open(args.out,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    print('saved',args.out)

if __name__=='__main__': main()
