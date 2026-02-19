# -*- coding: utf-8 -*-
import os
import time
import logging
import requests
from browser import create_browser
from checkin import do_login, do_checkin, get_username_from_page, BASE_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)

# 截图保存目录
SCREENSHOT_DIR = "/tmp/screenshots"


def mask_username(username: str) -> str:
    """隐藏用户名中间部分"""
    if not username or len(username) <= 2:
        return "***"
    if len(username) <= 4:
        return username[0] + "*" * (len(username) - 1)
    # 保留首尾各1-2个字符
    show_len = min(2, len(username) // 3)
    return username[:show_len] + "*" * (len(username) - show_len * 2) + username[-show_len:]


def send_telegram_notification(message: str) -> bool:
    """发送Telegram通知"""
    bot_token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    
    if not bot_token or not chat_id:
        log.info("ℹ️ 未配置TG通知，跳过")
        return False
    
    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        data = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        resp = requests.post(url, data=data, timeout=30)
        if resp.status_code == 200:
            log.info("✅ TG通知发送成功")
            return True
        else:
            log.error(f"❌ TG通知失败: {resp.text}")
            return False
    except Exception as e:
        log.error(f"❌ TG通知异常: {e}")
        return False


def parse_accounts(env_value: str) -> list:
    """
    解析账号配置，支持格式：
    username----password
    多账号换行
    """
    accounts = []
    for line in env_value.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "----" in line:
            parts = line.split("----", 1)
            if len(parts) == 2:
                accounts.append({
                    "username": parts[0].strip(),
                    "password": parts[1].strip()
                })
    return accounts


def ensure_screenshot_dir():
    """确保截图目录存在"""
    if not os.path.exists(SCREENSHOT_DIR):
        os.makedirs(SCREENSHOT_DIR)


def process_account(username: str, password: str, index: int) -> str:
    """处理单个账号"""
    masked_name = mask_username(username)
    driver = create_browser()
    
    if not driver:
        return f"[❌] 账号{index} ({masked_name}) 浏览器启动失败"

    try:
        # 登录
        if not do_login(driver, username, password):
            try:
                ensure_screenshot_dir()
                driver.save_screenshot(f"{SCREENSHOT_DIR}/login_failed_{index}.png")
            except Exception:
                pass
            return f"[❌] 账号{index} ({masked_name}) 登录失败"

        # 获取实际用户名（确认登录成功）- 仅用于日志确认，不显示
        actual_user = get_username_from_page(driver)
        if actual_user != "unknown":
            log.info(f"👤 账号{index} 登录确认成功")

        # 等待页面完全加载
        time.sleep(2)
        
        # 访问首页确保签到按钮可见
        driver.get(BASE_URL)
        time.sleep(3)

        # 执行签到
        result = do_checkin(driver, masked_name)
        
        # 保存签到后截图
        try:
            ensure_screenshot_dir()
            driver.save_screenshot(f"{SCREENSHOT_DIR}/checkin_{index}.png")
        except Exception:
            pass
            
        return result

    except Exception as e:
        log.error(f"❌ 处理账号异常: {e}")
        try:
            ensure_screenshot_dir()
            driver.save_screenshot(f"{SCREENSHOT_DIR}/error_{index}.png")
        except Exception:
            pass
        return f"[❌] 账号{index} ({masked_name}) 异常: {e}"
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def main():
    env_key = "NL_ACCOUNT"
    if env_key not in os.environ:
        print(f"❌ 未设置 {env_key} 环境变量")
        print("格式: username----password")
        return

    accounts = parse_accounts(os.environ[env_key])
    if not accounts:
        print("❌ 未找到有效账号")
        return

    log.info(f"✅ 共 {len(accounts)} 个账号，开始签到")
    
    # 确保截图目录存在
    ensure_screenshot_dir()

    results = []
    success_count = 0
    fail_count = 0
    
    for idx, acc in enumerate(accounts, 1):
        masked_name = mask_username(acc['username'])
        log.info(f"--- 账号 {idx}/{len(accounts)}: {masked_name} ---")
        result = process_account(acc["username"], acc["password"], idx)
        results.append(result)
        log.info(result)
        
        # 统计结果
        if "[🎉]" in result or "[⏭️]" in result:
            success_count += 1
        else:
            fail_count += 1
        
        if idx < len(accounts):
            time.sleep(5)

    log.info("✅ 全部完成")
    
    # 构建通知消息
    summary = f"📊 签到统计: 成功 {success_count} / 失败 {fail_count} / 共 {len(accounts)}"
    result_text = "\n".join(results)
    
    # 控制台输出
    print(f"\n{summary}")
    print(result_text)
    
    # 发送TG通知
    tg_message = f"<b>🔔 NodeLoc 签到报告</b>\n\n{summary}\n\n<pre>{result_text}</pre>"
    send_telegram_notification(tg_message)


if __name__ == "__main__":
    main()
