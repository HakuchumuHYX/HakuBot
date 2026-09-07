from nonebot import on_command
from nonebot.rule import to_me

gif_reverse_handler = on_command("img倒放", rule=to_me(), priority=5, block=True)
image_cutout_handler = on_command("imgcut", rule=to_me(), priority=5, block=True)
gif_speed_handler = on_command("imgx", rule=to_me(), priority=5, block=True)
image_symmetry_handler = on_command("img对称", rule=to_me(), priority=5, block=True)
image_symmetry_left_handler = on_command(
    "img左对称", rule=to_me(), priority=5, block=True
)
image_symmetry_right_handler = on_command(
    "img右对称", rule=to_me(), priority=5, block=True
)
image_symmetry_center_handler = on_command(
    "img中心对称", rule=to_me(), priority=5, block=True
)
image_symmetry_top_handler = on_command(
    "img上对称", rule=to_me(), priority=5, block=True
)
image_symmetry_bottom_handler = on_command(
    "img下对称", rule=to_me(), priority=5, block=True
)
image_help_handler = on_command("imghelp", priority=5, block=True)
video_to_gif_handler = on_command("imggif", rule=to_me(), priority=5, block=True)
image_mirror_handler = on_command("img镜像", rule=to_me(), priority=5, block=True)
image_mirror_vertical_handler = on_command(
    "img上镜像", rule=to_me(), priority=5, block=True
)
image_rotate_handler = on_command("img旋转", rule=to_me(), priority=5, block=True)
image_rotate_clockwise_handler = on_command(
    "img顺时针", rule=to_me(), priority=5, block=True
)
image_rotate_counter_handler = on_command(
    "img逆时针", rule=to_me(), priority=5, block=True
)
