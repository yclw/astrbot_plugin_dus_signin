import asyncio
import json
import re
import random
from typing import Dict, Optional
from dataclasses import dataclass, asdict
from datetime import datetime, time
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, StarTools, register
from astrbot.api import logger
import aiohttp

@dataclass
class SigninConfig:
    """用户签到配置"""
    cookie: str = ""
    lat: str = ""
    lng: str = ""
    class_id: str = ""
    auto_signin_enabled: bool = False
    auto_signin_time: str = "08:00"
    notification_targets: Dict[str, str] = None  # 通知目标 -> 通知级别映射
    offset: float = 0.000020  # 经纬度随机偏移值
    
    def __post_init__(self):
        if self.notification_targets is None:
            self.notification_targets = {}
    
    def is_complete(self) -> tuple[bool, str]:
        """检查配置是否完整"""
        if not self.cookie:
            return False, "Cookie未设置"
        if not self.lat:
            return False, "纬度未设置"
        if not self.lng:
            return False, "经度未设置"
        return True, ""

@register("dus_signin", "yclw", "DUS签到插件", "1.0.0", "https://github.com/yclw/astrbot_plugin_dus")
class DusSigninPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.user_configs: Dict[str, SigninConfig] = {}
        self.session: Optional[aiohttp.ClientSession] = None
        self.scheduled_tasks: Dict[str, asyncio.Task] = {}

    async def initialize(self):
        """插件初始化"""
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
        await self._load_user_configs()
        logger.info("DUS签到插件已初始化")

    async def terminate(self):
        """插件销毁时清理资源"""
        # 取消所有定时任务
        for task in self.scheduled_tasks.values():
            if not task.done():
                task.cancel()
        
        if self.scheduled_tasks:
            await asyncio.gather(*self.scheduled_tasks.values(), return_exceptions=True)
            
        # 保存用户配置
        await self._save_user_configs()
        
        # 关闭HTTP会话
        if self.session:
            await self.session.close()
            
        logger.info("DUS签到插件已清理资源")
        
    async def _load_user_configs(self):
        """加载用户配置"""
        try:
            # 从插件数据目录加载配置文件
            plugin_data_dir = StarTools.get_data_dir("dus_signin")
            config_file = plugin_data_dir / "dus_signin_configs.json"
            if config_file.exists():
                with open(config_file, 'r', encoding='utf-8') as f:
                    configs_data = json.load(f)
                    for user_id, config_data in configs_data.items():
                        # 兼容旧配置格式
                        if 'notification_level' in config_data and 'notification_target' in config_data:
                            # 旧格式转换为新格式
                            old_level = config_data.pop('notification_level', 'always')
                            old_target = config_data.pop('notification_target', '')
                            config_data['notification_targets'] = {old_target: old_level} if old_target else {}
                        
                        # 确保notification_targets字段存在
                        if 'notification_targets' not in config_data:
                            config_data['notification_targets'] = {}
                            
                        self.user_configs[user_id] = SigninConfig(**config_data)
                        
                        # 重新启动定时任务
                        if config_data.get('auto_signin_enabled', False):
                            await self._schedule_auto_signin(user_id)
                            
                logger.info(f"已加载 {len(self.user_configs)} 个用户配置")
        except Exception as e:
            logger.error(f"加载用户配置失败: {e}")
            
    async def _save_user_configs(self):
        """保存用户配置"""
        try:
            plugin_data_dir = StarTools.get_data_dir("dus_signin")
            config_file = plugin_data_dir / "dus_signin_configs.json"
            config_file.parent.mkdir(parents=True, exist_ok=True)
            
            configs_data = {
                user_id: asdict(config) 
                for user_id, config in self.user_configs.items()
            }
            
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(configs_data, f, ensure_ascii=False, indent=2)
                
        except Exception as e:
            logger.error(f"保存用户配置失败: {e}")
            
    def _get_user_config(self, user_id: str) -> SigninConfig:
        """获取用户配置"""
        if user_id not in self.user_configs:
            self.user_configs[user_id] = SigninConfig()
        return self.user_configs[user_id]
        
    async def _schedule_auto_signin(self, user_id: str):
        """为用户安排自动签到任务"""
        config = self._get_user_config(user_id)
        if not config.auto_signin_enabled:
            return
            
        # 取消现有任务
        if user_id in self.scheduled_tasks:
            self.scheduled_tasks[user_id].cancel()
            
        # 创建新的定时任务
        self.scheduled_tasks[user_id] = asyncio.create_task(
            self._auto_signin_task(user_id)
        )
        
    async def _auto_signin_task(self, user_id: str):
        """自动签到任务"""
        config = self._get_user_config(user_id)
        
        while config.auto_signin_enabled:
            try:
                # 解析时间
                hour, minute = map(int, config.auto_signin_time.split(':'))
                target_time = time(hour, minute)
                
                # 计算下次执行时间
                now = datetime.now()
                next_run = datetime.combine(now.date(), target_time)
                if next_run <= now:
                    next_run = next_run.replace(day=next_run.day + 1)
                    
                # 等待到指定时间
                sleep_seconds = (next_run - now).total_seconds()
                await asyncio.sleep(sleep_seconds)
                
                # 执行签到
                if config.auto_signin_enabled:  # 再次检查是否还启用
                    result = await self._perform_signin(config)
                    await self._send_signin_notification(config, result, user_id)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"自动签到任务错误 [{user_id}]: {e}")
                await asyncio.sleep(3600)  # 出错后等待1小时再重试
                
    async def _perform_signin(self, config: SigninConfig) -> dict:
        """执行签到操作"""
        headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 9; AKT-AK47 Build/USER-AK47; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/116.0.0.0 Mobile Safari/537.36 XWEB/1160065 MMWEBSDK/20231202 MMWEBID/1136 MicroMessenger/8.0.47.2560(0x28002F35) WeChat/arm64 Weixin NetType/4G Language/zh_CN ABI/arm64',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/wxpic,image/tpg,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Language': 'zh-CN,zh-SG;q=0.9,zh;q=0.8,en-SG;q=0.7,en-US;q=0.6,en;q=0.5',
            'Cookie': config.cookie,
            'X-Requested-With': 'com.tencent.mm'
        }
        
        try:
            # 如果没有class_id，获取班级列表
            if not config.class_id:
                class_id, class_name = await self._get_class_list(headers)
                if not class_id:
                    return {"success": False, "message": "未找到班级或获取班级列表失败"}
                config.class_id = class_id
                await self._save_user_configs()
            
            # 获取签到任务ID
            task_id = await self._get_task_id(config.class_id, headers)
            if not task_id:
                return {"success": False, "message": "未找到签到任务"}
                
            # 应用随机偏移
            lat_with_offset = self._apply_offset(config.lat, config.offset)
            lng_with_offset = self._apply_offset(config.lng, config.offset)
            
            # 执行签到
            result = await self._execute_signin(config.class_id, task_id, lat_with_offset, lng_with_offset, headers)
            return result
            
        except Exception as e:
            logger.error(f"签到操作失败: {e}")
            return {"success": False, "message": f"签到操作异常: {str(e)}"}
            
    async def _get_class_list(self, headers: dict) -> tuple[str, str]:
        """获取班级列表，返回第一个班级的ID和名称"""
        try:
            async with self.session.get("http://k8n.cn/student", headers=headers) as response:
                if response.status == 200:
                    content = await response.text()
                    
                    # 提取班级ID
                    class_ids = re.findall(r'course_id="(\d+)"', content)
                    if class_ids:
                        class_id = class_ids[0]
                        # 提取对应的班级名称
                        class_name_match = re.search(
                            rf'course_id="{class_id}".*?class="course_name"[^>]*>([^<]*)', 
                            content, re.DOTALL
                        )
                        class_name = class_name_match.group(1) if class_name_match else "未知班级"
                        return class_id, class_name
                        
        except Exception as e:
            logger.error(f"获取班级列表失败: {e}")
            
        return "", ""
        
    async def _get_task_id(self, class_id: str, headers: dict) -> str:
        """获取签到任务ID"""
        try:
            headers.update({
                'Referer': f'http://k8n.cn/student/course/{class_id}'
            })
            
            async with self.session.get(f"http://k8n.cn/student/course/{class_id}/punchs", headers=headers) as response:
                if response.status == 200:
                    content = await response.text()
                    # 提取任务ID
                    task_match = re.search(r'onclick="punch_gps\((\d+)\)"', content)
                    if task_match:
                        return task_match.group(1)
                        
        except Exception as e:
            logger.error(f"获取签到任务ID失败: {e}")
            
        return ""
        
    def _apply_offset(self, coordinate: str, offset: float) -> str:
        """对坐标应用随机偏移"""
        try:
            coord_float = float(coordinate)
            # 生成 -offset 到 +offset 之间的随机偏移
            random_offset = random.uniform(-offset, offset)
            new_coord = coord_float + random_offset
            return str(new_coord)
        except (ValueError, TypeError):
            # 如果坐标无法转换为数字，直接返回原值
            return coordinate
        
    async def _execute_signin(self, class_id: str, task_id: str, lat: str, lng: str, headers: dict) -> dict:
        """执行签到请求"""
        try:
            headers.update({
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': 'http://k8n.cn',
                'Referer': f'http://k8n.cn/student/course/{class_id}/punchs'
            })
            
            data = {
                'id': task_id,
                'lat': lat,
                'lng': lng,
                'acc': '10',
                'res': '',
                'gps_addr': ''
            }
            
            async with self.session.post(
                f"http://k8n.cn/student/punchs/course/{class_id}/{task_id}",
                headers=headers,
                data=data
            ) as response:
                
                content = await response.text()
                
                if "签到成功" in content:
                    return {"success": True, "message": "签到成功"}
                else:
                    return {"success": False, "message": "签到失败"}
                    
        except Exception as e:
            logger.error(f"执行签到请求失败: {e}")
            return {"success": False, "message": f"签到请求异常: {str(e)}"}
            
    async def _send_signin_notification(self, config: SigninConfig, result: dict, user_id: str):
        """发送签到通知"""
        message = f"自动签到结果: {result['message']}"
        
        # 向所有配置的通知目标发送通知
        for target, level in config.notification_targets.items():
            # 检查是否需要发送通知
            if level == "never":
                continue
            if level == "failure_only" and result["success"]:
                continue
                
            try:
                message_chain = MessageChain().message(message)
                await self.context.send_message(target, message_chain)
                logger.info(f"已发送签到通知到: {target} (级别: {level})")
            except Exception as e:
                logger.error(f"发送签到通知失败 [{target}]: {e}")
            
    @filter.command_group("signin")
    def signin_commands(self):
        """签到指令组"""
        pass
        
    @signin_commands.command("set")
    async def set_config(self, event: AstrMessageEvent, param: str, value: str = ""):
        """设置签到配置参数"""
        user_id = event.get_sender_id()
        config = self._get_user_config(user_id)
        
        param = param.lower()
        
        if param == "cookie":
            config.cookie = value
            await self._save_user_configs()
            yield event.plain_result("Cookie set successfully")
            
        elif param == "lat":
            config.lat = value
            await self._save_user_configs()
            yield event.plain_result(f"Latitude set to: {value}")
            
        elif param == "lng":
            config.lng = value
            await self._save_user_configs()
            yield event.plain_result(f"Longitude set to: {value}")
            
        elif param == "class_id":
            config.class_id = value
            await self._save_user_configs()
            yield event.plain_result(f"Class ID set to: {value}")
            
        elif param == "auto_time":
            if re.match(r'^\d{1,2}:\d{2}$', value):
                config.auto_signin_time = value
                await self._save_user_configs()
                
                # 重新安排定时任务
                if config.auto_signin_enabled:
                    await self._schedule_auto_signin(user_id)
                    
                yield event.plain_result(f"Auto signin time set to: {value}")
            else:
                yield event.plain_result("Time format error, please use HH:MM format, e.g.: 08:30")
                
        elif param == "auto_enable":
            if value.lower() in ["true", "1", "yes", "enable"]:
                config.auto_signin_enabled = True
                await self._save_user_configs()
                await self._schedule_auto_signin(user_id)
                yield event.plain_result("Auto signin enabled")
            elif value.lower() in ["false", "0", "no", "disable"]:
                config.auto_signin_enabled = False
                await self._save_user_configs()
                
                # 取消定时任务
                if user_id in self.scheduled_tasks:
                    self.scheduled_tasks[user_id].cancel()
                    del self.scheduled_tasks[user_id]
                    
                yield event.plain_result("Auto signin disabled")
            else:
                yield event.plain_result("Please use: enable/disable or true/false")
                
        elif param == "notification":
            if value in ["always", "never", "failure_only"]:
                # 在当前会话设置通知级别
                config.notification_targets[event.unified_msg_origin] = value
                
                await self._save_user_configs()
                
                # 判断会话类型给出提示
                session_type = "group" if event.get_group_id() else "private"
                yield event.plain_result(f"Notification level set to '{value}' for current {session_type} chat")
            else:
                yield event.plain_result("Notification level can only be: always/never/failure_only")
                
        elif param == "offset":
            try:
                offset_value = float(value)
                if offset_value < 0:
                    yield event.plain_result("Offset value cannot be negative")
                    return
                config.offset = offset_value
                await self._save_user_configs()
                yield event.plain_result(f"GPS offset set to: {offset_value}")
            except ValueError:
                yield event.plain_result("Invalid offset value, please enter a number")
                
        elif param == "remove_notification":
            if event.unified_msg_origin in config.notification_targets:
                del config.notification_targets[event.unified_msg_origin]
                await self._save_user_configs()
                session_type = "group" if event.get_group_id() else "private"
                yield event.plain_result(f"Notification settings removed for current {session_type} chat")
            else:
                yield event.plain_result("No notification settings for current chat")
        else:
            yield event.plain_result(
                "Available parameters:\n"
                "cookie <value> - Set login cookie\n"
                "lat <value> - Set latitude\n"
                "lng <value> - Set longitude\n"
                "class_id <value> - Set class ID\n"
                "offset <value> - Set GPS coordinate offset (default: 0.000020)\n"
                "auto_time <HH:MM> - Set auto signin time\n"
                "auto_enable <enable/disable> - Enable/disable auto signin\n"
                "notification <always/never/failure_only> - Set notification level for current chat\n"
                "remove_notification - Remove notification settings for current chat"
            )
            
    @signin_commands.command("now")
    async def manual_signin(self, event: AstrMessageEvent):
        """Execute signin immediately"""
        user_id = event.get_sender_id()
        config = self._get_user_config(user_id)
        
        # 检查配置完整性
        is_complete, error_msg = config.is_complete()
        if not is_complete:
            if error_msg == "Cookie未设置":
                yield event.plain_result("Please set cookie first: /signin set cookie <your_cookie>")
            elif error_msg == "纬度未设置":
                yield event.plain_result("Please set latitude first: /signin set lat <latitude_value>")
            elif error_msg == "经度未设置":
                yield event.plain_result("Please set longitude first: /signin set lng <longitude_value>")
            return
            
        if not config.class_id:
            # 获取班级列表
            headers = {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 9; AKT-AK47 Build/USER-AK47; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/116.0.0.0 Mobile Safari/537.36 XWEB/1160065 MMWEBSDK/20231202 MMWEBID/1136 MicroMessenger/8.0.47.2560(0x28002F35) WeChat/arm64 Weixin NetType/4G Language/zh_CN ABI/arm64',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/wxpic,image/tpg,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'zh-CN,zh-SG;q=0.9,zh;q=0.8,en-SG;q=0.7,en-US;q=0.6,en;q=0.5',
                'Cookie': config.cookie,
                'X-Requested-With': 'com.tencent.mm'
            }
            
            try:
                async with self.session.get("http://k8n.cn/student", headers=headers) as response:
                    if response.status == 200:
                        content = await response.text()
                        
                        # 提取所有班级信息
                        class_matches = re.findall(r'course_id="(\d+)"', content)
                        if not class_matches:
                            yield event.plain_result("No classes found")
                            return
                            
                        if len(class_matches) == 1:
                            # 只有一个班级，自动选择
                            config.class_id = class_matches[0]
                            await self._save_user_configs()
                        else:
                            # 多个班级，显示列表让用户选择
                            class_list = []
                            for i, class_id in enumerate(class_matches):
                                class_name_match = re.search(
                                    rf'course_id="{class_id}".*?class="course_name"[^>]*>([^<]*)', 
                                    content, re.DOTALL
                                )
                                class_name = class_name_match.group(1) if class_name_match else "Unknown Class"
                                class_list.append(f"{i+1}. {class_name} (ID: {class_id})")
                                
                            yield event.plain_result(
                                f"Found {len(class_matches)} classes:\n" + 
                                "\n".join(class_list) + 
                                "\n\nPlease use /signin set class_id <class_id> to set class"
                            )
                            return
                    else:
                        yield event.plain_result("Failed to get class list, please check if cookie is correct")
                        return
            except Exception as e:
                yield event.plain_result(f"Error getting class list: {str(e)}")
                return
                
        # 执行签到
        yield event.plain_result("Executing signin...")
        result = await self._perform_signin(config)
        
        if result["success"]:
            yield event.plain_result(f"✅ {result['message']}")
        else:
            yield event.plain_result(f"❌ {result['message']}")
            
    @signin_commands.command("config")
    async def view_config(self, event: AstrMessageEvent):
        """View current configuration"""
        user_id = event.get_sender_id()
        config = self._get_user_config(user_id)
        
        cookie_display = "Set" if config.cookie else "Not set"
        
        # 构建通知设置显示
        if config.notification_targets:
            notification_lines = []
            for target, level in config.notification_targets.items():
                # 简化显示目标（只显示部分ID）
                target_display = target[-10:] if len(target) > 10 else target
                notification_lines.append(f"  {target_display}: {level}")
            notification_text = "\n".join(notification_lines)
        else:
            notification_text = "  Not set"
        
        config_text = f"""Current Signin Configuration:
Cookie: {cookie_display}
Latitude: {config.lat or 'Not set'}
Longitude: {config.lng or 'Not set'}
Class ID: {config.class_id or 'Not set'}
GPS Offset: {config.offset}
Auto Signin: {'Enabled' if config.auto_signin_enabled else 'Disabled'}
Signin Time: {config.auto_signin_time}
Notification Settings:
{notification_text}"""
        
        yield event.plain_result(config_text)
        
    @signin_commands.command("help")
    async def show_help(self, event: AstrMessageEvent):
        """Show help information"""
        help_text = """DUS Signin Plugin Usage:

🔧 Configuration Commands:
/signin set cookie <value> - Set login cookie
/signin set lat <value> - Set latitude coordinate
/signin set lng <value> - Set longitude coordinate
/signin set class_id <value> - Set class ID
/signin set offset <value> - Set GPS coordinate offset (default: 0.000020)
/signin set auto_time <HH:MM> - Set auto signin time
/signin set auto_enable <enable/disable> - Enable/disable auto signin
/signin set notification <always/never/failure_only> - Set notification level for current chat
/signin set remove_notification - Remove notification settings for current chat

📱 Function Commands:
/signin now - Execute signin immediately
/signin config - View current configuration
/signin help - Show this help

💡 Notification Features:
- Different notification levels can be set for different chats
- Private chat: recommended "always", Group chat: recommended "failure_only"
- Example: Private chat set to "always", Group chat set to "failure_only"
- Signin results will be notified according to each chat's settings

⚠️ Notes:
1. Cookie/latitude/longitude are required parameters
2. Class ID will auto-fetch class list when empty
3. Support multi-chat notifications, each chat can set different notification levels"""
        
        yield event.plain_result(help_text)
