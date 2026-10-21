"""TCL 空调功能开关 —— switch 平台。

把协议里的 Opt_* / BeepEnable 等功能暴露成独立可开关的实体，
比塞在 climate 的 extra_state_attributes 里更好用（可进自动化、写卡片）。

实现刻意保持简单：每个开关自己轮询 ac.get_state()，与 climate/light 同构，
不引入 DataUpdateCoordinator，减少出错面。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, MODEL
from .protocol import SWITCHES, TclAcError, TclAirConditioner

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

ICONS = {
    "Opt_ECO": "mdi:leaf",
    "Opt_super": "mdi:flash",
    "Opt_healthy": "mdi:air-purifier",
    "Opt_antiMildew": "mdi:self-clean",
    "Opt_StereoWind": "mdi:weather-windy",
    "Opt_heating": "mdi:radiator",
    "BeepEnable": "mdi:volume-high",
    "Degree_Half": "mdi:thermometer-half",
}
# Opt_display 已由 light 平台提供，这里跳过避免重复
SKIP = {"Opt_display"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """创建功能开关实体。"""
    ac = entry.runtime_data
    if not isinstance(ac, TclAirConditioner):
        ac = None
    if ac is None:
        ac = TclAirConditioner.for_home_assistant(
            entry.data["host"], entry.data["device_id"], 7777, name=entry.title
        )
    async_add_entities(
        [TclAcSwitch(ac, key, label) for key, label in SWITCHES.items()
         if key not in SKIP]
    )


class TclAcSwitch(SwitchEntity):
    """单个功能开关。"""

    _attr_should_poll = True
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(self, ac: TclAirConditioner, key: str, label: str) -> None:
        self.ac = ac
        self._key = key
        self._attr_unique_id = f"{ac.device_id}_sw_{key}"
        self._attr_name = label
        self._attr_icon = ICONS.get(key)
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
            _LOGGER.debug("查询 %s 失败：%s", self._key, err)
            self._attr_available = False
            return
        self._attr_available = True
        self._attr_is_on = bool(state.switches.get(self._key))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_apply(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_apply(False)

    async def _async_apply(self, on: bool) -> None:
        try:
            await self.hass.async_add_executor_job(self.ac.set_switch, self._key, on)
        except (TclAcError, ValueError, OSError) as err:
            _LOGGER.error("切换 %s 失败：%s", self._key, err)
            return
        await asyncio.sleep(1.5)  # 实测设备约需1.5s 才反映，见 protocol.md 6.3
        await self.async_update()
        self.async_write_ha_state()
