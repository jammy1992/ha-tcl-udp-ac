# TCL Smart AC (LAN)

通过**局域网直连**控制 TCL 壁挂空调（内置 BroadLink WiFi 模块），无需云端、无需账号、无需 Root。

- 协议：明文 XML over UDP（控制端口 7777）
- 实体：1 个 climate（开关机/温度/模式/风速/扫风）+ 1 个 light（显示屏）+ 8 个 switch（节能/强劲/健康/自清洁/立体风/电加热/提示音/0.5℃）
- 已验证：Home Assistant 2026.10 + Python 3.14

LAN-only control for TCL wall-mounted air conditioners with a BroadLink WiFi
module. No cloud, no account, no root required.
