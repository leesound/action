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
COOKIE_DOMAIN = ".nodeloc.com"  # ← 修复：去掉 www


def wait_login_success(driver, timeout=10) -> bool:
    """检查登录状态"""
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".current-user"))
        )
        return True
    except TimeoutException:
        log.warning("⚠️ 未检测到登录状态，Cookie 可能失效")
        return False


def get_username(driver) -> str:
    """获取当前登录用户名"""
    # 方法1: 从顶部用户链接获取
    try:
        el = driver.find_element(By.CSS_SELECTOR, ".current-user a[href*='/u/']")
        href = el.get_attribute("href")
        username = href.rstrip("/").split("/u/")[-1]
        if username:
            return username
    except Exception:
        pass

    # 方法2: 从头像链接获取
    try:
        el = driver.find_element(By.CSS_SELECTOR, "#current-user a[data-user-card]")
        username = el.get_attribute("data-user-card")
        if username:
            return username
    except Exception:
        pass

    # 方法3: 从页面 JS 变量获取
    try:
        username = driver.execute_script(
            "return Discourse.User.current() ? Discourse.User.current().username : null"
        )
        if username:
            return username
    except Exception:
        pass

    return "unknown"


def _parse_alert(driver, timeout=5) -> str | None:
    """等待签到弹窗出现，返回弹窗文本"""
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
    """根据弹窗文本和页面内容判定签到结果"""
    if alert_text:
        if "签到成功" in alert_text:
            return f"[🎉] {username} {alert_text}"
        if "已经签到" in alert_text or "已签到" in alert_text:
            return f"[⏭️] {username} 今日已签到"
        return f"[⚠️] {username} 未知响应: {alert_text}"

    if "已连续签到" in page_source:
        return f"[⏭️] {username} 今日已签到(无弹窗)"

    return f"[❌] {username} 签到状态未知"


def do_checkin(driver, username: str) -> str:
    """执行签到流程"""
    checkin_url = f"{BASE_URL}/u/{username}/summary"
    driver.get(checkin_url)
    time.sleep(3)

    log.info(f"📌 {username} 执行签到")

    # 1. 检查是否已有弹窗
    existing = driver.find_elements(By.CSS_SELECTOR, ".discourse-checkin-notification")
    if existing:
        text = existing[0].text.replace("×", "").strip()
        if "已经签到" in text or "已签到" in text:
            msg = f"[⏭️] {username} 今日已签到"
            log.info(msg)
            return msg

    # 2. 查找签到按钮
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

    # 3. 判定结果
    alert_text = _parse_alert(driver, timeout=5)
    msg = _classify_result(alert_text, driver.page_source, username)
    log.info(msg)
    return msg
