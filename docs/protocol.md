# TCL 空调局域网协议（BroadLink WiFi 模块）

本文档记录 2017 年款 TCL 壁挂空调（WiFi 模块 BroadLink `3303-SBSL012001`）的**明文 XML over UDP** 控制协议，来源于对官方「TCL智能空调」App 的逆向分析。

协议全程明文、无加密、无认证，**只依赖设备 ID（tclid）匹配**。这意味着同一局域网内的任何主机都可以控制空调——请勿将此端口暴露到公网。

## 1. 端口与角色

| 用途 | 地址 | 说明 |
| --- | --- | --- |
| 设备发现 | `255.255.255.255:10075` | 广播 `SearchDevice`，设备以 `Declaration` 应答 |
| 控制 / 查询 | `<设备IP>:7777` | 单播，请求-响应模式 |

> 端口 10075 用于广播发现，**真正的控制端口由设备的 `Declaration` 报文给出**（通常是 7777），不要写死。

## 2. 设备发现

向 `255.255.255.255:10075`（以及网段广播地址，如 `192.168.0.255`）发送：

```xml
<message msgid="SearchDevice"></message>
```

设备应答（广播）：

```xml
<message msgid="Declaration">
  <DevType>aircond</DevType>
  <DevName>TAC_AABBCC</DevName>
  <DevMAC>AABBCCDDEEFF</DevMAC>
  <DevIP>192.168.1.100</DevIP>
  <DevPort>7777</DevPort>
  ...
</message>
```

`DevType` 必须是 `aircond`（模块也可能被其他 TCL 家电复用，需过滤）。

**`tclid` 的构造规则**：`DevMAC` 转小写、去掉分隔符，加 `_DEVICE` 后缀：

```text
AABBCCDDEEFF  →  aabbccddeeff_device
```

⚠️ 大小写敏感，必须与设备期望的完全一致，否则设备不应答。

## 3. 指令格式

所有控制 / 查询都是同一个信封：

```xml
<msg tclid="aabbccddeeff_device" msgid="SetMessage" type="Control" seq="1">
  <SetMessage><BaseMode>cool</BaseMode></SetMessage>
</msg>
```

| 字段 | 说明 |
| --- | --- |
| `tclid` | 设备 ID，见上 |
| `msgid` | 指令名，同时作为内层标签名 |
| `type` | 固定 `Control` |
| `seq` | **每次发送都必须变化**（设备据此把应答与请求配对）。应答里会原样回显，用它做匹配最可靠；建议用时间戳起始的递增整数，不要每次都从 1 重来 |

响应：

```xml
<msg ...><ACKSetMessage><Return>ok</Return></ACKSetMessage></msg>
```

`Return` 不为 `ok` 即被设备拒绝。查询类指令的响应体是 `<statusUpdateMsg>`。

## 4. 常用指令

| msgid | 作用 | 内层字段 |
| --- | --- | --- |
| `SyncStatusReq` | 查询状态（无需内层字段） | — |
| `SetMessage` | 下发参数 | 见下表 |
| `keepAlive` | 心跳 | — |

`SetMessage` 内层字段（布尔值用 `on` / `off` 字符串）：

| 字段 | 取值 | 说明 |
| --- | --- | --- |
| `TurnOn` | `on` / `off` | 开关机 |
| `BaseMode` | `cool` `heat` `fan` `dehumi` `selfFeel` | 模式，**除湿是 `dehumi` 不是 `dry`** |
| `SetTemp` | 华氏温度整数 | 16–31℃，换算见下 |
| `WindSpeed` | `Auto` `Low` `Middle` `High` | 风速，注意首字母大写 |
| `WindDirection_V` | `on` / `off` | 上下扫风 |
| `WindDirection_H` | `on` / `off` | 左右扫风 |
| `Opt_ECO` | `on` / `off` | 节能 |
| `Opt_super` | `on` / `off` | 强劲 |
| `Opt_healthy` | `on` / `off` | 健康 |
| `Opt_antiMildew` | `on` / `off` | 自清洁 / 防霉 |
| `Opt_StereoWind` | `on` / `off` | 立体风 |
| `Opt_heating` | `on` / `off` | 电加热 |
| `Opt_display` | `on` / `off` | 显示面板 |
| `BeepEnable` | `on` / `off` | 提示音 |
| `Degree_Half` | `on` / `off` | 0.5℃ 精度 |
| `Countdown_Timer_On/Off` | `HH:MM` | 定时开关机 |

⚠️ `TurnOn` 与 `Opt_sleepMode`（值是 `off:0:0:...` 这种冒号分隔串）等字段格式特殊，不要想当然按布尔处理。

## 5. 状态查询响应

`SyncStatusReq` 返回的 `<statusUpdateMsg>` 内是一次性全量快照。实测抓包（空调 26℃ 制冷、室内 27℃、室外 3℃）：

> 本文所有 `DevMAC` / `DevIP` / `tclid` / `DeviceName` 均为**脱敏示例值**
> （原始报文来自作者自己的设备，公开文档里换成 `AABBCCDDEEFF` 一类占位符）。
> 温度等字段值是真实的，未做改动 —— 换算关系见下。

```text
BaseMode=cool            TurnOn=on              SetTemp=79
WindSpeed=Low            WindDirection_V=on     WindDirection_H=off
IndoorTemp=81            OutdoorTemp=37         ErrorCode=0
DeviceName=TAC_AABBCC    WifiVer=1
Opt_display=off          Opt_ECO=off            Opt_super=off
Opt_healthy=off          Opt_antiMildew=off     Opt_StereoWind=off
Opt_heating=off          BeepEnable=on          Degree_Half=on
Countdown_Timer_On=00:00 Countdown_Timer_Off=00:00
Opt_sleepMode=off:0:0:0:0:0:0:0:0:0:0
HumidityEnable=off       CleannessEnable=off    Infrared_Direct=off
Infrared_TurnOn=off
```

**温度单位是华氏度**：

```text
26℃ → SetTemp 79      27.2℃ → IndoorTemp 81      2.8℃ → OutdoorTemp 37
```

换算即标准公式 `F = round(C × 9/5 + 32)`，反向 `C = round((F - 32) × 5/9)`。

## 6. 实测踩坑（这部分是本协议最容易写错的地方）

### 6.1 设备会「异步推送」，必须按报文类型等待

设备除了应答，还会**主动推送** `statusUpdateMsg`——每次操作后推一次，另外还会定时上报。

后果是：**上一条操作残留的推送，常常抢在本次应答之前到达**。天真的实现只`recvfrom()` 一次，
就会把那条推送当成应答吃掉，于是判定"指令未被确认"——**而设备的 ACK 其实稍后才到**。

实测抓到的序列（发一条 `Opt_display` 指令）：

```text
#1  statusUpdateMsg ...   ← 上一条操作残留的推送，抢先到达
#2  ACKSetMessage ...      ← 真正的应答，seq 与请求一致
```

正确做法（本集成 `_send()` 的实现）：

1. **发送前清空接收缓冲区** —— 丢掉残留推送
2. **循环接收直到期望的报文类型出现** —— `SetMessage` 等 `ACKSetMessage`，
   `SyncStatusReq` 等 `statusUpdateMsg`，中途来的推送直接丢弃

只判断"收到一条"是错的，必须判断"收到的是**哪种**报文"。

### 6.2 UDP 会丢包，需要重试

模块偶发丢包（实测 8 次连续操作里出现 1 次完全无响应，`ping` 和端口探测都正常）。
因为 UDP 无连接、且所有控制指令都是**幂等**的（设置一个值，重试无副作用），
所以超时后重试是安全且必要的。本集成默认重试 2 次（`TclAirConditioner(retries=2)`），
每次重试换新 `seq`。

### 6.3 设备会「回ACK 但不执行」

⚠️ **这一条极其反直觉，也最容易被误判成 bug。**

背靠背连发多条指令时，设备**每条都会回 `ACKSetMessage` + `Return>ok`**，
但其中一部分**根本不执行**。实测：间隔 `0.15s` 连发 12 条，全部 ACK 成功，
但查回来的状态显示大部分没生效（设了 27℃ 却读回 24℃）。

**间隔 ≥1 秒才稳定。**

关键区分——这是两回事，别混：

| 现象 | 归属 | 表现 |
| --- | --- | --- |
| 收不到 ACK，抛异常 | **网络丢包** | 需要重试兜底 |
| 收到 ACK 但状态没变 | **模块限流** | 重试无用，只能靠间隔 |

本集成在 `async_call` 里统一做了 1 秒间隔，所以 `apply()` 这类多参数批量下发
会被自动拆开、逐条间隔下发。

### 6.4 关机状态下，模式指令「照单全收但不执行」

⚠️ **这是最容易误判为"设备坏了"的行为。**

设备关机时收到 `BaseMode`，会**照单全收**——回 `ACKSetMessage` + `Return>ok`、
**响提示音**、`BaseMode` 字段也确实变了，但 `TurnOn` 仍是 `off`，
**压缩机不启动**。

实测：

```text
关机的 TurnOn=off   BaseMode=cool
  ↓ ac.set_mode("cool")         ← 回 ACK、响提示音
设制冷后 TurnOn=off   BaseMode=cool     ← 状态没变，不制冷
  ↓ ac.turn_on()               ← 只发这一条
只开机TurnOn=on    BaseMode=cool     ← 立刻开始制冷
```

**所以"设模式"必须带上 `TurnOn=on`**。两种正确写法，实测都可行：

```python
# 写法一：分两条（清晰，但多一个指令周期 + 多一次丢包机会）
ac.turn_on()
time.sleep(1.2)
ac.set_mode("cool")

# 写法二：合并成一条报文（更快更可靠，本集成采用）
ac.apply(power=True, mode="cool")
# → <SetMessage><TurnOn>on</TurnOn><BaseMode>cool</BaseMode></SetMessage>
```

配套的集成侧约定：

- HA 的 `climate.set_hvac_mode` 服务**不会**自动补 `turn_on`，集成必须自己带
- 关机时 `_attr_hvac_mode` 被覆写成 `OFF`，所以要另存"用户最近选择的模式"
  才能在开机时恢复（本集成用 `_last_mode`）

### 6.5 生效延迟实测：1.5 秒，不是 1 秒

实测收敛曲线（关机状态下发 `TurnOn=on` + `BaseMode=dehumi`）：

```text
t+0.5s  TurnOn=off  BaseMode=cool     ← 旧值
t+1.0s  TurnOn=off  BaseMode=cool     ← 旧值
t+1.5s  TurnOn=on   BaseMode=dehumi   ← 才生效
```

**间隔 1.0s 会"滞后一拍"**：快速连切模式时，每次读到的是上一次的模式：

```text
下发cool    →立即读 = dehumi
下发 fan    →立即读 = cool       ← 总是上一次的值
下发 heat   →立即读 = fan
...
```

用户侧表现为：**在 HomeKit 里快速点按模式，状态会乱跳**。
间隔 ≥1.5s 后 4/4 全部正确。

所以集成里 `climate.py` / `light.py` / `switch.py` 的控制后等待统一用 **1.5s**
（不是直觉上的 1s）。这也解释了 6.3 的"丢指令"现象——**1 秒是丢指令的下限，
1.5 秒才是安全值**。

### 6.6 HA 集成侧的阻塞调用注意

HA 2026.10 的 `block_async_io` 保护会检测执行器线程里的一批**已知阻塞调用**
（含 `time.sleep`）并记 ERROR 日志。socket 阻塞读本身是允许的，但 sleep 会误伤。

做法：把重试间隔做成可注入属性，集成侧注入 `threading.Event().wait`
（语义等价 —— 阻塞当前线程指定秒数 —— 但不在 HA 的检测名单里）：

```python
# protocol.py
self.retry_delay: Callable[[float], None] = time.sleep   # 裸 CLI 用这个

@classmethod
def for_home_assistant(cls, host, device_id, port, name=None):
    ac = cls(host, device_id, port, name=name)
    ac.retry_delay = threading.Event().wait             # HA 里用这个
    return ac
```

### 6.7 其他

- **指令生效有约 1 秒延迟**：下发后立即查询可能读到旧值，属正常。
- **关机状态下部分字段不接受**：关机时设置风速/模式可能被忽略。集成的
  `async_turn_on` 会记住上次模式并在开机后恢复。
- **UDP 无连接状态**：空调 IP 变更（DHCP 重新分配）后旧 IP 完全失效，
  建议路由器绑定静态 IP。
- **大小写敏感**：`tclid` 与 `WindSpeed` 的取值大小写都必须与上表一致。
  注意 `tclid` **发送时要大写**（`AABBCCDDEEFF_DEVICE`），文档第 2 节的
  `tclid` 构造规则给出的是小写形式——两者都要记住。

## 7. 复现 / 调试

集成内置纯标准库实现的 CLI，不需要 Home Assistant：

```bash
# 扫描局域网内的空调
python3 custom_components/tcl_ac/protocol.py discover

# 查询状态
python3 custom_components/tcl_ac/protocol.py status

# 控制
python3 custom_components/tcl_ac/protocol.py power on
python3 custom_components/tcl_ac/protocol.py temp 26
python3 custom_components/tcl_ac/protocol.py mode dehumi
python3 custom_components/tcl_ac/protocol.py fan Low
python3 custom_components/tcl_ac/protocol.py swing v on
python3 custom_components/tcl_ac/protocol.py switch Opt_ECO on
```

也可以用 `tcpdump` 抓包验证：

```bash
sudo tcpdump -i any -A -s0 'udp port 7777 or udp port 10075'
```

## 8. 逆向来源

- App 包 `TCL智能空调.apk`
- `com/broadlink/parse/tclac/`（协议解析）
- `libBLTclAcParse.so`（Native 层）
- `com/tcl/smart_home/communication_lib/impl/UdpComm.java`（UDP 传输）

**注意**：最初研究的 BroadLink AES 二进制帧是**红外发射器**路线（RM 系列），与本文的空调直控协议不是一回事。空调本体走的是明文 XML。
