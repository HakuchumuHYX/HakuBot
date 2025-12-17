# video_to_gif.py
import os
import tempfile
import aiohttp
import asyncio
from pathlib import Path
import subprocess
import cv2
from PIL import Image
import numpy as np
import urllib.parse
import random
from nonebot.log import logger
# 全局变量，用于缓存ffmpeg可用性检查结果
_ffmpeg_available = None


async def download_video(url: str, file_name: str = None, expected_size: int = 0, access_token: str = None) -> str:
    """下载视频到临时目录 - 修复QQ群文件下载问题"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': '*/*',
        'Accept-Encoding': 'identity',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Connection': 'keep-alive',
    }

    # 处理QQ群文件URL - 修复下载问题
    if 'ftn.qq.com' in url:
        logger.info("检测到QQ群文件URL，进行特殊处理 (添加Referer并修复fname)")
        headers['Referer'] = 'https://qun.qq.com/'  # 仅为 ftn 链接添加 Referer

        # 修复URL参数
        parsed_url = urllib.parse.urlparse(url)
        query_params = urllib.parse.parse_qs(parsed_url.query)

        # 确保有fname参数
        if 'fname' not in query_params or not query_params['fname'] or query_params['fname'][0] == '':
            if file_name:
                # 使用提供的文件名
                query_params['fname'] = [file_name]
            else:
                # 生成随机文件名
                query_params['fname'] = [f'video_{random.randint(10000, 99999)}.mp4']

        # 重建URL
        new_query = urllib.parse.urlencode(query_params, doseq=True)
        url = urllib.parse.urlunparse((
            parsed_url.scheme,
            parsed_url.netloc,
            parsed_url.path,
            parsed_url.params,
            new_query,
            parsed_url.fragment
        ))

        logger.info(f"修复后的QQ群文件URL: {url[:200]}...")

    # 处理 Go-CQHTTP 代理 URL
    # 如果不是 ftn 链接，且提供了 access_token，则假定为 Go-CQHTTP 代理链接
    elif access_token:
        logger.info("检测到非QQ群文件URL且Access Token存在，添加Authorization header")
        headers['Authorization'] = f'Bearer {access_token}'

    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            logger.info(f"开始下载视频: {url[:200]}...")

            # 先发送HEAD请求获取文件信息
            async with session.head(url) as head_response:
                if head_response.status not in [200, 206]:
                    logger.info(f"HEAD请求失败: {head_response.status}")
                else:
                    content_length = head_response.headers.get('content-length')
                    content_type = head_response.headers.get('content-type', '')
                    logger.info(f"文件信息 - 大小: {content_length}, 类型: {content_type}")

            # 下载文件
            temp_dir = tempfile.gettempdir()
            temp_path = os.path.join(temp_dir, f"temp_video_{os.urandom(4).hex()}.mp4")

            # 使用流式下载，避免内存问题
            async with session.get(url) as response:
                if response.status in [200, 206]:
                    total_size = int(response.headers.get('content-length', 0))
                    downloaded_size = 0

                    with open(temp_path, 'wb') as f:
                        async for chunk in response.content.iter_chunked(1024 * 1024):  # 1MB chunks
                            if chunk:
                                f.write(chunk)
                                downloaded_size += len(chunk)

                                # 显示下载进度
                                if total_size > 0:
                                    percent = (downloaded_size / total_size) * 100
                                    if int(percent) % 10 == 0:  # 每10%打印一次进度
                                        logger.info(f"下载进度: {percent:.1f}% ({downloaded_size}/{total_size} bytes)")
                                else:
                                    logger.info(f"已下载: {downloaded_size} bytes")

                    # 验证下载的文件大小
                    final_size = os.path.getsize(temp_path)
                    logger.info(f"下载完成: {temp_path}, 大小: {final_size} bytes")

                    # 优先使用从消息体中获取的预期大小进行验证
                    # 允许10%的误差（0.9倍）
                    if expected_size > 0 and final_size < expected_size * 0.9:
                        logger.warning(
                            f"严重警告: 下载的文件大小 ({final_size}) 与预期大小 ({expected_size}) 严重不符。下载可能已失败。")
                        try:
                            await safe_delete_file(temp_path)
                        except:
                            pass
                        # 抛出更准确的错误
                        raise Exception(
                            f"下载视频失败：文件大小不匹配 (预期: {expected_size}, 实际: {final_size})。这通常是由于群文件链接失效或需要认证。")

                    # 如果没有预期大小，或者大小匹配，再使用 content-length 验证
                    elif total_size > 0 and final_size < total_size * 0.9:
                        logger.warning(f"警告: 下载的文件可能不完整 (期望: {total_size}, 实际: {final_size})")

                    return temp_path
                else:
                    error_text = await response.text()
                    logger.error(f"下载失败，状态码: {response.status}")
                    if error_text:
                        logger.error(f"错误响应: {error_text[:500]}")
                    raise Exception(f"下载视频失败: {response.status}")

        except aiohttp.ClientError as e:
            raise Exception(f"下载视频时发生网络错误: {str(e)}")
        except Exception as e:
            raise Exception(f"下载视频时发生未知错误: {str(e)}")


async def get_video_info(video_path: str) -> dict:
    """获取视频详细信息 - 修复N/A值处理"""
    try:
        # 首先检查文件是否存在且可读
        if not os.path.exists(video_path):
            raise Exception(f"视频文件不存在: {video_path}")

        file_size = os.path.getsize(video_path)
        if file_size == 0:
            raise Exception("视频文件为空")

        logger.info(f"开始分析视频文件: {video_path}, 大小: {file_size} bytes")

        # 方法1: 使用OpenCV
        try:
            cap = cv2.VideoCapture(video_path)
            if not cap.isOpened():
                raise Exception("OpenCV无法打开视频文件")

            fps = cap.get(cv2.CAP_PROP_FPS)
            frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            duration = frame_count / fps if fps > 0 else 0

            cap.release()

            # 验证获取的信息是否合理
            if fps <= 0 or frame_count <= 0 or width <= 0 or height <= 0:
                raise Exception("OpenCV获取的视频信息异常")

            info = {
                'fps': fps,
                'frame_count': frame_count,
                'width': width,
                'height': height,
                'duration': duration,
                'method': 'opencv'
            }

            logger.info(f"通过OpenCV获取视频信息: {info}")
            return info
        except Exception as opencv_error:
            logger.error(f"OpenCV分析失败: {opencv_error}")

        # 方法2: 使用ffprobe - 修复N/A值处理
        try:
            cmd = [
                'ffprobe',
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height,r_frame_rate,nb_frames,duration',
                '-of', 'csv=p=0',
                video_path
            ]
            logger.info(f"执行ffprobe命令: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

            if result.returncode == 0:
                output = result.stdout.strip()
                logger.info(f"ffprobe原始输出: {output}")
                parts = output.split(',')

                if len(parts) >= 5:
                    # 处理N/A值
                    width = int(parts[0]) if parts[0] and parts[0] != 'N/A' else 640
                    height = int(parts[1]) if parts[1] and parts[1] != 'N/A' else 480
                    fps_str = parts[2] if len(parts) > 2 and parts[2] != 'N/A' else '25/1'
                    frame_count_str = parts[3] if len(parts) > 3 else '0'
                    duration_str = parts[4] if len(parts) > 4 else '0'

                    # 计算FPS
                    if '/' in fps_str:
                        num, den = fps_str.split('/')
                        fps = float(num) / float(den) if den != '0' else 25
                    else:
                        fps = float(fps_str) if fps_str and fps_str != 'N/A' else 25

                    # 处理帧数
                    try:
                        frame_count = int(frame_count_str) if frame_count_str != 'N/A' else 0
                    except:
                        frame_count = 0

                    # 处理时长
                    try:
                        duration = float(duration_str) if duration_str != 'N/A' else 0
                    except:
                        duration = 0

                    # 如果duration为0但frame_count和fps有效，计算duration
                    if duration <= 0 and frame_count > 0 and fps > 0:
                        duration = frame_count / fps

                    # 如果仍然没有duration，使用文件大小估算
                    if duration <= 0:
                        # 简单估算：假设1MB约等于1秒
                        file_size_mb = file_size / (1024 * 1024)
                        duration = min(file_size_mb, 60)  # 最大60秒
                        fps = 25  # 默认FPS
                        frame_count = int(duration * fps)

                    info = {
                        'fps': fps,
                        'frame_count': frame_count,
                        'width': width,
                        'height': height,
                        'duration': duration,
                        'method': 'ffprobe'
                    }

                    logger.info(f"通过ffprobe获取视频信息: {info}")
                    return info
            else:
                logger.error(f"ffprobe错误: {result.stderr}")
        except Exception as ffprobe_error:
            logger.error(f"ffprobe分析失败: {ffprobe_error}")

        # 方法3: 使用文件大小估算基本信息
        try:
            file_size_mb = file_size / (1024 * 1024)
            duration = min(file_size_mb, 60)  # 假设1MB约1秒，最大60秒
            fps = 25
            frame_count = int(duration * fps)

            info = {
                'fps': fps,
                'frame_count': frame_count,
                'width': 640,
                'height': 480,
                'duration': duration,
                'method': 'estimation'
            }

            logger.info(f"通过文件大小估算视频信息: {info}")
            return info

        except Exception as estimation_error:
            logger.error(f"估算视频信息失败: {estimation_error}")

        raise Exception("所有方法都无法获取视频信息")

    except Exception as e:
        logger.error(f"获取视频信息失败: {e}")
        raise Exception(f"无法获取视频信息: {str(e)}")


async def safe_delete_file(file_path: str, max_retries: int = 3):
    """安全删除文件"""
    for i in range(max_retries):
        try:
            if os.path.exists(file_path):
                os.unlink(file_path)
                return True
        except PermissionError:
            if i < max_retries - 1:
                await asyncio.sleep(0.1)
            else:
                return False
    return False


async def convert_video_to_gif_ffmpeg(video_path: str, output_path: str, fps: int, width: int, height: int):
    """使用ffmpeg转换视频为GIF（高质量）"""
    try:
        # 使用ffmpeg进行高质量GIF转换
        # 两阶段处理：先生成调色板，再生成GIF
        palette_path = output_path.replace('.gif', '_palette.png')

        # 第一步：生成调色板
        palette_cmd = [
            'ffmpeg', '-i', video_path,
            '-vf', f'fps={fps},scale={width}:{height}:flags=lanczos,palettegen=stats_mode=diff',
            '-y', palette_path
        ]

        logger.info(f"执行调色板生成命令: {' '.join(palette_cmd)}")
        result = await asyncio.create_subprocess_exec(
            *palette_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await result.communicate()

        if result.returncode != 0:
            logger.error(f"调色板生成错误: {stderr.decode()}")
            # 清理可能生成的调色板文件
            if os.path.exists(palette_path):
                os.unlink(palette_path)
            return False

        # 第二步：使用调色板生成GIF
        gif_cmd = [
            'ffmpeg', '-i', video_path, '-i', palette_path,
            '-filter_complex',
            f'fps={fps},scale={width}:{height}:flags=lanczos[x];[x][1:v]paletteuse=dither=sierra2:diff_mode=rectangle',
            '-y', output_path
        ]

        logger.info(f"执行GIF生成命令: {' '.join(gif_cmd)}")
        result = await asyncio.create_subprocess_exec(
            *gif_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await result.communicate()

        # 清理调色板文件
        if os.path.exists(palette_path):
            os.unlink(palette_path)

        if result.returncode != 0:
            logger.error(f"GIF生成错误: {stderr.decode()}")
            return False

        return True
    except Exception as e:
        logger.error(f"ffmpeg转换失败: {e}")
        # 清理可能残留的调色板文件
        palette_path = output_path.replace('.gif', '_palette.png')
        if os.path.exists(palette_path):
            os.unlink(palette_path)
        return False


async def convert_video_to_gif_opencv(video_path: str, output_path: str, fps: int, width: int, height: int):
    """使用OpenCV转换视频为GIF（备选方案）"""
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise Exception("无法打开视频文件")

        frames = []
        original_fps = cap.get(cv2.CAP_PROP_FPS)
        frame_interval = max(1, int(original_fps / fps)) if original_fps > 0 else 1

        frame_count = 0
        success_count = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # 按帧间隔采样
            if frame_count % frame_interval == 0:
                # 转换BGR到RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

                # 调整尺寸
                if frame_rgb.shape[1] != width or frame_rgb.shape[0] != height:
                    frame_rgb = cv2.resize(frame_rgb, (width, height), interpolation=cv2.INTER_LANCZOS4)

                # 转换为PIL图像
                pil_image = Image.fromarray(frame_rgb)
                frames.append(pil_image)
                success_count += 1

            frame_count += 1

            # 安全限制，避免处理过长的视频
            if frame_count > 10000:  # 最多处理10000帧
                logger.warning("达到帧数限制，停止读取")
                break

        cap.release()

        if not frames:
            raise Exception("没有提取到任何帧")

        logger.info(f"成功提取 {success_count} 帧")

        # 计算持续时间（毫秒）
        duration = int(1000 / fps)

        # 保存为GIF
        frames[0].save(
            output_path,
            save_all=True,
            append_images=frames[1:],
            duration=duration,
            loop=0,
            optimize=True
        )

        return True

    except Exception as e:
        logger.error(f"OpenCV转换失败: {e}")
        return False


async def optimize_gif_parameters(video_info: dict) -> tuple:
    """根据视频信息优化GIF参数"""
    duration = video_info['duration']
    original_fps = video_info['fps']
    original_width = video_info['width']
    original_height = video_info['height']

    logger.info(f"原始视频参数: 时长{duration:.1f}s, FPS{original_fps:.1f}, 分辨率{original_width}x{original_height}")

    # 根据时长调整FPS
    if duration <= 5:
        target_fps = min(15, original_fps)  # 短视频保持较高帧率
    elif duration <= 15:
        target_fps = min(12, original_fps)
    elif duration <= 30:
        target_fps = min(8, original_fps)
    else:
        target_fps = min(5, original_fps)  # 长视频降低帧率

    # 确保FPS至少为1
    target_fps = max(1, target_fps)

    # 根据分辨率调整缩放
    max_dimension = max(original_width, original_height)
    if max_dimension <= 320:
        scale_width = original_width
        scale_height = original_height
    elif max_dimension <= 480:
        scale_factor = 320 / max_dimension
        scale_width = int(original_width * scale_factor)
        scale_height = int(original_height * scale_factor)
    elif max_dimension <= 720:
        scale_factor = 320 / max_dimension
        scale_width = int(original_width * scale_factor)
        scale_height = int(original_height * scale_factor)
    elif max_dimension <= 1080:
        scale_factor = 480 / max_dimension
        scale_width = int(original_width * scale_factor)
        scale_height = int(original_height * scale_factor)
    else:
        scale_factor = 480 / max_dimension
        scale_width = int(original_width * scale_factor)
        scale_height = int(original_height * scale_factor)

    # 确保尺寸为偶数（某些编码器要求）
    scale_width = scale_width if scale_width % 2 == 0 else scale_width + 1
    scale_height = scale_height if scale_height % 2 == 0 else scale_height + 1

    # 限制最小尺寸
    scale_width = max(scale_width, 32)
    scale_height = max(scale_height, 32)

    logger.info(f"优化参数: 目标FPS{target_fps}, 缩放{scale_width}x{scale_height}")
    return target_fps, scale_width, scale_height


def check_ffmpeg_available() -> bool:
    """检查ffmpeg是否可用（同步版本）"""
    global _ffmpeg_available

    if _ffmpeg_available is not None:
        return _ffmpeg_available

    try:
        result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, timeout=5)
        _ffmpeg_available = result.returncode == 0

        if not _ffmpeg_available:
            logger.warning("警告: ffmpeg未安装或不可用，将使用OpenCV进行视频转换，质量可能较低")
            logger.warning("建议安装ffmpeg以获得更好的GIF质量: sudo apt-get install ffmpeg")
        else:
            logger.info("ffmpeg可用，将使用高质量GIF转换")

        return _ffmpeg_available
    except:
        _ffmpeg_available = False
        logger.warning("警告: ffmpeg未安装或不可用，将使用OpenCV进行视频转换，质量可能较低")
        logger.warning("建议安装ffmpeg以获得更好的GIF质量: sudo apt-get install ffmpeg")
        return False


async def convert_video_to_gif(video_url: str, file_name: str = None, expected_size: int = 0,
                               access_token: str = None) -> str:
    """主函数：将视频转换为GIF"""
    video_path = None
    try:
        logger.info(f"开始处理视频转GIF")
        logger.info(f"视频URL: {video_url[:200]}...")
        if file_name:
            logger.info(f"文件名: {file_name}")
        if expected_size > 0:
            logger.info(f"预期文件大小: {expected_size} bytes")

        # 检查ffmpeg可用性
        ffmpeg_available = check_ffmpeg_available()

        # 下载视频，传递文件名、预期大小和access_token
        video_path = await download_video(video_url, file_name, expected_size, access_token)
        if not video_path or not os.path.exists(video_path):
            raise Exception("下载视频失败或文件不存在")

        file_size = os.path.getsize(video_path)
        logger.info(f"视频下载成功: {video_path}, 大小: {file_size} bytes")

        # 获取视频信息
        video_info = await get_video_info(video_path)
        duration = video_info['duration']

        # 检查视频时长
        if duration > 60:
            raise Exception(f"视频时长超过限制（{duration:.1f}秒 > 60秒），请选择较短的视频")

        # 调整时长下限
        # 只有在ffprobe估算时长 < 0.2 且 文件大小 < 100KB 时才认为是异常
        if duration < 0.2 and file_size < 100 * 1024:
            raise Exception(f"视频时长过短({duration:.2f}s)且文件过小，无法处理")
        elif duration < 0.1:  # 绝对下限
            raise Exception("视频时长过短，无法处理")

        # 优化参数
        fps, width, height = await optimize_gif_parameters(video_info)

        # 创建输出目录
        output_dir = Path(tempfile.gettempdir()) / "nonebot_video_to_gif"
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / f"video_gif_{os.urandom(4).hex()}.gif"

        logger.info(f"开始转换，参数: FPS={fps}, 分辨率={width}x{height}")

        # 根据ffmpeg可用性选择转换方法
        success = False
        if ffmpeg_available:
            # 优先使用ffmpeg（高质量）
            success = await convert_video_to_gif_ffmpeg(video_path, str(output_path), fps, width, height)

            if not success:
                logger.warning("ffmpeg转换失败，尝试OpenCV备选方案")
                success = await convert_video_to_gif_opencv(video_path, str(output_path), fps, width, height)
        else:
            # 直接使用OpenCV
            success = await convert_video_to_gif_opencv(video_path, str(output_path), fps, width, height)

        if not success:
            raise Exception("所有转换方法都失败了")

        result_size = os.path.getsize(output_path)
        logger.info(f"视频转GIF成功: {output_path}, 大小: {result_size} bytes")

        return str(output_path)

    except Exception as e:
        logger.error(f"视频转GIF处理出错: {e}")
        import traceback
        traceback.print_exc()
        return ""
    finally:
        # 清理下载的临时视频文件
        if video_path and os.path.exists(video_path):
            try:
                await safe_delete_file(video_path)
                logger.info(f"已清理临时视频文件: {video_path}")
            except Exception as delete_error:
                logger.error(f"清理临时文件失败: {delete_error}")


def get_supported_video_formats() -> list:
    """获取支持的视频格式"""
    return ['.mp4', '.avi', '.mov', '.webm', '.mkv', '.flv', '.wmv']


# 模块导入时同步检查ffmpeg
_ = check_ffmpeg_available()