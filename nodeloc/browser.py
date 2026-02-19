# nodeloc/browser.py
# -*- coding: utf-8 -*-
"""
浏览器管理
"""

import logging
import undetected_chromedriver as uc

log = logging.getLogger(__name__)


def create_browser():
    """创建浏览器实例"""
    try:
        options = uc.ChromeOptions()
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--lang=zh-CN")

        driver = uc.Chrome(options=options, version_main=None)
        log.info("✅ 浏览器启动成功")
        return driver
    except Exception as e:
        log.error(f"❌ 浏览器启动失败: {e}")
        return None
