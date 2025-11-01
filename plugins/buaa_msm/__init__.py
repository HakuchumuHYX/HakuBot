# plugins/buaa_msm/__init__.py
from nonebot.plugin import Plugin
from nonebot import on_command, get_driver
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent, GroupMessageEvent, MessageSegment
from nonebot.log import logger
import os
from pathlib import Path

# 导入主要模块
from . import bind
from . import data_upload
from . import data_rename
from . import data_manage
from . import help
from . import decrypt_paint

# 导入文件管理模块以访问用户最新文件
from .data_manage import user_latest_files, file_storage_dir

# 创建命令处理器
msm_cmd = on_command("buaamsm", priority=5, block=True)

# 添加处理状态跟踪，防止重复处理
processing_users = set()


@msm_cmd.handle()
async def handle_msm_command(bot: Bot, event: PrivateMessageEvent):
    """处理 buaamsm 命令，解密用户上传的.bin文件并发送图片"""
    user_id = str(event.user_id)

    # 检查是否正在处理中，防止重复处理
    if user_id in processing_users:
        await msm_cmd.finish("您的请求正在处理中，请稍候...")
        return

    processing_users.add(user_id)

    try:
        # 检查用户是否有上传的文件
        if user_id not in user_latest_files:
            await msm_cmd.finish("您还没有上传过文件，请先使用'buaa上传文件'命令上传您的mysekai包体文件。")
            return

        latest_file_path = user_latest_files[user_id]

        # 检查文件是否存在
        if not latest_file_path.exists():
            await msm_cmd.finish("您的最新文件不存在，可能已被清理，请重新上传文件。")
            return

        # 检查文件扩展名是否为.bin
        if latest_file_path.suffix.lower() != '.bin':
            await msm_cmd.finish("您上传的文件不是.bin格式，请上传正确的mysekai包体文件。")
            return

        await msm_cmd.send("正在解密您的文件并生成地图预览，请稍候...")

        # 创建用户专属的输出目录
        user_output_dir = file_storage_dir / f"output_{user_id}"
        user_output_dir.mkdir(exist_ok=True)

        # 调用解密和图片生成功能
        input_file = str(latest_file_path)

        # 解密数据包
        decrypted_data = decrypt_paint.decrypt_packet(input_file)
        if decrypted_data is None:
            await msm_cmd.finish("文件解密失败，请检查文件格式是否正确。")
            return

        # 解析地图数据
        parsed_maps = decrypt_paint.parse_map(decrypted_data)
        if parsed_maps is None:
            await msm_cmd.finish("地图数据解析失败。")
            return

        # 生成图片
        generated_images = []
        for scene_key, scene_params in decrypt_paint.SCENES.items():
            scene_name = decrypt_paint.SCENE_KEY_TO_NAME.get(scene_key)
            if not scene_name:
                continue

            map_data = parsed_maps.get(scene_name)
            if map_data is None:
                continue

            # 生成图片文件
            output_filename = user_output_dir / f"{scene_key}_preview.png"
            try:
                # 调用生成函数并检查是否成功
                result = decrypt_paint.generate_map_preview(
                    scene_id=scene_key,
                    map_data=map_data,
                    output_filename=str(output_filename)
                )

                # 检查文件是否实际生成
                if result and output_filename.exists() and output_filename.stat().st_size > 0:
                    generated_images.append(output_filename)
                    logger.info(f"为用户 {user_id} 生成了 {scene_key} 的地图预览")
                else:
                    logger.warning(f"为用户 {user_id} 生成 {scene_key} 地图预览失败，文件未创建或为空")

            except Exception as e:
                logger.error(f"生成 {scene_key} 地图预览失败: {e}")
                continue

        if not generated_images:
            await msm_cmd.finish("未能生成任何地图预览图片，可能是资源文件缺失，请联系管理员。")
            return

        # 发送图片给用户
        success_count = 0
        for image_path in generated_images:
            try:
                # 再次确认文件存在且不为空
                if not image_path.exists() or image_path.stat().st_size == 0:
                    logger.warning(f"图片文件不存在或为空: {image_path}")
                    continue

                # 读取图片文件
                with open(image_path, 'rb') as f:
                    image_data = f.read()

                # 发送图片
                await bot.send_private_msg(
                    user_id=event.user_id,
                    message=MessageSegment.image(image_data)
                )

                success_count += 1
                logger.info(f"向用户 {user_id} 发送图片: {image_path.name}")

            except Exception as e:
                logger.error(f"发送图片失败 {image_path}: {e}")
                # 这里使用 bot.send_private_msg 而不是 msm_cmd.send
                await bot.send_private_msg(
                    user_id=event.user_id,
                    message=f"发送图片 {image_path.name} 失败: {str(e)}"
                )

        # 使用 send 而不是 finish 来结束，因为前面已经发送了图片
        if success_count > 0:
            await msm_cmd.send(f"地图预览生成完成！成功发送 {success_count} 张图片。")
        else:
            await msm_cmd.send("所有图片发送失败，请稍后重试或联系管理员。")

    except Exception as e:
        logger.error(f"处理 buaamsm 命令失败: {e}")
        await msm_cmd.finish(f"处理失败: {str(e)}")
    finally:
        # 无论成功还是失败，都移除处理状态
        if user_id in processing_users:
            processing_users.remove(user_id)


@msm_cmd.handle()
async def handle_group_msm_command(bot: Bot, event: GroupMessageEvent):
    """处理群聊中的 buaamsm 命令"""
    await msm_cmd.finish("该指令仅在私聊中可用")


# 可选：导出特定的matcher供其他插件使用
from .bind import bind_cmd, query_bind, unbind_cmd, view_all_binds

__all__ = ["bind_cmd", "query_bind", "unbind_cmd", "view_all_binds", "msm_cmd"]

# 插件加载成功提示
logger.success("BUAAMSM 插件加载成功！所有模块已就绪。")