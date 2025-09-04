#!/bin/bash

# 配置参数 (留空则会提示用户输入)
COOKIE=""
LAT=""
LNG=""
CLASS_ID=""

# 通用请求头
USER_AGENT="Mozilla/5.0 (Linux; Android 9; AKT-AK47 Build/USER-AK47; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/116.0.0.0 Mobile Safari/537.36 XWEB/1160065 MMWEBSDK/20231202 MMWEBID/1136 MicroMessenger/8.0.47.2560(0x28002F35) WeChat/arm64 Weixin NetType/4G Language/zh_CN ABI/arm64"
ACCEPT="text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/wxpic,image/tpg,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
ACCEPT_LANG="zh-CN,zh-SG;q=0.9,zh;q=0.8,en-SG;q=0.7,en-US;q=0.6,en;q=0.5"

# 检查并获取必需的参数
if [ -z "$COOKIE" ]; then
    echo "请输入 Cookie (从浏览器开发者工具中获取):"
    read -p "Cookie: " COOKIE
    if [ -z "$COOKIE" ]; then
        echo "错误: Cookie 不能为空"
        exit 1
    fi
fi

if [ -z "$LAT" ]; then
    echo "请输入纬度 (例如: 20):"
    read -p "纬度: " LAT
    if [ -z "$LAT" ]; then
        echo "错误: 纬度不能为空"
        exit 1
    fi
fi

if [ -z "$LNG" ]; then
    echo "请输入经度 (例如: 100):"
    read -p "经度: " LNG
    if [ -z "$LNG" ]; then
        echo "错误: 经度不能为空"
        exit 1
    fi
fi

# 获取班级列表并选择
if [ -z "$CLASS_ID" ]; then
    echo "正在获取班级列表..."
    COURSE_DATA=$(curl -s "http://k8n.cn/student" \
      -H "Cookie: ${COOKIE}" \
      -H "User-Agent: ${USER_AGENT}" \
      -H "Accept: ${ACCEPT}" \
      -H "X-Requested-With: com.tencent.mm" \
      -H "Accept-Language: ${ACCEPT_LANG}" \
      --compressed)

    # 一次性提取所有班级ID和名称
    declare -A COURSE_NAMES
    COURSE_IDS=($(echo "$COURSE_DATA" | grep -oE 'course_id="[0-9]+"' | grep -oE '[0-9]+'))

    # 为每个班级ID提取对应的名称
    for COURSE_ID in "${COURSE_IDS[@]}"; do
        COURSE_NAME=$(echo "$COURSE_DATA" | grep -A 5 "course_id=\"$COURSE_ID\"" | grep -oE 'class="course_name"[^>]*>[^<]*' | sed 's/.*>//')
        COURSE_NAMES["$COURSE_ID"]="$COURSE_NAME"
    done

    if [ ${#COURSE_IDS[@]} -eq 0 ]; then
        echo "未找到班级"
        exit 1
    else
        CLASS_COUNT=${#COURSE_IDS[@]}
        if [ "$CLASS_COUNT" -eq 1 ]; then
            CLASS_ID="${COURSE_IDS[0]}"
            echo "自动选择班级: ${COURSE_NAMES[$CLASS_ID]}"
        else
            echo "找到 $CLASS_COUNT 个班级："
            for i in "${!COURSE_IDS[@]}"; do
                COURSE_ID="${COURSE_IDS[$i]}"
                echo "$((i+1)). ${COURSE_NAMES[$COURSE_ID]}"
            done
            read -p "请选择班级序号 (1-$CLASS_COUNT): " CHOICE
            if [[ "$CHOICE" =~ ^[0-9]+$ ]] && [ "$CHOICE" -ge 1 ] && [ "$CHOICE" -le "$CLASS_COUNT" ]; then
                CLASS_ID="${COURSE_IDS[$((CHOICE-1))]}"
            else
                echo "无效的选择"
                exit 1
            fi
        fi
    fi
    echo "使用班级: ${COURSE_NAMES[$CLASS_ID]} (ID: $CLASS_ID)"
else
    echo "使用预设班级 ID: $CLASS_ID"
fi

# 获取签到任务ID
TASK_ID=$(curl -s -X GET "http://k8n.cn/student/course/${CLASS_ID}/punchs" \
  -H "User-Agent: ${USER_AGENT}" \
  -H "Accept: ${ACCEPT}" \
  -H "X-Requested-With: com.tencent.mm" \
  -H "Referer: http://k8n.cn/student/course/${CLASS_ID}" \
  -H "Accept-Language: ${ACCEPT_LANG}" \
  -H "Cookie: ${COOKIE}" \
  --compressed | grep -oE 'onclick="punch_gps\([0-9]+\)"' | sed 's/onclick="punch_gps(\([0-9]*\)).*/\1/' | head -1)

# 检查是否获取到任务ID
if [ -z "$TASK_ID" ]; then
    echo "未找到签到任务"
    exit 1
fi

# 执行签到
RESULT=$(curl -s -X POST "http://k8n.cn/student/punchs/course/${CLASS_ID}/${TASK_ID}" \
  -H "User-Agent: ${USER_AGENT}" \
  -H "Accept: ${ACCEPT}" \
  -H "X-Requested-With: com.tencent.mm" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -H "Origin: http://k8n.cn" \
  -H "Referer: http://k8n.cn/student/course/${CLASS_ID}/punchs" \
  -H "Accept-Language: ${ACCEPT_LANG}" \
  -H "Cookie: ${COOKIE}" \
  -d "id=${TASK_ID}&lat=${LAT}&lng=${LNG}&acc=10&res=&gps_addr=" \
  --compressed)

# 检查签到结果
if echo "$RESULT" | grep -q "签到成功"; then
    echo "签到成功"
else
    echo "签到失败"
    exit 1
fi
