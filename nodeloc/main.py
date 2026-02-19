# nodeloc/main.py
# -*- coding: utf-8 -*-
import os
import time
import logging
from browser import create_browser
from checkin import do_login, do_checkin, get_username_from_page, BASE_URL

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)


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


def process_account(username: str, password: str) -> str:
    """处理单个账号"""
    driver = create_browser()
    if not driver:
        return f"[❌] {username} 浏览器启动失败"

    try:
        # 登录
        if not do_login(driver, username, password):
            try:
                driver.save_screenshot("/tmp/login_failed.png")
            except Exception:
                pass
            return f"[❌] {username} 登录失败"

        # 获取实际用户名（确认登录成功）
        actual_user = get_username_from_page(driver)
        if actual_user != "unknown":
            log.info(f"👤 当前账号: {actual_user}")

        # 等待页面完全加载
        time.sleep(2)
        
        # 访问首页确保签到按钮可见
        driver.get(BASE_URL)
        time.sleep(3)

        # 执行签到
        return do_checkin(driver, username)

    except Exception as e:
        log.error(f"❌ 处理账号异常: {e}")
        return f"[❌] {username} 异常: {e}"
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

    results = []
    for idx, acc in enumerate(accounts, 1):
        log.info(f"--- 账号 {idx}/{len(accounts)}: {acc['username']} ---")
        result = process_account(acc["username"], acc["password"])
        results.append(result)
        log.info(result)
        
        if idx < len(accounts):
            time.sleep(5)

    log.info("✅ 全部完成")
    print("\n" + "\n".join(results))


if __name__ == "__main__":
    main()
