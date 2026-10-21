"""TCL 空调配置流程。

严格按 HA 2026.10 API 编写：
* 用 vol.Schema + 基础类型，不用可能变化的 selector 工厂
* 返回 ConfigFlowResult（2024.5+ 存在）
* 用 domain= 关键字注册（2026.10 支持）
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries

from .const import (
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_PORT,
    DEFAULT_DEVICE_ID,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DOMAIN,
)
from .protocol import TclAcError, TclAirConditioner

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
        vol.Required(CONF_DEVICE_ID, default=DEFAULT_DEVICE_ID): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


async def _validate(hass: Any, host: str, port: int, device_id: str):
    """校验设备，返回 (信息, 错误提示)。"""
    try:
        ac = TclAirConditioner(host, device_id, port, timeout=6.0)
        state = await hass.async_add_executor_job(ac.get_state)
    except TclAcError as err:
        return None, f"连接失败：{err}"
    except OSError as err:
        return None, f"网络错误：{err}"
    except Exception as err:
        _LOGGER.exception("校验时未预期异常")
        return None, f"校验失败：{err}"

    return {
        CONF_HOST: host,
        CONF_PORT: port,
        CONF_DEVICE_ID: device_id,
        "name": ac.name,
        "state": (
            f"{'开机' if state.power else '关机'} · "
            f"{state.mode} · {state.target_temp}°C"
        ),
    }, None


class TclAcConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """处理 TCL 空调的配置流程。"""

    VERSION = 1

    def __init__(self) -> None:
        self._found: dict[str, Any] | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """手动输入 IP。"""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = str(user_input.get(CONF_HOST) or "").strip()
            device_id = str(user_input.get(CONF_DEVICE_ID) or "").strip()
            try:
                port = int(user_input.get(CONF_PORT) or DEFAULT_PORT)
            except (TypeError, ValueError):
                port = DEFAULT_PORT

            if not host or not device_id:
                errors["base"] = "IP 和设备 ID 都不能为空"
            else:
                info, err = await _validate(self.hass, host, port, device_id)
                if err:
                    errors["base"] = err
                elif info:
                    await self.async_set_unique_id(info[CONF_DEVICE_ID])
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=info["name"],
                        data={
                            CONF_HOST: info[CONF_HOST],
                            CONF_PORT: info[CONF_PORT],
                            CONF_DEVICE_ID: info[CONF_DEVICE_ID],
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )
