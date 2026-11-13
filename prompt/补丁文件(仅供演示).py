# 本程序无法再使用，只做示例

import os
import shutil
import datetime

MAIN_FILE = "main.py"

CHANGES = [
    # 1) 技能图标路径：skill_1.png 与 skill_2.png（都在 skill/ 目录下）
    #    这个字符串在"启动时加载"和"选角完成后重新加载"两处出现，都会被替换。
    (
        'skill_path = f"./temp/character/{name}/skill/skill.png"',
        'skill_path = f"./temp/character/{name}/skill/skill_1.png"',
    ),
    (
        'ultimate_path = f"./temp/character/{name}/ultimate/ultimate.png"',
        'ultimate_path = f"./temp/character/{name}/skill/skill_2.png"',
    ),

    # 2) 1P 技能键：暂时不触发任何行为，只保留按键入口，方便以后实现
    (
        "                if keys[pygame.K_k]:\n"
        "                    char1.attack_skill_1()\n"
        "                if keys[pygame.K_l]:\n"
        "                    char1.attack_skill_2()",
        "                # 技能暂未实现：按下 K / L 不触发任何行为，仅保留按键入口\n"
        "                # 将来实现技能时，把下面的 pass 换成对应的攻击调用即可\n"
        "                if keys[pygame.K_k]:\n"
        "                    pass\n"
        "                if keys[pygame.K_l]:\n"
        "                    pass",
    ),

    # 3) 2P 技能键：改为小键盘 3 / 6，暂时不触发任何行为
    (
        "                if keys[pygame.K_KP3]:\n"
        "                    char2.attack_skill_1()\n"
        "                if keys[pygame.K_KP4]:\n"
        "                    char2.attack_skill_2()",
        "                # 技能暂未实现：按下小键盘 3 / 6 不触发任何行为，仅保留按键入口\n"
        "                # 将来实现技能时，把下面的 pass 换成对应的攻击调用即可\n"
        "                if keys[pygame.K_KP3]:\n"
        "                    pass\n"
        "                if keys[pygame.K_KP6]:\n"
        "                    pass",
    ),

    # 4) 2P 技能图标的按键标签：'4' 改为 '6'
    (
        "            p2_keys = ['1', '2', '3', '4']",
        "            p2_keys = ['1', '2', '3', '6']",
    ),
]

def main():
    if not os.path.exists(MAIN_FILE):
        print(f"找不到文件：{MAIN_FILE}")
        print("请确认 main.py 和 patch.py 在同一个文件夹里。")
        input("按回车键退出...")
        return

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"main_backup_{ts}.py"
    shutil.copy2(MAIN_FILE, backup)
    print(f"已备份为：{backup}")

    with open(MAIN_FILE, "r", encoding="utf-8") as f:
        text = f.read()

    total = 0
    for old, new in CHANGES:
        count = text.count(old)
        if count == 0:
            print("警告：以下旧代码没有找到，未替换：")
            print(old)
            print("---")
        else:
            text = text.replace(old, new)
            total += count
            print(f"替换 {count} 处。")

    with open(MAIN_FILE, "w", encoding="utf-8") as f:
        f.write(text)

    print(f"\n完成，共替换 {total} 处。")
    input("按回车键退出...")

if __name__ == "__main__":
    main()
