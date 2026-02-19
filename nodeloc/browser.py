# nodeloc/browser.py
# -*- coding: utf-8 -*-
"""
浏览器管理
"""

import logging
import undetected_chromedriver as uc

log = logging.getLogger(__name__)


def create_browser():
    options = uc.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')
    options.add_argument('--disable-gpu')
    options.add_argument('--window-size=1920,1080')
    
    driver = uc.Chrome(
        options=options,
        version_main=131
    )
    return driver
    except Exception as e:
        log.error(f"❌ 浏览器启动失败: {e}")
        return None
