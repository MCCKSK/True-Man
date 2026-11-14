import os
import sys

MAIN_FILE = "main.py"

CHANGES = [
    # 示例（把下面内容换成你实际需要修改的代码）：
    # (
    #     "旧代码",
    #     "新代码",
    # ),
]

def main():
    if not os.path.exists(MAIN_FILE):
        print(f"找不到文件：{MAIN_FILE}")
        print("请确认 main.py 和 patch.py 在同一个文件夹里。")
        input("按回车键退出...")
        return

    with open(MAIN_FILE, "r", encoding="utf-8") as f:
        text = f.read()

    total = 0
    failed = False
    for i, (old, new) in enumerate(CHANGES, 1):
        count = text.count(old)
        if count == 0:
            print(f"[{i}] 警告：旧代码没有找到，未替换。")
            print(old[:200] if len(old) > 200 else old)
            print("---")
            failed = True
        else:
            text = text.replace(old, new)
            total += count
            print(f"[{i}] 替换 {count} 处。")

    # 无论是否失败，只要有成功替换就写回 main.py
    with open(MAIN_FILE, "w", encoding="utf-8") as f:
        f.write(text)

    if failed:
        print("\n警告：有部分旧代码没有找到，未完全替换！")
        print("请将上面报“未找到”的编号反馈给 AI，并保留此 patch.py 文件。")
        input("按回车键退出...")
    else:
        print(f"\n完成，共替换 {total} 处。")
        print("补丁已成功应用。")
        input("按回车键将自动删除本补丁文件...")
        try:
            # 获取当前脚本的绝对路径并删除
            _this_file = os.path.abspath(__file__)
            os.remove(_this_file)
            print("本补丁文件已删除。")
        except Exception as e:
            print(f"删除本文件失败：{e}，请手动删除 patch.py。")


if __name__ == "__main__":
    main()