# OpenBot

[English](README.md) · [中文](README.zh-CN.md)

在自己的 Grok Bot Computer 上使用服务商账户，保留每个 Bot 的模型与推理设置、压缩和缓存控制、本地任务、MCP/ACP 与恢复工具。

## 安装

在 Computer 上以 sand-data 的所有者运行；不要修改桌面或手机 App。公开源码不需要 GitHub 登录：

```bash
openbot_source="$(mktemp -d /tmp/openbot-source.XXXXXX)"
git clone https://github.com/nickkostik/openbot-community.git "$openbot_source"
bash "$openbot_source/quickstart.sh" --check
bash "$openbot_source/quickstart.sh" --install
```

随后用 Computer 的浏览器打开 `http://127.0.0.1:9280`。需要 Bash、Python 3、flock 和 Node 22.13+（22.x）或 24+；安装脚本可以缓存 Node，不替换系统 Node。Tailscale 是可选项。

`--check` 只读取状态。若已配置公网 Tunnel，`--install` 会在安装前停止，保留原设置；确认需要公网访问时再显式选择 `--public-tunnel`。Quickstart 会把必要的宿主重启延后到空闲时，不能把待重启当成已完成。

[可直接复制的 Agent 指令](docs/agent-install.md) 说明安装、可选 Tunnel/Tailscale 和更新。Bot 模式会创建临时公网控制地址，即使设置了 OPENBOT_TUNNEL=off；不需要公网地址时使用其中的无公网 Tunnel 流程。密钥和 OAuth 文件只在自己的面板输入，不能贴到聊天或仓库。

## 同时更新官方与本地引擎

遵循 [协调更新指南](docs/updating.md)。OpenBot 源码、原生 Computer 镜像与兼容的本地引擎需要分别验证，并保持同一目标上游版本。保留模式、模型、推理、历史和任务设置。不要把暂存、待重启或 HTTP 200 当成实际完成。

Official 使用原厂宿主和平台投递，消耗 Grok 配额。Custom 需要经过审核且实际可运行的 BOX 引擎，并使用所选服务商。Temporal 云服务由厂商维护。新版上游移除 BOX 时，需要经过验证的兼容移植；版本号相同不代表所有功能已移植。

如果新版原厂宿主移除了可选的日志接口，已有 Official 安装仍可更新控制面板。安装器会明确提示请求日志不可用，并保留原厂投递、已选模式及已有设置。这不代表聊天或日志采集已验证；Custom 仍须通过本地引擎兼容性检查。参阅[源码更新及验证说明](docs/updating.md#update-the-product-source)。

## 配置与验证

- [模型、账户、推理、压缩、缓存和工具](docs/models-and-tools.md)
- [安装与网络访问](docs/portable-installation.md)
- [本地引擎与恢复](docs/portable-recovery.md)
- [排查问题](docs/troubleshooting.md)
- [发布干净的分支](docs/public-release.md)

每个服务商账户独立分组，未固定模型的 Bot 使用全局默认值。安装和更新不得重置现有选择、启用暂停任务或重新创建身份。通过真实消息、无副作用的文件读取和最终回复验证路由与工具。独立 Grok Bot iOS/Android 的 BOX 投递仍未解决，语音工具在挂断前完成也未验证；按实际客户端分别测试。

## 卸载

```bash
bash /home/box/sand-data/openbot/uninstall.sh
```

交互式卸载默认保留服务商密钥；只有显式 --purge-secrets 才删除，并在终端确认。

## 许可证

MIT，见 [LICENSE](LICENSE)。本项目独立于 xAI。
