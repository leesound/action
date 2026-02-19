# -*- coding: utf-8 -*-
"""
浏览器创建 & Cookie 注入模块
- 自动检测系统 Chrome 主版本号，解决 ChromeDriver 版本不匹配问题
- 使用 undetected_chromedriver 绕过基础反爬
"""

import logging
import re
import subprocess
import undetected_chromedriver as uc

log = logging.getLogger(__name__)


def _detect_chrome_major_version() -> int | None:
    """
    自动检测系统安装的 Chrome / Chromium 主版本号。
    依次尝试多个可能的命令，返回主版本号整数（如 144）。
    检测失败时返回 None，交由 undetected_chromedriver 自行处理。
    """
    commands = [
        "google-chrome --version",
        "google-chrome-stable --version",
        "chromium --version",
        "chromium-browser --version",
    ]
    for cmd in commands:
        try:
            out = subprocess.check_output(
                cmd, shell=True, stderr=subprocess.DEVNULL
            ).decode()
            match = re.search(r"(\d+)\.", out)
            if match:
                version = int(match.group(1))
                log.info(f"🔍 检测到 Chrome 主版本: {version}")
                return version
        except Exception:
            continue

    log.warning("⚠️ 无法检测 Chrome 版本，将由 undetected_chromedriver 自动匹配")
    return None


def create_browser(headless: bool = True):
    """
    创建并返回 Chrome WebDriver 实例。

    Parameters
    ----------
    headless : bool
        是否以无头模式启动，默认 True。

    Returns
    -------
    uc.Chrome | None
        成功返回 driver 实例，失败返回 None。
    """
    options = uc.ChromeOptions()

    # 基础启动参数
    base_args = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--window-size=1920,1080",
        "--disable-blink-features=AutomationControlled",
        "--disable-extensions",
    ]
    for arg in base_args:
        options.add_argument(arg)

    # 无头模式
    if headless:
        options.add_argument("--headless=new")

    # 自定义 User-Agent
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0 Safari/537.36"
    )

    try:
        # 关键：指定 version_main 确保 ChromeDriver 与 Chrome 版本匹配
        version = _detect_chrome_major_version()
        driver = uc.Chrome(options=options, version_main=version)
        driver.set_window_size(1920, 1080)

        # 反自动化基础伪装
        driver.execute_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>false})"
        )
        driver.execute_script("window.chrome={runtime:{}}")
        driver.execute_script(
            "Object.defineProperty(navigator,'languages',{get:()=>['zh-CN','zh']})"
        )
        driver.execute_script(
            "Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3]})"
        )

        log.info("✅ 浏览器启动成功")
        return driver

    except Exception as e:
        log.error(f"❌ 浏览器启动失败: {e}")
        return None


def inject_cookies(driver, base_url: str, cookie_str: str, domain: str):
    """
    向浏览器注入 Cookie。

    Parameters
    ----------
    driver : uc.Chrome
        WebDriver 实例。
    base_url : str
        先访问的基础 URL（注入 Cookie 前需要先打开同域页面）。
    cookie_str : str
        原始 Cookie 字符串，格式如 "key1=val1; key2=val2"。
    domain : str
        Cookie 所属域名，如 ".nodeloc.com"。
    """
    driver.get(base_url)

    for item in cookie_str.split(";"):
        item = item.strip()
        if not item or "=" not in item:
            continue

        name, value = item.split("=", 1)
        try:
            driver.add_cookie({
                "name": name.strip(),
                "value": value.strip(),
                "domain": domain,
                "path": "/",
                "secure": True,
                "httpOnly": False,
            })
        except Exception as e:
            log.warning(f"⚠️ Cookie 注入失败: {name.strip()} -> {e}")

    log.info(f"🍪 Cookie 注入完成 (域: {domain})")
