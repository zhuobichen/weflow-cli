#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从微信进程内存提取带 URL 的公众号文章（detailInfo.ContentUrl + showName/Digest）。

数据形状（实测 2026-11-27，PID 2212 Weixin.exe）：
  {\\\"AppMsgId\\\":N,\\\"DateTime\\\":T,\\\"detailInfo\\\":{\\\"ContentUrl\\\":\\\"http://mp.weixin.qq.com/s?...\\\",
   \\\"Digest\\\":\\\"...\\\",...},\\\"showName\\\":\\\"公众号名\\\"}
即三重转义（\\\\\\"）。只读，不联网。

用法: python scripts/extract_feed_with_url.py [--out articles.json]
"""
import argparse, ctypes, ctypes.wintypes as wt, datetime, json, os, re, subprocess, sys, time

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

def wx_pids():
    out=subprocess.run(['tasklist','/FO','CSV'],capture_output=True,text=True,
                       encoding='gbk',errors='replace',timeout=30).stdout
    pids=[]
    for line in out.splitlines():
        if re.search(r'"(Weixin|WeChatAppEx)\.exe"',line,re.I):
            parts=line.split('","')
            if len(parts)>=2:
                try: pids.append((int(parts[1].strip('"')),parts[0].strip('"')))
                except: pass
    return pids

def scan(pid,deadline=150):
    h=k.OpenProcess(0x0400|0x0010,False,pid)
    if not h: return []
    addr=0;mbi=MBI();chunks=[];t0=time.time();CHUNK=16*1024*1024
    try:
        while addr<0x7fffffffffff and time.time()-t0<deadline:
            if k.VirtualQueryEx(h,ctypes.c_void_p(addr),ctypes.byref(mbi),ctypes.sizeof(mbi))==0:break
            sz=mbi.RegionSize
            if mbi.State==0x1000 and mbi.Protect in READABLE and sz<=CHUNK:
                buf=ctypes.create_string_buffer(sz);got=ctypes.c_size_t()
                if k.ReadProcessMemory(h,ctypes.c_void_p(addr),buf,sz,ctypes.byref(got)) and got.value:
                    d=buf.raw[:got.value]
                    if b'ContentUrl' in d and b'mp.weixin.qq.com/s?' in d and b'AppMsgId' in d:
                        chunks.append(d)
            addr+=sz
    finally:k.CloseHandle(h)
    return chunks

# 三重转义：\\\"AppMsgId\\\":N ... \\\"ContentUrl\\\":\\\"URL\\\" ... \\\"showName\\\":\\\"NAME\\\"
# 用宽松匹配 AppMsgId -> ContentUrl 之间，再单独抓 showName / Digest / Title
# 内存里是双重转义 \"（1 反斜杠 + 引号），raw bytes 正则写 \\"
ART = re.compile(rb'AppMsgId\\":(\d+).{0,1500}?ContentUrl\\":\\"(http://mp\.weixin\.qq\.com/s\?[^\\"]+)', re.S)
SHOWNAME = re.compile(rb'showName\\":\\"([^\\"]{1,80})')
DIGEST = re.compile(rb'Digest\\":\\"([^\\"]{0,300})')
TITLE = re.compile(rb'Title\\":\\"([^\\"]{1,200})')
DT = re.compile(rb'DateTime\\":(\d+)')

def parse(chunks):
    arts={}
    for d in chunks:
        for m in ART.finditer(d):
            url=m.group(2).decode('utf-8','replace')
            ts_m=DT.search(d,max(0,m.start()-100),m.end()+200)
            ts=int(ts_m.group(1)) if ts_m else 0
            sn=SHOWNAME.search(d,m.start(),m.end()+3000)
            tt=TITLE.search(d,m.start(),m.end()+3000)
            dg=DIGEST.search(d,m.start(),m.end()+3000)
            title=(tt.group(1) if tt else (sn.group(1) if sn else ''))
            title=title.decode('utf-8','replace') if isinstance(title,bytes) else title
            # 内存读出的标题可能跨进下一个字段（NUL 后是二进制垃圾），先截断再过滤控制字符
            title=title.split('\x00')[0]
            # 半截多字节序列 decode 成 \ufffd（替换字符），它后面就是垃圾，一并截掉
            title=title.split('\ufffd')[0]
            title=''.join(ch for ch in title if ord(ch)>=32 and ch not in '\x7f')
            arts[url]={'title':title,'account':(sn.group(1).decode('utf-8','replace') if sn else ''),
                       'digest':(dg.group(1).decode('utf-8','replace') if dg else ''),
                       'url':url,'date_ts':ts,
                       'date':datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M') if ts else ''}
    return list(arts.values())

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--out',default=r'D:\github_project\ZhaoWen_GitHub维护\_tools\tmp\feed-with-url.json')
    ap.add_argument('--date',default='')
    args=ap.parse_args()
    allarts={}
    for pid,name in wx_pids():
        chunks=scan(pid)
        if not chunks: continue
        arts=parse(chunks)
        print(f'PID {pid} {name}: {len(chunks)} chunks -> {len(arts)} articles')
        for a in arts: allarts[a['url']]=a
    arts=list(allarts.values())
    if args.date: arts=[a for a in arts if a['date'].startswith(args.date)]
    print(f'total {len(arts)} articles with URL')
    for a in sorted(arts,key=lambda x:x['date_ts'],reverse=True)[:10]:
        print(f'  {a["date"]} | {a["title"][:40]} | {a["account"][:16]}')
    json.dump(arts,open(args.out,'w',encoding='utf-8'),ensure_ascii=False,indent=1)
    print('saved',args.out)

if __name__=='__main__': main()
