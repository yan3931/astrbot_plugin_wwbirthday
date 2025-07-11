import asyncio
import datetime
import json
import os.path

import aiohttp

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig
from astrbot.api import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core.message.message_event_result import MessageChain


class DataDownloadError(Exception):
    pass


@register("astrbot_plugin_wwbirthday", "arkina", "鸣潮角色生日播报", "1.0.2")
class WWBirthday(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        # 使用StarTools获取标准数据目录
        self.plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.plugin_dir = os.path.join(self.plugin_dir, "astrbot_plugin_wwbirthday")

        self.data_file = os.path.join(self.plugin_dir, "characters.json")
        self.data_dir = os.path.join(self.plugin_dir, "characters")
        os.makedirs(self.data_dir, exist_ok=True)

        self.image_download = self.config.get("image_download", False)
        self.image_timeout = self.config.get("image_timeout", 10)
        self.isphoto = self.config.get("isphoto", True)
        self.execute_time = self.config.get("time", "9:0")

        group_list = self.config.get("list", "")
        if not group_list or not group_list.strip():
            groups = set()
        else:
            groups = set()
            for group_id in group_list.split(","):
                group_id = group_id.strip()
                if group_id:
                    groups.add(group_id)

        self.group_ids = groups

        self.daily_task_handle = asyncio.create_task(self.daily_task())
        logger.info(f"[wwbirthday] 插件加载成功！版本 v1.0.2")
        logger.info(f"[wwbirthday] 配置: 图片发送={self.isphoto}, 定时时间={self.execute_time}")
        logger.info(f"[wwbirthday] 启用群组: {len(self.group_ids)}个")

    async def terminate(self):
        """异步清理资源（插件卸载时调用）"""
        logger.info("[wwbirthday] 插件终止中……")

        # 取消后台任务
        if hasattr(self, 'daily_task_handle') and not self.daily_task_handle.done():
            try:
                self.daily_task_handle.cancel()
                # 等待任务完成取消
                try:
                    await self.daily_task_handle
                except asyncio.CancelledError:
                    logger.info("[wwbirthday] 定时任务已取消")
                except Exception as e:
                    logger.error(f"任务取消过程中出错: {e}")
            except Exception as e:
                logger.error(f"取消任务时出错: {e}")

        logger.info("[wwbirthday] 插件已卸载")

    async def download_image(self, url: str, char_id: int):
        """下载并保存角色图片"""
        if not url.startswith(('http://', 'https://')):
            raise ValueError("非法的图片URL格式")

        filename = f"{char_id}.png"
        save_path = os.path.join(self.data_dir, filename)

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
        downloaded_path = os.path.join(self.data_dir, f"{char['id']}.png")
        if os.path.exists(downloaded_path):
            return downloaded_path

        # 网络下载模式
        if self.image_download and char.get("image_url"):
            if await self.download_image(char["image_url"], char["id"]):
                return downloaded_path

        return None  # 无可用图片

    async def today_birthdays(self):
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                characters = json.load(f)
        except FileNotFoundError:
            logger.error("角色数据文件不存在")
            return

        # 使用f-string统一日期格式
        today = datetime.date.today()
        today_str = f"{today.month}-{today.day}"
        today_chars = [c for c in characters if c.get("birthday") == today_str]

        if not today_chars:
            logger.info(f"今天没有角色过生日: {today_str}")
            return

        for char in today_chars:
            message = char.get("quote", "")
            chain = MessageChain().message(message)

            if self.isphoto:
                img_path = await self.load_character_image(char)
                if img_path:
                    chain = chain.file_image(img_path)
                else:
                    logger.warning(f"角色 {char['name']} 图片不可用")

            for group_id in self.group_ids:
                group_id = f"aiocqhttp:GroupMessage:{group_id}"
                await self.context.send_message(group_id, chain)

    def sleeptime(self):
        now = datetime.datetime.now()
        hour, minute = map(int, self.execute_time.split(":"))
        target_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        if target_time <= now:
            target_time += datetime.timedelta(days=1)

        return (target_time - now).total_seconds()

    async def daily_task(self):
        try:
            while True:
                sleep_time = self.sleeptime()
                logger.info(f"[wwbirthday] 下次检查: {sleep_time / 3600:.1f}小时后")
                await asyncio.sleep(sleep_time)
                await self.today_birthdays()
                # 避免重复执行
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info("定时任务被取消")
            raise
        except Exception as e:
            logger.error(f"定时任务异常: {e}")
            # 任务异常后重新启动
            self.daily_task_handle = asyncio.create_task(self.daily_task())

    async def update_characters(self):
        """更新角色数据并同步图片"""
        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if self.image_download:
                download_tasks = []
                logger.info("开始同步角色图片...")
                for char in data:
                    if char.get("image_url") and char.get("id"):
                        download_tasks.append(self.download_image(char["image_url"], char["id"]))

                results = await asyncio.gather(*download_tasks)
                success_count = sum(1 for r in results if r)
                logger.info(f"图片同步完成: 成功{success_count}/{len(results)}")
                return success_count, len(data)

            return 0, 0
        except json.JSONDecodeError:
            raise DataDownloadError("数据文件JSON格式错误")
        except FileNotFoundError:
            raise DataDownloadError("角色数据文件不存在")

    @filter.command("ww数据更新")
    async def update_chars_command(self, event: AstrMessageEvent):
        """手动更新角色数据命令"""
        try:
            success_count, total_count = await self.update_characters()
            msg = "✅角色数据更新成功！"
            if self.image_download:
                msg += f"\n已下载 {success_count}/{total_count} 个角色图片"
            yield event.plain_result(msg)
        except DataDownloadError as e:
            yield event.plain_result(f"❌更新失败: {str(e)}")
        except Exception as e:
            logger.error(f"更新数据时出错: {str(e)}", exc_info=True)
            yield event.plain_result(f"⚠️更新数据时发生错误: {str(e)}")

    @filter.command("ww生日")
    async def get_birthday(self, event: AstrMessageEvent):
        """手动获取今日生日角色"""
        try:
            if not os.path.exists(self.data_file):
                yield event.plain_result("❌角色数据不存在，请先使用 /ww数据更新 命令")
                return

            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            today = datetime.date.today()
            today_str = f"{today.month}-{today.day}"
            today_chars = [char for char in data if char.get("birthday") == today_str]

            if not today_chars:
                yield event.plain_result("⏳今天没有角色过生日哦~")
                return

            if len(today_chars) == 1:
                char = today_chars[0]

                if self.isphoto:
                    image_path = os.path.join(self.data_dir, f"{char['id']}.png")
                    if os.path.exists(image_path):
                        chain = [Comp.Plain(char.get("quote")), Comp.Image.fromFileSystem(image_path)]
                    else:
                        chain = [Comp.Plain("⚠️角色图片不可用")]

                    yield event.chain_result(chain)
            else:
                response = f"🎉今天是{len(today_chars)}位角色的生日：\n"
                response += "\n".join(f"- {char['name']}: {char.get('quote', '')[:50]}..." for char in today_chars)
                yield event.plain_result(response)

        except Exception as e:
            logger.error(f"获取生日信息出错: {str(e)}", exc_info=True)
            yield event.plain_result("⚠️获取生日信息时出错")

    @filter.command("ww生日enable")
    async def enable_group_command(self, event: AstrMessageEvent):
        group_id = event.get_group_id()

        try:
            self.group_ids.add(group_id)

            try:
                enabled_str = ",".join(self.group_ids)
                self.config["list"] = enabled_str

                if hasattr(self.config, "save_config") and callable(getattr(self.config, "save_config")):
                    self.config.save_config()
                    logger.info("更新并保存了群组配置")

            except Exception as config_error:
                logger.error(f"保存群组配置失败: {config_error}")

            yield event.plain_result(f"已为群 {group_id} 启用鸣潮角色生日播报功能")

        except Exception as e:
            logger.error(f"启用鸣潮角色生日播报功能失败: {e}")
            yield event.plain_result(f"启用鸣潮角色生日播报功能失败: {str(e)}")

    @filter.command("ww生日disable")
    async def disable_group_command(self, event: AstrMessageEvent):
        group_id = event.get_group_id()

        if group_id in self.group_ids:
            self.group_ids.remove(group_id)
            logger.info(f"已从白名单中移除群: {group_id}")

        try:
            # 更新配置
            enabled_str = ",".join(self.group_ids)
            self.config["list"] = enabled_str

            # 保存配置
            if hasattr(self.config, "save_config") and callable(getattr(self.config, "save_config")):
                self.config.save_config()
                logger.info("更新并保存了群组配置")

        except Exception as config_error:
            logger.error(f"保存群组配置失败: {config_error}")

        yield event.plain_result(f"已为群 {group_id} 禁用鸣潮角色生日播报功能")
