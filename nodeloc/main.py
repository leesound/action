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

# ================== 日志配置 ==================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger(__name__)
# =============================================


def process_account(cookie: str) -> str:
    driver = create_browser()
    if not driver:
        return "[❌] 浏览器启动失败"

    try:
        inject_cookies(driver, BASE_URL, cookie, COOKIE_DOMAIN)
        driver.get(USER_PAGE)

        if not wait_login_success(driver):
            return "[❌] 登录失败，Cookie 可能失效"

        username = get_username(driver)
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
        if line.strip()
    ]

    log.info(f"✅ 共 {len(cookies)} 个账号，开始签到")

    results = []
    for cookie in cookies:
        result = process_account(cookie)
        log.info(result)
        results.append(result)
        time.sleep(5)

    print("\n".join(results))
    log.info("✅ 全部完成")


if __name__ == "__main__":
    main()
