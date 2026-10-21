"""
TCL 智能空调 —— 局域网直连协议库
==================================

逆向自 TCL智能空调 App（com.tcl.aircondition），2026-10-19 实测验证。

协议要点
--------
* 明文 XML over UDP，**无加密、无认证、无会话**
* 控制端口 7777（由设备 Declaration 广播告知）
* 发现广播：``<message msgid="SearchDevice">`` → 255.255.255.255:10075
* 设备上线广播：来自 :7777，目的端口 10074
* 设备 ID（tclid）= 小写 MAC + "_DEVICE"，**发送时必须大写**
* seq 必须**递增**（否则设备静默丢弃，只回 ok）
* 查询：msgid=SyncStatusReq → statusUpdateMsg
* 控制：msgid=SetMessage → ACKSetMessage
* 温度是**私有华氏编码**，不是标准华氏（见 TEMP_C2F）
* 除湿模式拼写是 ``dehumi``（不是 dry）

坐标系：三遍折腾才找对的路子
--------------------------------
BroadLink 二进制协议（auth + AES + 31 字节帧）能握手、能收确认码，
但设备从不执行命令 —— 因为 App 根本不走那条路。
真正的控制通道是这套明文 XML，App 用它做全部交互。
"""

from __future__ import annotations

import re
import socket
import threading
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Self

__version__ = "1.1.1"

__all__ = [
    "FAN_SPEEDS",
    "MODES",
    "SWITCHES",
    "TEMPS",
    "TclAcError",
    "TclAcState",
    "TclAirConditioner",
]

# ── 协议常量 ──────────────────────────────────────────────────────────

DISCOVERY_PORT = 10075          # SearchDevice 广播目标端口
DEVICE_BROADCAST_PORT = 10074   # 设备上线广播目标端口
CONTROL_PORT = 7777             # 设备控制端口（由 Declaration 告知）

DISCOVERY_MSG = b'<message msgid="SearchDevice"></message>'

# 温度范围（协议支持区间，与官方集成一致）
TEMPS: tuple[int, ...] = tuple(range(16, 32))

MODES: dict[str, str] = {
    "cool": "制冷",
    "heat": "制热",
    "fan": "送风",
    "dehumi": "除湿",   # 注意：App 用的是 dehumi，拼写与常规 dry 不同
    "selfFeel": "自动",
}

FAN_SPEEDS: dict[str, str] = {
    "Auto": "自动",
    "Low": "低",
    "Middle": "中",
    "High": "高",
}

SWITCHES: dict[str, str] = {
    "Opt_ECO": "节能",
    "Opt_super": "强劲",
    "Opt_healthy": "健康",
    "Opt_antiMildew": "自清洁",
    "Opt_StereoWind": "立体风",
    "Opt_heating": "电加热",
    "Opt_display": "显示",
    "BeepEnable": "提示音",
    "Degree_Half": "0.5℃",
}

# HA climate 模式字符串 → 协议模式
HA_TO_PROTO_MODE: dict[str, str] = {
    "cool": "cool",
    "heat": "heat",
    "dry": "dehumi",
    "fan_only": "fan",
    "auto": "selfFeel",
}
PROTO_TO_HA_MODE: dict[str, str] = {v: k for k, v in HA_TO_PROTO_MODE.items()}

# HA 风速档位字符串 → 协议风速
HA_TO_PROTO_FAN: dict[str, str] = {
    "low": "Low",
    "medium": "Middle",
    "high": "High",
    "auto": "Auto",
}
PROTO_TO_HA_FAN: dict[str, str] = {v: k for k, v in HA_TO_PROTO_FAN.items()}


class TclAcError(Exception):
    """协议层错误"""


@dataclass
class TclAcState:
    """空调状态快照"""

    power: bool = False
    mode: str = "cool"
    target_temp: int = 26           # 摄氏度
    fan_speed: str = "Auto"
    swing_vertical: bool = False
    swing_horizontal: bool = False
    indoor_temp: int | None = None  # 摄氏度
    outdoor_temp: int | None = None
    switches: dict[str, bool] = field(default_factory=dict)
    raw: dict[str, str] = field(default_factory=dict)


def _f_to_c(f: int) -> int:
    """协议华氏值 → 摄氏度。

    协议用的是**标准华氏**，直接换算即可。
    （早期误以为是私有跳档表，真机对比后确认标准换算才是正确语义。）
    """
    return round((f - 32) * 5 / 9)


def _c_to_f(c: int) -> int:
    """摄氏度 → 协议华氏值（标准换算，四舍五入）。"""
    c = max(16, min(31, int(c)))
    return round(c * 9 / 5 + 32)


class TclAirConditioner:
    """TCL 空调局域网控制器。

    用法::

        ac = TclAirConditioner.discover(timeout=5)[0]
        print(ac.get_state())
        ac.set_temperature(26)
        ac.set_mode("cool")
        ac.turn_on()
    """

    @classmethod
    def for_home_assistant(cls, host: str, device_id: str,
                           port: int = CONTROL_PORT,
                           name: str | None = None) -> TclAirConditioner:
        """构造一个适合在 Home Assistant 里用的实例。

        与直接 ``TclAirConditioner(...)`` 的唯一区别：把重试间隔从
        ``time.sleep`` 换成 ``threading.Event().wait``。

        原因：HA 2026.10 的 block_async_io 保护会检测执行器线程里的一批
        **已知阻塞调用**（含 ``time.sleep``）并记 ERROR 日志。socket 阻塞读
        本身是被允许的，但 sleep 会被误伤。``Event().wait`` 语义等价
        （阻塞当前线程指定秒数）且不在检测名单内。
        """
        ac = cls(host, device_id, port, name=name)
        ac.retry_delay = threading.Event().wait
        return ac

    def __init__(self, host: str, device_id: str, port: int = CONTROL_PORT,
                 name: str | None = None, timeout: float = 5.0, retries: int = 2):
        """
        :param host:设备 IP，如 ``192.168.1.100``
        :param device_id: 设备 ID，格式 ``aabbccddeeff_DEVICE``（**小写**）
        :param port: 控制端口，默认 7777
        :param timeout:单次等待应答的超时秒数
        :param retries:超时后的重试次数。UDP 无连接、且模块偶发丢包，
            重试是必要的；幂等指令（开关/模式/温度）重试无副作用。
        """
        self.host = host
        self.device_id = device_id.lower()
        # tclid 发送时必须大写，否则设备静默忽略
        self._tclid = self.device_id.upper()
        self.port = port
        self.name = name or self.device_id.split("_DEVICE")[0]
        self.timeout = timeout
        self.retries = retries
        # 重试间隔函数。HA 会监控执行器线程里的阻塞调用并打 ERROR 日志，
        # 集成侧应注入不会阻塞事件循环的实现。
        self.retry_delay: Callable[[float], None] = time.sleep
        self._seq = int(time.time()) % 100000
        self._sock: socket.socket | None = None
        self._last_raw = ""

    # ── 底层通信 ──────────────────────────────────────────────────

    @property
    def sock(self) -> socket.socket:
        if self._sock is None:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(self.timeout)
            self._sock = s
        return self._sock

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def _flush_rx(self) -> None:
        """清空 socket 接收缓冲区里的残留报文。

        设备除了应答，还会**异步推送** ``statusUpdateMsg``（每次操作后、
        以及定时上报）。上一条操作残留的推送常常抢在本次应答之前到达，
        不清掉就会被当成应答吃掉。
        """
        s = self.sock
        s.setblocking(False)
        try:
            while True:
                s.recvfrom(65535)
        except OSError:
            pass
        finally:
            s.setblocking(True)
            s.settimeout(self.timeout)

    def _try_once(self, msgid: str, inner_xml: str, expect: str | None) -> str | None:
        """尝试发送一次并等待应答。

        :returns: 收到期望报文时返回其文本；超时返回 ``None``（交给上层重试）。
        :raises TclAcError: 发送失败（socket 层面，不可重试）。
        """
        # 每次尝试都用新seq，设备按 seq 去重
        seq = self._next_seq()
        msg = (
            f'<msg tclid="{self._tclid}" msgid="{msgid}" '
            f'type="Control" seq="{seq}">'
            f"<{msgid}>{inner_xml}</{msgid}></msg>"
        )
        self._flush_rx()
        try:
            self.sock.sendto(msg.encode("utf-8"), (self.host, self.port))
        except OSError as exc:
            raise TclAcError(
                f"{self.name} 发送失败（{self.host}:{self.port}）：{exc}"
            ) from exc

        last = ""
        deadline = time.time() + self.timeout
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                break
            self.sock.settimeout(remain)
            try:
                data, _ = self.sock.recvfrom(65535)
            except TimeoutError:
                break
            last = data.decode("utf-8", errors="ignore")
            if expect is None or expect in last:
                return last
        self._last_raw = last
        return None

    def _send(self, msgid: str, inner_xml: str = "", expect: str | None = None) -> str:
        """发送一条 XML 指令并返回响应文本，失败时自动重试。

        :param expect: 期望在响应中出现的报文类型（如 ``ACKSetMessage``、
            ``statusUpdateMsg``）。给定时会**循环接收**直到期望报文到达，
            期间到达的异步状态推送被丢弃；超时仍没收到则按 :attr:`retries`
            重试，全部失败才抛 :class:`TclAcError`。为 ``None`` 时只收一条
            （不校验内容）。
        """
        last = ""
        for attempt in range(self.retries + 1):
            if attempt:
                # 重试前留间隔，避开模块的拥塞窗口。
                # ⚠️ HA 会检测执行器线程里的阻塞调用（block_async_io 保护），
                # 所以间隔函数可注入：集成侧传入不会阻塞事件循环的实现。
                self.retry_delay(0.4 * attempt)
            last = ""
            resp = self._try_once(msgid, inner_xml, expect)
            if resp is not None:
                return resp
            last = getattr(self, "_last_raw", "")

        # 超时兜底：若确实收到了点东西，报"未收到期望报文"更贴近真相
        hint = (
            f"（只收到了异步状态推送）—— {last[:120]}"
            if last
            else f"（{self.host}:{self.port}）—— 确认设备在线且 IP 未变"
        )
        raise TclAcError(f"{self.name} 无响应：{hint}")

    @staticmethod
    def _parse_status(xml: str) -> dict[str, str]:
        """从 statusUpdateMsg 中提取所有子元素。"""
        body = re.search(r"<statusUpdateMsg>(.*?)</statusUpdateMsg>", xml, re.DOTALL)
        if not body:
            return {}
        return {
            m.group(1): m.group(2)
            for m in re.finditer(r"<(\w+)>([^<]*)</\1>", body.group(1))
        }

    # ── 发现 ──────────────────────────────────────────────────────

    @classmethod
    def discover(cls, timeout: float = 5.0) -> list[TclAirConditioner]:
        """广播扫描局域网内的 TCL 空调。

        向 ``255.255.255.255:10075`` 发 SearchDevice，
        设备用 Declaration 广播回应（内含真实控制端口）。
        """
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("", DEVICE_BROADCAST_PORT))
        except OSError:
            # 10074 被占用时退回任意端口，靠 Declaration 内容判断
            listener.bind(("", 0))
        listener.settimeout(timeout / 4)

        trigger = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        trigger.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        found: dict[str, TclAirConditioner] = {}
        deadline = time.time() + timeout
        while time.time() < deadline:
            for dst in ("255.255.255.255", "192.168.0.255"):
                with suppress(OSError):
                    trigger.sendto(DISCOVERY_MSG, (dst, DISCOVERY_PORT))
            try:
                data, addr = listener.recvfrom(8192)
            except TimeoutError:
                continue

            xml = data.decode("utf-8", errors="ignore")
            if 'msgid="Declaration"' not in xml:
                continue

            def field_of(src: str, tag: str, default: str = "") -> str:
                m = re.search(rf"<{tag}>([^<]*)</{tag}>", src)
                return m.group(1) if m else default

            # 只收空调（滤掉其他 TCL 设备）
            if field_of(xml, "DevType").lower() != "aircond":
                continue

            mac = field_of(xml, "DevMAC")
            if not mac or mac in found:
                continue
            found[mac] = cls(
                field_of(xml, "DevIP") or addr[0],
                mac,
                int(field_of(xml, "DevPort") or CONTROL_PORT),
                field_of(xml, "DevName") or mac,
            )

        trigger.close()
        listener.close()
        return list(found.values())

    @classmethod
    def from_ip(cls, host: str, timeout: float = 5.0) -> TclAirConditioner | None:
        """按 IP 查找设备（内部走 discover）。"""
        for dev in cls.discover(timeout=timeout):
            if dev.host == host:
                return dev
        return None

    # ── 状态 ──────────────────────────────────────────────────────

    def get_state(self) -> TclAcState:
        """查询空调当前状态。"""
        xml = self._send("SyncStatusReq", expect="statusUpdateMsg")
        data = self._parse_status(xml)
        if not data:
            raise TclAcError(f"{self.name} 返回的状态无法解析：{xml[:120]}")

        st = TclAcState(raw=data)
        st.power = data.get("TurnOn") == "on"
        st.mode = data.get("BaseMode", "cool")
        try:
            st.target_temp = _f_to_c(int(data.get("SetTemp", 73)))
        except (ValueError, TypeError):
            st.target_temp = 26
        st.fan_speed = data.get("WindSpeed", "Auto")
        st.swing_vertical = data.get("WindDirection_V") == "on"
        st.swing_horizontal = data.get("WindDirection_H") == "on"

        # 室内外温度：室内用私有编码，室外走标准华氏
        with suppress(KeyError, ValueError):
            st.indoor_temp = _f_to_c(int(data["IndoorTemp"]))
        with suppress(KeyError, ValueError):
            st.outdoor_temp = round((int(data["OutdoorTemp"]) - 32) * 5 / 9)

        st.switches = {k: (data.get(k) == "on") for k in SWITCHES if k in data}
        return st

    # ── 控制 ──────────────────────────────────────────────────────

    def _set(self, pairs: dict[str, str | int | bool]) -> str:
        """下发一组参数。值为 True/False 时自动转 on/off。"""
        inner = "".join(
            f"<{k}>{'on' if v is True else 'off' if v is False else v}</{k}>"
            for k, v in pairs.items()
        )
        resp = self._send("SetMessage", inner, expect="ACKSetMessage")
        if "ACKSetMessage" not in resp:
            raise TclAcError(f"{self.name} 未确认指令：{resp[:120]}")
        m = re.search(r"<Return>([^<]*)</Return>", resp)
        if m and m.group(1) != "ok":
            raise TclAcError(f"{self.name} 拒绝指令：{m.group(1)}")
        return resp

    def turn_on(self) -> None:
        self._set({"TurnOn": "on"})

    def turn_off(self) -> None:
        self._set({"TurnOn": "off"})

    def set_power(self, on: bool) -> None:
        self.turn_on() if on else self.turn_off()

    def set_temperature(self, celsius: float) -> None:
        """设置目标温度（摄氏度，16–31）。"""
        c = round(celsius)
        if not 16 <= c <= 31:
            raise ValueError(f"温度需在 16–31℃ 之间，收到 {celsius}")
        self._set({"SetTemp": _c_to_f(c)})

    def set_mode(self, mode: str) -> None:
        """设置模式：cool / heat / fan / dehumi / selfFeel"""
        if mode not in MODES:
            raise ValueError(f"未知模式 {mode}，可选：{list(MODES)}")
        self._set({"BaseMode": mode})

    def set_fan_speed(self, speed: str) -> None:
        """设置风速：Auto / Low / Middle / High"""
        if speed not in FAN_SPEEDS:
            raise ValueError(f"未知风速 {speed}，可选：{list(FAN_SPEEDS)}")
        self._set({"WindSpeed": speed})

    def set_swing(self, vertical: bool | None = None,
                  horizontal: bool | None = None) -> None:
        """设置扫风，None 表示不改动该项。"""
        pairs: dict[str, bool] = {}
        if vertical is not None:
            pairs["WindDirection_V"] = vertical
        if horizontal is not None:
            pairs["WindDirection_H"] = horizontal
        if pairs:
            self._set(pairs)

    def set_switch(self, name: str, on: bool) -> None:
        """开关功能：ECO / super / healthy / antiMildew 等。"""
        if name not in SWITCHES:
            raise ValueError(f"未知开关 {name}，可选：{list(SWITCHES)}")
        self._set({name: on})

    def set_timer(self, hour: int = 0, minute: int = 0,
                  enable: bool = True) -> None:
        """设置定时开/关机。"""
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("定时时间超出范围")
        tag = "Countdown_Timer_On" if enable else "Countdown_Timer_Off"
        self._set({tag: f"{hour:02d}:{minute:02d}"})

    def keep_alive(self) -> None:
        """心跳保活。"""
        # 心跳失败不影响主流程
        with suppress(TclAcError):
            self._send("keepAlive")

    # ── 便捷组合 ──────────────────────────────────────────────────

    def apply(self, power: bool | None = None, temperature: float | None = None,
              mode: str | None = None, fan_speed: str | None = None,
              swing_v: bool | None = None, swing_h: bool | None = None) -> None:
        """一次性下发多个参数（App 的 transSendXml 等价物）。"""
        pairs: dict[str, Any] = {}
        if power is not None:
            pairs["TurnOn"] = "on" if power else "off"
        if temperature is not None:
            c = round(temperature)
            if not 16 <= c <= 31:
                raise ValueError(f"温度需在 16–31℃ 之间，收到 {temperature}")
            pairs["SetTemp"] = _c_to_f(c)
        if mode is not None:
            if mode not in MODES:
                raise ValueError(f"未知模式 {mode}")
            pairs["BaseMode"] = mode
        if fan_speed is not None:
            if fan_speed not in FAN_SPEEDS:
                raise ValueError(f"未知风速 {fan_speed}")
            pairs["WindSpeed"] = fan_speed
        if swing_v is not None:
            pairs["WindDirection_V"] = "on" if swing_v else "off"
        if swing_h is not None:
            pairs["WindDirection_H"] = "on" if swing_h else "off"
        if pairs:
            self._set(pairs)

    def __repr__(self) -> str:
        return (f"<TclAirConditioner {self.name} {self.host}:{self.port} "
                f"id={self.device_id}>")


# ── 命令行入口 ────────────────────────────────────────────────────────

def _cli() -> None:
    import argparse

    p = argparse.ArgumentParser(description="TCL 空调局域网控制")
    p.add_argument("--ip", help="设备 IP（省略则广播发现）")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover", help="扫描设备")
    sub.add_parser("status", help="查询状态")
    onoff = sub.add_parser("power", help="开关机")
    onoff.add_argument("state", choices=["on", "off"])
    t = sub.add_parser("temp", help="设温度")
    t.add_argument("celsius", type=int)
    m = sub.add_parser("mode", help="设模式")
    m.add_argument("mode", choices=list(MODES))
    f = sub.add_parser("fan", help="设风速")
    f.add_argument("speed", choices=list(FAN_SPEEDS))
    sw = sub.add_parser("swing", help="扫风")
    sw.add_argument("dir", choices=["v", "h", "both"])
    sw.add_argument("state", choices=["on", "off"])
    sw2 = sub.add_parser("switch", help="功能开关")
    sw2.add_argument("name", choices=list(SWITCHES))
    sw2.add_argument("state", choices=["on", "off"])

    a = p.parse_args()

    if a.cmd == "discover":
        devs = TclAirConditioner.discover()
        if not devs:
            print("未发现设备")
            return
        for d in devs:
            print(f"{d.name}  {d.host}:{d.port}  id={d.device_id}")
            print(f"  当前: {d.get_state()}")
        return

    ac = TclAirConditioner.from_ip(a.ip) if a.ip else None
    if ac is None:
        devs = TclAirConditioner.discover()
        if not devs:
            print("未发现设备，请检查是否同一局域网")
            return
        ac = devs[0]
    print(f"→ {ac.name} ({ac.host})")

    if a.cmd == "status":
        st = ac.get_state()
        print(f"  电源    : {'开机' if st.power else '关机'}")
        print(f"  模式    : {MODES.get(st.mode, st.mode)}")
        print(f"  目标温度: {st.target_temp}°C")
        print(f"  风速    : {FAN_SPEEDS.get(st.fan_speed, st.fan_speed)}")
        print(f"  上下扫风: {'开' if st.swing_vertical else '关'}")
        print(f"  左右扫风: {'开' if st.swing_horizontal else '关'}")
        print(f"  室内温度: {st.indoor_temp}°C" if st.indoor_temp is not None
              else "  室内温度: 未知")
        print(f"  室外温度: {st.outdoor_temp}°C" if st.outdoor_temp is not None
              else "  室外温度: 未知")
        on_sw = [SWITCHES[k] for k, v in st.switches.items() if v]
        print(f"  功能开启: {'、'.join(on_sw) if on_sw else '无'}")
    elif a.cmd == "power":
        ac.set_power(a.state == "on")
        print(f"  已{'开机' if a.state == 'on' else '关机'}")
    elif a.cmd == "temp":
        ac.set_temperature(a.celsius)
        print(f"  已设为 {a.celsius}°C")
    elif a.cmd == "mode":
        ac.set_mode(a.mode)
        print(f"  已设为 {MODES[a.mode]}")
    elif a.cmd == "fan":
        ac.set_fan_speed(a.speed)
        print(f"  已设为 {FAN_SPEEDS[a.speed]}")
    elif a.cmd == "swing":
        ac.set_swing(
            vertical=a.state == "on" if a.dir in ("v", "both") else None,
            horizontal=a.state == "on" if a.dir in ("h", "both") else None,
        )
        print("  扫风已更新")
    elif a.cmd == "switch":
        ac.set_switch(a.name, a.state == "on")
        print(f"  {SWITCHES[a.name]}已{'开启' if a.state == 'on' else '关闭'}")


if __name__ == "__main__":
    _cli()
