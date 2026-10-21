"""TCL 空调显示面板 —— light 平台（对应协议 Opt_display）。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_DEVICE_ID, CONF_HOST, CONF_PORT, DEFAULT_PORT, DOMAIN, MODEL
from .protocol import TclAcError, TclAirConditioner

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """创建显示屏灯实体。"""
    ac = entry.runtime_data
    if not isinstance(ac, TclAirConditioner):
        ac = None
    if ac is None:
        try:
            port = int(entry.data.get(CONF_PORT) or DEFAULT_PORT)
        except (TypeError, ValueError):
            port = DEFAULT_PORT
        ac = TclAirConditioner.for_home_assistant(
            entry.data[CONF_HOST],
            entry.data[CONF_DEVICE_ID],
            port,
            name=entry.title,
        )
    async_add_entities([TclDisplayLight(ac)])


class TclDisplayLight(LightEntity):
    """空调显示屏（可开关）。"""

    _attr_should_poll = True
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(self, ac: TclAirConditioner) -> None:
        self.ac = ac
        self._attr_unique_id = f"{ac.device_id}_display"
        self._attr_name = f"{ac.name}显示屏"
        self._attr_is_on = False
        self._attr_available = True
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, ac.device_id)},
            name=ac.name,
            manufacturer="TCL",
            model=MODEL,
        )

    async def async_update(self) -> None:
        try:
            state = await self.hass.async_add_executor_job(self.ac.get_state)
        except (TclAcError, OSError) as err:
            _LOGGER.debug("查询空调状态失败：%s", err)
            self._attr_available = False
            return

        self._attr_available = True
        self._attr_is_on = bool(state.switches.get("Opt_display"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_apply(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_apply(False)

    async def _async_apply(self, on: bool) -> None:
        try:
            await self.hass.async_add_executor_job(
                self.ac.set_switch, "Opt_display", on
            )
        except (TclAcError, ValueError, OSError) as err:
            _LOGGER.error("控制显示屏失败：%s", err)
            return
        await asyncio.sleep(1.5)  # 实测设备约需1.5s 才反映，见 protocol.md 6.3
        await self.async_update()
        self.async_write_ha_state()
