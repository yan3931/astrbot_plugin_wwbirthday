import asyncio
import datetime
import json
import os.path

import aiohttp

from astrbot.api import AstrBotConfig
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import MessageChain


class DataDownloadError(Exception):
    pass


@register("astrbot_plugin_wwbirthday", "arkina", "鸣潮角色生日播报", "1.0.0")
class WWBirthday(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self.plugin_dir = os.path.join("data", "plugins", "astrbot_plugin_wwbirthday")
        self.data_file = os.path.join(self.plugin_dir, "characters.json")
        self.char_image_dir = os.path.join(self.plugin_dir, "characters")
        os.makedirs(self.char_image_dir, exist_ok=True)

        self.image_download = self.config.get("image_download", False)  # 新增配置项
        self.image_timeout = self.config.get("image_timeout", 10)  # 下载超时(秒)
        self.isphoto = self.config.get("isphoto", True)
        self.group_ids = self.config.get("list", [])
        self.execute_time = self.config.get("time", "9:0")

        asyncio.create_task(self.daily_task())
        logger.info(f"[wwbirthday] 插件加载成功！版本 v1.0")
        logger.info(f"[wwbirthday] 配置: 图片发送={self.isphoto}, 定时时间={self.execute_time}")
        logger.info(f"[wwbirthday] 启用群组: {len(self.group_ids)}个")

    async def download_image(self, url: str, char_id: int):
        """下载并保存角色图片"""
        if not url.startswith(('http://', 'https://')):
            raise ValueError("非法的图片URL格式")

        filename = f"{char_id}.png"
        save_path = os.path.join(self.char_image_dir, filename)

        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=self.image_timeout)) as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        with open(save_path, "wb") as f:
                            f.write(await response.read())
                        return True
                    logger.warning(f"图片下载失败 HTTP {response.status}: {url}")
        except Exception as e:
            logger.error(f"下载图片异常: {str(e)}")
        return False

    async def load_character_image(self, char: dict):
        """智能加载角色图片"""
        # 优先使用本地缓存
        local_path = os.path.join(self.plugin_dir, char.get("local_image", ""))
        if os.path.exists(local_path):
            return local_path

        # 检查网络下载的图片是否存在
        downloaded_path = os.path.join(self.char_image_dir, f"{char['id']}.png")
        if os.path.exists(downloaded_path):
            return downloaded_path

        # 网络下载模式
        if self.image_download and char.get("image_url"):
            if await self.download_image(char["image_url"], char["id"]):
                return downloaded_path

        return None  # 无可用图片

    async def today_birthdays(self):
        with open(self.data_file, "r", encoding="utf-8") as f:
            characters = json.load(f)

        today_str = datetime.date.today().strftime("%-m-%-d")
        for char in [c for c in characters if c["birthday"] == today_str]:
            # 构建消息
            message = char["quote"]
            # 发送逻辑
            chain = MessageChain().message(message)
            if self.isphoto:
                img_path = await self.load_character_image(char)
                if img_path:
                    chain = chain.file_image(img_path)

            for group_id in self.group_ids:
                await self.context.send_message(group_id, chain)

    def sleeptime(self):
        now = datetime.datetime.now()
        hour, minute = map(int, self.execute_time.split(":"))
        tomorrow = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if tomorrow <= now:
            tomorrow += datetime.timedelta(days=1)
        seconds = (tomorrow - now).total_seconds()
        return seconds

    async def daily_task(self):
        while True:
            try:
                sleep_time = self.sleeptime()
                logger.info(f"[wwbirthday]下一次生日检查将在 {sleep_time / 3600:.1f} 小时后进行")
                await asyncio.sleep(sleep_time)
                await self.today_birthdays()
                await asyncio.sleep(60)  # 避免重复执行
            except Exception as e:
                logger.error(f"定时任务执行失败: {e}")
                await asyncio.sleep(300)

    async def update_characters(self):
        """更新角色数据并同步图片"""
        try:
            # 加载本地数据文件
            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 图片下载处理
            if self.image_download:
                download_tasks = []
                logger.info("开始同步角色图片...")
                for char in data:
                    if char.get("image_url") and char.get("id"):
                        download_tasks.append(self.download_image(char["image_url"], char["id"]))

                results = await asyncio.gather(*download_tasks)
                logger.info(f"图片同步完成，成功{sum(results)}/{len(results)}")

            return data
        except json.JSONDecodeError:
            raise DataDownloadError("数据文件JSON格式错误")
        except FileNotFoundError:
            raise DataDownloadError("角色数据文件不存在")

    @filter.command("ww数据更新")
    async def update_chars_command(self, event: AstrMessageEvent):
        """手动更新角色数据命令"""
        try:
            # 执行数据更新
            data = await self.update_characters()

            # 构建响应消息
            msg = "✅角色数据更新成功！"
            if self.image_download:
                # 统计成功下载的图片数量
                image_count = sum(
                    [1 for char in data if os.path.exists(os.path.join(self.char_image_dir, f"{char['id']}.png"))])
                msg += f"\n已下载 {image_count}/{len(data)} 个角色图片"

            # 返回成功消息
            yield event.plain_result(msg)

        except DataDownloadError as e:
            # 返回错误消息
            yield event.plain_result(f"❌更新失败: {str(e)}")
        except Exception as e:
            logger.error(f"更新数据时出错: {str(e)}")
            yield event.plain_result(f"⚠️更新数据时发生错误: {str(e)}")

    @filter.command("ww生日")
    async def get_birthday(self, event: AstrMessageEvent):
        """手动获取今日生日角色"""
        try:
            if not os.path.exists(self.data_file):
                yield event.plain_result("❌角色数据不存在，请先使用/mc数据更新命令")
                return

            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            today = datetime.date.today()
            today_str = f"{today.month}-{today.day}"
            today_chars = [char for char in data if char.get("birthday") == today_str]

            if not today_chars:
                yield event.plain_result("⏳今天没有角色过生日哦~")
                return

            # 对于单个角色生日
            if len(today_chars) == 1:
                char = today_chars[0]
                message = char.get("quote", "")

                # 发送生日消息
                yield event.plain_result(message)

                # 发送角色图片（如果启用）
                if self.isphoto:
                    image_path = os.path.join(self.char_image_dir, f"{char['id']}.png")
                    if os.path.exists(image_path):
                        yield event.image_result(image_path)
                    else:
                        yield event.plain_result("⚠️角色图片不可用")

            # 对于多个角色生日
            else:
                response = f"🎉今天是{len(today_chars)}位角色的生日：\n"
                for char in today_chars:
                    preview = char.get("quote", "")[:50] + "..." if char.get("quote") else ""
                    response += f"\n- {char['name']}: {preview}"

                yield event.plain_result(response)

        except Exception as e:
            logger.error(f"获取生日信息出错: {str(e)}")
            yield event.plain_result("⚠️获取生日信息时出错")

    @filter.command("ww本周生日")
    async def week_birthdays(self, event: AstrMessageEvent):
        """获取本周剩余天数的角色生日"""
        try:
            if not os.path.exists(self.data_file):
                yield event.plain_result("❌角色数据不存在，请先更新数据")
                return

            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            today = datetime.date.today()
            current_weekday = today.isoweekday()
            days_until_sunday = 7 - current_weekday
            dates = [today + datetime.timedelta(days=i) for i in range(1, days_until_sunday + 1)]

            birthday_dict = {}
            for char in data:
                if birthday := char.get("birthday"):
                    birthday_dict.setdefault(birthday, []).append(char)

            # 构建消息链
            chain = MessageChain().message("🎂本周剩余生日角色：\n")
            found = False

            for d in dates:
                date_str = f"{d.month}-{d.day}"
                if chars := birthday_dict.get(date_str):
                    found = True
                    chain = chain.message(f"\n📅{d.month}月{d.day}日：")
                    for char in chars:
                        # 添加角色专属介绍预览
                        preview = char.get("quote", "")[:20] + "..." if char.get("quote") else ""
                        chain = chain.message(f"\n - {char['name']}：{preview}")

            if not found:
                chain = chain.message("\n本周没有其他角色过生日了~")

            yield event.chain_result(chain)

        except Exception as e:
            logger.error(f"获取本周生日出错: {e}")
            yield event.plain_result("⚠️获取本周生日时出错")
