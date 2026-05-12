"""Extract WeChat 3.x database encryption key from process memory."""
import sys, json, os

def extract_3x_key():
    """Extract 3.x key and return as JSON."""
    # Find WeChat 3.x process
    import psutil
    pid = None
    for proc in psutil.process_iter(['pid', 'name']):
        if proc.info['name'] == 'WeChat.exe':
            pid = proc.info['pid']
            break

    if not pid:
        print(json.dumps({"success": False, "error": "WeChat 3.x (WeChat.exe) 未运行"}))
        return

    # Get exe path
    try:
        exe_path = psutil.Process(pid).exe()
    except:
        exe_path = ""

    # Get wx_dir from registry
    from pywxdump.wx_core.wx_info import get_wx_dir_by_reg, get_info_wxid
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010
    kernel32 = ctypes.windll.kernel32
    hProcess = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)

    wxid = None
    wx_dir = None
    if hProcess:
        try:
            wxid = get_info_wxid(hProcess)
        except:
            pass

    if wxid:
        wx_dir = get_wx_dir_by_reg(wxid)

    if not wx_dir:
        wx_dir = get_wx_dir_by_reg("all")

    # Get msg_dir (parent of wx_dir)
    if wx_dir and wxid:
        msg_dir = os.path.join(wx_dir, wxid, "Msg")
    elif wx_dir:
        msg_dir = os.path.join(wx_dir, "Msg")
    else:
        msg_dir = None

    if not msg_dir or not os.path.exists(msg_dir):
        if hProcess:
            kernel32.CloseHandle(hProcess)
        print(json.dumps({"success": False, "error": f"未找到 3.x 数据目录: {msg_dir}"}))
        return

    # Extract key using memory search
    from pywxdump.wx_core.wx_info import get_key_by_mem_search
    key = get_key_by_mem_search(pid, msg_dir, 8)

    if hProcess:
        kernel32.CloseHandle(hProcess)

    if key:
        result = {
            "success": True,
            "key": key,
            "wxid": wxid,
            "wx_dir": wx_dir,
            "msg_dir": msg_dir,
            "pid": pid,
            "exe_path": exe_path
        }
    else:
        result = {"success": False, "error": "内存搜索未找到 3.x 密钥"}

    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    extract_3x_key()
