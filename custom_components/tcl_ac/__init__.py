"""TCL 智能空调 —— 局域网直连集成。

架构：config_flow 建配置项 → async_setup_entry 转发 climate / light / switch 平台。
（HA 2026.10 已从 climate / light / switch 移除 async_setup_platform，
 自定义集成必须走 config_flow + ConfigEntry + async_setup_entry。）

实体（共 10 个）：
  · climate   空调主控：开关机 / 温度 / 模式 / 风速 / 上下左右扫风
  · light     空调显示屏开关（协议 Opt_display）
  · switch×8  节能 / 强劲 / 健康 / 自清洁 / 立体风 / 电加热 / 提示音 / 0.5℃

协议：明文 XML over UDP，控制端口 7777，无需账号、无需云端。
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .const import CONF_DEVICE_ID, CONF_HOST, CONF_PORT, DEFAULT_PORT
from .protocol import TclAirConditioner

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.CLIMATE, Platform.LIGHT, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """创建空调对象并转发各平台。"""
    host = str(entry.data.get(CONF_HOST) or "").strip()
    device_id = str(entry.data.get(CONF_DEVICE_ID) or "").strip()
    try:
        port = int(entry.data.get(CONF_PORT) or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT

    if not host or not device_id:
        # 数据不完整属于致命错误，抛 ConfigEntryError 会让 HA 记日志并提示修复
        raise ConfigEntryError(
            f"配置项数据不完整（host={host!r}, device_id={device_id!r}），"
            "请在集成页面删除后重新添加"
        )

    # 三个平台共用同一个空调对象（同一个 UDP socket）
    # 用 for_home_assistant 而非直接构造：它会把重试间隔换成
    # threading.Event().wait，避开 HA 的 block_async_io 误报（time.sleep）
    entry.runtime_data = TclAirConditioner.for_home_assistant(
        host, device_id, port, name=entry.title
    )

    _LOGGER.info("正在加载 TCL 空调 %s（%s:%s）", entry.title, host, port)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载配置项。"""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
