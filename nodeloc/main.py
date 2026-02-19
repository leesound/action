# nodeloc/main.py
# -*- coding: utf-8 -*-
import os
import time
import logging
from browser import create_browser, inject_cookies
from checkin import (
    BASE_URL,
    USER_PAGE,
    COOKIE_DOMAIN,
    wait_login_success,
    get_username,
    do_checkin,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)


def process_account(cookie: str) -> str:
    driver = create_browser()
    if not driver:
        return "[❌] 浏览器启动失败"

    try:
        inject_cookies(driver, BASE_URL, cookie, COOKIE_DOMAIN)
        driver.get(USER_PAGE)
        time.sleep(3)  # 等待页面加载

        if not wait_login_success(driver):
            # 保存截图用于调试
            try:
                driver.save_screenshot("/tmp/login_failed.png")
                log.info("📸 已保存截图: /tmp/login_failed.png")
            except Exception:
                pass
            return "[❌] 登录失败，Cookie 可能失效"

        username = get_username(driver)
        if username == "unknown":
            log.warning("⚠️ 无法获取用户名，尝试从 Cookie 解析")
            # 可选：从 Cookie 中解析用户名
            
        log.info(f"👤 当前账号: {username}")
        return do_checkin(driver, username)

    finally:
        try:
            driver.quit()
        except Exception:
            pass


def main():
    if "NL_COOKIE" not in os.environ:
        print("❌ 未设置 NL_COOKIE 环境变量")
        return

    cookies = [
        line.strip().split("#", 1)[0]
        for line in os.environ["NL_COOKIE"].splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]

    if not cookies:
        print("❌ NL_COOKIE 为空")
        return

    log.info(f"✅ 共 {len(cookies)} 个账号，开始签到")

    results = []
    for idx, cookie in enumerate(cookies, 1):
        log.info(f"--- 账号 {idx}/{len(cookies)} ---")
        result = process_account(cookie)
        results.append(result)
        if idx < len(cookies):
            time.sleep(5)

    log.info("✅ 全部完成")
    print("\n".join(results))


if __name__ == "__main__":
    main()
