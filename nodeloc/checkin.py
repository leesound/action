# nodeloc/checkin.py
# -*- coding: utf-8 -*-
"""
NodeLoc 签到核心逻辑
使用账号密码登录，点击签到图标
"""

import logging
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

log = logging.getLogger(__name__)

BASE_URL = "https://www.nodeloc.com"
LOGIN_URL = f"{BASE_URL}/login"


def do_login(driver, username: str, password: str) -> bool:
    """使用账号密码登录"""
    log.info(f"🔐 开始登录: {username}")
    driver.get(LOGIN_URL)
    time.sleep(3)

    try:
        # 等待登录表单加载
        username_input = WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#login-account-name, input[name='login'], input#signin_username"))
        )
        
        # 尝试多种密码输入框选择器
        password_input = None
        password_selectors = [
            "#login-account-password",
            "input[name='password']",
            "input#signin_password",
            "input[type='password']"
        ]
        for sel in password_selectors:
            try:
                password_input = driver.find_element(By.CSS_SELECTOR, sel)
                if password_input:
                    break
            except Exception:
                continue

        if not password_input:
            log.error("❌ 找不到密码输入框")
            return False

        # 清空并输入账号密码
        username_input.clear()
        username_input.send_keys(username)
        time.sleep(0.5)
        
        password_input.clear()
        password_input.send_keys(password)
        time.sleep(0.5)

        # 点击登录按钮
        login_btn_selectors = [
            "#login-button",
            "button.btn-primary.login-button",
            "button[type='submit']",
            "#signin-button"
        ]
        
        login_btn = None
        for sel in login_btn_selectors:
            try:
                login_btn = driver.find_element(By.CSS_SELECTOR, sel)
                if login_btn and login_btn.is_displayed():
                    break
            except Exception:
                continue

        if login_btn:
            login_btn.click()
        else:
            # 尝试提交表单
            password_input.submit()

        time.sleep(5)

        # 验证登录成功
        if wait_login_success(driver):
            log.info("✅ 登录成功")
            return True
        else:
            log.error("❌ 登录失败")
            return False

    except Exception as e:
        log.error(f"❌ 登录异常: {e}")
        return False


def wait_login_success(driver, timeout=15) -> bool:
    """检查登录状态"""
    try:
        WebDriverWait(driver, timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "#current-user, .current-user"))
        )
        return True
    except TimeoutException:
        return False


def get_username_from_page(driver) -> str:
    """从页面获取当前登录用户名"""
    try:
        el = driver.find_element(By.CSS_SELECTOR, "#current-user img.avatar")
        src = el.get_attribute("src")
        # src 格式: /user_avatar/www.nodeloc.com/oyz/96/12871_2.png
        if "/user_avatar/" in src:
            parts = src.split("/")
            for i, p in enumerate(parts):
                if p == "user_avatar" and i + 2 < len(parts):
                    return parts[i + 2]
    except Exception:
        pass
    return "unknown"


def do_checkin(driver, username: str) -> str:
    """执行签到：点击签到图标"""
    log.info(f"📌 {username} 执行签到")

    try:
        # 查找签到按钮（日历图标）
        checkin_selectors = [
            "button.checkin-button",
            ".checkin-icon button",
            "button[title='每日签到']",
            "button[aria-label='每日签到']",
            ".header-dropdown-toggle.checkin-icon button",
        ]

        checkin_btn = None
        for sel in checkin_selectors:
            try:
                checkin_btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.CSS_SELECTOR, sel))
                )
                if checkin_btn:
                    log.info(f"✅ 找到签到按钮: {sel}")
                    break
            except Exception:
                continue

        if not checkin_btn:
            # 尝试用 XPath 查找包含日历图标的按钮
            try:
                checkin_btn = driver.find_element(
                    By.XPATH, 
                    "//button[contains(@class, 'checkin') or contains(@title, '签到')]"
                )
            except Exception:
                pass

        if not checkin_btn:
            log.warning("⚠️ 未找到签到按钮")
            return f"[❌] {username} 未找到签到按钮"

        # 点击签到
        checkin_btn.click()
        log.info("🖱️ 已点击签到按钮")
        time.sleep(3)

        # 等待并获取签到结果
        result = _get_checkin_result(driver, username)
        return result

    except Exception as e:
        log.error(f"❌ 签到异常: {e}")
        return f"[❌] {username} 签到异常: {e}"


def _get_checkin_result(driver, username: str) -> str:
    """获取签到结果"""
    # 尝试多种弹窗选择器
    alert_selectors = [
        ".discourse-checkin-notification",
        ".alert-success",
        ".bootbox-body",
        ".fancybox-content",
        ".dialog-body",
        ".popup-tip",
        ".notification",
    ]

    for sel in alert_selectors:
        try:
            alert = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, sel))
            )
            text = alert.text.replace("×", "").strip()
            if text:
                log.info(f"🔔 弹窗内容: {text}")
                
                if "签到成功" in text:
                    return f"[🎉] {username} {text}"
                elif "已经签到" in text or "已签到" in text:
                    return f"[⏭️] {username} 今日已签到"
                else:
                    return f"[⚠️] {username} {text}"
        except TimeoutException:
            continue

    # 检查页面源码
    page = driver.page_source
    if "签到成功" in page:
        return f"[🎉] {username} 签到成功"
    elif "已连续签到" in page or "已签到" in page:
        return f"[⏭️] {username} 今日已签到"

    return f"[❓] {username} 签到状态未知"
