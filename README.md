# TCL Smart AC (LAN)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Default-orange.svg)](https://github.com/jammy1992/ha-tcl-udp-ac)
[![HA Version](https://img.shields.io/badge/Home%20Assistant-2026.10%2B-blue.svg)](https://www.home-assistant.io/)

通过**局域网直连**控制 TCL 壁挂空调（内置 BroadLink WiFi 模块）。无需云端、无需账号、无需 Root、无需拆机刷固件。

> 协议为明文 XML over UDP，控制端口 `7777`。整个交互不出局域网。

English version: [README_EN.md](README_EN.md)

---

## 功能

| 实体 | 类型 | 能力 |
| --- | --- | --- |
| `<设备名>` | `climate` | 开关机 · 温度 16–31℃ · 制冷/制热/除湿/送风/自动 · 风速 · 上下/左右扫风 |
| `<设备名>显示屏` | `light` | 空调显示面板开关 |
| `节能` | `switch` | ECO 模式 |
| `强劲` | `switch` | Super / 强劲模式 |
| `健康` | `switch` | 健康（负离子等） |
| `自清洁` | `switch` | 防霉/自清洁 |
| `立体风` | `switch` | 立体风 |
| `电加热` | `switch` | 电辅热 |
| `提示音` | `switch` | 蜂鸣提示音 |
| `0.5℃` | `switch` | 0.5℃ 精度调温 |

状态本地轮询，设备离线时实体显示 `unavailable`，恢复后自动上线。

## 适用设备

- 使用 **BroadLink WiFi 模块**的 TCL 壁挂空调（拆开室内机外壳，模块丝印可见 `3303-SBSL012001` 一类编号）
- 能用官方「TCL智能空调」App 通过**局域网**绑定控制的机型
- 实测机型：2017 年购入的 TCL KFR 系列，MAC `aa:bb:cc:dd:ee:ff`

> 如果你的空调用的是其他厂商的 WiFi 模块（如庆科、汉枫），协议不同，本集成不适用。

## 安装

### 方式一：HACS（推荐）

1. HACS → 集成 → 右上角 `⋮` → **自定义仓库**
2. 类别选 `Integration`，仓库填 `https://github.com/jammy1992/ha-tcl-udp-ac`，点添加
3. 在集成页找到 **TCL Smart AC (LAN)**，下载
4. **重启 Home Assistant**

### 方式二：手动

1. 下载本仓库 ZIP 并解压
2. 把 `custom_components/tcl_ac` 整个目录复制到 HA 配置目录：

   ```text
   <配置目录>/custom_components/tcl_ac/
   ```

3. **重启 Home Assistant**

## 配置

### 第一步：找到设备 ID

设备 ID = **空调 MAC 地址（小写、去掉冒号）+ `_device` 后缀**。

最容易的办法是用集成自带的发现工具（纯标准库，不需要 HA）：

```bash
python3 custom_components/tcl_ac/protocol.py discover
```

输出类似：

```text
TAC_AABBCC  192.168.1.100:7777  id=aabbccddeeff_device
  当前: TclAcState(power=True, mode='cool', target_temp=26, ...)
```

记下 `id=` 后面的字符串。也可以直接去路由器客户端列表里找空调的 MAC 地址自己拼。

### 第二步：添加集成

设置 → 设备与服务 → **添加集成** → 搜索 `TCL` → 选择 **TCL Smart AC (LAN)**，填写：

| 字段 | 值 | 说明 |
| --- | --- | --- |
| IP 地址 | `192.168.1.100` | 空调的局域网 IP（建议在路由器里绑定静态 IP） |
| 设备 ID | `aabbccddeeff_device` | 上一步拿到的 ID，**大小写必须一致** |
| 控制端口 | `7777` | 默认即可 |

添加时会实际连接一次空调做校验，失败会提示具体原因。

## 使用

- **温控卡片**：把 `climate.<设备名>` 加到卡片上，模式里的「除湿」「送风」在 HA 里正常可选
- **HomeKit / 语音**： climate 在 HomeKit 里映射为温控器（HomeKit 协议不支持除湿/送风，HA 会自动降级）
- **自动化**：8 个 switch 可直接用于自动化，例如「室温高于 28℃ 且 节能关闭时打开强劲」

## 协议说明

本集成的协议来自对官方「TCL智能空调」App 的逆向，完整文档（报文格式、字段表、踩坑记录）见：

👉 [docs/protocol.md](docs/protocol.md)

## 常见问题

**Q：添加时提示"连接失败"？**
确认电脑/HA 与空调在同一网段；用上面的 `discover` 命令验证设备是否可达；检查路由器是否开启了 AP 隔离。

**Q：实体显示 `unavailable`？**
空调断电、WiFi 断连或 IP 变更。UDP 无连接状态，IP 变了必须改集成配置。

**Q：关机状态下选"制冷"，空调响了一声但不制冷？**

这是本协议最容易踩的坑，已在集成里修掉。原因是设备关机时收到 `BaseMode` 会
**照单全收**（回 ACK、响提示音、模式字段也变了）却不启动压缩机。
集成在下发模式时会自动带上开机指令，所以直接选"制冷"即可，无需先点开机。

**Q：在 HomeKit 里快速连点模式，状态会乱跳？**

设备的生效延迟约 1.5 秒。集成已把所有控制后的等待设为 1.5s，
若你手动改过这个值请不要低于 1.5s，否则会读到上一次的模式。

**Q：控制指令偶尔不生效？**

有两种不同情况，别混：

- **日志报"无响应"** → UDP 丢包。集成已内置重试（默认重试 2 次），一般用户感知不到。
- **日志一切正常，但状态没变** → 模块**限流**。该模块会丢弃间隔过短的连续指令，而且**丢弃时照样回 `ACK ok`**。集成内部已对所有控制调用统一做 1.5 秒间隔，正常操作不会遇到。

详见 [docs/protocol.md](docs/protocol.md) 第 6 节。

**Q：支持 2024/2025 的老版本 HA 吗？**
本集成基于 2026.10 的 `config_flow + ConfigEntry + async_setup_entry` 架构编写（2026.10 已从 climate/light/switch 移除 `async_setup_platform`）。更低版本未经测试。

## 免责声明

- 本项目为个人学习研究的开源作品，与 TCL 公司、BroadLink 公司无任何关联
- 逆向与联调基于作者自己拥有的设备，不保证适用于所有机型
- 使用本项目视为你已了解并自行承担可能的风险

## 致谢

- 协议分析参考了 `TCL智能空调` App 中 `com/broadlink/parse/tclac/` 与 `libBLTclAcParse.so` 的实现
- 感谢 Home Assistant 社区的基础设施

## License

[MIT](LICENSE)
