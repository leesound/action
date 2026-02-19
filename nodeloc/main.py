#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import logging
import sys
from config import load_accounts, BASE_URL
from browser import create_driver, inject_cookies
from checkin import do_checkin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def main():
    accounts = load_accounts()
    if not accounts:
        log.error("❌ 未配置任何账号")
        sys.exit(1)

    log.info(f"✅ 共 {len(accounts)} 个账号，开始签到")
    results = []

    for acc in accounts:
        driver = None
        try:
            driver = create_driver()
            inject_cookies(driver, acc["cookies"], BASE_URL)
            username = acc["username"]
            log.info(f"👤 当前账号: {username}")
            result = do_checkin(driver, BASE_URL, username)
            results.append(result)
            print(result)
        except Exception as e:
            msg = f"[err] {acc.get('username', '未知')} 异常: {e}"
            log.error(msg)
            results.append(msg)
        finally:
            if driver:
                driver.quit()

    log.info("✅ 全部完成")


if __name__ == "__main__":
    main()
