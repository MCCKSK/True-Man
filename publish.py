# -*- coding: utf-8 -*-
r"""
打包整理脚本
1. 调用 pyinstaller 打包 main.py
2. 删除 build 文件夹和 main.spec
3. 从 temp/NONJ.config 第一行提取版本号
4. 将 temp 文件夹及其内部文件复制到 dist/main 内部
5. dist/main -> dist/TrueMan-v{版本号}
6. 移动到 C:\Users\123\Desktop
7. 删除 dist 文件夹
8. 启动 Enigma Virtual Box，并把版本名复制到剪贴板
成功则直接关闭黑窗；出错则暂停并显示原因。
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

PYINSTALLER = Path(r"C:\Users\123\AppData\Local\Programs\Python\Python315\Scripts\pyinstaller.exe")
DESKTOP = Path(r"C:\Users\123\Desktop")
ENIGMA = Path(r"D:\gjl\softwares\Enigma Virtual Box\enigmavb.exe")


def log(msg):
    print(msg, flush=True)


def copy_to_clipboard(text: str) -> bool:
    """把文本写入 Windows 系统剪贴板。非 Windows 或失败时返回 False。"""
    if os.name != "nt":
        return False

    import ctypes
    from ctypes import wintypes

    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002

    kernel32 = ctypes.windll.kernel32
    user32 = ctypes.windll.user32

    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.CloseClipboard.restype = wintypes.BOOL

    # UTF-16LE 文本 + 结尾双字节 0
    data = text.encode("utf-16-le") + b"\x00\x00"

    if not user32.OpenClipboard(None):
        return False
    try:
        if not user32.EmptyClipboard():
            return False
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not h:
            return False
        p = kernel32.GlobalLock(h)
        if not p:
            kernel32.GlobalFree(h)
            return False
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(h)
        # SetClipboardData 成功后，内存所有权移交给系统，不能再 GlobalFree
        if not user32.SetClipboardData(CF_UNICODETEXT, h):
            kernel32.GlobalFree(h)
            return False
        return True
    finally:
        user32.CloseClipboard()


def main():
    base = Path(__file__).resolve().parent
    os.chdir(base)
    log("=" * 48)
    log("  开始打包并整理结果")
    log(f"  工作目录: {base}")
    log("=" * 48)

    # ---------- 1. 调用 pyinstaller 打包 ----------
    log("[1/8] 执行 pyinstaller 打包 ...")
    if not PYINSTALLER.exists():
        log(f"[错误] 找不到 pyinstaller：{PYINSTALLER}")
        return 1
    if not (base / "main.py").exists():
        log(f"[错误] 找不到 {base / 'main.py'}")
        return 1
    if not (base / "icon.ico").exists():
        log("[警告] 当前目录没有 icon.ico，pyinstaller 可能会报错")

    cmd = [str(PYINSTALLER), "main.py", "-w", "-i", "icon.ico"]
    log(f"      {' '.join(cmd)}")
    try:
        ret = subprocess.run(cmd, cwd=str(base)).returncode
    except OSError as e:
        log(f"[错误] 调用 pyinstaller 失败：{e}")
        return 1

    if ret != 0:
        log(f"[错误] pyinstaller 打包失败，返回码 {ret}，已中止")
        return 1
    log("      打包完成")

    # ---------- 2. 删除 build 文件夹和 main.spec ----------
    log("[2/8] 清理 build 文件夹和 main.spec ...")
    build_dir = base / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir)
        log("      已删除 build 文件夹")
    else:
        log("      build 文件夹不存在，跳过")

    spec_file = base / "main.spec"
    if spec_file.exists():
        spec_file.unlink()
        log("      已删除 main.spec")
    else:
        log("      main.spec 不存在，跳过")

    # ---------- 3. 读取版本号 ----------
    log("[3/8] 读取版本号 ...")
    temp_src = base / "temp"
    config = temp_src / "NONJ.config"
    if not config.exists():
        log(f"[错误] 找不到 {config}")
        return 1

    try:
        with open(config, "r", encoding="utf-8-sig") as f:
            ver = f.readline().strip()
    except UnicodeDecodeError:
        with open(config, "r", encoding="gbk") as f:
            ver = f.readline().strip()

    if not ver:
        log(f"[错误] 无法从 {config} 读取版本号")
        return 1

    log(f"      版本号：{ver}")
    target_name = f"TrueMan-v{ver}"

    # ---------- 4. 复制 temp 文件夹到 dist\main 内部 ----------
    log("[4/8] 复制 temp 文件夹到 dist\\main 内部 ...")
    dist_dir = base / "dist"
    src_main = dist_dir / "main"
    if not src_main.exists():
        log(f"[错误] 找不到 {src_main}")
        return 1
    if not temp_src.exists():
        log(f"[错误] 找不到 {temp_src}")
        return 1

    temp_dst = src_main / "temp"
    if temp_dst.exists():
        log(f"      目标 {temp_dst} 已存在，先删除")
        shutil.rmtree(temp_dst)

    shutil.copytree(str(temp_src), str(temp_dst))
    log(f"      已复制到 {temp_dst}")

    # ---------- 5. 重命名 dist\main ----------
    log(f"[5/8] 重命名 dist\\main 为 {target_name} ...")
    dst_main = dist_dir / target_name
    if dst_main.exists():
        log(f"      目标已存在，先删除旧的 {dst_main}")
        shutil.rmtree(dst_main)

    src_main.rename(dst_main)
    log("      重命名完成")

    # ---------- 6. 移动到桌面 ----------
    log(f"[6/8] 移动到 {DESKTOP} ...")
    if not DESKTOP.exists():
        log(f"[错误] 目标路径不存在：{DESKTOP}")
        return 1

    dst_desktop = DESKTOP / target_name
    if dst_desktop.exists():
        log("      桌面已存在同名文件夹，先删除")
        shutil.rmtree(dst_desktop)

    shutil.move(str(dst_main), str(dst_desktop))
    log("      移动完成")

    # ---------- 7. 删除 dist ----------
    log("[7/8] 删除 dist 文件夹 ...")
    if dist_dir.exists():
        shutil.rmtree(dist_dir)
        log("      已删除 dist 文件夹")

    # ---------- 8. 启动 Enigma Virtual Box ----------
    log("[8/8] 启动 Enigma Virtual Box ...")
    if not ENIGMA.exists():
        log(f"[错误] 找不到 {ENIGMA}")
        return 1

    # DETACHED_PROCESS：让 Enigma 不继承本控制台，黑窗关闭不影响它
    detached = getattr(subprocess, "DETACHED_PROCESS", 0x00000008) if os.name == "nt" else 0
    try:
        subprocess.Popen(
            [str(ENIGMA)],
            cwd=str(ENIGMA.parent),
            creationflags=detached,
            close_fds=True,
        )
        log(f"      已启动：{ENIGMA}")
    except OSError as e:
        log(f"[错误] 启动 Enigma Virtual Box 失败：{e}")
        return 1

    # 把版本名复制到剪贴板，方便在 EVB 里直接粘贴
    if copy_to_clipboard(target_name):
        log(f"      已复制版本名到剪贴板：{target_name}")
    else:
        log("      [警告] 复制版本名到剪贴板失败（不影响打包结果）")

    log("")
    log("=" * 48)
    log("  全部完成！")
    log(f"  输出位置：{dst_desktop}")
    log(f"  内含 temp 目录：{dst_desktop / 'temp'}")
    log(f"  剪贴板内容：{target_name}")
    log("  Enigma Virtual Box 已打开，请手动完成封装")
    log("=" * 48)
    return 0


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:
        print(f"\n[未捕获异常] {e}", file=sys.stderr)
        code = 1

    if code != 0:
        # 只有出错才停住，方便看原因
        try:
            input("\n程序出错，按回车键退出...")
        except EOFError:
            pass
    sys.exit(code)