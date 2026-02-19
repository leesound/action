# -*- coding: utf-8 -*-
"""
签到核心逻辑
"""

import logging
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

log = logging.getLogger(__name__)


def do_checkin(driver, base_url: str, username: str) -> str:
    """
    执行签到流程。

    Returns
    -------
    str
        签到结果消息，用于最终汇总输出。
    """
    checkin_url = f"{base_url}/u/{username}/summary"
    driver.get(checkin_url)
    time.sleep(3)

    log.info(f"{username} 执行签到")

    try:
        # 等待签到按钮出现并可点击
        btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btn-primary.checkin"))
        )
        btn.click()
        time.sleep(2)

        # 等待右上角 alert 提示框出现
        try:
            alert = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".discourse-checkin-notification"))
            )
            alert_text = alert.text
            log.debug(f"Alert 内容: {alert_text}")

            # 根据提示内容判断结果
            if "已经签到" in alert_text or "已签到" in alert_text:
                msg = f"[ok] {username} 今日已签到"
            elif "签到成功" in alert_text or "恭喜" in alert_text:
                msg = f"[ok] {username} 签到成功"
            else:
                msg = f"[ok] {username} {alert_text.replace('×', '').strip()}"
            
            log.info(msg)
            return msg

        except TimeoutException:
            # 没有 alert，检查页面状态
            if "已连续签到" in driver.page_source:
                msg = f"[ok] {username} 签到成功"
                log.info(msg)
                return msg
            else:
                msg = f"[err] {username} 签到状态未知"
                log.warning(msg)
                return msg

    except TimeoutException:
        # 按钮未出现
        if "已连续签到" in driver.page_source:
            msg = f"[ok] {username} 今日已签到(无按钮)"
            log.info(msg)
            return msg
        else:
            msg = f"[err] {username} 签到失败(未找到按钮)"
            log.warning(msg)
            return msg

    except Exception as e:
        msg = f"[err] {username} 签到异常: {e}"
        log.warning(msg)
        return msg
