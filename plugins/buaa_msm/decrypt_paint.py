import json
from PIL import Image, ImageDraw, ImageFont
import os
import sys
import msgpack
import msgspec
import re
from pathlib import Path
from typing import List, Optional

# 声明依赖并导入 nonebot-plugin-localstore
import nonebot_plugin_localstore as store

# 使用 localstore 获取插件数据目录
data_dir = store.get_plugin_data_dir()

# 获取插件目录
plugin_dir = Path(__file__).parent

# 输出目录
output_dir = data_dir / "output"
output_dir.mkdir(parents=True, exist_ok=True)

# 资源目录
resource_dir = plugin_dir / "resources"
resource_dir.mkdir(parents=True, exist_ok=True)

# 创建必要的子目录
(resource_dir / "img").mkdir(parents=True, exist_ok=True)
(resource_dir / "icon" / "Texture2D").mkdir(parents=True, exist_ok=True)

# --- Data Structures Ported from paint.py ---

# Scene parameters - 更新路径使用绝对路径
SCENES = {
    'scene1': {
        "physicalWidth": 33.333,
        "offsetX": 0,
        "offsetY": -40,
        "imagePath": str(resource_dir / "img" / "grassland.png"),
        "xDirection": 'x-',
        "yDirection": 'y-',
        "reverseXY": True,
    },
    'scene2': {
        "physicalWidth": 24.806,
        "offsetX": -62.015,
        "offsetY": 20.672,
        "imagePath": str(resource_dir / "img" / "flowergarden.png"),
        "xDirection": 'x-',
        "yDirection": 'y-',
        "reverseXY": True,
    },
    'scene3': {
        "physicalWidth": 20.513,
        "offsetX": 0,
        "offsetY": 80,
        "imagePath": str(resource_dir / "img" / "beach.png"),
        "xDirection": 'x+',
        "yDirection": 'y-',
        "reverseXY": False,
    },
    'scene4': {
        "physicalWidth": 21.333,
        "offsetX": 0,
        "offsetY": -106.667,
        "imagePath": str(resource_dir / "img" / "memorialplace.png"),
        "xDirection": 'x+',
        "yDirection": 'y-',
        "reverseXY": False,
    }
}

# Colors for fixture IDs
FIXTURE_COLORS = {
    112: '#f9f9f9',
    1001: '#da6d42', 1002: '#da6d42', 1003: '#da6d42', 1004: '#da6d42',
    2001: '#878685', 2002: '#d5750a', 2003: '#d5d5d5', 2004: '#a7c7cb', 2005: '#9933cc',
    3001: '#c95a49',
    4001: '#f8729a', 4002: '#f8729a', 4003: '#f8729a', 4004: '#f8729a',
    4005: '#f8729a', 4006: '#f8729a', 4007: '#f8729a', 4008: '#f8729a',
    4009: '#f8729a', 4010: '#f8729a', 4011: '#f8729a', 4012: '#f8729a',
    4013: '#f8729a', 4014: '#f8729a', 4015: '#f8729a', 4016: '#f8729a',
    4017: '#f8729a', 4018: '#f8729a', 4019: '#f8729a', 4020: '#f8729a',
    5001: '#f6f5f2', 5002: '#f6f5f2', 5003: '#f6f5f2', 5004: '#f6f5f2',
    5101: '#f6f5f2', 5102: '#f6f5f2', 5103: '#f6f5f2', 5104: '#f6f5f2',
    6001: '#6f4e37',
    7001: '#a5d5ff',
}

# Paths to item icons - 更新路径使用绝对路径
ITEM_TEXTURES = {
    'mysekai_material': {
        "1": str(resource_dir / "icon" / "Texture2D" / "item_wood_1.png"),
        "2": str(resource_dir / "icon" / "Texture2D" / "item_wood_2.png"),
        "3": str(resource_dir / "icon" / "Texture2D" / "item_wood_3.png"),
        "4": str(resource_dir / "icon" / "Texture2D" / "item_wood_4.png"),
        "5": str(resource_dir / "icon" / "Texture2D" / "item_wood_5.png"),
        "6": str(resource_dir / "icon" / "Texture2D" / "item_mineral_1.png"),
        "7": str(resource_dir / "icon" / "Texture2D" / "item_mineral_2.png"),
        "8": str(resource_dir / "icon" / "Texture2D" / "item_mineral_3.png"),
        "9": str(resource_dir / "icon" / "Texture2D" / "item_mineral_4.png"),
        "10": str(resource_dir / "icon" / "Texture2D" / "item_mineral_5.png"),
        "11": str(resource_dir / "icon" / "Texture2D" / "item_mineral_6.png"),
        "12": str(resource_dir / "icon" / "Texture2D" / "item_mineral_7.png"),
        "13": str(resource_dir / "icon" / "Texture2D" / "item_junk_1.png"),
        "14": str(resource_dir / "icon" / "Texture2D" / "item_junk_2.png"),
        "15": str(resource_dir / "icon" / "Texture2D" / "item_junk_3.png"),
        "16": str(resource_dir / "icon" / "Texture2D" / "item_junk_4.png"),
        "17": str(resource_dir / "icon" / "Texture2D" / "item_junk_5.png"),
        "18": str(resource_dir / "icon" / "Texture2D" / "item_junk_6.png"),
        "19": str(resource_dir / "icon" / "Texture2D" / "item_junk_7.png"),
        "20": str(resource_dir / "icon" / "Texture2D" / "item_plant_1.png"),
        "21": str(resource_dir / "icon" / "Texture2D" / "item_plant_2.png"),
        "22": str(resource_dir / "icon" / "Texture2D" / "item_plant_3.png"),
        "23": str(resource_dir / "icon" / "Texture2D" / "item_plant_4.png"),
        "24": str(resource_dir / "icon" / "Texture2D" / "item_tone_8.png"),
        "32": str(resource_dir / "icon" / "Texture2D" / "item_junk_8.png"),
        "33": str(resource_dir / "icon" / "Texture2D" / "item_mineral_8.png"),
        "34": str(resource_dir / "icon" / "Texture2D" / "item_junk_9.png"),
        "61": str(resource_dir / "icon" / "Texture2D" / "item_junk_10.png"),
        "62": str(resource_dir / "icon" / "Texture2D" / "item_junk_11.png"),
        "63": str(resource_dir / "icon" / "Texture2D" / "item_junk_12.png"),
        "64": str(resource_dir / "icon" / "Texture2D" / "item_mineral_9.png"),
        "65": str(resource_dir / "icon" / "Texture2D" / "item_mineral_10.png"),
    },
    'mysekai_item': {
        "7": str(resource_dir / "icon" / "Texture2D" / "item_blueprint_fragment.png"),
    },
    'mysekai_fixture': {
        "118": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sapling1_118.png"),
        "119": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sapling1_119.png"),
        "120": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sapling1_120.png"),
        "121": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sapling1_121.png"),
        "126": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_126.png"),
        "127": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_127.png"),
        "128": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_128.png"),
        "129": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_129.png"),
        "130": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_130.png"),
        "474": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_474.png"),
        "475": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_475.png"),
        "476": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_476.png"),
        "477": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_477.png"),
        "478": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_478.png"),
        "479": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_479.png"),
        "480": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_480.png"),
        "481": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_481.png"),
        "482": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_482.png"),
        "483": str(resource_dir / "icon" / "Texture2D" / "mdl_non1001_before_sprout1_483.png")
    },
    'mysekai_music_record': {
        352: str(resource_dir / "icon" / "Texture2D" / "music352.png")
    }
}

# Rarity definitions
RARE_ITEM = {
    'mysekai_material': [5, 12, 20, 24, 32, 33, 61, 62, 63, 64, 65],
    'mysekai_item': [7],
    'mysekai_music_record': [],
    'mysekai_fixture': [118, 119, 120, 121]
}

SUPER_RARE_ITEM = {
    'mysekai_material': [5, 12, 20, 24],
    'mysekai_item': [],
    'mysekai_fixture': [],
    'mysekai_music_record': []
}


# --- Data Structures Ported from receive.py ---

class UserMysekaiSiteHarvestFixture(msgspec.Struct):
    mysekaiSiteHarvestFixtureId: int
    positionX: int
    positionZ: int
    hp: int
    userMysekaiSiteHarvestFixtureStatus: str


class UserMysekaiSiteHarvestResourceDrop(msgspec.Struct):
    resourceType: str
    resourceId: int
    positionX: int
    positionZ: int
    hp: int
    seq: int
    mysekaiSiteHarvestResourceDropStatus: str
    quantity: int


class Map(msgspec.Struct, kw_only=True):
    mysekaiSiteId: int
    siteName: Optional[str] = None
    userMysekaiSiteHarvestFixtures: List[UserMysekaiSiteHarvestFixture]
    userMysekaiSiteHarvestResourceDrops: List[UserMysekaiSiteHarvestResourceDrop]


# 站点ID映射
SITE_ID = {
    1: "マイホーム",
    2: "1F",
    3: "2F",
    4: "3F",
    5: "さいしょの原っぱ",
    6: "願いの砂浜",
    7: "彩りの花畑",
    8: "忘れ去られた場所",
}

# 场景名称映射
SCENE_NAME_TO_KEY = {
    "さいしょの原っぱ": "scene1",
    "彩りの花畑": "scene2",
    "願いの砂浜": "scene3",
    "忘れ去られた場所": "scene4",
}
SCENE_KEY_TO_NAME = {v: k for k, v in SCENE_NAME_TO_KEY.items()}


# --- Helper Functions ---

def do_contains_rare_item(reward, is_super_rare=False):
    """检查奖励对象是否包含稀有或超稀有物品"""
    compare_list = SUPER_RARE_ITEM if is_super_rare else RARE_ITEM
    for category, items in reward.items():
        if category in compare_list:
            for item_id_str in items.keys():
                if int(item_id_str) in compare_list[category]:
                    return True
    return False


def get_font(size=8):
    """尝试加载字体，如果找不到则回退到默认字体"""
    try:
        return ImageFont.truetype("arial.ttf", size)
    except IOError:
        print("Arial font not found, using default font.")
        return ImageFont.load_default()


def get_icon(path, size=(20, 20)):
    """加载图标图像，如果找不到则返回占位符"""
    try:
        icon = Image.open(path).convert("RGBA")
        icon = icon.resize(size, Image.Resampling.LANCZOS)
        return icon
    except FileNotFoundError:
        print(f"Icon not found: {path}, using placeholder.")
        # 创建彩色占位符
        color_hash = hash(path) % 256
        color = (
            (color_hash * 37) % 256,
            (color_hash * 73) % 256,
            (color_hash * 109) % 256,
            255
        )
        icon = Image.new('RGBA', size, color)
        return icon


def adjust_item_list_positions(render_list, scene, max_lap_width=5, max_lap_height=5):
    """调整奖励物品框的位置以避免重叠"""
    reverse_xy = scene['reverseXY']

    for i in range(len(render_list)):
        if not render_list[i].get('box_rect'):
            continue

        rect1 = render_list[i]['box_rect']

        for j in range(i + 1, len(render_list)):
            if not render_list[j].get('box_rect'):
                continue

            rect2 = render_list[j]['box_rect']

            # 计算重叠
            r1_left, r1_top, r1_right, r1_bottom = rect1
            r2_left, r2_top, r2_right, r2_bottom = rect2

            overlap_width = min(r1_right, r2_right) - max(r1_left, r2_left)
            overlap_height = min(r1_bottom, r2_bottom) - max(r1_top, r2_top)

            # 检查重叠是否超出阈值
            if overlap_width > max_lap_width and overlap_height > max_lap_height:
                if reverse_xy:
                    nudge_amount = (overlap_height / 1.25)
                    r2_top -= nudge_amount
                    r2_bottom -= nudge_amount
                else:
                    nudge_amount = (overlap_width / 1.25)
                    r2_left -= nudge_amount
                    r2_right -= nudge_amount

                render_list[j]['box_rect'] = [r2_left, r2_top, r2_right, r2_bottom]

    return render_list


# --- Core Functions ---

def decrypt_packet(infile, region='jp'):
    """解密 sssekai 加密的数据包"""
    try:
        from sssekai.crypto.APIManager import decrypt, SEKAI_APIMANAGER_KEYSETS

        data = open(infile, "rb").read()
        plain = decrypt(data, SEKAI_APIMANAGER_KEYSETS[region])

        try:
            msg = msgpack.unpackb(plain)
            return msg
        except Exception:
            print("Malformed decrypted data")
            print("Please consider switching to another region (e.g., --region en)")
            return None
    except ImportError:
        print("Error: 'sssekai' module not found.")
        print("Please install it via: pip install sssekai")
        return None
    except Exception as e:
        print(f"Error decrypting packet: {str(e)}")
        return None


def parse_map(user_data: dict):
    """从解密的数据中解析地图采集点信息"""
    if "updatedResources" not in user_data or "userMysekaiHarvestMaps" not in user_data.get("updatedResources", {}):
        print("Error: 'userMysekaiHarvestMaps' not found in decrypted data.")
        return None

    try:
        harvest_maps: List[Map] = [
            msgspec.json.decode(msgspec.json.encode(mp), type=Map)
            for mp in user_data["updatedResources"]["userMysekaiHarvestMaps"]
        ]
    except Exception as e:
        print(f"Error decoding map data with msgspec: {e}")
        return None

    for mp in harvest_maps:
        mp.siteName = SITE_ID.get(mp.mysekaiSiteId, f"Unknown Site {mp.mysekaiSiteId}")

    processed_map = {}
    for mp in harvest_maps:
        mp_detail = []
        for fixture in mp.userMysekaiSiteHarvestFixtures:
            if fixture.userMysekaiSiteHarvestFixtureStatus == "spawned":
                mp_detail.append({
                    "location": (fixture.positionX, fixture.positionZ),
                    "fixtureId": fixture.mysekaiSiteHarvestFixtureId,
                    "reward": {}
                })

        for drop in mp.userMysekaiSiteHarvestResourceDrops:
            pos = (drop.positionX, drop.positionZ)
            for i in range(len(mp_detail)):
                if mp_detail[i]["location"] != pos:
                    continue

                mp_detail[i]["reward"].setdefault(drop.resourceType, {})
                mp_detail[i]["reward"][drop.resourceType][drop.resourceId] = \
                    mp_detail[i]["reward"][drop.resourceType].get(drop.resourceId, 0) + drop.quantity
                break

        processed_map[mp.siteName] = mp_detail

    return processed_map


def generate_map_preview(scene_id, map_data, output_filename=None):
    """
    生成地图预览图像

    Args:
        scene_id (str): 场景键 (如 'scene1')
        map_data (list): 点字典列表
        output_filename (str): 输出文件名，如果为None则使用默认路径

    Returns:
        bool: 是否成功生成图片
    """
    if scene_id not in SCENES:
        print(f"Error: Scene ID '{scene_id}' not found.")
        return False

    scene = SCENES[scene_id]

    # 如果没有指定输出文件名，使用默认路径
    if output_filename is None:
        output_filename = "map_preview.png"

    output_path = Path(output_filename)
    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 尝试加载基础图像，如果失败则创建占位图
    try:
        base_img = Image.open(scene['imagePath']).convert('RGBA')
        print(f"Loaded base image: {scene['imagePath']}")
    except FileNotFoundError:
        print(f"Warning: Base image not found at {scene['imagePath']}, creating placeholder.")
        # 创建一个简单的占位图
        base_img = Image.new('RGBA', (800, 600), (200, 200, 200, 255))
        draw = ImageDraw.Draw(base_img)
        # 在占位图上添加文字
        try:
            font = ImageFont.truetype("arial.ttf", 24)
        except:
            font = ImageFont.load_default()
        scene_name = SCENE_KEY_TO_NAME.get(scene_id, scene_id)
        draw.text((400, 300), f"Map: {scene_name}", fill=(0, 0, 0, 255), font=font, anchor="mm")
        draw.text((400, 340), "Base image not found", fill=(100, 100, 100, 255), font=font, anchor="mm")
    except Exception as e:
        print(f"Error loading base image: {e}")
        return False

    draw = ImageDraw.Draw(base_img, 'RGBA')

    # 加载字体
    qty_font = get_font(8)

    # 获取场景参数
    grid_px = scene['physicalWidth']
    origin_x = base_img.width / 2 + scene['offsetX']
    origin_y = base_img.height / 2 + scene['offsetY']
    reverse_xy = scene['reverseXY']
    x_dir = scene['xDirection']
    y_dir = scene['yDirection']

    # 阶段 1: 计算所有渲染元素的位置
    render_list = []

    for point in map_data:
        location = point['location']
        fixture_id = point['fixtureId']
        reward = point.get('reward', {})

        # 坐标转换
        x, y = (location[1], location[0]) if reverse_xy else (location[0], location[1])

        display_x = origin_x + x * grid_px if x_dir == 'x+' else origin_x - x * grid_px
        display_y = origin_y + y * grid_px if y_dir == 'y+' else origin_y - y * grid_px

        # 点的颜色和边框
        color = FIXTURE_COLORS.get(fixture_id, '#000000')
        is_rare = do_contains_rare_item(reward)
        border_color = 'red' if is_rare else 'black'

        render_item = {
            'point_coords': (display_x, display_y),
            'point_color': color,
            'point_border': border_color,
            'box_rect': None,
            'box_bg': None,
            'items_to_draw': []
        }

        # 准备奖励物品
        items_to_draw = []
        for category, items in reward.items():
            for item_id_str, quantity in items.items():
                texture_path = None
                item_id_str = str(item_id_str)
                if category == "mysekai_music_record":
                    texture_path = str(resource_dir / "icon" / "Texture2D" / "item_surplus_music_record.png")
                else:
                    texture_path = ITEM_TEXTURES.get(category, {}).get(item_id_str)

                if texture_path:
                    items_to_draw.append((texture_path, quantity))
                else:
                    print(f"Warning: No texture for {category} - {item_id_str}")

        if not items_to_draw:
            render_list.append(render_item)
            continue

        render_item['items_to_draw'] = items_to_draw

        # 奖励框背景
        is_super_rare = do_contains_rare_item(reward, is_super_rare=True)
        has_music = "mysekai_music_record" in reward

        if is_super_rare:
            bg_color = (255, 0, 0, 192)
        elif is_rare or has_music:
            bg_color = (0, 0, 180, 192)
        else:
            bg_color = (138, 138, 138, 216)

        render_item['box_bg'] = bg_color

        # 计算奖励框位置和尺寸
        icon_size = 20
        padding = 2
        num_items = len(items_to_draw)

        box_x = display_x + grid_px / 2
        box_y = display_y + grid_px / 3
        if reverse_xy:
            box_y -= 10

        if reverse_xy:  # 水平布局
            box_w = (icon_size * num_items) + (padding * (num_items + 1))
            box_h = icon_size + 2 * padding
        else:  # 垂直布局
            box_w = icon_size + 2 * padding
            box_h = (icon_size * num_items) + (padding * (num_items + 1))

        render_item['box_rect'] = [box_x, box_y, box_x + box_w, box_y + box_h]

        render_list.append(render_item)

    # 阶段 2: 调整物品框位置
    adjusted_render_list = adjust_item_list_positions(render_list, scene)

    # 阶段 3: 执行所有绘制操作
    for item in adjusted_render_list:
        # 绘制点
        display_x, display_y = item['point_coords']
        radius = 5
        bbox = [display_x - radius, display_y - radius, display_x + radius, display_y + radius]
        draw.ellipse(bbox, fill=item['point_color'], outline=item['point_border'], width=1)

        # 检查是否有物品框要绘制
        if not item['box_rect']:
            continue

        # 绘制物品框
        box_rect = item['box_rect']
        draw.rounded_rectangle(box_rect, radius=4, fill=item['box_bg'])

        # 绘制图标和数量
        icon_size = 20
        padding = 2
        box_x, box_y = box_rect[0], box_rect[1]

        current_x = box_x + padding
        current_y = box_y + padding

        for texture_path, quantity in item['items_to_draw']:
            icon = get_icon(texture_path, (icon_size, icon_size))
            base_img.paste(icon, (int(current_x), int(current_y)), icon)

            # 绘制数量
            qty_str = str(quantity)
            try:
                text_bbox = qty_font.getbbox(qty_str)
                text_w = text_bbox[2] - text_bbox[0]
                text_h = text_bbox[3] - text_bbox[1]

                text_x = current_x + icon_size - text_w - 2
                text_y = current_y + icon_size - text_h - 2

                draw.rectangle(
                    [text_x - 1, text_y - 1, text_x + text_w + 1, text_y + text_h + 1],
                    fill=(255, 255, 255, 192)
                )
                draw.text((text_x, text_y), qty_str, fill='black', font=qty_font)
            except Exception as e:
                print(f"Error drawing quantity text: {e}")

            if reverse_xy:
                current_x += icon_size + padding
            else:
                current_y += icon_size + padding

    # 阶段 4: 保存图像
    try:
        base_img = base_img.convert('RGB')
        base_img.save(output_path)
        print(f"Successfully saved map preview to '{output_path}'")
        return True
    except Exception as e:
        print(f"Error saving image: {e}")
        return False


# --- Main Execution Block ---

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: python {sys.argv[0]} <input_mysekai_packet.bin>")
        sys.exit(1)

    input_filename = sys.argv[1]

    if not os.path.exists(input_filename):
        print(f"Error: Input file not found: {input_filename}")
        sys.exit(1)

    # 确保输出目录存在
    os.makedirs(output_dir, exist_ok=True)

    # 1. 解密
    print(f"Decrypting {input_filename}...")
    decrypted_data = decrypt_packet(input_filename)
    if decrypted_data is None:
        print("Failed to decrypt packet. Is 'sssekai' module installed?")
        sys.exit(1)

    # 2. 解析
    print("Parsing map data...")
    try:
        parsed_maps = parse_map(decrypted_data)
        if parsed_maps is None:
            sys.exit(1)
    except Exception as e:
        print(f"Failed to parse map data: {e}")
        sys.exit(1)

    print(f"Successfully parsed {len(parsed_maps)} maps from .bin file.")

    # 3. 循环生成四张图
    generated_count = 0
    for scene_key, scene_params in SCENES.items():
        scene_name = SCENE_KEY_TO_NAME.get(scene_key)
        if not scene_name:
            continue

        map_data = parsed_maps.get(scene_name)
        if map_data is None:
            continue

        # 生成图片文件
        output_filename = output_dir / f"{scene_key}_preview.png"
        print(f"Generating {output_filename} for '{scene_name}'...")

        try:
            success = generate_map_preview(
                scene_id=scene_key,
                map_data=map_data,
                output_filename=str(output_filename)
            )
            if success:
                generated_count += 1
        except Exception as e:
            print(f"Error generating map for {scene_key}: {e}")

    print(f"\nDone. Generated {generated_count} map previews in '{output_dir}'.")