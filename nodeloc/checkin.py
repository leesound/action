# nodeloc/checkin.py
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

BASE_URL = "https://www.nodeloc.com"
USER_PAGE = f"{BASE_URL}/"
COOKIE_DOMAIN = ".www.nodeloc.com"


def wait_login_success(driver, timeout=10) -> bool:
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".current-user"))
        )
        return True
    except TimeoutException:
        return False


def get_username(driver) -> str:
    try:
        el = driver.find_element(By.CSS_SELECTOR, ".current-user a[href*='/u/']")
        href = el.get_attribute("href")
        return href.rstrip("/").split("/u/")[-1]
    except Exception:
        return "unknown"


def _parse_alert(driver, timeout=5) -> str | None:
    """等待签到弹窗出现，返回弹窗文本；超时返回 None"""
    try:
        alert = WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, ".discourse-checkin-notification")
            )
        )
        return alert.text.replace("×", "").strip()
    except TimeoutException:
        return None


def _classify_result(alert_text: str | None, page_source: str, username: str) -> str:
    """
    根据弹窗文本和页面内容，严格判定签到结果。
    只有明确包含"签到成功"才算成功，其余一律如实报告。
    """
    if alert_text:
        # "签到成功！您获得了 1 能量。" → 真正成功
        if "签到成功" in alert_text:
            return f"[🎉] {username} {alert_text}"

        # "您今天已经签到过了" → 重复签到
        if "已经签到" in alert_text or "已签到" in alert_text:
            return f"[⏭️] {username} 今日已签到"

        # 其他未知弹窗，原样返回，绝不误判
        return f"[⚠️] {username} 未知响应: {alert_text}"

    # 无弹窗，用页面内容兜底
    if "已连续签到" in page_source:
        return f"[⏭️] {username} 今日已签到(无弹窗)"

    return f"[❌] {username} 签到状态未知"


def do_checkin(driver, username: str) -> str:
    """执行签到流程"""
    checkin_url = f"{BASE_URL}/u/{username}/summary"
    driver.get(checkin_url)
    time.sleep(3)

    log.info(f"📌 {username} 执行签到")

    # ---- 1. 页面加载后先检查是否已有弹窗（某些情况页面直接提示） ----
    existing = driver.find_elements(By.CSS_SELECTOR, ".discourse-checkin-notification")
    if existing:
        text = existing[0].text.replace("×", "").strip()
        if "已经签到" in text or "已签到" in text:
            msg = f"[⏭️] {username} 今日已签到"
            log.info(msg)
            return msg

    # ---- 2. 查找并点击签到按钮 ----
    try:
        btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btn-primary.checkin"))
        )
    except TimeoutException:
        msg = _classify_result(None, driver.page_source, username)
        log.info(msg)
        return msg

    btn.click()
    time.sleep(2)

    # ---- 3. 读取弹窗并判定结果 ----
    alert_text = _parse_alert(driver, timeout=5)
    msg = _classify_result(alert_text, driver.page_source, username)
    log.info(msg)
    return msg
