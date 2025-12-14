import random
from pathlib import Path
import re
import nonebot_plugin_localstore as store
from nonebot import logger

from .._config import PixModel, PixResult, config, token
from ..utils import AsyncHttpx

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.6;"
    " rv:2.0.1) Gecko/20100101 Firefox/4.0.1",
    "Referer": "https://www.pixiv.net/",
}

TEMP_PATH = store.get_plugin_cache_dir()


class PixManage:
    @classmethod
    async def get_pix(
        cls,
        tags: tuple[str, ...],
        num: int,
        is_r18: bool,
        ai: bool | None,
        nsfw: tuple[int, ...],
        ratio_tuple: list[float] | None,
    ) -> PixResult[list[PixModel]]:
        """获取图片

        参数:
            tags: tags，包含uid和pid
            num: 数量

        返回:
            list[PixGallery]: 图片数据列表
        """
        api = f"{config.zxpix_api}/pix/get_pix"
        json_data = {
            "tags": tags,
            "num": num,
            "r18": is_r18,
            "ai": ai,
            "size": config.zxpix_image_size,
            "nsfw_tag": nsfw or None,
            "ratio": ratio_tuple,
        }
        logger.debug(f"尝试调用pix api: {api}, 参数: {json_data}")
        headers = None
        headers = {"Authorization": token.token} if token.token else None
        res = await AsyncHttpx.post(api, json=json_data, headers=headers)
        res.raise_for_status()
        res_data = res.json()
        res_data["data"] = [PixModel(**item) for item in res_data["data"]]
        return PixResult[list[PixModel]](**res_data)

    @classmethod
    async def get_image(cls, pix: PixModel, is_original: bool = False, page_index: int = None) -> Path | None:
        """获取图片 (支持多图页码 + 官方原图直连试错)"""

        # 1. 确定当前要下载第几页
        # 如果传入了 page_index，就用传入的；否则用 pix.img_p (API 指定的那一页)
        current_p = page_index if page_index is not None else int(pix.img_p)

        # 2. 强制原图逻辑 (配合配置文件)
        if config.zxpix_image_size == "original":
            is_original = True

        url = pix.url

        # --- 官方直连模式 (当没有配置反代，且需要原图时) ---
        if is_original and not config.zxpix_nginx:
            # 尝试从 API 给的 Master 链接还原出 Original 链接
            if "img-master" in url:
                # 替换路径核心部分
                base_url = url.replace("/img-master/", "/img-original/")
                base_url = base_url.replace("_master1200", "")

                # 强制使用官方图床域名 i.pximg.net
                if "/img-original/" in base_url:
                    base_url = "https://i.pximg.net/img-original/" + base_url.split("/img-original/")[1]

                # [关键] 使用正则将 URL 中的 pX 替换为当前页码 p{current_p}
                # 例如: 123456_p0.jpg -> 123456_p1.jpg
                base_url = re.sub(r'_p\d+\.', f'_p{current_p}.', base_url)

                # 后缀名自动试错逻辑
                original_ext = base_url.split(".")[-1]
                possible_exts = ["jpg", "png", "gif"]
                # 优先尝试原本的后缀
                if original_ext in possible_exts:
                    possible_exts.remove(original_ext)
                possible_exts.insert(0, original_ext)

                url_without_ext = base_url.rsplit(".", 1)[0]

                for ext in possible_exts:
                    try_url = f"{url_without_ext}.{ext}"
                    # 文件名加上页码区分
                    file = TEMP_PATH / f"pix_{pix.pid}_{current_p}_{random.randint(1, 1000)}.{ext}"

                    logger.debug(f"尝试下载官方原图 (P{current_p}): {try_url}")
                    if await AsyncHttpx.download_file(
                            try_url, file, headers=headers, timeout=config.zxpix_timeout, verify=False
                    ):
                        return file
                # 所有后缀都失败
                return None

        # --- 反代模式 (备用逻辑) ---
        if "limit_sanity_level" in url or (is_original and config.zxpix_nginx):
            image_type = url.split(".")[-1]
            if pix.is_multiple:
                # 构造反代链接: PID-页码.ext (注意：pixiv.re/cat 页码通常从1开始，对应p0)
                url = f"https://{config.zxpix_nginx}/{pix.pid}-{current_p + 1}.{image_type}"
            else:
                url = f"https://{config.zxpix_nginx}/{pix.pid}.{image_type}"
        elif config.zxpix_small_nginx:
            if "img-master" in url:
                url = "img-master" + url.split("img-master")[-1]
            elif "img-original" in url:
                url = "img-original" + url.split("img-original")[-1]
            url = f"https://{config.zxpix_small_nginx}/{url}"

        file = TEMP_PATH / f"pix_{pix.pid}_{current_p}_{random.randint(1, 1000)}.png"
        return (
            file
            if await AsyncHttpx.download_file(
                url, file, headers=headers, timeout=config.zxpix_timeout, verify=False
            )
            else None
        )

    @classmethod
    async def get_pix_result(cls, pix: PixModel) -> tuple[list, PixModel]:
        """构建返回消息

        参数:
            pix: PixGallery

        返回:
            list: 返回消息
        """
        if not (image := await cls.get_image(pix)):
            return [f"获取图片 pid: {pix.pid} 失败，可能是不存在此pid图片。"], pix
        message_list = []
        if config.zxpix_show_info:
            message_list.append(
                f"title: {pix.title}\n"
                f"author: {pix.author}\n"
                f"pid: {pix.pid}\nuid: {pix.uid}\n",
            )
        message_list.append(image)
        return message_list, pix
