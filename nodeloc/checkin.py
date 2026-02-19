# -*- coding: utf-8 -*-
import time
import logging
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import TimeoutException

log = logging.getLogger(__name__)

# ================== 站点配置 ==================
DOMAIN = "www.nodeloc.com"
BASE_URL = f"https://{DOMAIN}"
USER_PAGE = f"{BASE_URL}/u/"
COOKIE_DOMAIN = f".{DOMAIN}"

CHECKIN_BUTTON = "li.header-dropdown-toggle.checkin-icon button.checkin-button"
USERNAME_SELECTOR = "div.directory-table__row.me a[data-user-card]"
LOGIN_OK_SELECTOR = "div.directory-table__row.me"
# ============================================


def wait_login_success(driver, timeout=15) -> bool:
    """判断是否登录成功"""
    try:
        WebDriverWait(driver, timeout).until(
            EC.any_of(
                EC.presence_of_element_located((By.CSS_SELECTOR, LOGIN_OK_SELECTOR)),
                EC.presence_of_element_located((By.CSS_SELECTOR, CHECKIN_BUTTON)),
            )
        )
        return True
    except TimeoutException:
        return False


def get_username(driver) -> str:
    """获取当前登录用户名"""
    try:
        el = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, USERNAME_SELECTOR))
        )
        return el.get_attribute("data-user-card") or "未知用户"
    except Exception:
        return "未知用户"


def hover_checkin(driver):
    """触发签到按钮 hover"""
    try:
        btn = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, CHECKIN_BUTTON))
        )
        ActionChains(driver).move_to_element(btn).perform()
        time.sleep(1)
    except Exception as e:
        log.debug(f"hover 失败: {e}")


def already_checked_in(button) -> bool:
    """判断是否已签到"""
    cls = button.get_attribute("class") or ""
    disabled = button.get_attribute("disabled")
    return "checked-in" in cls or disabled


def do_checkin(driver, username: str) -> str:
    """执行签到流程"""
    driver.get(BASE_URL)

    hover_checkin(driver)

    try:
        button = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, CHECKIN_BUTTON))
        )
    except TimeoutException:
        return f"[❌] {username} 未找到签到按钮"

    if already_checked_in(button):
        return f"[✅] {username} 今日已签到"

    log.info(f"📌 {username} 执行签到")
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", button)
    time.sleep(1)
    driver.execute_script("arguments[0].click();", button)
    time.sleep(3)

    hover_checkin(driver)

    if already_checked_in(button):
        return f"[🎉] {username} 签到成功"
    else:
        return f"[⚠️] {username} 签到状态未确认"
