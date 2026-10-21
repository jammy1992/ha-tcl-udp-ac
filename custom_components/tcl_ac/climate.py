"""TCL 空调 —— climate 平台（开关机 / 温度 / 模式 / 风速 / 扫风）。

⚠️ 关键陷阱（曾导致实体注册失败、排查了十几轮）：

    ClimateEntityFeature 是 IntFlag，组合成员必须用 **|**（按位或）：

        _attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.FAN_MODE
            | ...
        )

    用 **+** 相加得到的是**普通 int**（如 425），不是 flag 对象。
    HA 的 ClimateEntity.capability_attributes 会做
    ``ClimateEntityFeature.TARGET_HUMIDITY in supported_features``，
    int 不可迭代 → TypeError → 实体被 entity_platform 的 except 吞掉，
    只留一行 "Error adding entity" ERROR 日志，前端看不到任何实体。
    light / switch 没有这类 flag 相加，所以一直表现正常。
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_DEVICE_ID, CONF_HOST, CONF_PORT, DEFAULT_PORT, DOMAIN, MODEL
from .protocol import SWITCHES, TclAcError, TclAirConditioner

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 0

# 协议模式 ↔ HA 模式（注意协议里除湿是 dehumi，不是 dry）
PROTO_TO_HA_MODE = {
    "cool": HVACMode.COOL,
    "heat": HVACMode.HEAT,
    "dehumi": HVACMode.DRY,
    "fan": HVACMode.FAN_ONLY,
    "selfFeel": HVACMode.AUTO,
}
HA_TO_PROTO_MODE = {v: k for k, v in PROTO_TO_HA_MODE.items()}

# 协议风速 ↔ HA 风速
PROTO_TO_HA_FAN = {"Auto": "auto", "Low": "low", "Middle": "medium", "High": "high"}
HA_TO_PROTO_FAN = {v: k for k, v in PROTO_TO_HA_FAN.items()}

# 扫风组合
SWING_TO_PROTO = {
    "off": (False, False),
    "vertical": (True, False),
    "horizontal": (False, True),
    "both": (True, True),
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """从配置项创建空调实体。"""
    ac = entry.runtime_data
    if not isinstance(ac, TclAirConditioner):
        ac = None
    if ac is None:
        # 兜底：极端情况下（如手工改过 .storage）自己造一个
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
    async_add_entities([TclClimateEntity(ac)])


class TclClimateEntity(ClimateEntity):
    """TCL 空调主控实体。"""

    _attr_should_poll = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 1
    _attr_min_temp = 16
    _attr_max_temp = 31
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
        HVACMode.AUTO,
    ]
    _attr_fan_modes = ["auto", "low", "medium", "high"]
    _attr_swing_modes = ["off", "vertical", "horizontal", "both"]
    # ★ 必须用 | 组合 IntFlag，用 + 会得到普通 int，详见模块 docstring
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.SWING_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, ac: TclAirConditioner) -> None:
        self.ac = ac
        self._attr_unique_id = f"{ac.device_id}_climate"
        self._attr_name = ac.name
        self._attr_available = True
        self._attr_current_temperature = None
        self._attr_target_temperature = 26
        self._attr_hvac_mode = HVACMode.OFF
        self._attr_fan_mode = "auto"
        self._attr_swing_mode = "off"
        # 最近一次"用户主动选择的模式"（不含 OFF）。
        # 关机会把 _attr_hvac_mode 覆写成 OFF，靠它才能在开机时恢复用户选的模式
        self._last_mode = HVACMode.COOL
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, ac.device_id)},
            name=ac.name,
            manufacturer="TCL",
            model=MODEL,
        )

    # ── 轮询 ─────────────────────────────────────────────────────────

    async def async_update(self) -> None:
        """拉取空调最新状态。"""
        try:
            state = await self.hass.async_add_executor_job(self.ac.get_state)
        except (TclAcError, OSError) as err:
            _LOGGER.debug("查询空调状态失败：%s", err)
            self._attr_available = False
            return

        self._attr_available = True
        self._attr_current_temperature = state.indoor_temp
        self._attr_target_temperature = state.target_temp
        if state.power:
            self._attr_hvac_mode = PROTO_TO_HA_MODE.get(state.mode, HVACMode.COOL)
            self._last_mode = self._attr_hvac_mode
        else:
            # 关机时保留 _last_mode，不覆写成 OFF
            self._attr_hvac_mode = HVACMode.OFF
        self._attr_fan_mode = PROTO_TO_HA_FAN.get(state.fan_speed, "auto")
        if state.swing_vertical and state.swing_horizontal:
            self._attr_swing_mode = "both"
        elif state.swing_vertical:
            self._attr_swing_mode = "vertical"
        elif state.swing_horizontal:
            self._attr_swing_mode = "horizontal"
        else:
            self._attr_swing_mode = "off"

        attrs: dict[str, Any] = {"室内温度": state.indoor_temp}
        if state.outdoor_temp is not None:
            attrs["室外温度"] = state.outdoor_temp
        for key, label in SWITCHES.items():
            if key in state.switches:
                attrs[label] = state.switches[key]
        self._attr_extra_state_attributes = attrs

    # ── 控制 ─────────────────────────────────────────────────────────

    async def _async_call(self, func: Any, *args: Any, **kwargs: Any) -> None:
        """执行控制命令并刷新状态。"""
        try:
            if kwargs:
                await self.hass.async_add_executor_job(partial(func, **kwargs))
            else:
                await self.hass.async_add_executor_job(func, *args)
        except (TclAcError, ValueError, OSError) as err:
            _LOGGER.error("控制空调失败：%s", err)
            return
        # 等设备真正执行完再刷新状态。
        # 实测：关机→开机需要约 1.5s 才反映出来；模式连切若间隔 <1.5s
        # 会"滞后一拍"（快速点按 HomeKit 时表现为状态乱跳）。1.0s 不够。
        await asyncio.sleep(1.5)
        await self.async_update()
        self.async_write_ha_state()

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is not None:
            await self._async_call(self.ac.set_temperature, temp)

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """切换模式。

        ⚠️ **选非 OFF 模式必须同时开机**（曾导致"关机后无法制冷"的 bug）：

            HA 的 climate.set_hvac_mode 服务只调本方法，**不会**自动补
            ``turn_on``。而设备在关机状态下收到 ``BaseMode`` 会照单全收
            （回 ACK + 响提示音）却不启动压缩机——表现为
            "提示音响了、模式也变了，就是不制冷"。

            因此这里必须带上 ``power=True``。设备支持在同一条报文里同时
            下发 TurnOn 与 BaseMode（实测可行），比分两条更快更可靠。
        """
        mode = str(hvac_mode)
        if mode == HVACMode.OFF:
            await self._async_call(self.ac.turn_off)
            return
        proto = HA_TO_PROTO_MODE.get(mode)
        if not proto:
            return
        self._last_mode = hvac_mode
        # 单条报文同时下发 TurnOn + BaseMode（实测可行）。
        # 必须带 power=True：设备在关机状态收到 BaseMode 会照单全收、
        # 回 ACK、响提示音，但不启动压缩机 —— 表现为"怎么都不制冷"。
        await self._async_call(self.ac.apply, power=True, mode=proto)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        proto = HA_TO_PROTO_FAN.get(fan_mode)
        if proto:
            await self._async_call(self.ac.set_fan_speed, proto)

    async def async_set_swing_mode(self, swing_mode: str) -> None:
        vertical, horizontal = SWING_TO_PROTO.get(swing_mode, (False, False))
        await self._async_call(self.ac.set_swing, vertical, horizontal)

    async def async_turn_on(self) -> None:
        """开机。

        HA 在实体声明了 ``TURN_ON`` 特性时，"开机"按钮会调本方法而不是
        ``async_set_hvac_mode``；HomeKit 的温控器同理（只发开机指令，
        不一定跟随设置模式）。

        设备自己会记住上次设的模式，所以这里只需发 ``TurnOn=on``；再补设
        一次用户最近选择的模式，保证 HA 卡片显示与设备实际运行一致。
        """
        proto = HA_TO_PROTO_MODE.get(str(self._last_mode))
        # 合并成一条报文下发（实测可行），比先开机再设模式更快更可靠
        if proto:
            await self._async_call(
                self.ac.apply, power=True, mode=proto
            )
        else:
            await self._async_call(self.ac.turn_on)

    async def async_turn_off(self) -> None:
        await self._async_call(self.ac.turn_off)
