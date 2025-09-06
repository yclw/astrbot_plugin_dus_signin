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
import astrbot.api.message_components as Comp
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
    notification_types: Dict[str, str] = None  # 通知目标 -> 消息类型映射 (group/private)
    offset: float = 0.000020  # 经纬度随机偏移值
    
    def __post_init__(self):
        if self.notification_targets is None:
            self.notification_targets = {}
        if self.notification_types is None:
            self.notification_types = {}
    
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
                        
                        # 确保notification_types字段存在
                        if 'notification_types' not in config_data:
                            config_data['notification_types'] = {}
                            
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
        logger.info("开始执行签到操作")
        logger.info(f"配置信息 - 班级ID: {config.class_id}, 纬度: {config.lat}, 经度: {config.lng}, GPS偏移: {config.offset}")
        
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
                logger.info("班级ID为空，正在获取班级列表")
                class_id, class_name = await self._get_class_list(headers)
                if not class_id:
                    logger.error("获取班级列表失败或未找到班级")
                    return {"success": False, "message": "未找到班级或获取班级列表失败"}
                logger.info(f"自动获取到班级: {class_name} (ID: {class_id})")
                config.class_id = class_id
                await self._save_user_configs()
            else:
                logger.info(f"使用已配置的班级ID: {config.class_id}")
            
            # 获取签到任务ID
            logger.info("正在获取签到任务ID")
            task_id = await self._get_task_id(config.class_id, headers)
            if not task_id:
                logger.error("未能获取到签到任务ID")
                return {"success": False, "message": "未找到签到任务"}
            logger.info(f"成功获取签到任务ID: {task_id}")
                
            # 应用随机偏移
            lat_with_offset = self._apply_offset(config.lat, config.offset)
            lng_with_offset = self._apply_offset(config.lng, config.offset)
            logger.info(f"坐标处理 - 原始: ({config.lat}, {config.lng}), 偏移后: ({lat_with_offset}, {lng_with_offset})")
            
            # 执行签到
            logger.info("正在执行签到请求")
            result = await self._execute_signin(config.class_id, task_id, lat_with_offset, lng_with_offset, headers)
            logger.info(f"签到结果: {result}")
            return result
            
        except Exception as e:
            logger.error(f"签到操作失败: {e}")
            import traceback
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return {"success": False, "message": f"签到操作异常: {str(e)}"}
            
    async def _get_class_list(self, headers: dict) -> tuple[str, str]:
        """获取班级列表，返回第一个班级的ID和名称"""
        try:
            url = "http://k8n.cn/student"
            logger.info(f"正在请求学生主页获取班级列表: {url}")
            
            async with self.session.get(url, headers=headers) as response:
                logger.info(f"学生主页响应状态: {response.status}")
                
                if response.status == 200:
                    content = await response.text()
                    logger.info(f"学生主页内容长度: {len(content)}")
                    
                    # 提取班级ID
                    class_ids = re.findall(r'course_id="(\d+)"', content)
                    logger.info(f"找到班级ID数量: {len(class_ids)}")
                    
                    if class_ids:
                        for i, cid in enumerate(class_ids[:5]):  # 记录前5个班级ID
                            logger.info(f"班级ID {i+1}: {cid}")
                            
                        class_id = class_ids[0]
                        logger.info(f"选择第一个班级ID: {class_id}")
                        
                        # 提取对应的班级名称
                        class_name_match = re.search(
                            rf'course_id="{class_id}".*?class="course_name"[^>]*>([^<]*)', 
                            content, re.DOTALL
                        )
                        class_name = class_name_match.group(1) if class_name_match else "未知班级"
                        logger.info(f"班级名称: {class_name}")
                        
                        return class_id, class_name
                    else:
                        logger.warning("页面中未找到班级ID")
                        # 记录可能的错误信息
                        if "登录" in content or "login" in content.lower():
                            logger.error("页面显示需要登录，Cookie可能已过期")
                        elif "错误" in content or "error" in content.lower():
                            logger.error("页面显示错误信息")
                        else:
                            logger.info("页面内容预览（前500字符）：")
                            content_preview = content[:500] if len(content) > 500 else content
                            logger.info(content_preview)
                else:
                    logger.error(f"请求学生主页失败，状态码: {response.status}")
                        
        except Exception as e:
            logger.error(f"获取班级列表失败: {e}")
            import traceback
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            
        return "", ""
        
    async def _get_task_id(self, class_id: str, headers: dict) -> str:
        """获取签到任务ID"""
        try:
            headers.update({
                'Referer': f'http://k8n.cn/student/course/{class_id}'
            })
            
            url = f"http://k8n.cn/student/course/{class_id}/punchs"
            logger.info(f"正在请求签到任务页面: {url}")
            logger.info(f"使用班级ID: {class_id}")
            
            async with self.session.get(url, headers=headers) as response:
                logger.info(f"签到任务页面响应状态: {response.status}")
                
                if response.status == 200:
                    content = await response.text()
                    logger.info(f"签到任务页面内容长度: {len(content)}")
                    
                    # 记录页面的关键部分用于调试
                    if "punch_gps" in content:
                        logger.info("页面中包含 punch_gps 相关内容")
                        # 提取包含 punch_gps 的行
                        lines_with_punch = [line.strip() for line in content.split('\n') if 'punch_gps' in line]
                        for i, line in enumerate(lines_with_punch[:5]):  # 只记录前5行
                            logger.info(f"包含punch_gps的行 {i+1}: {line[:200]}")
                    else:
                        logger.warning("页面中未找到 punch_gps 相关内容")
                        # 记录页面的部分内容用于分析
                        content_preview = content[:1000] if len(content) > 1000 else content
                        logger.info(f"页面内容预览: {content_preview}")
                    
                    # 提取任务ID - 原有的正则表达式
                    task_match = re.search(r'onclick="punch_gps\((\d+)\)"', content)
                    if task_match:
                        task_id = task_match.group(1)
                        logger.info(f"成功找到签到任务ID: {task_id}")
                        return task_id
                    else:
                        logger.warning("使用原有正则表达式未找到签到任务ID")
                        
                        # 尝试其他可能的匹配模式
                        alternative_patterns = [
                            r'punch_gps\((\d+)\)',  # 不限制onclick
                            r'data-id="(\d+)".*punch',  # data-id属性
                            r'id="(\d+)".*punch',  # id属性
                            r'/punchs.*?(\d+)',  # URL中的数字
                            r'task.*?(\d+)',  # task相关的数字
                        ]
                        
                        for pattern in alternative_patterns:
                            alt_match = re.search(pattern, content, re.IGNORECASE)
                            if alt_match:
                                task_id = alt_match.group(1)
                                logger.info(f"使用替代模式 '{pattern}' 找到可能的任务ID: {task_id}")
                                return task_id
                        
                        logger.error("所有匹配模式都未找到签到任务ID")
                        
                        # 如果还是找不到，记录更多有用信息
                        if "签到" in content or "打卡" in content:
                            logger.info("页面中包含签到或打卡相关内容，但无法提取任务ID")
                        if "没有签到任务" in content or "无签到任务" in content:
                            logger.warning("页面显示没有签到任务")
                        if "已签到" in content or "签到成功" in content:
                            logger.info("页面显示已经签到")
                            
                else:
                    logger.error(f"签到任务页面请求失败，状态码: {response.status}")
                    
        except Exception as e:
            logger.error(f"获取签到任务ID失败: {e}")
            import traceback
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            
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
            
            url = f"http://k8n.cn/student/punchs/course/{class_id}/{task_id}"
            logger.info(f"正在发送签到请求到: {url}")
            logger.info(f"请求数据: {data}")
            
            async with self.session.post(url, headers=headers, data=data) as response:
                logger.info(f"签到请求响应状态: {response.status}")
                
                content = await response.text()
                logger.info(f"签到响应内容长度: {len(content)}")
                
                # 记录响应内容用于调试
                if content:
                    logger.info(f"签到响应内容预览: {content[:500]}")
                    
                    # 检查各种可能的响应内容
                    if "签到成功" in content:
                        logger.info("检测到签到成功标识")
                        return {"success": True, "message": "签到成功"}
                    elif "已签到" in content:
                        logger.info("检测到已签到标识")
                        return {"success": True, "message": "已经签到"}
                    elif "签到失败" in content:
                        logger.warning("检测到签到失败标识")
                        return {"success": False, "message": "签到失败"}
                    elif "距离过远" in content or "位置不符" in content:
                        logger.warning("检测到距离相关错误")
                        return {"success": False, "message": "签到位置距离过远"}
                    elif "时间不符" in content or "非签到时间" in content:
                        logger.warning("检测到时间相关错误")
                        return {"success": False, "message": "不在签到时间范围内"}
                    elif "任务不存在" in content or "无效任务" in content:
                        logger.warning("检测到任务无效错误")
                        return {"success": False, "message": "签到任务无效或不存在"}
                    else:
                        logger.warning("未识别的响应内容")
                        # 记录完整内容用于分析
                        logger.info(f"完整响应内容: {content}")
                        return {"success": False, "message": f"签到状态未知: {content[:100]}"}
                else:
                    logger.error("签到响应内容为空")
                    return {"success": False, "message": "签到响应为空"}
                    
        except Exception as e:
            logger.error(f"执行签到请求失败: {e}")
            import traceback
            logger.error(f"详细错误信息: {traceback.format_exc()}")
            return {"success": False, "message": f"签到请求异常: {str(e)}"}
            
    async def _send_signin_notification(self, config: SigninConfig, result: dict, user_id: str):
        """发送签到通知"""
        
        # 向所有配置的通知目标发送通知
        for target, level in config.notification_targets.items():
            # 检查是否需要发送通知
            if level == "never":
                continue
            if level == "failure_only" and result["success"]:
                continue
                
            try:
                # 获取保存的会话类型，如果没有记录则通过target判断
                session_type = config.notification_types.get(target, "")
                if not session_type:
                    # 兼容旧数据，通过target特征判断
                    session_type = "group" if "group" in target.lower() or len(target.split("_")) > 1 else "private"
                
                # 构建消息组件列表，按照AstrBot文档标准
                if session_type == "group":
                    # 群聊中@用户
                    chain = MessageChain([
                        Comp.At(qq=user_id),
                        Comp.Plain(f" 自动签到结果: {result['message']}")
                    ])
                else:
                    # 私聊直接发送
                    chain = MessageChain([
                        Comp.Plain(f"自动签到结果: {result['message']}")
                    ])
                
                await self.context.send_message(target, chain)
                logger.info(f"已发送签到通知到: {target} (级别: {level}, 类型: {session_type})")
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
            yield event.plain_result("Cookie设置成功")
            
        elif param == "lat":
            config.lat = value
            await self._save_user_configs()
            yield event.plain_result(f"纬度已设置为: {value}")
            
        elif param == "lng":
            config.lng = value
            await self._save_user_configs()
            yield event.plain_result(f"经度已设置为: {value}")
            
        elif param == "class_id":
            config.class_id = value
            await self._save_user_configs()
            yield event.plain_result(f"班级ID已设置为: {value}")
            
        elif param == "auto_time":
            if re.match(r'^\d{1,2}:\d{2}$', value):
                config.auto_signin_time = value
                await self._save_user_configs()
                
                # 重新安排定时任务
                if config.auto_signin_enabled:
                    await self._schedule_auto_signin(user_id)
                    
                yield event.plain_result(f"自动签到时间已设置为: {value}")
            else:
                yield event.plain_result("时间格式错误，请使用HH:MM格式，例如：08:30")
                
        elif param == "auto_enable":
            if value.lower() in ["true", "1", "yes", "enable"]:
                config.auto_signin_enabled = True
                await self._save_user_configs()
                await self._schedule_auto_signin(user_id)
                yield event.plain_result("自动签到已启用")
            elif value.lower() in ["false", "0", "no", "disable"]:
                config.auto_signin_enabled = False
                await self._save_user_configs()
                
                # 取消定时任务
                if user_id in self.scheduled_tasks:
                    self.scheduled_tasks[user_id].cancel()
                    del self.scheduled_tasks[user_id]
                    
                yield event.plain_result("自动签到已禁用")
            else:
                yield event.plain_result("请使用: enable/disable 或 true/false")
                
        elif param == "notification":
            if value in ["always", "never", "failure_only"]:
                # 在当前会话设置通知级别
                config.notification_targets[event.unified_msg_origin] = value
                
                # 记录会话类型
                session_type = "group" if event.get_group_id() else "private"
                config.notification_types[event.unified_msg_origin] = session_type
                
                await self._save_user_configs()
                
                yield event.plain_result(f"已为当前{session_type}聊天设置通知级别为: {value}")
            else:
                yield event.plain_result("通知级别只能是: always/never/failure_only")
                
        elif param == "offset":
            try:
                offset_value = float(value)
                if offset_value < 0:
                    yield event.plain_result("偏移值不能为负数")
                    return
                config.offset = offset_value
                await self._save_user_configs()
                yield event.plain_result(f"GPS偏移已设置为: {offset_value}")
            except ValueError:
                yield event.plain_result("无效的偏移值，请输入数字")
                
        elif param == "remove_notification":
            if event.unified_msg_origin in config.notification_targets:
                del config.notification_targets[event.unified_msg_origin]
                # 同时删除会话类型记录
                if event.unified_msg_origin in config.notification_types:
                    del config.notification_types[event.unified_msg_origin]
                await self._save_user_configs()
                session_type = "group" if event.get_group_id() else "private"
                yield event.plain_result(f"已移除当前{session_type}聊天的通知设置")
            else:
                yield event.plain_result("当前聊天没有通知设置")
        else:
            yield event.plain_result(
                "可用参数：\n"
                "cookie <值> - 设置登录Cookie\n"
                "lat <值> - 设置纬度\n"
                "lng <值> - 设置经度\n"
                "class_id <值> - 设置班级ID\n"
                "offset <值> - 设置GPS坐标偏移（默认: 0.000020）\n"
                "auto_time <HH:MM> - 设置自动签到时间\n"
                "auto_enable <enable/disable> - 启用/禁用自动签到\n"
                "notification <always/never/failure_only> - 设置当前聊天的通知级别\n"
                "remove_notification - 移除当前聊天的通知设置"
            )
            
    @signin_commands.command("now")
    async def manual_signin(self, event: AstrMessageEvent):
        """立即执行签到"""
        user_id = event.get_sender_id()
        config = self._get_user_config(user_id)
        
        # 检查配置完整性
        is_complete, error_msg = config.is_complete()
        if not is_complete:
            if error_msg == "Cookie未设置":
                yield event.plain_result("请先设置Cookie: /signin set cookie <你的Cookie>")
            elif error_msg == "纬度未设置":
                yield event.plain_result("请先设置纬度: /signin set lat <纬度值>")
            elif error_msg == "经度未设置":
                yield event.plain_result("请先设置经度: /signin set lng <经度值>")
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
                            yield event.plain_result("未找到班级")
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
                                class_name = class_name_match.group(1) if class_name_match else "未知班级"
                                class_list.append(f"{i+1}. {class_name} (ID: {class_id})")
                                
                            yield event.plain_result(
                                f"找到 {len(class_matches)} 个班级:\n" + 
                                "\n".join(class_list) + 
                                "\n\n请使用 /signin set class_id <班级ID> 来设置班级"
                            )
                            return
                    else:
                        yield event.plain_result("获取班级列表失败，请检查Cookie是否正确")
                        return
            except Exception as e:
                yield event.plain_result(f"获取班级列表错误: {str(e)}")
                return
                
        # 执行签到
        yield event.plain_result("正在执行签到...")
        result = await self._perform_signin(config)
        
        if result["success"]:
            yield event.plain_result(f"✅ {result['message']}")
        else:
            yield event.plain_result(f"❌ {result['message']}")
            
    @signin_commands.command("config")
    async def view_config(self, event: AstrMessageEvent):
        """查看当前配置"""
        user_id = event.get_sender_id()
        config = self._get_user_config(user_id)
        
        cookie_display = "已设置" if config.cookie else "未设置"
        
        # 构建通知设置显示
        if config.notification_targets:
            notification_lines = []
            for target, level in config.notification_targets.items():
                # 简化显示目标（只显示部分ID）
                target_display = target[-10:] if len(target) > 10 else target
                session_type = config.notification_types.get(target, "unknown")
                notification_lines.append(f"  {target_display}: {level} ({session_type})")
            notification_text = "\n".join(notification_lines)
        else:
            notification_text = "  未设置"
        
        config_text = f"""当前签到配置:
Cookie: {cookie_display}
纬度: {config.lat or '未设置'}
经度: {config.lng or '未设置'}
班级ID: {config.class_id or '未设置'}
GPS偏移: {config.offset}
自动签到: {'已启用' if config.auto_signin_enabled else '已禁用'}
签到时间: {config.auto_signin_time}
通知设置:
{notification_text}"""
        
        yield event.plain_result(config_text)
        
    @signin_commands.command("help")
    async def show_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """DUS 签到插件使用方法:

配置命令:
/signin set cookie <值> - 设置登录Cookie
/signin set lat <值> - 设置纬度坐标
/signin set lng <值> - 设置经度坐标
/signin set class_id <值> - 设置班级ID
/signin set offset <值> - 设置GPS坐标偏移（默认: 0.000020）
/signin set auto_time <HH:MM> - 设置自动签到时间
/signin set auto_enable <enable/disable> - 启用/禁用自动签到
/signin set notification <always/never/failure_only> - 设置当前聊天的通知级别
/signin set remove_notification - 移除当前聊天的通知设置

功能命令:
/signin now - 立即执行签到
/signin config - 查看当前配置
/signin help - 显示此帮助

通知功能:
- 不同聊天可以设置不同的通知级别
- 私聊: 建议设置为 "always", 群聊: 建议设置为 "failure_only"
- 示例: 私聊设置为 "always", 群聊设置为 "failure_only"
- 签到结果将根据每个聊天的设置进行通知
- 在群聊中，用户将在通知中被@提及
- 在私聊中，通知直接发送不含@提及

注意事项:
1. Cookie/纬度/经度是必需参数
2. 班级ID为空时将自动获取班级列表"""
        
        yield event.plain_result(help_text)
