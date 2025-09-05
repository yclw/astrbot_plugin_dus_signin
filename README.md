# DUS 签到插件

这是一个为 AstrBot 开发的 DUS 签到插件，支持手动签到、定时签到和签到结果通知功能。

## 功能特性

1. **签到配置** - 支持设置 Cookie、经纬度坐标、班级ID等签到参数
2. **立即签到** - 根据配置立即执行签到
3. **定时签到** - 支持每日定时自动签到
4. **签到通知** - 可配置的签到结果通知级别（总是/从不/仅失败时）

## 使用方法

### 配置命令

```
/signin set cookie <值>             # 设置登录Cookie（必需）
/signin set lat <值>                # 设置纬度坐标（必需）
/signin set lng <值>                # 设置经度坐标（必需）
/signin set class_id <值>           # 设置班级ID（可选，自动获取）
/signin set offset <值>             # 设置GPS坐标偏移（默认：0.000020）
/signin set auto_time <HH:MM>       # 设置自动签到时间
/signin set auto_enable <启用/禁用>   # 启用/禁用自动签到
/signin set notification <级别>     # 设置当前聊天的通知级别
/signin set remove_notification    # 移除当前聊天的通知设置
```

### 功能命令

```
/signin now                         # 立即执行签到
/signin config                      # 查看当前配置
/signin help                        # 显示帮助信息
```

## 通知功能

### 多聊天通知支持
- **私聊通知**：建议设置为"always"，实时获取签到状态更新
- **群聊通知**：建议设置为"failure_only"，提醒群成员签到失败
- **灵活配置**：不同聊天可设置不同通知级别，互不干扰

### 使用示例
```
# 在私聊中设置总是通知
/signin set notification always

# 在群聊中设置仅失败时通知
/signin set notification failure_only

# 移除当前聊天的通知设置
/signin set remove_notification
```

## GPS偏移功能

插件支持GPS坐标偏移，为您的位置坐标添加随机变化：

- **默认偏移**：0.000020（大约2米）
- **范围**：在-偏移到+偏移之间对经纬度都应用随机偏移
- **目的**：通过添加轻微的随机性来帮助避免检测
- **配置**：使用 `/signin set offset <值>` 设置自定义偏移值

### 示例：
```
/signin set offset 0.000030    # 设置偏移为大约±30米
/signin set offset 0.000010    # 设置偏移为大约±10米
/signin set offset 0          # 禁用偏移（使用精确坐标）
```

## 注意事项

1. Cookie、纬度、经度是必需参数，可通过浏览器开发者工具获取
2. 班级ID为空时会自动获取班级列表
3. 支持多聊天通知，每个聊天可设置不同的通知级别
4. 通知级别：always/never/failure_only
5. GPS偏移为坐标添加随机变化以提高隐私性

## 依赖

```bash
pip install aiohttp>=3.8.0
```

## 核心特性

- **严格HTTP请求规范** - 基于 `signin.sh` 脚本的严格实现
- **智能班级识别** - 自动获取和选择班级
- **灵活通知系统** - 支持多种通知级别
- **持久化配置** - 用户配置自动保存和恢复
- **定时任务管理** - 可靠的定时签到机制

## 技术实现

该插件基于 AstrBot 插件开发框架，使用 aiohttp 进行 HTTP 请求，支持异步操作和任务调度。严格按照原始签到脚本的请求头和参数要求实现，确保签到成功率。