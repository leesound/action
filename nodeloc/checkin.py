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
COOKIE_DOMAIN = ".nodeloc.com"


def wait_login_success(driver, timeout=10) -> bool:
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".current-user"))
        )
        return True
    except TimeoutException:
        log.warning("⚠️ 未检测到登录状态")
        return False


def get_username(driver) -> str:
    try:
        el = driver.find_element(By.CSS_SELECTOR, ".current-user a[href*='/u/']")
        href = el.get_attribute("href")
        return href.rstrip("/").split("/u/")[-1]
    except Exception:
        pass
    
    try:
        el = driver.find_element(By.CSS_SELECTOR, "#current-user a[data-user-card]")
        return el.get_attribute("data-user-card")
    except Exception:
        pass
    
    return "unknown"


def _find_checkin_button(driver):
    """尝试多种选择器查找签到按钮"""
    selectors = [
        "button.btn-primary.checkin",
        "button.checkin",
        ".checkin-button",
        "button[class*='checkin']",
        ".user-main .btn-primary",
        "button.btn-primary",
    ]
    
    for sel in selectors:
        try:
            btns = driver.find_elements(By.CSS_SELECTOR, sel)
            for btn in btns:
                text = btn.text.strip()
                log.info(f"🔍 找到按钮 [{sel}]: '{text}'")
                if "签到" in text or "checkin" in text.lower():
                    return btn
        except Exception:
            pass
    
    return None


def _debug_page(driver, username: str):
    """调试：输出页面按钮信息"""
    try:
        driver.save_screenshot("/tmp/checkin_debug.png")
        log.info("📸 已保存截图: /tmp/checkin_debug.png")
    except Exception:
        pass
    
    # 查找所有按钮
    try:
        buttons = driver.find_elements(By.TAG_NAME, "button")
        log.info(f"🔍 页面共有 {len(buttons)} 个按钮:")
        for i, btn in enumerate(buttons[:10]):  # 只显示前10个
            text = btn.text.strip().replace("\n", " ")[:30]
            cls = btn.get_attribute("class") or ""
            log.info(f"   [{i}] class='{cls}' text='{text}'")
    except Exception as e:
        log.warning(f"调试失败: {e}")


def _parse_alert(driver, timeout=5) -> str | None:
    """等待签到弹窗"""
    selectors = [
        ".discourse-checkin-notification",
        ".alert-success",
        ".fancybox-content",
        ".bootbox-body",
        ".notification",
    ]
    
    for sel in selectors:
        try:
            alert = WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, sel))
            )
            text = alert.text.replace("×", "").strip()
            if text:
                log.info(f"🔔 弹窗 [{sel}]: {text}")
                return text
        except TimeoutException:
            continue
    
    return None


def _classify_result(alert_text: str | None, page_source: str, username: str) -> str:
    if alert_text:
        if "签到成功" in alert_text:
            return f"[🎉] {username} {alert_text}"
        if "已经签到" in alert_text or "已签到" in alert_text:
            return f"[⏭️] {username} 今日已签到"
        return f"[⚠️] {username} 响应: {alert_text}"

    if "已连续签到" in page_source:
        return f"[⏭️] {username} 今日已签到"

    return f"[❌] {username} 签到状态未知"


def do_checkin(driver, username: str) -> str:
    checkin_url = f"{BASE_URL}/u/{username}/summary"
    driver.get(checkin_url)
    time.sleep(3)

    log.info(f"📌 {username} 执行签到")

    # 调试输出
    _debug_page(driver, username)

    # 1. 检查已有弹窗
    existing = driver.find_elements(By.CSS_SELECTOR, ".discourse-checkin-notification")
    if existing:
        text = existing[0].text.replace("×", "").strip()
        if "已经签到" in text or "已签到" in text:
            msg = f"[⏭️] {username} 今日已签到"
            log.info(msg)
            return msg

    # 2. 查找签到按钮（多种方式）
    btn = _find_checkin_button(driver)
    
    if not btn:
        log.warning("⚠️ 未找到签到按钮")
        msg = _classify_result(None, driver.page_source, username)
        log.info(msg)
        return msg

    log.info(f"✅ 找到签到按钮: {btn.text}")
    btn.click()
    time.sleep(2)

    # 3. 判定结果
    alert_text = _parse_alert(driver, timeout=5)
    msg = _classify_result(alert_text, driver.page_source, username)
    log.info(msg)
    return msg
