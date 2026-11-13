import pygame
import sys
import random
import os
import json
import math
import re
import socket
import collections

# 使用自定义字典维护按键状态，解决 Pygame 2.x 键码错位及拖动窗口卡键问题
_custom_key_state = collections.defaultdict(bool)

# ---------- 安全初始化 ----------
try:
    pygame.init()
except pygame.error:
    pygame.display.init()
    pygame.font.init()

# 禁用系统输入法（IME）对键盘事件的拦截
try:
    pygame.key.stop_text_input()
except Exception:
    pass

audio_available = False
try:
    if not pygame.mixer.get_init():
        pygame.mixer.init()
    audio_available = True
except pygame.error:
    audio_available = False

# 常量
W, H = 800, 600
screen = pygame.display.set_mode((W, H))

# 强制获取窗口焦点（针对 Windows 系统常见的焦点丢失 Bug）
import sys
if sys.platform == 'win32':
    try:
        import ctypes
        wm_info = pygame.display.get_wm_info()
        if 'window' in wm_info:
            hwnd = wm_info['window']
            ctypes.windll.user32.SetForegroundWindow(hwnd)
            ctypes.windll.user32.SetFocus(hwnd)
    except Exception:
        pass

FPS = 60
clock = pygame.time.Clock()

with open("./temp/NONJ.config", "r", encoding="utf-8") as f:
    ver = f.readline().strip()
    _stamina_line = f.readline().strip()

# 体力击飞返还配置：NONJ.config 第二行格式 "1000:10"，
# 表示目标处于击飞 / 倒地 / 起立前时，攻击方每累计造成 1000 伤害回 10 体力。
# 未写、空行或格式错误时使用默认值 1000:10。
STAMINA_HIT_DAMAGE_PER = 1000
STAMINA_HIT_RECOVER = 10
if _stamina_line and ":" in _stamina_line:
    try:
        _s_a, _s_b = _stamina_line.split(":", 1)
        _s_a = int(_s_a.strip())
        _s_b = int(_s_b.strip())
        if _s_a > 0 and _s_b >= 0:
            STAMINA_HIT_DAMAGE_PER = _s_a
            STAMINA_HIT_RECOVER = _s_b
    except ValueError:
        pass

def parse_version(v):
    """解析版本字符串，返回 (数字列表, 后缀字符串)。
    后缀为 '-' 后面的内容；无后缀时为空字符串。
    数字部分从前往后按 "." 切分，遇到非数字段就停止。
    例如 "1.3.12" -> ([1, 3, 12], "")；
         "1.3.3-beta" -> ([1, 3, 3], "beta")。"""
    if not v:
        return [], ""
    if "-" in v:
        main, suffix = v.split("-", 1)
    else:
        main, suffix = v, ""
    main = main.strip()
    suffix = suffix.strip()
    parts = []
    for seg in main.split("."):
        seg = seg.strip()
        if seg.isdigit():
            parts.append(int(seg))
        else:
            break
    return parts, suffix

def compare_version(v1, v2):
    """逐级比较两个版本号。返回 1 表示 v1 大，-1 表示 v1 小，0 表示相等。
    数字段长度不足的位置按 0 补齐；数字全相等时再比较后缀，
    无后缀 > 有后缀，有后缀时按字典序比较（beta > alpha）。"""
    p1, s1 = parse_version(v1)
    p2, s2 = parse_version(v2)
    _n = max(len(p1), len(p2))
    for _i in range(_n):
        _a = p1[_i] if _i < len(p1) else 0
        _b = p2[_i] if _i < len(p2) else 0
        if _a > _b:
            return 1
        if _a < _b:
            return -1
    if s1 == s2:
        return 0
    if not s1:
        return 1
    if not s2:
        return -1
    return 1 if s1 > s2 else -1

pygame.display.set_caption(f"TrueMan {ver}")

# 窗口刚创建就立即设置图标，确保在窗口显示前生效。
# 这里直接用 pygame.image.load，不经过 convert_alpha，
# 避免部分平台在窗口尚未完全初始化时 convert 失败而被跳过。
try:
    _icon_surf = pygame.image.load("./temp/icon.png")
    pygame.display.set_icon(_icon_surf)
except Exception:
    pass

# 状态常量
MENU = 1
SINGLE_SELECT = 2
DUAL_SELECT = 3
ABOUT = 4
LOADING = 5
SHOW_MAP = 6
FIGHT = 7
DUAL_SELECT_1P = 8
DUAL_SELECT_2P = 9
FIGHT_KO = 10
SKILL_SELECT_1P = 11
SKILL_SELECT_2P = 12
SOLO_SETTINGS = 18     # 单人模式的战斗中设置（换角色/携带技能/地图）
KEYS = 19              # 按键设置页面

# 局域网联机状态
LAN_SELECT = 13        # 局域网子菜单：创建房间 / 加入房间
LAN_HOST_WAIT = 14     # 主机等待对方加入
LAN_CLIENT_INPUT = 15  # 客户端输入 IP
LAN_READY = 16         # 双方准备页面
WAIT_HOST = 17         # 联机客户端等待主机开始战斗

# 局域网监听端口（主机和客户端都用这个端口）
LAN_PORT = 12345

SHAKE_DURATION = 12
STATE = MENU

# 地图实际宽度（世界坐标；窗口 W=800，但地图 1000 宽）
MAP_W = 1000
# 横向相机偏移：屏幕 x = 世界 x - camera_x
camera_x = 0
# 相机平滑系数：每帧朝目标位置移动剩余距离的该比例。
# 越小越平滑但跟随越慢；越大越灵敏但越生硬。
CAMERA_LERP = 0.15

def get_world_bounds():
    """返回当前允许角色活动的世界坐标范围 (left, right)。
    联机模式：整张地图 0 ~ MAP_W。
    本地 / 训练模式：当前相机视野 camera_x ~ camera_x + W。
    """
    if network_connected:
        return 0, MAP_W
    return int(camera_x), int(camera_x) + W

# ---------- 按键映射 ----------
# 战斗中所有按键都可以在按键设置页面修改，修改后保存到运行目录下的
# keys.config 文件，下次启动会自动载入；文件不存在时使用默认值。
DEFAULT_KEYMAP = {
    'p1_up': pygame.K_w,
    'p1_down': pygame.K_s,
    'p1_left': pygame.K_a,
    'p1_right': pygame.K_d,
    'p1_attack': pygame.K_h,
    'p1_escape': pygame.K_u,
    'p1_skill1': pygame.K_j,
    'p1_skill2': pygame.K_k,
    'p1_extra': pygame.K_i,
    'p2_up': pygame.K_UP,
    'p2_down': pygame.K_DOWN,
    'p2_left': pygame.K_LEFT,
    'p2_right': pygame.K_RIGHT,
    'p2_attack': pygame.K_KP1,
    'p2_escape': pygame.K_KP5,
    'p2_skill1': pygame.K_KP2,
    'p2_skill2': pygame.K_KP3,
    'p2_extra': pygame.K_KP6,
}

# 动作的中文显示名
KEY_ACTION_NAMES = {
    'p1_up': '上', 'p1_down': '下', 'p1_left': '左', 'p1_right': '右',
    'p1_attack': '普攻', 'p1_escape': '逃脱',
    'p1_skill1': '技能一', 'p1_skill2': '技能二', 'p1_extra': '携带',
    'p2_up': '上', 'p2_down': '下', 'p2_left': '左', 'p2_right': '右',
    'p2_attack': '普攻', 'p2_escape': '逃脱',
    'p2_skill1': '技能一', 'p2_skill2': '技能二', 'p2_extra': '携带',
}

# 按键设置页面的显示顺序：先 1P 的 9 项，再 2P 的 9 项
KEY_ORDER = [
    'p1_up', 'p1_down', 'p1_left', 'p1_right',
    'p1_attack', 'p1_escape', 'p1_skill1', 'p1_skill2', 'p1_extra',
    'p2_up', 'p2_down', 'p2_left', 'p2_right',
    'p2_attack', 'p2_escape', 'p2_skill1', 'p2_skill2', 'p2_extra',
]

SETTINGS_CONFIG_FILE = "settings.config"

def key_display_name(key_code):
    """把 pygame 键码转成简短显示名。"""
    try:
        name = pygame.key.name(key_code)
    except Exception:
        return "?"
    name_map = {
        'up': 'UP', 'down': 'DOWN', 'left': 'LEFT', 'right': 'RIGHT',
        'return': 'Enter', 'escape': 'Esc', 'space': 'Space',
        'left shift': 'LShift', 'right shift': 'RShift',
        'left ctrl': 'LCtrl', 'right ctrl': 'RCtrl',
        'left alt': 'LAlt', 'right alt': 'RAlt',
        'keypad 0': 'KP0', 'keypad 1': 'KP1', 'keypad 2': 'KP2',
        'keypad 3': 'KP3', 'keypad 4': 'KP4', 'keypad 5': 'KP5',
        'keypad 6': 'KP6', 'keypad 7': 'KP7', 'keypad 8': 'KP8',
        'keypad 9': 'KP9',
        'keypad +': 'KP+', 'keypad -': 'KP-', 'keypad *': 'KP*',
        'keypad /': 'KP/', 'keypad .': 'KP.', 'keypad enter': 'KPEnter',
        'tab': 'Tab', 'backspace': 'Bksp', 'caps lock': 'Caps',
        'num lock': 'NumLk', 'scroll lock': 'ScrLk',
        'page up': 'PgUp', 'page down': 'PgDn',
        'insert': 'Ins', 'delete': 'Del', 'home': 'Home', 'end': 'End',
    }
    if name in name_map:
        return name_map[name]
    return name.upper()

def _read_settings_file():
    """读取整个 settings.config 到字典。忽略空行和 # 开头的注释行。
    返回的字典中：按键项 key 就是动作名（p1_up 等），其他配置项 key 自定义。
    """
    result = {}
    if not os.path.exists(SETTINGS_CONFIG_FILE):
        return result
    try:
        with open(SETTINGS_CONFIG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                result[k.strip()] = v.strip()
    except Exception:
        pass
    return result

def _write_settings_file(d):
    """整体写回 settings.config。按键部分固定按 KEY_ORDER 顺序，
    其他配置项按字母序排在后面。"""
    try:
        with open(SETTINGS_CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write("# True Man 配置文件\n")
            f.write("# 按键映射（可在游戏内按键设置页面修改）\n")
            for k in KEY_ORDER:
                if k in d:
                    f.write(f"{k}={d[k]}\n")
            f.write("\n")
            f.write("# 其他配置\n")
            for k in sorted(d.keys()):
                if k not in KEY_ORDER:
                    f.write(f"{k}={d[k]}\n")
    except Exception:
        pass

def load_setting(key, default=None):
    """读取单个配置项（按键之外的通用配置）。"""
    d = _read_settings_file()
    return d.get(key, default)

def save_setting(key, value):
    """保存单个配置项，不影响其他键。"""
    d = _read_settings_file()
    d[key] = str(value)
    _write_settings_file(d)

def load_keymap():
    """从 settings.config 载入按键映射；文件不存在或读取失败时用默认。"""
    km = dict(DEFAULT_KEYMAP)
    d = _read_settings_file()
    for k, v in d.items():
        if k in km and v:
            try:
                km[k] = pygame.key.key_code(v)
            except Exception:
                pass
    return km

def save_keymap(km):
    """把按键映射写入 settings.config，不影响其他配置项。"""
    d = _read_settings_file()
    for k, code in km.items():
        try:
            d[k] = pygame.key.name(code)
        except Exception:
            d[k] = ""
    _write_settings_file(d)

# 全局按键映射：程序启动时载入
keymap = load_keymap()

def get_key_item_rect(i):
    """按键设置页面：第 i 项的矩形。i<9 是 1P，i>=9 是 2P。"""
    if i < 9:
        col_x = 60
        row = i
    else:
        col_x = 420
        row = i - 9
    y = 90 + row * 48
    return pygame.Rect(col_x, y, 320, 42)


# ---------- KO 结算状态 ----------
# ko_phase: 0=正常游戏，非 0=KO 结算中
KO_ANIM_FRAMES = 300   # KO 击飞动画持续帧数（约 5 秒）
KO_MAX_BOUNCES = 3            # 落地后最多反弹次数
KO_BOUNCE_DECAY = 0.75        # 每次反弹后速度衰减比例（越小反弹越低）
KO_BOUNCE_MIN_SPEED = 2.0     # 下落速度低于该值时不再反弹
KO_FALL_GRAVITY_SCALE = 0.3   # 击飞阶段的重力系数（越小下落越慢）
# 血溅粒子列表：每个元素为 [x, y, vx, vy, life, size]
blood_particles = []
# 白烟粒子列表（携带技能1隐身施放时生成），格式同 blood_particles
smoke_particles = []
ko_phase = 0
ko_timer = 0
ko_loser_index = 0     # 0 表示 1P 被击倒，1 表示 2P 被击倒
ko_loser_airborne = False
ko_bounce_count = 0

# ---------- 战斗开场阶段常量 ----------
# 进入战场后先播放一段开场：
#   ready 印章 -> 保持 3 秒 -> start_fight 印章 -> 保持 1.5 秒 -> 战斗开始
# 战斗开始后，所有 UI 会从最近的屏幕边缘滑入。
FIGHT_INTRO_READY_STAMP = 0
FIGHT_INTRO_READY_HOLD = 1
FIGHT_INTRO_START_STAMP = 2
FIGHT_INTRO_START_HOLD = 3
FIGHT_INTRO_DONE = 4
INTRO_STAMP_FRAMES = 18        # 印章盖下动画时长
INTRO_HOLD_FRAMES = 90         # ready 保持 1.5 秒
INTRO_START_HOLD_FRAMES = 30   # start_fight 保持 0.5 秒
UI_SLIDE_FRAMES = 60           # UI 从边缘滑入时长（1 秒），更容易看清

# ---------- 受击保护机制参数 ----------
# 累计伤害达到不同阈值后，后续受到的伤害按倍率打折：
#   累计 >= 10000 且 < 15000 ：伤害 × 0.85（-15%）
#   累计 >= 15000 且 < 30000 ：伤害 × 0.70（-30%）
#   累计 >= 30000            ：伤害 × 0.50（-50%）
PROTECTION_THRESHOLD_1 = 10000
PROTECTION_THRESHOLD_2 = 15000
PROTECTION_THRESHOLD_3 = 30000
PROTECTION_MULT_1 = 0.85
PROTECTION_MULT_2 = 0.70
PROTECTION_MULT_3 = 0.50

PROTECTION_BORDER_WIDTH_0 = 3
PROTECTION_BORDER_WIDTH_1 = 5
PROTECTION_BORDER_WIDTH_2 = 7
PROTECTION_BORDER_WIDTH_3 = 9

# ============================================================================
# 技能系统总览
# ============================================================================
# 本游戏目前把技能分为三种形式，各自对应不同的行为模式和状态变量。
# 新增技能时，请先判断它属于哪一种，然后按下面对应的模式去实现。
#
# 重要：这三种形式是对“技能行为”的分类，与具体角色无关。
# 每个角色可以自由选择实现或不实现某类技能。
# 例如：chr 目前实现了技能一（普通）和技能二（存储）；
#       gjl 目前只实现了携带技能（持续），技能一/技能二暂未实现。
#
# ---------------------------------------------------------------------------
# 形式 1：普通技能（一次施放，然后进入冷却）
# ---------------------------------------------------------------------------
# 语义：
#   按下按键 -> 触发一次动作（例如 前摇 -> 释放 -> 收招）-> 立即进入冷却。
#   冷却结束后可再次使用，不做其他限制。
#
# 典型代表：chr 的一技能（投掷书本）
#   - 相关常量：
#       BOOK_SPEED / BOOK_DAMAGE / BOOK_WAVE_AMPLITUDE / BOOK_WAVE_SPEED
#   - 相关状态变量（Character 实例上）：
#       pending_book_launch   前摇剩余帧数；
#                             大于 0 表示正在前摇（硬控，不能移动/攻击）
#   - 冷却字段：
#       skills['skill_1']['current_cd'] / skills['skill_1']['cd']
#   - 触发流程（战斗循环里）：
#       1) 边沿检测按键（如 J / 小键盘 2），如果冷却好了且没有正在前摇 ->
#          设置 current_cd = cd，把 pending_book_launch 设为前摇帧数
#       2) 每帧递减 pending_book_launch，减到 0 时真正发射（生成投射物）
#       3) 前摇期间如果被打中或倒地，取消前摇，但冷却不退还
#
# ---------------------------------------------------------------------------
# 形式 2：存储技能（有使用次数，次数用完后进入冷却）
# ---------------------------------------------------------------------------
# 语义：
#   按下按键 -> 立即触发一次动作 -> 使用次数减 1。
#   次数 > 0 时按一下就能用；次数为 0 时进入冷却；
#   冷却结束把次数重置为满。
#
# 典型代表：chr 的二技能（一次投出 3 本书）
#   - 相关常量：
#       SKILL2_THROW_COUNT         总释放次数
#       SKILL2_BOOKS_PER_THROW     每次同时投出的书本数量
#       SKILL2_BOOK_VERTICAL_SPACING 相邻两本书之间的缝隙（像素）
#   - 相关状态变量（Character 实例上）：
#       skill2_uses_left   剩余释放次数；
#                          大于 0 表示还能按，0 表示进入冷却
#   - 冷却字段：
#       skills['skill_2']['current_cd'] / skills['skill_2']['cd']
#   - 触发流程（战斗循环里）：
#       1) 边沿检测按键（如 K / 小键盘 3），
#          如果 skill2_uses_left > 0 且不在冷却 ->
#          立即投出书本，skill2_uses_left -= 1
#       2) 当 skill2_uses_left 减到 0 时，设置 current_cd = cd
#       3) update_physics 的冷却循环里，如果 current_cd <= 0 且
#          uses_left <= 0，把 uses_left 重置回 SKILL2_THROW_COUNT
#   - 图标显示：
#       技能图标中央会用黄色文字显示“剩余/总数”（如 3/5）
#
# ---------------------------------------------------------------------------
# 形式 3：持续技能（持续一段时间，持续期间可以再次按键提前结束）
# ---------------------------------------------------------------------------
# 语义：
#   按下按键 -> 进入持续状态，持续 N 帧。持续期间：
#     - 会持续施加某种效果（隐身、无敌、加速等）
#     - 再次按键可以提前结束
#     - 被打中也可以提前结束（可选，视技能而定）
#     - 结束时进入冷却
#
# 典型代表：携带技能 1（隐身）
#   - 相关常量：
#       EXTRA_INVIS_SECONDS / EXTRA_INVIS_FRAMES / EXTRA_INVIS_ALPHA
#       EXTRA_INVIS_FLASH_PERIOD / EXTRA_INVIS_FLASH_FRAMES /
#       EXTRA_INVIS_FLASH_ALPHA
#   - 相关状态变量（Character 实例上）：
#       extra_invis_active   是否处于持续状态
#       extra_invis_timer    剩余持续帧数
#       extra_invis_phase    闪烁周期相位（视觉用）
#   - 冷却字段：
#       skills['extra']['current_cd'] / skills['extra']['cd']
#   - 触发流程（战斗循环里）：
#       1) 边沿检测按键（如 B / 小键盘 6）：
#            - 如果已经在持续状态 -> 调用 _end_extra_invis() 提前结束
#            - 如果不在持续状态且不在冷却 -> 开启持续状态
#       2) 每帧递减 extra_invis_timer，减到 0 时自动结束
#       3) 结束时把 current_cd = cd（进入冷却）
#
# ---------------------------------------------------------------------------
# 通用说明
# ---------------------------------------------------------------------------
# - 所有技能的冷却都在 Character.update_physics 里统一递减：
#     for key in ('escape', 'skill_1', 'skill_2', 'extra'):
#         if cd_enabled: 递减
#         else: current_cd = 0（表示关闭 CD 模式，可以随时释放）
#   新增技能时，如果这个技能也需要冷却，就把它的 key 加进这个循环。
#
# - 所有技能的“按键边沿检测”采用同样的模式：
#     按下时：if not <xxx>_pressed: 触发一次；<xxx>_pressed = True
#     松开时：<xxx>_pressed = False
#   相关变量存在 Character 实例上
#   （如 attack_pressed / escape_pressed / skill_pressed / extra_pressed 等）。
#   新增技能时，建议同样加一个 <新技能名>_pressed 布尔字段。
#
# - 前摇（recovery 字段，单位帧）统一来自 character_data.json 里对应技能
#   的 "recovery" 字段。前摇期间角色被“硬控”：不能移动、跳跃、普攻，
#   会显示站立帧或专用图片（如 chr/skill/skill_1_use.png）。
#
# ============================================================================

# ---------- 体力系统参数 ----------
STAMINA_MAX = 100                 # 体力上限
STAMINA_START = 50                # 每局开局体力
STAMINA_REGEN_FRAMES = int(3.0 * FPS)  # 每 3 秒恢复 1 点
ESCAPE_STAMINA_COST = 25          # 逃脱消耗
DEFAULT_SKILL_STAMINA_COSTS = [10, 25]  # 技能一 / 技能二默认消耗

# ---------- 逃脱技能参数 ----------
ESCAPE_DURATION_SECONDS = 2.0
ESCAPE_DURATION_FRAMES = int(ESCAPE_DURATION_SECONDS * FPS)
ESCAPE_CD_SECONDS = 20.0
ESCAPE_CD_FRAMES = int(ESCAPE_CD_SECONDS * FPS)
INVINCIBLE_ALPHA = 128

# ---------- 起身无敌参数 ----------
# 每次从倒地状态成功站起后，立即获得 1.5 秒无敌
STAND_UP_INVINCIBLE_SECONDS = 1.5
STAND_UP_INVINCIBLE_FRAMES = int(STAND_UP_INVINCIBLE_SECONDS * FPS)

# ---------- 携带技能参数 ----------
# 排序为 1 的携带技能：使用后角色完全隐身（透明不可见），持续一段时间。
# 期间被打到或再次按下按键就立即结束，结束时进入冷却。
# EXTRA_INVIS_ALPHA = 0 表示完全透明；想让隐身者隐约看到自己可改成 30~50。
EXTRA_INVIS_SECONDS = 10.0
EXTRA_INVIS_FRAMES = int(EXTRA_INVIS_SECONDS * FPS)
EXTRA_INVIS_ALPHA = 0
# 隐身期间每 EXTRA_INVIS_FLASH_PERIOD 帧闪一次，闪的时候角色短暂显示，
# 便于对手判断大致位置。EXTRA_INVIS_FLASH_FRAMES 是每次闪的持续帧数，
# EXTRA_INVIS_FLASH_ALPHA 是闪的时候的不透明度（204 约等于 80% 不透明）。
EXTRA_INVIS_FLASH_PERIOD = 120   # 2 秒
EXTRA_INVIS_FLASH_FRAMES = 12    # 0.2 秒
EXTRA_INVIS_FLASH_ALPHA = 51

# ---------- chr 技能一（投掷书本）参数 ----------
BOOK_SPEED = 12            # 平飞速度（像素/帧）
BOOK_DAMAGE = 1800         # 技能一命中伤害
BOOK_DAMAGE_SKILL2 = 1234  # 技能二命中伤害
BOOK_WAVE_AMPLITUDE = 8    # 上下摆动幅度（像素）
BOOK_WAVE_SPEED = 0.18     # 摆动相位增长速度（越大上下摆得越快）

# ---------- chr 技能二（三书连投）参数 ----------
SKILL2_THROW_COUNT = 3               # 技能二总释放次数（每次按下投出 3 本书）
SKILL2_BOOKS_PER_THROW = 3           # 每次同时投出的书本数量
SKILL2_BOOK_VERTICAL_SPACING = 10    # 相邻两本书之间的缝隙（上一本底部到下一本顶部）

# ---------- 普攻连击窗口参数 ----------
# 上一阶段后摇结束后，在这个时间内按下普攻即可接下一 a。
# 必须是"新按下"（先松开再按），一直按住不算。
ATTACK_COMBO_WINDOW_FRAMES = int(0.2 * FPS)   # 0.2 秒 = 12 帧

# ---------- 携带技能 2（持续震屏 + 武器滑过屏幕）参数 ----------
SKILL2_2_TRIGGER_DELAY_FRAMES = 60    # 按下后先震动 1 秒（60 帧）才出招
SKILL2_2_WEAPON_SPEED = 8             # 武器水平滑动速度（像素/帧），中等速度
SKILL2_2_WEAPON_DURATION_FRAMES = 200 # 武器滑过屏幕的固定时长（帧），略长于实际
SKILL2_2_TRAIL_SHAKE_FRAMES = 60      # 武器滑出后再震动 1 秒（60 帧）
SKILL2_2_DAMAGE = 100                 # 每次碰到敌人的伤害
SKILL2_2_HIT_INTERVAL_FRAMES = 6      # 伤害间隔 0.1 秒 = 6 帧（1 秒最多 10 次 = 300 伤害）
SKILL2_2_SHAKE_OFFSET = 3             # 屏幕震动最大偏移（像素）

# ---------- gjl 技能一（幻影突进）参数 ----------
# 按下技能键后先进入前摇（时长来自 character_data.json 的
# skills.skill_1.recovery）：
#   skill_1_use.png 从不透明渐变为透明，前摇结束刚好完全透明；
#   同时 trade.png 从透明渐变为不透明。
# 前摇结束后进入突进阶段（0.1 秒 = 6 帧）：
#   向前移动 500 像素，期间碰到敌人造成 555 伤害并击飞。
# 突进结束后立即恢复：trade.png 变透明、skill_1_use.png 变不透明。
# 注：gjl 技能一的前摇时长从 character_data.json 的 skills.skill_1.recovery
# 读取，不再使用独立常量。下面这条常量仅为兼容保留，代码中不再引用。
GJL_SKILL1_DASH_FRAMES = int(0.1 * FPS)   # 突进持续帧数（0.1 秒 = 6 帧）
GJL_SKILL1_DASH_DISTANCE = 500            # 突进总距离（像素）
GJL_SKILL1_DASH_SPEED = GJL_SKILL1_DASH_DISTANCE / GJL_SKILL1_DASH_FRAMES  # 每帧位移
GJL_SKILL1_DAMAGE = 832                   # 命中基础伤害
GJL_SKILL1_STORE_COUNT = 3                # 存储技能的可用次数（一次可连放 3 次）
GJL_SKILL1_DAMAGE_PER_PIXEL = 2           # 冲刺距离每像素额外叠加的伤害

# ---------- gjl 技能二（锁定 code）参数 ----------
# 按下技能二 -> 前摇（期间显示 skill_2_use.png，原地不动）->
# 前摇结束记录敌方此刻位置，从角色位置朝该方向发射 code.png。
# code 直线飞行，飞行期间技能二不进入 CD；
# code 出屏后技能二进入 CD。
# 若 code 还在飞行时玩家再次按下技能二：
#   删除原 code，生成两个更快的曲线 code，
#   初始方向分别为原方向 ±90 度（垂直），
#   之后每帧转向此刻锁定的敌方位置（画曲线），
#   技能二立即进入 CD。
GJL_SKILL2_WINDUP_FRAMES = 18          # 前摇时长（帧）
GJL_SKILL2_CODE_SPEED = 8              # 第一段 code 速度（像素/帧）
GJL_SKILL2_CODE2_SPEED = 16            # 第二段 code 速度（更快）
GJL_SKILL2_CODE_DAMAGE = 1343          # 第一段单个 code 命中伤害
GJL_SKILL2_CODE2_DAMAGE = 911          # 分裂出的每个小 code 命中伤害
GJL_SKILL2_CODE2_TURN = 0.08           # 第二段每帧转向系数（0~1，越大弯得越急）
GJL_SKILL2_STORE_COUNT = 3             # 技能二改为存储技能的可用次数

# ---------- wyk 技能一（扔蛋）参数 ----------
WYK_SKILL1_EGG_GRAVITY = 0.5          # 每帧重力加速度
WYK_SKILL1_TRANSFORM_FRAMES = int(5.0 * FPS)  # 变身持续帧数（5 秒）
WYK_SKILL1_REVERT_DAMAGE = 1532       # 变回来时施加的击飞伤害
WYK_SKILL1_EGG_PARTICLE_COUNT = 20    # 蛋碎时生成的粒子数量
WYK_SKILL1_STORE_COUNT = 3            # 存储技能可用次数
# 抛物线飞行时间的计算参数：n = FLIGHT_MIN + |dx| / FLIGHT_PER_PIXEL
# 这样扔出的蛋大约飞行 0.4~1 秒，对手有足够时间走位规避。
WYK_SKILL1_EGG_FLIGHT_MIN = 25        # 抛物线最小飞行帧数
WYK_SKILL1_EGG_FLIGHT_PER_PIXEL = 25  # 每多少像素水平距离增加 1 帧飞行时间

# ---------- wyk 技能二（声波）参数 ----------
WYK_SKILL2_DAMAGE = 574                     # 声波命中伤害
WYK_SKILL2_DURATION_FRAMES = int(0.5 * FPS) # 声波持续帧数（0.5 秒 = 30 帧）
WYK_SKILL2_VOICE_OFFSET_X = 70              # 声波中心相对角色的水平偏移（前方）
WYK_SKILL2_VOICE_OFFSET_Y = -60             # 声波中心相对角色底部的垂直偏移
WYK_SKILL2_VOICE_SHAKE = 4
WYK_SKILL2_HIT_INTERVAL_FRAMES = 8                  # 声波每帧随机震动幅度（像素）
WYK_SKILL2_HIT_VY = -5.0                    # 命中时施加的"很小高度"击飞初速度

# ---------- chr_1 技能一（环绕追踪书）参数 ----------
CHR1_SKILL1_DAMAGE = 932                    # 每本书命中伤害
CHR1_SKILL1_BOOK_SPEED = 10                 # 追踪书速度（像素/帧）
CHR1_SKILL1_BOOK_TURN = 0.10                # 追踪转向系数（0~1，越大弯得越急）
CHR1_SKILL1_ORBIT_RADIUS = 100              # 环绕半径（像素）
CHR1_SKILL1_ORBIT_SPEED = 0.025             # 环绕角速度（弧度/帧）
CHR1_SKILL1_STORE_COUNT = 3                 # 存储技能可用次数

# ---------- chr_1 技能二（旋转散射书）参数 ----------
CHR1_SKILL2_BOOK_COUNT = 16                 # 每次旋转散射的书本数量
CHR1_SKILL2_BOOK_SPEED = 8                  # 每本书的飞行速度（像素/帧）
CHR1_SKILL2_DAMAGE = 125                    # 每本书命中伤害
CHR1_SKILL2_ANGULAR_SPEED = 0.03            # 每帧角度增量（弧度），越大弯得越急
# chr_1 所有书本（环绕书、追踪书、散射书）的寿命与渐隐
CHR1_BOOK_LIFETIME_FRAMES = int(10.0 * FPS)  # 书本存活帧数（10 秒）
CHR1_BOOK_FADE_FRAMES = int(1.0 * FPS)       # 书本渐隐帧数（1 秒）
# chr_1 自动二技能：对方倒地期间被 book 击中累计到阈值时，
# 自动无 CD 持续发射散射书一段时间。
CHR1_AUTO_SKILL2_TRIGGER_HITS = 30          # 触发所需的累计命中次数
CHR1_AUTO_SKILL2_DURATION_FRAMES = 5         # 爆发持续帧数（5 帧，每帧一次）
CHR1_AUTO_SKILL2_INTERVAL = 1                # 爆发期间每多少帧发射一轮
# chr_1 自动二技能：对方倒地期间被 book 击中累计到阈值时，
# 自动无 CD 连续发射散射书 5 帧。
CHR1_AUTO_SKILL2_TRIGGER_HITS = 30          # 触发所需的累计命中次数
CHR1_AUTO_SKILL2_DURATION_FRAMES = 5         # 爆发持续帧数（5 帧，每帧一次）
CHR1_AUTO_SKILL2_INTERVAL = 1                # 爆发期间每多少帧发射一轮


# ---------- 单人模式 CD 开关 ----------
# 单人战斗界面右上角会显示一个开关按钮，控制技能冷却是否生效。
# 关闭时所有技能的 current_cd 每帧都会被清零，等于没有冷却。
# 双人模式固定开启 CD（进入双人战斗时会强制设回 True）。
cd_enabled = True
# 右上角开关按钮的矩形位置与大小
CD_TOGGLE_RECT = pygame.Rect(W - 140, 15, 125, 40)
# 单人模式战斗中设置按钮的矩形（在 CD 开关下方）
SOLO_SETTINGS_BTN_RECT = pygame.Rect(W - 140, 65, 125, 40)

# 训练模式下右上角按钮的位置：
# 训练模式战斗时有 2P，右上角 HUD 有 2P 头像和血条（y=15~70 区域），
# 原来的按钮位置会和血条重叠，所以整体下移。
# 训练模式下有三个按钮：CD 开关、设置、回满血。
# 从上到下：设置 / CD / 回满血
TRAINING_SETTINGS_BTN_RECT = pygame.Rect(W - 140, 90, 125, 35)
TRAINING_CD_TOGGLE_RECT = pygame.Rect(W - 140, 130, 125, 35)
TRAINING_HEAL_BTN_RECT = pygame.Rect(W - 140, 170, 125, 35)

# ---------- 训练模式标志 ----------
# 训练模式：从单人选角进入，但战斗时有两个角色。
# 1P 是玩家选的角色，2P 自动填充为 chr 并携带技能 1。
# 双人可同时操作；2P 死亡后不结算、倒地不动，点击回满血按钮可复活。
training_mode = False

# ---------- 局域网联机全局状态 ----------
# role: "host" 或 "client"；未联机时为 None
network_role = None
# 主机：监听用的 socket；客户端：连接后的 socket
network_sock = None
# 主机专用：accept 到的对方 socket
network_client_sock = None
network_connected = False
network_recv_buffer = ""
network_local_ready = False    # 自己是否已准备
network_remote_ready = False   # 对方是否已准备
network_peer_char_name = None  # 联机时收到对方选的角色名（选角阶段用）
network_peer_skill_num = None  # 联机时收到对方选的携带技能编号
network_remote_input = {}      # 主机专用：客户端最新一帧的按键输入
network_peer_state = None      # 客户端专用：主机最新一帧的战斗状态
# 网络延迟测量：本机每 0.5 秒发一次 PING，对端回 PONG，本机据此算出往返延迟
network_rtt_ms = 0             # 与对端的往返延迟（毫秒）
network_ping_timer = 0         # 距离下次发送 PING 还剩多少帧
network_ping_sent_ms = 0       # 上一次发送 PING 时的本地时间戳（毫秒）
network_error_msg = ""        # 显示在局域网页面上的错误提示
network_disconnect_pending = False  # 检测到对方断开，主循环据此回退
# 对方断开后先显示提示画面，倒计时结束再切到目标状态
network_disconnect_tip_timer = 0     # >0 表示正在显示"对方已断开"提示
network_disconnect_tip_target = MENU  # 提示结束后要切换到的目标状态
client_input_ip = load_setting("client_ip", "")  # 加入房间时输入的 IP（可带端口，如 1.2.3.4:12345），从 settings.config 读取
client_input_active = False    # IP 输入框是否处于激活输入状态
client_input_port = load_setting("port", "12345")  # 创建房间时使用的端口（从 settings.config 读取）
port_input_active = False      # 端口输入框是否处于激活输入状态
host_ip_display = ""          # 主机页面上显示的本机 IP
lan_select_hover = None        # 局域网子菜单当前鼠标悬浮的格子
# 联机版本校验：进入准备页后双方交换版本号（第一行）与迭代次数（第二行）
network_peer_ver = None        # 对方版本号字符串（如 "0.9-alpha"）
network_peer_build = None      # 对方迭代次数
network_version_mismatch = False  # 迭代次数不一致时为 True，禁止联机
network_ver_sent = False       # 本次连接是否已经发送过 VER 消息

# KO 结算退出的双方确认：任何一方点击 back 只算本机确认，
# 双方都确认后由主机统一切回准备界面。
network_ko_back_local = False   # 本机是否已确认退出
network_ko_back_remote = False  # 对方是否已确认退出

# 联机抽签结果：主机是不是 1P。
# 双方准备完成后由主机随机决定，通过 IS_1P 消息同步给客户端。
network_host_is_1p = False     # 主机是不是 1P
network_is_1p = False          # 本机是不是 1P

# ---------- 布局常量 ----------
CHAR_PREVIEW_TOPLEFT = (600, 50)
CHAR_PREVIEW_W = 60
CHAR_PREVIEW_H = 120
CHAR_PREVIEW_AXIS_X = CHAR_PREVIEW_TOPLEFT[0] + CHAR_PREVIEW_W // 2
CHAR_NAME_CENTER = (CHAR_PREVIEW_AXIS_X, 195)
CHAR_NAME_SIZE = 30

# ---------- 工具函数 ----------
def get_local_ip():
    """获取本机在局域网中的 IP。方法：向公网地址发一个 UDP 包，
    然后读 socket 的本地地址，通常就是本机内网 IP。
    失败时回退到 127.0.0.1。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def net_send(sock, msg):
    """向指定 socket 发送一行消息（末尾补 \n）。失败返回 False。"""
    if sock is None:
        return False
    try:
        sock.sendall((msg + "\n").encode("utf-8"))
        return True
    except Exception:
        return False

def net_send_to_peer(msg):
    """向对方发送一行消息（自动判断主机/客户端）。"""
    if network_role == "host":
        return net_send(network_client_sock, msg)
    elif network_role == "client":
        return net_send(network_sock, msg)
    return False

def net_cleanup():
    """关闭所有 socket，并重置所有联机状态。"""
    global network_role, network_sock, network_client_sock
    global network_connected, network_recv_buffer
    global network_local_ready, network_remote_ready
    global network_error_msg, network_disconnect_pending
    global network_peer_char_name, network_peer_skill_num
    global network_remote_input, network_peer_state
    global network_rtt_ms, network_ping_timer, network_ping_sent_ms
    global client_input_active
    global network_peer_ver, network_peer_build, network_version_mismatch
    global network_ver_sent
    global network_host_is_1p, network_is_1p
    global network_ko_back_local, network_ko_back_remote
    try:
        net_send_to_peer("BYE")
    except Exception:
        pass
    for sock in (network_client_sock, network_sock):
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
    network_role = None
    network_sock = None
    network_client_sock = None
    network_connected = False
    network_recv_buffer = ""
    network_local_ready = False
    network_remote_ready = False
    network_error_msg = ""
    network_disconnect_pending = False
    network_peer_char_name = None
    network_peer_skill_num = None
    network_peer_state = None
    network_remote_input.clear()
    network_rtt_ms = 0
    network_ping_timer = 0
    network_ping_sent_ms = 0
    client_input_active = False
    network_peer_ver = None
    network_peer_build = None
    network_version_mismatch = False
    network_ver_sent = False
    network_host_is_1p = False
    network_is_1p = False
    network_ko_back_local = False
    network_ko_back_remote = False

def net_handle_message(msg):
    """处理对方发来的一行文本消息。"""
    global network_remote_ready, network_disconnect_pending
    global network_peer_char_name, network_peer_skill_num
    global network_remote_input, network_peer_state
    global network_rtt_ms
    global STATE, network_local_ready, ko_phase, ko_timer
    global network_host_is_1p, network_is_1p, selected_idx
    global network_ko_back_local, network_ko_back_remote
    if msg == "KO_BACK":
        # 对方在 KO 结算界面点击了 back。
        # 只是标记对方已确认，不立即切状态。
        network_ko_back_remote = True
        return
    if msg == "BACK_TO_READY":
        # 主机通知客户端：本局结束，双方一起回到准备页。
        # 清空挂起的 STATE 包，否则上一帧的 FIGHT_KO 状态会把它盖回去。
        STATE = LAN_READY
        network_local_ready = False
        network_remote_ready = False
        ko_phase = 0
        ko_timer = 0
        network_peer_state = None
        network_ko_back_local = False
        network_ko_back_remote = False
        return
    if msg == "READY":
        network_remote_ready = True
    elif msg == "UNREADY":
        network_remote_ready = False
    elif msg == "BYE":
        network_disconnect_pending = True
    elif msg.startswith("CHAR_PICK:"):
        network_peer_char_name = msg.split(":", 1)[1]
    elif msg.startswith("SKILL_PICK:"):
        try:
            network_peer_skill_num = int(msg.split(":", 1)[1])
        except ValueError:
            pass
    elif msg.startswith("PING:"):
        # 收到对方的 PING，立即回一个带同样时间戳的 PONG
        try:
            _t = msg.split(":", 1)[1]
            net_send_to_peer(f"PONG:{_t}")
        except Exception:
            pass
    elif msg.startswith("PONG:"):
        # 收到对方的 PONG，用当前时间和原时间戳的差值作为往返延迟
        try:
            _t = int(msg.split(":", 1)[1])
            network_rtt_ms = pygame.time.get_ticks() - _t
        except Exception:
            pass
    elif msg.startswith("IS_1P:"):
        # 主机发来的抽签结果：IS_1P:1 表示主机是 1P；IS_1P:0 表示客户端是 1P。
        # 仅客户端处理，主机自己发出去的不用回环处理。
        _v = msg.split(":", 1)[1]
        network_host_is_1p = (_v == "1")
        if network_role == "client":
            network_is_1p = not network_host_is_1p
            STATE = DUAL_SELECT_1P
            selected_idx = -1
            btn_ok.active = False
            for _b in choose_btns:
                _b.selected = False
    elif msg.startswith("VER:"):
        # 收到对方的版本信息：VER:版本号字符串
        global network_peer_ver, network_version_mismatch
        try:
            network_peer_ver = msg.split(":", 1)[1]
        except Exception:
            network_peer_ver = None
        if network_peer_ver:
            _mismatch = (compare_version(network_peer_ver, ver) != 0)
            if _mismatch and not network_version_mismatch:
                # 版本不一致：清空双方准备状态，禁止进入选角
                network_version_mismatch = True
                network_local_ready = False
                network_remote_ready = False
                try:
                    net_send_to_peer("UNREADY")
                except Exception:
                    pass
            elif not _mismatch:
                network_version_mismatch = False
    elif msg.startswith("EMOJI:"):
        # 对方发来的表情：EMOJI:1p:3 或 EMOJI:2p:3
        global emoji_show_side, emoji_show_img, emoji_show_timer
        _parts = msg.split(":", 2)
        if len(_parts) == 3:
            _side = _parts[1]
            try:
                _num = int(_parts[2])
            except ValueError:
                _num = 0
            if _num in emoji_images and _side in ("1p", "2p"):
                emoji_show_side = _side
                emoji_show_img = emoji_images[_num]
                emoji_show_timer = 300  # 5 秒
    elif msg.startswith("INPUT:"):
        try:
            network_remote_input = json.loads(msg.split(":", 1)[1])
        except Exception:
            pass
    elif msg.startswith("STATE:"):
        try:
            network_peer_state = json.loads(msg.split(":", 1)[1])
        except Exception:
            pass

def net_poll():
    """在主循环每帧调用：非阻塞地读取对方消息，按行分割后交给
    net_handle_message 处理。"""
    global network_recv_buffer, network_connected
    global network_error_msg, network_disconnect_pending
    if not network_connected:
        return
    read_sock = network_client_sock if network_role == "host" else network_sock
    if read_sock is None:
        return
    try:
        data = read_sock.recv(4096)
    except BlockingIOError:
        return
    except Exception as e:
        network_connected = False
        network_error_msg = f"连接异常: {e}"
        network_disconnect_pending = True
        return
    if not data:
        network_connected = False
        network_error_msg = "对方已断开"
        network_disconnect_pending = True
        return
    network_recv_buffer += data.decode("utf-8", errors="ignore")
    while "\n" in network_recv_buffer:
        line, network_recv_buffer = network_recv_buffer.split("\n", 1)
        net_handle_message(line.strip())

def load_image(path):
    return pygame.image.load(path).convert_alpha()


# 翻转缓存：pygame.transform.flip 每次调用都会创建一张新 Surface，
# 角色的动画帧、投射物图片每帧都要翻转一次，性能开销很大。
# 这里按 id(surf) 缓存翻转结果，同一张图只翻转一次。
# 只用于长期存活的 Surface（角色动画帧、全局静态图），
# 不要对每帧临时创建的 Surface 使用（id 会被复用）。
# 翻转结果直接附着在 Surface 对象自身的属性上。
# 旧实现用全局 dict + id(surf) 作 key：一旦 Surface 被垃圾回收，
# 它的 id 可能被新创建的 Surface 复用，就会错误地返回其他动画帧
# 的翻转版本，导致角色"突然变成别人"。
# 改成附着在 Surface 上后，缓存随 Surface 的生命周期自动管理，
# 从根本上避免 id 复用冲突。
_FLIP_ATTR = "_true_man_flip_cache"

def get_flip(surf):
    """返回 surf 的水平翻转版本；结果存在 surf 自身属性上。"""
    cached = getattr(surf, _FLIP_ATTR, None)
    if cached is None:
        cached = pygame.transform.flip(surf, True, False)
        try:
            setattr(surf, _FLIP_ATTR, cached)
        except Exception:
            # 万一某些 pygame 版本的 Surface 不支持 setattr，
            # 就不缓存，直接返回本次翻转结果，功能依然正确。
            pass
    return cached


# 角色图片原点锚点系统。
# 当用户朝面向方向扩展了角色图片（例如攻击帧朝面向方向加宽）时，
# 图片中心不再等于人物脚底中心，直接用 midbottom 绘制会导致人物
# 视觉上先朝面向反方向退、随后又弹回来。
# 用户可以在加载图片后设置：
#     img._tm_origin_x = <人物脚底中心在图片中的 x 坐标>
# 之后所有绘制与碰撞矩形都会自动补偿偏移，让脚底中心落在 pos 处。
# 未设置时默认为图片中心，行为与原来完全一致。
_TM_ORIGIN_ATTR = "_tm_origin_x"


def get_image_origin_x(img):
    """返回图片中人物脚底中心的 x 坐标（相对图片左边缘）。
    未设置 _tm_origin_x 时返回图片宽度的一半（图片中心）。"""
    v = getattr(img, _TM_ORIGIN_ATTR, None)
    if v is None:
        return img.get_width() / 2.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return img.get_width() / 2.0


def set_image_origin_x(img, origin_x):
    """设置图片中人物脚底中心的 x 坐标（相对图片左边缘）。
    传 None 时清除设置，回到默认的图片中心。
    返回 img 以便链式调用。"""
    if origin_x is None:
        try:
            delattr(img, _TM_ORIGIN_ATTR)
        except AttributeError:
            pass
    else:
        try:
            setattr(img, _TM_ORIGIN_ATTR, float(origin_x))
        except Exception:
            pass
    return img


def _anchor_offset(raw_img, flip):
    """计算绘制时需要施加到 x 的偏移量，使人物脚底中心落在
    midbottom 锚点。raw_img 是翻转前的原始图片。
    flip 为 True 时图片会水平翻转，原点镜像为 width - origin_x。
    未设置 _tm_origin_x 时返回 0。"""
    ax = get_image_origin_x(raw_img)
    if flip:
        ax = raw_img.get_width() - ax
    return raw_img.get_width() / 2.0 - ax


# 脚底光环预渲染：原代码每帧都 new 一张 Surface 画椭圆，
# 改成在启动时预生成两张（1P 蓝 / 2P 红），绘制时直接 blit。
def _make_aura(c1, c2):
    _s = pygame.Surface((70, 35), pygame.SRCALPHA)
    pygame.draw.ellipse(_s, c1, _s.get_rect())
    pygame.draw.ellipse(_s, c2, _s.get_rect(), 2)
    return _s

AURA_BLUE = _make_aura((0, 150, 255, 100), (0, 100, 200, 180))
AURA_RED = _make_aura((255, 50, 50, 100), (200, 50, 50, 180))


# 烟雾粒子缓存：隐身施放时每帧都在 new Surface 画圆，
# 按 (size, alpha 量化档位) 缓存，避免重复创建。
_SMOKE_SURF_CACHE = {}

def get_smoke_surf(size, alpha):
    if alpha < 0:
        alpha = 0
    elif alpha > 255:
        alpha = 255
    level = alpha * 16 // 256
    qa = level * 17
    key = (size, qa)
    surf = _SMOKE_SURF_CACHE.get(key)
    if surf is None:
        surf = pygame.Surface((size * 2, size * 2), pygame.SRCALPHA)
        pygame.draw.circle(surf, (255, 255, 255, qa), (size, size), size)
        _SMOKE_SURF_CACHE[key] = surf
    return surf

def spawn_explosion(x, y):
    """在 (x, y) 位置生成一批橙黄色爆炸粒子。"""
    for _ in range(50):
        ang = random.uniform(0, math.pi * 2)
        spd = random.uniform(2, 8)
        color = random.choice([
            (255, 240, 180),
            (255, 200, 80),
            (255, 140, 40),
            (255, 80, 30),
        ])
        explosion_particles.append([
            x, y,
            math.cos(ang) * spd,
            math.sin(ang) * spd - random.uniform(0.5, 2),
            random.randint(20, 45),
            random.randint(2, 5),
            color,
        ])

def spawn_smoke(x, y):
    """在 (x, y) 位置生成一批白色烟雾粒子，用于隐身技能施放时的原地白烟。"""
    for _ in range(40):
        ang = random.uniform(0, math.pi * 2)
        spd = random.uniform(0.5, 2.5)
        smoke_particles.append([
            x + random.uniform(-15, 15),
            y + random.uniform(-40, 0),
            math.cos(ang) * spd,
            math.sin(ang) * spd - random.uniform(0.5, 2.0),
            random.randint(30, 60),
            random.randint(4, 9),
        ])

def spawn_egg_particles(x, y):
    """wyk 技能一蛋碎粒子特效：从 egg.png 随机取若干像素的颜色，
    向四周溅开。egg.png 未加载时用默认蛋黄色。"""
    _w, _h = (wyk_egg_img.get_size() if wyk_egg_img is not None else (30, 30))
    for _ in range(WYK_SKILL1_EGG_PARTICLE_COUNT):
        # 随机取 egg.png 上的一个像素作为颜色
        _color = (255, 240, 200)
        if wyk_egg_img is not None:
            try:
                _sx = random.randint(0, max(0, _w - 1))
                _sy = random.randint(0, max(0, _h - 1))
                _px = wyk_egg_img.get_at((_sx, _sy))
                if _px[3] >= 50:  # 透明像素跳过，用默认色
                    _color = (_px[0], _px[1], _px[2])
            except Exception:
                pass
        _ang = random.uniform(-math.pi, 0)  # 向上半圆喷溅
        _spd = random.uniform(2.5, 6.5)
        wyk_egg_particles.append([
            x + random.uniform(-8, 8),
            y + random.uniform(-6, 0),
            math.cos(_ang) * _spd,
            math.sin(_ang) * _spd - random.uniform(0.5, 2.0),
            random.randint(28, 55),
            random.randint(2, 5),
            _color,
        ])

def calc_egg_velocity(sx, sy, tx, ty):
    """计算从 (sx, sy) 发射、能精确命中 (tx, ty) 的蛋抛物线初速度。
    物理更新顺序：每帧先 vy += g，再 x += vx，再 y += vy。
    飞行 n 帧后水平位移 = n*vx；垂直位移 = n*vy0 + g*n*(n+1)/2。
    先根据水平距离确定飞行帧数 n，再反解出 vx、vy0。"""
    g = WYK_SKILL1_EGG_GRAVITY
    dx = tx - sx
    dy = ty - sy
    n = int(WYK_SKILL1_EGG_FLIGHT_MIN
            + abs(dx) / WYK_SKILL1_EGG_FLIGHT_PER_PIXEL)
    if n < 1:
        n = 1
    vx = dx / n
    vy0 = (dy - g * n * (n + 1) / 2.0) / n
    return vx, vy0


def spawn_skill2_2_weapon(owner_idx, facing):
    """生成技能 2 的武器。从屏幕边缘滑入，而不是在使用者身上突然出现。
    面向右时武器从屏幕左边缘外面滑入、向右滑过整个屏幕；
    面向左时武器从屏幕右边缘外面滑入、向左滑过整个屏幕。
    初始位置放在屏幕外面（-bw 或 W+bw），保证有一段明显的
    从屏幕外平滑滑入的过程。"""
    if len(fight_chars) <= owner_idx:
        return
    owner = fight_chars[owner_idx]
    y = owner.ground_y - 60
    if skill2_2_img is not None:
        bw = skill2_2_img.get_width()
    else:
        bw = 80
    if facing == 'right':
        # 面向右：从屏幕左边缘外面滑入
        x = -float(bw)
        vx = SKILL2_2_WEAPON_SPEED
    else:
        # 面向左：从屏幕右边缘外面滑入
        x = float(MAP_W) + float(bw)
        vx = -SKILL2_2_WEAPON_SPEED
    skill2_2_projectiles.append({
        'owner_index': owner_idx,
        'target_index': 1 - owner_idx,
        'facing': facing,
        'x': x,
        'y': y,
        'vx': vx,
        'last_hit_frame': -999,
    })

def update_extra_skill2_state(char, owner_idx):
    """推进技能 2 的状态机：准备震动 -> 武器滑动 -> 收尾震动。
    每一帧在主循环里调用一次，直到 extra_skill_2_active 变为 False。"""
    if not char.extra_skill_2_active:
        return
    if char.extra_skill_2_phase == 0:
        char.extra_skill_2_timer -= 1
        if char.extra_skill_2_timer <= 0:
            spawn_skill2_2_weapon(owner_idx, char.facing)
            char.extra_skill_2_phase = 1
            # phase 1 用固定时长，不再依赖 has_wep 检查。
            # 双方同时施放时，其中一个武器被移除会让 has_wep 变 False，
            # 导致 phase 1 提前结束、震动和 2.1 显示被截断。
            char.extra_skill_2_timer = SKILL2_2_WEAPON_DURATION_FRAMES
    elif char.extra_skill_2_phase == 1:
        char.extra_skill_2_timer -= 1
        if char.extra_skill_2_timer <= 0:
            char.extra_skill_2_phase = 2
            char.extra_skill_2_timer = SKILL2_2_TRAIL_SHAKE_FRAMES
    elif char.extra_skill_2_phase == 2:
        char.extra_skill_2_timer -= 1
        if char.extra_skill_2_timer <= 0:
            char.extra_skill_2_active = False
            char.extra_skill_2_phase = 0

def any_skill2_2_active():
    """是否有任何角色的技能 2 处于激活状态（决定屏幕是否震动）。"""
    for ch in fight_chars:
        if ch.extra_skill_2_active:
            return True
    return False

# chr_1 环绕红色粒子效果
def spawn_chr1_aura_particle(char, pos):
    """chr_1 环绕红色粒子效果已停用，本函数保留为空实现。"""
    return

def update_and_draw_chr1_aura(surface):
    """更新并绘制 chr_1 的环绕红色粒子（渐隐渐现）。"""
    for p in chr1_aura_particles[:]:
        p[0] += p[2]
        p[1] += p[3]
        p[4] -= 1
        if p[4] <= 0:
            chr1_aura_particles.remove(p)
            continue
        _t = p[4] / float(p[5])  # 1 -> 0
        if _t > 0.7:
            _a = int(255 * (1.0 - _t) / 0.3)
        elif _t < 0.3:
            _a = int(255 * _t / 0.3)
        else:
            _a = 255
        if _a < 0:
            _a = 0
        elif _a > 255:
            _a = 255
        _size = p[6]
        _surf = get_particle_surf(255, 60, 60, _size, _a)
        surface.blit(_surf, (int(p[0] - _size), int(p[1] - _size)))

def get_chr1_orbit_book_positions(char, pos):
    """返回 chr_1 当前环绕中每本书的 (索引, x, y) 列表。
    索引 0/1/2 分别对应 XY / YZ / XZ 三个平面。
    每本书是否存活由 chr1_orbit_books_alive 决定，
    与剩余本数无关：哪本碰到敌人就消耗哪一本，
    未碰到的书继续在自己的平面环绕。"""
    if chr1_book_img is None:
        return []
    if not char.chr1_skill1_activated:
        return []
    _alive = getattr(char, 'chr1_orbit_books_alive', None)
    if _alive is None:
        return []
    if not any(_alive):
        return []
    _cx = pos[0]
    _cy = pos[1] - 40
    _R = CHR1_SKILL1_ORBIT_RADIUS
    _base_ang = getattr(char, "chr1_orbit_books_age", 0) * CHR1_SKILL1_ORBIT_SPEED
    _result = []
    for _i in range(3):
        if not _alive[_i]:
            continue
        if _i == 0:
            _ang = _base_ang
            _x = _cx + _R * math.cos(_ang)
            _y = _cy + _R * math.sin(_ang)
        elif _i == 1:
            _ang = _base_ang + 2.0 * math.pi / 3.0
            _x = _cx
            _y = _cy + _R * math.cos(_ang)
        else:
            _ang = _base_ang + 4.0 * math.pi / 3.0
            _x = _cx + _R * math.cos(_ang)
            _y = _cy
        _result.append((_i, _x, _y))
    return _result


def draw_chr1_orbit_books(surface, char, pos, side='all'):
    """绘制 chr_1 周围环绕的书。三本书分别位于 XY / YZ / XZ 三个 3D 平面，
    绕角色旋转。投影时按 z 深度排序（远的先画），并按深度做透视缩放。
    每本书是否绘制由 chr1_orbit_books_alive 决定，与剩余本数无关。
    side 参数：
        'behind' -> 只画 z < 0 的书（在角色图层下面）
        'front'  -> 只画 z >= 0 的书（在角色图层上面）
        'all'    -> 全画（旧行为）"""
    if chr1_book_img is None:
        return
    # 未按下过技能键时不显示环绕书
    if not char.chr1_skill1_activated:
        return
    _alive = getattr(char, 'chr1_orbit_books_alive', None)
    if _alive is None:
        return
    if not any(_alive):
        return
    # 环绕书存活满 10 秒后渐隐（运动不停止）
    _age = getattr(char, 'chr1_orbit_books_age', 0)
    _alpha = 255
    if _age >= CHR1_BOOK_LIFETIME_FRAMES:
        _t = (_age - CHR1_BOOK_LIFETIME_FRAMES) / max(1, CHR1_BOOK_FADE_FRAMES)
        _alpha = int(255 * max(0.0, 1.0 - _t))
        if _alpha <= 0:
            return
    _cx = pos[0]
    _cy = pos[1] - 40
    _R = CHR1_SKILL1_ORBIT_RADIUS
    _base_ang = getattr(char, "chr1_orbit_books_age", 0) * CHR1_SKILL1_ORBIT_SPEED
    _books = []
    for _i in range(3):
        if not _alive[_i]:
            continue
        if _i == 0:
            # XY 平面：绕 Z 轴旋转，屏幕上是整圆
            _ang = _base_ang
            _x = _cx + _R * math.cos(_ang)
            _y = _cy + _R * math.sin(_ang)
            _z = 0.0
        elif _i == 1:
            # YZ 平面：绕 X 轴旋转，屏幕上竖直上下移动
            _ang = _base_ang + 2.0 * math.pi / 3.0
            _x = _cx
            _y = _cy + _R * math.cos(_ang)
            _z = _R * math.sin(_ang)
        else:
            # XZ 平面：绕 Y 轴旋转，屏幕上水平左右移动
            _ang = _base_ang + 4.0 * math.pi / 3.0
            _x = _cx + _R * math.cos(_ang)
            _y = _cy
            _z = _R * math.sin(_ang)
        _books.append((_x, _y, _z))
    # 按 z 深度升序排序：更远的先画，更近的后画
    _books.sort(key=lambda t: t[2])
    # 根据 side 只画需要的一侧
    if side == 'behind':
        _books = [b for b in _books if b[2] < 0]
    elif side == 'front':
        _books = [b for b in _books if b[2] >= 0]
    _persp = 200.0
    _bw, _bh = chr1_book_img.get_size()
    for _x, _y, _z in _books:
        _scale = 1.0 + _z / _persp
        if _scale < 0.5:
            _scale = 0.5
        elif _scale > 1.5:
            _scale = 1.5
        _w = max(1, int(_bw * _scale))
        _h = max(1, int(_bh * _scale))
        if _w != _bw or _h != _bh:
            _img = pygame.transform.smoothscale(chr1_book_img, (_w, _h))
        else:
            _img = chr1_book_img
        if _alpha < 255:
            _img = _img.copy()
            _img.set_alpha(_alpha)
        _r = _img.get_rect(center=(int(_x), int(_y)))
        surface.blit(_img, _r)

def draw_brick_wall(img):
    for x in range(0, W, 50):
        for y in range(0, H, 50):
            screen.blit(img, (x, y))

_FONT_CACHE = {}

def get_font(size):
    font = _FONT_CACHE.get(size)
    if font is None:
        font = pygame.font.Font("./temp/font.ttf", size)
        _FONT_CACHE[size] = font
    return font

def make_text_button(text, size=(170, 52),
                     bg=(60, 60, 90), border=(220, 220, 220),
                     text_color=(255, 255, 255), font_size=30):
    """程序内绘制按钮：生成一张带边框和文字的 Surface。
    替代原先 ./temp/main_pro/btn/ 下的按钮图片，
    这样可以直接删除 main_pro 文件夹。
    size        按钮尺寸（宽, 高）
    bg          按钮底色
    border      边框颜色
    text_color  文字颜色
    font_size   文字字号"""
    w, h = size
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    surf.fill(bg)
    pygame.draw.rect(surf, border, (0, 0, w, h), 3)
    # 顶部一条淡色高光，增加立体感
    pygame.draw.line(surf, (255, 255, 255, 60), (3, 3), (w - 4, 3), 1)
    # 文字与黑描边
    font = get_font(font_size)
    txt = font.render(text, True, text_color)
    shadow = font.render(text, True, (0, 0, 0))
    rect = txt.get_rect(center=(w // 2, h // 2))
    for dx, dy in ((0, 2), (0, -2), (2, 0), (-2, 0)):
        surf.blit(shadow, (rect.x + dx, rect.y + dy))
    surf.blit(txt, rect)
    return surf

_SCALED_AVATAR_CACHE = {}

def get_wyk_skill2_voice_rect(char, pos, shake_x=0, shake_y=0):
    """返回 wyk 技能二声波的矩形。
    水平：面朝右时声波左边缘贴角色右边缘；
          面朝左时声波右边缘贴角色左边缘。
    垂直：声波中心对齐角色中心。
    shake_x / shake_y 为震动的随机偏移。
    图片本身不做镜像，只调整矩形位置。"""
    if wyk_skill2_voice_img is None:
        return None
    img_w, img_h = wyk_skill2_voice_img.get_size()
    char_rect = char.get_rect(pos)
    rect = pygame.Rect(0, 0, img_w, img_h)
    if char.facing == 'right':
        rect.left = char_rect.right + shake_x
    else:
        rect.right = char_rect.left + shake_x
    rect.centery = char_rect.centery + shake_y
    return rect

def get_scaled_avatar(avatar_img, cache_key, size):
    key = (cache_key, size)
    surf = _SCALED_AVATAR_CACHE.get(key)
    if surf is None:
        surf = pygame.transform.scale(avatar_img, (size, size))
        _SCALED_AVATAR_CACHE[key] = surf
    return surf

# 爆炸粒子 Surface 缓存：
# 每个粒子每帧都 new 一个 Surface 太慢（技能二 3 本书命中会有 150 个粒子，
# 每帧 150 次内存分配），改成按 (颜色, 尺寸, alpha 量化档位) 缓存。
# 同一个组合只创建一次，之后直接 blit 复用。
_PARTICLE_SURF_CACHE = {}

def get_particle_surf(r, g, b, size, alpha):
    """获取一枚预渲染的粒子 Surface。alpha 量化到 16 档以复用缓存。
    返回的 Surface 已设置 surface-level alpha，直接 blit 即可。"""
    if alpha < 0:
        alpha = 0
    elif alpha > 255:
        alpha = 255
    # 量化到 16 档
    level = alpha * 16 // 256
    qa = level * 17  # 0, 17, 34, ..., 255
    key = (r, g, b, size, qa)
    surf = _PARTICLE_SURF_CACHE.get(key)
    if surf is None:
        surf = pygame.Surface((size * 2, size * 2))
        surf.fill((r, g, b))
        surf.set_alpha(qa)
        _PARTICLE_SURF_CACHE[key] = surf
    return surf

# bordered_text 的渲染结果缓存：
# 这个函数每帧在 UI 里被调用几十次，每次都做两次 font.render
# （白字 + 黑描边），对 CPU 来说相当贵。改成按 (text, size) 缓存
# 渲染好的 Surface，后续直接 blit。只缓存 alpha==255 的情况，
# 带透明度的调用仍走原逻辑（淡出动画等）。
# 加 500 条上限防止动态文字把缓存撑爆。
_BORDERED_TEXT_CACHE = {}

def bordered_text(text, pos, align="center", size=36, alpha=255):
    if alpha == 255:
        key = (text, size)
        cached = _BORDERED_TEXT_CACHE.get(key)
        if cached is None:
            font = get_font(size)
            txt = font.render(text, True, (255, 255, 255))
            shadow = font.render(text, True, (0, 0, 0))
            cached = (txt, shadow)
            if len(_BORDERED_TEXT_CACHE) > 500:
                _BORDERED_TEXT_CACHE.clear()
            _BORDERED_TEXT_CACHE[key] = cached
        txt, shadow = cached
    else:
        font = get_font(size)
        txt = font.render(text, True, (255, 255, 255))
        shadow = font.render(text, True, (0, 0, 0))
        txt.set_alpha(alpha)
        shadow.set_alpha(alpha)
    rect = txt.get_rect(center=pos) if align == "center" else txt.get_rect(topleft=pos)
    for dx, dy in ((0, 2), (0, -2), (2, 0), (-2, 0)):
        screen.blit(shadow, (rect.x + dx, rect.y + dy))
    screen.blit(txt, rect)

def _show_connecting_overlay(ip_text, port):
    """显示"正在尝试连接..."毛玻璃遮罩，并立即 flip 让用户看到。
    socket.connect 是阻塞调用，会卡住主循环 1.5 秒左右；
    先绘制一帧提示，可以避免用户以为程序卡死。"""
    try:
        _w, _h = screen.get_size()
        # 毛玻璃：把当前屏幕缩小再放大，模拟模糊
        _small = pygame.transform.smoothscale(
            screen, (max(1, _w // 8), max(1, _h // 8)))
        _blur = pygame.transform.smoothscale(_small, (_w, _h))
        screen.blit(_blur, (0, 0))
        # 深色半透明遮罩
        _overlay = pygame.Surface((_w, _h), pygame.SRCALPHA)
        _overlay.fill((20, 20, 30, 170))
        screen.blit(_overlay, (0, 0))
        # 中央提示框
        _box_w, _box_h = 340, 140
        _box_x = (_w - _box_w) // 2
        _box_y = (_h - _box_h) // 2
        # 阴影
        _shadow = pygame.Surface((_box_w, _box_h), pygame.SRCALPHA)
        _shadow.fill((0, 0, 0, 110))
        screen.blit(_shadow, (_box_x + 4, _box_y + 4))
        # 主体
        _box = pygame.Surface((_box_w, _box_h), pygame.SRCALPHA)
        _box.fill((40, 40, 55, 235))
        pygame.draw.rect(_box, (220, 220, 220), (0, 0, _box_w, _box_h), 2)
        pygame.draw.line(_box, (255, 255, 255, 60), (2, 2), (_box_w - 3, 2), 1)
        screen.blit(_box, (_box_x, _box_y))
        bordered_text("正在尝试连接...", (_w // 2, _box_y + 50), "center", size=28)
        bordered_text(f"{ip_text}:{port}", (_w // 2, _box_y + 100), "center", size=20)
        pygame.display.flip()
        pygame.event.pump()
        pygame.event.clear()
    except Exception:
        pass

def draw_wrapped_text(text, x, y, max_width, size=18, line_height=24):
    """按字符宽度自动换行绘制文本（中文不需要按词切）。"""
    font = get_font(size)
    lines = []
    current = ""
    for ch in text:
        test = current + ch
        if font.size(test)[0] > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = test
    if current:
        lines.append(current)
    for i, line in enumerate(lines):
        bordered_text(line, (x, y + i * line_height), "topleft", size=size)

def draw_stamp_animation(image, center, progress):
    progress = max(0.0, min(1.0, progress))
    ease = 1.0 - (1.0 - progress) ** 3
    scale = 2.4 - 1.4 * ease
    if progress < 0.25:
        alpha = int(255 * (progress / 0.25))
    else:
        alpha = 255
    w, h = image.get_size()
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    scaled = pygame.transform.smoothscale(image, (new_w, new_h))
    scaled.set_alpha(alpha)
    rect = scaled.get_rect(center=center)
    screen.blit(scaled, rect)

def get_avatar_frame_color(base_color, protection_total):
    if protection_total >= PROTECTION_THRESHOLD_3:
        factor = PROTECTION_MULT_3
    elif protection_total >= PROTECTION_THRESHOLD_2:
        factor = PROTECTION_MULT_2
    elif protection_total >= PROTECTION_THRESHOLD_1:
        factor = PROTECTION_MULT_1
    else:
        factor = 1.0
    return (
        max(0, min(255, int(base_color[0] * factor))),
        max(0, min(255, int(base_color[1] * factor))),
        max(0, min(255, int(base_color[2] * factor))),
    )

def get_avatar_frame_width(protection_total):
    if protection_total >= PROTECTION_THRESHOLD_3:
        return PROTECTION_BORDER_WIDTH_3
    elif protection_total >= PROTECTION_THRESHOLD_2:
        return PROTECTION_BORDER_WIDTH_2
    elif protection_total >= PROTECTION_THRESHOLD_1:
        return PROTECTION_BORDER_WIDTH_1
    else:
        return PROTECTION_BORDER_WIDTH_0

def get_protection_pulse(protection_total, frame):
    if protection_total >= PROTECTION_THRESHOLD_3:
        speed = 0.25
        amp = 1.0
    elif protection_total >= PROTECTION_THRESHOLD_2:
        speed = 0.18
        amp = 0.75
    elif protection_total >= PROTECTION_THRESHOLD_1:
        speed = 0.12
        amp = 0.5
    else:
        return 0.0
    val = (math.sin(frame * speed) + 1.0) * 0.5
    return val * amp

def draw_protection_glow(surface, base_rect, pad, frame_color, protection_total, frame):
    pulse = get_protection_pulse(protection_total, frame)
    if pulse <= 0.0:
        return
    spread = int(3 + pulse * 9)
    alpha = int(30 + 130 * pulse)
    total_pad = pad + spread
    glow_w = base_rect.width + total_pad * 2
    glow_h = base_rect.height + total_pad * 2
    r = min(255, frame_color[0] + 80)
    g = min(255, frame_color[1] + 80)
    b = min(255, frame_color[2] + 80)
    glow_surf = pygame.Surface((glow_w, glow_h), pygame.SRCALPHA)
    pygame.draw.rect(glow_surf, (r, g, b, alpha), glow_surf.get_rect(), 3)
    surface.blit(glow_surf, (base_rect.x - total_pad, base_rect.y - total_pad))

# ---------- 全屏抖动类 ----------
class ZoomShake:
    def __init__(self, duration):
        self.duration = duration
        self.timer = 0
        self.active = False
        self.current_max_scale = 1.04

    def start(self):
        self.current_max_scale = round(random.uniform(1.02, 1.05), 3)
        self.timer = 0
        self.active = True

    def update(self):
        if not self.active:
            return 1.0
        self.timer += 1
        if self.timer > self.duration:
            self.active = False
            return 1.0
        progress = self.timer / self.duration
        if progress < 0.4:
            t = progress / 0.4
            scale = 1.0 + (self.current_max_scale - 1.0) * (t * t * 1.5)
        else:
            t = (progress - 0.4) / 0.6
            scale = self.current_max_scale - (self.current_max_scale - 1.0) * (t * t * 2)
        scale = max(1.0, min(self.current_max_scale, scale))
        return scale

    def apply(self):
        scale = self.update()
        if scale == 1.0:
            return
        surf = screen.copy()
        w, h = surf.get_size()
        new_w, new_h = int(w * scale), int(h * scale)
        scaled_surf = pygame.transform.scale(surf, (new_w, new_h))
        rect = scaled_surf.get_rect(center=(w // 2, h // 2))
        screen.blit(scaled_surf, rect)

# ---------- 按钮类 ----------
class Button:
    def __init__(self, img, center, active=True, border=False):
        self.img = img
        self.rect = img.get_rect(center=center)
        self.active = active
        self.border = border
        self.selected = False

    def draw(self, mouse_pos):
        screen.blit(self.img, self.rect)
        if not self.active:
            mask = pygame.Surface(self.rect.size)
            mask.set_alpha(150)
            mask.fill((50, 50, 50))
            screen.blit(mask, self.rect)
        else:
            if self.border and not self.selected:
                pygame.draw.rect(screen, (0, 0, 0), self.rect, 5)
            if self.rect.collidepoint(mouse_pos):
                mask = pygame.Surface(self.rect.size)
                mask.set_alpha(150)
                mask.fill((150, 150, 150))
                screen.blit(mask, self.rect)

    def clicked(self, mouse_pos, event):
        return self.active and event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.rect.collidepoint(mouse_pos)

# ---------- 技能图标类 ----------
class SkillIcon:
    def __init__(self, image, center, key_label=""):
        self.image = image
        self.rect = image.get_rect(center=center)
        self.key_label = key_label

    def draw(self, mouse_pos, is_cooling=False, cd_remaining_seconds=0.0,
             overlay_text=None, overlay_color=(255, 255, 255)):
        screen.blit(self.image, self.rect)

        # 灰色圆形遮罩只在 is_cooling 为真时绘制。
        # 隐身期间虽然按钮仍可按，但不画遮罩，只显示黄色倒计时，
        # 通过不传 is_cooling 实现。
        if is_cooling:
            mask = pygame.Surface(self.rect.size, pygame.SRCALPHA)
            center = (self.rect.width // 2, self.rect.height // 2)
            radius = min(self.rect.width, self.rect.height) // 2
            pygame.draw.circle(mask, (50, 50, 50, 150), center, radius)
            screen.blit(mask, self.rect)

        # 计算要绘制的文字：
        #   - overlay_text 有值优先用（隐身黄色倒计时）
        #   - 否则在冷却中且剩余时间 > 0 时用白色冷却数字
        if overlay_text is not None:
            cd_text = overlay_text
            text_color = overlay_color
        elif is_cooling and cd_remaining_seconds > 0:
            if cd_remaining_seconds >= 10:
                cd_text = str(int(math.ceil(cd_remaining_seconds)))
            else:
                cd_text = f"{cd_remaining_seconds:.1f}"
            text_color = (255, 255, 255)
        else:
            cd_text = None

        if cd_text is not None:
            cd_font = get_font(20)
            txt = cd_font.render(cd_text, True, text_color)
            shadow = cd_font.render(cd_text, True, (0, 0, 0))
            text_rect = txt.get_rect(center=self.rect.center)
            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                screen.blit(shadow, (text_rect.x + dx, text_rect.y + dy))
            screen.blit(txt, text_rect)

        if self.key_label:
            font = get_font(14)
            txt = font.render(self.key_label, True, (255, 255, 255))
            shadow = font.render(self.key_label, True, (0, 0, 0))
            label_rect = txt.get_rect(bottomright=(self.rect.right - 2, self.rect.bottom - 2))
            for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
                screen.blit(shadow, (label_rect.x + dx, label_rect.y + dy))
            screen.blit(txt, label_rect)

# ---------- 伤害数字类 ----------
class DamageNumber:
    def __init__(self, x, y, damage):
        self.x = x
        self.y = y
        self.damage = int(round(damage))
        self.alpha = 255
        self.vy = -2.5
        # 左右随机偏移，防止同帧多个伤害数字完全重合看不清
        self.offset_x = random.randint(-25, 25)
        self.font = get_font(30)
        if self.damage <= 800:
            color = (255, 255, 255)
        elif self.damage <= 2000:
            color = (255, 255, 0)
        else:
            color = (255, 0, 0)
        _base_surf = self.font.render(str(self.damage), True, color)
        _outline_surf = self.font.render(str(self.damage), True, (0, 0, 0))
        # 关键：把黑描边和彩色主文字预先合成到同一张透明 surface 上，
        # 淡出时整张图一起设置 alpha。这样绝不会出现"黑边和彩字各自半透明
        # 叠成灰糊糊"的现象，边框也不会比彩字更早消失。
        _bw, _bh = _base_surf.get_size()
        _pad = 3
        self.composite_surf = pygame.Surface((_bw + _pad * 2, _bh + _pad * 2), pygame.SRCALPHA)
        for _dx, _dy in ((0, 2), (0, -2), (2, 0), (-2, 0),
                         (2, 2), (-2, -2), (2, -2), (-2, 2)):
            self.composite_surf.blit(_outline_surf, (_pad + _dx, _pad + _dy))
        self.composite_surf.blit(_base_surf, (_pad, _pad))
        self.text_rect = self.composite_surf.get_rect(center=(x, y))

    def update(self):
        self.y += self.vy
        self.alpha -= 4
        if self.alpha < 0:
            self.alpha = 0
        # 整张合成图（彩字 + 黑边）作为一个整体淡出，边框和彩字永远同步。
        # 不再分别设置 text_surf / outline_surf 的 alpha，避免出现
        # "边框比彩字先消失"或"边框彩字混成灰糊糊"的现象。
        self.composite_surf.set_alpha(self.alpha)
        self.text_rect.center = (self.x, self.y)

    def draw(self, surface):
        if self.alpha > 0:
            # 绘制时应用左右随机偏移，避免多个伤害数字完全重叠
            surface.blit(self.composite_surf,
                         (self.text_rect.x + self.offset_x,
                          self.text_rect.y))

    def is_dead(self):
        return self.alpha <= 0

# ---------- 角色父类 ----------
class Character:
    def __init__(self, data, custom_path=None):
        self.data = data
        self.name = data.get("name", "unknown")
        self.animations = {}
        self.current_frame = 0
        self.frame_timer = 0
        self.frame_speed = 10
        self.facing = "right"
        self.speed = float(data.get("speed", 10))
        self.jump_speed = data.get("jump_speed", -19.5)
        self.gravity = 0.8
        self.max_hp = 50000
        self.hp = 50000
        # 体力系统
        self.max_stamina = STAMINA_MAX
        self.stamina = STAMINA_START
        self.stamina_regen_timer = 0
        # 击飞期间累计伤害，每满 1000 给攻击方回 10 体力
        self.hit_recover_accum = 0
        _power = data.get("power", DEFAULT_SKILL_STAMINA_COSTS)
        if not isinstance(_power, list) or len(_power) < 2:
            _power = DEFAULT_SKILL_STAMINA_COSTS
        self.skill1_stamina_cost = int(_power[0])
        self.skill2_stamina_cost = int(_power[1])
        _attack_raw = data.get("attack", 500)
        if isinstance(_attack_raw, list):
            self.attack_powers = [float(x) for x in _attack_raw] if _attack_raw else [500.0]
            self.attack_power = self.attack_powers[0]
        else:
            self.attack_powers = [float(_attack_raw)]
            self.attack_power = float(_attack_raw)
        self.jump_phase = 0
        self.vy = 0
        self.ground_y = 360
        self.static_frame = None
        # 当前正在播放的动画名，用于联机时同步给客户端
        self.active_anim = "stand"

        self.damage_protection_total = 0.0

        self.is_invincible = False
        self.invincible_timer = 0
        self.is_stand_up_invincible = False
        self.stand_up_invincible_timer = 0
        self.needs_ground_snap = False
        # 训练模式：死亡后倒地不动，不进入结算
        self.is_training_dead = False
        # 技能一前摇计时器：>0 时表示正在前摇，减到 0 才真正发射
        self.pending_book_launch = 0
        # 技能二前摇计时器（存储技能同样可以有前摇）：>0 时正在前摇
        self.pending_skill2_launch = 0
        # 携带技能相关
        self.extra_skill_num = 0        # 选中的携带技能编号（对应 skills.json 的 num）
        self.extra_invis_active = False  # 是否正在隐身
        self.extra_invis_timer = 0       # 隐身剩余帧数
        self.extra_invis_phase = 0       # 隐身闪烁周期内的相位（0 ~ FLASH_PERIOD-1）
        # 携带技能 2（持续震屏 + 武器滑过屏幕）状态变量
        self.extra_skill_2_active = False  # 是否正在施放技能 2
        self.extra_skill_2_phase = 0       # 0=准备震动, 1=武器滑动, 2=收尾震动
        self.extra_skill_2_timer = 0       # 当前阶段的剩余帧数
        # gjl 技能一（幻影突进）状态变量
        #   gjl_skill1_phase: 0=未施放, 1=前摇（透明度渐变）, 2=突进
        #   gjl_skill1_timer: 当前阶段剩余帧数
        #   gjl_skill1_total_frames: 前摇总帧数（用于计算 alpha 比例）
        #   gjl_skill1_hit: 本次施放是否已命中过对方（防止一次冲撞多次伤害）
        self.gjl_skill1_phase = 0
        self.gjl_skill1_timer = 0
        self.gjl_skill1_total_frames = 0
        self.gjl_skill1_hit = False
        # gjl 技能一改为存储技能：可用次数，用完进入冷却
        self.gjl_skill1_uses_left = GJL_SKILL1_STORE_COUNT
        # gjl 技能二状态变量：
        #   phase: 0=未施放, 1=前摇, 2=第一段 code 飞行中, 3=第二段 code 飞行中
        #   timer: 前摇剩余帧数
        #   origin_angle: 第一段 code 的方向（用于二段计算垂直方向）
        self.gjl_skill2_phase = 0
        self.gjl_skill2_timer = 0
        self.gjl_skill2_origin_angle = 0.0
        # gjl 技能二改为存储技能：可用次数，用完进入冷却
        self.gjl_skill2_uses_left = GJL_SKILL2_STORE_COUNT
        # gjl 技能一冲刺起点（世界 x 坐标），用于叠加距离伤害
        self.gjl_skill1_start_x = 0.0

        self.skills = {
            'normal': {
                'anim_speed': 12,
                'cd': 0,
                'current_cd': 0,
                'max_speed': 10.0,
                'accel': 2.0,
                'decel': 3.0,
                'recovery': 8,
                'hurt_frame': 0,
                'hurt_frames': [0],
                'damage': self.attack_power,
                'damages': list(self.attack_powers)
            },
            'escape': {
                'anim_speed': 10,
                'cd': ESCAPE_CD_FRAMES,
                'current_cd': 0,
                'max_speed': 0,
                'accel': 0,
                'decel': 0,
                'recovery': 0,
                'hurt_frame': 0,
                'damage': 0
            },
            'skill_1': {
                'anim_speed': 10,
                'cd': 120,
                'current_cd': 0,
                'max_speed': 12.0,
                'accel': 2.5,
                'decel': 3.5,
                'recovery': 12,
                'hurt_frame': 0,
                'damage': self.attack_power * 1.5
            },
            'skill_2': {
                'anim_speed': 8,
                'cd': 300,
                'current_cd': 0,
                'max_speed': 15.0,
                'accel': 3.0,
                'decel': 4.0,
                'recovery': 16,
                'hurt_frame': 0,
                'damage': self.attack_power * 2.5
            },
            'extra': {
                'anim_speed': 10,
                'cd': 0,
                'current_cd': 0,
                'max_speed': 0,
                'accel': 0,
                'decel': 0,
                'recovery': 0,
                'hurt_frame': 0,
                'damage': 0
            }
        }
        if "skills" in data:
            for skill_name, params in data["skills"].items():
                target = "skill_1" if skill_name == "skill" else skill_name
                if target in self.skills:
                    if "cd" in params:
                        self.skills[target]["cd"] = int(round(params["cd"] * FPS))
                    if "recovery" in params:
                        self.skills[target]["recovery"] = params["recovery"]
                    if "hurt" in params:
                        _hurt_val = params["hurt"]
                        if isinstance(_hurt_val, list):
                            self.skills[target]["hurt_frames"] = [int(h) - 1 for h in _hurt_val]
                            if self.skills[target]["hurt_frames"]:
                                self.skills[target]["hurt_frame"] = self.skills[target]["hurt_frames"][0]
                        else:
                            _hf = int(_hurt_val) - 1
                            self.skills[target]["hurt_frame"] = _hf
                            self.skills[target]["hurt_frames"] = [_hf]
                    if "damage" in params:
                        self.skills[target]["damage"] = params["damage"]

        self.is_attacking = False
        self.attack_type = None
        self.attack_anim = None
        self.attack_phase = None
        self.attack_anim_done = False
        self.attack_speed = 0.0
        self.recovery_timer = 0
        self.has_delivered_hit = False

        self.is_hit = False
        self.hit_timer = 0
        self.hit_vx = 0
        self.hit_vy = 0
        self.hit_gravity = 0.5
        self.hit_ground_y = self.ground_y
        self.hit_facing = "right"
        # 受击落地后的弹跳次数计数：每次 take_damage 清零。
        # 具体最多弹几下由落地那一刻的保护等级决定。
        self._hit_bounce_count = 0
        self.fall_img = None
        self.stand_up_frames = []
        self.stand_up_index = 0
        self.stand_up_timer = 0
        self.is_standing_up = False
        self.stand_up_done = False

        self.icons = {'normal': None, 'escape': None, 'skill_1': None, 'skill_2': None, 'extra': None}
        self.anim_map = {'normal': 'hit', 'escape': 'stand', 'skill_1': 'skill', 'skill_2': 'ultimate'}

        self.attack_pressed = False
        self.escape_pressed = False
        self.skill_pressed = False
        self.ultimate_pressed = False
        self.jump_pressed = False
        self.extra_pressed = False
        self.skill2_pressed = False
        # 技能二剩余释放次数（每次按下投出 SKILL2_BOOKS_PER_THROW 本书）
        self.skill2_uses_left = SKILL2_THROW_COUNT
        # 普攻连击状态：
        #   attack_combo_phase           当前处于普攻的第几个连击阶段（0 索引）
        #   attack_held                  玩家本帧是否按着普攻键（保留字段）
        #   attack_combo_window_timer    连击输入窗口剩余帧数（>0 表示正在等待输入）
        #   attack_combo_input_pressed   本帧普攻键是否为"新按下"（先松再按）
        self.attack_combo_phase = 0
        self.attack_held = False
        self.attack_combo_window_timer = 0
        self.attack_combo_input_pressed = False
        # wyk 技能一：被变成 enemy.png 的状态
        #   wyk_egg_transformed  是否处于变身状态
        #   wyk_egg_timer        剩余帧数，归 0 时变回来并施加伤害
        self.wyk_egg_transformed = False
        self.wyk_egg_timer = 0
        # wyk 技能一改为存储技能：可用次数
        self.wyk_skill1_uses_left = WYK_SKILL1_STORE_COUNT
        # wyk 技能二（声波）状态：
        #   pending_wyk_skill2_launch  前摇剩余帧数（>0 时硬控）
        #   wyk_skill2_active          声波是否正在显示
        #   wyk_skill2_timer           声波剩余帧数
        #   wyk_skill2_hit             本次技能是否已经命中过（只判定一次）
        #   wyk_skill2_shake_x/y       本帧震动随机偏移（每帧重算）
        self.pending_wyk_skill2_launch = 0
        self.wyk_skill2_active = False
        self.wyk_skill2_timer = 0
        self.wyk_skill2_hit = False
        self.wyk_skill2_last_hit_frame = -999
        self.wyk_skill2_shake_x = 0
        self.wyk_skill2_shake_y = 0
        # 【音效】技能触发序号，用于联机时客户端通过 STATE 对比检测
        # 到技能触发并本地播放 skill_1 / skill_2 音效（每次触发 +1）。
        self.skill1_trigger_seq = 0
        self.skill2_trigger_seq = 0
        # 隐藏技能：wyk 抓取中接二技能触发的隐藏效果
        # wyk_hidden_boost_timer     暴击率提升剩余帧数（>0 时暴击率固定 35%）
        # wyk_hidden_smoke           被控玩家是否正在冒隐藏技能白烟
        # wyk_skill2_damage_override 本次声波伤害覆盖值（None 表示不覆盖）
        self.wyk_hidden_boost_timer = 0
        self.wyk_hidden_smoke = False
        self.wyk_skill2_damage_override = None
        # 【chr_1 技能一】环绕追踪书：剩余本数（用完进入冷却）
        self.chr1_skill1_uses_left = CHR1_SKILL1_STORE_COUNT
        # 是否已经按下过一次技能一（未按下时不显示环绕书）
        self.chr1_skill1_activated = False
        # 三本环绕书的独立存活状态：True 表示该书仍在环绕。
        # 每本书独立判断碰撞，哪本碰到敌人就消耗哪一本，
        # 未碰到的书不受影响，继续在自己的平面环绕。
        self.chr1_orbit_books_alive = [True, True, True]
        # 环绕书累计存活帧数（存活满 10 秒后渐隐消失）
        self.chr1_orbit_books_age = 0
        # chr_1 自动二技能：对方倒地期间被 book 击中的累计次数
        self.chr1_book_hit_count = 0
        # 是否正在自动爆发（持续无 CD 发射散射书）
        self.chr1_auto_skill2_active = False
        # 自动爆发剩余帧数
        self.chr1_auto_skill2_timer = 0
        # 自动爆发内两次发射之间的间隔计时
        self.chr1_auto_skill2_interval_timer = 0
        # 本局是否已经触发过一次自动爆发（一局仅触发一次）
        self.chr1_auto_skill2_used = False
        # 本局是否已经触发过一次自动爆发（一局仅触发一次）
        self.chr1_auto_skill2_used = False

        self.load_animations(custom_path)

        stand_up_val = float(data.get("stand_up", 12))
        self.stand_up_total_frames = max(0.1, stand_up_val)
        if self.stand_up_frames:
            self.stand_up_frame_delay = self.stand_up_total_frames / len(self.stand_up_frames)
        else:
            self.stand_up_frame_delay = 0.0
        self.effective_stand_up_speed = 1.0 / self.stand_up_total_frames

    def load_animations(self, custom_path):
        base = custom_path or f"./temp/character/{self.name}/movement/"
        for anim in ("stand", "walk", "jump", "hit", "skill", "ultimate"):
            path = base + anim + "/"
            if not os.path.exists(path):
                continue
            if anim == "hit":
                # hit 目录下可以有一个或多个子文件夹（1、2、3...），
                # 每个子文件夹代表普攻的一个连击阶段（1a、2a...）。
                # 按子文件夹编号排序，每个子文件夹内的 png 按数字排序。
                subdirs = []
                for fn in os.listdir(path):
                    sub = os.path.join(path, fn)
                    if os.path.isdir(sub) and fn.isdigit():
                        subdirs.append((int(fn), sub))
                subdirs.sort(key=lambda x: x[0])
                if subdirs:
                    phases = []
                    for _, sub in subdirs:
                        frames = []
                        for fname in os.listdir(sub):
                            nm = fname.split(".")[0]
                            if nm.isdigit():
                                frames.append((int(nm), load_image(os.path.join(sub, fname))))
                        frames.sort(key=lambda x: x[0])
                        phases.append([img for _, img in frames])
                    self.animations[anim] = phases
                else:
                    # 兼容旧结构：hit/ 下直接是 png，视为只有一个连击阶段
                    frames = []
                    for fname in os.listdir(path):
                        nm = fname.split(".")[0]
                        if nm.isdigit():
                            frames.append((int(nm), load_image(path + fname)))
                    frames.sort(key=lambda x: x[0])
                    if frames:
                        self.animations[anim] = [[img for _, img in frames]]
                continue
            frames = []
            for fname in os.listdir(path):
                name = fname.split(".")[0]
                if name.isdigit():
                    frames.append((int(name), load_image(path + fname)))
            frames.sort(key=lambda x: x[0])
            self.animations[anim] = [img for _, img in frames]

        fall_path = base + "fall/1.png"
        if os.path.exists(fall_path):
            self.fall_img = load_image(fall_path)
        else:
            self.fall_img = None

        stand_up_path = base + "stand_up/"
        if os.path.exists(stand_up_path):
            frames = []
            for fname in os.listdir(stand_up_path):
                name = fname.split(".")[0]
                if name.isdigit():
                    frames.append((int(name), load_image(stand_up_path + fname)))
            frames.sort(key=lambda x: x[0])
            self.stand_up_frames = [img for _, img in frames]
        else:
            self.stand_up_frames = []

        walk_frames = self.animations.get("walk")
        if walk_frames and len(walk_frames) > 1:
            self.static_frame = walk_frames[1]
        elif walk_frames:
            self.static_frame = walk_frames[0]
        else:
            self.static_frame = None

        # 加载技能前摇帧序列与"前摇结束后"帧序列。
        # 目录：./temp/character/{name}/skill/skill_1_use/ 与 skill_2_use/
        # 文件命名约定：
        #   1.png、2.png……   -> 前摇期间播放的帧（before）
        #   _1.png、_2.png…… -> 前摇结束后播放的帧（after）
        # 有则使用对应帧序列；无则由调用方回退到普通动画。
        self.skill_use_frames = {
            'skill_1_use': [], 'skill_1_use_after': [],
            'skill_2_use': [], 'skill_2_use_after': [],
        }
        if custom_path is None:
            _sk_base = f"./temp/character/{self.name}/skill/"
            for _sk_sub in ('skill_1_use', 'skill_2_use'):
                _dir = _sk_base + _sk_sub + "/"
                if not os.path.isdir(_dir):
                    continue
                _before = []
                _after = []
                for _fname in os.listdir(_dir):
                    _base_nm = _fname.rsplit(".", 1)[0]
                    _is_after = _base_nm.startswith("_")
                    _num_str = _base_nm[1:] if _is_after else _base_nm
                    if not _num_str.isdigit():
                        continue
                    try:
                        _img = load_image(_dir + _fname)
                    except Exception:
                        continue
                    if _is_after:
                        _after.append((int(_num_str), _img))
                    else:
                        _before.append((int(_num_str), _img))
                _before.sort(key=lambda x: x[0])
                _after.sort(key=lambda x: x[0])
                self.skill_use_frames[_sk_sub] = [img for _, img in _before]
                self.skill_use_frames[_sk_sub + '_after'] = [img for _, img in _after]

    def _current_attack_frames(self):
        """返回当前攻击阶段应该绘制的帧列表。
        普攻（'hit'）在 animations 里是嵌套结构：
            [[阶段1的帧], [阶段2的帧], ...]
        其他动画是扁平的帧列表。"""
        if self.attack_anim == 'hit':
            phases = self.animations.get('hit', [])
            if not phases:
                return self.animations.get('stand', [])
            p = self.attack_combo_phase
            if p >= len(phases):
                p = len(phases) - 1
            frames = phases[p]
            if not frames:
                return self.animations.get('stand', [])
            return frames
        return self.animations.get(self.attack_anim, [])

    def get_current_hurt_frame(self):
        """返回当前攻击阶段应该使用的中招判定帧（0 索引）。"""
        skill = self.skills.get(self.attack_type)
        if skill is None:
            return 0
        if self.attack_type == 'normal':
            hurt_frames = skill.get('hurt_frames')
            if hurt_frames:
                idx = min(self.attack_combo_phase, len(hurt_frames) - 1)
                return hurt_frames[idx]
        return skill.get('hurt_frame', 0)

    def get_current_damage(self):
        """返回当前攻击阶段应该造成的伤害。"""
        skill = self.skills.get(self.attack_type)
        if skill is None:
            return self.attack_power
        if self.attack_type == 'normal':
            damages = skill.get('damages')
            if damages:
                idx = min(self.attack_combo_phase, len(damages) - 1)
                return damages[idx]
        return skill.get('damage', self.attack_power)

    def get_skill_use_frame(self, skill_key, total_frames, timer):
        """根据前摇/前摇结束后的总帧数和当前剩余帧数，返回应该显示的图片。
        skill_key: 'skill_1_use' / 'skill_1_use_after' /
                   'skill_2_use' / 'skill_2_use_after'
        total_frames: 该阶段的总帧数
        timer: 该阶段剩余帧数（从 total_frames 递减到 0）
        返回 Surface；没有对应帧序列时返回 None。"""
        _frames = getattr(self, 'skill_use_frames', {}).get(skill_key, [])
        if not _frames:
            return None
        _n = len(_frames)
        if total_frames <= 0:
            return _frames[0]
        _elapsed = total_frames - timer
        if _elapsed < 0:
            _elapsed = 0
        _idx = (_elapsed * _n) // total_frames
        if _idx >= _n:
            _idx = _n - 1
        if _idx < 0:
            _idx = 0
        return _frames[_idx]

    def get_current_image(self, anim):
        if self.jump_phase == 2:
            img = self.static_frame
            if img is None:
                walk_frames = self.animations.get("walk")
                if walk_frames and len(walk_frames) > 1:
                    img = walk_frames[1]
                elif walk_frames:
                    img = walk_frames[0]
            return img
        if anim == "hit":
            frames = self._current_attack_frames()
        else:
            frames = self.animations.get(anim)
        if frames and self.current_frame < len(frames):
            return frames[self.current_frame]
        return None

    def update(self, anim):
        self.active_anim = anim
        if self.is_attacking and self.attack_phase == 'anim':
            frames = self._current_attack_frames()
            if not frames:
                return
            speed = self.skills[self.attack_type]['anim_speed']
            self.frame_timer += speed
            if self.frame_timer >= self.frame_speed:
                self.frame_timer = 0
                if self.current_frame < len(frames) - 1:
                    self.current_frame += 1
                else:
                    self.attack_anim_done = True
            return
        if self.jump_phase == 2:
            return
        frames = self.animations.get(anim)
        if not frames:
            return
        increment = 2 if anim == "stand" else (12 if anim == "jump" else 5)
        self.frame_timer += increment
        if self.frame_timer >= self.frame_speed:
            self.frame_timer = 0
            self.current_frame = (self.current_frame + 1) % len(frames)

    def _blit(self, img, rect):
        # 逃脱无敌或起身无敌期间角色半透明显示；
        # 携带技能闪烁期间让角色在 90%~100% 透明度之间随机横跳。
        if self.is_invincible or self.is_stand_up_invincible:
            img = img.copy()
            img.set_alpha(INVINCIBLE_ALPHA)
        elif self.extra_invis_active:
            # 周期性闪烁：进入闪烁窗口时短暂显形，其余时间完全隐身
            img = img.copy()
            if self.extra_invis_phase < EXTRA_INVIS_FLASH_FRAMES:
                img.set_alpha(EXTRA_INVIS_FLASH_ALPHA)
            else:
                img.set_alpha(EXTRA_INVIS_ALPHA)
        screen.blit(img, rect)

    def draw(self, anim, pos, anchor="topleft"):
        # wyk 技能一：被变成 enemy.png 时，直接画 enemy 图，
        # 不做其他任何绘制逻辑。
        if self.wyk_egg_transformed and wyk_enemy_img is not None:
            _raw = wyk_enemy_img
            _flip_here = (self.facing == "left")
            _off = _anchor_offset(_raw, _flip_here)
            _eimg = get_flip(_raw) if _flip_here else _raw
            _erect = _eimg.get_rect()
            if anchor == "midbottom":
                _erect.midbottom = (pos[0] + _off, pos[1])
            else:
                _erect.topleft = (pos[0], pos[1])
            screen.blit(_eimg, _erect)
            return
        if self.is_training_dead or self.is_hit:
            _raw = self.fall_img
            if _raw is not None:
                _flip_here = (self.hit_facing == "right")
                _off = _anchor_offset(_raw, _flip_here)
                img = get_flip(_raw) if _flip_here else _raw
                rect = img.get_rect()
                if anchor == "midbottom":
                    rect.midbottom = (pos[0] + _off, pos[1])
                else:
                    rect.topleft = (pos[0], pos[1])
                self._blit(img, rect)
            return

        if self.is_standing_up:
            if self.stand_up_frames and self.stand_up_index < len(self.stand_up_frames):
                _raw = self.stand_up_frames[self.stand_up_index]
                _flip_here = (self.facing == "right")
                _off = _anchor_offset(_raw, _flip_here)
                img = get_flip(_raw) if _flip_here else _raw
                rect = img.get_rect()
                if anchor == "midbottom":
                    rect.midbottom = (pos[0] + _off, pos[1])
                else:
                    rect.topleft = (pos[0], pos[1])
                self._blit(img, rect)
            else:
                self.draw("stand", pos, anchor)
            return

        if self.is_attacking and self.attack_phase == 'anim':
            frames = self._current_attack_frames()
            if frames:
                if self.current_frame >= len(frames):
                    self.current_frame = len(frames) - 1
                _raw = frames[self.current_frame]
                _flip_here = (self.facing == "left")
                _off = _anchor_offset(_raw, _flip_here)
                img = get_flip(_raw) if _flip_here else _raw
                rect = img.get_rect()
                if anchor == "midbottom":
                    rect.midbottom = (pos[0] + _off, pos[1])
                else:
                    rect.topleft = (pos[0], pos[1])
                self._blit(img, rect)
                return

        if self.jump_phase == 2:
            _raw = self.static_frame
            if _raw is None:
                walk_frames = self.animations.get("walk")
                if walk_frames and len(walk_frames) > 1:
                    _raw = walk_frames[1]
                elif walk_frames:
                    _raw = walk_frames[0]
                if _raw is None:
                    return
            _flip_here = (self.facing == "left")
            _off = _anchor_offset(_raw, _flip_here)
            img = get_flip(_raw) if _flip_here else _raw
            rect = img.get_rect()
            if anchor == "midbottom":
                rect.midbottom = (pos[0] + _off, pos[1])
            else:
                rect.topleft = (pos[0], pos[1])
            self._blit(img, rect)
            return

        frames = self.animations.get(anim)
        if not frames:
            return
        if self.current_frame >= len(frames):
            self.current_frame = 0
        _raw = frames[self.current_frame]
        _flip_here = (self.facing == "left")
        _off = _anchor_offset(_raw, _flip_here)
        img = get_flip(_raw) if _flip_here else _raw
        rect = img.get_rect()
        if anchor == "midbottom":
            rect.midbottom = (pos[0] + _off, pos[1])
        else:
            rect.topleft = (pos[0], pos[1])
        self._blit(img, rect)

    def draw_animation(self, anim, pos, anchor="topleft"):
        self.update(anim)
        self.draw(anim, pos, anchor)

    def move(self, pos, direction):
        if self.is_hit or self.is_standing_up:
            self.draw("stand", pos, "midbottom")
            return pos

        if self.is_attacking:
            if self.attack_phase == 'anim':
                self.update(self.attack_anim)
                self.draw(self.attack_anim, pos, "midbottom")
            else:
                self.draw("stand", pos, "midbottom")
            return pos

        if self.jump_phase == 2:
            anim = "walk"
        elif self.jump_phase == 1:
            anim = "jump"
        else:
            anim = "walk" if direction is not None else "stand"

        img = self.get_current_image(anim)
        if img is not None:
            width = img.get_width()
        else:
            width = 50

        if direction is not None:
            self.facing = direction
            pos[0] += self.speed if direction == "right" else -self.speed

        half_width = width // 2
        _bl, _br = get_world_bounds()
        pos[0] = max(_bl + half_width, min(_br - half_width, pos[0]))

        if self.jump_phase == 0:
            anim = "stand" if direction is None else "walk"
            self.update(anim)
            self.draw(anim, pos, "midbottom")
        elif self.jump_phase == 1:
            self.update("jump")
            self.draw("jump", pos, "midbottom")
        else:
            self.draw("walk", pos, "midbottom")
        return pos

    def start_jump(self):
        if self.is_hit or self.is_standing_up or self.is_training_dead:
            return
        if self.jump_phase == 0 and not self.is_attacking:
            # 没有跳跃动画帧时直接起跳，跳过前摇动画阶段
            if not self.animations.get("jump"):
                self.jump_phase = 2
                self.vy = self.jump_speed
            else:
                self.jump_phase = 1
            self.current_frame = 0
            self.frame_timer = 0

    def attack_normal(self):
        if self.is_hit or self.is_standing_up or self.is_training_dead:
            return
        self._start_attack('normal')

    def attack_skill_1(self):
        if self.is_hit or self.is_standing_up or self.is_training_dead:
            return
        self._start_attack('skill_1')

    def attack_skill_2(self):
        if self.is_hit or self.is_standing_up or self.is_training_dead:
            return
        self._start_attack('skill_2')

    def attack_escape(self):
        # 训练模式死亡后禁止逃脱（避免躺在地上也能瞬移起身）
        if self.is_training_dead:
            return False
        # 逃脱只有在受击（倒地 / 被击飞）状态才能使用
        if not (self.is_hit or self.is_standing_up):
            return False
        if self.is_invincible:
            return False
        if self.skills['escape']['current_cd'] > 0:
            return False
        if self.stamina < ESCAPE_STAMINA_COST:
            return False

        self.stamina -= ESCAPE_STAMINA_COST
        self.skills['escape']['current_cd'] = self.skills['escape']['cd']

        self.is_invincible = True
        self.invincible_timer = 0

        self.is_hit = False
        self.hit_timer = 0
        self.hit_vx = 0
        self.hit_vy = 0
        self.is_standing_up = False
        self.stand_up_index = 0
        self.stand_up_timer = 0
        self.stand_up_done = True
        self.jump_phase = 0
        self.vy = 0

        self.is_attacking = False
        self.attack_phase = None
        self.attack_type = None
        self.attack_anim = None
        self.attack_anim_done = False
        self.current_frame = 0
        self.frame_timer = 0
        self.has_delivered_hit = False

        self.needs_ground_snap = True
        return True

    def _end_extra_invis(self):
        """结束隐身并进入冷却。"""
        if not self.extra_invis_active:
            return
        self.extra_invis_active = False
        self.extra_invis_timer = 0
        self.extra_invis_phase = 0
        self.skills['extra']['current_cd'] = self.skills['extra']['cd']

    def attack_extra(self):
        """
        携带技能。按下对应按键时触发。
        目前只实现了“排序为 1”的携带技能：
          使用后角色完全隐身（透明），持续一段时间。
          隐身期间被打到或再次按下按键就立即结束。
          结束时进入冷却。
        其他编号的携带技能暂时无效果，等待后续实现。
        返回 True 表示本帧刚刚开启隐身（战斗循环据此生成白烟），
        返回 False 表示本次调用没有开启隐身。
        """
        if self.is_hit or self.is_standing_up or self.is_training_dead:
            return 0
        if self.icons.get('extra') is None:
            return 0
        # 隐身持续期间，再次按下立刻结束
        if self.extra_skill_num == 1 and self.extra_invis_active:
            self._end_extra_invis()
            return 0
        # 技能 2 持续期间，忽略重复触发
        if self.extra_skill_num == 2 and self.extra_skill_2_active:
            return 0
        if self.skills['extra']['current_cd'] > 0:
            return 0
        # 携带技能 1：隐身。返回 1 表示开启隐身（战斗循环据此生成白烟）
        if self.extra_skill_num == 1:
            self.extra_invis_active = True
            self.extra_invis_timer = EXTRA_INVIS_FRAMES
            self.extra_invis_phase = 0
            return 1
        # 携带技能 2：持续震屏 + 武器滑过屏幕。返回 2 表示技能 2 激活
        if self.extra_skill_num == 2:
            self.extra_skill_2_active = True
            self.extra_skill_2_phase = 0
            self.extra_skill_2_timer = SKILL2_2_TRIGGER_DELAY_FRAMES
            self.skills['extra']['current_cd'] = self.skills['extra']['cd']
            return 2
        return 0

    def _start_attack(self, attack_type):
        if self.is_attacking:
            return
        anim_key = self.anim_map.get(attack_type)
        if anim_key is None:
            return
        if anim_key == 'hit':
            # 普攻：检查是否存在任何一个非空的连击阶段
            phases = self.animations.get('hit', [])
            has_frames = any(p for p in phases)
        else:
            has_frames = bool(self.animations.get(anim_key))
        if not has_frames:
            if "stand" in self.animations and self.animations["stand"]:
                anim_key = "stand"
            else:
                return
        self.is_attacking = True
        self.attack_type = attack_type
        self.attack_anim = anim_key
        # 普攻连击阶段判断：
        #   如果连击检测窗口开着（说明上一次普攻后摇刚结束不久），
        #   就从下一 a 开始；否则从 1a 开始。
        if attack_type == 'normal' and self.attack_combo_window_timer > 0:
            self.attack_combo_phase += 1
        else:
            self.attack_combo_phase = 0
        self.attack_combo_window_timer = 0
        self.attack_phase = 'anim'
        self.attack_anim_done = False
        self.current_frame = 0
        self.frame_timer = 0
        self.attack_speed = 0.0
        self.has_delivered_hit = False
        # 普攻音效改为“命中敌人才播”，所以这里不再播放。
        # 真正的播放移到战斗循环的普攻命中判定处。

    def update_attack(self, pos):
        if not self.is_attacking:
            return pos
        skill = self.skills[self.attack_type]
        if self.attack_phase == 'anim':
            if self.attack_speed < skill['max_speed']:
                self.attack_speed += skill['accel']
                if self.attack_speed > skill['max_speed']:
                    self.attack_speed = skill['max_speed']
            else:
                self.attack_speed -= skill['decel']
                if self.attack_speed < 0:
                    self.attack_speed = 0
            if self.facing == "right":
                pos[0] += self.attack_speed
            else:
                pos[0] -= self.attack_speed
            img = self.get_current_image(self.attack_anim)
            if img:
                width = img.get_width()
            else:
                width = 50
            half_width = width // 2
            _bl, _br = get_world_bounds()
            pos[0] = max(_bl + half_width, min(_br - half_width, pos[0]))
            if self.attack_anim_done:
                self.attack_phase = 'recovery'
                self.recovery_timer = 0
                self.attack_speed = 0
        elif self.attack_phase == 'recovery':
            self.recovery_timer += 1
            if self.recovery_timer >= skill['recovery']:
                # 后摇结束：攻击立即终止，角色恢复自由（可移动、可再按普攻）。
                # 如果还有下一 a，就在后台开一个 0.2 秒的检测窗口，
                # 期间按下普攻即可接下一 a；窗口只是后台计时，不阻塞角色。
                _was_normal = (self.attack_type == 'normal'
                               and self.attack_anim == 'hit')
                _cur_phase = self.attack_combo_phase
                self.is_attacking = False
                self.attack_phase = None
                self.attack_type = None
                self.attack_anim = None
                self.attack_anim_done = False
                self.current_frame = 0
                self.frame_timer = 0
                self.has_delivered_hit = False
                if _was_normal:
                    phases = self.animations.get('hit', [])
                    if (_cur_phase + 1 < len(phases)
                            and phases[_cur_phase + 1]):
                        self.attack_combo_phase = _cur_phase
                        self.attack_combo_window_timer = ATTACK_COMBO_WINDOW_FRAMES
                    else:
                        self.attack_combo_phase = 0
                        self.attack_combo_window_timer = 0
                else:
                    self.attack_combo_phase = 0
                    self.attack_combo_window_timer = 0
        return pos

    def take_damage(self, damage, attacker_facing, attacker_pos, attacker):
        # 训练模式下死亡后倒地不动，不再受到任何伤害
        if self.is_training_dead:
            return None
        if self.is_invincible or self.is_stand_up_invincible:
            return None
        # 隐身期间被打到：立即结束隐身并进入冷却
        if self.extra_invis_active:
            self._end_extra_invis()

        if self.damage_protection_total >= PROTECTION_THRESHOLD_3:
            multiplier = PROTECTION_MULT_3
        elif self.damage_protection_total >= PROTECTION_THRESHOLD_2:
            multiplier = PROTECTION_MULT_2
        elif self.damage_protection_total >= PROTECTION_THRESHOLD_1:
            multiplier = PROTECTION_MULT_1
        else:
            multiplier = 1.0

        actual_damage = int(round(damage * multiplier))

        self.damage_protection_total += actual_damage

        self.hp -= actual_damage
        if self.hp < 0:
            self.hp = 0

        # 【音效】受伤后播放 hurt 音效（无敌 / 起身无敌会提前 return，
        # 不会走到这里；变身状态也扣血，所以也要播）。
        play_sfx(self.name, 'hurt')

        # wyk 技能一：变身期间可以受伤，但不被击飞。
        # 只扣血、生成伤害数字，不进入受击状态，保持不变身状态。
        if self.wyk_egg_transformed:
            return DamageNumber(attacker_pos[0], attacker_pos[1] - 80, actual_damage)

        # 击飞 / 倒地 / 起立前被继续打：攻击方每累计 STAMINA_HIT_DAMAGE_PER
        # 伤害回 STAMINA_HIT_RECOVER 点体力（数值见 NONJ.config 第二行）
        _target_was_downed = self.is_hit or self.is_standing_up
        if not _target_was_downed:
            attacker.hit_recover_accum = 0
        attacker.hit_recover_accum += actual_damage
        while attacker.hit_recover_accum >= STAMINA_HIT_DAMAGE_PER:
            attacker.hit_recover_accum -= STAMINA_HIT_DAMAGE_PER
            attacker.stamina = min(attacker.max_stamina,
                                   attacker.stamina + STAMINA_HIT_RECOVER)

        if self.is_standing_up:
            self.is_standing_up = False
            self.stand_up_index = 0
            self.stand_up_timer = 0
            self.stand_up_done = False

        self.is_hit = True
        self.hit_timer = 0
        # 每次新的受击都把弹跳计数清零
        self._hit_bounce_count = 0
        # 受到伤害时取消 gjl 技能一（如果在施放过程中被打中）
        if self.gjl_skill1_phase != 0:
            self.gjl_skill1_phase = 0
            self.gjl_skill1_timer = 0
            self.gjl_skill1_hit = False
        # 受到伤害时取消 gjl 技能二的前摇（已经发射的 code 不受影响）
        if self.gjl_skill2_phase == 1:
            self.gjl_skill2_phase = 0
            self.gjl_skill2_timer = 0

        if attacker_facing == "right":
            self.hit_facing = "right"
            self.hit_vx = 8
        else:
            self.hit_facing = "left"
            self.hit_vx = -8
        # 保护等级越高，被击飞高度越低，越容易落地（减少被打到空中无限连）。
        # 注意：这里不使用 PROTECTION_MULT_*（0.5/0.7/0.85），因为击飞高度与
        # 速度的平方成正比，直接用那几个倍率会让高度分别缩到 25%/49%/72%，
        # 看起来太矮、保护效果过于夸张。改用 0.7/0.8/0.9 后，高度约为
        # 49%/64%/81%，递减更平缓，同时仍保持“保护越强击飞越低”的效果。
        if self.damage_protection_total >= PROTECTION_THRESHOLD_3:
            launch_factor = 0.70
        elif self.damage_protection_total >= PROTECTION_THRESHOLD_2:
            launch_factor = 0.80
        elif self.damage_protection_total >= PROTECTION_THRESHOLD_1:
            launch_factor = 0.90
        else:
            launch_factor = 1.0
        self.hit_vy = -10 * launch_factor
        self.facing = self.hit_facing

        self.is_attacking = False
        self.attack_phase = None
        self.attack_type = None
        self.attack_anim = None
        self.attack_anim_done = False
        self.current_frame = 0
        self.frame_timer = 0
        self.has_delivered_hit = False

        return DamageNumber(attacker_pos[0], attacker_pos[1] - 80, actual_damage)

    def _tick_cooldowns(self):
        """统一处理技能冷却递减。
        注意：受击（is_hit）、倒地（is_standing_up）等状态下，
        角色仍然应当正常减少冷却。这个方法在 update_physics 的
        最开始就会被调用一次，保证所有 return 路径都先减过冷却。
        """
        for key in ('escape', 'skill_1', 'skill_2', 'extra'):
            if cd_enabled:
                if self.skills[key]['current_cd'] > 0:
                    self.skills[key]['current_cd'] -= 1
            else:
                # 关闭 CD 时把冷却视为已就绪
                self.skills[key]['current_cd'] = 0

        # 技能二冷却结束后，把剩余释放次数重置为满
        if self.skills['skill_2']['current_cd'] <= 0 and self.skill2_uses_left <= 0:
            self.skill2_uses_left = SKILL2_THROW_COUNT
        # gjl 技能一（存储技能）：冷却结束后重置可用次数
        if (self.name == 'gjl'
                and self.skills['skill_1']['current_cd'] <= 0
                and self.gjl_skill1_uses_left <= 0):
            self.gjl_skill1_uses_left = GJL_SKILL1_STORE_COUNT
        # wyk 技能一（存储技能）：冷却结束后重置可用次数
        if (self.name == 'wyk'
                and self.skills['skill_1']['current_cd'] <= 0
                and self.wyk_skill1_uses_left <= 0):
            self.wyk_skill1_uses_left = WYK_SKILL1_STORE_COUNT
        # gjl 技能二（存储技能）：冷却结束后重置可用次数
        if (self.name == 'gjl'
                and self.skills['skill_2']['current_cd'] <= 0
                and self.gjl_skill2_uses_left <= 0):
            self.gjl_skill2_uses_left = GJL_SKILL2_STORE_COUNT
        # chr_1 技能一（存储技能）：冷却结束后重置环绕书剩余本数
        # 同时把"已激活"标记清零：冷却结束后不再自动出现环绕书，
        # 需要玩家重新按一次技能键才会召唤。
        if (self.name == 'chr_1'
                and self.skills['skill_1']['current_cd'] <= 0
                and self.chr1_skill1_uses_left <= 0):
            self.chr1_skill1_uses_left = CHR1_SKILL1_STORE_COUNT
            self.chr1_skill1_activated = False
            self.chr1_orbit_books_alive = [True, True, True]
            self.chr1_orbit_books_age = 0

        # 环绕书寿命：激活且还有剩余本数时累加存活帧数。
        # 存活满 10 秒后开始渐隐，渐隐结束全部消失并进入冷却。
        if (self.name == 'chr_1'
                and self.chr1_skill1_activated
                and self.chr1_skill1_uses_left > 0):
            self.chr1_orbit_books_age += 1
            if self.chr1_orbit_books_age >= CHR1_BOOK_LIFETIME_FRAMES + CHR1_BOOK_FADE_FRAMES:
                self.chr1_orbit_books_alive = [False, False, False]
                self.chr1_skill1_uses_left = 0
                self.skills['skill_1']['current_cd'] = self.skills['skill_1']['cd']

        # 普攻连击检测窗口：每帧递减；到 0 时把连击阶段重置为 0。
        # 窗口只是后台计时，不影响角色的任何动作。
        if self.attack_combo_window_timer > 0:
            self.attack_combo_window_timer -= 1
            if self.attack_combo_window_timer <= 0:
                self.attack_combo_phase = 0

        # 隐藏技能：暴击率提升计时递减
        if self.wyk_hidden_boost_timer > 0:
            self.wyk_hidden_boost_timer -= 1
        # 体力恢复：每 STAMINA_REGEN_FRAMES 帧恢复 1 点，上限 max_stamina
        if self.stamina < self.max_stamina:
            self.stamina_regen_timer += 1
            if self.stamina_regen_timer >= STAMINA_REGEN_FRAMES:
                self.stamina_regen_timer = 0
                self.stamina = min(self.max_stamina, self.stamina + 1)
        else:
            self.stamina_regen_timer = 0

    def update_physics(self, pos):
        # wyk 技能一：变身期间角色冻结，位置不动，物理逻辑全部跳过。
        # 变身计时器在战斗循环里统一推进（因为要触发烟雾 + 520 伤害事件）。
        if self.wyk_egg_transformed:
            self._tick_cooldowns()
            return pos
        # 训练模式下死亡后：先跑受击物理（下落、撞墙、弹跳），
        # 落地后不再起身，保持倒地状态。
        if self.is_training_dead:
            if self.is_hit:
                self.hit_timer += 1
                self.hit_vy += self.hit_gravity
                pos[0] += self.hit_vx
                pos[1] += self.hit_vy
                _tbl, _tbr = get_world_bounds()
                if pos[0] < _tbl:
                    pos[0] = _tbl
                    self.hit_vx = -self.hit_vx * 0.5
                elif pos[0] > _tbr:
                    pos[0] = _tbr
                    self.hit_vx = -self.hit_vx * 0.5
                if pos[1] >= self.ground_y:
                    pos[1] = self.ground_y
                    _bc = getattr(self, '_training_bounce_count', 0)
                    if abs(self.hit_vy) > KO_BOUNCE_MIN_SPEED and _bc < KO_MAX_BOUNCES:
                        self.hit_vy = -abs(self.hit_vy) * KO_BOUNCE_DECAY
                        self.hit_vx *= KO_BOUNCE_DECAY
                        self._training_bounce_count = _bc + 1
                    else:
                        self.hit_vy = 0
                        self.hit_vx = 0
                        self.is_hit = False
                        self._training_bounce_count = 0
            return pos
        # 无论角色处于什么状态（正常、受击、倒地、起身无敌、前摇、空中），
        # 技能冷却都应当继续递减。把冷却递减放在这里，
        # 保证下面 is_hit / is_standing_up 的早退路径也会先减过冷却。
        self._tick_cooldowns()
        if self.needs_ground_snap:
            pos[1] = self.ground_y
            self.needs_ground_snap = False

        if self.is_invincible:
            self.invincible_timer += 1
            if self.invincible_timer >= ESCAPE_DURATION_FRAMES:
                self.is_invincible = False
                self.invincible_timer = 0

        if self.is_stand_up_invincible:
            self.stand_up_invincible_timer -= 1
            if self.stand_up_invincible_timer <= 0:
                self.is_stand_up_invincible = False
                self.stand_up_invincible_timer = 0

        if self.extra_invis_active:
            self.extra_invis_timer -= 1
            self.extra_invis_phase = (self.extra_invis_phase + 1) % EXTRA_INVIS_FLASH_PERIOD
            if self.extra_invis_timer <= 0:
                self._end_extra_invis()

        if self.is_hit:
            self.hit_timer += 1
            self.hit_vy += self.hit_gravity
            pos[0] += self.hit_vx
            pos[1] += self.hit_vy

            _bl, _br = get_world_bounds()
            if pos[0] < _bl:
                pos[0] = _bl
                self.hit_vx = -self.hit_vx * 0.5
            elif pos[0] > _br:
                pos[0] = _br
                self.hit_vx = -self.hit_vx * 0.5

            if pos[1] >= self.ground_y:
                pos[1] = self.ground_y
                # 根据保护等级决定落地后还能弹几下：
                #   无保护 / 一级保护（-15%）   ：弹 2 下
                #   二级保护（-30%）            ：弹 1 下
                #   三级保护（-50%）            ：不弹，直接起身
                if self.damage_protection_total >= PROTECTION_THRESHOLD_3:
                    _max_bounces = 0
                elif self.damage_protection_total >= PROTECTION_THRESHOLD_2:
                    _max_bounces = 1
                else:
                    _max_bounces = 2
                # 还能继续弹且下落速度够大：反向速度、衰减、计数 +1
                if (self._hit_bounce_count < _max_bounces
                        and abs(self.hit_vy) > KO_BOUNCE_MIN_SPEED):
                    self.hit_vy = -abs(self.hit_vy) * KO_BOUNCE_DECAY
                    self.hit_vx *= KO_BOUNCE_DECAY
                    self._hit_bounce_count += 1
                else:
                    # 不再弹：进入起身状态，弹跳计数清零
                    self.hit_vy = 0
                    self.hit_vx = 0
                    self.is_hit = False
                    self.is_standing_up = True
                    self.stand_up_index = 0
                    self.stand_up_timer = 0
                    self._hit_bounce_count = 0
                    if not self.stand_up_frames:
                        self.is_standing_up = False
                        self.stand_up_done = True
                        self.damage_protection_total = 0.0
            return pos

        if self.is_standing_up:
            if self.stand_up_frames:
                self.stand_up_timer += 1
                if self.stand_up_timer >= self.stand_up_frame_delay:
                    self.stand_up_timer = 0
                    self.stand_up_index += 1
                    if self.stand_up_index >= len(self.stand_up_frames):
                        self.is_standing_up = False
                        self.stand_up_done = True
                        self.stand_up_index = 0
                        self.damage_protection_total = 0.0
                        # 站起后立即获得一段无敌时间
                        self.is_stand_up_invincible = True
                        self.stand_up_invincible_timer = STAND_UP_INVINCIBLE_FRAMES
            else:
                self.is_standing_up = False
                self.stand_up_done = True
                self.damage_protection_total = 0.0
                # 站起后立即获得一段无敌时间
                self.is_stand_up_invincible = True
                self.stand_up_invincible_timer = STAND_UP_INVINCIBLE_FRAMES
            return pos

        if self.jump_phase == 1:
            frames = self.animations.get("jump")
            if frames:
                self.frame_timer += 12
                if self.frame_timer >= self.frame_speed:
                    self.frame_timer = 0
                    self.current_frame += 1
                    if self.current_frame >= len(frames):
                        self.jump_phase = 2
                        self.vy = self.jump_speed
                        self.current_frame = 0
                        self.frame_timer = 0

        if self.jump_phase == 2:
            self.vy += self.gravity
            pos[1] += self.vy
            if pos[1] >= self.ground_y:
                pos[1] = self.ground_y
                self.jump_phase = 0
                self.vy = 0
                self.current_frame = 0
                self.frame_timer = 0

        pos = self.update_attack(pos)

        return pos

    def get_rect(self, pos):
        # 变身状态下返回 enemy.png 的矩形
        if self.wyk_egg_transformed and wyk_enemy_img is not None:
            _eimg = wyk_enemy_img
            _flip_here = (self.facing == "left")
            _off = _anchor_offset(_eimg, _flip_here)
            rect = pygame.Rect(0, 0, _eimg.get_width(), _eimg.get_height())
            rect.midbottom = (pos[0] + _off, pos[1])
            return rect
        _raw = None
        _flip_here = False
        if self.is_hit and self.fall_img:
            _raw = self.fall_img
            _flip_here = (self.hit_facing == "right")
            width = _raw.get_width()
            height = _raw.get_height()
        elif self.is_standing_up and self.stand_up_frames:
            idx = min(self.stand_up_index, len(self.stand_up_frames)-1)
            _raw = self.stand_up_frames[idx]
            _flip_here = (self.facing == "right")
            width = _raw.get_width()
            height = _raw.get_height()
        else:
            if self.is_attacking and self.attack_phase == 'anim':
                frames = self._current_attack_frames()
                if frames and self.current_frame < len(frames):
                    _raw = frames[self.current_frame]
                    _flip_here = (self.facing == "left")
                    width = _raw.get_width()
                    height = _raw.get_height()
                else:
                    width, height = 50, 80
            elif self.jump_phase == 2:
                _raw = self.static_frame
                if _raw:
                    _flip_here = (self.facing == "left")
                    width, height = _raw.get_width(), _raw.get_height()
                else:
                    width, height = 50, 80
            else:
                frames = self.animations.get("stand")
                if frames:
                    _raw = frames[0]
                    _flip_here = (self.facing == "left")
                    width, height = _raw.get_width(), _raw.get_height()
                else:
                    width, height = 50, 80
        if _raw is not None:
            _off = _anchor_offset(_raw, _flip_here)
        else:
            _off = 0
        rect = pygame.Rect(0, 0, width, height)
        rect.midbottom = (pos[0] + _off, pos[1])
        return rect

# ---------- 全局抖动对象 ----------
shake = ZoomShake(SHAKE_DURATION)

# ---------- 角色子类 ----------
class Chr(Character):
    def __init__(self, data):
        super().__init__(data)

class Gjl(Character):
    def __init__(self, data):
        super().__init__(data)

class Wyk(Character):
    def __init__(self, data):
        super().__init__(data)

def get_char_class(name):
    """按角色名返回对应的 Character 子类。未注册的角色用 Chr 兜底。"""
    return {'chr': Chr, 'gjl': Gjl, 'wyk': Wyk}.get(name, Chr)

# ---------- 加载资源 ----------
brick_img = load_image("./temp/choose/brick.png")
load_brick = load_image("./temp/load/brick.png")
bg = load_image("./temp/bg/sky/res/bg.png")
cloud = load_image("./temp/bg/sky/res/cloud_1.png")
cloud_rect = cloud.get_rect()
# 战斗场景专用的云飘动状态（与标题界面独立，不影响彼此）
fight_cloud_rect = cloud.get_rect()
fight_cloud_x = 0

# sunset 地图：太阳浮动用的相位（每帧累加，通过 sin 得到上下偏移）
sunset_sun_phase = 0.0

def draw_map_background(map_name, map_img):
    """绘制战斗地图背景。
    如果当前地图是草原（文件夹名为 sky），在背景之上叠加一层
    与标题界面相同效果的飘动云，让战斗场景也有云在缓缓移动。
    如果当前地图是 sunset，bg 之上是缓慢上下浮动的 sun，
    最上面是 mountain 覆盖层。
    """
    global fight_cloud_x, sunset_sun_phase
    screen.blit(map_img, (0, 0))
    if map_name == "sky":
        fight_cloud_rect.x = MAP_W + fight_cloud_x
        if fight_cloud_rect.right < 0:
            fight_cloud_x = 0
        screen.blit(cloud, fight_cloud_rect)
        fight_cloud_x -= 1
    elif map_name == "sunset":
        # sunset 地图：bg 已铺底，接着绘制太阳（三角函数极缓慢上下小幅度浮动），
        # 最后把 mountain 覆盖在最上面。
        # 浮动参数：周期约 10.5 秒（phase 每帧 +0.01），上下幅度 10 像素。
        if sunset_sun_img is not None:
            sunset_sun_phase += 0.01
            _sun_dy = int(math.sin(sunset_sun_phase) * 10)
            _sun_rect = sunset_sun_img.get_rect(
                center=(MAP_W // 2, H // 2 + _sun_dy))
            screen.blit(sunset_sun_img, _sun_rect)
        if sunset_mountain_img is not None:
            # mountain 图层在地图中央水平居中，纵向保持贴顶（与原设计一致）。
            _mt_rect = sunset_mountain_img.get_rect()
            _mt_rect.centerx = MAP_W // 2
            _mt_rect.y = 0
            screen.blit(sunset_mountain_img, _mt_rect)
cover = load_image("./temp/two_player/cover.png")
# night 地图专用覆盖层：盖在地图、角色之上，粒子/伤害数字/UI 之下
try:
    night_cover_img = load_image("./temp/bg/nignt/res/cover.png")
except Exception:
    try:
        night_cover_img = load_image("./temp/bg/night/res/cover.png")
    except Exception:
        night_cover_img = None

def draw_night_cover():
    """night 地图的 cover 覆盖层已停用，本函数不再绘制任何内容。
    保留空函数是为了不改动所有调用点。"""
    return

chr_head = load_image("./temp/character/chr/movement/head.png")
gjl_head = load_image("./temp/character/gjl/movement/head.png")
hit_img = load_image("./temp/fight/hit.png")
# go_on 按钮已改为程序内绘制（见 make_text_button），不再加载图片
escape_img = load_image("./temp/fight/escape.png")

try:
    ready_img = load_image("./temp/fight/ready.png")
except Exception:
    ready_img = None
try:
    start_fight_img = load_image("./temp/fight/start_fight.png")
except Exception:
    start_fight_img = None

# chr 技能一投掷的书本图片
try:
    chr_book_img = load_image("./temp/character/chr/skill/book.png")
except Exception:
    chr_book_img = None

# chr_1 技能一投掷的书本图片
try:
    chr1_book_img = load_image("./temp/character/chr_1/skill/book.png")
except Exception:
    chr1_book_img = None

# 已废弃：chr 的技能前摇图改为从 ./temp/character/chr/skill/skill_1_use/
# 和 ./temp/character/chr/skill/skill_2_use/ 子文件夹中按 1.png、2.png……
# 顺序自动加载，具体见 Character.load_animations 与 get_skill_use_frame。

# ---------- 通用角色音效系统 ----------
# 目录结构：./temp/character/{角色名}/sounds/{类别}/*.mp3
# 类别：hurt / dead / skill_1 / skill_2 / ultimate
# 每个类别目录里可以有 0 个、1 个或多个音频文件（.mp3/.wav/.ogg）：
#   - 0 个：播放时静默跳过
#   - 1 个：直接播放该文件
#   - 多个：随机播放其中一个
# 音频加载失败、音频设备不可用都静默跳过，绝不抛异常。
# 加载结果按 (name, category) 缓存，第一次播放时加载。
_sfx_cache = {}

def load_sfx_list(name, category):
    """加载指定角色、指定类别下的所有音效文件（结果缓存）。
    返回 Sound 列表；目录不存在或无文件时返回空列表。"""
    key = (name, category)
    if key in _sfx_cache:
        return _sfx_cache[key]
    sounds = []
    if audio_available:
        _dir = f"./temp/character/{name}/sounds/{category}/"
        if os.path.isdir(_dir):
            try:
                for _fname in sorted(os.listdir(_dir)):
                    _lower = _fname.lower()
                    if not (_lower.endswith(".mp3")
                            or _lower.endswith(".wav")
                            or _lower.endswith(".ogg")):
                        continue
                    try:
                        sounds.append(pygame.mixer.Sound(_dir + _fname))
                    except Exception:
                        pass
            except Exception:
                pass
    _sfx_cache[key] = sounds
    return sounds

def play_sfx(name, category):
    """播放指定角色、指定类别的音效。
    有多个文件时随机选一个；没有则静默跳过。"""
    _lst = load_sfx_list(name, category)
    if _lst:
        try:
            random.choice(_lst).play()
        except Exception:
            pass

# ---------- 额外携带技能（选择页使用） ----------
# 从 ./temp/fight/extra_skill/ 读取：
#   skills.json   —— 每个技能的 num / name / description / cd / recovery
#   {num}.png     —— 与 num 对应的技能图标（50x50）
extra_skill_data = []
extra_skill_icons = {}
try:
    with open("./temp/fight/extra_skill/skills.json", "r", encoding="utf-8") as f:
        raw = f.read()
    # 去掉 JSON 尾部多余的逗号，例如 [ { ... }, ] 这种写法
    raw = re.sub(r',\s*([\]}])', r'\1', raw)
    extra_skill_data = json.loads(raw)
    extra_skill_data.sort(key=lambda d: d.get("num", 0))
except Exception:
    extra_skill_data = []

for _sk in extra_skill_data:
    _num = _sk.get("num", 0)
    _icon_path = f"./temp/fight/extra_skill/{_num}.png"
    if os.path.exists(_icon_path):
        try:
            extra_skill_icons[_num] = load_image(_icon_path)
        except Exception:
            pass

# 携带技能 2 的武器图片（与 2.png 同目录）
try:
    skill2_2_img = load_image("./temp/fight/extra_skill/2.1.png")
except Exception:
    skill2_2_img = None

# gjl 技能一突进阶段使用的替换图 trade.png；
# 前摇阶段的图片改为从 ./temp/character/gjl/skill/skill_1_use/ 子文件夹
# 自动加载。
try:
    gjl_trade_img = load_image("./temp/character/gjl/skill/trade.png")
except Exception:
    gjl_trade_img = None

# wyk 技能一的蛋图片（投射物）
try:
    wyk_egg_img = load_image("./temp/character/wyk/skill/egg.png")
except Exception:
    wyk_egg_img = None
# wyk 技能一命中敌人后，敌人变成的图片
try:
    wyk_enemy_img = load_image("./temp/character/wyk/skill/enemy.png")
except Exception:
    wyk_enemy_img = None

# wyk 技能二：声波图。前摇/激活阶段改为从
# ./temp/character/wyk/skill/skill_2_use/ 子文件夹自动加载。
try:
    wyk_skill2_voice_img = load_image("./temp/character/wyk/skill/skill_2_voice.png")
except Exception:
    wyk_skill2_voice_img = None

# gjl 技能二的 code 投射物图片。前摇图改为从
# ./temp/character/gjl/skill/skill_2_use/ 子文件夹自动加载。
try:
    gjl_code_img = load_image("./temp/character/gjl/skill/code.png")
except Exception:
    gjl_code_img = None

# ---------- 联机表情系统资源 ----------
# emoji_images 必须先初始化为空字典，
# 下面那个 for 循环会直接往里塞 {数字: Surface}。
emoji_images = {}
try:
    emoji_icon_img = load_image("./temp/emoji/icon.png")
except Exception:
    emoji_icon_img = None
try:
    emoji_message_img = load_image("./temp/emoji/message.png")
except Exception:
    emoji_message_img = None
if os.path.exists("./temp/emoji"):
    for _emoji_fn in sorted(os.listdir("./temp/emoji")):
        _emoji_nm = _emoji_fn.split(".")[0]
        if _emoji_nm.isdigit():
            try:
                emoji_images[int(_emoji_nm)] = load_image(
                    f"./temp/emoji/{_emoji_fn}")
            except Exception:
                pass

# 技能选择页面的当前光标（-1 表示未选择）
skill_select_idx_1p = -1
skill_select_idx_2p = -1

# 按键设置页面：正在等待输入按键的那一项索引，-1 表示无
keys_editing_idx = -1
# 键位冲突提示：显示一条红字，倒计时结束后消失
keys_conflict_msg = ""
keys_conflict_timer = 0

# ---------- 读取 About 页面文字 ----------
about_lines = []
try:
    with open("./temp/about.txt", "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip("\n").rstrip("\r")
            about_lines.append(stripped)
except FileNotFoundError:
    about_lines = []
except Exception:
    about_lines = []

# 主菜单按钮全部改为程序内绘制，main_pro 文件夹可以直接删除
btn_two = Button(make_text_button("双人"), (400, 300))
btn_single = Button(make_text_button("训练"), (400, 400))
btn_about = Button(make_text_button("关于"), (400, 500))
btn_keys = Button(make_text_button("键位"), (400, 200))
btn_back = Button(make_text_button("BACK", size=(80, 40), bg=(60, 60, 90), font_size=22), (60, 35))
btn_exit_room = Button(make_text_button("退出房间", size=(140, 44), bg=(120, 60, 60), font_size=22), (85, 35))
btn_ok = Button(make_text_button("确定", size=(100, 50), bg=(60, 60, 90), font_size=24), (690, 540), active=False)
btn_go_on = Button(make_text_button("进入", size=(150, 52)), (400, 150))

# ---------- 加载角色数据 ----------
choose_list = []
choose_btns = []

def load_character(name):
    with open(f"./temp/character/{name}/character_data.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    choose_list.append(data)
    idx = data["num"] - 1
    row, col = divmod(idx, 3)
    x = 50 + col * 135
    y = 100 + row * 135
    btn = Button(load_image(f"./temp/character/{name}/choose_btn/choose.png"), (x + 55, y + 55), border=True)
    choose_btns.append(btn)

for name in ("chr", "gjl", "wyk", "chr_1"):
    load_character(name)

normal_data = {}
try:
    with open("./temp/character/normal.json", "r", encoding="utf-8") as f:
        normal_data = json.load(f)
except FileNotFoundError:
    if choose_list:
        normal_data = choose_list[0]

normal_effective_speed = 1.0 / max(0.1, float(normal_data.get("stand_up", 12)))

# 【新增 wyk】按角色名映射到对应子类，避免硬编码 chr/gjl。
char_list = []
for _d in choose_list:
    _cls = get_char_class(_d.get("name"))
    char_list.append(_cls(_d))

for char in char_list:
    name = char.name
    char.icons['normal'] = hit_img
    char.icons['escape'] = escape_img
    skill_path = f"./temp/character/{name}/skill/skill_1.png"
    if os.path.exists(skill_path):
        char.icons['skill_1'] = load_image(skill_path)
    ultimate_path = f"./temp/character/{name}/skill/skill_2.png"
    if os.path.exists(ultimate_path):
        char.icons['skill_2'] = load_image(ultimate_path)

maps_data = []
maps_img = {}
map_img = None
for folder in os.listdir("./temp/bg"):
    with open(f"./temp/bg/{folder}/map.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        maps_data.append(data)
        maps_img[folder] = load_image(f"./temp/bg/{folder}/res/bg.png")

# sunset 地图的额外图层：sun 在中间浮动，mountain 覆盖在最上面。
# 文件位置按约定放在 ./temp/bg/sunset/res/ 下。
try:
    sunset_sun_img = load_image("./temp/bg/sunset/res/sun.png")
except Exception:
    sunset_sun_img = None
try:
    sunset_mountain_img = load_image("./temp/bg/sunset/res/mountain.png")
except Exception:
    sunset_mountain_img = None

select_border = Character({"name": "choose"}, custom_path="./temp/choose/")
select_border.frame_speed = 4

# ---------- 头像滑入类 ----------
class AvatarShow:
    def __init__(self):
        self.left_avatar = None
        self.right_avatar = None
        self.left_label = None
        self.right_label = None
        self.left_x = -200
        self.right_x = 900
        self.in_progress = False
        self.avatar_size = 120

    def set(self, a1, a2=None, label1=None, label2=None):
        self.left_avatar = a1
        self.right_avatar = a2
        self.left_label = label1
        self.right_label = label2
        self.left_x = -200
        self.right_x = 900 if a2 is not None else 900
        self.in_progress = True

    def update(self):
        if self.in_progress:
            self.left_x += 12
            if self.right_avatar is not None:
                self.right_x -= 12
            if self.left_x >= 50:
                self.left_x = 50
                if self.right_avatar is not None:
                    self.right_x = W - 50 - self.avatar_size
                self.in_progress = False

    def draw(self):
        self.update()
        if self.left_avatar:
            scaled_left = pygame.transform.scale(self.left_avatar, (self.avatar_size, self.avatar_size))
            screen.blit(scaled_left, (self.left_x, 130))
            frame_rect = pygame.Rect(self.left_x, 130, self.avatar_size, self.avatar_size)
            pygame.draw.rect(screen, (0, 100, 255), frame_rect, 3)
            if self.left_label:
                bordered_text(self.left_label,
                              (self.left_x + self.avatar_size // 2,
                               130 + self.avatar_size + 22),
                              "center", size=26)
        if self.right_avatar:
            scaled_right = pygame.transform.scale(self.right_avatar, (self.avatar_size, self.avatar_size))
            screen.blit(scaled_right, (self.right_x, 130))
            frame_rect = pygame.Rect(self.right_x, 130, self.avatar_size, self.avatar_size)
            pygame.draw.rect(screen, (255, 50, 50), frame_rect, 3)
            if self.right_label:
                bordered_text(self.right_label,
                              (self.right_x + self.avatar_size // 2,
                               130 + self.avatar_size + 22),
                              "center", size=26)

avatar_show = AvatarShow()

# ---------- 联机表情系统辅助函数 ----------
EMOJI_ICON_SIZE = 40
EMOJI_ICON_X = W - 55
EMOJI_ICON_Y = 75
EMOJI_PANEL_COLS = 4
EMOJI_CELL = 44
EMOJI_PANEL_PAD = 8
EMOJI_SHOW_FRAMES = 300  # 5 秒

def get_emoji_icon_rect():
    return pygame.Rect(EMOJI_ICON_X, EMOJI_ICON_Y,
                       EMOJI_ICON_SIZE, EMOJI_ICON_SIZE)

def get_emoji_panel_rect():
    """返回气泡面板的整体矩形；没有可显示的表情时返回 None。"""
    if not emoji_images:
        return None
    _n = len(emoji_images)
    _rows = (_n + EMOJI_PANEL_COLS - 1) // EMOJI_PANEL_COLS
    _w = EMOJI_PANEL_PAD * 2 + EMOJI_PANEL_COLS * EMOJI_CELL - 4
    _h = EMOJI_PANEL_PAD * 2 + _rows * EMOJI_CELL - 4
    _x2 = W - 15
    _x1 = _x2 - _w
    _y1 = EMOJI_ICON_Y + EMOJI_ICON_SIZE + 5
    return pygame.Rect(_x1, _y1, _w, _h)

def get_emoji_cell_rect(idx):
    """第 idx 个表情格子的矩形。"""
    _panel = get_emoji_panel_rect()
    if _panel is None:
        return None
    _col = idx % EMOJI_PANEL_COLS
    _row = idx // EMOJI_PANEL_COLS
    _x = _panel.x + EMOJI_PANEL_PAD + _col * EMOJI_CELL
    _y = _panel.y + EMOJI_PANEL_PAD + _row * EMOJI_CELL
    return pygame.Rect(_x, _y, EMOJI_CELL - 4, EMOJI_CELL - 4)

def get_emoji_num_list():
    return sorted(emoji_images.keys())

def show_emoji(side, num):
    """在指定侧（'1p' / '2p'）显示表情 num。"""
    global emoji_show_side, emoji_show_img, emoji_show_timer
    if num not in emoji_images:
        return
    emoji_show_side = side
    emoji_show_img = emoji_images[num]
    emoji_show_timer = EMOJI_SHOW_FRAMES

def draw_emoji_ui(mouse_pos):
    """绘制表情 icon 按钮、气泡面板、已显示的表情气泡。
    仅联机模式下调用；其他模式直接返回。
    KO 结算期间不再显示表情 UI（对方已被终结时禁止再发情）。"""
    global emoji_panel_open
    if not network_connected:
        return
    if STATE != FIGHT:
        return
    if ko_phase != 0:
        return
    if fight_intro_phase != FIGHT_INTRO_DONE:
        return
    if emoji_icon_img is None:
        return
    # 1) icon 按钮
    _ir = get_emoji_icon_rect()
    if _ir.collidepoint(mouse_pos):
        pygame.draw.rect(screen, (90, 90, 130), _ir)
    else:
        pygame.draw.rect(screen, (50, 50, 80), _ir)
    pygame.draw.rect(screen, (220, 220, 220), _ir, 2)
    _icon_scaled = pygame.transform.smoothscale(
        emoji_icon_img, (EMOJI_ICON_SIZE - 6, EMOJI_ICON_SIZE - 6))
    screen.blit(_icon_scaled, (_ir.x + 3, _ir.y + 3))
    # 2) 气泡面板
    if emoji_panel_open:
        _panel = get_emoji_panel_rect()
        if _panel is not None:
            _bg = pygame.Surface((_panel.w, _panel.h), pygame.SRCALPHA)
            _bg.fill((20, 20, 30, 230))
            screen.blit(_bg, _panel.topleft)
            pygame.draw.rect(screen, (200, 200, 200), _panel, 2)
            _nums = get_emoji_num_list()
            for _i, _num in enumerate(_nums):
                _cr = get_emoji_cell_rect(_i)
                if _cr is None:
                    continue
                if _cr.collidepoint(mouse_pos):
                    pygame.draw.rect(screen, (100, 100, 160), _cr)
                    pygame.draw.rect(screen, (255, 255, 255), _cr, 2)
                _img = emoji_images[_num]
                _scaled = pygame.transform.smoothscale(
                    _img, (_cr.w, _cr.h))
                screen.blit(_scaled, _cr.topleft)
    # 3) 已发出的表情气泡
    if (emoji_show_timer > 0 and emoji_show_side is not None
            and emoji_show_img is not None
            and emoji_message_img is not None):
        _msg = emoji_message_img
        # 2P 用默认朝向；1P 需要水平镜像（表情图片本身不镜像）
        if emoji_show_side == "1p":
            _msg = get_flip(_msg)
        _mw, _mh = _msg.get_size()
        if emoji_show_side == "1p":
            _mx = 15
            _my = 75
        else:
            _mx = W - 15 - _mw
            # 2P 气泡下移，避免盖住右上角的 emoji icon 按钮。
            # icon 按钮占据 y = EMOJI_ICON_Y ~ EMOJI_ICON_Y + EMOJI_ICON_SIZE
            # 这里从按钮下方 8 像素开始画气泡。
            _my = EMOJI_ICON_Y + EMOJI_ICON_SIZE + 8
        screen.blit(_msg, (_mx, _my))
        # 表情贴底、左右居中
        _emoji = emoji_show_img
        _max_w = max(1, _mw - 12)
        _max_h = max(1, _mh - 8)
        _ew, _eh = _emoji.get_size()
        if _ew > _max_w or _eh > _max_h:
            _s = min(_max_w / _ew, _max_h / _eh)
            _emoji = pygame.transform.smoothscale(
                _emoji,
                (max(1, int(_ew * _s)), max(1, int(_eh * _s))))
        _er = _emoji.get_rect()
        _er.midbottom = (_mx + _mw // 2, _my + _mh - 4)
        screen.blit(_emoji, _er)

dual_selected_1p = None
dual_selected_2p = None

fight_chars = []
char1_pos = [200, 360]
char2_pos = [800, 360]
selected_idx = -1
slide_center_x = 900
slide_active = False
fade_active = False
fade_text = ""
fade_alpha = 255
load_time = 0
catch_played = False
catch_finished = False
catch_map_json = None
catch_map_show_name = ""
wait_time = 0
# 上一次抽到的地图名：用于防止连续抽到同一张地图。
last_catch_map_name = None

damage_numbers = []

# wyk 技能一的蛋投射物列表。每个元素为 dict：
#   owner_index  发射方索引（0 或 1）
#   x, y         当前位置（浮点）
#   vx, vy       当前速度
wyk_eggs = []
# wyk 技能一的蛋碎粒子列表：格式同爆炸粒子
#   [x, y, vx, vy, life, size, (r, g, b)]
wyk_egg_particles = []

# 书本投射物列表：每个元素为 dict，包含 owner_index / target_index /
# facing / x / y / base_y / vx / phase 等字段
book_projectiles = []
# 携带技能 2 的武器列表：每个元素为 dict，字段为
#   owner_index / target_index / facing / x / y / vx / last_hit_frame
# last_hit_frame 记录最近一次命中目标时的全局帧号，用于 0.1s 伤害间隔判定
skill2_2_projectiles = []
# 爆炸粒子列表：每个元素为 [x, y, vx, vy, life, size, (r, g, b)]
explosion_particles = []

# gjl 技能二的 code 投射物列表。每个元素为 dict：
#   owner_index    发起方索引（0 或 1）
#   x, y           当前位置（浮点）
#   angle          当前运动角度（弧度）
#   speed          每帧速度（像素）
#   damage         命中伤害
#   is_curved      是否是第二段（曲线追踪）的 code
#   target_x, target_y  锁定的敌方位置（仅曲线 code 使用）
gjl_skill2_projectiles = []

# 客户端本地粒子生成用的状态：记录上一帧收到的投射物和双方隐身状态。
# 联机时粒子不走网络，客户端靠对比相邻两次 STATE 数据来推断
# "书本消失（命中）" 和 "隐身开启" 这两个事件，在本地生成粒子。
# 视觉上大致相似即可，不要求与主机完全同步。
_client_last_projs = []
_client_c1_last_invis = False
_client_c2_last_invis = False
# wyk 技能一的本地事件追踪
#   _client_last_eggs        上一帧收到的蛋列表（用于蛋碎粒子）
#   _client_last_transformed id(char) -> bool（用于变身/变回来烟雾）
_client_last_eggs = []
_client_last_transformed = {}
_client_last_invincible = {}

# chr_1 技能一追踪书投射物列表（主机逻辑，客户端从 STATE 重建）
chr1_track_books = []
# chr_1 技能二的旋转散射书列表（主机逻辑，客户端从 STATE 重建）
# 每个元素为 dict：owner_index / x / y / vx / vy / angle / damage
chr1_skill2_books = []
# chr_1 环绕红色粒子：每个元素为
#   [x, y, vx, vy, life, max_life, size]
chr1_aura_particles = []

# ---------- 联机表情系统状态 ----------
# emoji_icon_img / emoji_message_img / emoji_images 已经在上面
# 的资源加载段赋值过，这里绝对不能再重置为 None / {}，否则图片会被清空，
# 表情按钮和气泡都不会显示。
emoji_panel_open = False     # 气泡面板是否打开
emoji_show_side = None       # 当前展示表情的边（'1p' / '2p' / None）
emoji_show_img = None        # 当前展示的表情图片
emoji_show_timer = 0         # 剩余展示帧数（<=0 时隐藏）

ui_frame_count = 0

# ---------- 单人模式战斗中设置面板的状态 ----------
solo_settings_char_idx = 0      # 当前选中的角色索引（对应 char_list）
solo_settings_skill_num = None  # 当前选中的携带技能编号（None 表示不选）
solo_settings_map_idx = 0       # 当前选中的地图索引（对应 maps_data）
solo_settings_scroll = 0        # 整页滚动偏移（<=0，负数表示内容上移）

# 视口定义：顶部标题之下、底部按钮之上，是三个单元的滚动可视区
SOLO_VIEW_TOP = 60
SOLO_VIEW_BOTTOM = 540
SOLO_VIEW_H = SOLO_VIEW_BOTTOM - SOLO_VIEW_TOP
SOLO_UNIT_GAP = 20              # 三个单元之间的垂直间隔


# ---------- 战斗开场状态 ----------
# fight_intro_phase 为 FIGHT_INTRO_DONE 时表示开场结束，进入正常战斗。
# ui_slide_timer 用于控制战斗开始后 UI 从屏幕边缘滑入的效果：
#   达到 UI_SLIDE_FRAMES 时表示 UI 完全就位。
fight_intro_phase = FIGHT_INTRO_DONE
fight_intro_timer = 0
ui_slide_timer = UI_SLIDE_FRAMES

# ---------- 单人设置辅助函数 ----------
def reset_character_for_battle(char):
    """把角色的战斗状态清零，准备下一场战斗。"""
    char.hp = char.max_hp
    char.stamina = STAMINA_START
    char.stamina_regen_timer = 0
    char.hit_recover_accum = 0
    char.is_hit = False
    char.hit_timer = 0
    char.hit_vx = 0
    char.hit_vy = 0
    char._hit_bounce_count = 0
    char._training_bounce_count = 0
    char.is_standing_up = False
    char.stand_up_done = False
    char.stand_up_index = 0
    char.stand_up_timer = 0
    char.damage_protection_total = 0.0
    char.is_invincible = False
    char.invincible_timer = 0
    char.is_stand_up_invincible = False
    char.stand_up_invincible_timer = 0
    char.needs_ground_snap = False
    char.extra_invis_active = False
    char.extra_invis_timer = 0
    char.extra_invis_phase = 0
    char.pending_book_launch = 0
    char.pending_skill2_launch = 0
    char.skill2_uses_left = SKILL2_THROW_COUNT
    char.is_attacking = False
    char.attack_phase = None
    char.attack_type = None
    char.attack_anim = None
    char.attack_anim_done = False
    char.current_frame = 0
    char.frame_timer = 0
    char.has_delivered_hit = False
    char.jump_phase = 0
    char.vy = 0
    char.facing = "right"
    char.skills['skill_1']['current_cd'] = 0
    char.skills['skill_2']['current_cd'] = char.skills['skill_2']['cd']
    char.skills['escape']['current_cd'] = 0
    char.skills['extra']['current_cd'] = 0
    char.attack_pressed = False
    char.attack_held = False
    char.attack_combo_phase = 0
    char.attack_combo_window_timer = 0
    char.attack_combo_input_pressed = False
    char.escape_pressed = False
    char.skill_pressed = False
    char.skill2_pressed = False
    char.extra_pressed = False
    char.jump_pressed = False
    char.gjl_skill1_phase = 0
    char.gjl_skill1_timer = 0
    char.gjl_skill1_total_frames = 0
    char.gjl_skill1_hit = False
    char.gjl_skill1_uses_left = GJL_SKILL1_STORE_COUNT
    char.gjl_skill2_phase = 0
    char.gjl_skill2_timer = 0
    char.gjl_skill2_origin_angle = 0.0
    char.gjl_skill2_uses_left = GJL_SKILL2_STORE_COUNT
    char.gjl_skill1_start_x = 0.0
    # 清空变身状态
    char.wyk_egg_transformed = False
    char.wyk_egg_timer = 0
    # 重置 wyk 技能一存储次数
    char.wyk_skill1_uses_left = WYK_SKILL1_STORE_COUNT
    # 清空 wyk 技能二状态
    char.pending_wyk_skill2_launch = 0
    char.wyk_skill2_active = False
    char.wyk_skill2_timer = 0
    char.wyk_skill2_hit = False
    char.wyk_skill2_last_hit_frame = -999
    char.wyk_skill2_shake_x = 0
    char.wyk_skill2_shake_y = 0
    # 【音效】重置技能触发序号
    char.skill1_trigger_seq = 0
    char.skill2_trigger_seq = 0
    # 隐藏技能：重置
    char.wyk_hidden_boost_timer = 0
    char.wyk_hidden_smoke = False
    char.wyk_skill2_damage_override = None
    # 【chr_1 技能一】重置环绕书剩余本数
    char.chr1_skill1_uses_left = CHR1_SKILL1_STORE_COUNT
    char.chr1_skill1_activated = False
    char.chr1_orbit_books_alive = [True, True, True]
    char.chr1_orbit_books_age = 0
    # 【chr_1 自动二技能】重置
    char.chr1_book_hit_count = 0
    char.chr1_auto_skill2_active = False
    char.chr1_auto_skill2_timer = 0
    char.chr1_auto_skill2_interval_timer = 0
    char.chr1_auto_skill2_used = False
    char.chr1_auto_skill2_used = False

def apply_solo_settings():
    """应用单人设置：换角色、换携带技能、换地图，并重置战斗从头开始。"""
    global fight_chars, char1_pos, char2_pos, catch_map_json, map_img
    global catch_map_show_name, fight_intro_phase, fight_intro_timer
    global ui_slide_timer, ko_phase, ko_timer, ko_loser_index
    global ko_loser_airborne, ko_bounce_count
    # 1) 角色
    _new_char = char_list[solo_settings_char_idx]
    reset_character_for_battle(_new_char)
    # 2) 携带技能
    _num = solo_settings_skill_num
    if _num is not None:
        _icon = extra_skill_icons.get(_num)
        _sel = None
        for _s in extra_skill_data:
            if _s.get("num", 0) == _num:
                _sel = _s
                break
        if _icon is not None and _sel is not None:
            _new_char.icons['extra'] = _icon
            _new_char.skills['extra']['cd'] = int(round(_sel.get("cd", 0) * FPS))
            _new_char.skills['extra']['recovery'] = _sel.get("recovery", 0)
            _new_char.extra_skill_num = _num
        else:
            _new_char.icons['extra'] = None
            _new_char.extra_skill_num = 0
    else:
        _new_char.icons['extra'] = None
        _new_char.extra_skill_num = 0
    if training_mode and len(fight_chars) >= 2:
        # 训练模式：保留 2P，只替换 1P
        _2p = fight_chars[1]
        reset_character_for_battle(_2p)
        fight_chars = [_new_char, _2p]
    else:
        fight_chars = [_new_char]
    # 3) 地图
    if 0 <= solo_settings_map_idx < len(maps_data):
        _m = maps_data[solo_settings_map_idx]
        catch_map_json = _m
        catch_map_show_name = _m.get("show_name", _m.get("name", ""))
        _mn = _m.get("name", "")
        if _mn in maps_img:
            map_img = maps_img[_mn]
    # 4) 战场重置
    char1_pos = [200, 360]
    char2_pos = [800, 360]
    damage_numbers.clear()
    book_projectiles.clear()
    explosion_particles.clear()
    smoke_particles.clear()
    blood_particles.clear()
    skill2_2_projectiles.clear()
    chr1_skill2_books.clear()
    fight_intro_phase = FIGHT_INTRO_READY_STAMP
    fight_intro_timer = 0
    ui_slide_timer = UI_SLIDE_FRAMES
    ko_phase = 0
    ko_timer = 0
    ko_loser_index = 0
    ko_loser_airborne = False
    ko_bounce_count = 0

# ---------- 绘制HUD ----------
def draw_hud(char1, char2, slide=1.0):
    """
    slide: 1.0 表示 UI 就位，0.0 表示 UI 完全在屏幕外。
    左上角玩家 1 的 HUD 从左侧滑入，右上角玩家 2 的 HUD 从右侧滑入。
    其余元素位置由头像位置推导，会自动跟随偏移。
    """
    ease = 1.0 - (1.0 - slide) ** 3
    off = 1.0 - ease
    p1_dx = int(-300 * off)
    p2_dx = int(300 * off)

    avatar_size = 55
    spacing = 10
    bar_height = 16
    bar_y = 20
    bar_max_width = 280
    font = get_font(16)

    # 局域网下：本机角色蓝、对方红；其余模式 1P 蓝 2P 红
    if network_connected:
        if network_is_1p:
            BASE_FRAME_COLOR_P1 = (0, 100, 255)
            BASE_FRAME_COLOR_P2 = (255, 50, 50)
        else:
            BASE_FRAME_COLOR_P1 = (255, 50, 50)
            BASE_FRAME_COLOR_P2 = (0, 100, 255)
    else:
        BASE_FRAME_COLOR_P1 = (0, 100, 255)
        BASE_FRAME_COLOR_P2 = (255, 50, 50)

    if char1:
        try:
            idx = next(i for i, d in enumerate(choose_list) if d["name"] == char1.name)
            avatar_img = choose_btns[idx].img
            show_name = choose_list[idx]["show_name"]
            avatar_key = idx
        except (StopIteration, IndexError):
            avatar_img = chr_head if char1.name == "chr" else gjl_head
            show_name = char1.name
            avatar_key = char1.name

        avatar_img = get_scaled_avatar(avatar_img, avatar_key, avatar_size)
        avatar_rect = avatar_img.get_rect(topleft=(15 + p1_dx, 15))
        frame_color = get_avatar_frame_color(BASE_FRAME_COLOR_P1, char1.damage_protection_total)
        frame_width = get_avatar_frame_width(char1.damage_protection_total)
        pad = frame_width - 1
        border_outer_rect = pygame.Rect(
            avatar_rect.x - pad, avatar_rect.y - pad,
            avatar_size + pad * 2, avatar_size + pad * 2
        )
        draw_protection_glow(screen, avatar_rect, pad, frame_color,
                             char1.damage_protection_total, ui_frame_count)
        pygame.draw.rect(screen, frame_color, border_outer_rect, frame_width)
        screen.blit(avatar_img, avatar_rect)

        bar_x = avatar_rect.right + spacing
        bar_width = min(bar_max_width, (W // 2 - 30) - bar_x)
        hp_ratio = char1.hp / char1.max_hp
        pygame.draw.rect(screen, (0, 0, 0), (bar_x - 2, bar_y - 2, bar_width + 4, bar_height + 4))
        pygame.draw.rect(screen, (50, 50, 50), (bar_x, bar_y, bar_width, bar_height))
        if hp_ratio > 0.5:
            color = (0, 255, 0)
        elif hp_ratio > 0.25:
            color = (255, 255, 0)
        else:
            color = (255, 0, 0)
        pygame.draw.rect(screen, color, (bar_x, bar_y, bar_width * hp_ratio, bar_height))
        pygame.draw.rect(screen, (0, 0, 0), (bar_x - 2, bar_y - 2, bar_width + 4, bar_height + 4), 2)

        # 联机时在角色名后显示身份标签
        if network_connected:
            _name_display = show_name + ("（你）" if network_is_1p else "（对方）")
        else:
            _name_display = show_name
        # 体力条：长度与血量条相同，宽度较小，白色填充
        stamina_y = bar_y + bar_height + 2
        stamina_h = 5
        stamina_ratio = char1.stamina / char1.max_stamina
        pygame.draw.rect(screen, (0, 0, 0),
                         (bar_x - 2, stamina_y - 2, bar_width + 4, stamina_h + 4))
        pygame.draw.rect(screen, (50, 50, 50),
                         (bar_x, stamina_y, bar_width, stamina_h))
        pygame.draw.rect(screen, (255, 255, 255),
                         (bar_x, stamina_y, bar_width * stamina_ratio, stamina_h))
        pygame.draw.rect(screen, (0, 0, 0),
                         (bar_x - 2, stamina_y - 2, bar_width + 4, stamina_h + 4), 1)
        name_text = font.render(_name_display, True, (255, 255, 255))
        name_shadow = font.render(_name_display, True, (0, 0, 0))
        name_rect = name_text.get_rect(topleft=(bar_x, stamina_y + stamina_h + 3))
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            screen.blit(name_shadow, (name_rect.x + dx, name_rect.y + dy))
        screen.blit(name_text, name_rect)

    if char2:
        try:
            idx = next(i for i, d in enumerate(choose_list) if d["name"] == char2.name)
            avatar_img = choose_btns[idx].img
            show_name = choose_list[idx]["show_name"]
            avatar_key = idx
        except (StopIteration, IndexError):
            avatar_img = chr_head if char2.name == "chr" else gjl_head
            show_name = char2.name
            avatar_key = char2.name

        avatar_img = get_scaled_avatar(avatar_img, avatar_key, avatar_size)
        avatar_rect = avatar_img.get_rect(topright=(W - 15 + p2_dx, 15))
        frame_color = get_avatar_frame_color(BASE_FRAME_COLOR_P2, char2.damage_protection_total)
        frame_width = get_avatar_frame_width(char2.damage_protection_total)
        pad = frame_width - 1
        border_outer_rect = pygame.Rect(
            avatar_rect.x - pad, avatar_rect.y - pad,
            avatar_size + pad * 2, avatar_size + pad * 2
        )
        draw_protection_glow(screen, avatar_rect, pad, frame_color,
                             char2.damage_protection_total, ui_frame_count)
        pygame.draw.rect(screen, frame_color, border_outer_rect, frame_width)
        screen.blit(avatar_img, avatar_rect)

        bar_x = W // 2 + 30 + p2_dx
        bar_width = avatar_rect.left - spacing - bar_x
        hp_ratio = char2.hp / char2.max_hp
        pygame.draw.rect(screen, (0, 0, 0), (bar_x - 2, bar_y - 2, bar_width + 4, bar_height + 4))
        pygame.draw.rect(screen, (50, 50, 50), (bar_x, bar_y, bar_width, bar_height))
        if hp_ratio > 0.5:
            color = (0, 255, 0)
        elif hp_ratio > 0.25:
            color = (255, 255, 0)
        else:
            color = (255, 0, 0)
        pygame.draw.rect(screen, color, (bar_x, bar_y, bar_width * hp_ratio, bar_height))
        pygame.draw.rect(screen, (0, 0, 0), (bar_x - 2, bar_y - 2, bar_width + 4, bar_height + 4), 2)

        # 联机时在角色名后显示身份标签
        if network_connected:
            _name_display = show_name + ("（对方）" if network_is_1p else "（你）")
        else:
            _name_display = show_name
        # 体力条：长度与血量条相同，宽度较小，白色填充
        stamina_y = bar_y + bar_height + 2
        stamina_h = 5
        stamina_ratio = char2.stamina / char2.max_stamina
        pygame.draw.rect(screen, (0, 0, 0),
                         (bar_x - 2, stamina_y - 2, bar_width + 4, stamina_h + 4))
        pygame.draw.rect(screen, (50, 50, 50),
                         (bar_x, stamina_y, bar_width, stamina_h))
        pygame.draw.rect(screen, (255, 255, 255),
                         (bar_x, stamina_y, bar_width * stamina_ratio, stamina_h))
        pygame.draw.rect(screen, (0, 0, 0),
                         (bar_x - 2, stamina_y - 2, bar_width + 4, stamina_h + 4), 1)
        name_text = font.render(_name_display, True, (255, 255, 255))
        name_shadow = font.render(_name_display, True, (0, 0, 0))
        name_rect = name_text.get_rect(topright=(bar_x + bar_width, stamina_y + stamina_h + 3))
        for dx, dy in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            screen.blit(name_shadow, (name_rect.x + dx, name_rect.y + dy))
        screen.blit(name_text, name_rect)

# ---------- 联机战斗同步辅助函数 ----------
def build_network_state():
    """主机：把当前战斗状态打包成字典，用于发给客户端。"""
    def _cs(c):
        return {
            "name": c.name,
            "hp": int(c.hp),
            "stamina": int(c.stamina),
            "facing": c.facing,
            "is_hit": c.is_hit,
            "hit_facing": c.hit_facing,
            "is_standing_up": c.is_standing_up,
            "stand_up_index": c.stand_up_index,
            "is_attacking": c.is_attacking,
            "attack_phase": c.attack_phase if c.attack_phase else "",
            "attack_anim": c.attack_anim if c.attack_anim else "",
            "has_delivered_hit": c.has_delivered_hit,
            "current_frame": c.current_frame,
            "attack_combo_phase": c.attack_combo_phase,
            "jump_phase": c.jump_phase,
            "vy": c.vy,
            "is_invincible": c.is_invincible,
            "isu": c.is_stand_up_invincible,
            "extra_invis_active": c.extra_invis_active,
            "extra_invis_phase": c.extra_invis_phase,
            "extra_invis_timer": c.extra_invis_timer,
            "s2a": c.extra_skill_2_active,
            "s2p": c.extra_skill_2_phase,
            "s2t": c.extra_skill_2_timer,
            "dpt": c.damage_protection_total,
            "skill2_uses_left": c.skill2_uses_left,
            "pending_book_launch": c.pending_book_launch,
            "pending_skill2_launch": c.pending_skill2_launch,
            "cd_skill_1": c.skills['skill_1']['current_cd'],
            "cd_skill_2": c.skills['skill_2']['current_cd'],
            "cd_escape": c.skills['escape']['current_cd'],
            "cd_extra": c.skills['extra']['current_cd'],
            "active_anim": c.active_anim,
            # wyk 技能一：变身状态
            "wet": c.wyk_egg_transformed,
            "wyk_egg_timer": c.wyk_egg_timer,
            "wyk_skill1_uses_left": c.wyk_skill1_uses_left,
            # wyk 技能二（声波）状态
            "pw2l": c.pending_wyk_skill2_launch,
            "wyk_skill2_active": c.wyk_skill2_active,
            "wyk_skill2_timer": c.wyk_skill2_timer,
            # gjl 技能状态字段：让客户端能看到剩余次数和前摇/突进状态
            "gjl_skill1_uses_left": c.gjl_skill1_uses_left,
            "gjl_skill1_phase": c.gjl_skill1_phase,
            "gjl_skill1_timer": c.gjl_skill1_timer,
            "gjl_skill1_total_frames": c.gjl_skill1_total_frames,
            "gjl_skill2_phase": c.gjl_skill2_phase,
            "gjl_skill2_timer": c.gjl_skill2_timer,
            "gjl_skill2_origin_angle": c.gjl_skill2_origin_angle,
            "gjl_skill2_uses_left": c.gjl_skill2_uses_left,
            # 【音效】技能触发序号，客户端对比前后两帧识别技能触发
            "s1q": c.skill1_trigger_seq,
            "s2q": c.skill2_trigger_seq,
            # 隐藏技能：被控玩家是否正在冒白烟（客户端本地生成粒子）
            "wyk_hidden_smoke": c.wyk_hidden_smoke,
            # chr_1 技能一：环绕书剩余本数
            "chr1_skill1_uses_left": c.chr1_skill1_uses_left,
            # 是否已激活环绕书效果
            "chr1_skill1_activated": c.chr1_skill1_activated,
            # 每本环绕书的存活状态（客户端绘制时需要）
            "cbo": list(c.chr1_orbit_books_alive),
            # 环绕书累计存活帧数（用于客户端渐隐显示）
            "cbog": c.chr1_orbit_books_age,
        }
    return {
        "c1": _cs(fight_chars[0]) if len(fight_chars) >= 1 else None,
        "c2": _cs(fight_chars[1]) if len(fight_chars) >= 2 else None,
        "pos1": [int(char1_pos[0]), int(char1_pos[1])],
        "pos2": [int(char2_pos[0]), int(char2_pos[1])],
        "projs": [{"x": int(p['x']), "y": int(p['y']), "facing": p['facing']}
                  for p in book_projectiles],
        # wyk 技能一的蛋投射物同步
        "wyk_eggs": [{"x": int(e['x']), "y": int(e['y']),
                       "vx": e['vx'], "owner_index": e['owner_index']}
                      for e in wyk_eggs],
        "skill2_2_weps": [{"x": int(w['x']), "y": int(w['y']),
                            "facing": w['facing'], "owner_index": w['owner_index']}
                           for w in skill2_2_projectiles],
        "dmgs": [{"x": int(n.x), "y": int(n.y), "d": n.damage, "a": n.alpha}
                 for n in damage_numbers if n.alpha > 0],
        # 爆炸粒子和白烟是纯视觉细节，两端观感差别极难辨认，不通过网络同步；
        # 这样每次 STATE 包的体积能缩小 80% 以上，大幅降低主机序列化和
        # 客户端反序列化的 CPU 开销。客户端对应的列表会保持为空，
        # 绘制循环自然跳过。
        "map": (catch_map_json["name"]
                 if (catch_map_json and STATE in (SHOW_MAP, FIGHT, FIGHT_KO))
                 else ""),
        "intro": fight_intro_phase,
        "intro_t": fight_intro_timer,
        "ui_slide": ui_slide_timer,
        "state": STATE,
        "ko": {
            "phase": ko_phase,
            "timer": ko_timer,
            "loser": ko_loser_index,
            "airborne": ko_loser_airborne,
        },
        # gjl 技能二的 code 投射物同步。
        # code 是游戏实体（会命中造成伤害），必须同步给客户端。
        # 客户端不做本地预测，每帧按主机发来的位置重建即可。
        "gjl_codes": [
            {"x": int(p['x']), "y": int(p['y']),
             "angle": round(p['angle'], 4),
             "speed": p['speed'],
             "damage": p['damage'],
             "is_curved": p['is_curved'],
             "track_live": p.get('track_live', False),
             "owner_index": p['owner_index'],
             "target_x": round(p['target_x'], 1),
             "target_y": round(p['target_y'], 1)}
            for p in gjl_skill2_projectiles
        ],
        # chr_1 技能一的追踪书投射物同步
        "chr1_track_books": [
            {"x": int(p['x']), "y": int(p['y']),
             "angle": round(p['angle'], 4),
             "speed": p['speed'], "damage": p['damage'],
             "owner_index": p['owner_index'],
             "target_x": round(p['target_x'], 1),
             "target_y": round(p['target_y'], 1),
             "age": p.get('age', 0)}
            for p in chr1_track_books
        ],
        # chr_1 技能二的旋转散射书同步
        "chr1_s2_books": [
            {"x": int(p['x']), "y": int(p['y']),
             "angle": round(p['angle'], 4),
             "damage": p['damage'],
             "owner_index": p['owner_index'],
             "age": p.get('age', 0)}
            for p in chr1_skill2_books
        ],
        # 震屏状态：客户端复现主机的屏幕缩放效果，保证画面一致
        "shake": {
            "timer": shake.timer,
            "active": shake.active,
            "max_scale": shake.current_max_scale,
        },
    }

def apply_network_state(s):
    """客户端：把主机发来的状态应用到本地对象。"""
    global char1_pos, char2_pos, catch_map_json, map_img
    global fight_intro_phase, fight_intro_timer, ui_slide_timer
    global ko_phase, ko_timer, ko_loser_index, ko_loser_airborne
    global STATE
    global network_local_ready, network_remote_ready
    global catch_map_show_name, load_time, catch_played, catch_finished
    global _client_last_projs, _client_c1_last_invis, _client_c2_last_invis
    # wyk 技能一的本地事件追踪变量也在这里读写，
    # 必须在函数开头声明 global，否则会被当成局部变量导致
    # UnboundLocalError。
    global _client_last_eggs, _client_last_transformed
    global skill2_2_projectiles
    global chr1_track_books, chr1_skill2_books
    # 地图
    if s.get("map"):
        _mn = s["map"]
        if catch_map_json is None or catch_map_json.get("name") != _mn:
            catch_map_json = {"name": _mn}
            if _mn in maps_img:
                map_img = maps_img[_mn]
    # 位置
    if "pos1" in s and len(fight_chars) >= 1:
        char1_pos[0] = s["pos1"][0]
        char1_pos[1] = s["pos1"][1]
    if "pos2" in s and len(fight_chars) >= 2:
        char2_pos[0] = s["pos2"][0]
        char2_pos[1] = s["pos2"][1]
    # 角色字段
    def _ac(c, d, _idx=0):
        if d is None or c is None:
            return
        # wyk 技能一：变身状态从 False 变 True（或反向）时，
        # 在本地生成烟雾特效。变回来自带伤害由主机负责，客户端只画。
        _ck_t = id(c)
        _old_trans = _client_last_transformed.get(_ck_t, False)
        _new_trans = d.get("wet", False)
        if _new_trans != _old_trans:
            _tpos = char1_pos if _idx == 0 else char2_pos
            spawn_smoke(_tpos[0], _tpos[1])
        _client_last_transformed[_ck_t] = _new_trans
        # escape 触发（is_invincible 从 False 变 True）：生成烟雾
        _old_inv = _client_last_invincible.get(_ck_t, False)
        _new_inv = d["is_invincible"]
        if _new_inv and not _old_inv:
            _tpos_inv = char1_pos if _idx == 0 else char2_pos
            spawn_smoke(_tpos_inv[0], _tpos_inv[1])
        _client_last_invincible[_ck_t] = _new_inv
        # 【音效】客户端本地播放技能音效：
        # 对比相邻两帧 STATE 里 skill1/2_trigger_seq 是否增加，
        # 增加则说明技能刚被触发，本地播放对应音效。
        _new_s1_seq = d.get("s1q", 0)
        _new_s2_seq = d.get("s2q", 0)
        if _new_s1_seq > c.skill1_trigger_seq:
            play_sfx(c.name, 'skill_1')
        if _new_s2_seq > c.skill2_trigger_seq:
            play_sfx(c.name, 'skill_2')
        c.skill1_trigger_seq = _new_s1_seq
        c.skill2_trigger_seq = _new_s2_seq
        # 先找出 c 在 fight_chars 里的索引，用于判断对手是谁。
        # 客户端不跑逻辑，不知道谁是攻击方、谁是被打方，但可以根据
        # fight_chars 列表确定每个角色的“对手”是哪一位。
        _c_idx = -1
        for _i, _fc in enumerate(fight_chars):
            if _fc is c:
                _c_idx = _i
                break
        # 【音效】客户端对比 hp 是否减少，减少则本地播放 hurt 音效。
        _old_hp_c = c.hp
        _new_hp_c = d["hp"]
        if _new_hp_c < _old_hp_c:
            play_sfx(c.name, 'hurt')
        c.hp = _new_hp_c
        c.stamina = d.get("stamina", STAMINA_START)
        c.facing = d["facing"]
        c.is_hit = d["is_hit"]
        c.hit_facing = d["hit_facing"]
        c.is_standing_up = d["is_standing_up"]
        c.stand_up_index = d["stand_up_index"]
        c.is_attacking = d["is_attacking"]
        c.attack_phase = d["attack_phase"] if d["attack_phase"] else None
        c.attack_anim = d["attack_anim"] if d["attack_anim"] else None
        c.current_frame = d["current_frame"]
        c.attack_combo_phase = d.get("attack_combo_phase", 0)
        c.jump_phase = d["jump_phase"]
        c.vy = d["vy"]
        c.is_invincible = d["is_invincible"]
        c.is_stand_up_invincible = d.get("isu", False)
        c.extra_invis_active = d["extra_invis_active"]
        c.extra_invis_phase = d["extra_invis_phase"]
        c.extra_invis_timer = d["extra_invis_timer"]
        c.extra_skill_2_active = d.get("s2a", False)
        c.extra_skill_2_phase = d.get("s2p", 0)
        c.extra_skill_2_timer = d.get("s2t", 0)
        c.damage_protection_total = d.get("dpt", 0.0)
        c.skill2_uses_left = d["skill2_uses_left"]
        c.pending_book_launch = d["pending_book_launch"]
        c.pending_skill2_launch = d.get("pending_skill2_launch", 0)
        c.skills['skill_1']['current_cd'] = d["cd_skill_1"]
        c.skills['skill_2']['current_cd'] = d["cd_skill_2"]
        c.skills['escape']['current_cd'] = d["cd_escape"]
        c.skills['extra']['current_cd'] = d["cd_extra"]
        c.active_anim = d.get("active_anim", "stand")
        # wyk 技能一：同步变身状态
        c.wyk_egg_transformed = d.get("wet", False)
        c.wyk_egg_timer = d.get("wyk_egg_timer", 0)
        c.wyk_skill1_uses_left = d.get("wyk_skill1_uses_left",
                                       c.wyk_skill1_uses_left)
        # wyk 技能二（声波）状态同步
        c.pending_wyk_skill2_launch = d.get("pw2l", 0)
        c.wyk_skill2_active = d.get("wyk_skill2_active", False)
        c.wyk_skill2_timer = d.get("wyk_skill2_timer", 0)
        # 客户端绘制应和主机保持一致：
        #   主机只有在攻击 anim 阶段会画攻击动画，其他时候都画 stand（或 walk/jump）。
        #   主机的 active_anim 字段在攻击 anim 结束后会滞后一帧（还是攻击动画名），
        #   客户端直接使用会闪一下攻击动画的第一帧（攻击刚结束那一帧）。
        #   这里统一在非攻击 anim 阶段把攻击动画名替换为 stand。
        if c.active_anim in ('hit', 'skill', 'ultimate'):
            if not (c.is_attacking and c.attack_phase == 'anim'):
                c.active_anim = 'stand'
        # gjl 技能状态字段同步（旧版 STATE 包里没有这些键时用默认值兜底）
        c.gjl_skill1_uses_left = d.get("gjl_skill1_uses_left",
                                       c.gjl_skill1_uses_left)
        c.gjl_skill1_phase = d.get("gjl_skill1_phase", 0)
        c.gjl_skill1_timer = d.get("gjl_skill1_timer", 0)
        c.gjl_skill1_total_frames = d.get("gjl_skill1_total_frames", 0)
        c.gjl_skill2_phase = d.get("gjl_skill2_phase", 0)
        c.gjl_skill2_timer = d.get("gjl_skill2_timer", 0)
        c.gjl_skill2_origin_angle = d.get("gjl_skill2_origin_angle", 0.0)
        c.gjl_skill2_uses_left = d.get("gjl_skill2_uses_left",
                                       c.gjl_skill2_uses_left)
        # 隐藏技能：被控玩家白烟标记（仅用于客户端本地生成粒子）
        c.wyk_hidden_smoke = d.get("wyk_hidden_smoke", False)
        # chr_1 技能一：同步环绕书剩余本数
        c.chr1_skill1_uses_left = d.get("chr1_skill1_uses_left",
                                        c.chr1_skill1_uses_left)
        # 是否已激活环绕书效果
        c.chr1_skill1_activated = d.get("chr1_skill1_activated",
                                        c.chr1_skill1_activated)
        # 每本环绕书的存活状态
        _orb_alive = d.get("cbo", None)
        if _orb_alive is not None and len(_orb_alive) == 3:
            c.chr1_orbit_books_alive = [bool(_x) for _x in _orb_alive]
        c.chr1_orbit_books_age = d.get("cbog", 0)
    # 角色名校验：主机发来的 name 和本地不一致时，按主机的 name
    # 重建本地角色对象。否则后续所有状态（hp / 动画 / 位置）都会
    # 套到错误的角色上，表现为"跳成其他角色 / 抽搐"。
    def _verify_char(_idx, _d):
        if _d is None or _idx >= len(fight_chars):
            return None
        _expected = _d.get("name", "")
        if not _expected:
            return fight_chars[_idx]
        _c = fight_chars[_idx]
        if _c.name == _expected:
            return _c
        # 不匹配：从 choose_list 里找对应数据重建
        for _d2 in choose_list:
            if _d2["name"] == _expected:
                _cls = get_char_class(_expected)
                _new = _cls(_d2)
                # 保留携带技能配置（图标 / cd / 编号）
                _new.icons["extra"] = _c.icons.get("extra")
                _new.skills["extra"]["cd"] = _c.skills["extra"]["cd"]
                _new.skills["extra"]["recovery"] = _c.skills["extra"]["recovery"]
                _new.extra_skill_num = _c.extra_skill_num
                fight_chars[_idx] = _new
                return _new
        return _c
    if s.get("c1") is not None and len(fight_chars) >= 1:
        _c1_fixed = _verify_char(0, s["c1"])
        if _c1_fixed is not None:
            _ac(_c1_fixed, s["c1"], 0)
    if s.get("c2") is not None and len(fight_chars) >= 2:
        _c2_fixed = _verify_char(1, s["c2"])
        if _c2_fixed is not None:
            _ac(_c2_fixed, s["c2"], 1)
    # 本地粒子生成之一：爆炸粒子。
    # 对比上一帧和这一帧收到的投射物列表，找出在屏幕内消失的书本，
    # 认为它们命中了，在消失位置生成爆炸粒子。
    # 位置用 30 像素阈值近似匹配（投射物本身没有 id，且两次 STATE 包
    # 之间书本会移动约 24 像素），视觉上够用，不追求 100% 精确。
    _new_projs_raw = s.get("projs", [])
    for _op in _client_last_projs:
        _still_exists = False
        for _np in _new_projs_raw:
            if abs(_op['x'] - _np['x']) < 30 and abs(_op['y'] - _np['y']) < 30:
                _still_exists = True
                break
        # 世界宽 MAP_W，所以只要蛋 / 书本在世界里消失就认为命中；
        # 屏幕外的粒子会被世界画面裁掉，不影响观感。
        if not _still_exists and -20 < _op['x'] < MAP_W + 20:
            spawn_explosion(_op['x'], _op['y'])
    _client_last_projs = [dict(p) for p in _new_projs_raw]
    # wyk 技能一：蛋碎粒子本地推断。
    # 对比上一帧和这一帧的蛋列表，找出在屏幕内消失的蛋，在消失位置
    # 生成蛋碎粒子。
    _new_eggs_raw = s.get("wyk_eggs", [])
    for _oe in _client_last_eggs:
        _still_e = False
        for _ne in _new_eggs_raw:
            if abs(_oe['x'] - _ne['x']) < 30 and abs(_oe['y'] - _ne['y']) < 30:
                _still_e = True
                break
        if not _still_e and -20 < _oe['x'] < MAP_W + 20:
            spawn_egg_particles(_oe['x'], _oe['y'])
    _client_last_eggs = [dict(e) for e in _new_eggs_raw]
    # wyk 技能一：客户端蛋投射物重建（只用于绘制）
    wyk_eggs.clear()
    for _e in _new_eggs_raw:
        wyk_eggs.append({
            "owner_index": _e.get("owner_index", 0),
            "x": float(_e["x"]),
            "y": float(_e["y"]),
            "vx": _e["vx"],
            "vy": 0,
        })

    # 本地粒子生成之二：隐身白烟。
    # 检测角色的 extra_invis_active 从 False 变 True，在角色当前位置冒白烟。
    if s.get("c1") is not None and len(fight_chars) >= 1:
        _new_invis = s["c1"].get("extra_invis_active", False)
        if _new_invis and not _client_c1_last_invis:
            spawn_smoke(char1_pos[0], char1_pos[1])
        _client_c1_last_invis = _new_invis
    if s.get("c2") is not None and len(fight_chars) >= 2:
        _new_invis = s["c2"].get("extra_invis_active", False)
        if _new_invis and not _client_c2_last_invis:
            spawn_smoke(char2_pos[0], char2_pos[1])
        _client_c2_last_invis = _new_invis

    # 投射物
    book_projectiles.clear()
    for p in s.get("projs", []):
        book_projectiles.append({
            "owner_index": 0, "target_index": 1,
            "facing": p["facing"],
            "x": p["x"], "y": p["y"],
            "base_y": p["y"], "vx": 0, "phase": 0,
        })
    # chr_1 技能一追踪书：客户端不做本地预测，直接按主机数据重建
    chr1_track_books.clear()
    for _p in s.get("chr1_track_books", []):
        chr1_track_books.append({
            'owner_index': _p["owner_index"],
            'x': float(_p["x"]),
            'y': float(_p["y"]),
            'angle': _p["angle"],
            'speed': _p["speed"],
            'damage': _p["damage"],
            'target_x': _p["target_x"],
            'target_y': _p["target_y"],
            'age': _p.get("age", 0),
        })
    # chr_1 技能二的旋转散射书：客户端不做本地预测，直接按主机数据重建
    chr1_skill2_books.clear()
    for _p in s.get("chr1_s2_books", []):
        chr1_skill2_books.append({
            'owner_index': _p["owner_index"],
            'x': float(_p["x"]),
            'y': float(_p["y"]),
            'angle': _p["angle"],
            'damage': _p["damage"],
            'vx': 0.0,
            'vy': 0.0,
            'age': _p.get("age", 0),
        })
    # 技能 2 武器：客户端本地预测位置，首次出现时用主机的初始位置，
    # 之后由 draw_client_fight_view 每帧自己移动，保持平滑。
    _remote_weps = s.get("skill2_2_weps", [])
    _client_wep_by_owner = {w['owner_index']: w for w in skill2_2_projectiles}
    _new_wep_list = []
    for rw in _remote_weps:
        _owner = rw["owner_index"]
        if _owner in _client_wep_by_owner:
            _lw = _client_wep_by_owner[_owner]
            _lw['y'] = rw['y']
            _lw['facing'] = rw['facing']
            _new_wep_list.append(_lw)
        else:
            _new_wep_list.append({
                'owner_index': _owner,
                'target_index': 1 - _owner,
                'facing': rw['facing'],
                'x': rw['x'],
                'y': rw['y'],
                'vx': 0,
                'last_hit_frame': -999,
            })
    skill2_2_projectiles.clear()
    skill2_2_projectiles.extend(_new_wep_list)
    # gjl 技能二的 code 投射物：客户端不预测，直接按主机发来的数据
    # 每帧全量重建，位置由主机决定。
    gjl_skill2_projectiles.clear()
    for _p in s.get("gjl_codes", []):
        gjl_skill2_projectiles.append({
            'owner_index': _p["owner_index"],
            'x': float(_p["x"]),
            'y': float(_p["y"]),
            'angle': _p["angle"],
            'speed': _p["speed"],
            'damage': _p["damage"],
            'is_curved': _p["is_curved"],
            'track_live': _p.get("track_live", False),
            'target_x': _p["target_x"],
            'target_y': _p["target_y"],
        })
    # 伤害数字（原地更新，避免每帧重建字体 surface）
    # 原来每帧 clear + 重建 DamageNumber，构造函数里有 2 次 font.render
    # 和 9 次 blit，10 个伤害数字就是每帧 20 次字体渲染，非常吃 CPU。
    # 改成：数量不足时新建，数量一致时只更新位置和 alpha。
    _new_dmgs = s.get("dmgs", [])
    _new_n = len(_new_dmgs)
    if len(damage_numbers) > _new_n:
        del damage_numbers[_new_n:]
    for _di, d in enumerate(_new_dmgs):
        _dmg_val = d["d"]
        if _di < len(damage_numbers):
            _dn = damage_numbers[_di]
            if _dn.damage != _dmg_val:
                # 伤害值变了说明是不同的事件，重建一次
                _dn = DamageNumber(d["x"], d["y"], _dmg_val)
                damage_numbers[_di] = _dn
            _dn.x = d["x"]
            _dn.y = d["y"]
            _dn.alpha = d["a"]
            _dn.composite_surf.set_alpha(d["a"])
            _dn.text_rect.center = (d["x"], d["y"])
        else:
            _dn = DamageNumber(d["x"], d["y"], _dmg_val)
            _dn.alpha = d["a"]
            _dn.composite_surf.set_alpha(d["a"])
            damage_numbers.append(_dn)
    # 粒子和白烟不通过网络同步：上面已通过本地事件推断生成了。
    # 这里不要 clear，否则会把刚生成的粒子清掉。粒子的生命周期由
    # draw_client_fight_view 里的更新循环管理。
    # 状态
    _old_ko_phase = ko_phase
    fight_intro_phase = s["intro"]
    fight_intro_timer = s["intro_t"]
    ui_slide_timer = s["ui_slide"]
    ko_phase = s["ko"]["phase"]
    ko_timer = s["ko"]["timer"]
    ko_loser_index = s["ko"]["loser"]
    ko_loser_airborne = s["ko"]["airborne"]
    # 【音效】KO 从无到有时，客户端也在本地播放失败方角色的死亡音效，
    # 保证联机双方都能听到死亡音效。
    if (_old_ko_phase == 0 and ko_phase != 0
            and len(fight_chars) > ko_loser_index):
        play_sfx(fight_chars[ko_loser_index].name, 'dead')
    # 同步震屏：客户端不能自己 start，必须完全跟随主机
    if "shake" in s:
        shake.timer = s["shake"]["timer"]
        shake.active = s["shake"]["active"]
        shake.current_max_scale = s["shake"]["max_scale"]
    # 状态切换（注意：主机的 STATE 通过 json 发送后是整数，不是字符串）
    _remote_state = s.get("state")
    if _remote_state == FIGHT_KO and STATE in (FIGHT, WAIT_HOST):
        STATE = FIGHT_KO
    elif _remote_state == FIGHT and STATE not in (FIGHT, FIGHT_KO):
        STATE = FIGHT
    elif _remote_state == LOADING and STATE not in (LOADING, SHOW_MAP, FIGHT, FIGHT_KO):
        # 客户端也进入 LOADING，本地播放砖块动画，但不抽地图
        STATE = LOADING
        load_time = 0
        catch_played = True
        catch_finished = True
    elif _remote_state == SHOW_MAP and STATE not in (SHOW_MAP, FIGHT, FIGHT_KO):
        # 客户端进入 SHOW_MAP，显示主机抽到的地图名并触发头像滑入
        STATE = SHOW_MAP
        load_time = 0
        _mn = s.get("map", "")
        catch_map_show_name = _mn
        for _md in maps_data:
            if _md.get("name") == _mn:
                catch_map_show_name = _md.get("show_name", _mn)
                break
        if len(fight_chars) >= 1:
            try:
                _i1 = next(i for i, d in enumerate(choose_list) if d["name"] == fight_chars[0].name)
                if len(fight_chars) == 2:
                    _i2 = next(i for i, d in enumerate(choose_list) if d["name"] == fight_chars[1].name)
                    if network_connected:
                        if network_host_is_1p:
                            _l1, _l2 = "房主", "成员"
                        else:
                            _l1, _l2 = "成员", "房主"
                        avatar_show.set(choose_btns[_i1].img, choose_btns[_i2].img, _l1, _l2)
                    else:
                        avatar_show.set(choose_btns[_i1].img, choose_btns[_i2].img)
                else:
                    avatar_show.set(choose_btns[_i1].img)
            except (StopIteration, IndexError):
                pass
    elif _remote_state == LAN_READY:
        STATE = LAN_READY
        network_local_ready = False
        network_remote_ready = False
        ko_phase = 0
        ko_timer = 0

def client_send_input(keys_state):
    """客户端：收集本机玩家按键状态并发送给主机。
    局域网下双方都使用 p1 键位（WASD 等大键盘）。"""
    _prefix = 'p1_'
    inp = {
        "attack": bool(keys_state[keymap[_prefix + 'attack']]),
        "escape": bool(keys_state[keymap[_prefix + 'escape']]),
        "skill1": bool(keys_state[keymap[_prefix + 'skill1']]),
        "skill2": bool(keys_state[keymap[_prefix + 'skill2']]),
        "extra": bool(keys_state[keymap[_prefix + 'extra']]),
        "jump": bool(keys_state[keymap[_prefix + 'up']]),
        "left": bool(keys_state[keymap[_prefix + 'left']]),
        "right": bool(keys_state[keymap[_prefix + 'right']]),
    }
    net_send_to_peer("INPUT:" + json.dumps(inp))

def draw_client_fight_view(mouse_pos):
    """客户端：只绘制战斗画面（不跑任何本地逻辑）。"""
    global screen, camera_x
    # 相机跟随本机角色：前方 : 后方 = 2 : 1（相机可以活动时）
    _cam_tx = None
    _cam_face = 'right'
    if network_is_1p and len(fight_chars) >= 1:
        _cam_tx = char1_pos[0]
        _cam_face = fight_chars[0].facing
    elif (not network_is_1p) and len(fight_chars) >= 2:
        _cam_tx = char2_pos[0]
        _cam_face = fight_chars[1].facing
    if _cam_tx is None:
        if len(fight_chars) >= 2:
            _cam_tx = (char1_pos[0] + char2_pos[0]) / 2
        elif len(fight_chars) >= 1:
            _cam_tx = char1_pos[0]
        else:
            _cam_tx = W // 2
    if MAP_W > W:
        if _cam_face == 'right':
            _cam_screen_x = W // 3
        else:
            _cam_screen_x = W * 2 // 3
    else:
        _cam_screen_x = W // 2
    _target_cam = max(0, min(MAP_W - W, _cam_tx - _cam_screen_x))
    # 开场阶段直接对准目标；正常战斗时每帧朝目标缓动，形成平滑跟随。
    if fight_intro_phase != FIGHT_INTRO_DONE:
        camera_x = _target_cam
    else:
        camera_x += (_target_cam - camera_x) * CAMERA_LERP
        if abs(_target_cam - camera_x) < 0.5:
            camera_x = _target_cam
    # 世界内容先画到 1000x600 的离屏 surface，最后按相机偏移 blit
    _cam_real_screen = screen
    _cam_world_surf = pygame.Surface((MAP_W, H))
    screen = _cam_world_surf
    screen.fill((0, 0, 0))
    if catch_map_json is not None:
        draw_map_background(catch_map_json["name"], map_img)
    # 开场印章
    if fight_intro_phase != FIGHT_INTRO_DONE:
        if len(fight_chars) >= 1:
            fight_chars[0].draw("stand", char1_pos, "midbottom")
        if len(fight_chars) == 2:
            fight_chars[1].draw("stand", char2_pos, "midbottom")
        # night 地图：在角色之上、印章之下盖一层 cover.png
        draw_night_cover()
        # 印章画在"当前相机视野中心"，保证在自己屏幕上居中。
        _stamp_cx = int(camera_x) + W // 2
        if fight_intro_phase == FIGHT_INTRO_READY_STAMP and ready_img is not None:
            draw_stamp_animation(ready_img, (_stamp_cx, H // 2), fight_intro_timer / INTRO_STAMP_FRAMES)
        elif fight_intro_phase == FIGHT_INTRO_READY_HOLD and ready_img is not None:
            screen.blit(ready_img, ready_img.get_rect(center=(_stamp_cx, H // 2)))
        elif fight_intro_phase == FIGHT_INTRO_START_STAMP and start_fight_img is not None:
            draw_stamp_animation(start_fight_img, (_stamp_cx, H // 2), fight_intro_timer / INTRO_STAMP_FRAMES)
        elif fight_intro_phase == FIGHT_INTRO_START_HOLD and start_fight_img is not None:
            screen.blit(start_fight_img, start_fight_img.get_rect(center=(_stamp_cx, H // 2)))
        # 世界绘制结束：blit 到真正的屏幕
        screen = _cam_real_screen
        screen.fill((0, 0, 0))
        screen.blit(_cam_world_surf, (-int(camera_x), 0))
        return
    # 脚底光环
    # 局域网下：本机角色蓝、对方红
    _my_idx_c = 0 if network_is_1p else 1
    _aura_0_c = AURA_BLUE if _my_idx_c == 0 else AURA_RED
    _aura_1_c = AURA_RED if _my_idx_c == 0 else AURA_BLUE
    if len(fight_chars) >= 1 and not fight_chars[0].extra_invis_active:
        _p = char1_pos
        screen.blit(_aura_0_c, (_p[0] - 35, _p[1] - 17))
    if len(fight_chars) == 2 and not fight_chars[1].extra_invis_active:
        _p = char2_pos
        screen.blit(_aura_1_c, (_p[0] - 35, _p[1] - 17))
    # 绘制 chr_1 的环绕书：远侧（z<0）在角色图层下面
    for _ci_orb in range(min(len(fight_chars), 2)):
        _c_orb = fight_chars[_ci_orb]
        if _c_orb.name == 'chr_1':
            _pos_orb = char1_pos if _ci_orb == 0 else char2_pos
            draw_chr1_orbit_books(screen, _c_orb, _pos_orb, 'behind')
    # 角色绘制：与主机端分支完全一致——
    # 前摇期间画专用图（chr 技能一 / 技能二；gjl 技能一 / 技能二），
    # 其他状态交给 Character.draw（内部会处理受击倒地/起身/攻击/站立/走/跳）。
    for _idx in range(min(len(fight_chars), 2)):
        _ch = fight_chars[_idx]
        _pos = char1_pos if _idx == 0 else char2_pos
        _drawn = False
        if not _ch.is_hit and not _ch.is_standing_up:
            # wyk 技能二前摇：用 skill_2_use/ 帧序列
            if (_ch.pending_wyk_skill2_launch > 0
                    and _ch.name == 'wyk'):
                _total = max(1, int(_ch.skills['skill_2']['recovery']))
                _use_frame = _ch.get_skill_use_frame(
                    'skill_2_use', _total, _ch.pending_wyk_skill2_launch)
                if _use_frame is not None:
                    _img = _use_frame
                    if _ch.facing == 'left':
                        _img = get_flip(_img)
                    _rect = _img.get_rect(midbottom=(_pos[0], _pos[1]))
                    screen.blit(_img, _rect)
                    _drawn = True
            # wyk 技能二激活：用 skill_2_use/ 的 _数字 帧序列
            elif (_ch.wyk_skill2_active
                    and _ch.name == 'wyk'):
                _total = max(1, WYK_SKILL2_DURATION_FRAMES)
                _use_frame = _ch.get_skill_use_frame(
                    'skill_2_use_after', _total, _ch.wyk_skill2_timer)
                if _use_frame is not None:
                    _img = _use_frame
                    if _ch.facing == 'left':
                        _img = get_flip(_img)
                    _rect = _img.get_rect(midbottom=(_pos[0], _pos[1]))
                    screen.blit(_img, _rect)
                    _drawn = True
            # gjl 技能二前摇：用 skill_2_use/ 帧序列
            elif _ch.gjl_skill2_phase == 1:
                _total = max(1, GJL_SKILL2_WINDUP_FRAMES)
                _use_frame = _ch.get_skill_use_frame(
                    'skill_2_use', _total, _ch.gjl_skill2_timer)
                if _use_frame is not None:
                    _img = _use_frame
                    if _ch.facing == 'left':
                        _img = get_flip(_img)
                    _rect = _img.get_rect(midbottom=(_pos[0], _pos[1]))
                    screen.blit(_img, _rect)
                    _drawn = True
            # gjl 技能一：前摇用 skill_1_use/ 帧序列；突进中间 2 帧显示 trade
            elif _ch.gjl_skill1_phase != 0:
                if _ch.gjl_skill1_phase == 1:
                    _total = max(1, _ch.gjl_skill1_total_frames)
                    _use_frame = _ch.get_skill_use_frame(
                        'skill_1_use', _total, _ch.gjl_skill1_timer)
                    if _use_frame is not None:
                        _img1 = _use_frame
                        if _ch.facing == 'left':
                            _img1 = get_flip(_img1)
                        _r1 = _img1.get_rect(midbottom=(_pos[0], _pos[1]))
                        screen.blit(_img1, _r1)
                        _drawn = True
                else:
                    _t = _ch.gjl_skill1_timer
                    if 2 <= _t < 4 and gjl_trade_img is not None:
                        _img2 = gjl_trade_img
                        if _ch.facing == 'left':
                            _img2 = get_flip(_img2)
                        _r2 = _img2.get_rect(midbottom=(_pos[0], _pos[1]))
                        screen.blit(_img2, _r2)
                        _drawn = True
            # chr 技能一 / 技能二 / wyk 技能一前摇：用帧序列
            elif _ch.pending_book_launch > 0 or _ch.pending_skill2_launch > 0:
                if _ch.pending_book_launch > 0 and _ch.name in ('chr', 'wyk'):
                    _total = max(1, int(_ch.skills['skill_1']['recovery']))
                    _use_frame = _ch.get_skill_use_frame(
                        'skill_1_use', _total, _ch.pending_book_launch)
                    if _use_frame is not None:
                        _img = _use_frame
                        if _ch.facing == 'left':
                            _img = get_flip(_img)
                        _rect = _img.get_rect(midbottom=(_pos[0], _pos[1]))
                        screen.blit(_img, _rect)
                        _drawn = True
                elif _ch.pending_skill2_launch > 0 and _ch.name == 'chr':
                    _total = max(1, int(_ch.skills['skill_2']['recovery']))
                    _use_frame = _ch.get_skill_use_frame(
                        'skill_2_use', _total, _ch.pending_skill2_launch)
                    if _use_frame is not None:
                        _img = _use_frame
                        if _ch.facing == 'left':
                            _img = get_flip(_img)
                        _rect = _img.get_rect(midbottom=(_pos[0], _pos[1]))
                        screen.blit(_img, _rect)
                        _drawn = True
        if not _drawn:
            _ch.draw(_ch.active_anim, _pos, "midbottom")
    # 绘制 chr_1 的环绕书：近侧（z>=0）在角色图层上面
    for _ci_orb in range(min(len(fight_chars), 2)):
        _c_orb = fight_chars[_ci_orb]
        if _c_orb.name == 'chr_1':
            _pos_orb = char1_pos if _ci_orb == 0 else char2_pos
            draw_chr1_orbit_books(screen, _c_orb, _pos_orb, 'front')
    # 投射物
    for proj in book_projectiles:
        if chr_book_img is not None:
            img = chr_book_img
            if proj['facing'] == 'left':
                img = get_flip(img)
            screen.blit(img, img.get_rect(center=(int(proj['x']), int(proj['y']))))
    # wyk 技能一的蛋（客户端从 STATE 重建）
    for _egg in wyk_eggs:
        if wyk_egg_img is not None:
            _eimg = wyk_egg_img
            if _egg['vx'] < 0:
                _eimg = get_flip(_eimg)
            _rect = _eimg.get_rect(
                center=(int(_egg['x']), int(_egg['y'])))
            screen.blit(_eimg, _rect)
    # wyk 技能一的蛋碎粒子（客户端本地生成）
    for _ep in wyk_egg_particles[:]:
        _ep[0] += _ep[2]
        _ep[1] += _ep[3]
        _ep[3] += 0.3
        _ep[2] *= 0.97
        _ep[4] -= 1
        if _ep[4] <= 0:
            wyk_egg_particles.remove(_ep)
        else:
            _et = max(0.0, min(1.0, _ep[4] / 50.0))
            _ea = int(255 * _et)
            _esize = _ep[5]
            _er_gb = _ep[6]
            _eps = get_particle_surf(_er_gb[0], _er_gb[1], _er_gb[2],
                                     _esize, _ea)
            screen.blit(_eps, (int(_ep[0] - _esize), int(_ep[1] - _esize)))
    # 技能 2 武器：客户端本地预测位置 + 绘制。
    # 位置每帧自己推进，只在武器首次出现时用主机同步的初始 x，
    # 之后不会再被 STATE 覆盖，保持滑动平滑。
    for wep in skill2_2_projectiles[:]:
        if wep['facing'] == 'right':
            wep['x'] += SKILL2_2_WEAPON_SPEED
        else:
            wep['x'] -= SKILL2_2_WEAPON_SPEED
        if wep['x'] < -2000 or wep['x'] > W + 2000:
            skill2_2_projectiles.remove(wep)
            continue
        if skill2_2_img is not None:
            _img = skill2_2_img
            if wep['facing'] == 'left':
                _img = get_flip(_img)
            _r = _img.get_rect(center=(int(wep['x']), int(wep['y'])))
            screen.blit(_img, _r)
    # gjl 技能二的 code 投射物：位置由主机同步，直接画即可。
    # 图片默认朝右（angle=0 不旋转），按 angle 逆时针旋转。
    if gjl_code_img is not None:
        for _p in gjl_skill2_projectiles:
            _deg = -math.degrees(_p['angle'])
            _rotated = pygame.transform.rotate(gjl_code_img, _deg)
            _r = _rotated.get_rect(
                center=(int(_p['x']), int(_p['y'])))
            screen.blit(_rotated, _r)
    # chr_1 技能一追踪书：位置由主机同步（满 10 秒后渐隐）
    if chr1_book_img is not None:
        for _p in chr1_track_books:
            _deg = -math.degrees(_p['angle'])
            _rotated = pygame.transform.rotate(chr1_book_img, _deg)
            _r = _rotated.get_rect(
                center=(int(_p['x']), int(_p['y'])))
            _age = _p.get('age', 0)
            if _age >= CHR1_BOOK_LIFETIME_FRAMES:
                _t = (_age - CHR1_BOOK_LIFETIME_FRAMES) / max(1, CHR1_BOOK_FADE_FRAMES)
                _a = int(255 * max(0.0, 1.0 - _t))
                if _a <= 0:
                    continue
                _rotated = _rotated.copy()
                _rotated.set_alpha(_a)
            screen.blit(_rotated, _r)
    # chr_1 技能二的旋转散射书：位置由主机同步（满 10 秒后渐隐）
    if chr1_book_img is not None:
        for _p in chr1_skill2_books:
            _deg = -math.degrees(_p['angle'])
            _rotated = pygame.transform.rotate(chr1_book_img, _deg)
            _r = _rotated.get_rect(
                center=(int(_p['x']), int(_p['y'])))
            _age = _p.get('age', 0)
            if _age >= CHR1_BOOK_LIFETIME_FRAMES:
                _t = (_age - CHR1_BOOK_LIFETIME_FRAMES) / max(1, CHR1_BOOK_FADE_FRAMES)
                _a = int(255 * max(0.0, 1.0 - _t))
                if _a <= 0:
                    continue
                _rotated = _rotated.copy()
                _rotated.set_alpha(_a)
            screen.blit(_rotated, _r)
    # wyk 技能二：声波绘制（客户端本地随机震动）
    if wyk_skill2_voice_img is not None:
        for _i_w2 in range(min(len(fight_chars), 2)):
            _ch_w2 = fight_chars[_i_w2]
            if _ch_w2.name != 'wyk' or not _ch_w2.wyk_skill2_active:
                continue
            _p_w2 = char1_pos if _i_w2 == 0 else char2_pos
            _sx_w2 = random.randint(-WYK_SKILL2_VOICE_SHAKE,
                                    WYK_SKILL2_VOICE_SHAKE)
            _sy_w2 = random.randint(-WYK_SKILL2_VOICE_SHAKE,
                                    WYK_SKILL2_VOICE_SHAKE)
            _r_w2 = get_wyk_skill2_voice_rect(_ch_w2, _p_w2,
                                              _sx_w2, _sy_w2)
            if _r_w2 is not None:
                screen.blit(wyk_skill2_voice_img, _r_w2)
    # 爆炸粒子：客户端本地生成，所以每帧都要自己更新粒子的位置和生命。
    # 参数与主机端 FIGHT 分支里的更新循环完全一致，视觉上看起来一样。
    for p in explosion_particles[:]:
        p[0] += p[2]
        p[1] += p[3]
        p[3] += 0.3
        p[2] *= 0.96
        p[4] -= 1
        if p[4] <= 0:
            explosion_particles.remove(p)
        else:
            t = max(0.0, min(1.0, p[4] / 45.0))
            a = int(255 * t)
            size = p[5]
            r, g, b = p[6]
            _ps = get_particle_surf(r, g, b, size, a)
            screen.blit(_ps, (int(p[0] - size), int(p[1] - size)))
    # 隐藏技能：被控玩家身上持续冒白烟（客户端本地生成）
    for _si in range(min(len(fight_chars), 2)):
        _sc = fight_chars[_si]
        if _sc.wyk_hidden_smoke:
            if ui_frame_count % 15 == 0:
                _spos = char1_pos if _si == 0 else char2_pos
                for _ in range(8):
                    _sang = random.uniform(0, math.pi * 2)
                    _sspd = random.uniform(0.5, 2.5)
                    smoke_particles.append([
                        _spos[0] + random.uniform(-20, 20),
                        _spos[1] - 20 + random.uniform(-40, 0),
                        math.cos(_sang) * _sspd,
                        math.sin(_sang) * _sspd - random.uniform(0.5, 2.0),
                        random.randint(30, 60),
                        random.randint(4, 9),
                    ])
    # 白烟：同样本地更新 + 绘制
    for p in smoke_particles[:]:
        p[0] += p[2]
        p[1] += p[3]
        p[3] -= 0.05
        p[2] *= 0.97
        p[3] *= 0.97
        p[4] -= 1
        if p[4] <= 0:
            smoke_particles.remove(p)
        else:
            t = p[4] / 60.0
            a = int(180 * t)
            size = p[5]
            screen.blit(get_smoke_surf(size, a),
                        (int(p[0] - size), int(p[1] - size)))
    # chr_1 环绕红色粒子：生成 + 更新 + 绘制
    for _ci_aura in range(min(len(fight_chars), 2)):
        _c_aura = fight_chars[_ci_aura]
        if _c_aura.name == 'chr_1':
            _pos_aura = char1_pos if _ci_aura == 0 else char2_pos
            if random.random() < 0.5:
                spawn_chr1_aura_particle(_c_aura, _pos_aura)
    update_and_draw_chr1_aura(screen)
    # night 地图：在角色和所有技能特效之上，伤害数字/UI 之下盖一层 cover.png
    draw_night_cover()
    # 伤害数字
    for num in damage_numbers:
        num.draw(screen)
    # 世界绘制结束：按相机偏移 blit 到真正的屏幕
    screen = _cam_real_screen
    screen.fill((0, 0, 0))
    screen.blit(_cam_world_surf, (-int(camera_x), 0))
    # 震屏：调用位置与主机端一致（伤害数字之后，UI 之前），
    # 这样整个战场画面会一起缩放，视觉上与主机完全相同。
    if ko_phase == 0:
        shake.apply()
    # UI（KO 结算时不画 HUD 和技能图标，画面只留角色和血溅）
    if ko_phase == 0:
        if ui_slide_timer >= UI_SLIDE_FRAMES:
            _us = 1.0
        else:
            _us = ui_slide_timer / UI_SLIDE_FRAMES
        _c1 = fight_chars[0] if len(fight_chars) >= 1 else None
        _c2 = fight_chars[1] if len(fight_chars) == 2 else None
        draw_hud(_c1, _c2, _us)
        # 技能图标
        _sp = 70
        _sxl = 45
        _sxr = W - 45
        _yp = H - 50
        _p1k = [key_display_name(keymap['p1_attack']),
                key_display_name(keymap['p1_escape']),
                key_display_name(keymap['p1_skill1']),
                key_display_name(keymap['p1_skill2']),
                key_display_name(keymap['p1_extra'])]
        _p2k = [key_display_name(keymap['p2_attack']),
                key_display_name(keymap['p2_escape']),
                key_display_name(keymap['p2_skill1']),
                key_display_name(keymap['p2_skill2']),
                key_display_name(keymap['p2_extra'])]
        _so = ['normal', 'escape', 'skill_1', 'skill_2', 'extra']
        # 局域网下只显示本机自己的技能，双方都使用 p1 键位
        _lan_icon_pos_c = {
            'normal': (50, H - 50),
            'skill_1': (120, H - 50),
            'skill_2': (190, H - 50),
            'escape': (120, H - 120),
            'extra': (190, H - 120),
        }
        if len(fight_chars) == 2:
            if network_is_1p:
                _c = fight_chars[0]
            else:
                _c = fight_chars[1]
            _my_keys = _p1k  # 局域网下双方都用 p1 键位
            for _i, _sn in enumerate(_so):
                _img = _c.icons.get(_sn)
                if _img is None:
                    continue
                _cx, _cy = _lan_icon_pos_c[_sn]
                _ic = SkillIcon(_img, (_cx, _cy), key_label=_my_keys[_i])
                _cool = _c.skills[_sn]['current_cd'] > 0
                _cds = _c.skills[_sn]['current_cd'] / FPS
                _ot = None
                _oc = (255, 255, 255)
                # 逃脱：只有受击（倒地 / 被击飞）时才亮起可用；
                # 其他状态盖遮罩表示不可用（不算冷却，不显示数字）。
                if (_sn == 'escape'
                        and not (_c.is_hit or _c.is_standing_up)):
                    _cool = True
                    _cds = 0
                # 体力不足：技能不可用，灰色遮罩，不显示冷却数字
                _st_cost = 0
                if _sn == 'escape':
                    _st_cost = ESCAPE_STAMINA_COST
                elif _sn == 'skill_1':
                    if _c.name == 'gjl' and _c.gjl_skill1_uses_left < GJL_SKILL1_STORE_COUNT:
                        _st_cost = 0
                    elif _c.name == 'chr_1' and _c.chr1_skill1_activated:
                        _st_cost = 0
                    else:
                        _st_cost = _c.skill1_stamina_cost
                elif _sn == 'skill_2':
                    if _c.name == 'chr' and _c.skill2_uses_left < SKILL2_THROW_COUNT:
                        _st_cost = 0
                    else:
                        _st_cost = _c.skill2_stamina_cost
                if _st_cost > 0 and _c.stamina < _st_cost:
                    _cool = True
                    _cds = 0
                    _ot = None
                # 技能二的 3/3 计数是 chr 存储技能专属
                if (_sn == 'skill_2' and _c.name == 'chr'
                        and _c.skill2_uses_left > 0 and not _cool):
                    _ot = f"{_c.skill2_uses_left}/{SKILL2_THROW_COUNT}"
                    _oc = (255, 255, 0)
                # gjl 技能一改为存储技能：显示剩余次数
                if (_sn == 'skill_1' and _c.name == 'gjl'
                        and _c.gjl_skill1_uses_left > 0 and not _cool):
                    _ot = f"{_c.gjl_skill1_uses_left}/{GJL_SKILL1_STORE_COUNT}"
                    _oc = (255, 255, 0)
                # chr_1 技能一（存储技能）：激活后才显示剩余本数
                if (_sn == 'skill_1' and _c.name == 'chr_1'
                        and _c.chr1_skill1_activated
                        and _c.chr1_skill1_uses_left > 0 and not _cool):
                    _ot = f"{_c.chr1_skill1_uses_left}/{CHR1_SKILL1_STORE_COUNT}"
                    _oc = (255, 255, 0)
                # (gjl 技能二已改为普通技能，不再显示剩余次数)
                if _sn == 'extra' and _c.extra_invis_active:
                    _ot = f"{_c.extra_invis_timer / FPS:.1f}"
                    _oc = (255, 255, 0)
                _ic.draw(mouse_pos, _cool, _cds, _ot, _oc)
        # 联机客户端：退出房间按钮固定在右下角（技能图标统一在左下角）
        if network_connected and fight_intro_phase == FIGHT_INTRO_DONE:
            _exit_c = (W - 85, H - 50)
            _orig_exit_c = btn_exit_room.rect.center
            btn_exit_room.rect.center = _exit_c
            btn_exit_room.draw(mouse_pos)
            btn_exit_room.rect.center = _orig_exit_c
    # KO 结算遮罩
    if ko_phase != 0 and ko_timer >= KO_ANIM_FRAMES and len(fight_chars) == 2:
        _ov = pygame.Surface((W, H), pygame.SRCALPHA)
        _ov.fill((20, 20, 30, 120))
        screen.blit(_ov, (0, 0))
        # 联机客户端视角：本机角色索引由 network_is_1p 决定
        _my_idx = 0 if network_is_1p else 1
        if ko_loser_index == _my_idx:
            _ko_text = "你失败了"
        else:
            _ko_text = "你胜利了"
        # 与主机端完全一致：金色文字 + 脉冲放大动画
        _ko_pulse = 1.0 + 0.10 * math.sin(ko_timer * 0.15)
        _ko_font = get_font(56)
        if _ko_text == "你失败了":
            _ko_color = (150, 150, 150)
        else:
            _ko_color = (255, 215, 0)
        _ko_txt = _ko_font.render(_ko_text, True, _ko_color)
        _ko_shadow = _ko_font.render(_ko_text, True, (0, 0, 0))
        _ko_w = max(1, int(_ko_txt.get_width() * _ko_pulse))
        _ko_h = max(1, int(_ko_txt.get_height() * _ko_pulse))
        _ko_txt = pygame.transform.smoothscale(_ko_txt, (_ko_w, _ko_h))
        _ko_shadow = pygame.transform.smoothscale(_ko_shadow, (_ko_w, _ko_h))
        _ko_rect = _ko_txt.get_rect(center=(W // 2, H // 2 - 20))
        screen.blit(_ko_shadow, (_ko_rect.x + 3, _ko_rect.y + 3))
        screen.blit(_ko_txt, _ko_rect)
        # 下方 back 按钮：客户端点击后回准备界面
        _orig_c = btn_back.rect.center
        btn_back.rect.center = (W // 2, H - 100)
        btn_back.draw(mouse_pos)
        btn_back.rect.center = _orig_c

    # 联机表情 UI（icon 按钮 + 气泡面板 + 已发出的表情气泡）
    draw_emoji_ui(mouse_pos)

    # 技能 2 屏幕震动：整屏随机偏移（UI 也一起抖动）
    if any_skill2_2_active():
        _shk_surf = screen.copy()
        screen.fill((0, 0, 0))
        _ox = random.randint(-SKILL2_2_SHAKE_OFFSET, SKILL2_2_SHAKE_OFFSET)
        _oy = random.randint(-SKILL2_2_SHAKE_OFFSET, SKILL2_2_SHAKE_OFFSET)
        screen.blit(_shk_surf, (_ox, _oy))

# ---------- 主循环 ----------
running = True
cloud_x = 0
frame_count = 0
while running:
    ui_frame_count += 1

    # 每帧轮询网络消息
    net_poll()
    # 主机等待对方加入时，每帧检查是否有客户端连入。
    # 这段逻辑原来写在事件循环里，只有玩家移动鼠标或按键时
    # 才会被执行，导致主机不动鼠标就永远检测不到客户端连入。
    if STATE == LAN_HOST_WAIT and network_sock is not None and not network_connected:
        try:
            _accepted_sock, _accepted_addr = network_sock.accept()
            _accepted_sock.setblocking(False)
            network_client_sock = _accepted_sock
            network_connected = True
            network_local_ready = False
            network_remote_ready = False
            STATE = LAN_READY
        except BlockingIOError:
            pass
        except Exception as e:
            network_error_msg = f"accept 失败: {e}"
    # 进入准备页后立即交换版本信息（只发一次）
    if network_connected and STATE == LAN_READY and not network_ver_sent:
        try:
            net_send_to_peer(f"VER:{ver}")
            network_ver_sent = True
        except Exception:
            pass
    # 联机时每 0.5 秒发一次 PING 用来测量延迟
    if network_connected:
        network_ping_timer += 1
        if network_ping_timer >= 30:
            network_ping_timer = 0
            try:
                network_ping_sent_ms = pygame.time.get_ticks()
                net_send_to_peer(f"PING:{network_ping_sent_ms}")
            except Exception:
                pass
    # 联机客户端：非 FIGHT 状态时应用一次主机状态，
    # 让 WAIT_HOST / FIGHT_KO / 选角等状态能及时切换到目标状态。
    # （FIGHT 状态下的应用在 FIGHT 分支内部处理）
    if (network_connected and network_role == "client"
            and network_peer_state is not None
            and STATE != FIGHT):
        apply_network_state(network_peer_state)
        network_peer_state = None
    # 对方断开提示倒计时：到 0 时真正切到目标状态
    if network_disconnect_tip_timer > 0:
        network_disconnect_tip_timer -= 1
        if network_disconnect_tip_timer <= 0:
            network_disconnect_tip_timer = 0
            STATE = network_disconnect_tip_target
    # 检测到对方断开：清理 socket，先显示提示，2 秒后再退回原界面
    if network_disconnect_pending:
        _err = network_error_msg
        net_cleanup()
        network_error_msg = _err
        if STATE in (LAN_HOST_WAIT, LAN_CLIENT_INPUT, LAN_READY, WAIT_HOST):
            network_disconnect_tip_target = DUAL_SELECT
        else:
            network_disconnect_tip_target = MENU
        # 提示 2 秒（120 帧）
        network_disconnect_tip_timer = int(2.0 * FPS)
        network_disconnect_pending = False

    mouse_pos = pygame.mouse.get_pos()
    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
            continue
        # 记录按键状态
        if event.type == pygame.KEYDOWN:
            _custom_key_state[event.key] = True
        elif event.type == pygame.KEYUP:
            _custom_key_state[event.key] = False

        # 当用户点击窗口时，强制恢复焦点，解决切屏后键盘没反应的问题
        if event.type == pygame.MOUSEBUTTONDOWN:
            if sys.platform == 'win32':
                try:
                    import ctypes
                    wm_info = pygame.display.get_wm_info()
                    if 'window' in wm_info:
                        hwnd = wm_info['window']
                        ctypes.windll.user32.SetForegroundWindow(hwnd)
                        ctypes.windll.user32.SetFocus(hwnd)
                except Exception:
                    pass

        # 对方断开提示期间，只响应 QUIT，其他输入一律忽略
        if network_disconnect_tip_timer > 0:
            continue

        # 联机等待屏蔽：如果本机在当前阶段只是等待方，跳过所有输入
        _peer_input_blocked = False
        if network_connected:
            if STATE == DUAL_SELECT_1P and not network_is_1p:
                _peer_input_blocked = True
            elif STATE == DUAL_SELECT_2P and network_is_1p:
                _peer_input_blocked = True
            elif STATE == SKILL_SELECT_1P and not network_is_1p:
                _peer_input_blocked = True
            elif STATE == SKILL_SELECT_2P and network_is_1p:
                _peer_input_blocked = True
        if _peer_input_blocked:
            continue

        if STATE == MENU:
            # 注意：主菜单按钮点击后立即 break，让这一帧剩余的鼠标事件
            # 不再被新状态（如 KEYS 页面）的事件处理捕获。
            # 否则同一个 MOUSEBUTTONDOWN 会被 KEYS 页面再次判定，
            # 导致"一进页面就自动选中第一个键位"的 bug。
            if btn_two.clicked(mouse_pos, event):
                STATE = DUAL_SELECT
                training_mode = False
                break
            if btn_single.clicked(mouse_pos, event):
                STATE = SINGLE_SELECT
                break
            if btn_about.clicked(mouse_pos, event):
                STATE = ABOUT
                break
            if btn_keys is not None and btn_keys.clicked(mouse_pos, event):
                STATE = KEYS
                keys_editing_idx = -1
                break

        elif STATE == SINGLE_SELECT:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                for i, btn in enumerate(choose_btns):
                    if btn.rect.collidepoint(mouse_pos):
                        if selected_idx >= 0:
                            choose_btns[selected_idx].selected = False
                            fade_active = True
                            fade_text = choose_list[selected_idx]["show_name"]
                            fade_alpha = 255
                        selected_idx = i
                        choose_btns[i].selected = True
                        slide_center_x = 900
                        slide_active = True
                        btn_ok.active = True
            if btn_back.clicked(mouse_pos, event):
                STATE = MENU
                selected_idx = -1
                btn_ok.active = False
            if btn_ok.clicked(mouse_pos, event):
                if STATE == SINGLE_SELECT and selected_idx >= 0:
                    # 训练模式：1P 是玩家选的角色，2P 自动填充为 chr（携带技能 1）
                    _class1 = get_char_class(choose_list[selected_idx]["name"])
                    _class2 = Chr  # 2P 固定 chr
                    _c1 = _class1(choose_list[selected_idx])
                    _c2 = _class2(choose_list[0])  # choose_list[0] 是 chr
                    # 1P 用玩家实际选的角色名加载图标；2P 固定 chr。
                    # 之前这里两个都写死成 "chr"，导致选了 gjl 也显示 chr 的图标。
                    _name1 = _c1.name
                    for _name, _c in [(_name1, _c1), ("chr", _c2)]:
                        _c.icons['normal'] = hit_img
                        _c.icons['escape'] = escape_img
                        _sp = f"./temp/character/{_name}/skill/skill_1.png"
                        if os.path.exists(_sp):
                            _c.icons['skill_1'] = load_image(_sp)
                        _up = f"./temp/character/{_name}/skill/skill_2.png"
                        if os.path.exists(_up):
                            _c.icons['skill_2'] = load_image(_up)
                    fight_chars = [_c1, _c2]
                    # 训练模式：2P 默认携带技能 1（隐身）
                    for _sk in extra_skill_data:
                        if _sk.get("num", 0) == 1:
                            _icon = extra_skill_icons.get(1)
                            if _icon is not None:
                                _c2.icons['extra'] = _icon
                                _c2.skills['extra']['cd'] = int(round(_sk.get("cd", 0) * FPS))
                                _c2.skills['extra']['recovery'] = _sk.get("recovery", 0)
                                _c2.extra_skill_num = 1
                            break
                    training_mode = True
                    avatar_show.set(choose_btns[selected_idx].img)
                    # 训练模式也进入技能选择流程，但只为 1P 选择
                    if len(extra_skill_data) == 0:
                        STATE = LOADING
                        load_time = 0
                        catch_played = False
                        catch_finished = False
                    else:
                        STATE = SKILL_SELECT_1P
                        skill_select_idx_1p = -1
                        skill_select_idx_2p = -1
                        btn_ok.active = False
                    selected_idx = -1
                    for btn in choose_btns:
                        btn.selected = False

        elif STATE == DUAL_SELECT:
            if btn_back.clicked(mouse_pos, event):
                STATE = MENU
            if btn_go_on.clicked(mouse_pos, event):
                # 本地对战
                STATE = DUAL_SELECT_1P
                selected_idx = -1
                btn_ok.active = False
                for btn in choose_btns:
                    btn.selected = False
                fade_active = False
            # “局域网”下方的两个格子：左创建房间 / 右加入房间
            # 右边格子下方还有一个输入框，用来输入主机 IP
            lan_host_rect = pygame.Rect(150, 360, 220, 70)
            lan_client_rect = pygame.Rect(430, 360, 220, 70)
            lan_port_rect = pygame.Rect(150, 445, 220, 36)
            lan_input_rect = pygame.Rect(430, 445, 220, 36)

            # 鼠标悬浮高亮
            if lan_host_rect.collidepoint(mouse_pos):
                lan_select_hover = "host"
            elif lan_client_rect.collidepoint(mouse_pos):
                lan_select_hover = "client"
            else:
                lan_select_hover = None

            # 鼠标点击：区分点格子 / 点输入框 / 点其他
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if lan_host_rect.collidepoint(mouse_pos):
                    # 创建房间：用端口输入框里的值作为本地监听端口
                    client_input_active = False
                    port_input_active = False
                    network_error_msg = ""
                    _port_str = client_input_port.strip()
                    if not _port_str:
                        _port_str = str(LAN_PORT)
                    _port = None
                    try:
                        _port = int(_port_str)
                        if _port < 1 or _port > 65535:
                            raise ValueError()
                    except ValueError:
                        network_error_msg = f"端口无效：{_port_str}（应为 1-65535 的整数）"
                    if _port is not None:
                        net_cleanup()
                        try:
                            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                            srv.bind(("0.0.0.0", _port))
                            srv.listen(1)
                            srv.setblocking(False)
                            network_sock = srv
                            network_role = "host"
                            host_ip_display = get_local_ip()
                            # 成功监听后把端口保存到 settings.config，下次启动自动填充
                            save_setting("port", str(_port))
                            STATE = LAN_HOST_WAIT
                        except Exception as e:
                            network_error_msg = f"无法监听端口 {_port}: {e}"
                elif lan_client_rect.collidepoint(mouse_pos):
                    # 加入房间：支持 "IP:端口" 或纯 IP（默认端口）
                    client_input_active = False
                    port_input_active = False
                    ip_to_use = client_input_ip.strip()
                    if not ip_to_use:
                        network_error_msg = "请先在下方的输入框里填写主机 IP"
                    else:
                        _conn_port = LAN_PORT
                        if ":" in ip_to_use:
                            _host_part, _port_part = ip_to_use.rsplit(":", 1)
                            try:
                                _conn_port = int(_port_part)
                                ip_to_use = _host_part
                            except ValueError:
                                pass
                        net_cleanup()
                        network_error_msg = ""
                        network_role = "client"
                        _show_connecting_overlay(ip_to_use, _conn_port)
                        try:
                            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            s.settimeout(1.5)
                            s.connect((ip_to_use, _conn_port))
                            s.settimeout(None)
                            s.setblocking(False)
                            network_sock = s
                            network_connected = True
                            network_local_ready = False
                            network_remote_ready = False
                            network_error_msg = ""
                            # 连接成功后把输入的 IP/域名:端口 保存到 settings.config
                            save_setting("client_ip", client_input_ip.strip())
                            STATE = LAN_READY
                        except Exception as e:
                            network_error_msg = f"连接失败: {e}"
                elif lan_input_rect.collidepoint(mouse_pos):
                    # 点 IP 输入框：激活输入
                    client_input_active = True
                    port_input_active = False
                elif lan_port_rect.collidepoint(mouse_pos):
                    # 点端口输入框：激活输入
                    port_input_active = True
                    client_input_active = False
                else:
                    # 点其他地方：取消输入框激活
                    client_input_active = False
                    port_input_active = False

            # 端口输入框激活时接收键盘输入（只允许数字，最多 5 位）
            if port_input_active and event.type == pygame.KEYDOWN:
                if event.key == pygame.K_BACKSPACE:
                    client_input_port = client_input_port[:-1]
                elif event.key == pygame.K_ESCAPE:
                    port_input_active = False
                elif event.key == pygame.K_RETURN:
                    port_input_active = False
                else:
                    _pch = event.unicode
                    if _pch and _pch.isdigit():
                        if len(client_input_port) < 5:
                            client_input_port += _pch

            # IP / 域名输入框激活时接收键盘输入
            # 允许字母、数字、点、冒号、连字符、下划线（用于 frp-cat.com:12345 这种地址）
            if client_input_active and event.type == pygame.KEYDOWN:
                if event.key == pygame.K_BACKSPACE:
                    client_input_ip = client_input_ip[:-1]
                elif event.key == pygame.K_ESCAPE:
                    client_input_active = False
                elif event.key == pygame.K_RETURN:
                    # 回车等效于点击“加入房间”
                    ip_to_use = client_input_ip.strip()
                    if not ip_to_use:
                        network_error_msg = "请先填写主机 IP 或域名"
                    else:
                        client_input_active = False
                        # 支持 "IP:端口" 或 "域名:端口" 格式，未指定端口时用默认端口
                        _conn_port = LAN_PORT
                        if ":" in ip_to_use:
                            _host_part, _port_part = ip_to_use.rsplit(":", 1)
                            try:
                                _conn_port = int(_port_part)
                                ip_to_use = _host_part
                            except ValueError:
                                pass
                        net_cleanup()
                        network_error_msg = ""
                        network_role = "client"
                        _show_connecting_overlay(ip_to_use, _conn_port)
                        try:
                            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            s.settimeout(1.5)
                            s.connect((ip_to_use, _conn_port))
                            s.settimeout(None)
                            s.setblocking(False)
                            network_sock = s
                            network_connected = True
                            network_local_ready = False
                            network_remote_ready = False
                            network_error_msg = ""
                            # 连接成功后把输入的 IP/域名:端口 保存到 settings.config
                            save_setting("client_ip", client_input_ip.strip())
                            STATE = LAN_READY
                        except Exception as e:
                            network_error_msg = f"连接失败: {e}"
                else:
                    ch = event.unicode
                    _valid_host_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.:-_"
                    # 兜底：中文输入法会把 event.unicode 变成全角或空字符串，
                    # 只要 ch 不在合法表里，就用 event.key 物理键位再映射一次。
                    if (not ch) or (ch not in _valid_host_chars):
                        _name = pygame.key.name(event.key)
                        _ch2 = ""
                        if len(_name) == 1 and _name.isalpha():
                            _ch2 = _name
                        else:
                            _key_map = {
                                pygame.K_PERIOD: ".",
                                pygame.K_COLON: ":",
                                pygame.K_SEMICOLON: ":",
                                pygame.K_MINUS: "-",
                                pygame.K_SLASH: "/",
                                pygame.K_BACKSLASH: "/",
                                pygame.K_COMMA: ",",
                                pygame.K_0: "0", pygame.K_1: "1",
                                pygame.K_2: "2", pygame.K_3: "3",
                                pygame.K_4: "4", pygame.K_5: "5",
                                pygame.K_6: "6", pygame.K_7: "7",
                                pygame.K_8: "8", pygame.K_9: "9",
                                pygame.K_KP0: "0", pygame.K_KP1: "1",
                                pygame.K_KP2: "2", pygame.K_KP3: "3",
                                pygame.K_KP4: "4", pygame.K_KP5: "5",
                                pygame.K_KP6: "6", pygame.K_KP7: "7",
                                pygame.K_KP8: "8", pygame.K_KP9: "9",
                                pygame.K_KP_PERIOD: ".",
                                pygame.K_KP_MINUS: "-",
                                pygame.K_KP_DIVIDE: "/",
                            }
                            _ch2 = _key_map.get(event.key, "")
                        if _ch2:
                            ch = _ch2
                    if ch and ch in _valid_host_chars:
                        if len(client_input_ip) < 60:
                            client_input_ip += ch

        elif STATE == DUAL_SELECT_1P:
            # 1P 确认选角之前保留返回按钮；确认之后进入后续页面就没有了
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                for i, btn in enumerate(choose_btns):
                    if btn.rect.collidepoint(mouse_pos):
                        if selected_idx >= 0:
                            choose_btns[selected_idx].selected = False
                        selected_idx = i
                        choose_btns[i].selected = True
                        btn_ok.active = True
            if network_connected:
                # 联机中："退出房间"，断开连接并回双人界面
                if btn_exit_room.clicked(mouse_pos, event):
                    net_cleanup()
                    STATE = DUAL_SELECT
                    selected_idx = -1
                    btn_ok.active = False
                    for btn in choose_btns:
                        btn.selected = False
            else:
                if btn_back.clicked(mouse_pos, event):
                    STATE = DUAL_SELECT
                    selected_idx = -1
                    btn_ok.active = False
                    for btn in choose_btns:
                        btn.selected = False
            if btn_ok.clicked(mouse_pos, event):
                if selected_idx >= 0:
                    dual_selected_1p = choose_list[selected_idx]
                    # 联机时把 1P 选的角色名发送给对方
                    if network_connected:
                        net_send_to_peer(f"CHAR_PICK:{dual_selected_1p['name']}")
                    STATE = DUAL_SELECT_2P
                    selected_idx = -1
                    btn_ok.active = False
                    for btn in choose_btns:
                        btn.selected = False

        elif STATE == DUAL_SELECT_2P:
            # 联机中："退出房间"
            if network_connected and btn_exit_room.clicked(mouse_pos, event):
                net_cleanup()
                STATE = DUAL_SELECT
                selected_idx = -1
                btn_ok.active = False
                for btn in choose_btns:
                    btn.selected = False
                dual_selected_1p = None
                dual_selected_2p = None
            # 本地双人：允许返回上一步
            elif not network_connected and btn_back.clicked(mouse_pos, event):
                STATE = DUAL_SELECT_1P
                selected_idx = -1
                btn_ok.active = False
                for btn in choose_btns:
                    btn.selected = False
                dual_selected_1p = None
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                for i, btn in enumerate(choose_btns):
                    if btn.rect.collidepoint(mouse_pos):
                        if selected_idx >= 0:
                            choose_btns[selected_idx].selected = False
                        selected_idx = i
                        choose_btns[i].selected = True
                        btn_ok.active = True
            if btn_ok.clicked(mouse_pos, event):
                if selected_idx >= 0:
                    dual_selected_2p = choose_list[selected_idx]
                    # 联机时把 2P 选的角色名发送给对方
                    if network_connected:
                        net_send_to_peer(f"CHAR_PICK:{dual_selected_2p['name']}")
                    char_class_1 = get_char_class(dual_selected_1p["name"])
                    char_class_2 = get_char_class(dual_selected_2p["name"])
                    char1 = char_class_1(dual_selected_1p)
                    char2 = char_class_2(dual_selected_2p)
                    # 用角色自己的名字加载图标，避免双方都选 gjl 时
                    # 1P 图标错误地从 chr 文件夹加载。
                    for char in [char1, char2]:
                        name = char.name
                        char.icons['normal'] = hit_img
                        char.icons['escape'] = escape_img
                        skill_path = f"./temp/character/{name}/skill/skill_1.png"
                        if os.path.exists(skill_path):
                            char.icons['skill_1'] = load_image(skill_path)
                        ultimate_path = f"./temp/character/{name}/skill/skill_2.png"
                        if os.path.exists(ultimate_path):
                            char.icons['skill_2'] = load_image(ultimate_path)
                    fight_chars = [char1, char2]
                    avatar_show.set(
                        choose_btns[choose_list.index(dual_selected_1p)].img,
                        choose_btns[choose_list.index(dual_selected_2p)].img
                    )
                    # 进入技能选择流程；若没有额外技能数据则直接进入加载
                    if len(extra_skill_data) == 0:
                        STATE = LOADING
                        load_time = 0
                        catch_played = False
                        catch_finished = False
                    else:
                        STATE = SKILL_SELECT_1P
                        skill_select_idx_1p = -1
                        skill_select_idx_2p = -1
                        btn_ok.active = False
                    selected_idx = -1
                    for btn in choose_btns:
                        btn.selected = False
                    dual_selected_1p = None
                    dual_selected_2p = None

        elif STATE == SKILL_SELECT_1P:
            # 点击技能图标选中
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                for i, sk in enumerate(extra_skill_data):
                    num = sk.get("num", 0)
                    icon = extra_skill_icons.get(num)
                    if icon is None:
                        continue
                    col = i % 4
                    row = i // 4
                    cx = 60 + col * 70
                    cy = 130 + row * 70
                    rect = icon.get_rect(center=(cx, cy))
                    if rect.collidepoint(mouse_pos):
                        skill_select_idx_1p = i
                        btn_ok.active = True
            # 联机中："退出房间"
            if network_connected and btn_exit_room.clicked(mouse_pos, event):
                net_cleanup()
                STATE = DUAL_SELECT
                fight_chars = []
                dual_selected_1p = None
                dual_selected_2p = None
                selected_idx = -1
                btn_ok.active = False
                skill_select_idx_1p = -1
            # 本地双人 / 训练模式：允许返回上一步
            elif not network_connected and btn_back.clicked(mouse_pos, event):
                if training_mode:
                    STATE = SINGLE_SELECT
                    training_mode = False
                    fight_chars = []
                    selected_idx = -1
                    btn_ok.active = False
                else:
                    STATE = DUAL_SELECT_1P
                    fight_chars = []
                    dual_selected_1p = None
                    dual_selected_2p = None
                    selected_idx = -1
                    btn_ok.active = False
                skill_select_idx_1p = -1
            if btn_ok.clicked(mouse_pos, event):
                if skill_select_idx_1p >= 0:
                    sel = extra_skill_data[skill_select_idx_1p]
                    num = sel.get("num", 0)
                    icon = extra_skill_icons.get(num)
                    if icon is not None and len(fight_chars) >= 1:
                        fight_chars[0].icons['extra'] = icon
                        fight_chars[0].skills['extra']['cd'] = int(round(sel.get("cd", 0) * FPS))
                        fight_chars[0].skills['extra']['recovery'] = sel.get("recovery", 0)
                        fight_chars[0].extra_skill_num = num
                    # 联机时把自己的携带技能编号发送给对方
                    if network_connected:
                        net_send_to_peer(f"SKILL_PICK:{num}")
                    # 单人 / 训练模式直接进入加载；双人模式继续选 2P
                    if len(fight_chars) == 1 or training_mode:
                        STATE = LOADING
                        load_time = 0
                        catch_played = False
                        catch_finished = False
                    else:
                        STATE = SKILL_SELECT_2P
                        skill_select_idx_2p = -1
                    skill_select_idx_1p = -1
                    btn_ok.active = False

        elif STATE == SKILL_SELECT_2P:
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                for i, sk in enumerate(extra_skill_data):
                    num = sk.get("num", 0)
                    icon = extra_skill_icons.get(num)
                    if icon is None:
                        continue
                    col = i % 4
                    row = i // 4
                    cx = 60 + col * 70
                    cy = 130 + row * 70
                    rect = icon.get_rect(center=(cx, cy))
                    if rect.collidepoint(mouse_pos):
                        skill_select_idx_2p = i
                        btn_ok.active = True
            # 联机中："退出房间"
            if network_connected and btn_exit_room.clicked(mouse_pos, event):
                net_cleanup()
                STATE = DUAL_SELECT
                fight_chars = []
                dual_selected_1p = None
                dual_selected_2p = None
                selected_idx = -1
                skill_select_idx_1p = -1
                skill_select_idx_2p = -1
                btn_ok.active = False
            # 本地双人：允许返回上一步
            elif not network_connected and btn_back.clicked(mouse_pos, event):
                STATE = SKILL_SELECT_1P
                skill_select_idx_1p = -1
                skill_select_idx_2p = -1
                btn_ok.active = False
                # 清空 1P 之前选的技能，页面会重新展示
                if len(fight_chars) >= 1:
                    fight_chars[0].icons['extra'] = None
                    fight_chars[0].skills['extra']['cd'] = 0
                    fight_chars[0].extra_skill_num = 0
            if btn_ok.clicked(mouse_pos, event):
                if skill_select_idx_2p >= 0:
                    sel = extra_skill_data[skill_select_idx_2p]
                    num = sel.get("num", 0)
                    icon = extra_skill_icons.get(num)
                    if icon is not None and len(fight_chars) >= 2:
                        fight_chars[1].icons['extra'] = icon
                        fight_chars[1].skills['extra']['cd'] = int(round(sel.get("cd", 0) * FPS))
                        fight_chars[1].skills['extra']['recovery'] = sel.get("recovery", 0)
                        fight_chars[1].extra_skill_num = num
                    # 联机时把自己的携带技能编号发送给对方
                    if network_connected:
                        net_send_to_peer(f"SKILL_PICK:{num}")
                    # 联机客户端也进入 LOADING；但客户端不本地抽地图，
                    # 地图由主机同步，客户端等主机状态推进到 SHOW_MAP / FIGHT。
                    STATE = LOADING
                    load_time = 0
                    catch_played = False
                    catch_finished = False
                    skill_select_idx_1p = -1
                    skill_select_idx_2p = -1
                    btn_ok.active = False

        elif STATE == LAN_SELECT:
            # 两个可点击格子：创建房间（左）/ 加入房间（右）
            left_rect = pygame.Rect(80, 200, 280, 180)
            right_rect = pygame.Rect(440, 200, 280, 180)
            if left_rect.collidepoint(mouse_pos):
                lan_select_hover = "host"
            elif right_rect.collidepoint(mouse_pos):
                lan_select_hover = "client"
            else:
                lan_select_hover = None
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if left_rect.collidepoint(mouse_pos):
                    # 创建房间：启动监听
                    net_cleanup()
                    network_error_msg = ""
                    try:
                        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                        srv.bind(("0.0.0.0", LAN_PORT))
                        srv.listen(1)
                        srv.setblocking(False)
                        network_sock = srv
                        network_role = "host"
                        host_ip_display = get_local_ip()
                        save_setting("port", str(LAN_PORT))
                        STATE = LAN_HOST_WAIT
                    except Exception as e:
                        network_error_msg = f"无法监听端口 {LAN_PORT}: {e}"
                elif right_rect.collidepoint(mouse_pos):
                    # 加入房间
                    net_cleanup()
                    network_error_msg = ""
                    client_input_ip = ""
                    network_role = "client"
                    STATE = LAN_CLIENT_INPUT
            if btn_exit_room.clicked(mouse_pos, event):
                net_cleanup()
                STATE = DUAL_SELECT

        elif STATE == LAN_HOST_WAIT:
            # 非阻塞 accept
            if network_sock is not None and not network_connected:
                try:
                    client_sock, addr = network_sock.accept()
                    client_sock.setblocking(False)
                    network_client_sock = client_sock
                    network_connected = True
                    network_local_ready = False
                    network_remote_ready = False
                    STATE = LAN_READY
                except BlockingIOError:
                    pass
                except Exception as e:
                    network_error_msg = f"accept 失败: {e}"
            if btn_exit_room.clicked(mouse_pos, event):
                net_cleanup()
                STATE = DUAL_SELECT

        elif STATE == LAN_CLIENT_INPUT:
            # 文本输入：允许字母、数字、点、冒号、连字符、下划线
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_BACKSPACE:
                    client_input_ip = client_input_ip[:-1]
                elif event.key == pygame.K_ESCAPE:
                    net_cleanup()
                    STATE = LAN_SELECT
                elif event.key == pygame.K_RETURN:
                    # 连接：支持 "IP:端口" 或 "域名:端口"
                    _ip_to_use = client_input_ip.strip()
                    _conn_port = LAN_PORT
                    if ":" in _ip_to_use:
                        _host_part, _port_part = _ip_to_use.rsplit(":", 1)
                        try:
                            _conn_port = int(_port_part)
                            _ip_to_use = _host_part
                        except ValueError:
                            pass
                    _show_connecting_overlay(_ip_to_use, _conn_port)
                    try:
                        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        s.settimeout(1.5)
                        s.connect((_ip_to_use, _conn_port))
                        s.settimeout(None)
                        s.setblocking(False)
                        network_sock = s
                        network_connected = True
                        network_local_ready = False
                        network_remote_ready = False
                        network_error_msg = ""
                        # 连接成功后把输入的 IP/域名:端口 保存到 settings.config
                        save_setting("client_ip", client_input_ip.strip())
                        STATE = LAN_READY
                    except Exception as e:
                        network_error_msg = f"连接失败: {e}"
                else:
                    ch = event.unicode
                    _valid_host_chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.:-_"
                    # 兜底：中文输入法会把 event.unicode 变成全角或空字符串，
                    # 只要 ch 不在合法表里，就用 event.key 物理键位再映射一次。
                    if (not ch) or (ch not in _valid_host_chars):
                        _name = pygame.key.name(event.key)
                        _ch2 = ""
                        if len(_name) == 1 and _name.isalpha():
                            _ch2 = _name
                        else:
                            _key_map = {
                                pygame.K_PERIOD: ".",
                                pygame.K_COLON: ":",
                                pygame.K_SEMICOLON: ":",
                                pygame.K_MINUS: "-",
                                pygame.K_SLASH: "/",
                                pygame.K_BACKSLASH: "/",
                                pygame.K_COMMA: ",",
                                pygame.K_0: "0", pygame.K_1: "1",
                                pygame.K_2: "2", pygame.K_3: "3",
                                pygame.K_4: "4", pygame.K_5: "5",
                                pygame.K_6: "6", pygame.K_7: "7",
                                pygame.K_8: "8", pygame.K_9: "9",
                                pygame.K_KP0: "0", pygame.K_KP1: "1",
                                pygame.K_KP2: "2", pygame.K_KP3: "3",
                                pygame.K_KP4: "4", pygame.K_KP5: "5",
                                pygame.K_KP6: "6", pygame.K_KP7: "7",
                                pygame.K_KP8: "8", pygame.K_KP9: "9",
                                pygame.K_KP_PERIOD: ".",
                                pygame.K_KP_MINUS: "-",
                                pygame.K_KP_DIVIDE: "/",
                            }
                            _ch2 = _key_map.get(event.key, "")
                        if _ch2:
                            ch = _ch2
                    if ch and ch in _valid_host_chars:
                        if len(client_input_ip) < 60:
                            client_input_ip += ch
            if btn_exit_room.clicked(mouse_pos, event):
                net_cleanup()
                STATE = DUAL_SELECT

        elif STATE == LAN_READY:
            # 准备按钮：点击切换自己的准备状态，并把状态同步给对方
            ready_btn_rect = pygame.Rect(280, 420, 240, 60)
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if ready_btn_rect.collidepoint(mouse_pos):
                    # 版本不一致时禁止准备
                    if not network_version_mismatch:
                        network_local_ready = not network_local_ready
                        if network_local_ready:
                            net_send_to_peer("READY")
                        else:
                            net_send_to_peer("UNREADY")
            if btn_exit_room.clicked(mouse_pos, event):
                net_cleanup()
                STATE = DUAL_SELECT
            # 双方都准备好 -> 主机抽签决定谁是 1P，双方都从 1P 选角页开始
            # （客户端通过 IS_1P 消息进入 DUAL_SELECT_1P，主机在此处进入）
            if (network_local_ready and network_remote_ready
                    and not network_version_mismatch
                    and network_role == "host"):
                # 主机随机抽签：True 表示主机是 1P，False 表示客户端是 1P
                network_host_is_1p = (random.random() < 0.5)
                network_is_1p = network_host_is_1p
                try:
                    net_send_to_peer(f"IS_1P:{1 if network_host_is_1p else 0}")
                except Exception:
                    pass
                STATE = DUAL_SELECT_1P
                selected_idx = -1
                btn_ok.active = False
                for btn in choose_btns:
                    btn.selected = False
                network_local_ready = False
                network_remote_ready = False

        elif STATE == ABOUT:
            if btn_back.clicked(mouse_pos, event):
                STATE = MENU

        elif STATE == KEYS:
            if keys_editing_idx >= 0:
                # 正在等待输入：按下任意键就修改，ESC 取消
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        keys_editing_idx = -1
                    else:
                        _action = KEY_ORDER[keys_editing_idx]
                        # 检查这个键是否已经被其他动作占用，避免冲突
                        _conflict = None
                        for _other, _code in keymap.items():
                            if _other != _action and _code == event.key:
                                _conflict = _other
                                break
                        if _conflict is not None:
                            # 冲突：拒绝修改并提示玩家
                            _side = "1P" if _conflict.startswith("p1") else "2P"
                            _name = KEY_ACTION_NAMES.get(_conflict, _conflict)
                            keys_conflict_msg = (
                                f"冲突：{_side} {_name} 已占用该键，无法修改")
                            keys_conflict_timer = 72   # 1.2 秒
                            keys_editing_idx = -1
                        else:
                            keymap[_action] = event.key
                            keys_editing_idx = -1
                            save_keymap(keymap)
            else:
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    for _i in range(len(KEY_ORDER)):
                        _r = get_key_item_rect(_i)
                        if _r.collidepoint(mouse_pos):
                            keys_editing_idx = _i
                            break
                if btn_back.clicked(mouse_pos, event):
                    STATE = MENU

        elif STATE == SOLO_SETTINGS:
            # 滚轮：三个单元（角色 / 携带技能 / 地图）作为一体上下滚动
            if event.type == pygame.MOUSEWHEEL:
                _skill_rows = (len(extra_skill_data) + 5) // 6 if extra_skill_data else 0
                _map_rows = (len(maps_data) + 3) // 4 if maps_data else 0
                _unit1_h = 150
                _unit2_h = 65 + _skill_rows * 60 + 15
                _unit3_h = 35 + _map_rows * 45 + 15
                _total_h = _unit1_h + SOLO_UNIT_GAP + _unit2_h + SOLO_UNIT_GAP + _unit3_h
                _max_scroll = max(0, _total_h - SOLO_VIEW_H)
                solo_settings_scroll += event.y * 30
                solo_settings_scroll = max(-_max_scroll, min(0, solo_settings_scroll))
            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                # ESC 取消，返回战斗（不重置）
                STATE = FIGHT
            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                # 三个单元的绝对 Y 位置（受 solo_settings_scroll 影响，滚轮一滚整体动）
                _skill_rows = (len(extra_skill_data) + 5) // 6 if extra_skill_data else 0
                _map_rows = (len(maps_data) + 3) // 4 if maps_data else 0
                _unit1_h = 150
                _unit2_h = 65 + _skill_rows * 60 + 15
                _y1 = SOLO_VIEW_TOP + solo_settings_scroll
                _y2 = _y1 + _unit1_h + SOLO_UNIT_GAP
                _y3 = _y2 + _unit2_h + SOLO_UNIT_GAP
                # 角色选择（单元1内：标题下 30，按钮高 100）
                for _ci in range(len(char_list)):
                    _rect = pygame.Rect(80 + _ci * 180, _y1 + 30, 160, 100)
                    if _rect.collidepoint(mouse_pos):
                        solo_settings_char_idx = _ci
                # 携带技能（单元2内：标题下 45 开始图标网格，一行 6 个）
                if len(extra_skill_data) > 0:
                    for _si, _sk in enumerate(extra_skill_data):
                        _num = _sk.get("num", 0)
                        _icon = extra_skill_icons.get(_num)
                        if _icon is None:
                            continue
                        _col = _si % 6
                        _row = _si // 6
                        _cx = 80 + _col * 60
                        _cy = _y2 + 65 + _row * 60
                        _rect = _icon.get_rect(center=(_cx, _cy))
                        if _rect.collidepoint(mouse_pos):
                            if solo_settings_skill_num == _num:
                                solo_settings_skill_num = None
                            else:
                                solo_settings_skill_num = _num
                # 地图（单元3内：标题下 35 开始按钮网格，一行 4 个）
                for _mi, _md in enumerate(maps_data):
                    _col = _mi % 4
                    _row = _mi // 4
                    _rect = pygame.Rect(80 + _col * 170, _y3 + 35 + _row * 45, 160, 38)
                    if _rect.collidepoint(mouse_pos):
                        solo_settings_map_idx = _mi
            # 返回按钮：取消
            if btn_back.clicked(mouse_pos, event):
                STATE = FIGHT
            # OK 按钮：临时移到 (690, 560) 检测；绘制里也放在同一位置
            _orig_ok_c = btn_ok.rect.center
            _orig_ok_active = btn_ok.active
            btn_ok.rect.center = (690, 560)
            btn_ok.active = True
            if btn_ok.clicked(mouse_pos, event):
                apply_solo_settings()
                STATE = FIGHT
            btn_ok.rect.center = _orig_ok_c
            btn_ok.active = _orig_ok_active

        elif STATE == FIGHT:
            # 单人 / 训练 / 本地双人：保留返回按钮（中间下方）
            # 联机模式：不显示
            _fight_show_back_ev = (
                len(fight_chars) == 1 or training_mode
                or (not network_connected and len(fight_chars) == 2)
            )
            if _fight_show_back_ev:
                original_center = btn_back.rect.center
                btn_back.rect.center = (W // 2, H - 50)
                if btn_back.clicked(mouse_pos, event):
                    STATE = MENU
                    training_mode = False
                    fight_chars = []
                    char1_pos = [200, 360]
                    char2_pos = [800, 360]
                    for char in char_list:
                        char.jump_phase = 0
                        char.vy = 0
                        char.current_frame = 0
                        char.frame_timer = 0
                        char.is_attacking = False
                        char.attack_phase = None
                        char.is_hit = False
                        char.is_standing_up = False
                        char.hp = char.max_hp
                        char.damage_protection_total = 0.0
                        char.is_invincible = False
                        char.invincible_timer = 0
                        char.needs_ground_snap = False
                    damage_numbers.clear()
                    avatar_show.in_progress = False
                    ko_phase = 0
                    ko_timer = 0
                    ko_loser_index = 0
                    ko_loser_airborne = False
                    ko_bounce_count = 0
                    blood_particles.clear()
                btn_back.rect.center = original_center

            # 单人模式 / 训练模式：右上角按钮（CD 开关 / 设置 / 回满血）
            # 只有在开场动画结束后（UI 已就位）才响应点击
            if (len(fight_chars) == 1 or training_mode) and fight_intro_phase == FIGHT_INTRO_DONE:
                if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if training_mode:
                        # 训练模式：三个按钮（CD / 设置 / 回满血）
                        if TRAINING_CD_TOGGLE_RECT.collidepoint(mouse_pos):
                            cd_enabled = not cd_enabled
                        elif TRAINING_SETTINGS_BTN_RECT.collidepoint(mouse_pos):
                            # 打开设置面板（只为 1P 调整，2P 保持不变）
                            _cur_name = fight_chars[0].name
                            solo_settings_char_idx = 0
                            for _ci, _cd in enumerate(char_list):
                                if _cd.name == _cur_name:
                                    solo_settings_char_idx = _ci
                                    break
                            solo_settings_skill_num = fight_chars[0].extra_skill_num
                            if solo_settings_skill_num == 0:
                                solo_settings_skill_num = None
                            solo_settings_map_idx = 0
                            if catch_map_json is not None:
                                for _mi, _md in enumerate(maps_data):
                                    if _md.get("name") == catch_map_json.get("name"):
                                        solo_settings_map_idx = _mi
                                        break
                            STATE = SOLO_SETTINGS
                        elif TRAINING_HEAL_BTN_RECT.collidepoint(mouse_pos):
                            # 一键回满血 + 复活双方：把双方拉回地面并恢复站立
                            for _c in fight_chars:
                                _c.hp = _c.max_hp
                                _c.stamina = _c.max_stamina
                                _c.stamina_regen_timer = 0
                                _c.is_training_dead = False
                                _c._training_bounce_count = 0
                                _c.is_hit = False
                                _c.is_standing_up = False
                                _c.stand_up_index = 0
                                _c.stand_up_timer = 0
                                _c.stand_up_done = False
                                _c.damage_protection_total = 0.0
                                _c.hit_vx = 0
                                _c.hit_vy = 0
                                _c.jump_phase = 0
                                _c.vy = 0
                                _c.is_attacking = False
                                _c.attack_phase = None
                                _c.attack_type = None
                                _c.attack_anim = None
                                _c.attack_anim_done = False
                                _c.current_frame = 0
                                _c.frame_timer = 0
                                _c.pending_book_launch = 0
                                _c.pending_skill2_launch = 0
                                _c.extra_invis_active = False
                                _c.extra_invis_timer = 0
                                _c.extra_invis_phase = 0
                                _c.needs_ground_snap = True
                    else:
                        # 单人模式：原来的两个按钮
                        if CD_TOGGLE_RECT.collidepoint(mouse_pos):
                            cd_enabled = not cd_enabled
                        elif SOLO_SETTINGS_BTN_RECT.collidepoint(mouse_pos):
                            _cur_name = fight_chars[0].name
                            solo_settings_char_idx = 0
                            for _ci, _cd in enumerate(char_list):
                                if _cd.name == _cur_name:
                                    solo_settings_char_idx = _ci
                                    break
                            solo_settings_skill_num = fight_chars[0].extra_skill_num
                            if solo_settings_skill_num == 0:
                                solo_settings_skill_num = None
                            solo_settings_map_idx = 0
                            if catch_map_json is not None:
                                for _mi, _md in enumerate(maps_data):
                                    if _md.get("name") == catch_map_json.get("name"):
                                        solo_settings_map_idx = _mi
                                        break
                            STATE = SOLO_SETTINGS

            # 联机战斗中：退出房间按钮固定在右下角（技能图标统一在左下角）
            if (network_connected
                    and fight_intro_phase == FIGHT_INTRO_DONE):
                _exit_ev_c = (W - 85, H - 50)
                _orig_exit_ev_c = btn_exit_room.rect.center
                btn_exit_room.rect.center = _exit_ev_c
                if btn_exit_room.clicked(mouse_pos, event):
                    net_cleanup()
                    STATE = DUAL_SELECT
                    fight_chars = []
                    char1_pos = [200, 360]
                    char2_pos = [800, 360]
                    for _cc in char_list:
                        _cc.hp = _cc.max_hp
                        _cc.is_hit = False
                        _cc.is_standing_up = False
                        _cc.damage_protection_total = 0.0
                        _cc.is_invincible = False
                        _cc.invincible_timer = 0
                        _cc.is_stand_up_invincible = False
                        _cc.stand_up_invincible_timer = 0
                        _cc.needs_ground_snap = False
                    damage_numbers.clear()
                    blood_particles.clear()
                    smoke_particles.clear()
                    ko_phase = 0
                    ko_timer = 0
                    ko_loser_index = 0
                    ko_loser_airborne = False
                    ko_bounce_count = 0
                btn_exit_room.rect.center = _orig_exit_ev_c

        # 联机表情：icon 按钮与气泡面板的点击处理
        # KO 结算期间禁止再发送表情
        if (STATE == FIGHT and ko_phase == 0 and network_connected
                and fight_intro_phase == FIGHT_INTRO_DONE
                and emoji_icon_img is not None
                and event.type == pygame.MOUSEBUTTONDOWN
                and event.button == 1):
            _icon_rect_e = get_emoji_icon_rect()
            if _icon_rect_e.collidepoint(mouse_pos):
                emoji_panel_open = not emoji_panel_open
            elif emoji_panel_open:
                _nums_e = get_emoji_num_list()
                for _i_e, _num_e in enumerate(_nums_e):
                    _cr_e = get_emoji_cell_rect(_i_e)
                    if _cr_e is not None and _cr_e.collidepoint(mouse_pos):
                        _my_side_e = "1p" if network_is_1p else "2p"
                        show_emoji(_my_side_e, _num_e)
                        try:
                            net_send_to_peer(
                                f"EMOJI:{_my_side_e}:{_num_e}")
                        except Exception:
                            pass
                        emoji_panel_open = False
                        break

        if STATE == FIGHT_KO:
            # KO 结算阶段：覆盖层出现后返回按钮可点击
            if ko_timer >= KO_ANIM_FRAMES:
                original_center = btn_back.rect.center
                btn_back.rect.center = (W // 2, H - 100)
                if btn_back.clicked(mouse_pos, event):
                    if network_connected:
                        # 联机：谁点 back 谁就退出到准备界面，对对方无影响
                        STATE = LAN_READY
                        network_local_ready = False
                        network_remote_ready = False
                        ko_phase = 0
                        ko_timer = 0
                        network_ko_back_local = False
                        network_ko_back_remote = False
                    else:
                        STATE = MENU
                        fight_chars = []
                        char1_pos = [200, 360]
                        char2_pos = [800, 360]
                        for char in char_list:
                            char.jump_phase = 0
                            char.vy = 0
                            char.current_frame = 0
                            char.frame_timer = 0
                            char.is_attacking = False
                            char.attack_phase = None
                            char.is_hit = False
                            char.is_standing_up = False
                            char.hp = char.max_hp
                            char.damage_protection_total = 0.0
                            char.is_invincible = False
                            char.invincible_timer = 0
                            char.needs_ground_snap = False
                        damage_numbers.clear()
                        avatar_show.in_progress = False
                        ko_phase = 0
                        ko_timer = 0
                        ko_loser_index = 0
                        ko_loser_airborne = False
                        ko_bounce_count = 0
                        blood_particles.clear()
                btn_back.rect.center = original_center

    # ===== 联机流程推进：根据收到的对端消息切换状态 =====
    if network_connected:
        if (STATE == DUAL_SELECT_1P and not network_is_1p
                and network_peer_char_name is not None):
            # 2P 方收到 1P 选的角色名，保存并进入 2P 选角
            for _d in choose_list:
                if _d["name"] == network_peer_char_name:
                    dual_selected_1p = _d
                    break
            network_peer_char_name = None
            STATE = DUAL_SELECT_2P
            selected_idx = -1
            btn_ok.active = False
            for btn in choose_btns:
                btn.selected = False
        elif (STATE == DUAL_SELECT_2P and network_is_1p
                and network_peer_char_name is not None):
            # 1P 方收到 2P 选的角色名，构造 fight_chars 并进入技能选择
            for _d in choose_list:
                if _d["name"] == network_peer_char_name:
                    dual_selected_2p = _d
                    break
            network_peer_char_name = None
            _class1 = get_char_class(dual_selected_1p["name"])
            _class2 = get_char_class(dual_selected_2p["name"])
            _c1 = _class1(dual_selected_1p)
            _c2 = _class2(dual_selected_2p)
            # 用角色实际名字加载图标，不要写死 "chr"/"gjl"，
            # 否则双方都选同一角色时会加载错误目录的图标。
            for _c in [_c1, _c2]:
                _name = _c.name
                _c.icons['normal'] = hit_img
                _c.icons['escape'] = escape_img
                _sp = f"./temp/character/{_name}/skill/skill_1.png"
                if os.path.exists(_sp):
                    _c.icons['skill_1'] = load_image(_sp)
                _up = f"./temp/character/{_name}/skill/skill_2.png"
                if os.path.exists(_up):
                    _c.icons['skill_2'] = load_image(_up)
            fight_chars = [_c1, _c2]
            # 1P 方触发一次双方头像滑入（在随后的 SHOW_MAP 里展示）；
            # 头像下方带身份标签：房主（主机）/ 成员（客户端）。
            try:
                _i1 = next(i for i, d in enumerate(choose_list) if d["name"] == _c1.name)
                _i2 = next(i for i, d in enumerate(choose_list) if d["name"] == _c2.name)
                if network_connected:
                    if network_host_is_1p:
                        _l1, _l2 = "房主", "成员"
                    else:
                        _l1, _l2 = "成员", "房主"
                    avatar_show.set(choose_btns[_i1].img, choose_btns[_i2].img, _l1, _l2)
                else:
                    avatar_show.set(choose_btns[_i1].img, choose_btns[_i2].img)
            except (StopIteration, IndexError):
                pass
            dual_selected_1p = None
            dual_selected_2p = None
            if len(extra_skill_data) == 0:
                STATE = LOADING
                load_time = 0
                catch_played = False
                catch_finished = False
            else:
                STATE = SKILL_SELECT_1P
                skill_select_idx_1p = -1
                skill_select_idx_2p = -1
                btn_ok.active = False
        elif (STATE == SKILL_SELECT_1P and not network_is_1p
                and network_peer_skill_num is not None):
            # 2P 方收到 1P 的技能编号，配置 fight_chars[0] 并进入 2P 选技能
            _num = network_peer_skill_num
            _icon = extra_skill_icons.get(_num)
            _sel = None
            for _s in extra_skill_data:
                if _s.get("num", 0) == _num:
                    _sel = _s
                    break
            if _icon is not None and _sel is not None and len(fight_chars) >= 1:
                fight_chars[0].icons['extra'] = _icon
                fight_chars[0].skills['extra']['cd'] = int(round(_sel.get("cd", 0) * FPS))
                fight_chars[0].skills['extra']['recovery'] = _sel.get("recovery", 0)
                fight_chars[0].extra_skill_num = _num
            network_peer_skill_num = None
            STATE = SKILL_SELECT_2P
            skill_select_idx_1p = -1
            skill_select_idx_2p = -1
            btn_ok.active = False
        elif (STATE == SKILL_SELECT_2P and network_is_1p
                and network_peer_skill_num is not None):
            # 1P 方收到 2P 的技能编号，配置 fight_chars[1] 并进入加载
            _num = network_peer_skill_num
            _icon = extra_skill_icons.get(_num)
            _sel = None
            for _s in extra_skill_data:
                if _s.get("num", 0) == _num:
                    _sel = _s
                    break
            if _icon is not None and _sel is not None and len(fight_chars) >= 2:
                fight_chars[1].icons['extra'] = _icon
                fight_chars[1].skills['extra']['cd'] = int(round(_sel.get("cd", 0) * FPS))
                fight_chars[1].skills['extra']['recovery'] = _sel.get("recovery", 0)
                fight_chars[1].extra_skill_num = _num
            network_peer_skill_num = None
            STATE = LOADING
            load_time = 0
            catch_played = False
            catch_finished = False
            skill_select_idx_1p = -1
            skill_select_idx_2p = -1
            btn_ok.active = False

    # 联机等待画面：如果本机在当前阶段只是等待方，本帧只画等待画面
    _peer_waiting = False
    if network_connected:
        if STATE == DUAL_SELECT_1P and not network_is_1p:
            _peer_waiting = True
        elif STATE == DUAL_SELECT_2P and network_is_1p:
            _peer_waiting = True
        elif STATE == SKILL_SELECT_1P and not network_is_1p:
            _peer_waiting = True
        elif STATE == SKILL_SELECT_2P and network_is_1p:
            _peer_waiting = True

    # 联机表情气泡倒计时：无论处于哪个状态，都往下递减
    if emoji_show_timer > 0:
        emoji_show_timer -= 1
        if emoji_show_timer <= 0:
            emoji_show_side = None
            emoji_show_img = None

    # ---------- 绘制 ----------
    if network_disconnect_tip_timer > 0:
        # 对方断开提示：黑屏 + 红字，倒计时结束自动切回原界面
        screen.fill((0, 0, 0))
        bordered_text("对方已断开连接", (W // 2, H // 2 - 40), "center", size=56)
        _remain = network_disconnect_tip_timer / FPS
        bordered_text(f"{_remain:.1f} 秒后返回", (W // 2, H // 2 + 40), "center", size=28)
    elif _peer_waiting:
        screen.fill((0, 0, 0))
        draw_brick_wall(brick_img)
        bordered_text("等待对方操作……", (400, 300), "center", size=44)
    elif STATE == MENU:
        screen.fill((0, 0, 0))
        screen.blit(bg, (0, 0))
        cloud_rect.x = 800 + cloud_x
        if cloud_rect.right < 0:
            cloud_x = 0
        screen.blit(cloud, cloud_rect)
        cloud_x -= 1
        btn_two.draw(mouse_pos)
        btn_single.draw(mouse_pos)
        btn_about.draw(mouse_pos)
        if btn_keys is not None:
            btn_keys.draw(mouse_pos)

    elif STATE == ABOUT:
        screen.fill((0, 0, 0))
        draw_brick_wall(brick_img)
        btn_back.draw(mouse_pos)
        about_line_height = 50
        about_start_y = 80
        about_font_size = 36
        for i, line in enumerate(about_lines):
            if line.strip() == "":
                continue
            bordered_text(line, (0, about_start_y + i * about_line_height),
                          "topleft", size=about_font_size)
        frame_count += 1
        chr_head_rot = pygame.transform.rotate(chr_head, frame_count * 5 % 360)
        gjl_head_rot = pygame.transform.rotate(gjl_head, -frame_count * 5 % 360)
        screen.blit(chr_head_rot, chr_head_rot.get_rect(center=(600, 400)))
        screen.blit(gjl_head_rot, gjl_head_rot.get_rect(center=(200, 400)))

    elif STATE == KEYS:
        screen.fill((0, 0, 0))
        draw_brick_wall(brick_img)
        btn_back.draw(mouse_pos)
        bordered_text("按键设置", (400, 40), "center", size=32)
        bordered_text("1P", (220, 70), "center", size=20)
        bordered_text("2P", (580, 70), "center", size=20)
        for _i, _act in enumerate(KEY_ORDER):
            _r = get_key_item_rect(_i)
            if _i == keys_editing_idx:
                pygame.draw.rect(screen, (120, 80, 30), _r)
                pygame.draw.rect(screen, (255, 220, 0), _r, 3)
            else:
                pygame.draw.rect(screen, (40, 40, 55), _r)
                pygame.draw.rect(screen, (180, 180, 180), _r, 2)
            bordered_text(KEY_ACTION_NAMES[_act], (_r.x + 90, _r.centery),
                          "center", size=20)
            if _i == keys_editing_idx:
                bordered_text("按任意键...", (_r.x + 240, _r.centery),
                              "center", size=18)
            else:
                bordered_text(key_display_name(keymap[_act]),
                              (_r.x + 240, _r.centery), "center", size=20)
        bordered_text("点击键位后按键盘任意键修改，ESC 取消",
                      (400, 560), "center", size=15)
        bordered_text("2P 的数字键为小键盘按键",
                      (400, 586), "center", size=15)
        # 键位冲突提示（红色，短暂显示）
        if keys_conflict_timer > 0:
            keys_conflict_timer -= 1
            _cf = get_font(18)
            _cs = _cf.render(keys_conflict_msg, True, (255, 80, 80))
            _csh = _cf.render(keys_conflict_msg, True, (0, 0, 0))
            _cr = _cs.get_rect(center=(400, 530))
            for _dx, _dy in ((0, 2), (0, -2), (2, 0), (-2, 0)):
                screen.blit(_csh, (_cr.x + _dx, _cr.y + _dy))
            screen.blit(_cs, _cr)

    elif STATE == SOLO_SETTINGS:
        screen.fill((0, 0, 0))
        draw_brick_wall(brick_img)
        # 顶部固定：页面标题
        bordered_text("单人设置", (400, 25), "center", size=36)
        # 计算三个单元的高度与相对位置
        _skill_rows = (len(extra_skill_data) + 5) // 6 if extra_skill_data else 0
        _map_rows = (len(maps_data) + 3) // 4 if maps_data else 0
        _unit1_h = 150                                            # 角色单元：标题20 + 按钮100 + 边距30
        _unit2_h = 65 + _skill_rows * 60 + 15                     # 技能单元：标题 + 网格 + 边距
        _unit3_h = 35 + _map_rows * 45 + 15                       # 地图单元：标题 + 网格 + 边距
        _y1 = SOLO_VIEW_TOP + solo_settings_scroll
        _y2 = _y1 + _unit1_h + SOLO_UNIT_GAP
        _y3 = _y2 + _unit2_h + SOLO_UNIT_GAP
        # 滚动视口：只有这个矩形内的内容会被画出来
        _viewport = pygame.Rect(0, SOLO_VIEW_TOP, W, SOLO_VIEW_H)
        screen.set_clip(_viewport)

        # ===== 单元 1：角色 =====
        pygame.draw.rect(screen, (25, 25, 35),
                         pygame.Rect(20, _y1 - 5, W - 40, _unit1_h))
        bordered_text("角色", (40, _y1 + 5), "topleft", size=22)
        for _ci in range(len(char_list)):
            _rect = pygame.Rect(80 + _ci * 180, _y1 + 30, 160, 100)
            _selected = (_ci == solo_settings_char_idx)
            _hover = _rect.collidepoint(mouse_pos) and not _selected
            _bg = (60, 100, 160) if _selected else (
                (80, 80, 115) if _hover else (40, 40, 55))
            _bd = (0, 220, 255) if _selected else (
                (220, 220, 255) if _hover else (180, 180, 180))
            pygame.draw.rect(screen, _bg, _rect)
            pygame.draw.rect(screen, _bd, _rect, 3 if _selected else 2)
            _avatar = get_scaled_avatar(choose_btns[_ci].img, _ci, 60)
            screen.blit(_avatar, _avatar.get_rect(center=(_rect.centerx, _rect.y + 35)))
            bordered_text(choose_list[_ci]["show_name"],
                          (_rect.centerx, _rect.y + 80), "center", size=18)

        # ===== 单元 2：携带技能 =====
        pygame.draw.rect(screen, (25, 25, 35),
                         pygame.Rect(20, _y2 - 5, W - 40, _unit2_h))
        bordered_text("携带技能（点击切换，再点一次取消）", (40, _y2 + 5), "topleft", size=18)
        if len(extra_skill_data) == 0:
            bordered_text("（没有可用的携带技能）", (80, _y2 + 45), "topleft", size=18)
        else:
            for _si, _sk in enumerate(extra_skill_data):
                _num = _sk.get("num", 0)
                _icon = extra_skill_icons.get(_num)
                if _icon is None:
                    continue
                _col = _si % 6
                _row = _si // 6
                _cx = 80 + _col * 60
                _cy = _y2 + 65 + _row * 60
                _rect = _icon.get_rect(center=(_cx, _cy))
                _hover = _rect.collidepoint(mouse_pos)
                if solo_settings_skill_num == _num:
                    pygame.draw.rect(screen, (0, 200, 255), _rect.inflate(10, 10), 3)
                elif _hover:
                    pygame.draw.rect(screen, (255, 255, 255), _rect.inflate(10, 10), 2)
                screen.blit(_icon, _rect)

        # ===== 单元 3：地图 =====
        pygame.draw.rect(screen, (25, 25, 35),
                         pygame.Rect(20, _y3 - 5, W - 40, _unit3_h))
        bordered_text("地图", (40, _y3 + 5), "topleft", size=22)
        for _mi, _md in enumerate(maps_data):
            _col = _mi % 4
            _row = _mi // 4
            _rect = pygame.Rect(80 + _col * 170, _y3 + 35 + _row * 45, 160, 38)
            _selected = (_mi == solo_settings_map_idx)
            _hover = _rect.collidepoint(mouse_pos) and not _selected
            _bg = (60, 100, 160) if _selected else (
                (80, 80, 115) if _hover else (40, 40, 55))
            _bd = (0, 220, 255) if _selected else (
                (220, 220, 255) if _hover else (180, 180, 180))
            pygame.draw.rect(screen, _bg, _rect)
            pygame.draw.rect(screen, _bd, _rect, 3 if _selected else 2)
            bordered_text(_md.get("show_name", _md.get("name", "")),
                          _rect.center, "center", size=18)

        screen.set_clip(None)
        # 视口边框与滚动提示
        pygame.draw.rect(screen, (120, 120, 140), _viewport, 2)
        if (_unit1_h + SOLO_UNIT_GAP + _unit2_h + SOLO_UNIT_GAP + _unit3_h) > SOLO_VIEW_H:
            bordered_text("滚轮上下滑动", (W // 2, SOLO_VIEW_BOTTOM - 15), "center", size=14)
        # 底部固定按钮
        btn_back.draw(mouse_pos)
        _orig_ok_c = btn_ok.rect.center
        _orig_ok_active = btn_ok.active
        btn_ok.rect.center = (690, 565)
        btn_ok.active = True
        btn_ok.draw(mouse_pos)
        btn_ok.rect.center = _orig_ok_c
        btn_ok.active = _orig_ok_active
        bordered_text("点 OK 应用并重开战斗，点返回或按 ESC 取消",
                      (400, 580), "center", size=15)

    elif STATE == LAN_SELECT:
        screen.fill((0, 0, 0))
        draw_brick_wall(brick_img)
        btn_exit_room.draw(mouse_pos)
        bordered_text("局域网联机", (400, 60), "center")
        left_rect = pygame.Rect(80, 200, 280, 180)
        right_rect = pygame.Rect(440, 200, 280, 180)
        for rect, key, label in (
                (left_rect, "host", "创建房间"),
                (right_rect, "client", "加入房间")):
            if lan_select_hover == key:
                pygame.draw.rect(screen, (90, 90, 110), rect)
            else:
                pygame.draw.rect(screen, (40, 40, 55), rect)
            pygame.draw.rect(screen, (220, 220, 220), rect, 3)
            bordered_text(label, rect.center, "center", size=36)
        if network_error_msg:
            bordered_text(network_error_msg, (400, 470), "center", size=22)

    elif STATE == LAN_HOST_WAIT:
        screen.fill((0, 0, 0))
        draw_brick_wall(brick_img)
        btn_exit_room.draw(mouse_pos)
        bordered_text("创建房间", (400, 60), "center")
        bordered_text("请把下面的 IP 和端口告诉对方：", (400, 150), "center", size=24)
        _listen_port = client_input_port.strip() or str(LAN_PORT)
        bordered_text(f"{host_ip_display}:{_listen_port}", (400, 210), "center", size=40)
        bordered_text("等待对方加入……", (400, 340), "center", size=28)
        bordered_text("（对方需在同一局域网内）", (400, 390), "center", size=18)
        if network_error_msg:
            bordered_text(network_error_msg, (400, 500), "center", size=22)

    elif STATE == LAN_CLIENT_INPUT:
        screen.fill((0, 0, 0))
        draw_brick_wall(brick_img)
        btn_exit_room.draw(mouse_pos)
        bordered_text("加入房间", (400, 60), "center")
        bordered_text("请输入主机的 IP 或域名:端口：", (400, 160), "center", size=24)
        input_rect = pygame.Rect(150, 220, 500, 60)
        pygame.draw.rect(screen, (20, 20, 20), input_rect)
        pygame.draw.rect(screen, (200, 200, 200), input_rect, 2)
        ip_show = client_input_ip if client_input_ip else "例如 192.168.1.5 或 frp-cat.com:12345"
        ip_color = (255, 255, 255) if client_input_ip else (120, 120, 120)
        _f = get_font(32)
        _surf = _f.render(ip_show, True, ip_color)
        _rect = _surf.get_rect(midleft=(input_rect.x + 15, input_rect.centery))
        screen.blit(_surf, _rect)
        # 光标闪烁（简单的竖线）
        if (ui_frame_count // 30) % 2 == 0:
            cx = _rect.right + 3
            pygame.draw.line(screen, (255, 255, 255), (cx, input_rect.y + 10),
                             (cx, input_rect.bottom - 10), 2)
        bordered_text("按回车键连接，ESC 或返回键取消", (400, 330), "center", size=22)
        if network_error_msg:
            bordered_text(network_error_msg, (400, 430), "center", size=22)

    elif STATE == LAN_READY:
        screen.fill((0, 0, 0))
        draw_brick_wall(brick_img)
        btn_exit_room.draw(mouse_pos)
        bordered_text("准备页面", (400, 80), "center")
        # 双方准备状态：固定 1P 在上，2P 在下；已准备绿色，未准备红色
        if network_role == "host":
            p1_label = "1P（房主）"
            p2_label = "2P（对方）"
            p1_ready = network_local_ready
            p2_ready = network_remote_ready
        else:
            p1_label = "1P（房主）"
            p2_label = "2P（加入者）"
            p1_ready = network_remote_ready
            p2_ready = network_local_ready
        _ready_font = get_font(28)
        for _label, _ready, _cy in ((p1_label, p1_ready, 200), (p2_label, p2_ready, 270)):
            _prefix = _label + "："
            _state_str = "已准备" if _ready else "未准备"
            _state_color = (0, 220, 0) if _ready else (255, 60, 60)
            _pfx_surf = _ready_font.render(_prefix, True, (255, 255, 255))
            _st_surf = _ready_font.render(_state_str, True, _state_color)
            _pfx_shadow = _ready_font.render(_prefix, True, (0, 0, 0))
            _st_shadow = _ready_font.render(_state_str, True, (0, 0, 0))
            _total_w = _pfx_surf.get_width() + _st_surf.get_width()
            _sx = 400 - _total_w // 2
            _sy = _cy - _pfx_surf.get_height() // 2
            for _dx, _dy in ((0, 2), (0, -2), (2, 0), (-2, 0)):
                screen.blit(_pfx_shadow, (_sx + _dx, _sy + _dy))
                screen.blit(_st_shadow, (_sx + _pfx_surf.get_width() + _dx, _sy + _dy))
            screen.blit(_pfx_surf, (_sx, _sy))
            screen.blit(_st_surf, (_sx + _pfx_surf.get_width(), _sy))
        # 版本信息：双方交换后显示；不一致时提示，并把迭代次数小的一方版本号标红
        if network_peer_ver is not None:
            if network_role == "host":
                _p1_ver = ver
                _p2_ver = network_peer_ver
            else:
                _p1_ver = network_peer_ver
                _p2_ver = ver
            if network_version_mismatch:
                bordered_text("版本不一致，无法联机", (W // 2, 320), "center", size=26)
            # 版本低的一方显示红色
            _cmp = compare_version(_p1_ver or "", _p2_ver or "")
            _p1_color = (255, 60, 60) if _cmp < 0 else (255, 255, 255)
            _p2_color = (255, 60, 60) if _cmp > 0 else (255, 255, 255)
            _vf = get_font(22)
            for _p_label, _p_ver, _p_color, _p_cy in (
                    ("1P：", _p1_ver, _p1_color, 358),
                    ("2P：", _p2_ver, _p2_color, 388)):
                _pfx_surf = _vf.render(_p_label, True, (255, 255, 255))
                _ver_surf = _vf.render(_p_ver, True, _p_color)
                _pfx_shadow = _vf.render(_p_label, True, (0, 0, 0))
                _ver_shadow = _vf.render(_p_ver, True, (0, 0, 0))
                _tw = _pfx_surf.get_width() + _ver_surf.get_width()
                _sx = W // 2 - _tw // 2
                _sy = _p_cy - _pfx_surf.get_height() // 2
                for _dx, _dy in ((0, 2), (0, -2), (2, 0), (-2, 0)):
                    screen.blit(_pfx_shadow, (_sx + _dx, _sy + _dy))
                    screen.blit(_ver_shadow, (_sx + _pfx_surf.get_width() + _dx, _sy + _dy))
                screen.blit(_pfx_surf, (_sx, _sy))
                screen.blit(_ver_surf, (_sx + _pfx_surf.get_width(), _sy))
        # 准备按钮
        ready_btn_rect = pygame.Rect(280, 420, 240, 60)
        if network_version_mismatch:
            pygame.draw.rect(screen, (60, 60, 60), ready_btn_rect)
            _label = "版本不一致"
        elif network_local_ready:
            pygame.draw.rect(screen, (30, 140, 30), ready_btn_rect)
            _label = "取消准备"
        else:
            pygame.draw.rect(screen, (140, 140, 40), ready_btn_rect)
            _label = "准备"
        pygame.draw.rect(screen, (255, 255, 255), ready_btn_rect, 2)
        bordered_text(_label, ready_btn_rect.center, "center", size=30)
        bordered_text("双方都准备后将进入选角", (400, 510), "center", size=20)
        if network_error_msg:
            bordered_text(network_error_msg, (400, 550), "center", size=20)

    elif STATE == WAIT_HOST:
        screen.fill((0, 0, 0))
        draw_brick_wall(brick_img)
        bordered_text("等待主机开始战斗……", (400, 300), "center", size=40)
        bordered_text("（主机正在加载地图）", (400, 360), "center", size=22)

    elif STATE == SKILL_SELECT_1P:
        screen.fill((0, 0, 0))
        draw_brick_wall(brick_img)
        btn_ok.draw(mouse_pos)
        # 联机显示"退出房间"，本地双人 / 训练模式显示 BACK
        if network_connected:
            btn_exit_room.draw(mouse_pos)
        else:
            btn_back.draw(mouse_pos)
        bordered_text("1P 选择携带技能", (400, 40), "center")
        for i, sk in enumerate(extra_skill_data):
            num = sk.get("num", 0)
            icon = extra_skill_icons.get(num)
            if icon is None:
                continue
            col = i % 4
            row = i // 4
            cx = 60 + col * 70
            cy = 130 + row * 70
            rect = icon.get_rect(center=(cx, cy))
            # 悬浮检测：鼠标在图标上时轻微上浮并放大，带一点呼吸波动
            hover = rect.collidepoint(mouse_pos)
            if hover:
                bob = int(3 * math.sin(ui_frame_count * 0.15))
                offset_y = -4 + bob
                new_w = max(1, int(rect.width * 1.12))
                new_h = max(1, int(rect.height * 1.12))
                draw_icon = pygame.transform.smoothscale(icon, (new_w, new_h))
                draw_rect = draw_icon.get_rect(center=(cx, cy + offset_y))
            else:
                draw_icon = icon
                draw_rect = rect
            # 仅在选中时绘制高亮边框；未选中不画任何边框
            if skill_select_idx_1p == i:
                bg_rect = draw_rect.inflate(10, 10)
                pygame.draw.rect(screen, (0, 200, 255), bg_rect, 3)
            screen.blit(draw_icon, draw_rect)
        # 右侧信息面板不加黑底、不加边框
        panel_rect = pygame.Rect(400, 90, 380, 400)
        if 0 <= skill_select_idx_1p < len(extra_skill_data):
            sel = extra_skill_data[skill_select_idx_1p]
            name = sel.get("name", "") or "(未命名)"
            desc = sel.get("description", "") or "(无描述)"
            cd = sel.get("cd", 0)
            rec = sel.get("recovery", 0)
            # 名称显示在左侧，放大的技能图标显示在名称右侧、同一行、垂直居中
            # 图标不透明度 70%
            tx = panel_rect.x + 20
            ty = panel_rect.y + 20
            bordered_text(name, (tx, ty), "topleft", size=40)
            bordered_text(f"冷却: {cd} 秒", (tx, panel_rect.y + 90), "topleft", size=22)
            bordered_text(f"前摇: {rec} 帧", (tx, panel_rect.y + 125), "topleft", size=22)
            draw_wrapped_text(desc, tx, panel_rect.y + 170, panel_rect.width - 40, size=18, line_height=24)

    elif STATE == SKILL_SELECT_2P:
        screen.fill((0, 0, 0))
        draw_brick_wall(brick_img)
        btn_ok.draw(mouse_pos)
        # 联机显示"退出房间"，本地双人显示 BACK
        if network_connected:
            btn_exit_room.draw(mouse_pos)
        else:
            btn_back.draw(mouse_pos)
        bordered_text("2P 选择携带技能", (400, 40), "center")
        for i, sk in enumerate(extra_skill_data):
            num = sk.get("num", 0)
            icon = extra_skill_icons.get(num)
            if icon is None:
                continue
            col = i % 4
            row = i // 4
            cx = 60 + col * 70
            cy = 130 + row * 70
            rect = icon.get_rect(center=(cx, cy))
            # 悬浮检测：鼠标在图标上时轻微上浮并放大，带一点呼吸波动
            hover = rect.collidepoint(mouse_pos)
            if hover:
                bob = int(3 * math.sin(ui_frame_count * 0.15))
                offset_y = -4 + bob
                new_w = max(1, int(rect.width * 1.12))
                new_h = max(1, int(rect.height * 1.12))
                draw_icon = pygame.transform.smoothscale(icon, (new_w, new_h))
                draw_rect = draw_icon.get_rect(center=(cx, cy + offset_y))
            else:
                draw_icon = icon
                draw_rect = rect
            # 仅在选中时绘制高亮边框；未选中不画任何边框
            if skill_select_idx_2p == i:
                bg_rect = draw_rect.inflate(10, 10)
                pygame.draw.rect(screen, (255, 80, 80), bg_rect, 3)
            screen.blit(draw_icon, draw_rect)
        # 右侧信息面板不加黑底、不加边框
        panel_rect = pygame.Rect(400, 90, 380, 400)
        if 0 <= skill_select_idx_2p < len(extra_skill_data):
            sel = extra_skill_data[skill_select_idx_2p]
            name = sel.get("name", "") or "(未命名)"
            desc = sel.get("description", "") or "(无描述)"
            cd = sel.get("cd", 0)
            rec = sel.get("recovery", 0)
            # 名称显示在左侧，放大的技能图标显示在名称右侧、同一行、垂直居中
            # 图标不透明度 70%
            tx = panel_rect.x + 20
            ty = panel_rect.y + 20
            bordered_text(name, (tx, ty), "topleft", size=40)
            bordered_text(f"冷却: {cd} 秒", (tx, panel_rect.y + 90), "topleft", size=22)
            bordered_text(f"前摇: {rec} 帧", (tx, panel_rect.y + 125), "topleft", size=22)
            draw_wrapped_text(desc, tx, panel_rect.y + 170, panel_rect.width - 40, size=18, line_height=24)

    elif STATE == DUAL_SELECT:
        screen.fill((0, 0, 0))
        draw_brick_wall(brick_img)
        btn_back.draw(mouse_pos)
        screen.blit(cover, (0, 0))
        # “本地”紧贴在 back 按钮（中心 (60, 35)）下方，x 与“局域网”都为 10。
        bordered_text("本地", (10, 70), "topleft")
        bordered_text("局域网", (10, 300), "topleft")
        btn_go_on.draw(mouse_pos)
        # “局域网”下方的两个格子：左创建房间 / 右加入房间
        lan_host_rect = pygame.Rect(150, 360, 220, 70)
        lan_client_rect = pygame.Rect(430, 360, 220, 70)
        for _rect, _key, _label in (
                (lan_host_rect, "host", "创建房间"),
                (lan_client_rect, "client", "加入房间")):
            if lan_select_hover == _key:
                pygame.draw.rect(screen, (90, 90, 110), _rect)
            else:
                pygame.draw.rect(screen, (40, 40, 55), _rect)
            pygame.draw.rect(screen, (220, 220, 220), _rect, 3)
            bordered_text(_label, _rect.center, "center", size=26)
        # 左边格子下方的端口输入框（创建房间用）
        lan_port_rect = pygame.Rect(150, 445, 220, 36)
        pygame.draw.rect(screen, (20, 20, 20), lan_port_rect)
        if port_input_active:
            _pbd_color = (255, 220, 0)
        else:
            _pbd_color = (180, 180, 180)
        pygame.draw.rect(screen, _pbd_color, lan_port_rect, 2)
        if client_input_port:
            _port_text = "端口 " + client_input_port
            _port_color = (255, 255, 255)
        elif port_input_active:
            _port_text = ""
            _port_color = (255, 255, 255)
        else:
            _port_text = "端口 12345"
            _port_color = (120, 120, 120)
        _pf = get_font(20)
        _psurf = _pf.render(_port_text, True, _port_color)
        _prect_txt = _psurf.get_rect(midleft=(lan_port_rect.x + 10, lan_port_rect.centery))
        screen.blit(_psurf, _prect_txt)
        if port_input_active and (ui_frame_count // 30) % 2 == 0:
            _pcx = _prect_txt.right + 3
            pygame.draw.line(screen, (255, 255, 0),
                             (_pcx, lan_port_rect.y + 8),
                             (_pcx, lan_port_rect.bottom - 8), 2)
        # 右边格子下方的 IP 输入框
        lan_input_rect = pygame.Rect(430, 445, 220, 36)
        pygame.draw.rect(screen, (20, 20, 20), lan_input_rect)
        if client_input_active:
            _bd_color = (255, 220, 0)
        else:
            _bd_color = (180, 180, 180)
        pygame.draw.rect(screen, _bd_color, lan_input_rect, 2)
        if client_input_ip:
            _ip_text = client_input_ip
            _ip_color = (255, 255, 255)
        elif client_input_active:
            # 输入框已激活但还没输入任何内容：隐藏占位文字，
            # 这样光标会停在输入框起始位置，不会跑到框外。
            _ip_text = ""
            _ip_color = (255, 255, 255)
        else:
            _ip_text = "输入 IP 或域名:端口"
            _ip_color = (120, 120, 120)
        _f = get_font(20)
        _surf = _f.render(_ip_text, True, _ip_color)
        _rect_txt = _surf.get_rect(midleft=(lan_input_rect.x + 10, lan_input_rect.centery))
        screen.blit(_surf, _rect_txt)
        # 输入框激活时在文字末尾画闪烁光标
        if client_input_active and (ui_frame_count // 30) % 2 == 0:
            _cx = _rect_txt.right + 3
            pygame.draw.line(screen, (255, 255, 0),
                             (_cx, lan_input_rect.y + 8),
                             (_cx, lan_input_rect.bottom - 8), 2)
        bordered_text("远程可使用内网穿透联机",
                      (400, 590), "center", size=16)
        if network_error_msg:
            bordered_text(network_error_msg, (400, 555), "center", size=18)

    elif STATE == SINGLE_SELECT:
        screen.fill((0, 0, 0))
        draw_brick_wall(brick_img)
        btn_ok.draw(mouse_pos)
        for btn in choose_btns:
            btn.draw(mouse_pos)
        btn_back.draw(mouse_pos)
        bordered_text("选择角色", (400, 50), "center")
        if selected_idx >= 0:
            char_list[selected_idx].draw_animation("stand", CHAR_PREVIEW_TOPLEFT)
            select_border.update("stand")
            br = choose_btns[selected_idx].rect
            select_border.draw("stand", (br.x, br.y))
            if slide_active:
                slide_center_x -= 40
                if slide_center_x <= CHAR_PREVIEW_AXIS_X:
                    slide_center_x = CHAR_PREVIEW_AXIS_X
                    slide_active = False
            if fade_active:
                fade_alpha -= 20
                if fade_alpha <= 0:
                    fade_alpha = 0
                    fade_active = False
                bordered_text(fade_text, (CHAR_PREVIEW_AXIS_X, CHAR_NAME_CENTER[1]),
                              "center", size=CHAR_NAME_SIZE, alpha=fade_alpha)
            bordered_text(choose_list[selected_idx]["show_name"], (slide_center_x, CHAR_NAME_CENTER[1]),
                          "center", size=CHAR_NAME_SIZE)

    elif STATE == DUAL_SELECT_1P:
        screen.fill((0, 0, 0))
        draw_brick_wall(brick_img)
        btn_ok.draw(mouse_pos)
        for btn in choose_btns:
            btn.draw(mouse_pos)
        # 联机显示"退出房间"，否则显示 BACK
        if network_connected:
            btn_exit_room.draw(mouse_pos)
        else:
            btn_back.draw(mouse_pos)
        bordered_text("1P 选角", (400, 50), "center")
        if selected_idx >= 0:
            char_list[selected_idx].draw_animation("stand", CHAR_PREVIEW_TOPLEFT)
            select_border.update("stand")
            br = choose_btns[selected_idx].rect
            select_border.draw("stand", (br.x, br.y))
            bordered_text(choose_list[selected_idx]["show_name"], CHAR_NAME_CENTER,
                          "center", size=CHAR_NAME_SIZE)

    elif STATE == DUAL_SELECT_2P:
        screen.fill((0, 0, 0))
        draw_brick_wall(brick_img)
        btn_ok.draw(mouse_pos)
        for btn in choose_btns:
            btn.draw(mouse_pos)
        # 联机显示"退出房间"，本地双人显示 BACK
        if network_connected:
            btn_exit_room.draw(mouse_pos)
        else:
            btn_back.draw(mouse_pos)
        bordered_text("2P 选角", (400, 50), "center")
        if selected_idx >= 0:
            char_list[selected_idx].draw_animation("stand", CHAR_PREVIEW_TOPLEFT)
            select_border.update("stand")
            br = choose_btns[selected_idx].rect
            select_border.draw("stand", (br.x, br.y))
            bordered_text(choose_list[selected_idx]["show_name"], CHAR_NAME_CENTER,
                          "center", size=CHAR_NAME_SIZE)

    elif STATE == LOADING:
        if network_connected and network_role == "host":
            # 每帧都发 STATE，让客户端攻击动画更流畅
            try:
                net_send_to_peer("STATE:" + json.dumps(build_network_state(), separators=(',', ':')))
            except Exception:
                pass
        for i in range(min(load_time, 221)):
            r, c = divmod(i, 17)
            screen.blit(load_brick, (c * 50, r * 50))
        if load_time >= 221:
            bordered_text("抽到的地图为：", (400, 100), "center")
            # 联机客户端不本地抽地图，地图由主机同步；
            # 客户端的 LOADING 停在最后一帧，等主机推进到 SHOW_MAP。
            if not (network_connected and network_role == "client"):
                if not catch_played:
                    if audio_available:
                        try:
                            pygame.mixer.Sound("./temp/load/catch.wav").play()
                        except pygame.error:
                            pass
                    catch_played = True
                if not catch_finished:
                    if not audio_available or not pygame.mixer.get_busy():
                        if audio_available:
                            try:
                                pygame.mixer.Sound("./temp/load/got.wav").play()
                            except pygame.error:
                                pass
                        catch_finished = True
                        # 防止连续抽到同一张地图：如果地图数量 > 1，
                        # 就把上一次抽到的那张排除掉，从剩下的里随机选。
                        _candidates = maps_data
                        if len(maps_data) > 1 and last_catch_map_name is not None:
                            _filtered = [m for m in maps_data
                                         if m.get("name") != last_catch_map_name]
                            if _filtered:
                                _candidates = _filtered
                        catch_map_json = random.choice(_candidates)
                        last_catch_map_name = catch_map_json.get("name", "")
                        catch_map_show_name = catch_map_json["show_name"]
                        STATE = SHOW_MAP
                        load_time = 0
                        wait_time = 100 + random.randint(-20, 60)
        if not (network_connected and network_role == "client" and load_time >= 221):
            load_time += 6

    elif STATE == SHOW_MAP:
        if network_connected and network_role == "host":
            # 每帧都发 STATE，让客户端攻击动画更流畅
            try:
                net_send_to_peer("STATE:" + json.dumps(build_network_state(), separators=(',', ':')))
            except Exception:
                pass
        screen.fill((0, 0, 0))
        draw_brick_wall(load_brick)
        avatar_show.draw()
        bordered_text("抽到的地图为：", (400, 100), "center")
        bordered_text(catch_map_show_name, (400, 200), "center")
        bordered_text("加载中...", (400, 500), "center", 16)
        # 联机客户端不本地切到 FIGHT，等主机发 FIGHT 状态；
        # 同时客户端不重置角色 HP / CD，这些由主机同步过来。
        if (not (network_connected and network_role == "client")) and load_time > wait_time:
            STATE = FIGHT
            map_img = maps_img[catch_map_json["name"]]
            # 进入新战斗时清空上一局残留的技能 2 武器
            skill2_2_projectiles.clear()
            chr1_skill2_books.clear()
            if not fight_chars:
                fight_chars = [char_list[0]]
            if network_connected:
                # 联机开局：把双方拉到地图两端，使开局互相看不到对方。
                # 配合 2:1 视角：本机角色前方占屏幕 2/3，视野外即对方位置。
                # 1P 在屏幕左缘（世界 100），2P 在屏幕右缘之外（世界 900）。
                char1_pos = [100, 360]
                char2_pos = [900, 360]
            else:
                char1_pos = [200, 360]
                char2_pos = [800, 360]
            if len(fight_chars) == 2:
                fight_chars[1].facing = "left"
            for char in fight_chars:
                char.hp = char.max_hp
                char.stamina = STAMINA_START
                char.stamina_regen_timer = 0
                char.is_hit = False
                char.is_standing_up = False
                char.stand_up_done = False
                char.damage_protection_total = 0.0
                char.is_invincible = False
                char.invincible_timer = 0
                char.is_stand_up_invincible = False
                char.stand_up_invincible_timer = 0
                char.needs_ground_snap = False
                char.extra_invis_active = False
                char.extra_invis_timer = 0
                char.extra_invis_phase = 0
                char.pending_book_launch = 0
                char.skill2_uses_left = SKILL2_THROW_COUNT
                char.is_training_dead = False
                # 战斗一开始就让所有二技能进入冷却
                char.skills['skill_2']['current_cd'] = char.skills['skill_2']['cd']
            damage_numbers.clear()
            # 双人模式强制开启 CD；单人模式保留玩家自己的开关设置
            if len(fight_chars) == 2:
                cd_enabled = True
            # 重置战斗开场，从 ready 印章开始
            fight_intro_phase = FIGHT_INTRO_READY_STAMP
            fight_intro_timer = 0
            # 开场阶段 UI 视为已就位，真正的滑入会在战斗开始后触发
            ui_slide_timer = UI_SLIDE_FRAMES
            # 重置 KO 状态
            ko_phase = 0
            ko_timer = 0
            ko_loser_index = 0
            ko_loser_airborne = False
        load_time += 1

    elif STATE == FIGHT:
        # 进入战斗时再次禁用输入法，防止切屏后输入法重新激活吃掉按键
        try:
            pygame.key.stop_text_input()
        except Exception:
            pass

        # 联机客户端：只发送输入 + 应用主机状态 + 绘制，不跑本地逻辑
        if network_connected and network_role == "client":
            client_send_input(_custom_key_state)
            if network_peer_state is not None:
                apply_network_state(network_peer_state)
                network_peer_state = None
            draw_client_fight_view(mouse_pos)
            # 客户端在这里 continue，跳过了主循环末尾的延迟显示，
            # 所以单独在这里把延迟画到屏幕上方
            if network_rtt_ms > 0:
                _rtt_text = f"延迟 {network_rtt_ms} ms"
            else:
                _rtt_text = "延迟 -- ms"
            _rf = get_font(18)
            _rsurf = _rf.render(_rtt_text, True, (255, 240, 120))
            _rrect = _rsurf.get_rect(midtop=(W // 2, 4))
            _rbg = pygame.Rect(_rrect.x - 8, _rrect.y - 2, _rrect.w + 16, _rrect.h + 4)
            pygame.draw.rect(screen, (20, 20, 20), _rbg)
            pygame.draw.rect(screen, (180, 180, 180), _rbg, 1)
            screen.blit(_rsurf, _rrect)
            pygame.display.flip()
            clock.tick(FPS)
            continue
        # 主机：无论开场与否，都发送战斗状态给客户端，让客户端也能看到
        # ready / start_fight 印章、地图和进度。
        # 每帧都发 STATE，让客户端攻击动画更流畅。
        if network_connected and network_role == "host":
            try:
                net_send_to_peer("STATE:" + json.dumps(build_network_state(), separators=(',', ':')))
            except Exception:
                pass
        intro_running = (fight_intro_phase != FIGHT_INTRO_DONE)

        # ===== 相机计算（只对局域网联机生效）=====
        # 联机跟随本机角色：相机可以活动（MAP_W > W）时，
        # 角色面向的前方占屏幕 2/3、后方占 1/3，形成 2:1 的视野偏向。
        # 相机无法活动（MAP_W <= W）时保持居中。
        # 本地双人 / 训练 / 单人固定居中，不移动。
        if network_connected:
            if network_is_1p and len(fight_chars) >= 1:
                _cam_tx = char1_pos[0]
                _cam_face = fight_chars[0].facing
            elif (not network_is_1p) and len(fight_chars) >= 2:
                _cam_tx = char2_pos[0]
                _cam_face = fight_chars[1].facing
            else:
                _cam_tx = W // 2
                _cam_face = 'right'
            if MAP_W > W:
                # 前方 : 后方 = 2 : 1
                if _cam_face == 'right':
                    _cam_screen_x = W // 3
                else:
                    _cam_screen_x = W * 2 // 3
            else:
                _cam_screen_x = W // 2
            _target_cam = max(0, min(MAP_W - W, _cam_tx - _cam_screen_x))
            # 开场阶段直接对准目标（避免从上一局残留位置滑过来）；
            # 正常战斗时每帧朝目标缓动，形成平滑的相机跟随。
            if intro_running:
                camera_x = _target_cam
            else:
                camera_x += (_target_cam - camera_x) * CAMERA_LERP
                if abs(_target_cam - camera_x) < 0.5:
                    camera_x = _target_cam
        else:
            # 本地 / 训练 / 单人：镜头锁定地图中央，屏幕显示世界
            # 100~900 区域，视觉上与 800 宽地图时完全一致。
            camera_x = MAP_W // 2 - W // 2

        # 把绘制目标临时重定向到 1000x600 的世界 surface，
        # 所有世界内容先画到它上面，最后按 camera_x 偏移 blit 到真正屏幕。
        _cam_real_screen = screen
        _cam_world_surf = pygame.Surface((MAP_W, H))
        screen = _cam_world_surf

        # 先铺好背景（天空地图会自动叠加飘动的云）
        screen.fill((0, 0, 0))
        draw_map_background(catch_map_json["name"], map_img)

        if intro_running:
            # ===== 开场阶段 =====
            fight_intro_timer += 1
            if fight_intro_phase == FIGHT_INTRO_READY_STAMP:
                if fight_intro_timer >= INTRO_STAMP_FRAMES:
                    fight_intro_phase = FIGHT_INTRO_READY_HOLD
                    fight_intro_timer = 0
            elif fight_intro_phase == FIGHT_INTRO_READY_HOLD:
                if fight_intro_timer >= INTRO_HOLD_FRAMES:
                    fight_intro_phase = FIGHT_INTRO_START_STAMP
                    fight_intro_timer = 0
            elif fight_intro_phase == FIGHT_INTRO_START_STAMP:
                if fight_intro_timer >= INTRO_STAMP_FRAMES:
                    fight_intro_phase = FIGHT_INTRO_START_HOLD
                    fight_intro_timer = 0
            elif fight_intro_phase == FIGHT_INTRO_START_HOLD:
                if fight_intro_timer >= INTRO_START_HOLD_FRAMES:
                    fight_intro_phase = FIGHT_INTRO_DONE
                    fight_intro_timer = 0
                    # 从此刻开始，UI 从边缘滑入
                    ui_slide_timer = 0
                    # 把攻击 / 逃脱 / 跳跃按键标记为“已按下”，
                    # 避免玩家在开场时一直按住某个键导致战斗一开就触发
                    for ch in fight_chars:
                        ch.attack_pressed = True
                        ch.escape_pressed = True
                        ch.jump_pressed = True

            # 角色保持站立，不处理任何输入
            if len(fight_chars) >= 1:
                fight_chars[0].draw_animation("stand", char1_pos, "midbottom")
            if len(fight_chars) == 2:
                fight_chars[1].draw_animation("stand", char2_pos, "midbottom")

            # night 地图：在角色之上、印章之下盖一层 cover.png
            draw_night_cover()

            # 绘制当前阶段的印章
            # 印章画在"当前相机视野中心"：世界坐标 = camera_x + W//2，
            # 这样无论相机跟随谁，印章都出现在自己屏幕的正中。
            _stamp_cx = int(camera_x) + W // 2
            if fight_intro_phase == FIGHT_INTRO_READY_STAMP and ready_img is not None:
                progress = fight_intro_timer / INTRO_STAMP_FRAMES
                draw_stamp_animation(ready_img, (_stamp_cx, H // 2), progress)
            elif fight_intro_phase == FIGHT_INTRO_READY_HOLD and ready_img is not None:
                r = ready_img.get_rect(center=(_stamp_cx, H // 2))
                screen.blit(ready_img, r)
            elif fight_intro_phase == FIGHT_INTRO_START_STAMP and start_fight_img is not None:
                progress = fight_intro_timer / INTRO_STAMP_FRAMES
                draw_stamp_animation(start_fight_img, (_stamp_cx, H // 2), progress)
            elif fight_intro_phase == FIGHT_INTRO_START_HOLD and start_fight_img is not None:
                r = start_fight_img.get_rect(center=(_stamp_cx, H // 2))
                screen.blit(start_fight_img, r)
        else:
            # ===== 正常战斗逻辑 =====
            # UI 滑入计时推进
            if ui_slide_timer < UI_SLIDE_FRAMES:
                ui_slide_timer += 1

            # 使用自定义按键状态字典
            keys = _custom_key_state

            # ===== 脚底光环 =====
            # 隐身时不绘制光环，避免暴露位置
            # 局域网下：本机角色蓝、对方红；其余模式 1P 蓝 2P 红
            if network_connected:
                _my_idx_lan = 0 if network_is_1p else 1
                _aura_0 = AURA_BLUE if _my_idx_lan == 0 else AURA_RED
                _aura_1 = AURA_RED if _my_idx_lan == 0 else AURA_BLUE
            else:
                _aura_0 = AURA_BLUE
                _aura_1 = AURA_RED
            if len(fight_chars) >= 1 and not fight_chars[0].extra_invis_active:
                pos = char1_pos
                screen.blit(_aura_0, (pos[0] - 35, pos[1] - 17))
            if len(fight_chars) == 2 and not fight_chars[1].extra_invis_active:
                pos = char2_pos
                screen.blit(_aura_1, (pos[0] - 35, pos[1] - 17))

            # 绘制 chr_1 的环绕书：远侧（z<0）在角色图层下面
            for _ci_orb in range(min(len(fight_chars), 2)):
                _c_orb = fight_chars[_ci_orb]
                if _c_orb.name == 'chr_1':
                    _pos_orb = char1_pos if _ci_orb == 0 else char2_pos
                    draw_chr1_orbit_books(screen, _c_orb, _pos_orb, 'behind')

            # ===== 玩家1 =====
            if len(fight_chars) >= 1:
                char1 = fight_chars[0]

                # 联机主机：根据抽签结果决定 fight_chars[0] 的输入源。
                # 主机本机永远用 p1 键位；如果本机不是 1P，fight_chars[0] 由客户端控制。
                if network_connected and network_role == "host" and not network_is_1p:
                    _c1_up     = network_remote_input.get("jump", False)
                    _c1_escape = network_remote_input.get("escape", False)
                    _c1_attack = network_remote_input.get("attack", False)
                    _c1_skill1 = network_remote_input.get("skill1", False)
                    _c1_skill2 = network_remote_input.get("skill2", False)
                    _c1_extra  = network_remote_input.get("extra", False)
                    _c1_left   = network_remote_input.get("left", False)
                    _c1_right  = network_remote_input.get("right", False)
                else:
                    _c1_up     = bool(keys[keymap['p1_up']])
                    _c1_escape = bool(keys[keymap['p1_escape']])
                    _c1_attack = bool(keys[keymap['p1_attack']])
                    _c1_skill1 = bool(keys[keymap['p1_skill1']])
                    _c1_skill2 = bool(keys[keymap['p1_skill2']])
                    _c1_extra  = bool(keys[keymap['p1_extra']])
                    _c1_left   = bool(keys[keymap['p1_left']])
                    _c1_right  = bool(keys[keymap['p1_right']])

                # wyk 技能一：被变身期间禁止一切操作
                if char1.wyk_egg_transformed:
                    _c1_up = False
                    _c1_escape = False
                    _c1_attack = False
                    _c1_skill1 = False
                    _c1_skill2 = False
                    _c1_extra = False
                    _c1_left = False
                    _c1_right = False

                # 跳跃按键独立处理：每帧都同步 jump_pressed，与角色当前状态无关。
                # 这样即使处于攻击/受击/前摇/空中等状态，jump_pressed 也能正确更新，
                # 避免落地瞬间被误判为"新一次按下"而导致按住 W 会连跳。
                if _c1_up:
                    if not char1.jump_pressed:
                        char1.jump_pressed = True
                        if (not char1.is_hit and not char1.is_standing_up
                                and not char1.is_training_dead
                                and not char1.is_attacking
                                and char1.pending_book_launch <= 0
                                and char1.pending_skill2_launch <= 0
                                and char1.pending_wyk_skill2_launch <= 0
                                and not char1.wyk_skill2_active
                                and char1.jump_phase == 0):
                            char1.start_jump()
                else:
                    char1.jump_pressed = False

                if _c1_escape:
                    if not char1.escape_pressed:
                        if char1.attack_escape():
                            char1_pos[1] = char1.ground_y
                            char1.needs_ground_snap = False
                            spawn_smoke(char1_pos[0], char1_pos[1])
                        char1.escape_pressed = True
                else:
                    char1.escape_pressed = False

                if char1.needs_ground_snap:
                    char1_pos[1] = char1.ground_y
                    char1.needs_ground_snap = False

                if _c1_attack:
                    _c1_new_press = not char1.attack_pressed
                    char1.attack_pressed = True
                    char1.attack_combo_input_pressed = _c1_new_press
                    if (_c1_new_press and not char1.is_hit
                            and not char1.is_standing_up
                            and not char1.is_training_dead
                            and char1.pending_book_launch <= 0
                            and char1.pending_skill2_launch <= 0
                            and char1.pending_wyk_skill2_launch <= 0
                            and not char1.wyk_skill2_active):
                        char1.attack_normal()
                    char1.attack_held = True
                else:
                    char1.attack_pressed = False
                    char1.attack_held = False
                    char1.attack_combo_input_pressed = False

                # 技能暂未实现：按下 K / L / B 不触发任何行为，仅保留按键入口
                # 将来实现技能时，把下面的 pass 换成对应的攻击调用即可
                # 技能一：chr 投掷书本（有前摇，前摇结束后才发射）
                if _c1_skill1:
                    if not char1.skill_pressed:
                        char1.skill_pressed = True
                        if (not char1.is_hit and not char1.is_standing_up
                                and not char1.is_training_dead
                                and char1.skills['skill_1']['current_cd'] <= 0
                                and char1.pending_book_launch == 0
                                and char1.gjl_skill1_phase == 0
                                and char1.name == 'chr'
                                and chr_book_img is not None
                                and char1.stamina >= char1.skill1_stamina_cost):
                            char1.stamina -= char1.skill1_stamina_cost
                            char1.skills['skill_1']['current_cd'] = char1.skills['skill_1']['cd']
                            char1.pending_book_launch = max(1, int(char1.skills['skill_1']['recovery']))
                            # 前摇期间不移动、不播放攻击动画；角色原地硬直等待。
                            # 冷却在按下瞬间已进入，即使前摇被打断也不会退还。
                            # 【音效】触发技能一 -> 播 skill_1，seq +1
                            char1.skill1_trigger_seq += 1
                            play_sfx(char1.name, 'skill_1')
                        elif (not char1.is_hit and not char1.is_standing_up
                                and not char1.is_training_dead
                                and char1.skills['skill_1']['current_cd'] <= 0
                                and char1.gjl_skill1_uses_left > 0
                                and char1.pending_book_launch == 0
                                and char1.gjl_skill1_phase == 0
                                and char1.name == 'gjl'
                                and (char1.gjl_skill1_uses_left < GJL_SKILL1_STORE_COUNT
                                     or char1.stamina >= char1.skill1_stamina_cost)):
                            # 存储技能：只有本轮第一下消耗体力，后续次免费
                            if char1.gjl_skill1_uses_left == GJL_SKILL1_STORE_COUNT:
                                char1.stamina -= char1.skill1_stamina_cost
                            # gjl 技能一改为存储技能：先扣次数，归 0 时进冷却
                            char1.gjl_skill1_uses_left -= 1
                            if char1.gjl_skill1_uses_left <= 0:
                                char1.skills['skill_1']['current_cd'] = char1.skills['skill_1']['cd']
                            char1.gjl_skill1_phase = 1
                            # 前摇时长从 character_data.json 的
                            # skills.skill_1.recovery 读取（单位：帧）。
                            char1.gjl_skill1_timer = max(
                                1, int(char1.skills['skill_1']['recovery']))
                            char1.gjl_skill1_total_frames = char1.gjl_skill1_timer
                            char1.gjl_skill1_hit = False
                            # 【音效】触发技能一 -> 播 skill_1，seq +1
                            char1.skill1_trigger_seq += 1
                            play_sfx(char1.name, 'skill_1')
                        elif (not char1.is_hit and not char1.is_standing_up
                                and not char1.is_training_dead
                                and char1.skills['skill_1']['current_cd'] <= 0
                                and char1.pending_book_launch == 0
                                and char1.gjl_skill1_phase == 0
                                and char1.name == 'wyk'
                                and wyk_egg_img is not None
                                and char1.stamina >= char1.skill1_stamina_cost):
                            char1.stamina -= char1.skill1_stamina_cost
                            # wyk 技能一：普通技能，按下立即进入冷却。
                            char1.skills['skill_1']['current_cd'] = char1.skills['skill_1']['cd']
                            char1.pending_book_launch = max(1, int(char1.skills['skill_1']['recovery']))
                            # 【音效】触发技能一 -> 播 skill_1，seq +1
                            char1.skill1_trigger_seq += 1
                            play_sfx(char1.name, 'skill_1')
                        elif (not char1.is_hit and not char1.is_standing_up
                                and not char1.is_training_dead
                                and char1.name == 'chr_1'
                                and chr1_book_img is not None
                                and ((not char1.chr1_skill1_activated
                                      and char1.stamina >= char1.skill1_stamina_cost)
                                     or (char1.chr1_skill1_activated
                                         and char1.skills['skill_1']['current_cd'] <= 0
                                         and char1.chr1_skill1_uses_left > 0))):
                            # 召唤时消耗一次体力；之后释放追踪书免费
                            if not char1.chr1_skill1_activated:
                                char1.stamina -= char1.skill1_stamina_cost
                            # chr_1 技能一：
                            #   第一次按下 -> 召唤 3 本环绕书（不消耗、不释放）
                            #   之后每按一次 -> 释放 1 本追踪书
                            if not char1.chr1_skill1_activated:
                                char1.chr1_skill1_activated = True
                                char1.chr1_skill1_uses_left = CHR1_SKILL1_STORE_COUNT
                                char1.chr1_orbit_books_alive = [True, True, True]
                                char1.chr1_orbit_books_age = 0
                                char1.skill1_trigger_seq += 1
                                play_sfx(char1.name, 'skill_1')
                            elif (char1.skills['skill_1']['current_cd'] <= 0
                                    and char1.chr1_skill1_uses_left > 0):
                                # 消耗一本存活的书（按索引从小到大）
                                for _ai_1 in range(3):
                                    if char1.chr1_orbit_books_alive[_ai_1]:
                                        char1.chr1_orbit_books_alive[_ai_1] = False
                                        break
                                char1.chr1_skill1_uses_left = sum(
                                    1 for _b_1 in char1.chr1_orbit_books_alive if _b_1)
                                if char1.chr1_skill1_uses_left <= 0:
                                    char1.skills['skill_1']['current_cd'] = char1.skills['skill_1']['cd']
                                _c1_sx = char1_pos[0]
                                _c1_sy = char1_pos[1] - 60
                                if len(fight_chars) >= 2:
                                    _c1_trect = fight_chars[1].get_rect(char2_pos)
                                    _c1_tx = float(_c1_trect.centerx)
                                    _c1_ty = float(_c1_trect.centery)
                                else:
                                    _c1_tx = _c1_sx + (300 if char1.facing == 'right' else -300)
                                    _c1_ty = _c1_sy
                                _c1_ang = math.atan2(_c1_ty - _c1_sy, _c1_tx - _c1_sx)
                                chr1_track_books.append({
                                    'owner_index': 0,
                                    'x': float(_c1_sx),
                                    'y': float(_c1_sy),
                                    'angle': _c1_ang,
                                    'speed': CHR1_SKILL1_BOOK_SPEED,
                                    'damage': CHR1_SKILL1_DAMAGE,
                                    'target_x': _c1_tx,
                                    'target_y': _c1_ty,
                                    'age': 0,
                                })
                                char1.skill1_trigger_seq += 1
                                play_sfx(char1.name, 'skill_1')
                else:
                    char1.skill_pressed = False
                # 技能二：chr 三书投掷（无前摇，每次按下立即投出 3 本，共可释放 SKILL2_THROW_COUNT 次；
                # 次数用完后进入冷却，冷却结束次数重置）
                if _c1_skill2:
                    if not char1.skill2_pressed:
                        char1.skill2_pressed = True
                        if (not char1.is_hit and not char1.is_standing_up
                                and not char1.is_training_dead
                                and char1.skills['skill_2']['current_cd'] <= 0
                                and char1.skill2_uses_left > 0
                                and char1.pending_skill2_launch == 0
                                and char1.pending_book_launch == 0
                                and char1.name == 'chr'
                                and chr_book_img is not None
                                and (char1.skill2_uses_left < SKILL2_THROW_COUNT
                                     or char1.stamina >= char1.skill2_stamina_cost)):
                            # 存储技能：只有本轮第一下消耗体力，后续次免费
                            if char1.skill2_uses_left == SKILL2_THROW_COUNT:
                                char1.stamina -= char1.skill2_stamina_cost
                            # 技能二有前摇：按下先进入前摇，前摇结束才真正投出 3 本书。
                            # 次数在按下瞬间就扣掉（视为已使用），前摇被打断也不退还。
                            char1.skill2_uses_left -= 1
                            if char1.skill2_uses_left <= 0:
                                char1.skills['skill_2']['current_cd'] = char1.skills['skill_2']['cd']
                            char1.pending_skill2_launch = max(1, int(char1.skills['skill_2']['recovery']))
                            # 【音效】触发技能二 -> 播 skill_2，seq +1
                            char1.skill2_trigger_seq += 1
                            play_sfx(char1.name, 'skill_2')
                        elif (not char1.is_hit and not char1.is_standing_up
                                and not char1.is_training_dead
                                and char1.name == 'gjl'
                                and gjl_code_img is not None):
                            # gjl 技能二：
                            #   phase==0 且 CD 好了 -> 进入前摇
                            #   phase==2（第一段 code 飞行中）-> 触发二段
                            if (char1.gjl_skill2_phase == 0
                                    and char1.skills['skill_2']['current_cd'] <= 0
                                    and char1.stamina >= char1.skill2_stamina_cost):
                                char1.stamina -= char1.skill2_stamina_cost
                                # 普通技能：按下立即进入冷却
                                char1.skills['skill_2']['current_cd'] = char1.skills['skill_2']['cd']
                                char1.gjl_skill2_phase = 1
                                char1.gjl_skill2_timer = GJL_SKILL2_WINDUP_FRAMES
                                # 【音效】触发技能二 -> 播 skill_2，seq +1
                                char1.skill2_trigger_seq += 1
                                play_sfx(char1.name, 'skill_2')
                            elif char1.gjl_skill2_phase == 2:
                                # 二段：让当前飞行的 code 就地分裂成两个垂直方向的曲线 code
                                _old_p = None
                                for _p in gjl_skill2_projectiles:
                                    if _p['owner_index'] == 0 and not _p['is_curved']:
                                        _old_p = _p
                                        break
                                if _old_p is not None:
                                    _px = _old_p['x']
                                    _py = _old_p['y']
                                    # 删除原 code
                                    gjl_skill2_projectiles[:] = [
                                        _p for _p in gjl_skill2_projectiles
                                        if not (_p['owner_index'] == 0 and not _p['is_curved'])]
                                    # 锁定此刻敌方位置
                                    if len(fight_chars) == 2:
                                        _opos = char2_pos
                                        _tx = float(_opos[0])
                                        _ty = float(_opos[1] - 60)
                                    else:
                                        _tx = _px
                                        _ty = _py
                                    _base = char1.gjl_skill2_origin_angle
                                    for _ang in (_base + math.pi / 2, _base - math.pi / 2):
                                        gjl_skill2_projectiles.append({
                                            'owner_index': 0,
                                            'x': float(_px),
                                            'y': float(_py),
                                            'angle': _ang,
                                            'speed': GJL_SKILL2_CODE2_SPEED,
                                            'damage': GJL_SKILL2_CODE2_DAMAGE,
                                            'is_curved': True,
                                            'track_live': False,
                                            'target_x': _tx,
                                            'target_y': _ty,
                                        })
                                    # 分裂完成后技能施放流程就结束了，phase 直接回 0。
                                    # 分裂出的小 code 继续独立飞行，不再阻塞技能二。
                                    # 存储技能模式下 CD 已由次数用尽控制，这里不再额外进 CD。
                                    char1.gjl_skill2_phase = 0
                        elif (not char1.is_hit and not char1.is_standing_up
                                and not char1.is_training_dead
                                and char1.skills['skill_2']['current_cd'] <= 0
                                and char1.pending_wyk_skill2_launch == 0
                                and not char1.wyk_skill2_active
                                and char1.pending_book_launch == 0
                                and char1.pending_skill2_launch == 0
                                and char1.name == 'wyk'
                                and char1.stamina >= char1.skill2_stamina_cost):
                            # wyk 技能二（声波）：按下立即进入 CD，
                            # 进入前摇（显示 skill_2_use1.png）
                            char1.stamina -= char1.skill2_stamina_cost
                            char1.skills['skill_2']['current_cd'] = char1.skills['skill_2']['cd']
                            char1.pending_wyk_skill2_launch = max(1, int(char1.skills['skill_2']['recovery']))
                            char1.skill2_trigger_seq += 1
                            play_sfx(char1.name, 'skill_2')
                        elif (not char1.is_hit and not char1.is_standing_up
                                and not char1.is_training_dead
                                and char1.skills['skill_2']['current_cd'] <= 0
                                and char1.name == 'chr_1'
                                and chr1_book_img is not None
                                and char1.stamina >= char1.skill2_stamina_cost):
                            # chr_1 技能二（旋转散射书，普通技能）：
                            # 以角色朝向为基础角，旋转一整圈均匀发射 16 本书，
                            # 每本书沿自己的角度直线飞行，命中造成固定伤害。
                            # 按下立即进入冷却。
                            char1.stamina -= char1.skill2_stamina_cost
                            char1.skills['skill_2']['current_cd'] = char1.skills['skill_2']['cd']
                            _c1_base_ang = 0.0 if char1.facing == 'right' else math.pi
                            _c1_s2_sx = float(char1_pos[0])
                            _c1_s2_sy = float(char1_pos[1] - 60)
                            for _k1 in range(CHR1_SKILL2_BOOK_COUNT):
                                _a1 = _c1_base_ang + _k1 * (2.0 * math.pi / CHR1_SKILL2_BOOK_COUNT)
                                chr1_skill2_books.append({
                                    'owner_index': 0,
                                    'x': _c1_s2_sx,
                                    'y': _c1_s2_sy,
                                    'angle': _a1,
                                    'angular_speed': CHR1_SKILL2_ANGULAR_SPEED,
                                    'damage': CHR1_SKILL2_DAMAGE,
                                    'age': 0,
                                })
                            char1.skill2_trigger_seq += 1
                            play_sfx(char1.name, 'skill_2')
                else:
                    char1.skill2_pressed = False
                if _c1_extra:
                    # 边沿触发：只有从松到按才触发一次，避免按住不放反复开启/结束
                    if not char1.extra_pressed:
                        _ret = char1.attack_extra()
                        if _ret == 1:
                            # 隐身：原地冒白烟
                            spawn_smoke(char1_pos[0], char1_pos[1])
                        # _ret == 2 时技能 2 激活，震动由全局状态驱动
                        char1.extra_pressed = True
                else:
                    char1.extra_pressed = False

                if not char1.is_hit and not char1.is_standing_up:
                    if (char1.pending_wyk_skill2_launch > 0
                            and char1.name == 'wyk'):
                        # wyk 技能二前摇：用 skill_2_use/ 帧序列
                        _total = max(1, int(char1.skills['skill_2']['recovery']))
                        _use_frame = char1.get_skill_use_frame(
                            'skill_2_use', _total,
                            char1.pending_wyk_skill2_launch)
                        if _use_frame is not None:
                            _img = _use_frame
                            if char1.facing == 'left':
                                _img = get_flip(_img)
                            _rect = _img.get_rect(midbottom=(char1_pos[0], char1_pos[1]))
                            screen.blit(_img, _rect)
                        else:
                            char1.update("stand")
                            char1.draw("stand", char1_pos, "midbottom")
                    elif (char1.wyk_skill2_active
                            and char1.name == 'wyk'):
                        # wyk 技能二激活：用 skill_2_use/ 的 _数字 帧序列
                        _total = max(1, WYK_SKILL2_DURATION_FRAMES)
                        _use_frame = char1.get_skill_use_frame(
                            'skill_2_use_after', _total, char1.wyk_skill2_timer)
                        if _use_frame is not None:
                            _img = _use_frame
                            if char1.facing == 'left':
                                _img = get_flip(_img)
                            _rect = _img.get_rect(midbottom=(char1_pos[0], char1_pos[1]))
                            screen.blit(_img, _rect)
                        else:
                            char1.update("stand")
                            char1.draw("stand", char1_pos, "midbottom")
                    elif char1.gjl_skill2_phase == 1:
                        # gjl 技能二前摇：用 skill_2_use/ 帧序列
                        _total = max(1, GJL_SKILL2_WINDUP_FRAMES)
                        _use_frame = char1.get_skill_use_frame(
                            'skill_2_use', _total, char1.gjl_skill2_timer)
                        if _use_frame is not None:
                            _img = _use_frame
                            if char1.facing == 'left':
                                _img = get_flip(_img)
                            _rect = _img.get_rect(midbottom=(char1_pos[0], char1_pos[1]))
                            screen.blit(_img, _rect)
                        else:
                            char1.update("stand")
                            char1.draw("stand", char1_pos, "midbottom")
                    elif char1.gjl_skill1_phase != 0:
                        # gjl 技能一绘制：
                        #   前摇阶段：用 skill_1_use/ 帧序列显示。
                        #   突进阶段：中间 2 帧显示 trade，其余帧画普通动画。
                        _gjl1_drawn = False
                        if char1.gjl_skill1_phase == 1:
                            _total = max(1, char1.gjl_skill1_total_frames)
                            _use_frame = char1.get_skill_use_frame(
                                'skill_1_use', _total, char1.gjl_skill1_timer)
                            if _use_frame is not None:
                                _img1 = _use_frame
                                if char1.facing == 'left':
                                    _img1 = get_flip(_img1)
                                _r1 = _img1.get_rect(midbottom=(char1_pos[0], char1_pos[1]))
                                screen.blit(_img1, _r1)
                                _gjl1_drawn = True
                        else:
                            _t = char1.gjl_skill1_timer
                            if 2 <= _t < 4 and gjl_trade_img is not None:
                                _img2 = gjl_trade_img
                                if char1.facing == 'left':
                                    _img2 = get_flip(_img2)
                                _r2 = _img2.get_rect(midbottom=(char1_pos[0], char1_pos[1]))
                                screen.blit(_img2, _r2)
                                _gjl1_drawn = True
                        if not _gjl1_drawn:
                            char1.update("stand")
                            char1.draw("stand", char1_pos, "midbottom")
                    elif char1.pending_book_launch > 0 or char1.pending_skill2_launch > 0:
                        # 技能前摇期间：角色被硬控，原地站立，不能移动 / 跳跃。
                        # 前摇图从 skill/skill_1_use/ 与 skill/skill_2_use/
                        # 子文件夹自动加载，按前摇总帧数分配每帧停留时间。
                        # 找不到图就回退到站立图。
                        _drawn = False
                        if char1.pending_book_launch > 0 and char1.name in ('chr', 'wyk'):
                            _total = max(1, int(char1.skills['skill_1']['recovery']))
                            _use_frame = char1.get_skill_use_frame(
                                'skill_1_use', _total, char1.pending_book_launch)
                            if _use_frame is not None:
                                img = _use_frame
                                if char1.facing == 'left':
                                    img = get_flip(img)
                                rect = img.get_rect(midbottom=(char1_pos[0], char1_pos[1]))
                                screen.blit(img, rect)
                                _drawn = True
                        elif char1.pending_skill2_launch > 0 and char1.name == 'chr':
                            _total = max(1, int(char1.skills['skill_2']['recovery']))
                            _use_frame = char1.get_skill_use_frame(
                                'skill_2_use', _total, char1.pending_skill2_launch)
                            if _use_frame is not None:
                                img = _use_frame
                                if char1.facing == 'left':
                                    img = get_flip(img)
                                rect = img.get_rect(midbottom=(char1_pos[0], char1_pos[1]))
                                screen.blit(img, rect)
                                _drawn = True
                        if not _drawn:
                            char1.update("stand")
                            char1.draw("stand", char1_pos, "midbottom")
                    elif not char1.is_attacking:
                        if _c1_left:
                            char1_pos = char1.move(char1_pos, "left")
                        elif _c1_right:
                            char1_pos = char1.move(char1_pos, "right")
                        else:
                            if char1.jump_phase == 0:
                                char1.update("stand")
                                char1.draw("stand", char1_pos, "midbottom")
                            elif char1.jump_phase == 1:
                                char1.update("jump")
                                char1.draw("jump", char1_pos, "midbottom")
                            else:
                                char1.draw("walk", char1_pos, "midbottom")
                    else:
                        if char1.attack_phase == 'anim':
                            char1.update(char1.attack_anim)
                            char1.draw(char1.attack_anim, char1_pos, "midbottom")
                        else:
                            char1.draw("stand", char1_pos, "midbottom")
                else:
                    char1.draw("stand", char1_pos, "midbottom")

                char1_pos = char1.update_physics(char1_pos)

                # 技能一前摇计时：被打断就取消，否则到点发射书本 / 蛋
                if char1.pending_book_launch > 0:
                    if char1.is_hit or char1.is_standing_up:
                        char1.pending_book_launch = 0
                    else:
                        char1.pending_book_launch -= 1
                        if char1.pending_book_launch == 0:
                            if char1.name == 'wyk' and wyk_egg_img is not None:
                                # wyk 技能一：扔蛋。发射时读取对手此刻
                                # 的矩形中心作为瞄准点，反推出一条能精确命中该
                                # 点的抛物线初速度。对手在此之后移动就会躲开。
                                _sx = char1_pos[0]
                                _sy = char1_pos[1] - 60
                                if len(fight_chars) >= 2:
                                    _t1_rect = fight_chars[1].get_rect(char2_pos)
                                    _tx = _t1_rect.centerx
                                    _ty = _t1_rect.centery
                                else:
                                    # 单机模式没有对手，往正前方飞
                                    _tx = _sx + (300 if char1.facing == 'right' else -300)
                                    _ty = _sy
                                _evx, _evy = calc_egg_velocity(_sx, _sy, _tx, _ty)
                                wyk_eggs.append({
                                    'owner_index': 0,
                                    'x': float(_sx),
                                    'y': float(_sy),
                                    'vx': _evx,
                                    'vy': _evy,
                                })
                            else:
                                sx = char1_pos[0]
                                sy = char1_pos[1] - 60
                                vx = BOOK_SPEED if char1.facing == 'right' else -BOOK_SPEED
                                book_projectiles.append({
                                    'owner_index': 0,
                                    'target_index': 1,
                                    'facing': char1.facing,
                                    'x': sx,
                                    'y': sy,
                                    'base_y': sy,
                                    'vx': vx,
                                    'phase': 0,
                                })

                # 技能二前摇计时：被打断就取消，否则到点投出 3 本书
                if char1.pending_skill2_launch > 0:
                    if char1.is_hit or char1.is_standing_up:
                        char1.pending_skill2_launch = 0
                    else:
                        char1.pending_skill2_launch -= 1
                        if char1.pending_skill2_launch == 0:
                            sy_center = char1_pos[1] - 60
                            vx = BOOK_SPEED if char1.facing == 'right' else -BOOK_SPEED
                            if chr_book_img is not None:
                                step = chr_book_img.get_height() + SKILL2_BOOK_VERTICAL_SPACING
                            else:
                                step = 40 + SKILL2_BOOK_VERTICAL_SPACING
                            half = (SKILL2_BOOKS_PER_THROW - 1) / 2
                            for k in range(SKILL2_BOOKS_PER_THROW):
                                offset = (k - half) * step
                                sy = sy_center + offset
                                book_projectiles.append({
                                    'owner_index': 0,
                                    'target_index': 1,
                                    'facing': char1.facing,
                                    'x': char1_pos[0],
                                    'y': sy,
                                    'base_y': sy,
                                    'vx': vx,
                                    'phase': 0,
                                    'damage': BOOK_DAMAGE_SKILL2,
                                })

                # wyk 技能二：前摇计时
                if char1.pending_wyk_skill2_launch > 0:
                    if char1.is_hit or char1.is_standing_up:
                        char1.pending_wyk_skill2_launch = 0
                    else:
                        char1.pending_wyk_skill2_launch -= 1
                        if char1.pending_wyk_skill2_launch == 0:
                            char1.wyk_skill2_active = True
                            char1.wyk_skill2_timer = WYK_SKILL2_DURATION_FRAMES
                            char1.wyk_skill2_hit = False
                            char1.wyk_skill2_last_hit_frame = -999
                            char1.wyk_skill2_shake_x = 0
                            char1.wyk_skill2_shake_y = 0

                # wyk 技能二：声波激活更新
                if char1.wyk_skill2_active:
                    if char1.is_hit or char1.is_standing_up:
                        char1.wyk_skill2_active = False
                        char1.wyk_skill2_timer = 0
                        char1.wyk_skill2_hit = False
                        char1.wyk_skill2_damage_override = None
                    else:
                        char1.wyk_skill2_timer -= 1
                        char1.wyk_skill2_shake_x = random.randint(
                            -WYK_SKILL2_VOICE_SHAKE, WYK_SKILL2_VOICE_SHAKE)
                        char1.wyk_skill2_shake_y = random.randint(
                            -WYK_SKILL2_VOICE_SHAKE, WYK_SKILL2_VOICE_SHAKE)
                        # 命中判定：每隔 WYK_SKILL2_HIT_INTERVAL_FRAMES 帧判定一次，
                        # 让对手浮在空中被声波反复击中，而不是只挨一下。
                        if len(fight_chars) == 2:
                            if (ui_frame_count - char1.wyk_skill2_last_hit_frame
                                    >= WYK_SKILL2_HIT_INTERVAL_FRAMES):
                                _v_r = get_wyk_skill2_voice_rect(
                                    char1, char1_pos,
                                    char1.wyk_skill2_shake_x,
                                    char1.wyk_skill2_shake_y)
                                _t_ch = fight_chars[1]
                                _t_r = _t_ch.get_rect(char2_pos)
                                if _v_r is not None and _v_r.colliderect(_t_r):
                                    # 隐藏技能：目标处于变身（被 wyk 一技能抓取）
                                    # 状态时，本次声波触发隐藏技能：
                                    #   伤害变为 832、本次二技能冷却 +10s、
                                    #   被控玩家持续冒白烟、10s 内暴击率 35%
                                    _dmg2 = WYK_SKILL2_DAMAGE
                                    if (_t_ch.wyk_egg_transformed
                                            and char1.wyk_skill2_damage_override is None):
                                        char1.wyk_skill2_damage_override = 832
                                        char1.skills['skill_2']['current_cd'] += int(10.0 * FPS)
                                        char1.wyk_hidden_boost_timer = int(10.0 * FPS)
                                        _t_ch.wyk_hidden_smoke = True
                                    if char1.wyk_skill2_damage_override is not None:
                                        _dmg2 = char1.wyk_skill2_damage_override
                                    _new_num = _t_ch.take_damage(
                                        _dmg2,
                                        char1.facing, char1_pos, char1)
                                    if _new_num is not None:
                                        # 覆盖为较大的击飞初速度，让对方浮空；
                                        # 同时把水平速度压小，避免对手飞出声道范围。
                                        _t_ch.hit_vy = WYK_SKILL2_HIT_VY
                                        _t_ch.hit_vx = 0
                                        # 兜底：对手底部不能高于声波中心上方 50 像素，
                                        # 否则下一次判定会打不到。
                                        _min_bottom_y = _v_r.centery - 50
                                        if char2_pos[1] < _min_bottom_y:
                                            char2_pos[1] = _min_bottom_y
                                        _new_num.x = char2_pos[0]
                                        _new_num.y = char2_pos[1] - 80
                                        damage_numbers.append(_new_num)
                                        shake.start()
                                        char1.wyk_skill2_hit = True
                                        char1.wyk_skill2_last_hit_frame = ui_frame_count
                        if char1.wyk_skill2_timer <= 0:
                            char1.wyk_skill2_active = False
                            char1.wyk_skill2_hit = False
                            char1.wyk_skill2_damage_override = None

            # ===== 玩家2 =====
            if len(fight_chars) == 2:
                char2 = fight_chars[1]
                # 联机主机：根据抽签结果决定 fight_chars[1] 的输入源。
                # 如果本机是 1P，fight_chars[1] 由客户端控制；
                # 如果本机是 2P，fight_chars[1] 是本机角色，用本机键盘
                # （p2 键位，即小键盘）控制。
                if network_connected and network_role == "host":
                    if network_is_1p:
                        # 本机是 1P，fight_chars[1] 由客户端控制
                        p2_attack = network_remote_input.get("attack", False)
                        p2_escape = network_remote_input.get("escape", False)
                        p2_skill1 = network_remote_input.get("skill1", False)
                        p2_skill2 = network_remote_input.get("skill2", False)
                        p2_extra = network_remote_input.get("extra", False)
                        p2_jump = network_remote_input.get("jump", False)
                        p2_left = network_remote_input.get("left", False)
                        p2_right = network_remote_input.get("right", False)
                    else:
                        # 本机是 2P，局域网下也用 p1 键位（WASD）
                        p2_attack = bool(keys[keymap['p1_attack']])
                        p2_escape = bool(keys[keymap['p1_escape']])
                        p2_skill1 = bool(keys[keymap['p1_skill1']])
                        p2_skill2 = bool(keys[keymap['p1_skill2']])
                        p2_extra = bool(keys[keymap['p1_extra']])
                        p2_jump = bool(keys[keymap['p1_up']])
                        p2_left = bool(keys[keymap['p1_left']])
                        p2_right = bool(keys[keymap['p1_right']])
                else:
                    p2_attack = bool(keys[keymap['p2_attack']])
                    p2_escape = bool(keys[keymap['p2_escape']])
                    p2_skill1 = bool(keys[keymap['p2_skill1']])
                    p2_skill2 = bool(keys[keymap['p2_skill2']])
                    p2_extra = bool(keys[keymap['p2_extra']])
                    p2_jump = bool(keys[keymap['p2_up']])
                    p2_left = bool(keys[keymap['p2_left']])
                    p2_right = bool(keys[keymap['p2_right']])

                # wyk 技能一：被变身期间禁止一切操作
                if char2.wyk_egg_transformed:
                    p2_jump = False
                    p2_escape = False
                    p2_attack = False
                    p2_skill1 = False
                    p2_skill2 = False
                    p2_extra = False
                    p2_left = False
                    p2_right = False

                # 跳跃按键独立处理（与 1P 相同思路）
                if p2_jump:
                    if not char2.jump_pressed:
                        char2.jump_pressed = True
                        if (not char2.is_hit and not char2.is_standing_up
                                and not char2.is_training_dead
                                and not char2.is_attacking
                                and char2.pending_book_launch <= 0
                                and char2.pending_skill2_launch <= 0
                                and char2.pending_wyk_skill2_launch <= 0
                                and not char2.wyk_skill2_active
                                and char2.jump_phase == 0):
                            char2.start_jump()
                else:
                    char2.jump_pressed = False

                if p2_escape:
                    if not char2.escape_pressed:
                        if char2.attack_escape():
                            char2_pos[1] = char2.ground_y
                            char2.needs_ground_snap = False
                            spawn_smoke(char2_pos[0], char2_pos[1])
                        char2.escape_pressed = True
                else:
                    char2.escape_pressed = False

                if char2.needs_ground_snap:
                    char2_pos[1] = char2.ground_y
                    char2.needs_ground_snap = False

                if p2_attack:
                    _p2_new_press = not char2.attack_pressed
                    char2.attack_pressed = True
                    char2.attack_combo_input_pressed = _p2_new_press
                    if (_p2_new_press and not char2.is_hit
                            and not char2.is_standing_up
                            and not char2.is_training_dead
                            and char2.pending_book_launch <= 0
                            and char2.pending_skill2_launch <= 0
                            and char2.pending_wyk_skill2_launch <= 0
                            and not char2.wyk_skill2_active):
                        char2.attack_normal()
                    char2.attack_held = True
                else:
                    char2.attack_pressed = False
                    char2.attack_held = False
                    char2.attack_combo_input_pressed = False

                # 技能暂未实现：按下小键盘 3 / 6 / 5 不触发任何行为，仅保留按键入口
                # 将来实现技能时，把下面的 pass 换成对应的攻击调用即可
                # 技能一：chr 投掷书本（有前摇，前摇结束后才发射）
                if p2_skill1:
                    if not char2.skill_pressed:
                        char2.skill_pressed = True
                        if (not char2.is_hit and not char2.is_standing_up
                                and not char2.is_training_dead
                                and char2.skills['skill_1']['current_cd'] <= 0
                                and char2.pending_book_launch == 0
                                and char2.gjl_skill1_phase == 0
                                and char2.name == 'chr'
                                and chr_book_img is not None
                                and char2.stamina >= char2.skill1_stamina_cost):
                            char2.stamina -= char2.skill1_stamina_cost
                            char2.skills['skill_1']['current_cd'] = char2.skills['skill_1']['cd']
                            char2.pending_book_launch = max(1, int(char2.skills['skill_1']['recovery']))
                            # 前摇期间不移动、不播放攻击动画；角色原地硬直等待。
                            # 冷却在按下瞬间已进入，即使前摇被打断也不会退还。
                            # 【音效】触发技能一 -> 播 skill_1，seq +1
                            char2.skill1_trigger_seq += 1
                            play_sfx(char2.name, 'skill_1')
                        elif (not char2.is_hit and not char2.is_standing_up
                                and not char2.is_training_dead
                                and char2.skills['skill_1']['current_cd'] <= 0
                                and char2.gjl_skill1_uses_left > 0
                                and char2.pending_book_launch == 0
                                and char2.gjl_skill1_phase == 0
                                and char2.name == 'gjl'
                                and (char2.gjl_skill1_uses_left < GJL_SKILL1_STORE_COUNT
                                     or char2.stamina >= char2.skill1_stamina_cost)):
                            # 存储技能：只有本轮第一下消耗体力，后续次免费
                            if char2.gjl_skill1_uses_left == GJL_SKILL1_STORE_COUNT:
                                char2.stamina -= char2.skill1_stamina_cost
                            # gjl 技能一改为存储技能：先扣次数，归 0 时进冷却
                            char2.gjl_skill1_uses_left -= 1
                            if char2.gjl_skill1_uses_left <= 0:
                                char2.skills['skill_1']['current_cd'] = char2.skills['skill_1']['cd']
                            char2.gjl_skill1_phase = 1
                            # 前摇时长从 character_data.json 的
                            # skills.skill_1.recovery 读取（单位：帧）。
                            char2.gjl_skill1_timer = max(
                                1, int(char2.skills['skill_1']['recovery']))
                            char2.gjl_skill1_total_frames = char2.gjl_skill1_timer
                            char2.gjl_skill1_hit = False
                            # 【音效】触发技能一 -> 播 skill_1，seq +1
                            char2.skill1_trigger_seq += 1
                            play_sfx(char2.name, 'skill_1')
                        elif (not char2.is_hit and not char2.is_standing_up
                                and not char2.is_training_dead
                                and char2.skills['skill_1']['current_cd'] <= 0
                                and char2.pending_book_launch == 0
                                and char2.gjl_skill1_phase == 0
                                and char2.name == 'wyk'
                                and wyk_egg_img is not None
                                and char2.stamina >= char2.skill1_stamina_cost):
                            char2.stamina -= char2.skill1_stamina_cost
                            # wyk 技能一：普通技能，按下立即进入冷却。
                            char2.skills['skill_1']['current_cd'] = char2.skills['skill_1']['cd']
                            char2.pending_book_launch = max(1, int(char2.skills['skill_1']['recovery']))
                            # 【音效】触发技能一 -> 播 skill_1，seq +1
                            char2.skill1_trigger_seq += 1
                            play_sfx(char2.name, 'skill_1')
                        elif (not char2.is_hit and not char2.is_standing_up
                                and not char2.is_training_dead
                                and char2.name == 'chr_1'
                                and chr_book_img is not None
                                and ((not char2.chr1_skill1_activated
                                      and char2.stamina >= char2.skill1_stamina_cost)
                                     or (char2.chr1_skill1_activated
                                         and char2.skills['skill_1']['current_cd'] <= 0
                                         and char2.chr1_skill1_uses_left > 0))):
                            # 召唤时消耗一次体力；之后释放追踪书免费
                            if not char2.chr1_skill1_activated:
                                char2.stamina -= char2.skill1_stamina_cost
                            # chr_1 技能一：
                            #   第一次按下 -> 召唤 3 本环绕书（不消耗、不释放）
                            #   之后每按一次 -> 释放 1 本追踪书
                            if not char2.chr1_skill1_activated:
                                char2.chr1_skill1_activated = True
                                char2.chr1_skill1_uses_left = CHR1_SKILL1_STORE_COUNT
                                char2.chr1_orbit_books_alive = [True, True, True]
                                char2.chr1_orbit_books_age = 0
                                char2.skill1_trigger_seq += 1
                                play_sfx(char2.name, 'skill_1')
                            elif (char2.skills['skill_1']['current_cd'] <= 0
                                    and char2.chr1_skill1_uses_left > 0):
                                # 消耗一本存活的书（按索引从小到大）
                                for _ai_2 in range(3):
                                    if char2.chr1_orbit_books_alive[_ai_2]:
                                        char2.chr1_orbit_books_alive[_ai_2] = False
                                        break
                                char2.chr1_skill1_uses_left = sum(
                                    1 for _b_2 in char2.chr1_orbit_books_alive if _b_2)
                                if char2.chr1_skill1_uses_left <= 0:
                                    char2.skills['skill_1']['current_cd'] = char2.skills['skill_1']['cd']
                                _c2_sx = char2_pos[0]
                                _c2_sy = char2_pos[1] - 60
                                if len(fight_chars) >= 2:
                                    _c2_trect = fight_chars[0].get_rect(char1_pos)
                                    _c2_tx = float(_c2_trect.centerx)
                                    _c2_ty = float(_c2_trect.centery)
                                else:
                                    _c2_tx = _c2_sx + (300 if char2.facing == 'right' else -300)
                                    _c2_ty = _c2_sy
                                _c2_ang = math.atan2(_c2_ty - _c2_sy, _c2_tx - _c2_sx)
                                chr1_track_books.append({
                                    'owner_index': 1,
                                    'x': float(_c2_sx),
                                    'y': float(_c2_sy),
                                    'angle': _c2_ang,
                                    'speed': CHR1_SKILL1_BOOK_SPEED,
                                    'damage': CHR1_SKILL1_DAMAGE,
                                    'target_x': _c2_tx,
                                    'target_y': _c2_ty,
                                    'age': 0,
                                })
                                char2.skill1_trigger_seq += 1
                                play_sfx(char2.name, 'skill_1')
                else:
                    char2.skill_pressed = False
                # 技能二：chr 三书投掷（无前摇，每次按下立即投出 3 本，共可释放 SKILL2_THROW_COUNT 次；
                # 次数用完后进入冷却，冷却结束次数重置）
                if p2_skill2:
                    if not char2.skill2_pressed:
                        char2.skill2_pressed = True
                        if (not char2.is_hit and not char2.is_standing_up
                                and not char2.is_training_dead
                                and char2.skills['skill_2']['current_cd'] <= 0
                                and char2.skill2_uses_left > 0
                                and char2.pending_skill2_launch == 0
                                and char2.pending_book_launch == 0
                                and char2.name == 'chr'
                                and chr_book_img is not None
                                and (char2.skill2_uses_left < SKILL2_THROW_COUNT
                                     or char2.stamina >= char2.skill2_stamina_cost)):
                            # 存储技能：只有本轮第一下消耗体力，后续次免费
                            if char2.skill2_uses_left == SKILL2_THROW_COUNT:
                                char2.stamina -= char2.skill2_stamina_cost
                            # 技能二有前摇：按下先进入前摇，前摇结束才真正投出 3 本书。
                            char2.skill2_uses_left -= 1
                            if char2.skill2_uses_left <= 0:
                                char2.skills['skill_2']['current_cd'] = char2.skills['skill_2']['cd']
                            char2.pending_skill2_launch = max(1, int(char2.skills['skill_2']['recovery']))
                            # 【音效】触发技能二 -> 播 skill_2，seq +1
                            char2.skill2_trigger_seq += 1
                            play_sfx(char2.name, 'skill_2')
                        elif (not char2.is_hit and not char2.is_standing_up
                                and not char2.is_training_dead
                                and char2.name == 'gjl'
                                and gjl_code_img is not None):
                            if (char2.gjl_skill2_phase == 0
                                    and char2.skills['skill_2']['current_cd'] <= 0
                                    and char2.stamina >= char2.skill2_stamina_cost):
                                char2.stamina -= char2.skill2_stamina_cost
                                # 普通技能：按下立即进入冷却
                                char2.skills['skill_2']['current_cd'] = char2.skills['skill_2']['cd']
                                char2.gjl_skill2_phase = 1
                                char2.gjl_skill2_timer = GJL_SKILL2_WINDUP_FRAMES
                                # 【音效】触发技能二 -> 播 skill_2，seq +1
                                char2.skill2_trigger_seq += 1
                                play_sfx(char2.name, 'skill_2')
                            elif char2.gjl_skill2_phase == 2:
                                # 二段：让当前飞行的 code 就地分裂成两个垂直方向的曲线 code
                                _old_p = None
                                for _p in gjl_skill2_projectiles:
                                    if _p['owner_index'] == 1 and not _p['is_curved']:
                                        _old_p = _p
                                        break
                                if _old_p is not None:
                                    _px = _old_p['x']
                                    _py = _old_p['y']
                                    # 删除原 code
                                    gjl_skill2_projectiles[:] = [
                                        _p for _p in gjl_skill2_projectiles
                                        if not (_p['owner_index'] == 1 and not _p['is_curved'])]
                                    # 锁定此刻敌方位置
                                    if len(fight_chars) == 2:
                                        _opos = char1_pos
                                        _tx = float(_opos[0])
                                        _ty = float(_opos[1] - 60)
                                    else:
                                        _tx = _px
                                        _ty = _py
                                    _base = char2.gjl_skill2_origin_angle
                                    for _ang in (_base + math.pi / 2, _base - math.pi / 2):
                                        gjl_skill2_projectiles.append({
                                            'owner_index': 1,
                                            'x': float(_px),
                                            'y': float(_py),
                                            'angle': _ang,
                                            'speed': GJL_SKILL2_CODE2_SPEED,
                                            'damage': GJL_SKILL2_CODE2_DAMAGE,
                                            'is_curved': True,
                                            'track_live': False,
                                            'target_x': _tx,
                                            'target_y': _ty,
                                        })
                                    # 分裂完成后技能施放流程就结束了，phase 直接回 0。
                                    # 分裂出的小 code 继续独立飞行，不再阻塞技能二。
                                    # 存储技能模式下 CD 已由次数用尽控制，这里不再额外进 CD。
                                    char2.gjl_skill2_phase = 0
                        elif (not char2.is_hit and not char2.is_standing_up
                                and not char2.is_training_dead
                                and char2.skills['skill_2']['current_cd'] <= 0
                                and char2.pending_wyk_skill2_launch == 0
                                and not char2.wyk_skill2_active
                                and char2.pending_book_launch == 0
                                and char2.pending_skill2_launch == 0
                                and char2.name == 'wyk'
                                and char2.stamina >= char2.skill2_stamina_cost):
                            char2.stamina -= char2.skill2_stamina_cost
                            # wyk 技能二（声波）
                            char2.skills['skill_2']['current_cd'] = char2.skills['skill_2']['cd']
                            char2.pending_wyk_skill2_launch = max(1, int(char2.skills['skill_2']['recovery']))
                            char2.skill2_trigger_seq += 1
                            play_sfx(char2.name, 'skill_2')
                        elif (not char2.is_hit and not char2.is_standing_up
                                and not char2.is_training_dead
                                and char2.skills['skill_2']['current_cd'] <= 0
                                and char2.name == 'chr_1'
                                and chr1_book_img is not None
                                and char2.stamina >= char2.skill2_stamina_cost):
                            # chr_1 技能二（旋转散射书，普通技能）：
                            # 以角色朝向为基础角，旋转一整圈均匀发射 16 本书。
                            # 按下立即进入冷却。
                            char2.stamina -= char2.skill2_stamina_cost
                            char2.skills['skill_2']['current_cd'] = char2.skills['skill_2']['cd']
                            _c2_base_ang = 0.0 if char2.facing == 'right' else math.pi
                            _c2_s2_sx = float(char2_pos[0])
                            _c2_s2_sy = float(char2_pos[1] - 60)
                            for _k2 in range(CHR1_SKILL2_BOOK_COUNT):
                                _a2 = _c2_base_ang + _k2 * (2.0 * math.pi / CHR1_SKILL2_BOOK_COUNT)
                                chr1_skill2_books.append({
                                    'owner_index': 1,
                                    'x': _c2_s2_sx,
                                    'y': _c2_s2_sy,
                                    'angle': _a2,
                                    'angular_speed': CHR1_SKILL2_ANGULAR_SPEED,
                                    'damage': CHR1_SKILL2_DAMAGE,
                                    'age': 0,
                                })
                            char2.skill2_trigger_seq += 1
                            play_sfx(char2.name, 'skill_2')
                else:
                    char2.skill2_pressed = False
                if p2_extra:
                    # 边沿触发：只有从松到按才触发一次，避免按住不放反复开启/结束
                    if not char2.extra_pressed:
                        _ret = char2.attack_extra()
                        if _ret == 1:
                            # 隐身：原地冒白烟
                            spawn_smoke(char2_pos[0], char2_pos[1])
                        # _ret == 2 时技能 2 激活，震动由全局状态驱动
                        char2.extra_pressed = True
                else:
                    char2.extra_pressed = False

                if not char2.is_hit and not char2.is_standing_up:
                    if (char2.pending_wyk_skill2_launch > 0
                            and char2.name == 'wyk'):
                        # wyk 技能二前摇：用 skill_2_use/ 帧序列
                        _total = max(1, int(char2.skills['skill_2']['recovery']))
                        _use_frame = char2.get_skill_use_frame(
                            'skill_2_use', _total,
                            char2.pending_wyk_skill2_launch)
                        if _use_frame is not None:
                            _img = _use_frame
                            if char2.facing == 'left':
                                _img = get_flip(_img)
                            _rect = _img.get_rect(midbottom=(char2_pos[0], char2_pos[1]))
                            screen.blit(_img, _rect)
                        else:
                            char2.update("stand")
                            char2.draw("stand", char2_pos, "midbottom")
                    elif (char2.wyk_skill2_active
                            and char2.name == 'wyk'):
                        # wyk 技能二激活：用 skill_2_use/ 的 _数字 帧序列
                        _total = max(1, WYK_SKILL2_DURATION_FRAMES)
                        _use_frame = char2.get_skill_use_frame(
                            'skill_2_use_after', _total, char2.wyk_skill2_timer)
                        if _use_frame is not None:
                            _img = _use_frame
                            if char2.facing == 'left':
                                _img = get_flip(_img)
                            _rect = _img.get_rect(midbottom=(char2_pos[0], char2_pos[1]))
                            screen.blit(_img, _rect)
                        else:
                            char2.update("stand")
                            char2.draw("stand", char2_pos, "midbottom")
                    elif char2.gjl_skill2_phase == 1:
                        # gjl 技能二前摇：用 skill_2_use/ 帧序列
                        _total = max(1, GJL_SKILL2_WINDUP_FRAMES)
                        _use_frame = char2.get_skill_use_frame(
                            'skill_2_use', _total, char2.gjl_skill2_timer)
                        if _use_frame is not None:
                            _img = _use_frame
                            if char2.facing == 'left':
                                _img = get_flip(_img)
                            _rect = _img.get_rect(midbottom=(char2_pos[0], char2_pos[1]))
                            screen.blit(_img, _rect)
                        else:
                            char2.update("stand")
                            char2.draw("stand", char2_pos, "midbottom")
                    elif char2.gjl_skill1_phase != 0:
                        # gjl 技能一绘制（2P 版本）：
                        #   前摇阶段：用 skill_1_use/ 帧序列显示。
                        #   突进阶段：中间 2 帧显示 trade，其余帧画普通动画。
                        _gjl1_drawn = False
                        if char2.gjl_skill1_phase == 1:
                            _total = max(1, char2.gjl_skill1_total_frames)
                            _use_frame = char2.get_skill_use_frame(
                                'skill_1_use', _total, char2.gjl_skill1_timer)
                            if _use_frame is not None:
                                _img1 = _use_frame
                                if char2.facing == 'left':
                                    _img1 = get_flip(_img1)
                                _r1 = _img1.get_rect(midbottom=(char2_pos[0], char2_pos[1]))
                                screen.blit(_img1, _r1)
                                _gjl1_drawn = True
                        else:
                            _t = char2.gjl_skill1_timer
                            if 2 <= _t < 4 and gjl_trade_img is not None:
                                _img2 = gjl_trade_img
                                if char2.facing == 'left':
                                    _img2 = get_flip(_img2)
                                _r2 = _img2.get_rect(midbottom=(char2_pos[0], char2_pos[1]))
                                screen.blit(_img2, _r2)
                                _gjl1_drawn = True
                        if not _gjl1_drawn:
                            char2.update("stand")
                            char2.draw("stand", char2_pos, "midbottom")
                    elif char2.pending_book_launch > 0 or char2.pending_skill2_launch > 0:
                        # 技能前摇期间：角色被硬控，原地站立，不能移动 / 跳跃。
                        # 前摇图从 skill/skill_1_use/ 与 skill/skill_2_use/
                        # 子文件夹自动加载，按前摇总帧数分配每帧停留时间。
                        # 找不到图就回退到站立图。
                        _drawn = False
                        if char2.pending_book_launch > 0 and char2.name in ('chr', 'wyk'):
                            _total = max(1, int(char2.skills['skill_1']['recovery']))
                            _use_frame = char2.get_skill_use_frame(
                                'skill_1_use', _total, char2.pending_book_launch)
                            if _use_frame is not None:
                                img = _use_frame
                                if char2.facing == 'left':
                                    img = get_flip(img)
                                rect = img.get_rect(midbottom=(char2_pos[0], char2_pos[1]))
                                screen.blit(img, rect)
                                _drawn = True
                        elif char2.pending_skill2_launch > 0 and char2.name == 'chr':
                            _total = max(1, int(char2.skills['skill_2']['recovery']))
                            _use_frame = char2.get_skill_use_frame(
                                'skill_2_use', _total, char2.pending_skill2_launch)
                            if _use_frame is not None:
                                img = _use_frame
                                if char2.facing == 'left':
                                    img = get_flip(img)
                                rect = img.get_rect(midbottom=(char2_pos[0], char2_pos[1]))
                                screen.blit(img, rect)
                                _drawn = True
                        if not _drawn:
                            char2.update("stand")
                            char2.draw("stand", char2_pos, "midbottom")
                    elif not char2.is_attacking:
                        if p2_left:
                            char2_pos = char2.move(char2_pos, "left")
                        elif p2_right:
                            char2_pos = char2.move(char2_pos, "right")
                        else:
                            if char2.jump_phase == 0:
                                char2.update("stand")
                                char2.draw("stand", char2_pos, "midbottom")
                            elif char2.jump_phase == 1:
                                char2.update("jump")
                                char2.draw("jump", char2_pos, "midbottom")
                            else:
                                char2.draw("walk", char2_pos, "midbottom")
                    else:
                        if char2.attack_phase == 'anim':
                            char2.update(char2.attack_anim)
                            char2.draw(char2.attack_anim, char2_pos, "midbottom")
                        else:
                            char2.draw("stand", char2_pos, "midbottom")
                else:
                    char2.draw("stand", char2_pos, "midbottom")

                char2_pos = char2.update_physics(char2_pos)

                # 技能一前摇计时：被打断就取消，否则到点发射书本 / 蛋
                if char2.pending_book_launch > 0:
                    if char2.is_hit or char2.is_standing_up:
                        char2.pending_book_launch = 0
                    else:
                        char2.pending_book_launch -= 1
                        if char2.pending_book_launch == 0:
                            if char2.name == 'wyk' and wyk_egg_img is not None:
                                # wyk 技能一：扔蛋。发射时读取对手此刻
                                # 的矩形中心作为瞄准点，反推出一条能精确命中该
                                # 点的抛物线初速度。对手在此之后移动就会躲开。
                                _sx = char2_pos[0]
                                _sy = char2_pos[1] - 60
                                if len(fight_chars) >= 2:
                                    _t1_rect = fight_chars[0].get_rect(char1_pos)
                                    _tx = _t1_rect.centerx
                                    _ty = _t1_rect.centery
                                else:
                                    # 单机模式没有对手，往正前方飞
                                    _tx = _sx + (300 if char2.facing == 'right' else -300)
                                    _ty = _sy
                                _evx, _evy = calc_egg_velocity(_sx, _sy, _tx, _ty)
                                wyk_eggs.append({
                                    'owner_index': 1,
                                    'x': float(_sx),
                                    'y': float(_sy),
                                    'vx': _evx,
                                    'vy': _evy,
                                })
                            else:
                                sx = char2_pos[0]
                                sy = char2_pos[1] - 60
                                vx = BOOK_SPEED if char2.facing == 'right' else -BOOK_SPEED
                                book_projectiles.append({
                                    'owner_index': 1,
                                    'target_index': 0,
                                    'facing': char2.facing,
                                    'x': sx,
                                    'y': sy,
                                    'base_y': sy,
                                    'vx': vx,
                                    'phase': 0,
                                })

                # 技能二前摇计时：被打断就取消，否则到点投出 3 本书
                if char2.pending_skill2_launch > 0:
                    if char2.is_hit or char2.is_standing_up:
                        char2.pending_skill2_launch = 0
                    else:
                        char2.pending_skill2_launch -= 1
                        if char2.pending_skill2_launch == 0:
                            sy_center = char2_pos[1] - 60
                            vx = BOOK_SPEED if char2.facing == 'right' else -BOOK_SPEED
                            if chr_book_img is not None:
                                step = chr_book_img.get_height() + SKILL2_BOOK_VERTICAL_SPACING
                            else:
                                step = 40 + SKILL2_BOOK_VERTICAL_SPACING
                            half = (SKILL2_BOOKS_PER_THROW - 1) / 2
                            for k in range(SKILL2_BOOKS_PER_THROW):
                                offset = (k - half) * step
                                sy = sy_center + offset
                                book_projectiles.append({
                                    'owner_index': 1,
                                    'target_index': 0,
                                    'facing': char2.facing,
                                    'x': char2_pos[0],
                                    'y': sy,
                                    'base_y': sy,
                                    'vx': vx,
                                    'phase': 0,
                                    'damage': BOOK_DAMAGE_SKILL2,
                                })

                # wyk 技能二：前摇计时
                if char2.pending_wyk_skill2_launch > 0:
                    if char2.is_hit or char2.is_standing_up:
                        char2.pending_wyk_skill2_launch = 0
                    else:
                        char2.pending_wyk_skill2_launch -= 1
                        if char2.pending_wyk_skill2_launch == 0:
                            char2.wyk_skill2_active = True
                            char2.wyk_skill2_timer = WYK_SKILL2_DURATION_FRAMES
                            char2.wyk_skill2_hit = False
                            char2.wyk_skill2_last_hit_frame = -999
                            char2.wyk_skill2_shake_x = 0
                            char2.wyk_skill2_shake_y = 0

                # wyk 技能二：声波激活更新
                if char2.wyk_skill2_active:
                    if char2.is_hit or char2.is_standing_up:
                        char2.wyk_skill2_active = False
                        char2.wyk_skill2_timer = 0
                        char2.wyk_skill2_hit = False
                        char2.wyk_skill2_damage_override = None
                    else:
                        char2.wyk_skill2_timer -= 1
                        char2.wyk_skill2_shake_x = random.randint(
                            -WYK_SKILL2_VOICE_SHAKE, WYK_SKILL2_VOICE_SHAKE)
                        char2.wyk_skill2_shake_y = random.randint(
                            -WYK_SKILL2_VOICE_SHAKE, WYK_SKILL2_VOICE_SHAKE)
                        if len(fight_chars) == 2:
                            if (ui_frame_count - char2.wyk_skill2_last_hit_frame
                                    >= WYK_SKILL2_HIT_INTERVAL_FRAMES):
                                _v_r = get_wyk_skill2_voice_rect(
                                    char2, char2_pos,
                                    char2.wyk_skill2_shake_x,
                                    char2.wyk_skill2_shake_y)
                                _t_ch = fight_chars[0]
                                _t_r = _t_ch.get_rect(char1_pos)
                                if _v_r is not None and _v_r.colliderect(_t_r):
                                    # 隐藏技能：目标处于变身（被 wyk 一技能抓取）
                                    # 状态时，本次声波触发隐藏技能：
                                    #   伤害变为 832、本次二技能冷却 +10s、
                                    #   被控玩家持续冒白烟、10s 内暴击率 35%
                                    _dmg2 = WYK_SKILL2_DAMAGE
                                    if (_t_ch.wyk_egg_transformed
                                            and char2.wyk_skill2_damage_override is None):
                                        char2.wyk_skill2_damage_override = 832
                                        char2.skills['skill_2']['current_cd'] += int(10.0 * FPS)
                                        char2.wyk_hidden_boost_timer = int(10.0 * FPS)
                                        _t_ch.wyk_hidden_smoke = True
                                    if char2.wyk_skill2_damage_override is not None:
                                        _dmg2 = char2.wyk_skill2_damage_override
                                    _new_num = _t_ch.take_damage(
                                        _dmg2,
                                        char2.facing, char2_pos, char2)
                                    if _new_num is not None:
                                        _t_ch.hit_vy = WYK_SKILL2_HIT_VY
                                        _t_ch.hit_vx = 0
                                        # 兜底：对手底部不能高于声波中心上方 50 像素，
                                        # 否则下一次判定会打不到。
                                        _min_bottom_y = _v_r.centery - 50
                                        if char1_pos[1] < _min_bottom_y:
                                            char1_pos[1] = _min_bottom_y
                                        _new_num.x = char1_pos[0]
                                        _new_num.y = char1_pos[1] - 80
                                        damage_numbers.append(_new_num)
                                        shake.start()
                                        char2.wyk_skill2_hit = True
                                        char2.wyk_skill2_last_hit_frame = ui_frame_count
                        if char2.wyk_skill2_timer <= 0:
                            char2.wyk_skill2_active = False
                            char2.wyk_skill2_hit = False
                            char2.wyk_skill2_damage_override = None

            # ===== 技能 2 状态机更新 =====
            if len(fight_chars) >= 1:
                update_extra_skill2_state(fight_chars[0], 0)
            if len(fight_chars) == 2:
                update_extra_skill2_state(fight_chars[1], 1)

            # ===== gjl 技能一（幻影突进）状态机 =====
            # phase 1：前摇，两个图 alpha 由绘制分支控制，这里只走计时。
            # phase 2：突进，每帧向前位移并做一次命中判定。
            # 被打中或倒地时立即取消技能。
            if len(fight_chars) >= 1:
                _c = fight_chars[0]
                _cpos = char1_pos
                if _c.gjl_skill1_phase != 0:
                    if _c.is_hit or _c.is_standing_up or _c.is_training_dead:
                        _c.gjl_skill1_phase = 0
                        _c.gjl_skill1_timer = 0
                        _c.gjl_skill1_hit = False
                    elif _c.gjl_skill1_phase == 1:
                        _c.gjl_skill1_timer -= 1
                        if _c.gjl_skill1_timer <= 0:
                            _c.gjl_skill1_phase = 2
                            _c.gjl_skill1_timer = GJL_SKILL1_DASH_FRAMES
                            # 记录冲刺起点，用于叠加距离伤害
                            _c.gjl_skill1_start_x = _cpos[0]
                    elif _c.gjl_skill1_phase == 2:
                        _c.gjl_skill1_timer -= 1
                        _dir = 1 if _c.facing == 'right' else -1
                        _cpos[0] += GJL_SKILL1_DASH_SPEED * _dir
                        _gbl, _gbr = get_world_bounds()
                        _cpos[0] = max(_gbl, min(_gbr, _cpos[0]))
                        if (not _c.gjl_skill1_hit) and len(fight_chars) == 2:
                            _t = fight_chars[1]
                            _tpos = char2_pos
                            _tr = _t.get_rect(_tpos)
                            _cr = _c.get_rect(_cpos)
                            if _cr.colliderect(_tr):
                                # 叠加伤害：冲刺起点到命中点的水平距离 × 3
                                _dash_dist = abs(_cpos[0] - _c.gjl_skill1_start_x)
                                _total_dmg = GJL_SKILL1_DAMAGE + int(
                                    _dash_dist * GJL_SKILL1_DAMAGE_PER_PIXEL)
                                _new_num = _t.take_damage(
                                    _total_dmg, _c.facing, _cpos, _c)
                                # 只有真正造成伤害时才标记命中；
                                # 目标无敌时视为空击，突进继续判定。
                                if _new_num is not None:
                                    _new_num.x = _tpos[0]
                                    _new_num.y = _tpos[1] - 80
                                    damage_numbers.append(_new_num)
                                    shake.start()
                                    _c.gjl_skill1_hit = True
                        if _c.gjl_skill1_timer <= 0:
                            _c.gjl_skill1_phase = 0
                            _c.gjl_skill1_hit = False
            if len(fight_chars) == 2:
                _c = fight_chars[1]
                _cpos = char2_pos
                if _c.gjl_skill1_phase != 0:
                    if _c.is_hit or _c.is_standing_up or _c.is_training_dead:
                        _c.gjl_skill1_phase = 0
                        _c.gjl_skill1_timer = 0
                        _c.gjl_skill1_hit = False
                    elif _c.gjl_skill1_phase == 1:
                        _c.gjl_skill1_timer -= 1
                        if _c.gjl_skill1_timer <= 0:
                            _c.gjl_skill1_phase = 2
                            _c.gjl_skill1_timer = GJL_SKILL1_DASH_FRAMES
                            # 记录冲刺起点，用于叠加距离伤害
                            _c.gjl_skill1_start_x = _cpos[0]
                    elif _c.gjl_skill1_phase == 2:
                        _c.gjl_skill1_timer -= 1
                        _dir = 1 if _c.facing == 'right' else -1
                        _cpos[0] += GJL_SKILL1_DASH_SPEED * _dir
                        _gbl, _gbr = get_world_bounds()
                        _cpos[0] = max(_gbl, min(_gbr, _cpos[0]))
                        if (not _c.gjl_skill1_hit) and len(fight_chars) == 2:
                            _t = fight_chars[0]
                            _tpos = char1_pos
                            _tr = _t.get_rect(_tpos)
                            _cr = _c.get_rect(_cpos)
                            if _cr.colliderect(_tr):
                                # 叠加伤害：冲刺起点到命中点的水平距离 × 3
                                _dash_dist = abs(_cpos[0] - _c.gjl_skill1_start_x)
                                _total_dmg = GJL_SKILL1_DAMAGE + int(
                                    _dash_dist * GJL_SKILL1_DAMAGE_PER_PIXEL)
                                _new_num = _t.take_damage(
                                    _total_dmg, _c.facing, _cpos, _c)
                                # 只有真正造成伤害时才标记命中；
                                # 目标无敌时视为空击，突进继续判定。
                                if _new_num is not None:
                                    _new_num.x = _tpos[0]
                                    _new_num.y = _tpos[1] - 80
                                    damage_numbers.append(_new_num)
                                    shake.start()
                                    _c.gjl_skill1_hit = True
                        if _c.gjl_skill1_timer <= 0:
                            _c.gjl_skill1_phase = 0
                            _c.gjl_skill1_hit = False

            # ===== gjl 技能二状态机 =====
            # phase 1：前摇（走计时，结束时发射第一段 code）；
            # phase 2：第一段 code 飞行中，检查是否已经出屏（都出屏则进 CD）；
            # phase 3：第二段 code 飞行中，检查是否都出屏（都出屏则回 0）。
            for _oi in range(min(len(fight_chars), 2)):
                _c = fight_chars[_oi]
                if _c.gjl_skill2_phase == 1:
                    if _c.is_hit or _c.is_standing_up or _c.is_training_dead:
                        _c.gjl_skill2_phase = 0
                        _c.gjl_skill2_timer = 0
                    else:
                        _c.gjl_skill2_timer -= 1
                        if _c.gjl_skill2_timer <= 0:
                            _cpos = char1_pos if _oi == 0 else char2_pos
                            # 初始方向 = 玩家朝向（面向右=0，面向左=pi）
                            _angle = 0.0 if _c.facing == 'right' else math.pi
                            # 记录发射瞬间敌方位置，作为追踪目标；
                            # 之后的更新循环会每帧把 target 更新为敌方当前位置。
                            if len(fight_chars) == 2:
                                _opos = char2_pos if _oi == 0 else char1_pos
                                _tx = float(_opos[0])
                                _ty = float(_opos[1] - 60)
                            else:
                                _tx = float(_cpos[0])
                                _ty = float(_cpos[1] - 60)
                            gjl_skill2_projectiles.append({
                                'owner_index': _oi,
                                'x': float(_cpos[0]),
                                'y': float(_cpos[1] - 60),
                                'angle': _angle,
                                'speed': GJL_SKILL2_CODE_SPEED,
                                'damage': GJL_SKILL2_CODE_DAMAGE,
                                'is_curved': False,
                                'track_live': True,
                                'target_x': _tx,
                                'target_y': _ty,
                            })
                            _c.gjl_skill2_origin_angle = _angle
                            _c.gjl_skill2_phase = 2
                elif _c.gjl_skill2_phase == 2:
                    _has = any(p['owner_index'] == _oi
                               and not p['is_curved']
                               for p in gjl_skill2_projectiles)
                    if not _has:
                        # 存储技能模式下，CD 在次数用尽时已设置；
                        # code 出屏只清 phase，不再额外进 CD。
                        _c.gjl_skill2_phase = 0
                # phase==3 的分支已删除：分裂后 phase 直接回 0，
                # 不再依赖"所有小 code 都出屏"来判断技能结束。

            # ===== code 投射物更新与碰撞 =====
            # 第一段 code（track_live=True）：每帧把目标更新为敌方当前位置，
            #   从而不断修正方向追踪敌方。
            # 分裂出的小 code（is_curved=True）：目标固定在分裂时的位置，
            #   只朝那个固定点弯曲飞行。
            # 关键：先缓存双方角色的碰撞矩形。因为 get_rect 在角色进入
            # 受击状态后返回的是"倒地矩形"，若第一个 code 命中后目标进入
            # 受击状态，第二个 code 再用 get_rect 就拿到倒地矩形，可能对不上。
            # 缓存成帧开始时的矩形后，同一帧内多个 code 都能正常命中。
            _cached_target_rects = {}
            if len(fight_chars) >= 1:
                _cached_target_rects[0] = fight_chars[0].get_rect(char1_pos)
            if len(fight_chars) >= 2:
                _cached_target_rects[1] = fight_chars[1].get_rect(char2_pos)
            for _p in gjl_skill2_projectiles[:]:
                if _p.get('track_live', False) and len(fight_chars) == 2:
                    _ti = 1 - _p['owner_index']
                    _tpos = char1_pos if _ti == 0 else char2_pos
                    _p['target_x'] = float(_tpos[0])
                    _p['target_y'] = float(_tpos[1] - 60)
                if _p['is_curved'] or _p.get('track_live', False):
                    _ta = math.atan2(_p['target_y'] - _p['y'],
                                     _p['target_x'] - _p['x'])
                    _diff = (_ta - _p['angle'] + math.pi) % (2 * math.pi) - math.pi
                    _p['angle'] += _diff * GJL_SKILL2_CODE2_TURN
                _p['x'] += math.cos(_p['angle']) * _p['speed']
                _p['y'] += math.sin(_p['angle']) * _p['speed']
                if _p['x'] < -100 or _p['x'] > W + 100 or _p['y'] < -100 or _p['y'] > H + 100:
                    gjl_skill2_projectiles.remove(_p)
                    continue
                if len(fight_chars) < 2:
                    continue
                _ti = 1 - _p['owner_index']
                _t = fight_chars[_ti]
                _tpos = char1_pos if _ti == 0 else char2_pos
                _tr = _cached_target_rects.get(_ti)
                if _tr is None:
                    _tr = _t.get_rect(_tpos)
                if gjl_code_img is not None:
                    _bw = gjl_code_img.get_width()
                    _bh = gjl_code_img.get_height()
                else:
                    _bw, _bh = 40, 40
                _pr = pygame.Rect(0, 0, _bw, _bh)
                _pr.center = (int(_p['x']), int(_p['y']))
                if _pr.colliderect(_tr):
                    _atk = fight_chars[_p['owner_index']]
                    _new_num = _t.take_damage(
                        _p['damage'],
                        'right' if _p['x'] < _tpos[0] else 'left',
                        [_p['x'], _p['y']], _atk)
                    # 只有真正造成伤害时才移除 code；
                    # 目标无敌时视为空击，code 继续穿过。
                    if _new_num is not None:
                        _new_num.x = _tpos[0]
                        _new_num.y = _tpos[1] - 80
                        damage_numbers.append(_new_num)
                        shake.start()
                        gjl_skill2_projectiles.remove(_p)

            # ===== chr_1 技能一：追踪书更新与碰撞 =====
            for _p in chr1_track_books[:]:
                # 方向在发射瞬间已锁定，之后不再追踪敌方新位置（直线飞行）
                _p['age'] = _p.get('age', 0) + 1
                _p['x'] += math.cos(_p['angle']) * _p['speed']
                _p['y'] += math.sin(_p['angle']) * _p['speed']
                if (_p['x'] < -100 or _p['x'] > MAP_W + 100
                        or _p['y'] < -100 or _p['y'] > H + 100):
                    chr1_track_books.remove(_p)
                    continue
                # 存活满 10 秒后渐隐，渐隐结束移除（运动不停止）
                if _p['age'] >= CHR1_BOOK_LIFETIME_FRAMES + CHR1_BOOK_FADE_FRAMES:
                    chr1_track_books.remove(_p)
                    continue
                if len(fight_chars) < 2:
                    continue
                _ti = 1 - _p['owner_index']
                _t = fight_chars[_ti]
                _tpos = char1_pos if _ti == 0 else char2_pos
                _tr = _t.get_rect(_tpos)
                if chr1_book_img is not None:
                    _bw, _bh = chr1_book_img.get_size()
                else:
                    _bw, _bh = 30, 30
                _pr = pygame.Rect(0, 0, _bw, _bh)
                _pr.center = (int(_p['x']), int(_p['y']))
                if _pr.colliderect(_tr):
                    _atk = fight_chars[_p['owner_index']]
                    _new_num = _t.take_damage(
                        _p['damage'],
                        'right' if _p['x'] < _tpos[0] else 'left',
                        [_p['x'], _p['y']], _atk)
                    if _new_num is not None:
                        _new_num.x = _tpos[0]
                        _new_num.y = _tpos[1] - 80
                        damage_numbers.append(_new_num)
                        shake.start()
                        # 累计 book 命中次数（用于自动二技能触发）
                        _atk.chr1_book_hit_count += 1
                        chr1_track_books.remove(_p)

            # chr_1 自动二技能已删除

            # ===== chr_1 技能二：旋转散射书更新与碰撞 =====
            # 每帧角度加上角速度，位置按新角度前进，形成螺旋/旋涡轨迹。
            for _p in chr1_skill2_books[:]:
                _p['age'] = _p.get('age', 0) + 1
                _p['angle'] += _p['angular_speed']
                _p['x'] += math.cos(_p['angle']) * CHR1_SKILL2_BOOK_SPEED
                _p['y'] += math.sin(_p['angle']) * CHR1_SKILL2_BOOK_SPEED
                # 散射书是螺旋轨迹，可能绕圈跑到屏幕外很远再回来；
                # 所以把出界删除的范围扩大，避免 book 在屏幕附近就被误删。
                if (_p['x'] < -3000 or _p['x'] > MAP_W + 3000
                        or _p['y'] < -3000 or _p['y'] > H + 3000):
                    chr1_skill2_books.remove(_p)
                    continue
                # 存活满 10 秒后渐隐，渐隐结束移除（运动不停止）
                if _p['age'] >= CHR1_BOOK_LIFETIME_FRAMES + CHR1_BOOK_FADE_FRAMES:
                    chr1_skill2_books.remove(_p)
                    continue
                if len(fight_chars) < 2:
                    continue
                _ti = 1 - _p['owner_index']
                _t = fight_chars[_ti]
                _tpos = char1_pos if _ti == 0 else char2_pos
                _tr = _t.get_rect(_tpos)
                if chr1_book_img is not None:
                    _bw, _bh = chr1_book_img.get_size()
                else:
                    _bw, _bh = 30, 30
                _pr = pygame.Rect(0, 0, _bw, _bh)
                _pr.center = (int(_p['x']), int(_p['y']))
                if _pr.colliderect(_tr):
                    _atk = fight_chars[_p['owner_index']]
                    _new_num = _t.take_damage(
                        _p['damage'],
                        'right' if _p['x'] < _tpos[0] else 'left',
                        [_p['x'], _p['y']], _atk)
                    if _new_num is not None:
                        _new_num.x = _tpos[0]
                        _new_num.y = _tpos[1] - 80
                        damage_numbers.append(_new_num)
                        shake.start()
                        # 累计 book 命中次数（用于自动二技能触发）
                        _atk.chr1_book_hit_count += 1
                        chr1_skill2_books.remove(_p)

            # ===== 攻击判定 =====
            if len(fight_chars) == 2:
                if char1.is_attacking and char1.attack_phase == 'anim':
                    frames = char1._current_attack_frames()
                    if frames and not char1.has_delivered_hit:
                        hurt_frame = char1.get_current_hurt_frame()
                        if char1.current_frame >= hurt_frame:
                            rect1 = char1.get_rect(char1_pos)
                            rect2 = char2.get_rect(char2_pos)
                            if rect1.colliderect(rect2):
                                base = char1.get_current_damage()
                                # 隐藏技能：触发后 10s 内暴击率固定 35%
                                _c1_crit = 0.15
                                if char1.wyk_hidden_boost_timer > 0:
                                    _c1_crit = 0.35
                                if char1.attack_type == 'normal' and random.random() < _c1_crit:
                                    base *= random.uniform(1.05, 1.25)
                                base = int(round(base))

                                new_num = char2.take_damage(base, char1.facing, char1_pos, char1)
                                if new_num is not None:
                                    new_num.x = char2_pos[0]
                                    new_num.y = char2_pos[1] - 80
                                    damage_numbers.append(new_num)
                                    shake.start()

                                    # 音效已移除，只保留命中标记
                                    # （无敌目标返回 None，不算命中）。
                                    char1.has_delivered_hit = True

                if char2.is_attacking and char2.attack_phase == 'anim':
                    frames = char2._current_attack_frames()
                    if frames and not char2.has_delivered_hit:
                        hurt_frame = char2.get_current_hurt_frame()
                        if char2.current_frame >= hurt_frame:
                            rect1 = char1.get_rect(char1_pos)
                            rect2 = char2.get_rect(char2_pos)
                            if rect2.colliderect(rect1):
                                base = char2.get_current_damage()
                                # 隐藏技能：触发后 10s 内暴击率固定 35%
                                _c2_crit = 0.15
                                if char2.wyk_hidden_boost_timer > 0:
                                    _c2_crit = 0.35
                                if char2.attack_type == 'normal' and random.random() < _c2_crit:
                                    base *= random.uniform(1.05, 1.25)
                                base = int(round(base))

                                new_num = char1.take_damage(base, char2.facing, char2_pos, char2)
                                if new_num is not None:
                                    new_num.x = char1_pos[0]
                                    new_num.y = char1_pos[1] - 80
                                    damage_numbers.append(new_num)
                                    shake.start()

                                    # 音效已移除，只保留命中标记
                                    # （无敌目标返回 None，不算命中）。
                                    char2.has_delivered_hit = True

            # ===== 书本投射物更新与碰撞 =====
            for proj in book_projectiles[:]:
                proj['x'] += proj['vx']
                proj['phase'] += BOOK_WAVE_SPEED
                proj['y'] = proj['base_y'] + BOOK_WAVE_AMPLITUDE * math.sin(proj['phase'])

                # 飞出世界（MAP_W 宽）才移除，避免在世界坐标里
                # 提前消失，导致对方看到突然消失。
                if proj['x'] < -100 or proj['x'] > MAP_W + 100:
                    book_projectiles.remove(proj)
                    continue

                # 与目标碰撞检测
                # 单人模式下没有敌人，只判断飞出屏幕，跳过碰撞判定
                if len(fight_chars) < 2:
                    continue
                target_idx = proj['target_index']
                if target_idx >= len(fight_chars):
                    continue
                target = fight_chars[target_idx]
                target_pos = char1_pos if target_idx == 0 else char2_pos
                target_rect = target.get_rect(target_pos)

                if chr_book_img is not None:
                    bw = chr_book_img.get_width()
                    bh = chr_book_img.get_height()
                else:
                    bw, bh = 40, 40
                book_rect = pygame.Rect(0, 0, bw, bh)
                book_rect.center = (int(proj['x']), int(proj['y']))

                if book_rect.colliderect(target_rect):
                    attacker = fight_chars[proj['owner_index']]
                    new_num = target.take_damage(
                        proj.get('damage', BOOK_DAMAGE), proj['facing'],
                        [proj['x'], proj['y']], attacker
                    )
                    # 只有真正造成伤害时，才爆炸并移除书本；
                    # 目标处于无敌 / 起身无敌时视为空击，书本继续穿过。
                    if new_num is not None:
                        new_num.x = target_pos[0]
                        new_num.y = target_pos[1] - 80
                        damage_numbers.append(new_num)
                        shake.start()
                        spawn_explosion(proj['x'], proj['y'])
                        book_projectiles.remove(proj)

            # wyk 技能一：蛋投射物更新与碰撞
            for _egg in wyk_eggs[:]:
                _egg['vy'] += WYK_SKILL1_EGG_GRAVITY
                _egg['x'] += _egg['vx']
                _egg['y'] += _egg['vy']
                # 飞出世界（MAP_W 宽）才移除
                if _egg['x'] < -100 or _egg['x'] > MAP_W + 100:
                    wyk_eggs.remove(_egg)
                    continue
                # 碰地 -> 碎裂，生成粒子，移除
                if _egg['y'] >= 360:
                    spawn_egg_particles(_egg['x'], 360)
                    wyk_eggs.remove(_egg)
                    continue
                if len(fight_chars) < 2:
                    continue
                # 命中敌人 -> 变身
                _ti = 1 - _egg['owner_index']
                _t = fight_chars[_ti]
                _tpos = char1_pos if _ti == 0 else char2_pos
                _tr = _t.get_rect(_tpos)
                if wyk_egg_img is not None:
                    _ew, _eh = wyk_egg_img.get_size()
                else:
                    _ew, _eh = 30, 30
                _er = pygame.Rect(0, 0, _ew, _eh)
                _er.center = (int(_egg['x']), int(_egg['y']))
                if _er.colliderect(_tr):
                    # 目标处于无敌 / 起身无敌时视为空击：
                    # 蛋穿过去，不变身、不移除。
                    if not (_t.is_invincible or _t.is_stand_up_invincible):
                        # 变身：敌人进入变身状态，播放烟雾特效
                        _t.wyk_egg_transformed = True
                        _t.wyk_egg_timer = WYK_SKILL1_TRANSFORM_FRAMES
                        # 打断敌人当前所有动作
                        _t.is_attacking = False
                        _t.attack_phase = None
                        _t.attack_type = None
                        _t.attack_anim = None
                        _t.is_hit = False
                        _t.is_standing_up = False
                        _t.jump_phase = 0
                        _t.vy = 0
                        _t.pending_book_launch = 0
                        _t.pending_skill2_launch = 0
                        _t.extra_invis_active = False
                        spawn_smoke(_tpos[0], _tpos[1])
                        # 移除蛋（不生成蛋碎粒子，因为没碎）
                        wyk_eggs.remove(_egg)
                        shake.start()

            # wyk 技能一：变身计时更新（在战斗循环里统一处理）
            for _ei in range(min(len(fight_chars), 2)):
                _ec = fight_chars[_ei]
                if _ec.wyk_egg_transformed:
                    _ec.wyk_egg_timer -= 1
                    if _ec.wyk_egg_timer <= 0:
                        _ec.wyk_egg_transformed = False
                        _ec.wyk_egg_timer = 0
                        _ec.wyk_hidden_smoke = False
                        _epos = char1_pos if _ei == 0 else char2_pos
                        spawn_smoke(_epos[0], _epos[1])
                        # 施加 520 击飞伤害
                        if len(fight_chars) == 2:
                            _eatk = fight_chars[1 - _ei]
                            _eatkpos = char2_pos if _ei == 0 else char1_pos
                            _efacing = 'right' if _epos[0] > _eatkpos[0] else 'left'
                            _enum = _ec.take_damage(
                                WYK_SKILL1_REVERT_DAMAGE, _efacing, _eatkpos, _eatk)
                            if _enum is not None:
                                _enum.x = _epos[0]
                                _enum.y = _epos[1] - 80
                                damage_numbers.append(_enum)
                                shake.start()

            # wyk 技能一：蛋碎粒子更新与绘制
            for _ep in wyk_egg_particles[:]:
                _ep[0] += _ep[2]
                _ep[1] += _ep[3]
                _ep[3] += 0.3
                _ep[2] *= 0.97
                _ep[4] -= 1
                if _ep[4] <= 0:
                    wyk_egg_particles.remove(_ep)
                else:
                    _et = max(0.0, min(1.0, _ep[4] / 50.0))
                    _ea = int(255 * _et)
                    _esize = _ep[5]
                    _er_gb = _ep[6]
                    _eps = get_particle_surf(_er_gb[0], _er_gb[1], _er_gb[2],
                                             _esize, _ea)
                    screen.blit(_eps, (int(_ep[0] - _esize), int(_ep[1] - _esize)))

            # chr_1 技能一：环绕中的书碰到敌人 -> 造成伤害并消耗该本
            for _oi_orb in range(min(len(fight_chars), 2)):
                _orb_c = fight_chars[_oi_orb]
                if _orb_c.name != 'chr_1':
                    continue
                if (not _orb_c.chr1_skill1_activated
                        or _orb_c.chr1_skill1_uses_left <= 0):
                    continue
                if len(fight_chars) < 2:
                    continue
                _orb_pos = char1_pos if _oi_orb == 0 else char2_pos
                _orb_ti = 1 - _oi_orb
                _orb_t = fight_chars[_orb_ti]
                _orb_tpos = char1_pos if _orb_ti == 0 else char2_pos
                _orb_tr = _orb_t.get_rect(_orb_tpos)
                _orb_bw, _orb_bh = chr1_book_img.get_size()
                for _bi, _bx, _by in get_chr1_orbit_book_positions(
                        _orb_c, _orb_pos):
                    _orb_br = pygame.Rect(0, 0, _orb_bw, _orb_bh)
                    _orb_br.center = (int(_bx), int(_by))
                    if not _orb_br.colliderect(_orb_tr):
                        continue
                    _orb_facing = 'right' if _bx < _orb_tpos[0] else 'left'
                    _orb_num = _orb_t.take_damage(
                        CHR1_SKILL1_DAMAGE, _orb_facing,
                        [_bx, _by], _orb_c)
                    if _orb_num is None:
                        # 目标无敌：视为空击，书不消耗，继续环绕
                        break
                    # 累计 book 命中次数（用于自动二技能触发）
                    _orb_c.chr1_book_hit_count += 1
                    _orb_num.x = _orb_tpos[0]
                    _orb_num.y = _orb_tpos[1] - 80
                    damage_numbers.append(_orb_num)
                    shake.start()
                    # 只消耗碰到敌人的那一本书（用它的索引 _bi 定位）
                    if 0 <= _bi < 3:
                        _orb_c.chr1_orbit_books_alive[_bi] = False
                    _orb_c.chr1_skill1_uses_left = sum(
                        1 for _b in _orb_c.chr1_orbit_books_alive if _b)
                    if _orb_c.chr1_skill1_uses_left <= 0:
                        _orb_c.chr1_skill1_uses_left = 0
                        _orb_c.skills['skill_1']['current_cd'] = _orb_c.skills['skill_1']['cd']
                    # 每帧每角色最多消耗一本，避免同一帧多本重叠一起消耗
                    break
            # 绘制 chr_1 的环绕书：近侧（z>=0）在角色图层上面
            for _ci_orb in range(min(len(fight_chars), 2)):
                _c_orb = fight_chars[_ci_orb]
                if _c_orb.name == 'chr_1':
                    _pos_orb = char1_pos if _ci_orb == 0 else char2_pos
                    draw_chr1_orbit_books(screen, _c_orb, _pos_orb, 'front')

            # 绘制书本
            for proj in book_projectiles:
                if chr_book_img is not None:
                    img = chr_book_img
                    if proj['facing'] == 'left':
                        img = get_flip(img)
                    rect = img.get_rect(center=(int(proj['x']), int(proj['y'])))
                    screen.blit(img, rect)

            # 绘制 wyk 技能一的蛋
            for _egg in wyk_eggs:
                if wyk_egg_img is not None:
                    _eimg = wyk_egg_img
                    if _egg['vx'] < 0:
                        _eimg = get_flip(_eimg)
                    _rect = _eimg.get_rect(
                        center=(int(_egg['x']), int(_egg['y'])))
                    screen.blit(_eimg, _rect)

            # ===== 技能 2 武器更新与碰撞 =====
            for wep in skill2_2_projectiles[:]:
                wep['x'] += wep['vx']
                # 完全滑出屏幕后移除（留一点余量，避免刚出屏就被移除）
                if wep['x'] < -2000 or wep['x'] > W + 2000:
                    skill2_2_projectiles.remove(wep)
                    continue
                if len(fight_chars) < 2:
                    continue
                target_idx = wep['target_index']
                if target_idx >= len(fight_chars):
                    continue
                target = fight_chars[target_idx]
                target_pos = char1_pos if target_idx == 0 else char2_pos
                target_rect = target.get_rect(target_pos)
                if skill2_2_img is not None:
                    bw = skill2_2_img.get_width()
                    bh = skill2_2_img.get_height()
                else:
                    bw, bh = 80, 80
                wep_rect = pygame.Rect(0, 0, bw, bh)
                wep_rect.center = (int(wep['x']), int(wep['y']))
                if wep_rect.colliderect(target_rect):
                    # 0.1s 伤害间隔：两次命中之间至少 6 帧
                    if ui_frame_count - wep['last_hit_frame'] >= SKILL2_2_HIT_INTERVAL_FRAMES:
                        new_num = target.take_damage(
                            SKILL2_2_DAMAGE, wep['facing'], [wep['x'], wep['y']],
                            fight_chars[wep['owner_index']]
                        )
                        # 只有真正造成伤害时才记录命中时间；
                        # 目标无敌时视为空击，武器继续滑动。
                        if new_num is not None:
                            wep['last_hit_frame'] = ui_frame_count
                            new_num.x = target_pos[0]
                            new_num.y = target_pos[1] - 80
                            damage_numbers.append(new_num)
                            shake.start()

            # 绘制技能 2 武器
            for wep in skill2_2_projectiles:
                if skill2_2_img is not None:
                    _img = skill2_2_img
                    if wep['facing'] == 'left':
                        _img = get_flip(_img)
                    _r = _img.get_rect(center=(int(wep['x']), int(wep['y'])))
                    screen.blit(_img, _r)

            # 绘制 gjl 技能二的 code 投射物
            # 图片默认朝右（angle=0 不旋转），angle 越大顺时针旋转。
            # pygame.transform.rotate 逆时针为正，所以这里取 -degrees(angle)。
            if gjl_code_img is not None:
                for _p in gjl_skill2_projectiles:
                    _deg = -math.degrees(_p['angle'])
                    _rotated = pygame.transform.rotate(gjl_code_img, _deg)
                    _r = _rotated.get_rect(
                        center=(int(_p['x']), int(_p['y'])))
                    screen.blit(_rotated, _r)

            # 绘制 chr_1 技能一的追踪书（满 10 秒后渐隐，运动不停止）
            if chr1_book_img is not None:
                for _p in chr1_track_books:
                    _deg = -math.degrees(_p['angle'])
                    _rotated = pygame.transform.rotate(chr1_book_img, _deg)
                    _r = _rotated.get_rect(
                        center=(int(_p['x']), int(_p['y'])))
                    _age = _p.get('age', 0)
                    if _age >= CHR1_BOOK_LIFETIME_FRAMES:
                        _t = (_age - CHR1_BOOK_LIFETIME_FRAMES) / max(1, CHR1_BOOK_FADE_FRAMES)
                        _a = int(255 * max(0.0, 1.0 - _t))
                        if _a <= 0:
                            continue
                        _rotated = _rotated.copy()
                        _rotated.set_alpha(_a)
                    screen.blit(_rotated, _r)
            # 绘制 chr_1 技能二的旋转散射书（满 10 秒后渐隐，运动不停止）
            if chr1_book_img is not None:
                for _p in chr1_skill2_books:
                    _deg = -math.degrees(_p['angle'])
                    _rotated = pygame.transform.rotate(chr1_book_img, _deg)
                    _r = _rotated.get_rect(
                        center=(int(_p['x']), int(_p['y'])))
                    _age = _p.get('age', 0)
                    if _age >= CHR1_BOOK_LIFETIME_FRAMES:
                        _t = (_age - CHR1_BOOK_LIFETIME_FRAMES) / max(1, CHR1_BOOK_FADE_FRAMES)
                        _a = int(255 * max(0.0, 1.0 - _t))
                        if _a <= 0:
                            continue
                        _rotated = _rotated.copy()
                        _rotated.set_alpha(_a)
                    screen.blit(_rotated, _r)

            # wyk 技能二：声波绘制
            if wyk_skill2_voice_img is not None:
                for _i_w2 in range(min(len(fight_chars), 2)):
                    _ch_w2 = fight_chars[_i_w2]
                    if _ch_w2.name != 'wyk' or not _ch_w2.wyk_skill2_active:
                        continue
                    _p_w2 = char1_pos if _i_w2 == 0 else char2_pos
                    _r_w2 = get_wyk_skill2_voice_rect(
                        _ch_w2, _p_w2,
                        _ch_w2.wyk_skill2_shake_x,
                        _ch_w2.wyk_skill2_shake_y)
                    if _r_w2 is not None:
                        screen.blit(wyk_skill2_voice_img, _r_w2)

            # ===== 爆炸粒子更新与绘制 =====
            # 用 get_particle_surf 的缓存，避免每帧每粒子 new 一张 Surface
            for p in explosion_particles[:]:
                p[0] += p[2]
                p[1] += p[3]
                p[3] += 0.3
                p[2] *= 0.96
                p[4] -= 1
                if p[4] <= 0:
                    explosion_particles.remove(p)
                else:
                    t = max(0.0, min(1.0, p[4] / 45.0))
                    alpha = int(255 * t)
                    size = p[5]
                    r, g, b = p[6]
                    _ps = get_particle_surf(r, g, b, size, alpha)
                    screen.blit(_ps, (int(p[0] - size), int(p[1] - size)))

            # 更新伤害数字
            for num in damage_numbers[:]:
                num.update()
                if num.is_dead():
                    damage_numbers.remove(num)

            # ===== KO 检测 =====
            # 任意一方血量归零即进入 KO 结算阶段：
            # 冻结双方操作，切换到 FIGHT_KO 状态，由该状态负责播放
            # 击飞动画和之后的结算界面。
            if ko_phase == 0 and len(fight_chars) == 2:
                if training_mode:
                    # 训练模式：任意一方死亡都像被击飞一样落地，不结算
                    for _ci in range(2):
                        _c = fight_chars[_ci]
                        if _c.hp <= 0 and not _c.is_training_dead:
                            _c.is_training_dead = True
                            # 【音效】播放该角色的死亡音效
                            play_sfx(_c.name, 'dead')
                            if not _c.is_hit:
                                # 兜底：万一没有经过 take_damage，也让它进入受击状态
                                _c.is_hit = True
                                _c.hit_vx = 8 if _c.facing == "left" else -8
                                _c.hit_vy = -10
                                _c.hit_facing = _c.facing
                            _c._training_bounce_count = 0
                            _c.is_attacking = False
                            _c.attack_phase = None
                            _c.attack_type = None
                            _c.attack_anim = None
                elif fight_chars[0].hp <= 0:
                    ko_phase = 1
                    ko_timer = 0
                    ko_loser_index = 0
                    ko_loser_airborne = True
                    ko_bounce_count = 0
                    # 【音效】播放失败方角色的死亡音效
                    play_sfx(fight_chars[0].name, 'dead')
                    # 血溅粒子：在被击败玩家位置向四周喷射红色小像素
                    sx = char1_pos[0]
                    sy = char1_pos[1] - 40
                    for _ in range(150):
                        ang = random.uniform(0, math.pi * 2)
                        spd = random.uniform(2.5, 9.0)
                        blood_particles.append([
                            sx + random.uniform(-10, 10),
                            sy + random.uniform(-20, 20),
                            math.cos(ang) * spd,
                            math.sin(ang) * spd - random.uniform(1.5, 4.0),
                            random.randint(35, 60),
                            random.randint(2, 4),
                        ])
                    win = fight_chars[1]
                    win.is_attacking = False
                    win.attack_phase = None
                    win.attack_type = None
                    win.attack_anim = None
                    win.current_frame = 0
                    win.frame_timer = 0
                    win.is_hit = False
                    win.is_standing_up = False
                    # KO 结算阶段会绘制双方角色，若此时有人处于隐身
                    # 状态会因透明度为 0 而看不见。这里直接清掉双方的隐身。
                    win.extra_invis_active = False
                    win.extra_invis_timer = 0
                    win.extra_invis_phase = 0
                    fight_chars[0].extra_invis_active = False
                    fight_chars[0].extra_invis_timer = 0
                    fight_chars[0].extra_invis_phase = 0
                    chr1_aura_particles.clear()
                    STATE = FIGHT_KO
                elif fight_chars[1].hp <= 0:
                    ko_phase = 1
                    ko_timer = 0
                    ko_loser_index = 1
                    ko_loser_airborne = True
                    ko_bounce_count = 0
                    # 【音效】播放失败方角色的死亡音效
                    play_sfx(fight_chars[1].name, 'dead')
                    # 血溅粒子：在被击败玩家位置向四周喷射红色小像素
                    sx = char2_pos[0]
                    sy = char2_pos[1] - 40
                    for _ in range(150):
                        ang = random.uniform(0, math.pi * 2)
                        spd = random.uniform(2.5, 9.0)
                        blood_particles.append([
                            sx + random.uniform(-10, 10),
                            sy + random.uniform(-20, 20),
                            math.cos(ang) * spd,
                            math.sin(ang) * spd - random.uniform(1.5, 4.0),
                            random.randint(35, 60),
                            random.randint(2, 4),
                        ])
                    win = fight_chars[0]
                    win.is_attacking = False
                    win.attack_phase = None
                    win.attack_type = None
                    win.attack_anim = None
                    win.current_frame = 0
                    win.frame_timer = 0
                    win.is_hit = False
                    win.is_standing_up = False
                    # KO 结算阶段会绘制双方角色，若此时有人处于隐身
                    # 状态会因透明度为 0 而看不见。这里直接清掉双方的隐身。
                    win.extra_invis_active = False
                    win.extra_invis_timer = 0
                    win.extra_invis_phase = 0
                    fight_chars[1].extra_invis_active = False
                    fight_chars[1].extra_invis_timer = 0
                    fight_chars[1].extra_invis_phase = 0
                    chr1_aura_particles.clear()
                    STATE = FIGHT_KO

            # 隐藏技能：被控玩家身上持续冒白烟
            for _si in range(min(len(fight_chars), 2)):
                _sc = fight_chars[_si]
                if _sc.wyk_hidden_smoke:
                    if ui_frame_count % 15 == 0:
                        _spos = char1_pos if _si == 0 else char2_pos
                        for _ in range(8):
                            _sang = random.uniform(0, math.pi * 2)
                            _sspd = random.uniform(0.5, 2.5)
                            smoke_particles.append([
                                _spos[0] + random.uniform(-20, 20),
                                _spos[1] - 20 + random.uniform(-40, 0),
                                math.cos(_sang) * _sspd,
                                math.sin(_sang) * _sspd - random.uniform(0.5, 2.0),
                                random.randint(30, 60),
                                random.randint(4, 9),
                            ])

            # 更新并绘制白烟粒子（提前到伤害数字之前，让白烟也在 night cover 之下）
            for p in smoke_particles[:]:
                p[0] += p[2]
                p[1] += p[3]
                p[3] -= 0.05        # 白烟轻微上飘
                p[2] *= 0.97        # 空气阻力
                p[3] *= 0.97
                p[4] -= 1
                if p[4] <= 0:
                    smoke_particles.remove(p)
                else:
                    t = p[4] / 60.0
                    a = int(180 * t)
                    size = p[5]
                    screen.blit(get_smoke_surf(size, a),
                                (int(p[0] - size), int(p[1] - size)))

            # chr_1 环绕红色粒子：生成 + 更新 + 绘制
            for _ci_aura in range(min(len(fight_chars), 2)):
                _c_aura = fight_chars[_ci_aura]
                if _c_aura.name == 'chr_1':
                    _pos_aura = char1_pos if _ci_aura == 0 else char2_pos
                    if random.random() < 0.5:
                        spawn_chr1_aura_particle(_c_aura, _pos_aura)
            update_and_draw_chr1_aura(screen)

            # night 地图：在角色和所有技能特效之上，伤害数字/UI 之下盖一层 cover.png
            draw_night_cover()

            # 绘制伤害数字
            for num in damage_numbers:
                num.draw(screen)

            # 震屏
            shake.apply()

        # 世界绘制结束：按相机偏移 blit 到真正的屏幕
        screen = _cam_real_screen
        screen.fill((0, 0, 0))
        screen.blit(_cam_world_surf, (-int(camera_x), 0))

        # ===== 战斗UI（HUD + 图标 + 返回按钮）=====
        # 只在战斗已经真正开始时绘制，并随 ui_slide_timer 从边缘滑入。
        if not intro_running:
            ui_slide = ui_slide_timer / UI_SLIDE_FRAMES
            ease = 1.0 - (1.0 - ui_slide) ** 3
            off = 1.0 - ease
            # 底部元素（技能图标、返回按钮）从底部滑入
            icon_dy = int(150 * off)

            # HUD
            char1_hud = fight_chars[0] if len(fight_chars) >= 1 else None
            char2_hud = fight_chars[1] if len(fight_chars) == 2 else None
            draw_hud(char1_hud, char2_hud, ui_slide)

            # 技能图标
            # 起始位置向外移动，为新增的第五个技能按钮（额外携带技能）预留空间
            spacing = 70
            start_x_left = 45
            start_x_right = W - 45
            y_pos = H - 50 + icon_dy

            p1_keys = [key_display_name(keymap['p1_attack']),
                       key_display_name(keymap['p1_escape']),
                       key_display_name(keymap['p1_skill1']),
                       key_display_name(keymap['p1_skill2']),
                       key_display_name(keymap['p1_extra'])]
            p2_keys = [key_display_name(keymap['p2_attack']),
                       key_display_name(keymap['p2_escape']),
                       key_display_name(keymap['p2_skill1']),
                       key_display_name(keymap['p2_skill2']),
                       key_display_name(keymap['p2_extra'])]
            skill_order = ['normal', 'escape', 'skill_1', 'skill_2', 'extra']

            # 联机时只显示自己的技能；本地双人显示双方
            # 联机主机根据 network_is_1p 决定显示哪一侧
            _show_p1 = True
            _show_p2 = True
            if network_connected:
                if network_role == "host":
                    if network_is_1p:
                        _show_p2 = False
                    else:
                        _show_p1 = False
                elif network_role == "client":
                    _show_p1 = False
                    _show_p2 = False

            _lan_icon_pos = {
                'normal': (50, H - 50 + icon_dy),
                'skill_1': (120, H - 50 + icon_dy),
                'skill_2': (190, H - 50 + icon_dy),
                'escape': (120, H - 120 + icon_dy),
                'extra': (190, H - 120 + icon_dy),
            }
            if _show_p1 and len(fight_chars) >= 1:
                char1 = fight_chars[0]
                for i, skill_name in enumerate(skill_order):
                    img = char1.icons.get(skill_name)
                    if img is None:
                        continue
                    if network_connected:
                        _cx, _cy = _lan_icon_pos[skill_name]
                        icon = SkillIcon(img, (_cx, _cy), key_label=p1_keys[i])
                    else:
                        x = start_x_left + i * spacing
                        icon = SkillIcon(img, (x, y_pos), key_label=p1_keys[i])
                    is_cooling = char1.skills[skill_name]['current_cd'] > 0
                    cd_remaining_seconds = char1.skills[skill_name]['current_cd'] / FPS
                    overlay_text = None
                    overlay_color = (255, 255, 255)
                    # 逃脱：只有受击（倒地 / 被击飞）时才亮起可用；
                    # 其他状态盖遮罩表示不可用（不算冷却，不显示数字）。
                    if (skill_name == 'escape'
                            and not (char1.is_hit or char1.is_standing_up)):
                        is_cooling = True
                        cd_remaining_seconds = 0
                    # 体力不足：技能不可用，灰色遮罩，不显示冷却数字
                    _st_cost = 0
                    if skill_name == 'escape':
                        _st_cost = ESCAPE_STAMINA_COST
                    elif skill_name == 'skill_1':
                        if char1.name == 'gjl' and char1.gjl_skill1_uses_left < GJL_SKILL1_STORE_COUNT:
                            _st_cost = 0
                        elif char1.name == 'chr_1' and char1.chr1_skill1_activated:
                            _st_cost = 0
                        else:
                            _st_cost = char1.skill1_stamina_cost
                    elif skill_name == 'skill_2':
                        if char1.name == 'chr' and char1.skill2_uses_left < SKILL2_THROW_COUNT:
                            _st_cost = 0
                        else:
                            _st_cost = char1.skill2_stamina_cost
                    if _st_cost > 0 and char1.stamina < _st_cost:
                        is_cooling = True
                        cd_remaining_seconds = 0
                        overlay_text = None
                    # 技能二的 3/3 计数是 chr 存储技能专属
                    if (skill_name == 'skill_2' and char1.name == 'chr'
                            and char1.skill2_uses_left > 0 and not is_cooling):
                        overlay_text = f"{char1.skill2_uses_left}/{SKILL2_THROW_COUNT}"
                        overlay_color = (255, 255, 0)
                    # gjl 技能一改为存储技能：显示剩余次数
                    if (skill_name == 'skill_1' and char1.name == 'gjl'
                            and char1.gjl_skill1_uses_left > 0 and not is_cooling):
                        overlay_text = f"{char1.gjl_skill1_uses_left}/{GJL_SKILL1_STORE_COUNT}"
                        overlay_color = (255, 255, 0)
                    # chr_1 技能一（存储技能）：激活后才显示剩余本数
                    if (skill_name == 'skill_1' and char1.name == 'chr_1'
                            and char1.chr1_skill1_activated
                            and char1.chr1_skill1_uses_left > 0 and not is_cooling):
                        overlay_text = f"{char1.chr1_skill1_uses_left}/{CHR1_SKILL1_STORE_COUNT}"
                        overlay_color = (255, 255, 0)
                    # (gjl 技能二已改为普通技能，不再显示剩余次数)
                    if skill_name == 'extra' and char1.extra_invis_active:
                        overlay_text = f"{char1.extra_invis_timer / FPS:.1f}"
                        overlay_color = (255, 255, 0)
                    icon.draw(mouse_pos, is_cooling, cd_remaining_seconds, overlay_text, overlay_color)

            if _show_p2 and len(fight_chars) == 2:
                char2 = fight_chars[1]
                # 局域网下本机 2P 也用 p1 键位（WASD），键位标签同步
                if network_connected:
                    _p2_label_keys = p1_keys
                else:
                    _p2_label_keys = p2_keys
                for i, skill_name in enumerate(skill_order):
                    img = char2.icons.get(skill_name)
                    if img is None:
                        continue
                    if network_connected:
                        _cx, _cy = _lan_icon_pos[skill_name]
                        icon = SkillIcon(img, (_cx, _cy), key_label=_p2_label_keys[i])
                    else:
                        x = start_x_right - (len(skill_order) - 1) * spacing + i * spacing
                        icon = SkillIcon(img, (x, y_pos), key_label=_p2_label_keys[i])
                    is_cooling = char2.skills[skill_name]['current_cd'] > 0
                    cd_remaining_seconds = char2.skills[skill_name]['current_cd'] / FPS
                    overlay_text = None
                    overlay_color = (255, 255, 255)
                    # 逃脱：只有受击（倒地 / 被击飞）时才亮起可用；
                    # 其他状态盖遮罩表示不可用（不算冷却，不显示数字）。
                    if (skill_name == 'escape'
                            and not (char2.is_hit or char2.is_standing_up)):
                        is_cooling = True
                        cd_remaining_seconds = 0
                    # 体力不足：技能不可用，灰色遮罩，不显示冷却数字
                    _st_cost = 0
                    if skill_name == 'escape':
                        _st_cost = ESCAPE_STAMINA_COST
                    elif skill_name == 'skill_1':
                        if char2.name == 'gjl' and char2.gjl_skill1_uses_left < GJL_SKILL1_STORE_COUNT:
                            _st_cost = 0
                        elif char2.name == 'chr_1' and char2.chr1_skill1_activated:
                            _st_cost = 0
                        else:
                            _st_cost = char2.skill1_stamina_cost
                    elif skill_name == 'skill_2':
                        if char2.name == 'chr' and char2.skill2_uses_left < SKILL2_THROW_COUNT:
                            _st_cost = 0
                        else:
                            _st_cost = char2.skill2_stamina_cost
                    if _st_cost > 0 and char2.stamina < _st_cost:
                        is_cooling = True
                        cd_remaining_seconds = 0
                        overlay_text = None
                    if (skill_name == 'skill_2' and char2.name == 'chr'
                            and char2.skill2_uses_left > 0 and not is_cooling):
                        overlay_text = f"{char2.skill2_uses_left}/{SKILL2_THROW_COUNT}"
                        overlay_color = (255, 255, 0)
                    # gjl 技能一改为存储技能：显示剩余次数
                    if (skill_name == 'skill_1' and char2.name == 'gjl'
                            and char2.gjl_skill1_uses_left > 0 and not is_cooling):
                        overlay_text = f"{char2.gjl_skill1_uses_left}/{GJL_SKILL1_STORE_COUNT}"
                        overlay_color = (255, 255, 0)
                    # chr_1 技能一（存储技能）：激活后才显示剩余本数
                    if (skill_name == 'skill_1' and char2.name == 'chr_1'
                            and char2.chr1_skill1_activated
                            and char2.chr1_skill1_uses_left > 0 and not is_cooling):
                        overlay_text = f"{char2.chr1_skill1_uses_left}/{CHR1_SKILL1_STORE_COUNT}"
                        overlay_color = (255, 255, 0)
                    # (gjl 技能二已改为普通技能，不再显示剩余次数)
                    if skill_name == 'extra' and char2.extra_invis_active:
                        overlay_text = f"{char2.extra_invis_timer / FPS:.1f}"
                        overlay_color = (255, 255, 0)
                    icon.draw(mouse_pos, is_cooling, cd_remaining_seconds, overlay_text, overlay_color)

            # 返回按钮（从底部滑入）
            _fight_show_back = (
                len(fight_chars) == 1 or training_mode
                or (not network_connected and len(fight_chars) == 2)
            )
            if _fight_show_back:
                original_center = btn_back.rect.center
                btn_back.rect.center = (W // 2, H - 50 + icon_dy)
                btn_back.draw(mouse_pos)
                btn_back.rect.center = original_center

            # 联机主机：退出房间按钮固定在右下角（技能图标统一在左下角）
            if (network_connected and network_role == "host"
                    and fight_intro_phase == FIGHT_INTRO_DONE):
                _exit_draw_c = (W - 85, H - 50 + icon_dy)
                _orig_exit_draw_c = btn_exit_room.rect.center
                btn_exit_room.rect.center = _exit_draw_c
                btn_exit_room.draw(mouse_pos)
                btn_exit_room.rect.center = _orig_exit_draw_c

            # 单人模式 / 训练模式：右侧按钮（CD / 设置 / 回满血）
            if len(fight_chars) == 1 or training_mode:
                if training_mode:
                    # 训练模式：三个按钮（设置 / CD / 回满血），从上到下
                    # CD 开关
                    _cd_hover = TRAINING_CD_TOGGLE_RECT.collidepoint(mouse_pos)
                    if cd_enabled:
                        _cd_bg = (30, 120, 30)
                        _cd_bg_h = (60, 170, 60)
                        _cd_fg = (255, 255, 255)
                        _cd_label = "CD 开启"
                    else:
                        _cd_bg = (60, 60, 60)
                        _cd_bg_h = (100, 100, 100)
                        _cd_fg = (255, 220, 0)
                        _cd_label = "CD 关闭"
                    pygame.draw.rect(screen, _cd_bg_h if _cd_hover else _cd_bg, TRAINING_CD_TOGGLE_RECT)
                    pygame.draw.rect(screen, (255, 255, 255), TRAINING_CD_TOGGLE_RECT, 2)
                    _cd_txt = get_font(20).render(_cd_label, True, _cd_fg)
                    _cd_rect = _cd_txt.get_rect(center=TRAINING_CD_TOGGLE_RECT.center)
                    screen.blit(_cd_txt, _cd_rect)
                    # 设置按钮
                    _set_hover = TRAINING_SETTINGS_BTN_RECT.collidepoint(mouse_pos)
                    pygame.draw.rect(screen, (100, 100, 150) if _set_hover else (60, 60, 100),
                                     TRAINING_SETTINGS_BTN_RECT)
                    pygame.draw.rect(screen, (255, 255, 255), TRAINING_SETTINGS_BTN_RECT, 2)
                    _set_txt = get_font(20).render("设置", True, (255, 255, 255))
                    _set_rect = _set_txt.get_rect(center=TRAINING_SETTINGS_BTN_RECT.center)
                    screen.blit(_set_txt, _set_rect)
                    # 回满血按钮
                    _heal_hover = TRAINING_HEAL_BTN_RECT.collidepoint(mouse_pos)
                    pygame.draw.rect(screen, (90, 180, 90) if _heal_hover else (60, 130, 60),
                                     TRAINING_HEAL_BTN_RECT)
                    pygame.draw.rect(screen, (255, 255, 255), TRAINING_HEAL_BTN_RECT, 2)
                    _heal_txt = get_font(20).render("回满血", True, (255, 255, 255))
                    _heal_rect = _heal_txt.get_rect(center=TRAINING_HEAL_BTN_RECT.center)
                    screen.blit(_heal_txt, _heal_rect)
                else:
                    # 单人模式：CD 开关 + 设置按钮
                    if cd_enabled:
                        _cd_bg = (30, 120, 30)
                        _cd_fg = (255, 255, 255)
                        _cd_label = "CD 开启"
                    else:
                        _cd_bg = (60, 60, 60)
                        _cd_fg = (255, 220, 0)
                        _cd_label = "CD 关闭"
                    pygame.draw.rect(screen, _cd_bg, CD_TOGGLE_RECT)
                    pygame.draw.rect(screen, (255, 255, 255), CD_TOGGLE_RECT, 2)
                    _cd_txt = get_font(22).render(_cd_label, True, _cd_fg)
                    _cd_rect = _cd_txt.get_rect(center=CD_TOGGLE_RECT.center)
                    screen.blit(_cd_txt, _cd_rect)
                    pygame.draw.rect(screen, (60, 60, 100), SOLO_SETTINGS_BTN_RECT)
                    pygame.draw.rect(screen, (255, 255, 255), SOLO_SETTINGS_BTN_RECT, 2)
                    _set_txt = get_font(22).render("设置", True, (255, 255, 255))
                    _set_rect = _set_txt.get_rect(center=SOLO_SETTINGS_BTN_RECT.center)
                    screen.blit(_set_txt, _set_rect)

            # 联机表情 UI（icon 按钮 + 气泡面板 + 已发出的表情气泡）
            draw_emoji_ui(mouse_pos)

        # ===== 技能 2 屏幕震动：整屏随机偏移 =====
        # 在 UI 绘制之后应用，这样 UI 也跟着一起抖。
        if any_skill2_2_active():
            _shk_surf = screen.copy()
            screen.fill((0, 0, 0))
            _ox = random.randint(-SKILL2_2_SHAKE_OFFSET, SKILL2_2_SHAKE_OFFSET)
            _oy = random.randint(-SKILL2_2_SHAKE_OFFSET, SKILL2_2_SHAKE_OFFSET)
            screen.blit(_shk_surf, (_ox, _oy))

    elif STATE == FIGHT_KO:
        # 联机客户端：只绘制（状态由主机同步）
        if network_connected and network_role == "client":
            draw_client_fight_view(mouse_pos)
            # 客户端在这里 continue，跳过了主循环末尾的延迟显示，单独补上
            if network_rtt_ms > 0:
                _rtt_text = f"延迟 {network_rtt_ms} ms"
            else:
                _rtt_text = "延迟 -- ms"
            _rf = get_font(18)
            _rsurf = _rf.render(_rtt_text, True, (255, 240, 120))
            _rrect = _rsurf.get_rect(midtop=(W // 2, 4))
            _rbg = pygame.Rect(_rrect.x - 8, _rrect.y - 2, _rrect.w + 16, _rrect.h + 4)
            pygame.draw.rect(screen, (20, 20, 20), _rbg)
            pygame.draw.rect(screen, (180, 180, 180), _rbg, 1)
            screen.blit(_rsurf, _rrect)
            pygame.display.flip()
            clock.tick(FPS)
            continue
        # KO 结算：先播放约 5 秒的慢动作击飞动画，随后覆盖结算界面。
        # 期间双方操作全部冻结，只有返回按钮在结算界面出现后可点击。
        # 主机在 FIGHT_KO 期间也持续发送状态，让客户端的 KO 动画同步。
        # 每帧都发 STATE，让客户端攻击动画更流畅。
        if network_connected and network_role == "host":
            try:
                net_send_to_peer("STATE:" + json.dumps(build_network_state(), separators=(',', ':')))
            except Exception:
                pass
        # ===== 相机计算（KO 阶段，只对局域网联机生效）=====
        if network_connected:
            if ko_loser_index == 0:
                _cam_tx = char1_pos[0]
            else:
                _cam_tx = char2_pos[0]
            _target_cam = max(0, min(MAP_W - W, _cam_tx - W // 2))
            camera_x += (_target_cam - camera_x) * CAMERA_LERP
            if abs(_target_cam - camera_x) < 0.5:
                camera_x = _target_cam
        else:
            # 本地 / 训练 / 单人：镜头锁定地图中央，屏幕显示世界
            # 100~900 区域，视觉上与 800 宽地图时完全一致。
            camera_x = MAP_W // 2 - W // 2
        _cam_real_screen = screen
        _cam_world_surf = pygame.Surface((MAP_W, H))
        screen = _cam_world_surf

        screen.fill((0, 0, 0))
        draw_map_background(catch_map_json["name"], map_img)

        ko_timer += 1

        # 判断被击飞一方和站立一方的位置引用
        if ko_loser_index == 0:
            loser_pos = char1_pos
            winner_pos = char2_pos
        else:
            loser_pos = char2_pos
            winner_pos = char1_pos

        loser_char = fight_chars[ko_loser_index]
        winner_char = fight_chars[1 - ko_loser_index]

        # 慢动作击飞物理：重力、水平速度都按系数缩小，让整个被击飞
        # 动画被拉长；落地后保持在倒地图像上，不再播放起身动画。
        if ko_loser_airborne:
            loser_char.hit_vy += loser_char.hit_gravity * KO_FALL_GRAVITY_SCALE
            loser_pos[0] += loser_char.hit_vx * 0.4
            loser_pos[1] += loser_char.hit_vy
            # 水平方向用倒地图像的实际宽度限制边界，避免角色长时间
            # 伸出屏幕外很远才被弹回。
            if loser_char.fall_img is not None:
                half_w = loser_char.fall_img.get_width() // 2
            else:
                half_w = 30
            _kbl, _kbr = get_world_bounds()
            left_bound = _kbl + half_w
            right_bound = _kbr - half_w
            if loser_pos[0] < left_bound:
                loser_pos[0] = left_bound
                loser_char.hit_vx = -loser_char.hit_vx * 0.5
            elif loser_pos[0] > right_bound:
                loser_pos[0] = right_bound
                loser_char.hit_vx = -loser_char.hit_vx * 0.5
            if loser_pos[1] >= loser_char.ground_y:
                loser_pos[1] = loser_char.ground_y
                # 落地反弹：下落速度够大时反向弹起，弹起高度逐渐减小
                if ko_bounce_count < KO_MAX_BOUNCES and abs(loser_char.hit_vy) > KO_BOUNCE_MIN_SPEED:
                    loser_char.hit_vy = -abs(loser_char.hit_vy) * KO_BOUNCE_DECAY
                    loser_char.hit_vx *= KO_BOUNCE_DECAY
                    ko_bounce_count += 1
                else:
                    loser_char.hit_vy = 0
                    loser_char.hit_vx = 0
                    ko_loser_airborne = False

        # 绘制被击飞一方：始终使用倒地图像，不再站起
        if loser_char.fall_img is not None:
            img = loser_char.fall_img
            if loser_char.hit_facing == "right":
                img = get_flip(img)
            rect = img.get_rect()
            rect.midbottom = (loser_pos[0], loser_pos[1])
            screen.blit(img, rect)
        else:
            loser_char.draw("stand", loser_pos, "midbottom")

        # 绘制获胜一方（保持站立）
        winner_char.update("stand")
        winner_char.draw("stand", winner_pos, "midbottom")

        # 更新并绘制血溅粒子
        blood_ground_y = loser_char.ground_y
        for p in blood_particles[:]:
            p[0] += p[2]
            p[1] += p[3]
            p[3] += 0.35           # 重力
            p[2] *= 0.98           # 空气阻力
            # 触地反弹：下落过程中碰到地面就反向弹起，速度衰减
            if p[1] >= blood_ground_y and p[3] > 0:
                p[1] = blood_ground_y
                p[3] = -p[3] * 0.45
                p[2] *= 0.7
            p[4] -= 1
            if p[4] <= 0:
                blood_particles.remove(p)
            else:
                # 生命值越低越暗，模拟血色渐渐干掉
                t = p[4] / 60.0
                r = min(255, int(180 + 75 * t))
                pygame.draw.rect(screen, (r, 0, 0), (int(p[0]), int(p[1]), p[5], p[5]))

        # night 地图：在角色和血溅之上，结算界面之下盖一层 cover.png
        draw_night_cover()

        # 伤害数字：KO 后继续更新并绘制，让它们正常淡出消失
        for num in damage_numbers[:]:
            num.update()
            num.draw(screen)
            if num.is_dead():
                damage_numbers.remove(num)

        # 世界绘制结束：按相机偏移 blit 到真正的屏幕
        screen = _cam_real_screen
        screen.fill((0, 0, 0))
        screen.blit(_cam_world_surf, (-int(camera_x), 0))

        # 联机时：结算界面出现后再等 3 秒，超时视作本机已确认。
        # 仍需要双方都确认才切回准备页（由主机判定）。
        if ko_timer >= KO_ANIM_FRAMES + 180:
            if network_connected:
                # 超时：本机自己退出到准备界面，对对方无影响
                STATE = LAN_READY
                network_local_ready = False
                network_remote_ready = False
                ko_phase = 0
                ko_timer = 0
                network_ko_back_local = False
                network_ko_back_remote = False
            else:
                STATE = MENU
                fight_chars = []
                char1_pos = [200, 360]
                char2_pos = [800, 360]
                for char in char_list:
                    char.jump_phase = 0
                    char.vy = 0
                    char.current_frame = 0
                    char.frame_timer = 0
                    char.is_attacking = False
                    char.attack_phase = None
                    char.is_hit = False
                    char.is_standing_up = False
                    char.hp = char.max_hp
                    char.damage_protection_total = 0.0
                    char.is_invincible = False
                    char.invincible_timer = 0
                    char.needs_ground_snap = False
                damage_numbers.clear()
                avatar_show.in_progress = False
                ko_phase = 0
                ko_timer = 0
                ko_loser_index = 0
                ko_loser_airborne = False
                ko_bounce_count = 0
                blood_particles.clear()

        # 5 秒后覆盖结算界面
        if ko_timer >= KO_ANIM_FRAMES:
            # 结算遮罩使用半透明，让底下的击飞弹跳动画依然清晰可见。
            # 物理更新在 FIGHT_KO 分支里是无条件执行的，不依赖 ko_timer，
            # 所以弹跳会在遮罩下继续播放到自然停下，不影响结算显示。
            overlay = pygame.Surface((W, H), pygame.SRCALPHA)
            overlay.fill((20, 20, 30, 120))
            screen.blit(overlay, (0, 0))

            # 获胜者文字带脉冲放大强调
            pulse = 1.0 + 0.10 * math.sin(ko_timer * 0.15)

            winner_show_name = winner_char.name
            try:
                idx = next(i for i, d in enumerate(choose_list) if d["name"] == winner_char.name)
                winner_show_name = choose_list[idx]["show_name"]
            except (StopIteration, IndexError):
                pass

            # 联机时根据抽签结果判断本机角色（你胜利了 / 你失败了）
            if network_connected:
                # 本机角色索引由 network_is_1p 决定
                _my_idx = 0 if network_is_1p else 1
                if ko_loser_index == _my_idx:
                    text = "你失败了"
                else:
                    text = "你胜利了"
            else:
                winner_side = "2P" if ko_loser_index == 0 else "1P"
                text = f"{winner_side} {winner_show_name} 获胜"

            base_font = get_font(56)
            if network_connected and text == "你失败了":
                _txt_color = (150, 150, 150)
            else:
                _txt_color = (255, 215, 0)
            txt_surf = base_font.render(text, True, _txt_color)
            shadow_surf = base_font.render(text, True, (0, 0, 0))

            new_w = max(1, int(txt_surf.get_width() * pulse))
            new_h = max(1, int(txt_surf.get_height() * pulse))
            scaled_txt = pygame.transform.smoothscale(txt_surf, (new_w, new_h))
            scaled_shadow = pygame.transform.smoothscale(shadow_surf, (new_w, new_h))

            text_rect = scaled_txt.get_rect(center=(W // 2, H // 2 - 20))
            screen.blit(scaled_shadow, (text_rect.x + 3, text_rect.y + 3))
            screen.blit(scaled_txt, text_rect)

            # 返回按钮：联机 / 本地都显示，点击后回到准备界面或主菜单
            original_center = btn_back.rect.center
            btn_back.rect.center = (W // 2, H - 100)
            btn_back.draw(mouse_pos)
            btn_back.rect.center = original_center

    # 联机时在屏幕上方中央显示实时延迟
    if network_connected:
        if network_rtt_ms > 0:
            _rtt_text = f"延迟 {network_rtt_ms} ms"
        else:
            _rtt_text = "延迟 -- ms"
        _rf = get_font(18)
        _rsurf = _rf.render(_rtt_text, True, (255, 240, 120))
        _rrect = _rsurf.get_rect(midtop=(W // 2, 4))
        _rbg = pygame.Rect(_rrect.x - 8, _rrect.y - 2, _rrect.w + 16, _rrect.h + 4)
        pygame.draw.rect(screen, (20, 20, 20), _rbg)
        pygame.draw.rect(screen, (180, 180, 180), _rbg, 1)
        screen.blit(_rsurf, _rrect)

    pygame.display.flip()
    clock.tick(FPS)

pygame.quit()
sys.exit()